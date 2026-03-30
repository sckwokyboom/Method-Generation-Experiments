from __future__ import annotations

import json
import logging
import random
from pathlib import Path

from pipeline.config import Config
from pipeline.models import ExtractionData, ExtractedMethod

log = logging.getLogger(__name__)


def load_extraction(path: str | Path) -> ExtractionData:
    with open(path) as f:
        raw = json.load(f)
    return ExtractionData.from_dict(raw)


def filter_methods(
    methods: list[ExtractedMethod],
    exclude_categories: list[str],
    min_statements: int,
) -> list[ExtractedMethod]:
    filtered = []
    for m in methods:
        if m.category in exclude_categories:
            continue
        if m.statement_count < min_statements:
            continue
        if not m.invocations:
            continue
        filtered.append(m)
    log.info("Filtered %d -> %d methods", len(methods), len(filtered))
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


def build_dataset(config: Config) -> tuple[list[ExtractedMethod], list[str]]:
    extraction = load_extraction(config.extraction.output)
    methods = filter_methods(
        extraction.methods,
        config.extraction.exclude_categories,
        config.extraction.min_statements,
    )
    sampled = sample_dataset(methods, config.dataset.sample_count, config.dataset.random_seed)

    dataset_path = Path(config.dataset.output)
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dataset_path, "w") as f:
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
