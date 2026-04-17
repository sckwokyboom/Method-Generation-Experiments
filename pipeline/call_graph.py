"""Build a weighted adjacency matrix of method call edges from an extractor JSON."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import torch

log = logging.getLogger(__name__)


_WHITESPACE_AFTER_COMMA = re.compile(r",\s+")
_MULTISPACE = re.compile(r"\s+")


def canonicalize_invocation_signature(signature: str) -> str:
    head, sep, tail = signature.partition("->")
    head = _WHITESPACE_AFTER_COMMA.sub(",", head).strip()
    if not sep:
        return head
    tail = _MULTISPACE.sub(" ", tail).strip()
    return f"{head} -> {tail}"


def canonical_vertex_id(
    class_fqn: str,
    method_name: str,
    parameter_types: Iterable[str],
    return_type: str,
) -> str:
    # Route through the invocation-signature normalizer so vertex IDs and
    # invocation-signature IDs use identical whitespace rules. Without this,
    # a param like "Map<K, V>" (space after comma) would produce different
    # IDs on each side and drop the edge silently.
    raw = f"{class_fqn}::{method_name}({','.join(parameter_types)}) -> {return_type}"
    return canonicalize_invocation_signature(raw)


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


@dataclass
class CallGraph:
    """Sparse COO torch tensor representation of method call graph."""

    vertex_ids: list[str]  # index → canonical vertex id (len == N)
    vertex_meta: list[dict]  # index → dict from flatten_vertices (len == N)
    adjacency: "torch.Tensor"  # sparse COO, shape (N, N), dtype=torch.int64, weight = invocation count

    def to_dense(self) -> torch.Tensor:
        """Convert sparse adjacency matrix to dense."""
        return self.adjacency.to_dense()


def build_call_graph(extraction_data: dict) -> CallGraph:
    """Build a torch sparse COO adjacency matrix from extraction data."""
    vertices = flatten_vertices(extraction_data)
    edges = build_edge_counts(vertices)

    vertex_ids = [v["vertex_id"] for v in vertices]
    index = {vid: i for i, vid in enumerate(vertex_ids)}
    n = len(vertex_ids)

    if edges:
        srcs = [index[src] for (src, _dst) in edges.keys()]
        dsts = [index[dst] for (_src, dst) in edges.keys()]
        values = list(edges.values())
        indices = torch.tensor([srcs, dsts], dtype=torch.int64)
        values_t = torch.tensor(values, dtype=torch.int64)
    else:
        indices = torch.empty((2, 0), dtype=torch.int64)
        values_t = torch.empty((0,), dtype=torch.int64)

    adjacency = torch.sparse_coo_tensor(
        indices, values_t, size=(n, n), dtype=torch.int64
    ).coalesce()
    return CallGraph(vertex_ids=vertex_ids, vertex_meta=vertices, adjacency=adjacency)


def save_call_graph(graph: CallGraph, out_path: Path) -> None:
    """Save CallGraph to directory: adjacency.pt and vertices.json."""
    out_path = Path(out_path)
    out_path.mkdir(parents=True, exist_ok=True)
    torch.save(graph.adjacency, out_path / "adjacency.pt")
    with (out_path / "vertices.json").open("w", encoding="utf-8") as f:
        json.dump(
            {"vertex_ids": graph.vertex_ids, "vertex_meta": graph.vertex_meta},
            f,
            ensure_ascii=False,
            indent=2,
        )


def load_call_graph(in_path: Path) -> CallGraph:
    """Load CallGraph from directory: adjacency.pt and vertices.json."""
    in_path = Path(in_path)
    with (in_path / "vertices.json").open("r", encoding="utf-8") as f:
        data = json.load(f)
    adjacency = torch.load(in_path / "adjacency.pt", weights_only=False)
    return CallGraph(
        vertex_ids=data["vertex_ids"],
        vertex_meta=data["vertex_meta"],
        adjacency=adjacency,
    )
