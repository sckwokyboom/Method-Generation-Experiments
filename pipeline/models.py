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
    invocations_as_used: list[ResolvedInvocation]
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
    lcs_no_ident_length: int | None = None
    lcs_no_ident_ratio: float | None = None
    codebleu: float | None = None


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
    method_signature: str
    ground_truth: str
    generated: str
    normalized_ground_truth: str
    normalized_generated: str
    invocations_ordered: list[dict]
    invocations_as_used: list[dict]
    augmentation_block: str | None
    prompt: str
    metrics: MetricsResult
    compilability: CompilabilityResult | None
    llm_response: dict

    def to_dict(self) -> dict:
        return {
            "method_id": self.method_id,
            "file_path": self.file_path,
            "mode": self.mode,
            "method_signature": self.method_signature,
            "ground_truth": self.ground_truth,
            "generated": self.generated,
            "normalized_ground_truth": self.normalized_ground_truth,
            "normalized_generated": self.normalized_generated,
            "invocations_ordered": self.invocations_ordered,
            "invocations_as_used": self.invocations_as_used,
            "augmentation_block": self.augmentation_block,
            "prompt": self.prompt,
            "metrics": {
                "em": self.metrics.em,
                "es": self.metrics.es,
                "iou": self.metrics.iou,
                "lcs_length": self.metrics.lcs_length,
                "lcs_ratio": self.metrics.lcs_ratio,
                "lcs_no_ident_length": self.metrics.lcs_no_ident_length,
                "lcs_no_ident_ratio": self.metrics.lcs_no_ident_ratio,
                "codebleu": self.metrics.codebleu,
                "compilable": self.compilability.success if self.compilability else None,
                "compile_errors": self.compilability.error_messages if self.compilability else [],
                "compile_exit_code": self.compilability.exit_code if self.compilability else None,
            },
            "llm_response": self.llm_response,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SampleResult:
        metrics_d = d["metrics"]
        metrics = MetricsResult(
            em=metrics_d["em"],
            es=metrics_d["es"],
            iou=metrics_d["iou"],
            lcs_length=metrics_d["lcs_length"],
            lcs_ratio=metrics_d["lcs_ratio"],
            lcs_no_ident_length=metrics_d.get("lcs_no_ident_length"),
            lcs_no_ident_ratio=metrics_d.get("lcs_no_ident_ratio"),
            codebleu=metrics_d.get("codebleu"),
        )
        comp = None
        if metrics_d.get("compilable") is not None:
            comp = CompilabilityResult(
                success=metrics_d["compilable"],
                error_messages=metrics_d.get("compile_errors", []),
                exit_code=metrics_d.get("compile_exit_code", 0),
            )
        return cls(
            method_id=d["method_id"],
            file_path=d["file_path"],
            mode=d["mode"],
            method_signature=d["method_signature"],
            ground_truth=d["ground_truth"],
            generated=d.get("generated", ""),
            normalized_ground_truth=d.get("normalized_ground_truth", ""),
            normalized_generated=d.get("normalized_generated", ""),
            invocations_ordered=d.get("invocations_ordered", []),
            invocations_as_used=d.get("invocations_as_used", []),
            augmentation_block=d.get("augmentation_block"),
            prompt=d.get("prompt", ""),
            metrics=metrics,
            compilability=comp,
            llm_response=d.get("llm_response", {}),
        )
