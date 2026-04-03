"""Recompute metrics and/or compilability for existing experiment sample files.

Usage:
    python -m pipeline.recompute <results-dir> [options]

Options:
    --no-codebleu                Skip CodeBLEU computation (faster)
    --recompute-compilability    Re-run javac compilability checks
    --extraction-json PATH       Path to extracted_methods.json (required for compilability)
    --source-version VER         Java source version for javac (default: 21)
    --java-home PATH             Path to JDK (optional, uses system javac if not set)
    --dry-run                    Compute without writing changes
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

from pipeline.metrics import (
    compute_all_metrics,
    invocation_recall_at_k, api_coverage_at_k, mrr_for_similar_method,
    retrieval_precision_at_k, retrieval_ndcg_at_k, retrieval_type_iou, owner_type_recall,
)
from pipeline.models import ExtractedMethod, ResolvedInvocation, RetrievalResult, SampleResult
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


def _load_methods_index(extraction_path: Path) -> dict[str, ExtractedMethod]:
    """Load extraction JSON and build a method_id -> ExtractedMethod index."""
    log.info("Loading extraction data from %s", extraction_path)
    with open(extraction_path, encoding="utf-8") as f:
        raw = json.load(f)

    classpath = raw.get("classpath", [])
    index: dict[str, ExtractedMethod] = {}

    for m_raw in raw["methods"]:
        method = ExtractedMethod.from_dict(m_raw)
        method_id = f"{method.class_fqn}#{method.method_name}"
        index[method_id] = method

    log.info("Loaded %d methods, %d classpath entries", len(index), len(classpath))
    return index, classpath


def recompute_metrics(
    results_dir: Path,
    include_codebleu: bool = True,
    recompute_compilability: bool = False,
    recompute_retrieval: bool = False,
    extraction_json: Path | None = None,
    source_version: str = "21",
    java_home: str | None = None,
    dry_run: bool = False,
) -> None:
    """Recompute metrics for all sample files under results_dir."""
    mode_dirs = _discover_modes(results_dir)
    if not mode_dirs:
        log.error("No mode directories with samples/ found in %s", results_dir)
        sys.exit(1)

    log.info("Found %d mode(s): %s", len(mode_dirs), [d.name for d in mode_dirs])

    # Load extraction data if needed.
    methods_index: dict[str, ExtractedMethod] = {}
    classpath: list[str] = []
    needs_extraction = recompute_compilability or recompute_retrieval
    if needs_extraction:
        if not extraction_json:
            default = results_dir / "extracted_methods.json"
            if default.exists():
                extraction_json = default
        if extraction_json and extraction_json.exists():
            methods_index, classpath = _load_methods_index(extraction_json)
        elif recompute_compilability:
            log.error(
                "--recompute-compilability requires --extraction-json or "
                "extracted_methods.json in the results directory"
            )
            sys.exit(1)
        else:
            log.warning(
                "Extraction data not found — retrieval_type_iou will not be recomputed. "
                "Other retrieval metrics will still be recomputed from saved data."
            )

    all_results: dict[str, list[SampleResult]] = {}

    for mode_dir in mode_dirs:
        mode_name = mode_dir.name
        samples_dir = mode_dir / "samples"
        sample_files = sorted(samples_dir.glob("sample_*.json"))

        if not sample_files:
            log.warning("No sample files in %s", samples_dir)
            continue

        log.info(
            "[%s] Recomputing %d samples (codebleu=%s, compilability=%s, dry_run=%s)",
            mode_name, len(sample_files), include_codebleu, recompute_compilability, dry_run,
        )

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

            # Preserve or recompute compilability.
            old_metrics = raw.get("metrics", {})
            comp_success = old_metrics.get("compilable")
            comp_errors = old_metrics.get("compile_errors", [])
            comp_exit_code = old_metrics.get("compile_exit_code")

            if recompute_compilability:
                method_id = raw.get("method_id", "")
                method = methods_index.get(method_id)
                if method:
                    from pipeline.compilability import check_compilability
                    comp_result = check_compilability(
                        method, generated, classpath,
                        java_home=java_home,
                        source_version=source_version,
                    )
                    comp_success = comp_result.success
                    comp_errors = comp_result.error_messages
                    comp_exit_code = comp_result.exit_code
                else:
                    log.warning(
                        "  %s: method_id '%s' not found in extraction data, skipping compilability",
                        sample_path.name, method_id,
                    )

            # Retrieval metrics: recompute from saved data or preserve old values.
            old_metrics = raw.get("metrics", {})
            ret_recall = old_metrics.get("recall_at_k")
            ret_api_cov = old_metrics.get("api_coverage_at_k")
            ret_mrr = old_metrics.get("mrr")
            ret_precision = old_metrics.get("retrieval_precision_at_k")
            ret_ndcg = old_metrics.get("retrieval_ndcg_at_k")
            ret_type_iou = old_metrics.get("retrieval_type_iou")
            ret_owner_recall = old_metrics.get("owner_type_recall")

            if recompute_retrieval:
                raw_retrieval = raw.get("retrieval_results")
                raw_invocations = raw.get("invocations_ordered", [])

                if raw_retrieval is None:
                    # Not a retrieval mode sample — skip silently
                    pass
                elif not raw_invocations:
                    log.debug("  %s: no invocations_ordered, skipping retrieval recompute",
                              sample_path.name)
                else:
                    rr = [RetrievalResult.from_dict(r) for r in raw_retrieval]
                    oracle = [ResolvedInvocation(
                        signature=inv["signature"],
                        resolution_mode=inv["resolution_mode"],
                        order_index=inv["order_index"],
                    ) for inv in raw_invocations]

                    aug_block = raw.get("augmentation_block") or ""
                    ret_recall = invocation_recall_at_k(aug_block, oracle)
                    ret_api_cov = api_coverage_at_k(rr, oracle)
                    ret_mrr = mrr_for_similar_method(rr, ground_truth)
                    ret_precision = retrieval_precision_at_k(rr, oracle)
                    ret_ndcg = retrieval_ndcg_at_k(rr, oracle)
                    ret_owner_recall = owner_type_recall(rr, oracle)

                    # type_iou needs method metadata from extraction
                    method_id = raw.get("method_id", "")
                    method = methods_index.get(method_id) if methods_index else None
                    if method:
                        ret_type_iou = retrieval_type_iou(
                            rr, method.imports, method.class_fields,
                            method.parameter_types, method.return_type,
                        )

                    log.info("  %s: retrieval recomputed — recall=%.3f precision=%.3f ndcg=%.3f",
                             sample_path.name, ret_recall, ret_precision, ret_ndcg)

            raw["metrics"] = {
                "em": metrics.em,
                "es": metrics.es,
                "iou": metrics.iou,
                "lcs_length": metrics.lcs_length,
                "lcs_ratio": metrics.lcs_ratio,
                "lcs_no_ident_length": metrics.lcs_no_ident_length,
                "lcs_no_ident_ratio": metrics.lcs_no_ident_ratio,
                "gen_token_count": metrics.gen_token_count,
                "ref_token_count": metrics.ref_token_count,
                "lcs_tokens": metrics.lcs_tokens,
                "codebleu": metrics.codebleu,
                "recall_at_k": ret_recall,
                "api_coverage_at_k": ret_api_cov,
                "mrr": ret_mrr,
                "retrieval_precision_at_k": ret_precision,
                "retrieval_ndcg_at_k": ret_ndcg,
                "retrieval_type_iou": ret_type_iou,
                "owner_type_recall": ret_owner_recall,
                "compilable": comp_success,
                "compile_errors": comp_errors,
                "compile_exit_code": comp_exit_code,
            }
            raw["normalized_ground_truth"] = norm_ref
            raw["normalized_generated"] = norm_gen

            if not dry_run:
                _atomic_write_json(raw, sample_path)

            # Parse back for aggregation.
            mode_results.append(SampleResult.from_dict(raw))

            comp_str = "N/A"
            if comp_success is not None:
                comp_str = "OK" if comp_success else f"FAIL({len(comp_errors)} errs)"

            log.info(
                "  %s: EM=%s ES=%.4f LCS=%.4f LCS-NI=%.4f CB=%s Comp=%s",
                sample_path.name,
                metrics.em, metrics.es, metrics.lcs_ratio,
                metrics.lcs_no_ident_ratio if metrics.lcs_no_ident_ratio is not None else 0.0,
                f"{metrics.codebleu:.4f}" if metrics.codebleu is not None else "N/A",
                comp_str,
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
        description="Recompute metrics and/or compilability for existing experiment samples"
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
        "--recompute-compilability",
        action="store_true",
        help="Re-run javac compilability checks (requires extraction data)",
    )
    parser.add_argument(
        "--recompute-retrieval",
        action="store_true",
        help="Recompute retrieval diagnostic metrics from saved retrieval_results",
    )
    parser.add_argument(
        "--extraction-json",
        type=Path,
        default=None,
        help="Path to extracted_methods.json (default: <results-dir>/extracted_methods.json)",
    )
    parser.add_argument(
        "--source-version",
        default="21",
        help="Java source version for javac (default: 21)",
    )
    parser.add_argument(
        "--java-home",
        default=None,
        help="Path to JDK home (uses system javac if not set)",
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
        recompute_compilability=args.recompute_compilability,
        recompute_retrieval=args.recompute_retrieval,
        extraction_json=args.extraction_json,
        source_version=args.source_version,
        java_home=args.java_home,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
