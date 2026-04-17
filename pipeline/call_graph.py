"""Build a weighted adjacency matrix of method call edges from an extractor JSON."""
from __future__ import annotations

import logging
import re
from typing import Iterable

log = logging.getLogger(__name__)


def canonical_vertex_id(
    class_fqn: str,
    method_name: str,
    parameter_types: Iterable[str],
    return_type: str,
) -> str:
    params = ",".join(parameter_types)
    return f"{class_fqn}::{method_name}({params}) -> {return_type}"


_WHITESPACE_AFTER_COMMA = re.compile(r",\s+")
_MULTISPACE = re.compile(r"\s+")


def canonicalize_invocation_signature(signature: str) -> str:
    head, sep, tail = signature.partition("->")
    head = _WHITESPACE_AFTER_COMMA.sub(",", head).strip()
    if not sep:
        return head
    tail = _MULTISPACE.sub(" ", tail).strip()
    return f"{head} -> {tail}"


def flatten_vertices(extraction_data: dict) -> list[dict]:
    """
    Walk extraction JSON and return deterministic list of vertex dicts.

    Covers both primary methods (methods[*]) and sibling methods (methods[*].siblingMethods[*]).
    Deduplicates by canonical vertex_id.

    Each returned vertex dict has:
    - vertex_id: str (from canonical_vertex_id)
    - class_fqn: str
    - method_name: str
    - file_path: str
    - parameter_types: list[str]
    - return_type: str
    - invocations: list[dict] (raw invocation dicts, preserved as-is from JSON)
    """
    seen: dict[str, dict] = {}
    for primary in extraction_data.get("methods", []):
        class_fqn = primary["classFqn"]
        file_path = primary["filePath"]

        _insert_vertex(
            seen,
            class_fqn=class_fqn,
            method_name=primary["methodName"],
            parameter_types=primary.get("parameterTypes", []),
            return_type=primary.get("returnType") or "void",
            file_path=file_path,
            invocations=primary.get("invocations", []),
        )

        for sib in primary.get("siblingMethods", []):
            _insert_vertex(
                seen,
                class_fqn=class_fqn,
                method_name=sib["methodName"],
                parameter_types=sib.get("parameterTypes", []),
                return_type=sib.get("returnType") or "void",
                file_path=file_path,
                invocations=sib.get("invocations", []),
            )

    return sorted(seen.values(), key=lambda v: v["vertex_id"])


def _insert_vertex(
    seen: dict,
    *,
    class_fqn: str,
    method_name: str,
    parameter_types: list[str],
    return_type: str,
    file_path: str,
    invocations: list[dict],
) -> None:
    """Insert a vertex into the seen dict, warn on collision and keep first."""
    vid = canonical_vertex_id(class_fqn, method_name, parameter_types, return_type)
    if vid in seen:
        log.warning("duplicate vertex_id collision: %s (keeping first)", vid)
        return
    seen[vid] = {
        "vertex_id": vid,
        "class_fqn": class_fqn,
        "method_name": method_name,
        "file_path": file_path,
        "parameter_types": list(parameter_types),
        "return_type": return_type,
        "invocations": list(invocations),
    }


def build_edge_counts(vertices: list[dict]) -> dict[tuple[str, str], int]:
    """Count occurrences of each EXACT internal method invocation edge."""
    vertex_ids = {v["vertex_id"] for v in vertices}
    counts: dict[tuple[str, str], int] = {}
    for v in vertices:
        src = v["vertex_id"]
        for inv in v.get("invocations", []):
            if inv.get("resolutionMode") != "EXACT":
                continue
            dst = canonicalize_invocation_signature(inv["signature"])
            if dst not in vertex_ids:
                continue
            key = (src, dst)
            counts[key] = counts.get(key, 0) + 1
    return counts
