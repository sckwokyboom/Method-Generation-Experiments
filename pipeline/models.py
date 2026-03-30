from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ResolvedInvocation:
    signature: str
    resolution_mode: str  # EXACT | UNRESOLVED
    order_index: int

    @classmethod
    def from_dict(cls, d: dict) -> ResolvedInvocation:
        return cls(
            signature=d["signature"],
            resolution_mode=d["resolutionMode"],
            order_index=d["orderIndex"],
        )


@dataclass
class ExtractedMethod:
    file_path: str
    file_content: str
    class_fqn: str
    method_signature: str
    method_name: str
    method_body: str
    body_start_offset: int
    body_end_offset: int
    statement_count: int
    category: str
    invocations: list[ResolvedInvocation]

    @classmethod
    def from_dict(cls, d: dict) -> ExtractedMethod:
        return cls(
            file_path=d["filePath"],
            file_content=d["fileContent"],
            class_fqn=d["classFqn"],
            method_signature=d["methodSignature"],
            method_name=d["methodName"],
            method_body=d["methodBody"],
            body_start_offset=d["bodyStartOffset"],
            body_end_offset=d["bodyEndOffset"],
            statement_count=d["statementCount"],
            category=d["category"],
            invocations=[ResolvedInvocation.from_dict(inv) for inv in d.get("invocations", [])],
        )


@dataclass
class ExtractionData:
    project_name: str
    classpath: list[str]
    methods: list[ExtractedMethod]

    @classmethod
    def from_dict(cls, d: dict) -> ExtractionData:
        return cls(
            project_name=d["projectName"],
            classpath=d.get("classpath", []),
            methods=[ExtractedMethod.from_dict(m) for m in d["methods"]],
        )


@dataclass
class FIMPrompt:
    prefix: str
    suffix: str
    ground_truth: str
    augmentation_block: str | None
    full_prompt: str


@dataclass
class CompletionResult:
    text: str
    finish_reason: str
    usage: dict
    latency_ms: float
    raw_response: dict


@dataclass
class MetricsResult:
    em: bool
    es: float
    iou: float
    lcs_length: int
    lcs_ratio: float


@dataclass
class CompilabilityResult:
    success: bool
    error_messages: list[str]
    exit_code: int


@dataclass
class SampleResult:
    method_id: str
    file_path: str
    mode: str
    prompt: str
    ground_truth: str
    generated: str
    normalized_ground_truth: str
    normalized_generated: str
    metrics: MetricsResult
    compilability: CompilabilityResult | None
    llm_response: dict

    def to_dict(self) -> dict:
        return {
            "method_id": self.method_id,
            "file_path": self.file_path,
            "mode": self.mode,
            "prompt": self.prompt,
            "ground_truth": self.ground_truth,
            "generated": self.generated,
            "normalized_ground_truth": self.normalized_ground_truth,
            "normalized_generated": self.normalized_generated,
            "metrics": {
                "em": self.metrics.em,
                "es": self.metrics.es,
                "iou": self.metrics.iou,
                "lcs_length": self.metrics.lcs_length,
                "lcs_ratio": self.metrics.lcs_ratio,
                "compilable": self.compilability.success if self.compilability else None,
                "compile_errors": self.compilability.error_messages if self.compilability else [],
            },
            "llm_response": self.llm_response,
        }
