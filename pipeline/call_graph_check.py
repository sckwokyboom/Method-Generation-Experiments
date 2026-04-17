"""Validation report for a built call-graph: structural stats + sanity checks."""
from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from pathlib import Path

import torch
from tabulate import tabulate

from pipeline.call_graph import CallGraph, load_call_graph


def _shorten(vid: str, max_len: int = 90) -> str:
    return vid if len(vid) <= max_len else vid[: max_len - 1] + "…"


def collect_stats(graph: CallGraph) -> dict:
    adj = graph.adjacency
    n = len(graph.vertex_ids)
    indices = adj.indices()
    values = adj.values()
    e_unique = values.numel()
    weight_sum = int(values.sum().item()) if e_unique else 0

    srcs = indices[0].tolist() if e_unique else []
    dsts = indices[1].tolist() if e_unique else []
    vals = values.tolist() if e_unique else []

    out_deg = torch.zeros(n, dtype=torch.int64)
    in_deg = torch.zeros(n, dtype=torch.int64)
    for s, d, w in zip(srcs, dsts, vals):
        out_deg[s] += w
        in_deg[d] += w

    self_loop_pairs = [(s, w) for s, d, w in zip(srcs, dsts, vals) if s == d]
    isolated = [i for i in range(n) if out_deg[i] == 0 and in_deg[i] == 0]

    return {
        "n_vertices": n,
        "n_edges_unique": e_unique,
        "weight_sum": weight_sum,
        "density": e_unique / (n * n) if n else 0.0,
        "self_loops": self_loop_pairs,
        "isolated": isolated,
        "in_deg": in_deg,
        "out_deg": out_deg,
        "total_deg": in_deg + out_deg,
        "values": vals,
    }


def sanity_checks(graph: CallGraph, stats: dict) -> list[tuple[str, bool, str]]:
    adj = graph.adjacency
    n = stats["n_vertices"]
    checks: list[tuple[str, bool, str]] = []

    checks.append((
        "adjacency shape matches vertex count",
        tuple(adj.shape) == (n, n),
        f"shape={tuple(adj.shape)}, expected ({n},{n})",
    ))
    checks.append((
        "dtype is int64",
        adj.dtype == torch.int64,
        f"dtype={adj.dtype}",
    ))
    checks.append((
        "adjacency is sparse (coalesced)",
        adj.is_sparse and adj.is_coalesced(),
        f"is_sparse={adj.is_sparse}, is_coalesced={adj.is_coalesced()}",
    ))
    values = adj.values()
    if values.numel():
        checks.append((
            "all edge weights are positive",
            bool((values > 0).all().item()),
            f"min={int(values.min().item())}",
        ))
    else:
        checks.append(("all edge weights are positive", True, "no edges"))

    indices = adj.indices()
    if indices.numel():
        checks.append((
            "all edge indices within [0, N)",
            bool(((indices >= 0) & (indices < n)).all().item()),
            "out-of-bounds indices found" if not bool(((indices >= 0) & (indices < n)).all().item()) else "ok",
        ))

    checks.append((
        "vertex_meta length matches vertex count",
        len(graph.vertex_meta) == n,
        f"len(vertex_meta)={len(graph.vertex_meta)}, N={n}",
    ))
    checks.append((
        "no duplicate vertex_ids",
        len(set(graph.vertex_ids)) == n,
        f"unique={len(set(graph.vertex_ids))}, N={n}",
    ))
    return checks


def _render_histogram(values: list[int], buckets: int = 10, width: int = 40) -> str:
    if not values:
        return "(no data)"
    vmin, vmax = min(values), max(values)
    if vmin == vmax:
        return f"all values = {vmin} (n={len(values)})"
    # Log-ish buckets for skewed degree distribution
    edges = [vmin + (vmax - vmin) * i / buckets for i in range(buckets + 1)]
    counts = [0] * buckets
    for v in values:
        # Map v to bucket
        idx = min(int((v - vmin) / (vmax - vmin) * buckets), buckets - 1)
        counts[idx] += 1
    peak = max(counts) or 1
    lines = []
    for i in range(buckets):
        lo, hi = edges[i], edges[i + 1]
        bar = "█" * int(width * counts[i] / peak)
        lines.append(f"  [{lo:>6.1f} .. {hi:<6.1f}) {counts[i]:>5}  {bar}")
    return "\n".join(lines)


def print_report(graph: CallGraph, stats: dict, seed: int = 0) -> bool:
    print("=" * 72)
    print("CALL-GRAPH VALIDATION REPORT")
    print("=" * 72)

    print("\n── Summary ──")
    summary = [
        ["vertices (N)", stats["n_vertices"]],
        ["unique edges (nnz)", stats["n_edges_unique"]],
        ["total invocations (sum of weights)", stats["weight_sum"]],
        ["density (nnz / N²)", f"{stats['density']:.6f}"],
        ["self-loop edges", len(stats["self_loops"])],
        ["self-loop vertices", len({s for s, _ in stats["self_loops"]})],
        ["isolated vertices", len(stats["isolated"])],
    ]
    print(tabulate(summary, tablefmt="simple"))

    print("\n── Sanity checks ──")
    checks = sanity_checks(graph, stats)
    rows = [[("PASS" if ok else "FAIL"), name, detail] for name, ok, detail in checks]
    print(tabulate(rows, headers=["", "Check", "Detail"], tablefmt="simple"))
    all_pass = all(ok for _, ok, _ in checks)

    def _top(deg_tensor: torch.Tensor, k: int = 10) -> list[list]:
        vals, idxs = torch.topk(deg_tensor, min(k, deg_tensor.numel()))
        return [[int(vals[i].item()), _shorten(graph.vertex_ids[int(idxs[i].item())])]
                for i in range(idxs.numel()) if vals[i].item() > 0]

    print("\n── Top 10 out-degree (most calls made) ──")
    print(tabulate(_top(stats["out_deg"]), headers=["out", "vertex"], tablefmt="simple"))

    print("\n── Top 10 in-degree (most called) ──")
    print(tabulate(_top(stats["in_deg"]), headers=["in", "vertex"], tablefmt="simple"))

    print("\n── Top 10 total degree ──")
    print(tabulate(_top(stats["total_deg"]), headers=["deg", "vertex"], tablefmt="simple"))

    print("\n── Edge weight distribution (values of A where A>0) ──")
    weight_counter = Counter(stats["values"])
    if weight_counter:
        rows = sorted(weight_counter.items())
        print(tabulate(rows, headers=["weight", "count"], tablefmt="simple"))

    print("\n── Out-degree distribution ──")
    print(_render_histogram([int(x.item()) for x in stats["out_deg"]]))

    print("\n── In-degree distribution ──")
    print(_render_histogram([int(x.item()) for x in stats["in_deg"]]))

    if stats["self_loops"]:
        print("\n── Self-loops (recursion) ──")
        rows = [[w, _shorten(graph.vertex_ids[s])] for s, w in stats["self_loops"]]
        print(tabulate(rows, headers=["weight", "vertex"], tablefmt="simple"))

    if stats["isolated"]:
        print(f"\n── Isolated vertices (no edges): {len(stats['isolated'])} ──")
        sample = stats["isolated"][: min(10, len(stats["isolated"]))]
        for idx in sample:
            print(f"  {_shorten(graph.vertex_ids[idx])}")
        if len(stats["isolated"]) > 10:
            print(f"  … and {len(stats['isolated']) - 10} more")

    print("\n── Random sample for hand-verification (5 methods with outgoing edges) ──")
    rng = random.Random(seed)
    candidates = [i for i in range(stats["n_vertices"]) if stats["out_deg"][i] > 0]
    picks = rng.sample(candidates, k=min(5, len(candidates)))
    adj_dense = graph.adjacency.to_dense()
    for i in picks:
        meta = graph.vertex_meta[i]
        print(f"\n  [{i}] {graph.vertex_ids[i]}")
        print(f"       file: {meta['file_path']}")
        targets = [(j, int(adj_dense[i, j].item())) for j in range(stats["n_vertices"]) if adj_dense[i, j] > 0]
        for j, w in targets:
            print(f"       -> [w={w}] {_shorten(graph.vertex_ids[j], 80)}")

    print()
    print("=" * 72)
    print("ALL SANITY CHECKS PASSED" if all_pass else "SOME SANITY CHECKS FAILED")
    print("=" * 72)
    return all_pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mgx-call-graph-check",
        description="Print structural stats and sanity checks for a built call graph.",
    )
    parser.add_argument("graph_dir", type=Path, help="Directory with adjacency.pt and vertices.json.")
    parser.add_argument("--seed", type=int, default=0, help="Seed for random-sample section.")
    args = parser.parse_args(argv)

    if not (args.graph_dir / "adjacency.pt").exists():
        print(f"error: {args.graph_dir}/adjacency.pt not found", file=sys.stderr)
        return 2

    graph = load_call_graph(args.graph_dir)
    stats = collect_stats(graph)
    ok = print_report(graph, stats, seed=args.seed)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
