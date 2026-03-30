from __future__ import annotations

import json
import logging
import statistics
from pathlib import Path

from tabulate import tabulate

from pipeline.models import SampleResult

log = logging.getLogger(__name__)


def aggregate_metrics(samples: list[SampleResult]) -> dict:
    if not samples:
        return {}

    em_values = [1 if s.metrics.em else 0 for s in samples]
    es_values = [s.metrics.es for s in samples]
    iou_values = [s.metrics.iou for s in samples]
    lcs_ratio_values = [s.metrics.lcs_ratio for s in samples]
    lcs_length_values = [s.metrics.lcs_length for s in samples]

    compilable = [s for s in samples if s.compilability is not None]
    comp_values = [1 if s.compilability.success else 0 for s in compilable]

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
        "compilable": stats(comp_values) if comp_values else None,
    }


def generate_comparison_table(all_results: dict[str, list[SampleResult]]) -> str:
    aggregates = {mode: aggregate_metrics(samples) for mode, samples in all_results.items()}

    headers = ["Metric"] + list(all_results.keys())
    rows = []

    for metric_name in ["em", "es", "iou", "lcs_ratio", "compilable"]:
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
) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = {}

    for mode, samples in all_results.items():
        mode_dir = output_dir / mode
        samples_dir = mode_dir / "samples"
        samples_dir.mkdir(parents=True, exist_ok=True)

        agg = aggregate_metrics(samples)
        summary[mode] = agg

        with open(mode_dir / "aggregate.json", "w", encoding="utf-8") as f:
            json.dump(agg, f, indent=2)

        for i, sample in enumerate(samples):
            sample_data = sample.to_dict()
            if not save_prompts:
                sample_data.pop("prompt", None)
            if not save_responses:
                sample_data.pop("generated", None)

            with open(samples_dir / f"sample_{i:03d}.json", "w", encoding="utf-8") as f:
                json.dump(sample_data, f, indent=2, ensure_ascii=False)

    with open(output_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    table = generate_comparison_table(all_results)
    with open(output_dir / "comparison_table.txt", "w", encoding="utf-8") as f:
        f.write(table)

    log.info("Report saved to %s", output_dir)
    print("\n=== Experiment Results ===\n")
    print(table)
    print()
