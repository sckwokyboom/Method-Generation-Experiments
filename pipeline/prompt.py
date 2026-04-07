from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass

from pipeline.models import ExtractedMethod, FIMPrompt, ResolvedInvocation
from pipeline.tokenizer import count_tokens

log = logging.getLogger(__name__)

FIM_PREFIX = "<|fim_prefix|>"
FIM_SUFFIX = "<|fim_suffix|>"
FIM_MIDDLE = "<|fim_middle|>"

RAG_MODE_PREFIX = "rag_"

# FIM special tokens contribute a small fixed overhead.
_FIM_OVERHEAD_TOKENS = 10


def is_retrieval_mode(mode: str) -> bool:
    return mode.startswith(RAG_MODE_PREFIX) or mode == "retrieval_augmentation"


def retriever_name_from_mode(mode: str) -> str:
    if mode.startswith(RAG_MODE_PREFIX):
        return mode[len(RAG_MODE_PREFIX):]
    raise ValueError(f"Cannot extract retriever name from non-rag mode: {mode}")


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


# ═══════════════════════════════════════════════════════════════════════════
# Semantic-aware prompt trimming
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class _FileSections:
    """Parsed structural sections of a Java source file (up to the target method)."""
    package_decl: str       # "package com.example;\n" or ""
    import_block: str       # all import lines joined
    class_header: str       # annotations + class declaration + "{\n"
    class_body_before: str  # fields, constructors, sibling methods before target
    method_header: str      # annotations + method signature + "{"


_PACKAGE_RE = re.compile(r"^\s*package\s+[\w.]+\s*;\s*\n?", re.MULTILINE)
_IMPORT_LINE_RE = re.compile(r"^\s*import\s+", re.MULTILINE)
_CLASS_KW_RE = re.compile(
    r"^[ \t]*(?:(?:public|protected|private|abstract|final|static|strictfp)\s+)*"
    r"(?:class|interface|enum|@interface)\s+",
    re.MULTILINE,
)


def _extract_file_sections(
    file_content: str,
    body_start_offset: int,
    method: ExtractedMethod,
) -> _FileSections:
    """Parse file_content into structural sections up to the target method."""

    # ── package ──
    m_pkg = _PACKAGE_RE.search(file_content, 0, min(500, len(file_content)))
    if m_pkg:
        package_decl = file_content[m_pkg.start():m_pkg.end()]
        after_package = m_pkg.end()
    else:
        package_decl = ""
        after_package = 0

    # ── imports ──
    # Find the contiguous block of import statements (may have blank lines between groups)
    import_start = None
    import_end = after_package
    for m_imp in _IMPORT_LINE_RE.finditer(file_content, after_package, body_start_offset):
        if import_start is None:
            import_start = m_imp.start()
        # extend import_end to end of this import line
        line_end = file_content.find("\n", m_imp.end())
        import_end = (line_end + 1) if line_end != -1 else len(file_content)

    if import_start is not None:
        import_block = file_content[import_start:import_end]
        after_imports = import_end
    else:
        import_block = ""
        after_imports = after_package

    # ── class header ──
    # Find the class/interface/enum declaration that encloses our method
    class_header = ""
    class_body_start = after_imports

    # Derive simple class name (handle inner classes: "Outer.Inner" or "Outer$Inner")
    simple_name = method.class_fqn.rsplit(".", 1)[-1].split("$")[-1]

    # Search for class keyword matching our class name
    for m_cls in _CLASS_KW_RE.finditer(file_content, after_imports, body_start_offset):
        # Check if this declaration matches our class name
        rest = file_content[m_cls.end():min(m_cls.end() + 200, body_start_offset)]
        if rest.lstrip().startswith(simple_name):
            # Walk backward to include annotations and javadoc
            decl_start = _find_declaration_start(file_content, m_cls.start())
            # Find the opening brace
            brace_pos = file_content.find("{", m_cls.end())
            if brace_pos != -1 and brace_pos < body_start_offset:
                class_header = file_content[decl_start:brace_pos + 1] + "\n"
                class_body_start = brace_pos + 1
                break

    if not class_header:
        # Fallback: find any class declaration before the method
        for m_cls in _CLASS_KW_RE.finditer(file_content, after_imports, body_start_offset):
            decl_start = _find_declaration_start(file_content, m_cls.start())
            brace_pos = file_content.find("{", m_cls.end())
            if brace_pos != -1 and brace_pos < body_start_offset:
                class_header = file_content[decl_start:brace_pos + 1] + "\n"
                class_body_start = brace_pos + 1
                break

    # ── method header ──
    method_sig_start = find_method_signature_position(file_content, body_start_offset)
    method_header = file_content[method_sig_start:body_start_offset + 1]

    # ── class body before method ──
    class_body_before = file_content[class_body_start:method_sig_start]

    return _FileSections(
        package_decl=package_decl,
        import_block=import_block,
        class_header=class_header,
        class_body_before=class_body_before,
        method_header=method_header,
    )


def _trim_imports(
    import_block: str,
    method: ExtractedMethod,
    budget_tokens: int,
) -> str:
    """Trim imports to fit budget, keeping the most relevant ones."""
    if count_tokens(import_block) <= budget_tokens:
        return import_block

    # Build relevance set from used_types and invocation owner types
    relevant_names: set[str] = set()
    for t in (method.used_types or []):
        relevant_names.add(t.rsplit(".", 1)[-1])  # simple name
        relevant_names.add(t)  # full FQN
    for inv in (method.invocations or []):
        # "com.example.Foo::bar(...)" -> "Foo"
        sig = inv.signature
        owner = sig.split("::")[0] if "::" in sig else ""
        if owner:
            relevant_names.add(owner.rsplit(".", 1)[-1])
            relevant_names.add(owner)

    # Score each import line
    scored: list[tuple[int, str]] = []
    for line in import_block.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped.startswith("import"):
            continue
        # Extract imported name: "import com.example.Foo;" -> "com.example.Foo"
        imported = stripped.removeprefix("import").removesuffix(";").strip()
        imported = imported.removeprefix("static").strip()
        simple = imported.rsplit(".", 1)[-1]

        score = 0
        if simple == "*":
            score = 1  # wildcard — moderate value
        elif simple in relevant_names or imported in relevant_names:
            score = 2  # directly referenced
        scored.append((score, line))

    # Sort by score descending, include greedily
    scored.sort(key=lambda x: -x[0])
    result_lines: list[str] = []
    tokens_used = 0
    for score, line in scored:
        line_tokens = count_tokens(line)
        if tokens_used + line_tokens > budget_tokens:
            break
        result_lines.append(line)
        tokens_used += line_tokens

    # Restore original file order
    original_order = {line: i for i, line in enumerate(import_block.splitlines(keepends=True))}
    result_lines.sort(key=lambda l: original_order.get(l, 0))
    return "".join(result_lines)


def _split_class_members(class_body: str) -> list[str]:
    """Split class body into individual member chunks by tracking brace depth.

    Each chunk is a top-level member (field, method, constructor, inner class, etc.)
    at brace depth 0 within the class body.
    """
    if not class_body.strip():
        return []

    members: list[str] = []
    current_start = 0
    depth = 0
    i = 0
    in_string = False
    in_char = False
    in_line_comment = False
    in_block_comment = False

    while i < len(class_body):
        c = class_body[i]

        # Handle comments
        if in_line_comment:
            if c == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if c == "*" and i + 1 < len(class_body) and class_body[i + 1] == "/":
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue

        # Handle strings and chars
        if in_string:
            if c == "\\" and i + 1 < len(class_body):
                i += 2  # skip escaped char
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if in_char:
            if c == "\\" and i + 1 < len(class_body):
                i += 2
                continue
            if c == "'":
                in_char = False
            i += 1
            continue

        # Detect comment/string starts
        if c == "/" and i + 1 < len(class_body):
            next_c = class_body[i + 1]
            if next_c == "/":
                in_line_comment = True
                i += 2
                continue
            elif next_c == "*":
                in_block_comment = True
                i += 2
                continue
        if c == '"':
            in_string = True
            i += 1
            continue
        if c == "'":
            in_char = True
            i += 1
            continue

        # Track braces
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                # End of a top-level member (method/inner class)
                # Find the end of this line
                line_end = class_body.find("\n", i)
                end = (line_end + 1) if line_end != -1 else len(class_body)
                chunk = class_body[current_start:end]
                if chunk.strip():
                    members.append(chunk)
                current_start = end
        elif c == ";" and depth == 0:
            # End of a field/import/etc at top level
            line_end = class_body.find("\n", i)
            end = (line_end + 1) if line_end != -1 else len(class_body)
            chunk = class_body[current_start:end]
            if chunk.strip():
                members.append(chunk)
            current_start = end

        i += 1

    # Remaining text (trailing whitespace/comments)
    if current_start < len(class_body):
        remainder = class_body[current_start:]
        if remainder.strip():
            members.append(remainder)

    return members


def _score_member(
    member: str,
    method: ExtractedMethod,
    class_simple_name: str,
) -> int:
    """Score a class member by relevance to the target method."""
    stripped = member.strip()

    # Skip blank/comment-only chunks
    if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
        return -1  # will be dropped

    invocation_sigs = {inv.signature for inv in (method.invocations or [])}
    used = set(method.used_types or [])
    used_simple = {t.rsplit(".", 1)[-1] for t in used}

    # Check if this is a constructor (no return type, name matches class)
    is_constructor = bool(re.search(
        rf"\b{re.escape(class_simple_name)}\s*\(", stripped
    )) and "class " not in stripped

    # Check if it contains a field declaration with a relevant type
    is_field = ";" in stripped and "{" not in stripped  # heuristic: no braces → field
    field_type_relevant = False
    if is_field:
        for t in used_simple:
            if t in stripped:
                field_type_relevant = True
                break

    # Check if it's a sibling method invoked by target
    is_invoked_sibling = False
    for sig in invocation_sigs:
        # Extract method name from "com.example.Foo::bar(...)"
        if "::" in sig:
            method_name = sig.split("::")[1].split("(")[0]
            if method_name in stripped:
                is_invoked_sibling = True
                break

    if is_invoked_sibling:
        return 3
    if field_type_relevant:
        return 3
    if is_constructor:
        return 2
    if is_field:
        return 1
    return 0


def _prioritized_class_body(
    class_body: str,
    method: ExtractedMethod,
    budget_tokens: int,
) -> str:
    """Select class body members by relevance, fitting within budget."""
    if not class_body.strip() or budget_tokens <= 0:
        return ""

    total_tokens = count_tokens(class_body)
    if total_tokens <= budget_tokens:
        return class_body

    members = _split_class_members(class_body)
    if not members:
        return ""

    class_simple_name = method.class_fqn.rsplit(".", 1)[-1].split("$")[-1]

    # Score each member
    scored: list[tuple[int, int, str]] = []  # (score, original_index, text)
    for idx, member in enumerate(members):
        score = _score_member(member, method, class_simple_name)
        if score >= 0:
            scored.append((score, idx, member))

    # Sort by score desc, then by proximity to target method (later = closer)
    scored.sort(key=lambda x: (-x[0], -x[1]))

    # Greedy inclusion
    selected_indices: set[int] = set()
    tokens_used = 0
    for score, idx, member_text in scored:
        member_tokens = count_tokens(member_text)
        if tokens_used + member_tokens > budget_tokens:
            continue  # try smaller members
        selected_indices.add(idx)
        tokens_used += member_tokens

    # Reconstruct in original order
    result = "".join(members[i] for i in sorted(selected_indices))
    return result


def _build_structured_prefix(
    method: ExtractedMethod,
    aug_block: str | None,
    budget_tokens: int,
) -> str:
    """Build a trimmed prefix preserving semantic structure."""
    file_content = method.file_content
    body_start = method.body_start_offset

    sections = _extract_file_sections(file_content, body_start, method)

    # P0: method_header is MUST
    method_header_tokens = count_tokens(sections.method_header)
    if method_header_tokens >= budget_tokens:
        # Extreme case: even method header doesn't fit
        return sections.method_header

    # P1 + P2: package + class_header
    pkg_tokens = count_tokens(sections.package_decl) if sections.package_decl else 0
    cls_tokens = count_tokens(sections.class_header) if sections.class_header else 0

    mandatory_tokens = method_header_tokens + pkg_tokens + cls_tokens
    remaining = budget_tokens - mandatory_tokens

    # P3: imports
    imports_text = ""
    if remaining > 0 and sections.import_block:
        import_tokens = count_tokens(sections.import_block)
        if import_tokens <= remaining:
            imports_text = sections.import_block
            remaining -= import_tokens
        else:
            # Trim imports to fit
            imports_text = _trim_imports(sections.import_block, method, remaining)
            remaining -= count_tokens(imports_text)

    # P4: augmentation block
    aug_text = ""
    if remaining > 0 and aug_block:
        aug_tokens = count_tokens(aug_block + "\n")
        if aug_tokens <= remaining:
            aug_text = aug_block + "\n"
            remaining -= aug_tokens
        # If aug_block doesn't fit, we still include it but sacrifice class body
        elif aug_tokens <= remaining + count_tokens(sections.class_body_before):
            aug_text = aug_block + "\n"
            remaining -= aug_tokens

    # P5-P7: class body members by priority
    body_text = ""
    if remaining > 0 and sections.class_body_before:
        body_text = _prioritized_class_body(sections.class_body_before, method, remaining)

    # Assemble in file order
    return (
        sections.package_decl
        + imports_text
        + sections.class_header
        + body_text
        + aug_text
        + sections.method_header
    )


def _build_structured_suffix(
    method: ExtractedMethod,
    budget_tokens: int,
) -> str:
    """Build a trimmed suffix preserving closing braces."""
    file_content = method.file_content
    body_end = method.body_end_offset
    full_suffix = file_content[body_end:]

    if count_tokens(full_suffix) <= budget_tokens:
        return full_suffix

    # Find minimum suffix: walk until brace depth reaches 0 (file level)
    depth = 0
    min_end = 0
    for i, c in enumerate(full_suffix):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        # When depth goes to -1, we've closed the enclosing method's remaining brace
        # When depth goes further negative, we close the class
        if c == "\n" and depth <= -2:
            # We've closed at least the method and the class
            min_end = i + 1
            break
    else:
        # Didn't find enough closing braces — take first few lines
        min_end = min(len(full_suffix), 200)

    suffix_near = full_suffix[:min_end]
    near_tokens = count_tokens(suffix_near)

    if near_tokens >= budget_tokens:
        return suffix_near  # even min suffix exceeds budget, but we need it

    # Fill remaining budget from the rest of suffix
    suffix_rest = full_suffix[min_end:]
    rest_budget = budget_tokens - near_tokens
    if suffix_rest and rest_budget > 0:
        trimmed_rest = _trim_from_end(suffix_rest, rest_budget)
        return suffix_near + trimmed_rest

    return suffix_near


# ── Legacy line-based trimmers (kept for suffix_rest and backward compat) ──

def _trim_from_start(text: str, max_tokens: int) -> str:
    """Keep the *end* of text, trim from beginning at line boundaries."""
    if count_tokens(text) <= max_tokens:
        return text
    lines = text.split("\n")
    lo, hi = 0, len(lines)
    while lo < hi:
        mid = (lo + hi) // 2
        candidate = "\n".join(lines[mid:])
        if count_tokens(candidate) <= max_tokens:
            hi = mid
        else:
            lo = mid + 1
    return "\n".join(lines[lo:])


def _trim_from_end(text: str, max_tokens: int) -> str:
    """Keep the *beginning* of text, trim from end at line boundaries."""
    if count_tokens(text) <= max_tokens:
        return text
    lines = text.split("\n")
    lo, hi = 0, len(lines)
    while lo < hi:
        mid = (lo + hi) // 2
        candidate = "\n".join(lines[:mid + 1])
        if count_tokens(candidate) <= max_tokens:
            lo = mid + 1
        else:
            hi = mid
    return "\n".join(lines[:lo])


# ═══════════════════════════════════════════════════════════════════════════
# Main prompt builder
# ═══════════════════════════════════════════════════════════════════════════

def build_fim_prompt(
    method: ExtractedMethod,
    mode: str,
    shuffle_seed: int | None = None,
    retrieval_augmentation: str | None = None,
    token_budget: int = 0,
) -> FIMPrompt:
    """Build a FIM prompt for the given method and mode.

    Parameters
    ----------
    token_budget:
        Maximum number of tokens for the **prompt** (context_window - max_tokens).
        When > 0, prefix and suffix are trimmed semantically. When 0, no trimming.
    """
    file_content = method.file_content
    body_start = method.body_start_offset
    body_end = method.body_end_offset

    prefix = file_content[:body_start + 1]
    suffix = file_content[body_end:]
    ground_truth = file_content[body_start + 1 : body_end]

    cross_file_context = ""
    if is_retrieval_mode(mode):
        aug_block = retrieval_augmentation if retrieval_augmentation else None
        if aug_block:
            cross_file_context = aug_block + "\n"
        invocations_as_used = sorted(method.invocations, key=lambda inv: inv.order_index)
    else:
        aug_block, invocations_as_used = build_augmentation_block(method.invocations, mode, shuffle_seed)
        if aug_block and token_budget <= 0:
            # No trimming: insert aug_block into prefix at the original position
            insert_pos = find_method_signature_position(file_content, body_start)
            prefix = file_content[:insert_pos] + aug_block + "\n" + file_content[insert_pos:body_start + 1]

    # ── Semantic trimming ──
    if token_budget > 0:
        fim_overhead = count_tokens(FIM_PREFIX + FIM_SUFFIX + FIM_MIDDLE) + _FIM_OVERHEAD_TOKENS
        cfc_tokens = count_tokens(cross_file_context) if cross_file_context else 0
        available = token_budget - fim_overhead - cfc_tokens

        if available <= 0:
            log.warning("Token budget (%d) too small for FIM overhead + cross-file context", token_budget)
        else:
            suffix_budget = int(available * 0.35)
            prefix_budget = available - suffix_budget

            # For non-retrieval modes, aug_block is included inside the prefix
            aug_for_prefix = aug_block if not is_retrieval_mode(mode) else None
            prefix = _build_structured_prefix(method, aug_for_prefix, prefix_budget)
            suffix = _build_structured_suffix(method, suffix_budget)

    # ── Assemble FIM prompt (tokens added LAST, after all trimming) ──
    full_prompt = f"{cross_file_context}{FIM_PREFIX}{prefix}{FIM_SUFFIX}{suffix}{FIM_MIDDLE}"

    # ── Integrity assertion ──
    assert full_prompt.count(FIM_PREFIX) == 1, "FIM_PREFIX missing or duplicated"
    assert full_prompt.count(FIM_SUFFIX) == 1, "FIM_SUFFIX missing or duplicated"
    assert full_prompt.count(FIM_MIDDLE) == 1, "FIM_MIDDLE missing or duplicated"

    return FIMPrompt(
        prefix=prefix,
        suffix=suffix,
        ground_truth=ground_truth,
        augmentation_block=aug_block,
        invocations_as_used=invocations_as_used,
        full_prompt=full_prompt,
    )
