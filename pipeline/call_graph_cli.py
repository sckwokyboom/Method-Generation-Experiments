"""CLI: read extractor JSON, build call-graph adjacency matrix, save to directory."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from pipeline.call_graph import build_call_graph, save_call_graph


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mgx-call-graph",
        description="Build a call-graph adjacency matrix from an extractor JSON.",
    )
    parser.add_argument(
        "extraction_json",
        type=Path,
        help="Path to extracted_methods.json produced by the extractor.",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        required=True,
        help="Output directory (will contain adjacency.pt and vertices.json).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log INFO-level progress.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("mgx-call-graph")

    if not args.extraction_json.exists():
        log.error("extraction json not found: %s", args.extraction_json)
        return 2

    log.info("loading extraction: %s", args.extraction_json)
    with args.extraction_json.open("r", encoding="utf-8") as f:
        data = json.load(f)

    log.info("building call graph")
    graph = build_call_graph(data)

    log.info("saving to %s", args.out)
    save_call_graph(graph, args.out)

    n = len(graph.vertex_ids)
    nnz = graph.adjacency.values().numel()
    print(f"call graph: {n} vertices, {nnz} edges (after coalesce) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
