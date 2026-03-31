from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pipeline.compilability import check_compilability
from pipeline.config import Config
from pipeline.dataset import build_dataset, load_extraction
from pipeline.llm import generate_completion
from pipeline.metrics import (
    compute_all_metrics, invocation_recall_at_k, api_coverage_at_k, mrr_for_similar_method,
    retrieval_precision_at_k, retrieval_ndcg_at_k, retrieval_type_iou, owner_type_recall,
)
from pipeline.models import ExtractedMethod, RetrievalResponse, RetrievalResult, SampleResult
from pipeline.normalize import normalize_code
from pipeline.prompt import build_fim_prompt
from pipeline.report import generate_report, load_sample_result, update_progress, write_sample_result
from pipeline.retrieval import build_index, build_search_request, format_retrieval_augmentation, search_batch
from pipeline.tokenizer import count_tokens

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def run_extraction(config: Config) -> None:
    extraction_output = Path(config.extraction.output)
    if extraction_output.exists():
        log.info("Extraction output already exists at %s, skipping", extraction_output)
        return

    jar_path = Path(config.extraction.extractor_jar)
    if not jar_path.exists():
        raise FileNotFoundError(
            f"Extractor JAR not found at {jar_path}. "
            "Build it first: cd extractor && ./gradlew jar"
        )

    extraction_output.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "java", "-jar", str(jar_path),
        "--project-path", config.project.path,
        "--output", str(extraction_output),
        "--min-statements", str(config.extraction.min_statements),
        "--build-first",
    ]

    log.info("Running extractor: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        log.error("Extractor stderr:\n%s", result.stderr)
        raise RuntimeError(f"Extractor failed with exit code {result.returncode}")
    log.info("Extraction complete: %s", extraction_output)


def process_sample(
    method: ExtractedMethod,
    mode: str,
    config: Config,
    classpath: list[str],
    sample_idx: int,
    total: int,
    retrieval_response: RetrievalResponse | None = None,
) -> SampleResult:
    log.info("[%s] Processing sample %d/%d: %s#%s",
             mode, sample_idx + 1, total, method.class_fqn, method.method_name)

    # Build retrieval augmentation and apply budget trimming.
    # effective_retrieval tracks the actual results the model sees (post-trimming),
    # used for metrics and serialization. Original retrieval_response is never mutated.
    retrieval_aug = None
    effective_retrieval: RetrievalResponse | None = None
    if mode == "retrieval_augmentation" and retrieval_response and config.retrieval:
        effective_retrieval = retrieval_response
        retrieval_aug = format_retrieval_augmentation(effective_retrieval, config.retrieval)

    fim_prompt = build_fim_prompt(method, mode, config.experiment.shuffle_seed, retrieval_aug)

    # Budget check: if prompt + max_tokens would exceed context_window,
    # progressively trim augmentation (fewer retrieved methods) until it fits.
    # When zero methods fit, retrieval_aug becomes "" (not None) so that
    # build_fim_prompt stays in retrieval_augmentation mode without falling
    # back to oracle invocation augmentation.
    context_window = config.llm.context_window
    if context_window > 0 and effective_retrieval:
        prompt_tokens = count_tokens(fim_prompt.full_prompt)
        n_results = len(effective_retrieval.results)
        while prompt_tokens + config.llm.max_tokens > context_window and n_results > 0:
            n_results -= 1
            effective_retrieval = RetrievalResponse(
                results=retrieval_response.results[:n_results],
                total_hits=retrieval_response.total_hits,
                search_time_ms=retrieval_response.search_time_ms,
                query_debug=retrieval_response.query_debug,
            )
            retrieval_aug = format_retrieval_augmentation(effective_retrieval, config.retrieval)
            fim_prompt = build_fim_prompt(method, mode, config.experiment.shuffle_seed, retrieval_aug)
            prompt_tokens = count_tokens(fim_prompt.full_prompt)
            log.info("[%s] Trimmed augmentation to %d results (%d prompt tokens)",
                     mode, n_results, prompt_tokens)

    completion = generate_completion(fim_prompt.full_prompt, config.llm)

    norm_gen = normalize_code(completion.text, identifier_unify=True)
    norm_ref = normalize_code(fim_prompt.ground_truth, identifier_unify=True)

    metrics = compute_all_metrics(completion.text, fim_prompt.ground_truth, identifier_unify=True)

    # Compute retrieval-oriented metrics on the effective (post-trim) results,
    # i.e. exactly what the model actually saw.
    if effective_retrieval and effective_retrieval.results:
        aug_text = retrieval_aug or ""
        results = effective_retrieval.results
        metrics.recall_at_k = invocation_recall_at_k(aug_text, method.invocations)
        metrics.api_coverage_at_k = api_coverage_at_k(results, method.invocations)
        metrics.mrr = mrr_for_similar_method(results, fim_prompt.ground_truth)
        metrics.retrieval_precision_at_k = retrieval_precision_at_k(results, method.invocations)
        metrics.retrieval_ndcg_at_k = retrieval_ndcg_at_k(results, method.invocations)
        metrics.retrieval_type_iou = retrieval_type_iou(
            results, method.imports, method.class_fields,
            method.parameter_types, method.return_type,
        )
        metrics.owner_type_recall = owner_type_recall(results, method.invocations)
    elif mode == "retrieval_augmentation":
        # Zero-hit retrieval: count as 0.0 instead of None so that
        # aggregate metrics don't silently drop these samples.
        metrics.recall_at_k = 0.0
        metrics.api_coverage_at_k = 0.0
        metrics.mrr = 0.0
        metrics.retrieval_precision_at_k = 0.0
        metrics.retrieval_ndcg_at_k = 0.0
        metrics.retrieval_type_iou = 0.0
        metrics.owner_type_recall = 0.0

    compilability = None
    if config.compilability.enabled:
        compilability = check_compilability(
            method, completion.text, classpath,
            java_home=config.compilability.java_home,
            source_version=config.compilability.source_version,
        )

    method_id = f"{method.class_fqn}#{method.method_name}"

    invocations_ordered = [
        {"order_index": inv.order_index, "signature": inv.signature, "resolution_mode": inv.resolution_mode}
        for inv in sorted(method.invocations, key=lambda i: i.order_index)
    ]
    invocations_as_used = [
        {"order_index": inv.order_index, "signature": inv.signature, "resolution_mode": inv.resolution_mode}
        for inv in fim_prompt.invocations_as_used
    ]

    # Serialize the effective (post-trim) retrieval, not the original.
    retrieval_results_dicts = None
    retrieval_query = None
    if effective_retrieval:
        retrieval_results_dicts = [r.to_dict() for r in effective_retrieval.results]
        retrieval_query = effective_retrieval.query_debug

    return SampleResult(
        method_id=method_id,
        file_path=method.file_path,
        mode=mode,
        method_signature=method.method_signature,
        ground_truth=fim_prompt.ground_truth,
        generated=completion.text,
        normalized_ground_truth=norm_ref,
        normalized_generated=norm_gen,
        invocations_ordered=invocations_ordered,
        invocations_as_used=invocations_as_used,
        augmentation_block=fim_prompt.augmentation_block,
        prompt=fim_prompt.full_prompt,
        metrics=metrics,
        compilability=compilability,
        llm_response={
            "finish_reason": completion.finish_reason,
            "usage": completion.usage,
            "latency_ms": round(completion.latency_ms, 1),
        },
        retrieval_results=retrieval_results_dicts,
        retrieval_query=retrieval_query,
    )


def _compute_run_manifest(mode: str, config: Config) -> str:
    """Compute a hash that captures all parameters affecting sample outputs."""
    manifest = {
        "mode": mode,
        "model_name": config.llm.model_name,
        "temperature": config.llm.temperature,
        "max_tokens": config.llm.max_tokens,
        "seed": config.llm.seed,
        "context_window": config.llm.context_window,
        "shuffle_seed": config.experiment.shuffle_seed,
        "sample_count": config.dataset.sample_count,
        "random_seed": config.dataset.random_seed,
    }
    if config.retrieval and mode == "retrieval_augmentation":
        manifest["retrieval"] = {
            "top_k": config.retrieval.top_k,
            "max_augmentation_tokens": config.retrieval.max_augmentation_tokens,
            "max_results_in_prompt": config.retrieval.max_results_in_prompt,
            "max_body_lines": config.retrieval.max_body_lines,
            "include_body": config.retrieval.include_body,
            "near_duplicate_threshold": config.retrieval.near_duplicate_threshold,
        }
    raw = json.dumps(manifest, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _validate_manifest(samples_dir: Path, mode: str, config: Config) -> bool:
    """Check if cached samples match the current run config. Returns True if valid."""
    manifest_path = samples_dir / "run_manifest.json"
    current_hash = _compute_run_manifest(mode, config)
    if manifest_path.exists():
        try:
            stored = json.loads(manifest_path.read_text(encoding="utf-8"))
            if stored.get("hash") == current_hash:
                return True
            log.warning("Run manifest mismatch in %s — cached samples will be recomputed", samples_dir)
        except Exception:
            pass
    return False


def _write_manifest(samples_dir: Path, mode: str, config: Config) -> None:
    manifest_path = samples_dir / "run_manifest.json"
    manifest_path.write_text(
        json.dumps({"hash": _compute_run_manifest(mode, config)}, indent=2),
        encoding="utf-8",
    )


def _run_mode_sequential(
    methods: list[ExtractedMethod],
    mode: str,
    config: Config,
    classpath: list[str],
    samples_dir: Path,
    mode_dir: Path,
    retrieval_responses: list[RetrievalResponse | None] | None = None,
) -> list[SampleResult]:
    results_by_idx: dict[int, SampleResult] = {}
    completed = 0
    cache_valid = _validate_manifest(samples_dir, mode, config)

    for i, method in enumerate(methods):
        sample_path = samples_dir / f"sample_{i:03d}.json"

        if cache_valid and sample_path.exists():
            log.info("[%s] Sample %d/%d already exists, loading (resume)",
                     mode, i + 1, len(methods))
            try:
                results_by_idx[i] = load_sample_result(sample_path)
                completed += 1
                continue
            except Exception as e:
                log.warning("Failed to load existing sample %s, recomputing: %s",
                            sample_path, e)

        ret_resp = retrieval_responses[i] if retrieval_responses else None
        try:
            result = process_sample(method, mode, config, classpath, i, len(methods), ret_resp)
            write_sample_result(
                result, sample_path,
                save_prompts=config.output.save_prompts,
                save_responses=config.output.save_responses,
            )
            results_by_idx[i] = result
            completed += 1
        except Exception as e:
            log.error("Failed to process sample %d (%s#%s): %s",
                      i, method.class_fqn, method.method_name, e)

        update_progress(mode_dir, mode, completed, len(methods))

    _write_manifest(samples_dir, mode, config)
    return [results_by_idx[i] for i in sorted(results_by_idx)]


def _run_mode_concurrent(
    methods: list[ExtractedMethod],
    mode: str,
    config: Config,
    classpath: list[str],
    samples_dir: Path,
    mode_dir: Path,
    max_workers: int,
    retrieval_responses: list[RetrievalResponse | None] | None = None,
) -> list[SampleResult]:
    results_by_idx: dict[int, SampleResult] = {}
    completed = 0
    progress_lock = threading.Lock()
    cache_valid = _validate_manifest(samples_dir, mode, config)

    # First pass: load cached results
    to_process: list[tuple[int, ExtractedMethod]] = []
    for i, method in enumerate(methods):
        sample_path = samples_dir / f"sample_{i:03d}.json"
        if cache_valid and sample_path.exists():
            log.info("[%s] Sample %d/%d already exists, loading (resume)",
                     mode, i + 1, len(methods))
            try:
                results_by_idx[i] = load_sample_result(sample_path)
                completed += 1
                continue
            except Exception as e:
                log.warning("Failed to load existing sample %s, recomputing: %s",
                            sample_path, e)
        to_process.append((i, method))

    if not to_process:
        update_progress(mode_dir, mode, completed, len(methods))
        return [results_by_idx[i] for i in sorted(results_by_idx)]

    log.info("[%s] %d samples cached, %d to compute (max_workers=%d)",
             mode, completed, len(to_process), max_workers)

    def _process_and_write(i: int, method: ExtractedMethod) -> tuple[int, SampleResult | None]:
        nonlocal completed
        sample_path = samples_dir / f"sample_{i:03d}.json"
        ret_resp = retrieval_responses[i] if retrieval_responses else None
        try:
            result = process_sample(method, mode, config, classpath, i, len(methods), ret_resp)
            write_sample_result(
                result, sample_path,
                save_prompts=config.output.save_prompts,
                save_responses=config.output.save_responses,
            )
            with progress_lock:
                completed += 1
                update_progress(mode_dir, mode, completed, len(methods))
            return i, result
        except Exception as e:
            log.error("Failed to process sample %d (%s#%s): %s",
                      i, method.class_fqn, method.method_name, e)
            return i, None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_process_and_write, i, method): i
            for i, method in to_process
        }
        for future in as_completed(futures):
            i, result = future.result()
            if result is not None:
                results_by_idx[i] = result

    _write_manifest(samples_dir, mode, config)
    return [results_by_idx[i] for i in sorted(results_by_idx)]


def run_experiment(config: Config) -> None:
    log.info("=== Starting Experiment ===")
    log.info("Config: %s", json.dumps(config.to_dict(), indent=2, default=str))

    run_extraction(config)

    methods, classpath = build_dataset(config)
    log.info("Dataset: %d methods, %d classpath entries", len(methods), len(classpath))

    # Build retrieval index if needed
    has_retrieval = "retrieval_augmentation" in config.experiment.modes
    retrieval_responses: list[RetrievalResponse | None] | None = None
    if has_retrieval:
        if config.retrieval is None:
            raise ValueError("retrieval_augmentation mode requires 'retrieval' section in config")
        build_index(config)

        # Batch search for all samples
        log.info("=== Building retrieval queries for %d samples ===", len(methods))
        requests = [build_search_request(m, config) for m in methods]
        retrieval_responses = search_batch(requests, config)

    output_dir = Path(config.output.dir)
    max_concurrent = config.llm.max_concurrent_requests
    all_results: dict[str, list[SampleResult]] = {}

    for mode in config.experiment.modes:
        log.info("=== Running mode: %s ===", mode)
        mode_dir = output_dir / mode
        samples_dir = mode_dir / "samples"
        samples_dir.mkdir(parents=True, exist_ok=True)

        mode_retrieval = retrieval_responses if mode == "retrieval_augmentation" else None

        if max_concurrent > 1:
            mode_results = _run_mode_concurrent(
                methods, mode, config, classpath, samples_dir, mode_dir, max_concurrent,
                mode_retrieval,
            )
        else:
            mode_results = _run_mode_sequential(
                methods, mode, config, classpath, samples_dir, mode_dir,
                mode_retrieval,
            )

        all_results[mode] = mode_results
        log.info("Mode %s: %d/%d samples completed", mode, len(mode_results), len(methods))

    config_summary = {
        "model_name": config.llm.model_name,
        "sample_count": config.dataset.sample_count,
    }
    generate_report(
        all_results,
        config.output.dir,
        save_prompts=config.output.save_prompts,
        save_responses=config.output.save_responses,
        config_summary=config_summary,
    )


def main():
    parser = argparse.ArgumentParser(description="Java Method Generation Experiment Pipeline")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--mode", nargs="*", help="Run only specific modes")
    parser.add_argument("--output-dir", default=None, help="Override output directory (default: from config)")
    parser.add_argument("--skip-extraction", action="store_true", help="Skip extraction step")
    parser.add_argument("--skip-compilability", action="store_true", help="Skip compilability checks")
    args = parser.parse_args()

    config = Config.load(args.config)

    if args.mode:
        config.experiment.modes = args.mode

    if args.output_dir:
        config.output.dir = args.output_dir

    if args.skip_compilability:
        config.compilability.enabled = False

    if args.skip_extraction:
        if not Path(config.extraction.output).exists():
            log.error("Extraction output not found and --skip-extraction was specified")
            sys.exit(1)

    run_experiment(config)


if __name__ == "__main__":
    main()
