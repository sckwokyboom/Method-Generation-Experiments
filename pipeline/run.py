from __future__ import annotations

import argparse
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
from pipeline.metrics import compute_all_metrics
from pipeline.models import ExtractedMethod, SampleResult
from pipeline.normalize import normalize_code
from pipeline.prompt import build_fim_prompt
from pipeline.report import generate_report, load_sample_result, update_progress, write_sample_result

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
    result = subprocess.run(cmd, capture_output=True, text=True)
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
) -> SampleResult:
    log.info("[%s] Processing sample %d/%d: %s#%s",
             mode, sample_idx + 1, total, method.class_fqn, method.method_name)

    fim_prompt = build_fim_prompt(method, mode, config.experiment.shuffle_seed)

    completion = generate_completion(fim_prompt.full_prompt, config.llm)

    norm_gen = normalize_code(completion.text, identifier_unify=True)
    norm_ref = normalize_code(fim_prompt.ground_truth, identifier_unify=True)

    metrics = compute_all_metrics(completion.text, fim_prompt.ground_truth, identifier_unify=True)

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
    )


def _run_mode_sequential(
    methods: list[ExtractedMethod],
    mode: str,
    config: Config,
    classpath: list[str],
    samples_dir: Path,
    mode_dir: Path,
) -> list[SampleResult]:
    results_by_idx: dict[int, SampleResult] = {}
    completed = 0

    for i, method in enumerate(methods):
        sample_path = samples_dir / f"sample_{i:03d}.json"

        if sample_path.exists():
            log.info("[%s] Sample %d/%d already exists, loading (resume)",
                     mode, i + 1, len(methods))
            try:
                results_by_idx[i] = load_sample_result(sample_path)
                completed += 1
                continue
            except Exception as e:
                log.warning("Failed to load existing sample %s, recomputing: %s",
                            sample_path, e)

        try:
            result = process_sample(method, mode, config, classpath, i, len(methods))
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

    return [results_by_idx[i] for i in sorted(results_by_idx)]


def _run_mode_concurrent(
    methods: list[ExtractedMethod],
    mode: str,
    config: Config,
    classpath: list[str],
    samples_dir: Path,
    mode_dir: Path,
    max_workers: int,
) -> list[SampleResult]:
    results_by_idx: dict[int, SampleResult] = {}
    completed = 0
    progress_lock = threading.Lock()

    # First pass: load cached results
    to_process: list[tuple[int, ExtractedMethod]] = []
    for i, method in enumerate(methods):
        sample_path = samples_dir / f"sample_{i:03d}.json"
        if sample_path.exists():
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
        try:
            result = process_sample(method, mode, config, classpath, i, len(methods))
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

    return [results_by_idx[i] for i in sorted(results_by_idx)]


def run_experiment(config: Config) -> None:
    log.info("=== Starting Experiment ===")
    log.info("Config: %s", json.dumps(config.to_dict(), indent=2, default=str))

    run_extraction(config)

    methods, classpath = build_dataset(config)
    log.info("Dataset: %d methods, %d classpath entries", len(methods), len(classpath))

    output_dir = Path(config.output.dir)
    max_concurrent = config.llm.max_concurrent_requests
    all_results: dict[str, list[SampleResult]] = {}

    for mode in config.experiment.modes:
        log.info("=== Running mode: %s ===", mode)
        mode_dir = output_dir / mode
        samples_dir = mode_dir / "samples"
        samples_dir.mkdir(parents=True, exist_ok=True)

        if max_concurrent > 1:
            mode_results = _run_mode_concurrent(
                methods, mode, config, classpath, samples_dir, mode_dir, max_concurrent,
            )
        else:
            mode_results = _run_mode_sequential(
                methods, mode, config, classpath, samples_dir, mode_dir,
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
    parser.add_argument("--skip-extraction", action="store_true", help="Skip extraction step")
    parser.add_argument("--skip-compilability", action="store_true", help="Skip compilability checks")
    args = parser.parse_args()

    config = Config.load(args.config)

    if args.mode:
        config.experiment.modes = args.mode

    if args.skip_compilability:
        config.compilability.enabled = False

    if args.skip_extraction:
        if not Path(config.extraction.output).exists():
            log.error("Extraction output not found and --skip-extraction was specified")
            sys.exit(1)

    run_experiment(config)


if __name__ == "__main__":
    main()
