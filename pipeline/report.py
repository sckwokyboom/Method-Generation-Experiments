from __future__ import annotations

import json
import logging
import os
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from tabulate import tabulate

from pipeline.models import SampleResult

log = logging.getLogger(__name__)


def write_sample_result(
    result: SampleResult,
    output_path: Path,
    save_prompts: bool = True,
    save_responses: bool = True,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sample_data = result.to_dict()
    if not save_prompts:
        sample_data.pop("prompt", None)
    if not save_responses:
        sample_data.pop("generated", None)

    fd, tmp_path = tempfile.mkstemp(
        dir=output_path.parent, suffix=".tmp", prefix=output_path.stem
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(sample_data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, output_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_sample_result(path: Path) -> SampleResult:
    with open(path, encoding="utf-8") as f:
        return SampleResult.from_dict(json.load(f))


def update_progress(mode_dir: Path, mode: str, completed: int, total: int) -> None:
    progress = {
        "mode": mode,
        "completed": completed,
        "total": total,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    progress_path = mode_dir / "progress.json"
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2)


def aggregate_metrics(samples: list[SampleResult]) -> dict:
    if not samples:
        return {}

    em_values = [1 if s.metrics.em else 0 for s in samples]
    es_values = [s.metrics.es for s in samples]
    iou_values = [s.metrics.iou for s in samples]
    lcs_ratio_values = [s.metrics.lcs_ratio for s in samples]
    lcs_length_values = [s.metrics.lcs_length for s in samples]

    lcs_no_ident_ratio_values = [
        s.metrics.lcs_no_ident_ratio for s in samples if s.metrics.lcs_no_ident_ratio is not None
    ]
    lcs_no_ident_length_values = [
        s.metrics.lcs_no_ident_length for s in samples if s.metrics.lcs_no_ident_length is not None
    ]
    codebleu_values = [s.metrics.codebleu for s in samples if s.metrics.codebleu is not None]

    compilable = [s for s in samples if s.compilability is not None]
    comp_values = [1 if s.compilability.success else 0 for s in compilable]

    test_eval_samples = [s for s in samples if s.test_eval is not None]
    test_pass_values = [1 if s.test_eval.success else 0 for s in test_eval_samples]

    recall_values = [s.metrics.recall_at_k for s in samples if s.metrics.recall_at_k is not None]
    api_cov_values = [s.metrics.api_coverage_at_k for s in samples if s.metrics.api_coverage_at_k is not None]
    mrr_values = [s.metrics.mrr for s in samples if s.metrics.mrr is not None]
    precision_values = [s.metrics.retrieval_precision_at_k for s in samples if s.metrics.retrieval_precision_at_k is not None]
    ndcg_values = [s.metrics.retrieval_ndcg_at_k for s in samples if s.metrics.retrieval_ndcg_at_k is not None]
    type_iou_values = [s.metrics.retrieval_type_iou for s in samples if s.metrics.retrieval_type_iou is not None]
    owner_recall_values = [s.metrics.owner_type_recall for s in samples if s.metrics.owner_type_recall is not None]

    def stats(values: list[float]) -> dict:
        if not values:
            return {"mean": 0.0, "median": 0.0, "std": 0.0}
        return {
            "mean": round(statistics.mean(values), 4),
            "median": round(statistics.median(values), 4),
            "std": round(statistics.stdev(values) if len(values) > 1 else 0.0, 4),
        }

    return {
        "sample_count": len(samples),
        "em": stats(em_values),
        "es": stats(es_values),
        "iou": stats(iou_values),
        "lcs_length": stats(lcs_length_values),
        "lcs_ratio": stats(lcs_ratio_values),
        "lcs_no_ident_length": stats(lcs_no_ident_length_values) if lcs_no_ident_length_values else None,
        "lcs_no_ident_ratio": stats(lcs_no_ident_ratio_values) if lcs_no_ident_ratio_values else None,
        "codebleu": stats(codebleu_values) if codebleu_values else None,
        "compilable": stats(comp_values) if comp_values else None,
        "test_pass": stats(test_pass_values) if test_pass_values else None,
        "recall_at_k": stats(recall_values) if recall_values else None,
        "api_coverage_at_k": stats(api_cov_values) if api_cov_values else None,
        "mrr": stats(mrr_values) if mrr_values else None,
        "retrieval_precision_at_k": stats(precision_values) if precision_values else None,
        "retrieval_ndcg_at_k": stats(ndcg_values) if ndcg_values else None,
        "retrieval_type_iou": stats(type_iou_values) if type_iou_values else None,
        "owner_type_recall": stats(owner_recall_values) if owner_recall_values else None,
    }


def generate_comparison_table(all_results: dict[str, list[SampleResult]]) -> str:
    aggregates = {mode: aggregate_metrics(samples) for mode, samples in all_results.items()}

    headers = ["Metric"] + list(all_results.keys())
    rows = []

    for metric_name in ["em", "es", "iou", "lcs_ratio", "lcs_no_ident_ratio", "codebleu",
                        "compilable", "test_pass", "recall_at_k", "api_coverage_at_k", "mrr",
                        "retrieval_precision_at_k", "retrieval_ndcg_at_k",
                        "retrieval_type_iou", "owner_type_recall"]:
        row = [metric_name]
        for mode in all_results:
            agg = aggregates[mode].get(metric_name)
            if agg is None:
                row.append("N/A")
            else:
                row.append(f"{agg['mean']:.4f} (std={agg['std']:.4f})")
        rows.append(row)

    return tabulate(rows, headers=headers, tablefmt="grid")


def generate_report(
    all_results: dict[str, list[SampleResult]],
    output_dir: str | Path,
    save_prompts: bool = True,
    save_responses: bool = True,
    config_summary: dict | None = None,
) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = {}

    for mode, samples in all_results.items():
        mode_dir = output_dir / mode
        mode_dir.mkdir(parents=True, exist_ok=True)

        agg = aggregate_metrics(samples)
        summary[mode] = agg

        with open(mode_dir / "aggregate.json", "w", encoding="utf-8") as f:
            json.dump(agg, f, indent=2)

    with open(output_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    table = generate_comparison_table(all_results)
    with open(output_dir / "comparison_table.txt", "w", encoding="utf-8") as f:
        f.write(table)

    generate_markdown_report(all_results, output_dir, config_summary or {})

    log.info("Report saved to %s", output_dir)
    print("\n=== Experiment Results ===\n")
    print(table)
    print()


def _format_sample_markdown(i: int, sample: SampleResult) -> str:
    m = sample.metrics
    comp_str = "N/A"
    if sample.compilability is not None:
        comp_str = "Yes" if sample.compilability.success else "No"

    lcs_ni = f"{m.lcs_no_ident_ratio:.4f}" if m.lcs_no_ident_ratio is not None else "N/A"
    cb = f"{m.codebleu:.4f}" if m.codebleu is not None else "N/A"

    lines = [
        f"#### Sample {i:03d}: `{sample.method_id}`",
        f"**File**: `{sample.file_path}`  ",
        f"**Signature**: `{sample.method_signature}`",
        "",
        "| EM | ES | IoU | LCS Ratio | LCS-NoIdent | CodeBLEU | Compilable |",
        "|----|-----|-----|-----------|-------------|----------|------------|",
        f"| {1 if m.em else 0} | {m.es:.4f} | {m.iou:.4f} | {m.lcs_ratio:.4f} | {lcs_ni} | {cb} | {comp_str} |",
        "",
    ]

    if sample.invocations_ordered:
        lines.append("**Invocations (ordered)**:")
        for inv in sample.invocations_ordered:
            lines.append(f"- `{inv['signature']}` [{inv['resolution_mode']}]")
        lines.append("")

    if sample.augmentation_block:
        lines.append("**Augmentation block**:")
        lines.append("```java")
        lines.append(sample.augmentation_block)
        lines.append("```")
        lines.append("")

    lines.append("**Ground truth**:")
    lines.append("```java")
    lines.append(sample.ground_truth)
    lines.append("```")
    lines.append("")

    lines.append("**Generated**:")
    lines.append("```java")
    lines.append(sample.generated)
    lines.append("```")
    lines.append("")

    resp = sample.llm_response
    lines.append(
        f"**LLM**: finish_reason={resp.get('finish_reason', '?')}, "
        f"latency={resp.get('latency_ms', '?')}ms, "
        f"tokens={resp.get('usage', {})}"
    )

    if sample.compilability and not sample.compilability.success:
        lines.append("")
        lines.append("**Compilation errors**:")
        for err in sample.compilability.error_messages:
            lines.append(f"```")
            lines.append(err)
            lines.append(f"```")

    if sample.test_eval is not None:
        lines.append("")
        te = sample.test_eval
        status = "PASS" if te.success else "FAIL"
        lines.append(f"**Test evaluation**: {status} (run={te.tests_run}, passed={te.tests_passed}, failed={te.tests_failed})")
        if te.failed_test_names:
            lines.append("")
            lines.append("**Failed tests**:")
            for name in te.failed_test_names[:5]:
                lines.append(f"- `{name}`")

    lines.append("")
    lines.append(f"*Full prompt: see `{sample.mode}/samples/sample_{i:03d}.json`*")
    lines.append("")
    lines.append("---")
    lines.append("")

    return "\n".join(lines)


def generate_markdown_report(
    all_results: dict[str, list[SampleResult]],
    output_dir: Path,
    config_summary: dict,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    model = config_summary.get("model_name", "?")
    sample_count = config_summary.get("sample_count", "?")
    modes = list(all_results.keys())

    lines: list[str] = []

    # Header
    lines.append("# Experiment Report")
    lines.append("")
    lines.append(f"Generated: {now}  ")
    lines.append(f"Model: **{model}**  ")
    lines.append(f"Samples per mode: **{sample_count}**  ")
    lines.append(f"Modes: {', '.join(f'`{m}`' for m in modes)}")
    lines.append("")

    # Table of contents
    lines.append("## Table of Contents")
    lines.append("")
    lines.append("- [1. Summary](#1-summary)")
    lines.append("- [2. Aggregate Metrics](#2-aggregate-metrics)")
    for m in modes:
        anchor = m.replace("_", "-")
        lines.append(f"  - [{m}](#{anchor})")
    lines.append("- [3. Per-Sample Details](#3-per-sample-details)")
    for m in modes:
        anchor = f"mode-{m.replace('_', '-')}"
        lines.append(f"  - [{m}](#{anchor})")
    lines.append("")

    # Section 1: Summary
    lines.append("## 1. Summary")
    lines.append("")

    aggregates = {mode: aggregate_metrics(samples) for mode, samples in all_results.items()}

    # Markdown comparison table
    headers = ["Metric"] + modes
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for metric_name in ["em", "es", "iou", "lcs_ratio", "lcs_no_ident_ratio", "codebleu", "compilable", "test_pass"]:
        row = [metric_name]
        for mode in modes:
            agg = aggregates[mode].get(metric_name)
            if agg is None:
                row.append("N/A")
            else:
                row.append(f"{agg['mean']:.4f} (std={agg['std']:.4f})")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # Key findings
    lines.append("### Key Findings")
    lines.append("")
    for metric_name in ["em", "es", "iou", "lcs_ratio", "lcs_no_ident_ratio", "codebleu",
                        "compilable", "test_pass", "recall_at_k", "api_coverage_at_k", "mrr",
                        "retrieval_precision_at_k", "retrieval_ndcg_at_k",
                        "retrieval_type_iou", "owner_type_recall"]:
        best_mode = None
        best_val = -1.0
        for mode in modes:
            agg = aggregates[mode].get(metric_name)
            if agg and agg["mean"] > best_val:
                best_val = agg["mean"]
                best_mode = mode
        if best_mode:
            lines.append(f"- Best **{metric_name}**: `{best_mode}` ({best_val:.4f})")
    lines.append("")

    # Section 2: Aggregate Metrics
    lines.append("## 2. Aggregate Metrics")
    lines.append("")

    for mode in modes:
        agg = aggregates[mode]
        lines.append(f"### {mode}")
        lines.append("")
        lines.append(f"Sample count: {agg.get('sample_count', 0)}")
        lines.append("")
        lines.append("| Metric | Mean | Median | Std |")
        lines.append("|--------|------|--------|-----|")
        for metric_name in ["em", "es", "iou", "lcs_length", "lcs_ratio", "lcs_no_ident_length",
                            "lcs_no_ident_ratio", "codebleu", "compilable", "test_pass",
                            "recall_at_k", "api_coverage_at_k", "mrr",
                            "retrieval_precision_at_k", "retrieval_ndcg_at_k",
                            "retrieval_type_iou", "owner_type_recall"]:
            m = agg.get(metric_name)
            if m is None:
                lines.append(f"| {metric_name} | N/A | N/A | N/A |")
            else:
                lines.append(f"| {metric_name} | {m['mean']:.4f} | {m['median']:.4f} | {m['std']:.4f} |")
        lines.append("")

    # Section 3: Per-Sample Details
    lines.append("## 3. Per-Sample Details")
    lines.append("")

    for mode in modes:
        anchor = f"mode-{mode.replace('_', '-')}"
        lines.append(f"### <a id=\"{anchor}\"></a>Mode: `{mode}`")
        lines.append("")
        samples = all_results[mode]
        for i, sample in enumerate(samples):
            lines.append(_format_sample_markdown(i, sample))

    report_path = output_dir / "report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info("Markdown report saved to %s", report_path)
