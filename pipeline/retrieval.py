from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from pipeline.config import Config, RetrievalConfig
from pipeline.models import ExtractedMethod, RetrievalResponse, RetrievalResult

log = logging.getLogger(__name__)


def _java_cmd() -> str:
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        java_bin = Path(java_home) / "bin" / "java"
        if java_bin.exists():
            return str(java_bin)
    return "java"


def build_index(config: Config) -> None:
    retrieval = config.retrieval
    if retrieval is None:
        raise ValueError("Retrieval config is required for indexing")

    index_dir = Path(retrieval.index_dir)
    if index_dir.exists() and any(index_dir.iterdir()):
        log.info("Lucene index already exists at %s, skipping", index_dir)
        return

    jar_path = Path(retrieval.retriever_jar)
    if not jar_path.exists():
        raise FileNotFoundError(
            f"Retriever JAR not found at {jar_path}. "
            "Build it first: cd extractor && ./gradlew :retriever:jar"
        )

    extraction_path = Path(config.extraction.output)
    if not extraction_path.exists():
        raise FileNotFoundError(f"Extraction output not found at {extraction_path}")

    cmd = [
        _java_cmd(), "-jar", str(jar_path),
        "index",
        "--input", str(extraction_path),
        "--index-dir", str(index_dir),
    ]

    log.info("Building Lucene index: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("Indexing stderr:\n%s", result.stderr)
        raise RuntimeError(f"Indexing failed with exit code {result.returncode}")
    log.info("Lucene index built at %s", index_dir)


def build_search_request(method: ExtractedMethod, config: Config) -> dict:
    retrieval = config.retrieval
    top_k = retrieval.top_k if retrieval else 5
    threshold = retrieval.near_duplicate_threshold if retrieval else 0.8

    # Build FIM prefix/suffix (same as prompt.py logic)
    file_content = method.file_content
    body_start = method.body_start_offset
    body_end = method.body_end_offset
    prefix = file_content[:body_start + 1]
    suffix = file_content[body_end:]

    # Collect sibling info
    sibling_sigs = []
    sibling_owner_types: set[str] = set()
    sibling_used_types = []
    for sib in method.sibling_methods:
        sibling_sigs.append(sib.signature)
        for inv in sib.invocations:
            if inv.resolution_mode == "EXACT":
                # Extract owner type simple name: "com.foo.Bar::method(...)" -> "Bar"
                parts = inv.signature.split("::", 1)
                if parts:
                    fqn = parts[0]
                    simple = fqn.rsplit(".", 1)[-1]
                    if len(simple) >= 3:
                        sibling_owner_types.add(simple)
        sibling_used_types.extend(sib.used_types)

    sibling_used_types = list(dict.fromkeys(sibling_used_types))

    return {
        "request": {
            "methodSignature": method.method_signature,
            "classFqn": method.class_fqn,
            "supertypes": method.supertypes,
            "imports": method.imports,
            "classFields": [f.type_fqn for f in method.class_fields],
            "siblingSignatures": sibling_sigs,
            "siblingOwnerTypes": sorted(sibling_owner_types),
            "siblingUsedTypes": sibling_used_types,
            "fimPrefix": prefix[-500:] if len(prefix) > 500 else prefix,
            "fimSuffix": suffix[:500] if len(suffix) > 500 else suffix,
            "targetFilePath": method.file_path,
            "targetBodyStartOffset": method.body_start_offset,
            "topK": top_k,
            "nearDuplicateThreshold": threshold,
        },
        "targetMethodBody": method.method_body,
    }


def search_batch(
    requests: list[dict],
    config: Config,
) -> list[RetrievalResponse]:
    retrieval = config.retrieval
    if retrieval is None:
        raise ValueError("Retrieval config is required for searching")

    jar_path = Path(retrieval.retriever_jar)
    if not jar_path.exists():
        raise FileNotFoundError(f"Retriever JAR not found at {jar_path}")

    index_dir = Path(retrieval.index_dir)
    if not index_dir.exists():
        raise FileNotFoundError(f"Lucene index not found at {index_dir}")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="batch_requests_", delete=False,
    ) as req_file:
        json.dump(requests, req_file, ensure_ascii=False)
        req_path = req_file.name

    with tempfile.NamedTemporaryFile(
        suffix=".json", prefix="batch_results_", delete=False,
    ) as res_file:
        res_path = res_file.name

    try:
        cmd = [
            _java_cmd(), "-jar", str(jar_path),
            "search-batch",
            "--index-dir", str(index_dir),
            "--requests", req_path,
            "--output", res_path,
            "--top-k", str(retrieval.top_k),
        ]

        log.info("Running batch search: %d requests", len(requests))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            log.error("Search stderr:\n%s", result.stderr)
            raise RuntimeError(f"Batch search failed with exit code {result.returncode}")

        with open(res_path, encoding="utf-8") as f:
            raw_responses = json.load(f)

        responses = [RetrievalResponse.from_dict(r) for r in raw_responses]
        log.info("Batch search complete: %d responses", len(responses))
        return responses
    finally:
        Path(req_path).unlink(missing_ok=True)
        Path(res_path).unlink(missing_ok=True)


def _simplify_signature(signature: str) -> str:
    """Strip access modifiers and simplify FQN types to simple names.

    'public static boolean validate(com.example.Whitelist whitelist, IPlayer p)'
    -> 'static boolean validate(Whitelist whitelist, IPlayer p)'
    """
    import re

    # Remove access modifiers
    sig = re.sub(r'\b(public|private|protected)\s+', '', signature)

    # Replace FQN types with simple names: 'com.foo.Bar' -> 'Bar'
    # But keep generics intact: 'List<com.foo.Bar>' -> 'List<Bar>'
    sig = re.sub(r'(?<![.\w])([a-z][a-z0-9]*\.)+([A-Z]\w*)', r'\2', sig)

    return sig.strip()


def _extract_body_lines(method_body: str, max_lines: int) -> list[str] | None:
    """Extract inner body lines from a method body block.

    Strips the outer '{' '}' braces and leading/trailing blank lines.
    Returns None if body is empty or whitespace-only.
    """
    if not method_body or not method_body.strip():
        return None

    body = method_body.strip()

    # Strip outer braces
    if body.startswith("{"):
        body = body[1:]
    if body.endswith("}"):
        body = body[:-1]

    lines = body.split("\n")

    # Strip leading/trailing blank lines
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    if not lines:
        return None

    # Dedent: find minimum indentation
    indents = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
    min_indent = min(indents) if indents else 0
    lines = [l[min_indent:] if len(l) >= min_indent else l for l in lines]

    truncated = len(lines) > max_lines
    lines = lines[:max_lines]
    if truncated:
        lines.append("    // ...")

    return lines


def format_retrieval_augmentation(
    response: RetrievalResponse,
    retrieval_config: RetrievalConfig,
) -> str:
    if not response.results:
        return ""

    max_results = retrieval_config.max_results_in_prompt
    max_body = retrieval_config.max_body_lines
    include_body = retrieval_config.include_body

    parts: list[str] = ["// --- Related methods from project ---"]

    for result in response.results[:max_results]:
        # Source class as a short comment
        class_simple = result.class_fqn.rsplit(".", 1)[-1] if result.class_fqn else ""
        parts.append(f"// From: {result.class_fqn}")

        sig = _simplify_signature(result.signature)

        if include_body:
            body_lines = _extract_body_lines(result.method_body, max_body)
        else:
            body_lines = None

        if body_lines:
            parts.append(f"{sig} {{")
            for bl in body_lines:
                parts.append(f"    {bl}" if bl.strip() else "")
            parts.append("}")
        else:
            # Signature-only stub
            parts.append(f"{sig} {{ /* ... */ }}")

        parts.append("")  # blank line between methods

    parts.append("// --- End related methods ---")

    block = "\n".join(parts)

    # Enforce token limit
    max_chars = retrieval_config.max_augmentation_tokens * 4
    if len(block) > max_chars:
        # Truncate to last complete method before the limit
        cut = block[:max_chars].rfind("\n// From:")
        if cut > 0:
            block = block[:cut] + "\n// --- End related methods ---"
        else:
            block = block[:max_chars] + "\n// --- End related methods ---"

    return block
