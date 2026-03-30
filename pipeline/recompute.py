"""Recompute metrics for existing experiment sample files.

Usage:
    python -m pipeline.recompute <results-dir> [--no-codebleu] [--dry-run]

Loads each sample JSON, recomputes all metrics (including new ones like
LCS-NoIdent and CodeBLEU), updates normalized fields, and writes back.
Regenerates aggregate.json, summary.json, and comparison_table.txt.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

from pipeline.metrics import compute_all_metrics
from pipeline.models import SampleResult
from pipeline.normalize import normalize_code
from pipeline.report import aggregate_metrics, generate_comparison_table

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def _atomic_write_json(data: dict, path: Path) -> None:
    """Write JSON atomically via tempfile + os.replace."""
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


def _discover_modes(results_dir: Path) -> list[Path]:
    """Find mode directories (subdirectories containing a samples/ folder)."""
    modes = []
    for child in sorted(results_dir.iterdir()):
        if child.is_dir() and (child / "samples").is_dir():
            modes.append(child)
    return modes


def recompute_metrics(
    results_dir: Path,
    include_codebleu: bool = True,
    dry_run: bool = False,
) -> None:
    """Recompute metrics for all sample files under results_dir."""
    mode_dirs = _discover_modes(results_dir)
    if not mode_dirs:
        log.error("No mode directories with samples/ found in %s", results_dir)
        sys.exit(1)

    log.info("Found %d mode(s): %s", len(mode_dirs), [d.name for d in mode_dirs])

    all_results: dict[str, list[SampleResult]] = {}

    for mode_dir in mode_dirs:
        mode_name = mode_dir.name
        samples_dir = mode_dir / "samples"
        sample_files = sorted(samples_dir.glob("sample_*.json"))

        if not sample_files:
            log.warning("No sample files in %s", samples_dir)
            continue

        log.info("[%s] Recomputing %d samples (codebleu=%s, dry_run=%s)",
                 mode_name, len(sample_files), include_codebleu, dry_run)

        mode_results: list[SampleResult] = []

        for sample_path in sample_files:
            with open(sample_path, encoding="utf-8") as f:
                raw = json.load(f)

            generated = raw.get("generated")
            ground_truth = raw.get("ground_truth")

            if not generated or not ground_truth:
                log.warning("Skipping %s: missing generated or ground_truth", sample_path.name)
                continue

            # Recompute metrics.
            metrics = compute_all_metrics(
                generated, ground_truth,
                identifier_unify=True,
                include_codebleu=include_codebleu,
            )

            # Recompute normalized fields.
            norm_gen = normalize_code(generated, identifier_unify=True)
            norm_ref = normalize_code(ground_truth, identifier_unify=True)

            # Merge into raw dict, preserving compilability and any other fields.
            old_metrics = raw.get("metrics", {})
            raw["metrics"] = {
                "em": metrics.em,
                "es": metrics.es,
                "iou": metrics.iou,
                "lcs_length": metrics.lcs_length,
                "lcs_ratio": metrics.lcs_ratio,
                "lcs_no_ident_length": metrics.lcs_no_ident_length,
                "lcs_no_ident_ratio": metrics.lcs_no_ident_ratio,
                "codebleu": metrics.codebleu,
                # Preserve compilability fields from the original.
                "compilable": old_metrics.get("compilable"),
                "compile_errors": old_metrics.get("compile_errors", []),
                "compile_exit_code": old_metrics.get("compile_exit_code"),
            }
            raw["normalized_ground_truth"] = norm_ref
            raw["normalized_generated"] = norm_gen

            if not dry_run:
                _atomic_write_json(raw, sample_path)

            # Parse back for aggregation.
            mode_results.append(SampleResult.from_dict(raw))

            log.info(
                "  %s: EM=%s ES=%.4f IoU=%.4f LCS=%.4f LCS-NI=%.4f CB=%s",
                sample_path.name,
                metrics.em, metrics.es, metrics.iou, metrics.lcs_ratio,
                metrics.lcs_no_ident_ratio if metrics.lcs_no_ident_ratio is not None else 0.0,
                f"{metrics.codebleu:.4f}" if metrics.codebleu is not None else "N/A",
            )

        all_results[mode_name] = mode_results

        # Regenerate aggregate.json for this mode.
        if mode_results and not dry_run:
            agg = aggregate_metrics(mode_results)
            _atomic_write_json(agg, mode_dir / "aggregate.json")
            log.info("[%s] Updated aggregate.json", mode_name)

    if not all_results:
        log.error("No samples were processed")
        return

    if not dry_run:
        # Regenerate summary.json.
        summary = {mode: aggregate_metrics(samples) for mode, samples in all_results.items()}
        _atomic_write_json(summary, results_dir / "summary.json")

        # Regenerate comparison table.
        table = generate_comparison_table(all_results)
        table_path = results_dir / "comparison_table.txt"
        with open(table_path, "w", encoding="utf-8") as f:
            f.write(table)

        log.info("Updated summary.json and comparison_table.txt")
        print("\n=== Recomputed Results ===\n")
        print(table)
        print()
    else:
        log.info("Dry run complete — no files were modified")


def main():
    parser = argparse.ArgumentParser(
        description="Recompute metrics for existing experiment samples"
    )
    parser.add_argument(
        "results_dir",
        type=Path,
        help="Path to the results directory containing mode subdirectories",
    )
    parser.add_argument(
        "--no-codebleu",
        action="store_true",
        help="Skip CodeBLEU computation (faster)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute metrics without writing changes to disk",
    )
    args = parser.parse_args()

    if not args.results_dir.is_dir():
        log.error("Results directory does not exist: %s", args.results_dir)
        sys.exit(1)

    recompute_metrics(
        results_dir=args.results_dir,
        include_codebleu=not args.no_codebleu,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
