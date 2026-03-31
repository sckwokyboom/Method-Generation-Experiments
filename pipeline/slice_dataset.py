"""Slice the dataset into diagnostic groups for targeted experiments.

Creates separate dataset JSON files for different sample categories,
enabling A/B analysis of where retrieval helps vs hurts.

Usage:
    python -m pipeline.slice_dataset --config config.yaml

Produces sliced datasets in results/slices/ and prints run instructions.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pipeline.config import Config
from pipeline.dataset import load_extraction, filter_methods, sample_dataset

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def _classify_api_type(method) -> str:
    """Classify by dominant API type in invocations."""
    if not method.invocations:
        return "no_invocations"
    project_count = sum(
        1 for inv in method.invocations
        if inv.resolution_mode == "EXACT"
        and "java." not in inv.signature
        and "javax." not in inv.signature
    )
    total = sum(1 for inv in method.invocations if inv.resolution_mode == "EXACT")
    if total == 0:
        return "no_invocations"
    ratio = project_count / total
    if ratio > 0.7:
        return "project_api"
    elif ratio < 0.3:
        return "standard_api"
    return "mixed_api"


def _classify_complexity(method) -> str:
    """Classify by invocation count (proxy for complexity)."""
    n = len(method.invocations)
    if n <= 3:
        return "simple"
    elif n <= 10:
        return "medium"
    return "complex"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Slice dataset into diagnostic groups")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    config = Config.load(args.config)
    extraction = load_extraction(config.extraction.output)
    methods = filter_methods(
        extraction.methods,
        config.extraction.exclude_categories,
        config.extraction.min_statements,
    )
    sampled = sample_dataset(methods, config.dataset.sample_count, config.dataset.random_seed)

    slices_dir = Path(config.output.dir) / "slices"
    slices_dir.mkdir(parents=True, exist_ok=True)

    # Define slices
    slices = {
        # By API type
        "project_api": [i for i, m in enumerate(sampled) if _classify_api_type(m) == "project_api"],
        "standard_api": [i for i, m in enumerate(sampled) if _classify_api_type(m) == "standard_api"],
        "mixed_api": [i for i, m in enumerate(sampled) if _classify_api_type(m) == "mixed_api"],

        # By complexity
        "simple": [i for i, m in enumerate(sampled) if _classify_complexity(m) == "simple"],
        "medium": [i for i, m in enumerate(sampled) if _classify_complexity(m) == "medium"],
        "complex": [i for i, m in enumerate(sampled) if _classify_complexity(m) == "complex"],

        # Full set for reference
        "all": list(range(len(sampled))),
    }

    print(f"\nDataset: {len(sampled)} samples total\n")
    print(f"{'Slice':<20s} {'Count':>6s}  {'Avg invoc':>10s}  {'Avg stmts':>10s}  Sample indices")
    print("─" * 90)

    for name, indices in sorted(slices.items()):
        if not indices:
            continue
        subset = [sampled[i] for i in indices]
        avg_inv = sum(len(m.invocations) for m in subset) / len(subset)
        avg_stm = sum(m.statement_count for m in subset) / len(subset)
        idx_preview = str(indices[:8])[1:-1]
        if len(indices) > 8:
            idx_preview += ", ..."
        print(f"{name:<20s} {len(indices):>6d}  {avg_inv:>10.1f}  {avg_stm:>10.1f}  [{idx_preview}]")

        # Save slice info
        slice_data = {
            "name": name,
            "count": len(indices),
            "indices": indices,
            "methods": [
                {
                    "index": i,
                    "method_id": f"{sampled[i].class_fqn}#{sampled[i].method_name}",
                    "invocation_count": len(sampled[i].invocations),
                    "statement_count": sampled[i].statement_count,
                    "api_type": _classify_api_type(sampled[i]),
                    "complexity": _classify_complexity(sampled[i]),
                }
                for i in indices
            ],
        }
        with open(slices_dir / f"{name}.json", "w", encoding="utf-8") as f:
            json.dump(slice_data, f, indent=2, ensure_ascii=False)

    print(f"\nSlice files saved to {slices_dir}/")
    print()
    print("=" * 90)
    print("HOW TO RUN EXPERIMENTS")
    print("=" * 90)
    print("""
1. Run the full experiment (both modes, all 100 samples):

   JAVA_HOME=/path/to/jdk17 python -m pipeline.run \\
     --config config.yaml \\
     --mode no_augmentation retrieval_augmentation \\
     --output-dir ./results

2. After the run, analyze per-slice performance:

   python -m pipeline.analyze_retrieval ./results

3. Share with me:
   a) The comparison_table.txt from the results
   b) The terminal output from analyze_retrieval
   c) The slices/*.json files (so I can cross-reference sample indices)
   d) The summary.json

These files contain only metric numbers and sample indices — no generated code.
""")


if __name__ == "__main__":
    main()
