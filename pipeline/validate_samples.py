"""Validate and repair sample consistency across experiment modes.

Checks that all modes agree on the method identity for each sample index,
and optionally rebuilds ordered_augmentation FIM prompts from extraction data.

Usage:
    # Diagnose: show mismatches
    python -m pipeline.validate_samples <results-dir>

    # Repair ordered_augmentation prompts (no LLM re-run, just metadata)
    python -m pipeline.validate_samples <results-dir> --repair --extraction-json extracted_methods.json

    # Full re-run of broken ordered_augmentation samples (requires running LLM endpoint)
    python -m pipeline.validate_samples <results-dir> --rerun --config config.yaml
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def _atomic_write_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=path.stem)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def discover_modes(results_dir: Path) -> dict[str, Path]:
    modes = {}
    for entry in sorted(results_dir.iterdir()):
        samples_dir = entry / "samples"
        if entry.is_dir() and samples_dir.is_dir():
            modes[entry.name] = samples_dir
    return modes


def load_samples_by_index(
    modes_map: dict[str, Path],
) -> dict[str, dict[str, dict]]:
    """Load all sample JSONs, grouped by index then mode."""
    grouped: dict[str, dict[str, dict]] = {}
    for mode, samples_dir in modes_map.items():
        for path in sorted(samples_dir.glob("sample_*.json")):
            m = re.search(r"sample_(\d+)", path.stem)
            if not m:
                continue
            idx = m.group(1)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data["_path"] = str(path)
            grouped.setdefault(idx, {})[mode] = data
    return grouped


def validate_consistency(results_dir: Path) -> list[dict]:
    """Check that all modes have the same method for each sample index.

    Returns a list of issues found.
    """
    modes_map = discover_modes(results_dir)
    if not modes_map:
        log.error("No mode directories with samples/ found in %s", results_dir)
        return []

    mode_names = list(modes_map.keys())
    log.info("Found modes: %s", mode_names)

    grouped = load_samples_by_index(modes_map)
    log.info("Found %d sample indices", len(grouped))

    issues: list[dict] = []

    for idx in sorted(grouped.keys(), key=lambda x: int(x)):
        mode_data = grouped[idx]
        if len(mode_data) < 2:
            continue

        # Use the first mode as reference
        ref_mode = next(iter(mode_data))
        ref = mode_data[ref_mode]
        ref_method_id = ref.get("method_id", "")
        ref_file_path = ref.get("file_path", "")
        ref_ground_truth = ref.get("ground_truth", "")

        for mode, data in mode_data.items():
            if mode == ref_mode:
                continue

            method_id = data.get("method_id", "")
            file_path = data.get("file_path", "")
            ground_truth = data.get("ground_truth", "")

            mismatches = []
            if method_id != ref_method_id:
                mismatches.append(f"method_id: {ref_mode}={ref_method_id!r} vs {mode}={method_id!r}")
            if file_path != ref_file_path:
                mismatches.append(f"file_path: {ref_mode}={ref_file_path!r} vs {mode}={file_path!r}")
            if ground_truth != ref_ground_truth:
                mismatches.append("ground_truth differs")

            if mismatches:
                issue = {
                    "index": idx,
                    "ref_mode": ref_mode,
                    "mismatch_mode": mode,
                    "details": mismatches,
                    "ref_method_id": ref_method_id,
                    "actual_method_id": method_id,
                }
                issues.append(issue)

        # Within-mode validation: check augmentation block matches invocations
        for mode, data in mode_data.items():
            aug_block = data.get("augmentation_block")
            invocations_as_used = data.get("invocations_as_used", [])
            invocations_ordered = data.get("invocations_ordered", [])

            if "ordered" in mode and aug_block and invocations_ordered:
                # For ordered_augmentation, invocations_as_used should match
                # invocations_ordered (sorted by order_index)
                ordered_sigs = [inv["signature"] for inv in
                                sorted(invocations_ordered, key=lambda i: i["order_index"])]
                used_sigs = [inv["signature"] for inv in invocations_as_used]

                if ordered_sigs != used_sigs:
                    issues.append({
                        "index": idx,
                        "ref_mode": mode,
                        "mismatch_mode": mode,
                        "details": [
                            "ordered_augmentation: invocations_as_used != sorted(invocations_ordered)",
                            f"  ordered: {ordered_sigs[:3]}...",
                            f"  as_used: {used_sigs[:3]}...",
                        ],
                        "ref_method_id": data.get("method_id", ""),
                        "actual_method_id": data.get("method_id", ""),
                    })

    return issues


def rerun_broken_samples(
    results_dir: Path,
    config_path: Path,
    broken_indices: list[str],
    mode: str = "ordered_augmentation",
) -> None:
    """Re-run LLM generation for specific broken samples."""
    from pipeline.config import Config
    from pipeline.coverage import build_coverage_map
    from pipeline.dataset import build_dataset, load_extraction
    from pipeline.llm import generate_completion
    from pipeline.metrics import compute_all_metrics
    from pipeline.normalize import normalize_code
    from pipeline.prompt import build_fim_prompt, is_retrieval_mode
    from pipeline.report import write_sample_result
    from pipeline.run import process_sample

    config = Config.load(config_path)

    coverage_map = None
    if config.extraction.require_test_coverage:
        from pipeline.coverage import build_coverage_map
        jacoco_report = Path(config.project.path) / "target" / "site" / "jacoco" / "jacoco.xml"
        coverage_map = build_coverage_map(
            config.project.path,
            build_system=config.project.build_system,
            cached_report=str(jacoco_report) if jacoco_report.exists() else None,
        )

    methods, classpath = build_dataset(config, coverage_map=coverage_map)
    log.info("Dataset: %d methods", len(methods))

    samples_dir = results_dir / mode / "samples"
    if not samples_dir.is_dir():
        log.error("Mode directory not found: %s", samples_dir)
        return

    for idx_str in broken_indices:
        i = int(idx_str)
        if i >= len(methods):
            log.warning("Index %d out of range (dataset has %d methods)", i, len(methods))
            continue

        method = methods[i]
        log.info("Re-running sample %d: %s#%s", i, method.class_fqn, method.method_name)

        result = process_sample(
            method, mode, config, classpath, i, len(methods),
            coverage_map=coverage_map,
        )

        sample_path = samples_dir / f"sample_{i:03d}.json"
        write_sample_result(
            result, sample_path,
            save_prompts=config.output.save_prompts,
            save_responses=config.output.save_responses,
        )
        log.info("  Written: %s (method=%s)", sample_path.name, result.method_id)


def main():
    parser = argparse.ArgumentParser(
        description="Validate and repair sample consistency across experiment modes"
    )
    parser.add_argument(
        "results_dir",
        type=Path,
        help="Path to the results directory containing mode subdirectories",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Show detailed mismatch report (default: just validate)",
    )
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="Re-run LLM generation for broken samples (requires --config)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (required for --rerun)",
    )
    parser.add_argument(
        "--mode",
        default="ordered_augmentation",
        help="Mode to re-run (default: ordered_augmentation)",
    )
    parser.add_argument(
        "--extraction-json",
        type=Path,
        default=None,
        help="Path to extracted_methods.json",
    )

    args = parser.parse_args()

    if not args.results_dir.is_dir():
        log.error("Results directory does not exist: %s", args.results_dir)
        sys.exit(1)

    # Always validate first
    print(f"\n=== Validating samples in {args.results_dir} ===\n")
    issues = validate_consistency(args.results_dir)

    if not issues:
        print("\nAll samples are consistent across modes.")
        return

    print(f"\nFound {len(issues)} issue(s):\n")
    broken_indices = set()
    for issue in issues:
        idx = issue["index"]
        broken_indices.add(idx)
        print(f"  Sample {idx}:")
        for detail in issue["details"]:
            print(f"    {detail}")
        if issue["ref_method_id"] != issue["actual_method_id"]:
            print(f"    Expected: {issue['ref_method_id']}")
            print(f"    Got:      {issue['actual_method_id']}")
        print()

    broken_by_mode: dict[str, set] = {}
    for issue in issues:
        mode = issue["mismatch_mode"]
        broken_by_mode.setdefault(mode, set()).add(issue["index"])

    print("Summary by mode:")
    for mode, indices in sorted(broken_by_mode.items()):
        print(f"  {mode}: {len(indices)} broken samples (indices: {sorted(indices, key=int)[:10]}{'...' if len(indices) > 10 else ''})")
    print()

    if args.rerun:
        if not args.config:
            log.error("--rerun requires --config <path-to-config.yaml>")
            sys.exit(1)

        target_indices = sorted(broken_by_mode.get(args.mode, set()), key=int)
        if not target_indices:
            print(f"No broken samples found for mode '{args.mode}'")
            return

        print(f"Re-running {len(target_indices)} broken samples for mode '{args.mode}'...")
        rerun_broken_samples(args.results_dir, args.config, target_indices, args.mode)
        print("Done. Re-run the viewer generator to update the HTML.")


if __name__ == "__main__":
    main()
