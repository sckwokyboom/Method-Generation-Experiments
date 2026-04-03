from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
import threading
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pipeline.compilability import check_compilability
from pipeline.config import Config
from pipeline.coverage import CoverageMap, build_coverage_map
from pipeline.dataset import build_dataset, load_extraction
from pipeline.llm import generate_completion
from pipeline.metrics import (
    compute_all_metrics, invocation_recall_at_k, api_coverage_at_k, mrr_for_similar_method,
    retrieval_precision_at_k, retrieval_ndcg_at_k, retrieval_type_iou, owner_type_recall,
)
from pipeline.models import ExtractedMethod, RetrievalResponse, RetrievalResult, SampleResult, TestEvalResult
from pipeline.normalize import normalize_code
from pipeline.prompt import build_fim_prompt, is_retrieval_mode, retriever_name_from_mode
from pipeline.report import generate_report, load_sample_result, update_progress, write_sample_result
from pipeline.retrieval import build_index, build_search_request, filter_leakage, format_retrieval_augmentation, search_batch
from pipeline.test_runner import run_test_evaluation
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
        log.error("Extractor failed (exit code %d)", result.returncode)
        if result.stdout:
            log.error("Extractor stdout:\n%s", result.stdout[-3000:])
        if result.stderr:
            log.error("Extractor stderr:\n%s", result.stderr[-3000:])
        raise RuntimeError(
            f"Extractor failed with exit code {result.returncode}.\n"
            f"stdout: {result.stdout[-1000:]}\n"
            f"stderr: {result.stderr[-1000:]}"
        )
    log.info("Extraction complete: %s", extraction_output)


def _retrieval_config_for_mode(config: Config, mode: str):
    """Resolve RetrievalConfig for a given mode, or None if not a retrieval mode."""
    if mode == "retrieval_augmentation":
        return config.retrieval  # legacy backward compat
    if is_retrieval_mode(mode):
        name = retriever_name_from_mode(mode)  # rag_lucene → lucene
        ret_cfg = config.retrievers.get(name)
        if ret_cfg is None:
            raise ValueError(
                f"Mode '{mode}' requires retrievers.{name} section in config, "
                f"available: {list(config.retrievers.keys())}"
            )
        return ret_cfg
    return None


@contextmanager
def _with_retrieval_config(config: Config, ret_cfg):
    """Temporarily swap config.retrieval so retrieval.py functions use the right config."""
    original = config.retrieval
    config.retrieval = ret_cfg
    try:
        yield
    finally:
        config.retrieval = original


def _offset_to_line(content: str, offset: int) -> int:
    """Convert a character offset to a 1-based line number."""
    return content[:offset].count("\n") + 1


def _find_test_files(method: ExtractedMethod, project_path: str) -> list[str] | None:
    """Search test source files for references to the target method name."""
    from pathlib import Path as _Path
    project = _Path(project_path)
    test_dirs = list(project.rglob("src/test"))
    if not test_dirs:
        return None
    method_name = method.method_name
    found: list[str] = []
    for test_dir in test_dirs:
        for java_file in test_dir.rglob("*.java"):
            try:
                text = java_file.read_text(encoding="utf-8", errors="ignore")
                if method_name in text:
                    found.append(str(java_file.relative_to(project)))
            except OSError:
                continue
    return found if found else None


def process_sample(
    method: ExtractedMethod,
    mode: str,
    config: Config,
    classpath: list[str],
    sample_idx: int,
    total: int,
    retrieval_response: RetrievalResponse | None = None,
    coverage_map: CoverageMap | None = None,
) -> SampleResult:
    log.info("[%s] Processing sample %d/%d: %s#%s",
             mode, sample_idx + 1, total, method.class_fqn, method.method_name)

    # Build retrieval augmentation and apply budget trimming.
    # effective_retrieval tracks the actual results the model sees (post-trimming),
    # used for metrics and serialization. Original retrieval_response is never mutated.
    retrieval_aug = None
    effective_retrieval: RetrievalResponse | None = None
    ret_cfg = _retrieval_config_for_mode(config, mode)
    _is_ret = is_retrieval_mode(mode)  # includes legacy "retrieval_augmentation"
    if _is_ret and retrieval_response and ret_cfg:
        effective_retrieval = filter_leakage(retrieval_response, method)
        retrieval_aug = format_retrieval_augmentation(effective_retrieval, ret_cfg)

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
            retrieval_aug = format_retrieval_augmentation(effective_retrieval, ret_cfg)
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
    elif _is_ret:
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

    # Per-line coverage from JaCoCo
    cov_ratio = None
    line_cov = None
    if coverage_map is not None:
        cov_ratio = coverage_map.get_method_coverage_ratio(
            method.class_fqn, method.method_name, method.parameter_types,
        )
        start_line = _offset_to_line(method.file_content, method.body_start_offset)
        end_line = _offset_to_line(method.file_content, method.body_end_offset)
        lc = coverage_map.get_line_coverage(method.class_fqn, start_line, end_line)
        if lc is not None:
            gt_lines = method.file_content.splitlines()
            line_cov = []
            for entry in lc:
                src = gt_lines[entry.line_number - 1] if entry.line_number <= len(gt_lines) else ""
                line_cov.append({
                    "line": entry.line_number,
                    "covered": entry.is_covered,
                    "source": src,
                })

    # Test file references
    test_files = None
    if coverage_map is not None:
        test_files = _find_test_files(method, config.project.path)

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
        coverage_ratio=cov_ratio,
        line_coverage=line_cov,
        test_file_paths=test_files,
    )


def _hash_file(path: Path) -> str:
    """Return SHA-256 hex digest of a file, or empty string if missing."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


# Python source files whose content affects generated prompts / augmentations.
# A change in any of them invalidates the sample cache.
_PROMPT_AFFECTING_SOURCES = [
    Path(__file__).parent / "retrieval.py",
    Path(__file__).parent / "prompt.py",
]


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
    ret_cfg = _retrieval_config_for_mode(config, mode)
    if ret_cfg:
        manifest["retrieval"] = {
            "retriever_type": ret_cfg.retriever_type,
            "top_k": ret_cfg.top_k,
            "max_augmentation_tokens": ret_cfg.max_augmentation_tokens,
            "max_results_in_prompt": ret_cfg.max_results_in_prompt,
            "max_body_lines": ret_cfg.max_body_lines,
            "include_body": ret_cfg.include_body,
            "near_duplicate_threshold": ret_cfg.near_duplicate_threshold,
            "external_retriever_class": ret_cfg.external_retriever_class,
            "external_retriever_jars": sorted(ret_cfg.external_retriever_jars),
            "project_source_roots": sorted(ret_cfg.project_source_roots),
        }
    # Include hashes of source files that affect prompt / augmentation formatting
    # so that code changes invalidate the cache automatically.
    manifest["_source_hashes"] = {
        src.name: _hash_file(src) for src in _PROMPT_AFFECTING_SOURCES
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
    coverage_map: CoverageMap | None = None,
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
            result = process_sample(method, mode, config, classpath, i, len(methods), ret_resp,
                                    coverage_map=coverage_map)
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
    coverage_map: CoverageMap | None = None,
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
            result = process_sample(method, mode, config, classpath, i, len(methods), ret_resp,
                                    coverage_map=coverage_map)
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

    # Build coverage map if test-coverage filtering is enabled
    coverage_map = None
    if config.extraction.require_test_coverage:
        log.info("=== Building coverage map via JaCoCo ===")
        jacoco_report = Path(config.project.path) / "target" / "site" / "jacoco" / "jacoco.xml"
        coverage_map = build_coverage_map(
            config.project.path,
            build_system=config.project.build_system,
            cached_report=str(jacoco_report) if jacoco_report.exists() else None,
        )
        log.info("Coverage map: %s", coverage_map)

    methods, classpath = build_dataset(config, coverage_map=coverage_map)
    log.info("Dataset: %d methods, %d classpath entries", len(methods), len(classpath))

    # Build retrieval indexes and batch-search for each retrieval mode
    retrieval_modes = [m for m in config.experiment.modes if is_retrieval_mode(m)]
    retrieval_responses_map: dict[str, list[RetrievalResponse | None]] = {}

    for ret_mode in retrieval_modes:
        ret_cfg = _retrieval_config_for_mode(config, ret_mode)
        # _retrieval_config_for_mode raises ValueError if rag_<name> has no config

        log.info("=== Setting up retrieval for mode: %s (type=%s) ===",
                 ret_mode, ret_cfg.retriever_type)

        with _with_retrieval_config(config, ret_cfg):
            build_index(config)

            # Load full extraction data for enrichment when using external retriever.
            all_extraction_methods = None
            if ret_cfg.retriever_type == "external":
                extraction_full = load_extraction(config.extraction.output)
                all_extraction_methods = extraction_full.methods
                log.info("Loaded %d methods for external enrichment", len(all_extraction_methods))

            log.info("=== Building retrieval queries for %d samples (%s) ===",
                     len(methods), ret_mode)
            requests = [build_search_request(m, config) for m in methods]
            responses = search_batch(
                requests, config, classpath=classpath,
                all_methods=all_extraction_methods,
            )
            retrieval_responses_map[ret_mode] = responses

    output_dir = Path(config.output.dir)
    max_concurrent = config.llm.max_concurrent_requests
    all_results: dict[str, list[SampleResult]] = {}

    for mode in config.experiment.modes:
        log.info("=== Running mode: %s ===", mode)
        mode_dir = output_dir / mode
        samples_dir = mode_dir / "samples"
        samples_dir.mkdir(parents=True, exist_ok=True)

        mode_retrieval = retrieval_responses_map.get(mode)

        if max_concurrent > 1:
            mode_results = _run_mode_concurrent(
                methods, mode, config, classpath, samples_dir, mode_dir, max_concurrent,
                mode_retrieval, coverage_map=coverage_map,
            )
        else:
            mode_results = _run_mode_sequential(
                methods, mode, config, classpath, samples_dir, mode_dir,
                mode_retrieval, coverage_map=coverage_map,
            )

        all_results[mode] = mode_results
        log.info("Mode %s: %d/%d samples completed", mode, len(mode_results), len(methods))

    # === Test Evaluation Phase (sequential, modifies source files) ===
    if config.test_evaluation.enabled:
        log.info("=== Running test evaluation for pass@k ===")
        project_path = Path(config.project.path)
        for mode, mode_results in all_results.items():
            log.info("Test evaluation for mode: %s (%d samples)", mode, len(mode_results))
            samples_dir = output_dir / mode / "samples"
            for i, result in enumerate(mode_results):
                if result.test_eval is not None:
                    log.info("[%s] Sample %d/%d already has test_eval, skipping",
                             mode, i + 1, len(mode_results))
                    continue

                # Skip samples that failed compilation — no point running tests
                if result.compilability is not None and not result.compilability.success:
                    log.info("[%s] Sample %d/%d not compilable, skipping test eval",
                             mode, i + 1, len(mode_results))
                    result.test_eval = TestEvalResult(
                        success=False, tests_run=0, tests_passed=0, tests_failed=0,
                        failed_test_names=[], build_success=False,
                        error_messages=["Skipped: code does not compile"], duration_ms=0.0,
                    )
                    sample_path = samples_dir / f"sample_{i:03d}.json"
                    write_sample_result(
                        result, sample_path,
                        save_prompts=config.output.save_prompts,
                        save_responses=config.output.save_responses,
                    )
                    continue

                # Find the corresponding method for source restoration
                method = next(
                    (m for m in methods
                     if f"{m.class_fqn}#{m.method_name}" == result.method_id),
                    None,
                )
                if method is None:
                    log.warning("Could not find method for %s, skipping test eval", result.method_id)
                    continue

                log.info("[%s] Test eval %d/%d: %s", mode, i + 1, len(mode_results), result.method_id)
                test_result = run_test_evaluation(
                    method, result.generated, project_path,
                    build_system=config.test_evaluation.build_system,
                    timeout_seconds=config.test_evaluation.timeout_seconds,
                    test_command=config.project.test_command or None,
                )
                result.test_eval = TestEvalResult(
                    success=test_result.success,
                    tests_run=test_result.tests_run,
                    tests_passed=test_result.tests_passed,
                    tests_failed=test_result.tests_failed,
                    failed_test_names=test_result.failed_test_names,
                    build_success=test_result.build_success,
                    error_messages=test_result.error_messages,
                    duration_ms=test_result.duration_ms,
                )
                # Re-save the sample with test_eval data
                sample_path = samples_dir / f"sample_{i:03d}.json"
                write_sample_result(
                    result, sample_path,
                    save_prompts=config.output.save_prompts,
                    save_responses=config.output.save_responses,
                )
                status = "PASS" if test_result.success else "FAIL"
                log.info(
                    "[%s] Test eval %d/%d: %s (tests=%d, passed=%d, failed=%d)",
                    mode, i + 1, len(mode_results), status,
                    test_result.tests_run, test_result.tests_passed, test_result.tests_failed,
                )
                if not test_result.success and test_result.error_messages:
                    log.info("[%s]   errors: %s", mode, "; ".join(test_result.error_messages[:3]))

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


def recompute_test_evaluation(config: Config) -> None:
    """Re-run test evaluation on existing sample results without re-generating code."""
    output_dir = Path(config.output.dir)
    project_path = Path(config.project.path)

    # Load extraction data to get method objects for source restoration
    extraction_data = load_extraction(config.extraction.output)
    methods_by_id = {
        f"{m.class_fqn}#{m.method_name}": m for m in extraction_data.methods
    }

    all_results: dict[str, list[SampleResult]] = {}

    for mode in config.experiment.modes:
        samples_dir = output_dir / mode / "samples"
        if not samples_dir.exists():
            log.warning("No samples directory for mode %s, skipping", mode)
            continue

        sample_files = sorted(samples_dir.glob("sample_*.json"))
        if not sample_files:
            log.warning("No sample files found in %s, skipping", samples_dir)
            continue

        log.info("=== Recomputing test evaluation for mode: %s (%d samples) ===", mode, len(sample_files))
        mode_results: list[SampleResult] = []

        for i, sample_path in enumerate(sample_files):
            result = load_sample_result(sample_path)

            # Skip samples that failed compilation — no point running tests
            if result.compilability is not None and not result.compilability.success:
                log.info("[%s] Sample %d/%d not compilable, skipping test eval",
                         mode, i + 1, len(sample_files))
                result.test_eval = TestEvalResult(
                    success=False, tests_run=0, tests_passed=0, tests_failed=0,
                    failed_test_names=[], build_success=False,
                    error_messages=["Skipped: code does not compile"], duration_ms=0.0,
                )
                write_sample_result(
                    result, sample_path,
                    save_prompts=config.output.save_prompts,
                    save_responses=config.output.save_responses,
                )
                mode_results.append(result)
                continue

            method = methods_by_id.get(result.method_id)
            if method is None:
                log.warning("Could not find method for %s, skipping test eval", result.method_id)
                mode_results.append(result)
                continue

            log.info("[%s] Test eval %d/%d: %s", mode, i + 1, len(sample_files), result.method_id)
            test_result = run_test_evaluation(
                method, result.generated, project_path,
                build_system=config.test_evaluation.build_system,
                timeout_seconds=config.test_evaluation.timeout_seconds,
                test_command=config.project.test_command or None,
            )
            result.test_eval = TestEvalResult(
                success=test_result.success,
                tests_run=test_result.tests_run,
                tests_passed=test_result.tests_passed,
                tests_failed=test_result.tests_failed,
                failed_test_names=test_result.failed_test_names,
                build_success=test_result.build_success,
                error_messages=test_result.error_messages,
                duration_ms=test_result.duration_ms,
            )
            write_sample_result(
                result, sample_path,
                save_prompts=config.output.save_prompts,
                save_responses=config.output.save_responses,
            )
            status = "PASS" if test_result.success else "FAIL"
            log.info(
                "[%s] Test eval %d/%d: %s (tests=%d, passed=%d, failed=%d)",
                mode, i + 1, len(sample_files), status,
                test_result.tests_run, test_result.tests_passed, test_result.tests_failed,
            )
            if not test_result.success and test_result.error_messages:
                log.info("[%s]   errors: %s", mode, "; ".join(test_result.error_messages[:3]))
            mode_results.append(result)

        all_results[mode] = mode_results

    config_summary = {
        "model_name": config.llm.model_name,
        "sample_count": config.dataset.sample_count,
    }
    generate_report(
        all_results, config.output.dir,
        save_prompts=config.output.save_prompts,
        save_responses=config.output.save_responses,
        config_summary=config_summary,
    )
    log.info("=== Recompute complete ===")


def main():
    parser = argparse.ArgumentParser(description="Java Method Generation Experiment Pipeline")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--mode", nargs="*", help="Run only specific modes")
    parser.add_argument("--output-dir", default=None, help="Override output directory (default: from config)")
    parser.add_argument("--skip-extraction", action="store_true", help="Skip extraction step")
    parser.add_argument("--skip-compilability", action="store_true", help="Skip compilability checks")
    parser.add_argument("--recompute-tests", action="store_true",
                        help="Re-run test evaluation on existing results (skip generation)")
    args = parser.parse_args()

    config = Config.load(args.config)

    if args.mode:
        config.experiment.modes = args.mode

    if args.output_dir:
        config.output.dir = args.output_dir

    if args.skip_compilability:
        config.compilability.enabled = False

    if args.recompute_tests:
        recompute_test_evaluation(config)
        return

    if args.skip_extraction:
        if not Path(config.extraction.output).exists():
            log.error("Extraction output not found and --skip-extraction was specified")
            sys.exit(1)

    run_experiment(config)


if __name__ == "__main__":
    main()
