from __future__ import annotations

import random

from pipeline.models import ExtractedMethod, FIMPrompt, ResolvedInvocation

FIM_PREFIX = "<|fim_prefix|>"
FIM_SUFFIX = "<|fim_suffix|>"
FIM_MIDDLE = "<|fim_middle|>"


def build_augmentation_block(
    invocations: list[ResolvedInvocation],
    mode: str,
    shuffle_seed: int | None = None,
) -> tuple[str | None, list[ResolvedInvocation]]:
    if mode == "no_augmentation" or not invocations:
        return None, sorted(invocations, key=lambda inv: inv.order_index)

    ordered = sorted(invocations, key=lambda inv: inv.order_index)

    if mode == "shuffled_augmentation":
        ordered = list(ordered)
        if shuffle_seed is not None:
            random.Random(shuffle_seed).shuffle(ordered)
        else:
            random.shuffle(ordered)

    lines = ["/*", " * Method invocations used in this method:"]
    for i, inv in enumerate(ordered, 1):
        lines.append(f" * {i}. {inv.signature} [{inv.resolution_mode}]")
    lines.append(" */")

    return "\n".join(lines), ordered


def find_method_signature_position(file_content: str, body_start_offset: int) -> int:
    search_start = max(0, body_start_offset - 500)
    region = file_content[search_start:body_start_offset]

    last_newline = region.rfind("\n")
    if last_newline == -1:
        return search_start

    line_start = search_start + last_newline + 1
    preceding_line = file_content[line_start:body_start_offset].strip()

    if preceding_line.endswith("{") or preceding_line == "":
        search_region = file_content[search_start:line_start]
        prev_newline = search_region.rfind("\n")
        if prev_newline != -1:
            candidate = search_start + prev_newline + 1
            candidate_line = file_content[candidate:line_start].strip()
            if candidate_line:
                return _find_declaration_start(file_content, candidate)

    return _find_declaration_start(file_content, line_start)


def _find_declaration_start(file_content: str, approx_pos: int) -> int:
    pos = approx_pos
    while pos > 0:
        prev_newline = file_content.rfind("\n", 0, pos)
        if prev_newline == -1:
            return 0
        line = file_content[prev_newline + 1 : pos].strip()
        if line.startswith("@") or line.startswith("//") or line.startswith("*") or line.startswith("/*"):
            pos = prev_newline
        else:
            break
    return pos


def build_fim_prompt(
    method: ExtractedMethod,
    mode: str,
    shuffle_seed: int | None = None,
    retrieval_augmentation: str | None = None,
) -> FIMPrompt:
    file_content = method.file_content
    body_start = method.body_start_offset
    body_end = method.body_end_offset

    prefix = file_content[:body_start + 1]
    suffix = file_content[body_end:]
    ground_truth = file_content[body_start + 1 : body_end]

    if mode == "retrieval_augmentation" and retrieval_augmentation:
        aug_block = retrieval_augmentation
        invocations_as_used = sorted(method.invocations, key=lambda inv: inv.order_index)
    else:
        aug_block, invocations_as_used = build_augmentation_block(method.invocations, mode, shuffle_seed)

    if aug_block:
        insert_pos = find_method_signature_position(file_content, body_start)
        prefix = file_content[:insert_pos] + aug_block + "\n" + file_content[insert_pos:body_start + 1]

    full_prompt = f"{FIM_PREFIX}{prefix}{FIM_SUFFIX}{suffix}{FIM_MIDDLE}"

    return FIMPrompt(
        prefix=prefix,
        suffix=suffix,
        ground_truth=ground_truth,
        augmentation_block=aug_block,
        invocations_as_used=invocations_as_used,
        full_prompt=full_prompt,
    )
