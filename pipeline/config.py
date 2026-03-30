from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ProjectConfig:
    name: str
    path: str
    tag: str
    fallback_tag: str = ""


@dataclass
class ExtractionConfig:
    extractor_jar: str
    min_statements: int
    exclude_categories: list[str]
    output: str


@dataclass
class DatasetConfig:
    sample_count: int
    random_seed: int
    output: str


@dataclass
class LLMConfig:
    endpoint_url: str
    model_name: str
    temperature: float
    max_tokens: int
    stop_sequences: list[str]
    num_generations: int
    seed: int
    timeout_seconds: int
    max_concurrent_requests: int = 1


@dataclass
class ExperimentConfig:
    modes: list[str]
    shuffle_seed: int


@dataclass
class CompilabilityConfig:
    enabled: bool
    mode: str  # "file" | "project"


@dataclass
class OutputConfig:
    dir: str
    save_prompts: bool
    save_responses: bool


@dataclass
class Config:
    project: ProjectConfig
    extraction: ExtractionConfig
    dataset: DatasetConfig
    llm: LLMConfig
    experiment: ExperimentConfig
    compilability: CompilabilityConfig
    output: OutputConfig

    @classmethod
    def load(cls, path: str | Path) -> Config:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        return cls(
            project=ProjectConfig(**raw["project"]),
            extraction=ExtractionConfig(**raw["extraction"]),
            dataset=DatasetConfig(**raw["dataset"]),
            llm=LLMConfig(**raw["llm"]),
            experiment=ExperimentConfig(**raw["experiment"]),
            compilability=CompilabilityConfig(**raw["compilability"]),
            output=OutputConfig(**raw["output"]),
        )

    def to_dict(self) -> dict:
        import dataclasses
        def _convert(obj):
            if dataclasses.is_dataclass(obj):
                return {k: _convert(v) for k, v in dataclasses.asdict(obj).items()}
            return obj
        return _convert(self)
