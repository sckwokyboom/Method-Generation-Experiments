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
class ClassField:
    name: str
    type_fqn: str

    @classmethod
    def from_dict(cls, d: dict) -> ClassField:
        return cls(name=d["name"], type_fqn=d["typeFqn"])


@dataclass
class SiblingMethod:
    signature: str
    method_name: str
    parameter_types: list[str]
    return_type: str | None
    invocations: list[ResolvedInvocation]
    used_types: list[str]

    @classmethod
    def from_dict(cls, d: dict) -> SiblingMethod:
        return cls(
            signature=d["signature"],
            method_name=d.get("methodName", ""),
            parameter_types=d.get("parameterTypes", []),
            return_type=d.get("returnType"),
            invocations=[ResolvedInvocation.from_dict(inv) for inv in d.get("invocations", [])],
            used_types=d.get("usedTypes", []),
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
    imports: list[str] = field(default_factory=list)
    class_fields: list[ClassField] = field(default_factory=list)
    supertypes: list[str] = field(default_factory=list)
    sibling_methods: list[SiblingMethod] = field(default_factory=list)
    return_type: str | None = None
    parameter_types: list[str] = field(default_factory=list)
    thrown_exceptions: list[str] = field(default_factory=list)
    used_types: list[str] = field(default_factory=list)

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
            imports=d.get("imports", []),
            class_fields=[ClassField.from_dict(f) for f in d.get("classFields", [])],
            supertypes=d.get("supertypes", []),
            sibling_methods=[SiblingMethod.from_dict(s) for s in d.get("siblingMethods", [])],
            return_type=d.get("returnType"),
            parameter_types=d.get("parameterTypes", []),
            thrown_exceptions=d.get("thrownExceptions", []),
            used_types=d.get("usedTypes", []),
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
class RetrievalResult:
    method_id: str
    file_path: str
    class_fqn: str
    signature: str
    method_body: str
    method_card: str
    invocation_profile: str
    type_profile: str
    score: float
    rank: int
    explain: str = ""
    content: str = ""
    tag_scores: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> RetrievalResult:
        return cls(
            method_id=d.get("id", ""),
            file_path=d.get("filePath", ""),
            class_fqn=d.get("classFqn", ""),
            signature=d.get("signature", ""),
            method_body=d.get("methodBody", ""),
            method_card=d.get("methodCard", ""),
            invocation_profile=d.get("invocationProfile", ""),
            type_profile=d.get("typeProfile", ""),
            score=d.get("score", 0.0),
            rank=d.get("rank", 0),
            explain=d.get("explain", ""),
            content=d.get("content") or "",
            tag_scores=d.get("tagScores", {}),
        )

    def to_dict(self) -> dict:
        d = {
            "id": self.method_id,
            "filePath": self.file_path,
            "classFqn": self.class_fqn,
            "signature": self.signature,
            "methodBody": self.method_body,
            "methodCard": self.method_card,
            "invocationProfile": self.invocation_profile,
            "typeProfile": self.type_profile,
            "score": self.score,
            "rank": self.rank,
            "explain": self.explain,
        }
        if self.content:
            d["content"] = self.content
        if self.tag_scores:
            d["tagScores"] = self.tag_scores
        return d


@dataclass
class RetrievalResponse:
    results: list[RetrievalResult]
    total_hits: int
    search_time_ms: float
    query_debug: str

    @classmethod
    def from_dict(cls, d: dict) -> RetrievalResponse:
        return cls(
            results=[RetrievalResult.from_dict(r) for r in d.get("results", [])],
            total_hits=d.get("totalHits", 0),
            search_time_ms=d.get("searchTimeMs", 0),
            query_debug=d.get("queryDebug", ""),
        )

    def to_dict(self) -> dict:
        return {
            "results": [r.to_dict() for r in self.results],
            "totalHits": self.total_hits,
            "searchTimeMs": self.search_time_ms,
            "queryDebug": self.query_debug,
        }


@dataclass
class MetricsResult:
    # --- Generation quality metrics ---
    em: bool
    es: float
    iou: float
    lcs_length: int
    lcs_ratio: float
    lcs_no_ident_length: int | None = None
    lcs_no_ident_ratio: float | None = None
    gen_token_count: int | None = None
    ref_token_count: int | None = None
    lcs_tokens: list[str] | None = None
    lcsubstring_no_ident_length: int | None = None
    lcsubstring_no_ident_ratio: float | None = None
    lcsubstring_no_ident_tokens: list[str] | None = None
    codebleu: float | None = None

    # --- Retrieval quality metrics ---
    recall_at_k: float | None = None
    api_coverage_at_k: float | None = None
    mrr: float | None = None

    # --- Retrieval diagnostic metrics ---
    # Precision: fraction of retrieved methods that contain at least 1 needed API
    retrieval_precision_at_k: float | None = None
    # NDCG: normalized discounted cumulative gain (relevant = has oracle API overlap)
    retrieval_ndcg_at_k: float | None = None
    # Type IoU between query types and retrieved types (averaged over results)
    retrieval_type_iou: float | None = None
    # How many unique oracle owner types are covered by retrieved methods
    owner_type_recall: float | None = None


@dataclass
class CompilabilityResult:
    success: bool
    error_messages: list[str]
    exit_code: int


@dataclass
class TestEvalResult:
    success: bool
    tests_run: int
    tests_passed: int
    tests_failed: int
    failed_test_names: list[str]
    build_success: bool
    error_messages: list[str]
    duration_ms: float


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
    retrieval_results: list[dict] | None = None
    retrieval_query: str | None = None
    test_eval: TestEvalResult | None = None
    coverage_ratio: float | None = None
    line_coverage: list[dict] | None = None  # [{line: int, covered: bool, source: str}]
    test_file_paths: list[str] | None = None
    generated_invocations: list[dict] | None = None  # [{signature, resolutionMode, orderIndex}]
    direct_test_methods: list[dict] | None = None  # [{test_file, test_class, test_method}]

    def to_dict(self) -> dict:
        d = {
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
                "gen_token_count": self.metrics.gen_token_count,
                "ref_token_count": self.metrics.ref_token_count,
                "lcs_tokens": self.metrics.lcs_tokens,
                "lcsubstring_no_ident_length": self.metrics.lcsubstring_no_ident_length,
                "lcsubstring_no_ident_ratio": self.metrics.lcsubstring_no_ident_ratio,
                "lcsubstring_no_ident_tokens": self.metrics.lcsubstring_no_ident_tokens,
                "codebleu": self.metrics.codebleu,
                "recall_at_k": self.metrics.recall_at_k,
                "api_coverage_at_k": self.metrics.api_coverage_at_k,
                "mrr": self.metrics.mrr,
                "retrieval_precision_at_k": self.metrics.retrieval_precision_at_k,
                "retrieval_ndcg_at_k": self.metrics.retrieval_ndcg_at_k,
                "retrieval_type_iou": self.metrics.retrieval_type_iou,
                "owner_type_recall": self.metrics.owner_type_recall,
                "compilable": self.compilability.success if self.compilability else None,
                "compile_errors": self.compilability.error_messages if self.compilability else [],
                "compile_exit_code": self.compilability.exit_code if self.compilability else None,
            },
            "llm_response": self.llm_response,
        }
        if self.retrieval_results is not None:
            d["retrieval_results"] = self.retrieval_results
        if self.retrieval_query is not None:
            d["retrieval_query"] = self.retrieval_query
        if self.test_eval is not None:
            d["test_eval"] = {
                "success": self.test_eval.success,
                "tests_run": self.test_eval.tests_run,
                "tests_passed": self.test_eval.tests_passed,
                "tests_failed": self.test_eval.tests_failed,
                "failed_test_names": self.test_eval.failed_test_names,
                "build_success": self.test_eval.build_success,
                "error_messages": self.test_eval.error_messages,
                "duration_ms": self.test_eval.duration_ms,
            }
        if self.coverage_ratio is not None:
            d["coverage_ratio"] = self.coverage_ratio
        if self.line_coverage is not None:
            d["line_coverage"] = self.line_coverage
        if self.test_file_paths is not None:
            d["test_file_paths"] = self.test_file_paths
        if self.generated_invocations is not None:
            d["generated_invocations"] = self.generated_invocations
        if self.direct_test_methods is not None:
            d["direct_test_methods"] = self.direct_test_methods
        return d

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
            gen_token_count=metrics_d.get("gen_token_count"),
            ref_token_count=metrics_d.get("ref_token_count"),
            lcs_tokens=metrics_d.get("lcs_tokens"),
            lcsubstring_no_ident_length=metrics_d.get("lcsubstring_no_ident_length"),
            lcsubstring_no_ident_ratio=metrics_d.get("lcsubstring_no_ident_ratio"),
            lcsubstring_no_ident_tokens=metrics_d.get("lcsubstring_no_ident_tokens"),
            codebleu=metrics_d.get("codebleu"),
            recall_at_k=metrics_d.get("recall_at_k"),
            api_coverage_at_k=metrics_d.get("api_coverage_at_k"),
            mrr=metrics_d.get("mrr"),
            retrieval_precision_at_k=metrics_d.get("retrieval_precision_at_k"),
            retrieval_ndcg_at_k=metrics_d.get("retrieval_ndcg_at_k"),
            retrieval_type_iou=metrics_d.get("retrieval_type_iou"),
            owner_type_recall=metrics_d.get("owner_type_recall"),
        )
        comp = None
        if metrics_d.get("compilable") is not None:
            comp = CompilabilityResult(
                success=metrics_d["compilable"],
                error_messages=metrics_d.get("compile_errors", []),
                exit_code=metrics_d.get("compile_exit_code", 0),
            )
        test_eval = None
        if d.get("test_eval"):
            te = d["test_eval"]
            test_eval = TestEvalResult(
                success=te["success"],
                tests_run=te["tests_run"],
                tests_passed=te["tests_passed"],
                tests_failed=te["tests_failed"],
                failed_test_names=te.get("failed_test_names", []),
                build_success=te.get("build_success", True),
                error_messages=te.get("error_messages", []),
                duration_ms=te.get("duration_ms", 0.0),
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
            retrieval_results=d.get("retrieval_results"),
            retrieval_query=d.get("retrieval_query"),
            test_eval=test_eval,
            coverage_ratio=d.get("coverage_ratio"),
            line_coverage=d.get("line_coverage"),
            test_file_paths=d.get("test_file_paths"),
            generated_invocations=d.get("generated_invocations"),
            direct_test_methods=d.get("direct_test_methods"),
        )
