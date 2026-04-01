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

    if retrieval.retriever_type == "external":
        log.info("External retriever manages its own index, skipping index build")
        return

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
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        log.error("Indexing stderr:\n%s", result.stderr)
        raise RuntimeError(f"Indexing failed with exit code {result.returncode}")
    log.info("Lucene index built at %s", index_dir)


def build_search_request(method: ExtractedMethod, config: Config) -> dict:
    retrieval = config.retrieval
    if retrieval and retrieval.retriever_type == "external":
        return _build_external_search_request(method, config)
    return _build_lucene_search_request(method, config)


def _build_external_search_request(method: ExtractedMethod, config: Config) -> dict:
    return {
        "sourceCode": method.file_content,
        "bodyStartOffset": method.body_start_offset,
        "bodyEndOffset": method.body_end_offset,
        "filePath": method.file_path,
        "userPrompt": "",
        "targetMethodBody": method.method_body,
    }


def _build_lucene_search_request(method: ExtractedMethod, config: Config) -> dict:
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
    classpath: list[str] | None = None,
    all_methods: list[ExtractedMethod] | None = None,
) -> list[RetrievalResponse]:
    retrieval = config.retrieval
    if retrieval is None:
        raise ValueError("Retrieval config is required for searching")

    if retrieval.retriever_type == "external":
        return _search_batch_external(requests, config, classpath or [], all_methods)
    return _search_batch_lucene(requests, config)


def _search_batch_lucene(
    requests: list[dict],
    config: Config,
) -> list[RetrievalResponse]:
    retrieval = config.retrieval

    jar_path = Path(retrieval.retriever_jar)
    if not jar_path.exists():
        raise FileNotFoundError(f"Retriever JAR not found at {jar_path}")

    index_dir = Path(retrieval.index_dir)
    if not index_dir.exists():
        raise FileNotFoundError(f"Lucene index not found at {index_dir}")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="batch_requests_", delete=False, encoding="utf-8",
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
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=600)
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


def _search_batch_external(
    requests: list[dict],
    config: Config,
    classpath: list[str],
    all_methods: list[ExtractedMethod] | None = None,
) -> list[RetrievalResponse]:
    retrieval = config.retrieval

    jar_path = Path(retrieval.retriever_jar)
    if not jar_path.exists():
        raise FileNotFoundError(f"Retriever JAR not found at {jar_path}")

    # Write project config for the external retriever
    project_config = {
        "projectName": config.project.name,
        "projectPath": config.project.path,
        "classpath": classpath,
        "sourceRoots": retrieval.project_source_roots,
    }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="project_config_", delete=False, encoding="utf-8",
    ) as pc_file:
        json.dump(project_config, pc_file, ensure_ascii=False)
        project_config_path = pc_file.name

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="batch_requests_", delete=False, encoding="utf-8",
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
            "unified-search",
            "--retriever-type", "external",
            "--retriever-class", retrieval.external_retriever_class,
            "--project-config", project_config_path,
            "--requests", req_path,
            "--output", res_path,
            "--top-k", str(retrieval.top_k),
        ]

        if retrieval.external_retriever_jars:
            cmd.extend(["--retriever-jars", ",".join(retrieval.external_retriever_jars)])

        log.info("Running external batch search: %d requests", len(requests))
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=600)
        if result.returncode != 0:
            log.error("External search stderr:\n%s", result.stderr)
            raise RuntimeError(f"External batch search failed with exit code {result.returncode}")

        with open(res_path, encoding="utf-8") as f:
            raw_responses = json.load(f)

        responses = [RetrievalResponse.from_dict(r) for r in raw_responses]
        log.info("External batch search complete: %d responses", len(responses))

        # Enrich minimal external results with data from extraction
        if all_methods:
            _enrich_external_responses(responses, requests, all_methods, config.project.path)
        else:
            log.warning(
                "No extraction data for external enrichment; "
                "augmentation and retrieval metrics will be degraded"
            )

        return responses
    finally:
        Path(project_config_path).unlink(missing_ok=True)
        Path(req_path).unlink(missing_ok=True)
        Path(res_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# External result enrichment
# ---------------------------------------------------------------------------


def _build_extraction_index(
    all_methods: list[ExtractedMethod],
    project_path: str | None = None,
) -> dict[str, list[ExtractedMethod]]:
    """Build a file_path → list[ExtractedMethod] index for enrichment lookup.

    Indexes under both the raw relative path and the absolute path (if
    project_path is provided), so that external retrievers returning either
    form can be matched.
    """
    index: dict[str, list[ExtractedMethod]] = {}
    for method in all_methods:
        raw = method.file_path
        index.setdefault(raw, []).append(method)
        if project_path:
            abs_path = str(Path(project_path) / raw)
            index.setdefault(abs_path, []).append(method)
    return index


def _find_matching_method(
    file_path: str,
    result_id: str,
    extraction_index: dict[str, list[ExtractedMethod]],
) -> ExtractedMethod:
    """Find the matching ExtractedMethod for an external retrieval result.

    Strategy:
    1. Exact path match → candidates from index
    2. Suffix match (handles absolute vs. relative path mismatch)
    3. Single candidate → return it
    4. Multiple candidates → match by result_id (must be ``classFqn#methodName``
       or full method signature)

    Raises ValueError on no match or ambiguous match — fail-fast so that
    misconfigured retrievers are caught immediately rather than producing
    silently incorrect experiment data.
    """
    # 1. Exact path match
    candidates = extraction_index.get(file_path)

    # 2. Suffix match
    if not candidates:
        for key, methods in extraction_index.items():
            if key.endswith(file_path) or file_path.endswith(key):
                candidates = methods
                break

    if not candidates:
        raise ValueError(
            f"Enrichment failed: no extracted methods found for file_path={file_path!r}. "
            f"The external retriever returned a location that does not exist in "
            f"extracted_methods.json. Check that the retriever and extraction "
            f"use the same project source tree."
        )

    # 3. Single candidate
    if len(candidates) == 1:
        return candidates[0]

    # 4. Disambiguate by result_id — must be unambiguous
    if result_id:
        matches = []
        for m in candidates:
            method_id = f"{m.class_fqn}#{m.method_name}"
            if result_id == method_id or result_id == m.method_signature:
                matches.append(m)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            sigs = [m.method_signature for m in matches]
            raise ValueError(
                f"Enrichment failed: result_id={result_id!r} matched {len(matches)} "
                f"methods in {file_path!r} (overloaded?): {sigs}. "
                f"The external retriever must return an unambiguous ID that "
                f"distinguishes overloaded methods (e.g. include parameter types)."
            )

    candidate_sigs = [f"  {m.class_fqn}#{m.method_name} — {m.method_signature}" for m in candidates]
    raise ValueError(
        f"Enrichment failed: {len(candidates)} methods in {file_path!r} and "
        f"result_id={result_id!r} does not uniquely identify one of them.\n"
        f"Candidates:\n" + "\n".join(candidate_sigs) + "\n"
        f"The external retriever must include a stable method identifier in "
        f"IRetrievalResult.getId() (e.g. 'classFqn#methodName' or full signature)."
    )


def _extract_short_name(signature: str) -> str:
    """Extract short name from invocation signature.

    'com.example.Foo::bar(int, String) -> void' => 'Foo::bar'
    """
    paren = signature.find("(")
    prefix = signature[:paren] if paren > 0 else signature
    double_colon = prefix.find("::")
    search_end = double_colon if double_colon > 0 else len(prefix)
    last_dot = prefix.rfind(".", 0, search_end)
    return prefix[last_dot + 1:] if last_dot > 0 else prefix


def _build_invocation_profile(method: ExtractedMethod) -> str:
    """Replicate InvocationProfileBuilder.build() in Python.

    Produces the exact same text format that _result_api_set() in metrics.py
    parses: individual signatures with optional frequency, bigram/trigram lines.
    """
    exact = [inv for inv in method.invocations if inv.resolution_mode == "EXACT"]
    exact.sort(key=lambda inv: inv.order_index)
    if not exact:
        return ""

    # Frequency map (preserving insertion order)
    freq: dict[str, int] = {}
    for inv in exact:
        freq[inv.signature] = freq.get(inv.signature, 0) + 1

    lines: list[str] = []
    for sig, count in freq.items():
        lines.append(f"{sig} x{count}" if count > 1 else sig)

    # Bigrams
    for i in range(len(exact) - 1):
        a = _extract_short_name(exact[i].signature)
        b = _extract_short_name(exact[i + 1].signature)
        lines.append(f"bigram: {a} -> {b}")

    # Trigrams
    for i in range(len(exact) - 2):
        a = _extract_short_name(exact[i].signature)
        b = _extract_short_name(exact[i + 1].signature)
        c = _extract_short_name(exact[i + 2].signature)
        lines.append(f"trigram: {a} -> {b} -> {c}")

    return "\n".join(lines) + "\n"


def _append_simple_type_names(parts: list[str], fqn: str | None) -> None:
    """Extract simple type names (≥3 chars, capitalized) from a possibly generic FQN."""
    import re
    if not fqn or not fqn.strip():
        return
    for part in re.split(r"[<>,\s]+", fqn):
        dot = part.rfind(".")
        simple = part[dot + 1:] if dot > 0 else part
        if len(simple) >= 3 and simple[0].isupper():
            parts.append(simple)


def _build_type_profile(method: ExtractedMethod) -> str:
    """Replicate TypeProfileBuilder.build() in Python.

    Produces space-separated simple type names from the same sources and in the
    same order as the Java builder, so that _extract_simple_types() in metrics.py
    can parse it.
    """
    parts: list[str] = []
    _append_simple_type_names(parts, method.return_type)
    for pt in method.parameter_types:
        _append_simple_type_names(parts, pt)
    for f in method.class_fields:
        _append_simple_type_names(parts, f.type_fqn)
    for ut in method.used_types:
        _append_simple_type_names(parts, ut)
    _append_simple_type_names(parts, method.class_fqn)
    for st in method.supertypes:
        _append_simple_type_names(parts, st)
    return " ".join(parts)


def _enrich_external_responses(
    responses: list[RetrievalResponse],
    requests: list[dict],
    all_methods: list[ExtractedMethod],
    project_path: str | None,
) -> None:
    """Enrich minimal external retrieval results with extraction data.

    Matches each result by file_path to the extraction index and fills in
    class_fqn, signature, method_body, invocation_profile, and type_profile.
    Uses ``or`` semantics: fields already set by the external retriever are
    not overwritten.

    Raises ValueError on any match failure — fail-fast to prevent silently
    incorrect experiment data from misconfigured retrievers.
    """
    extraction_index = _build_extraction_index(all_methods, project_path)
    enriched = 0

    for i, response in enumerate(responses):
        for result in response.results:
            # Raises ValueError on no match or ambiguous match
            match = _find_matching_method(
                result.file_path, result.method_id, extraction_index,
            )
            result.class_fqn = result.class_fqn or match.class_fqn
            result.signature = result.signature or match.method_signature
            result.method_body = result.method_body or match.method_body
            result.file_path = result.file_path or match.file_path
            result.invocation_profile = result.invocation_profile or _build_invocation_profile(match)
            result.type_profile = result.type_profile or _build_type_profile(match)
            enriched += 1

    log.info("Enrichment complete: %d results matched to extraction data", enriched)


# ---------------------------------------------------------------------------
# Augmentation formatting
# ---------------------------------------------------------------------------


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


FILE_SEP = "<|file_sep|>"


def format_retrieval_augmentation(
    response: RetrievalResponse,
    retrieval_config: RetrievalConfig,
) -> str:
    """Format retrieved methods as cross-file context using <|file_sep|>.

    Qwen2.5 Coder was trained with cross-file context in this format:
        <|file_sep|>path/to/File.java
        {file content}

    This goes BEFORE <|fim_prefix|> in the prompt, matching the training
    distribution for repo-level code completion.
    """
    if not response.results:
        return ""

    max_results = retrieval_config.max_results_in_prompt
    max_body = retrieval_config.max_body_lines
    include_body = retrieval_config.include_body
    min_score_ratio = retrieval_config.min_score_ratio

    # Score threshold: skip results with score < min_score_ratio * top-1 score
    top_score = response.results[0].score if response.results else 0
    score_threshold = top_score * min_score_ratio

    parts: list[str] = []

    included = 0
    for result in response.results:
        if included >= max_results:
            break
        if result.score < score_threshold:
            break

        # Convert class FQN to file path: com.example.Foo -> com/example/Foo.java
        file_path = result.file_path or (result.class_fqn.replace(".", "/") + ".java")

        # Build file fragment
        fragment_lines: list[str] = []

        # Package declaration
        pkg = result.class_fqn.rsplit(".", 1)[0] if "." in result.class_fqn else ""
        if pkg:
            fragment_lines.append(f"package {pkg};")
            fragment_lines.append("")

        # Class context line
        class_simple = result.class_fqn.rsplit(".", 1)[-1] if result.class_fqn else ""
        if class_simple:
            fragment_lines.append(f"// Class: {class_simple}")

        # Method signature + body
        sig = _simplify_signature(result.signature)

        if include_body:
            body_lines = _extract_body_lines(result.method_body, max_body)
        else:
            body_lines = None

        if body_lines:
            fragment_lines.append(f"{sig} {{")
            for bl in body_lines:
                fragment_lines.append(f"    {bl}" if bl.strip() else "")
            fragment_lines.append("}")
        else:
            fragment_lines.append(f"{sig} {{ /* ... */ }}")

        parts.append(f"{FILE_SEP}{file_path}")
        parts.append("\n".join(fragment_lines))

        included += 1

    if not parts:
        return ""

    block = "\n".join(parts)

    # Enforce token limit — truncate to last complete file fragment
    max_chars = retrieval_config.max_augmentation_tokens * 4
    if len(block) > max_chars:
        cut = block[:max_chars].rfind(f"\n{FILE_SEP}")
        if cut > 0:
            block = block[:cut]
        else:
            block = block[:max_chars]

    return block
