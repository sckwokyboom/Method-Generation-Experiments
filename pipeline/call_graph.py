"""Build a weighted adjacency matrix of method call edges from an extractor JSON."""
from __future__ import annotations

import re
from typing import Iterable


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
