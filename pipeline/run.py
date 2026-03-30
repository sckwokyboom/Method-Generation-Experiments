from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

from pipeline.compilability import check_compilability
from pipeline.config import Config
from pipeline.dataset import build_dataset, load_extraction
from pipeline.llm import generate_completion
from pipeline.metrics import compute_all_metrics
from pipeline.models import ExtractedMethod, SampleResult
from pipeline.normalize import normalize_code
from pipeline.prompt import build_fim_prompt
from pipeline.report import generate_report

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

    norm_gen = normalize_code(completion.text)
    norm_ref = normalize_code(fim_prompt.ground_truth)

    metrics = compute_all_metrics(completion.text, fim_prompt.ground_truth)

    compilability = None
    if config.compilability.enabled:
        compilability = check_compilability(method, completion.text, classpath)

    method_id = f"{method.class_fqn}#{method.method_name}"

    return SampleResult(
        method_id=method_id,
        file_path=method.file_path,
        mode=mode,
        prompt=fim_prompt.full_prompt,
        ground_truth=fim_prompt.ground_truth,
        generated=completion.text,
        normalized_ground_truth=norm_ref,
        normalized_generated=norm_gen,
        metrics=metrics,
        compilability=compilability,
        llm_response={
            "finish_reason": completion.finish_reason,
            "usage": completion.usage,
            "latency_ms": round(completion.latency_ms, 1),
        },
    )


def run_experiment(config: Config) -> None:
    log.info("=== Starting Experiment ===")
    log.info("Config: %s", json.dumps(config.to_dict(), indent=2, default=str))

    run_extraction(config)

    methods, classpath = build_dataset(config)
    log.info("Dataset: %d methods, %d classpath entries", len(methods), len(classpath))

    all_results: dict[str, list[SampleResult]] = {}

    for mode in config.experiment.modes:
        log.info("=== Running mode: %s ===", mode)
        mode_results: list[SampleResult] = []

        for i, method in enumerate(methods):
            try:
                result = process_sample(method, mode, config, classpath, i, len(methods))
                mode_results.append(result)
            except Exception as e:
                log.error("Failed to process sample %d (%s#%s): %s",
                          i, method.class_fqn, method.method_name, e)

        all_results[mode] = mode_results
        log.info("Mode %s: %d/%d samples completed", mode, len(mode_results), len(methods))

    generate_report(
        all_results,
        config.output.dir,
        save_prompts=config.output.save_prompts,
        save_responses=config.output.save_responses,
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
