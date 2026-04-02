from __future__ import annotations

import json
import logging
import random
from pathlib import Path

from pipeline.config import Config
from pipeline.coverage import CoverageMap
from pipeline.models import ExtractionData, ExtractedMethod

log = logging.getLogger(__name__)


def load_extraction(path: str | Path) -> ExtractionData:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return ExtractionData.from_dict(raw)


def filter_methods(
    methods: list[ExtractedMethod],
    exclude_categories: list[str],
    min_statements: int,
    require_non_void: bool = False,
    require_parameters: bool = False,
    require_test_coverage: bool = False,
    coverage_map: CoverageMap | None = None,
) -> list[ExtractedMethod]:
    filtered = []
    stats = {"category": 0, "statements": 0, "invocations": 0, "unresolved": 0,
             "void": 0, "no_params": 0, "no_coverage": 0}
    for m in methods:
        if m.category in exclude_categories:
            stats["category"] += 1
            continue
        if m.statement_count < min_statements:
            stats["statements"] += 1
            continue
        if not m.invocations:
            stats["invocations"] += 1
            continue
        if any(inv.resolution_mode != "EXACT" for inv in m.invocations):
            stats["unresolved"] += 1
            continue
        if require_non_void and (m.return_type is None or m.return_type == "void"):
            stats["void"] += 1
            continue
        if require_parameters and not m.parameter_types:
            stats["no_params"] += 1
            continue
        if require_test_coverage:
            if coverage_map is None:
                raise ValueError("require_test_coverage is set but no coverage_map provided")
            if not coverage_map.is_method_covered(m.class_fqn, m.method_name, m.parameter_types):
                stats["no_coverage"] += 1
                continue
        filtered.append(m)
    log.info(
        "Filtered %d -> %d methods. Rejected: %s",
        len(methods), len(filtered),
        ", ".join(f"{k}={v}" for k, v in stats.items() if v > 0),
    )
    return filtered


def sample_dataset(
    methods: list[ExtractedMethod],
    sample_count: int,
    seed: int,
) -> list[ExtractedMethod]:
    rng = random.Random(seed)
    if sample_count >= len(methods):
        log.warning(
            "Requested %d samples but only %d available, using all",
            sample_count, len(methods),
        )
        return list(methods)
    sampled = rng.sample(methods, sample_count)
    log.info("Sampled %d methods (seed=%d)", len(sampled), seed)
    return sampled


def build_dataset(
    config: Config,
    coverage_map: CoverageMap | None = None,
) -> tuple[list[ExtractedMethod], list[str]]:
    extraction = load_extraction(config.extraction.output)
    methods = filter_methods(
        extraction.methods,
        config.extraction.exclude_categories,
        config.extraction.min_statements,
        require_non_void=config.extraction.require_non_void,
        require_parameters=config.extraction.require_parameters,
        require_test_coverage=config.extraction.require_test_coverage,
        coverage_map=coverage_map,
    )
    sampled = sample_dataset(methods, config.dataset.sample_count, config.dataset.random_seed)

    dataset_path = Path(config.dataset.output)
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dataset_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "sample_count": len(sampled),
                "seed": config.dataset.random_seed,
                "methods": [
                    {
                        "file_path": m.file_path,
                        "class_fqn": m.class_fqn,
                        "method_name": m.method_name,
                        "statement_count": m.statement_count,
                        "invocation_count": len(m.invocations),
                    }
                    for m in sampled
                ],
            },
            f,
            indent=2,
        )
    log.info("Saved dataset index to %s", dataset_path)

    return sampled, extraction.classpath
