from __future__ import annotations

import math
import re
from collections import Counter

import Levenshtein

from pipeline.codebleu_metric import compute_codebleu
from pipeline.models import MetricsResult, ResolvedInvocation, RetrievalResult
from pipeline.normalize import normalize_code, tokenize_code
from pipeline.strip_identifiers import strip_identifiers_and_literals


def exact_match(generated: str, reference: str) -> bool:
    return generated == reference


def edit_similarity(generated: str, reference: str) -> float:
    if not generated and not reference:
        return 1.0
    max_len = max(len(generated), len(reference))
    if max_len == 0:
        return 1.0
    dist = Levenshtein.distance(generated, reference)
    return 1.0 - dist / max_len


def token_iou(generated: str, reference: str) -> float:
    gen_tokens = Counter(tokenize_code(generated))
    ref_tokens = Counter(tokenize_code(reference))

    if not gen_tokens and not ref_tokens:
        return 1.0
    if not gen_tokens or not ref_tokens:
        return 0.0

    intersection = sum((gen_tokens & ref_tokens).values())
    union = sum((gen_tokens | ref_tokens).values())

    return intersection / union if union > 0 else 0.0


def longest_common_subsequence(generated: str, reference: str) -> int:
    gen_tokens = tokenize_code(generated)
    ref_tokens = tokenize_code(reference)

    m, n = len(gen_tokens), len(ref_tokens)
    if m == 0 or n == 0:
        return 0

    prev = [0] * (n + 1)
    curr = [0] * (n + 1)

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if gen_tokens[i - 1] == ref_tokens[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, [0] * (n + 1)

    return prev[n]


def lcs_ratio(generated: str, reference: str) -> float:
    gen_tokens = tokenize_code(generated)
    ref_tokens = tokenize_code(reference)
    max_len = max(len(gen_tokens), len(ref_tokens))
    if max_len == 0:
        return 1.0
    lcs_len = longest_common_subsequence(generated, reference)
    return lcs_len / max_len


def compute_all_metrics(
    generated: str,
    reference: str,
    identifier_unify: bool = False,
    include_codebleu: bool = True,
) -> MetricsResult:
    norm_gen = normalize_code(generated, identifier_unify=identifier_unify)
    norm_ref = normalize_code(reference, identifier_unify=identifier_unify)

    lcs_len = longest_common_subsequence(norm_gen, norm_ref)
    gen_tokens = tokenize_code(norm_gen)
    ref_tokens = tokenize_code(norm_ref)
    max_tok_len = max(len(gen_tokens), len(ref_tokens))

    # LCS on identifier/literal-stripped code.
    stripped_gen = strip_identifiers_and_literals(generated)
    stripped_ref = strip_identifiers_and_literals(reference)
    lcs_no_ident_len = longest_common_subsequence(stripped_gen, stripped_ref)
    stripped_gen_tokens = tokenize_code(stripped_gen)
    stripped_ref_tokens = tokenize_code(stripped_ref)
    max_stripped_len = max(len(stripped_gen_tokens), len(stripped_ref_tokens))

    # CodeBLEU.
    codebleu_score = compute_codebleu(generated, reference) if include_codebleu else None

    return MetricsResult(
        em=exact_match(norm_gen, norm_ref),
        es=edit_similarity(norm_gen, norm_ref),
        iou=token_iou(norm_gen, norm_ref),
        lcs_length=lcs_len,
        lcs_ratio=lcs_len / max_tok_len if max_tok_len > 0 else 1.0,
        lcs_no_ident_length=lcs_no_ident_len,
        lcs_no_ident_ratio=lcs_no_ident_len / max_stripped_len if max_stripped_len > 0 else 1.0,
        codebleu=codebleu_score,
    )


def _parse_invocation_key(signature: str) -> tuple[str, str]:
    """Extract (ownerType, methodName) from 'com.example.Foo::bar(int) -> void'."""
    parts = signature.split("::", 1)
    if len(parts) != 2:
        return (signature, "")
    owner = parts[0]
    rest = parts[1]
    paren_idx = rest.find("(")
    method_name = rest[:paren_idx] if paren_idx > 0 else rest
    return (owner, method_name)


def invocation_recall_at_k(
    retrieved_text: str,
    oracle_invocations: list[ResolvedInvocation],
) -> float:
    """Fraction of oracle invocation signatures whose key parts appear in retrieved text."""
    if not oracle_invocations:
        return 1.0
    if not retrieved_text:
        return 0.0

    text_lower = retrieved_text.lower()
    found = 0
    for inv in oracle_invocations:
        if inv.resolution_mode != "EXACT":
            continue
        owner, method_name = _parse_invocation_key(inv.signature)
        # Check if both owner simple name and method name appear
        owner_simple = owner.rsplit(".", 1)[-1].lower()
        if owner_simple in text_lower and method_name.lower() in text_lower:
            found += 1

    exact_count = sum(1 for inv in oracle_invocations if inv.resolution_mode == "EXACT")
    return found / exact_count if exact_count > 0 else 1.0


def api_coverage_at_k(
    retrieval_results: list[RetrievalResult],
    oracle_invocations: list[ResolvedInvocation],
) -> float:
    """Fraction of needed (ownerType, methodName) pairs recovered across all retrieved methods."""
    if not oracle_invocations:
        return 1.0

    needed = set()
    for inv in oracle_invocations:
        if inv.resolution_mode == "EXACT":
            needed.add(_parse_invocation_key(inv.signature))

    if not needed:
        return 1.0

    # Collect all invocation keys from retrieved method cards and invocation profiles
    found = set()
    for result in retrieval_results:
        combined = (result.method_card or "") + " " + (result.invocation_profile or "")
        combined_lower = combined.lower()
        for owner, method_name in needed:
            owner_simple = owner.rsplit(".", 1)[-1].lower()
            if owner_simple in combined_lower and method_name.lower() in combined_lower:
                found.add((owner, method_name))

    return len(found) / len(needed)


def mrr_for_similar_method(
    retrieval_results: list[RetrievalResult],
    target_body: str,
    similarity_threshold: float = 0.3,
) -> float:
    """MRR based on token overlap of retrieved method bodies with the target ground truth."""
    if not retrieval_results or not target_body:
        return 0.0

    target_tokens = set(tokenize_code(target_body))
    if not target_tokens:
        return 0.0

    for result in retrieval_results:
        if not result.method_body:
            continue
        result_tokens = set(tokenize_code(result.method_body))
        if not result_tokens:
            continue

        intersection = target_tokens & result_tokens
        union = target_tokens | result_tokens
        jaccard = len(intersection) / len(union) if union else 0.0

        if jaccard >= similarity_threshold:
            return 1.0 / result.rank

    return 0.0


def _oracle_api_set(oracle_invocations: list[ResolvedInvocation]) -> set[tuple[str, str]]:
    """Unique (owner_fqn, method_name) pairs from oracle invocations."""
    apis = set()
    for inv in oracle_invocations:
        if inv.resolution_mode != "EXACT":
            continue
        owner, method_name = _parse_invocation_key(inv.signature)
        apis.add((owner.lower(), method_name.lower()))
    return apis


def _result_api_set(result: RetrievalResult) -> set[tuple[str, str]]:
    """Unique (owner_fqn, method_name) pairs from a retrieved method's invocation profile."""
    apis = set()
    profile = result.invocation_profile or ""
    for line in profile.split("\n"):
        line = line.strip()
        if not line or line.startswith("bigram:") or line.startswith("trigram:"):
            continue
        # "com.foo.Bar::baz(int) -> void" or "com.foo.Bar::baz(int) -> void x3"
        parts = line.split("::", 1)
        if len(parts) < 2:
            continue
        owner_fqn = parts[0].lower()
        rest = parts[1]
        paren_idx = rest.find("(")
        method_name = rest[:paren_idx].lower() if paren_idx > 0 else rest.split()[0].lower()
        apis.add((owner_fqn, method_name))
    return apis


def _extract_simple_types(text: str) -> set[str]:
    """Extract simple type names (capitalized, ≥3 chars) from any text."""
    types = set()
    for token in re.split(r"[^a-zA-Z0-9]+", text or ""):
        if len(token) >= 3 and token[0].isupper():
            types.add(token.lower())
    return types


def retrieval_precision_at_k(
    retrieval_results: list[RetrievalResult],
    oracle_invocations: list[ResolvedInvocation],
) -> float:
    """Fraction of retrieved methods that contain at least 1 needed oracle API."""
    if not retrieval_results:
        return 0.0
    needed = _oracle_api_set(oracle_invocations)
    if not needed:
        return 1.0

    relevant_count = 0
    for result in retrieval_results:
        result_apis = _result_api_set(result)
        if needed & result_apis:
            relevant_count += 1
    return relevant_count / len(retrieval_results)


def retrieval_ndcg_at_k(
    retrieval_results: list[RetrievalResult],
    oracle_invocations: list[ResolvedInvocation],
) -> float:
    """NDCG where relevance = number of oracle APIs found in each retrieved method."""
    if not retrieval_results:
        return 0.0
    needed = _oracle_api_set(oracle_invocations)
    if not needed:
        return 1.0

    # Compute gains
    gains = []
    for result in retrieval_results:
        result_apis = _result_api_set(result)
        overlap = len(needed & result_apis)
        gains.append(overlap)

    # DCG
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))

    # Ideal DCG: all needed APIs found in first slot
    ideal_gains = sorted(gains, reverse=True)
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal_gains))

    # If ideal is also 0, nothing was findable
    if idcg == 0:
        return 0.0
    return dcg / idcg


def retrieval_type_iou(
    retrieval_results: list[RetrievalResult],
    method_imports: list[str],
    method_class_fields: list,
    method_parameter_types: list[str],
    method_return_type: str | None,
) -> float:
    """Average Type IoU between query types and each retrieved method's type profile."""
    # Build query types from the same sources as the query
    query_types: set[str] = set()
    for imp in (method_imports or []):
        s = imp.rsplit(".", 1)[-1].lower()
        if len(s) >= 3:
            query_types.add(s)
    for f in (method_class_fields or []):
        fqn = f.type_fqn if hasattr(f, "type_fqn") else (f.get("typeFqn", "") if isinstance(f, dict) else "")
        s = fqn.rsplit(".", 1)[-1].lower()
        if len(s) >= 3:
            query_types.add(s)
    for pt in (method_parameter_types or []):
        for tok in re.split(r"[<>,\s]+", pt):
            simple = tok.rsplit(".", 1)[-1]
            if len(simple) >= 3 and simple[0].isupper():
                query_types.add(simple.lower())
    if method_return_type:
        for tok in re.split(r"[<>,\s]+", method_return_type):
            simple = tok.rsplit(".", 1)[-1]
            if len(simple) >= 3 and simple[0].isupper():
                query_types.add(simple.lower())

    stop = {"string", "object", "list", "map", "set", "optional", "integer",
            "boolean", "void", "exception", "override"}
    query_types -= stop

    if not retrieval_results or not query_types:
        return 0.0

    ious = []
    for result in retrieval_results:
        result_types = _extract_simple_types(result.type_profile)
        result_types -= stop
        inter = query_types & result_types
        union = query_types | result_types
        iou = len(inter) / len(union) if union else 0.0
        ious.append(iou)

    return sum(ious) / len(ious) if ious else 0.0


def owner_type_recall(
    retrieval_results: list[RetrievalResult],
    oracle_invocations: list[ResolvedInvocation],
) -> float:
    """Fraction of unique oracle owner types covered by any retrieved method."""
    needed_owners = set()
    for inv in oracle_invocations:
        if inv.resolution_mode != "EXACT":
            continue
        owner, _ = _parse_invocation_key(inv.signature)
        needed_owners.add(owner.lower())
    if not needed_owners:
        return 1.0

    found_owners = set()
    for result in retrieval_results:
        result_apis = _result_api_set(result)
        for owner_fqn, _ in result_apis:
            if owner_fqn in needed_owners:
                found_owners.add(owner_fqn)

    return len(found_owners) / len(needed_owners)
