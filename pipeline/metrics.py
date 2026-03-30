from __future__ import annotations

from collections import Counter

import Levenshtein

from pipeline.models import MetricsResult
from pipeline.normalize import normalize_code, tokenize_code


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
) -> MetricsResult:
    norm_gen = normalize_code(generated, identifier_unify=identifier_unify)
    norm_ref = normalize_code(reference, identifier_unify=identifier_unify)

    lcs_len = longest_common_subsequence(norm_gen, norm_ref)
    gen_tokens = tokenize_code(norm_gen)
    ref_tokens = tokenize_code(norm_ref)
    max_tok_len = max(len(gen_tokens), len(ref_tokens))

    return MetricsResult(
        em=exact_match(norm_gen, norm_ref),
        es=edit_similarity(norm_gen, norm_ref),
        iou=token_iou(norm_gen, norm_ref),
        lcs_length=lcs_len,
        lcs_ratio=lcs_len / max_tok_len if max_tok_len > 0 else 1.0,
    )
