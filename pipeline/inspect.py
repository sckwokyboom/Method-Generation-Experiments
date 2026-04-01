"""
Dry-run inspection tool: shows dataset samples without calling the LLM.

Usage:
    python -m pipeline.inspect --config config.yaml [--n 3] [--index 5] [--mode ordered_augmentation]
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import textwrap
from pathlib import Path

from pipeline.config import Config
from pipeline.dataset import build_dataset, load_extraction
from pipeline.models import ExtractedMethod, RetrievalResponse
from pipeline.prompt import build_fim_prompt, is_retrieval_mode
from pipeline.retrieval import build_index, build_search_request, search_batch, format_retrieval_augmentation

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger(__name__)

# Terminal color codes
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
MAGENTA= "\033[95m"
WHITE  = "\033[97m"


def hr(char="─", width=100, color=DIM):
    print(f"{color}{char * width}{RESET}")


def header(text: str, color=BOLD + CYAN):
    hr()
    print(f"{color}  {text}{RESET}")
    hr()


def section(label: str, color=YELLOW):
    print(f"\n{color}{BOLD}{'━' * 4} {label} {'━' * (90 - len(label))}{RESET}")


def print_sample(
    method: ExtractedMethod,
    sample_idx: int,
    total: int,
    modes: list[str],
    shuffle_seed: int,
    retrieval_response: RetrievalResponse | None = None,
    retrieval_config=None,
) -> None:
    header(
        f"SAMPLE {sample_idx + 1}/{total}  │  {method.class_fqn}#{method.method_name}",
        color=BOLD + CYAN,
    )

    # ── Method metadata ──────────────────────────────────────────────────────
    section("METHOD METADATA")
    print(f"  {DIM}File:{RESET}       {method.file_path}")
    print(f"  {DIM}Class:{RESET}      {method.class_fqn}")
    print(f"  {DIM}Signature:{RESET}  {method.method_signature}")
    print(f"  {DIM}Category:{RESET}   {method.category}")
    print(f"  {DIM}Statements:{RESET} {method.statement_count}")
    print(f"  {DIM}Invocations:{RESET} {len(method.invocations)}")

    # ── Ground truth body ─────────────────────────────────────────────────────
    section("GROUND TRUTH BODY", color=GREEN)
    body_lines = method.method_body.splitlines()
    for line in body_lines:
        print(f"  {GREEN}{line}{RESET}")

    # ── Invocation signatures ─────────────────────────────────────────────────
    section(f"EXTRACTED INVOCATION SIGNATURES ({len(method.invocations)})", color=MAGENTA)
    if not method.invocations:
        print(f"  {DIM}(no invocations found){RESET}")
    else:
        for inv in sorted(method.invocations, key=lambda i: i.order_index):
            mode_color = GREEN if inv.resolution_mode == "EXACT" else RED
            print(
                f"  {DIM}[{inv.order_index:2d}]{RESET}  "
                f"{mode_color}{inv.resolution_mode:10s}{RESET}  "
                f"{inv.signature}"
            )

    # ── Retrieval results (if available) ─────────────────────────────────────
    if retrieval_response and retrieval_response.results:
        section(f"RETRIEVAL RESULTS ({len(retrieval_response.results)} hits, {retrieval_response.search_time_ms}ms)", color=CYAN)
        for r in retrieval_response.results:
            print(
                f"  {BOLD}#{r.rank}{RESET}  "
                f"{YELLOW}score={r.score:.2f}{RESET}  "
                f"{GREEN}{r.signature}{RESET}"
            )
            print(f"    {DIM}{r.class_fqn} | {r.file_path}{RESET}")
            if r.invocation_profile:
                inv_lines = [l.strip() for l in r.invocation_profile.split("\n")
                             if l.strip() and not l.strip().startswith("bigram:") and not l.strip().startswith("trigram:")]
                if inv_lines:
                    print(f"    {DIM}Invocations: {', '.join(inv_lines[:4])}{RESET}")
            print()

        if retrieval_response.query_debug:
            section("LUCENE QUERY", color=DIM)
            for line in textwrap.wrap(retrieval_response.query_debug, width=120):
                print(f"  {DIM}{line}{RESET}")

    # ── Prompts per mode ──────────────────────────────────────────────────────
    for mode in modes:
        section(f"PROMPT  ({mode})", color=YELLOW)

        retrieval_aug = None
        if is_retrieval_mode(mode) and retrieval_response and retrieval_config:
            retrieval_aug = format_retrieval_augmentation(retrieval_response, retrieval_config)

        fim = build_fim_prompt(method, mode, shuffle_seed, retrieval_aug)

        if fim.augmentation_block:
            print(f"  {DIM}Augmentation block:{RESET}")
            for line in fim.augmentation_block.splitlines():
                print(f"    {CYAN}{line}{RESET}")
            print()

        prompt_display = fim.full_prompt
        prefix_end   = prompt_display.find("<|fim_suffix|>")
        suffix_end   = prompt_display.find("<|fim_middle|>")
        prefix_part  = prompt_display[len("<|fim_prefix|>"):prefix_end]
        suffix_part  = prompt_display[prefix_end + len("<|fim_suffix|>"):suffix_end]

        MAX_CONTEXT_LINES = 20
        prefix_lines = prefix_part.splitlines()
        suffix_lines = suffix_part.splitlines()

        print(f"  {DIM}── prefix (last {MAX_CONTEXT_LINES} lines) ──────────────────{RESET}")
        for line in prefix_lines[-MAX_CONTEXT_LINES:]:
            print(f"  {WHITE}{line}{RESET}")

        print(f"\n  {DIM}── <|fim_middle|>  ← model generates here ──────────────{RESET}\n")

        print(f"  {DIM}── suffix (first {MAX_CONTEXT_LINES} lines) ──────────────────{RESET}")
        for line in suffix_lines[:MAX_CONTEXT_LINES]:
            print(f"  {WHITE}{line}{RESET}")

        print(f"\n  {DIM}Prompt token estimate: ~{len(fim.full_prompt) // 4} tokens{RESET}")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Inspect dataset samples without calling the LLM"
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--n", type=int, default=3, help="Number of samples to inspect (default: 3)")
    parser.add_argument("--index", type=int, default=None,
                        help="Inspect a specific sample index (0-based) from the dataset")
    parser.add_argument("--mode", nargs="*", default=None,
                        help="Modes to show (default: all three)")
    parser.add_argument("--json", action="store_true",
                        help="Also dump raw sample JSON for the first sample")
    parser.add_argument("--skip-extraction", action="store_true",
                        help="Skip extraction, use existing results/extracted_methods.json")
    args = parser.parse_args()

    config = Config.load(args.config)
    modes = args.mode or config.experiment.modes

    extraction_path = Path(config.extraction.output)
    if not extraction_path.exists():
        if args.skip_extraction:
            print(f"ERROR: {extraction_path} not found and --skip-extraction was given")
            return
        print(f"Extraction output not found at {extraction_path}.")
        print("Run extraction first:")
        print(f"  java -jar {config.extraction.extractor_jar} \\")
        print(f"    --project-path {config.project.path} \\")
        print(f"    --output {extraction_path} \\")
        print(f"    --min-statements {config.extraction.min_statements} \\")
        print(f"    --build-first")
        return

    methods, classpath = build_dataset(config)

    if args.index is not None:
        if args.index >= len(methods):
            print(f"ERROR: index {args.index} out of range (dataset has {len(methods)} methods)")
            return
        to_show = [methods[args.index]]
        start_idx = args.index
    else:
        count = min(args.n, len(methods))
        to_show = methods[:count]
        start_idx = 0

    # Build retrieval responses for any retrieval mode requested.
    # For inspect, we use the first retrieval mode found and show its results.
    retrieval_responses: list[RetrievalResponse | None] = [None] * len(methods)
    retrieval_config_for_inspect = None
    retrieval_modes_in_inspect = [m for m in modes if is_retrieval_mode(m)]
    if retrieval_modes_in_inspect:
        # Use the first retrieval mode to build responses for inspection
        first_ret_mode = retrieval_modes_in_inspect[0]
        from pipeline.run import _retrieval_config_for_mode
        try:
            retrieval_config_for_inspect = _retrieval_config_for_mode(config, first_ret_mode)
        except ValueError as e:
            print(f"{RED}ERROR: {e}{RESET}")
            return
        if retrieval_config_for_inspect is None:
            print(f"{RED}ERROR: {first_ret_mode} mode requires a retrieval config{RESET}")
            return
        # Temporarily swap config.retrieval for the retrieval.py calls
        original_retrieval = config.retrieval
        config.retrieval = retrieval_config_for_inspect
        try:
            build_index(config)
            indices = [start_idx + i for i in range(len(to_show))]
            requests = [build_search_request(methods[idx], config) for idx in indices]
            responses = search_batch(requests, config)
            for j, idx in enumerate(indices):
                retrieval_responses[idx] = responses[j]
        except Exception as e:
            print(f"{RED}WARNING: Retrieval failed: {e}{RESET}")
            print(f"{DIM}Continuing without retrieval results...{RESET}\n")
        finally:
            config.retrieval = original_retrieval

    print(f"\n{BOLD}Dataset: {len(methods)} methods sampled{RESET}")
    print(f"Showing: {len(to_show)} sample(s) | Modes: {', '.join(modes)}\n")

    for i, method in enumerate(to_show):
        idx = start_idx + i
        print_sample(
            method,
            sample_idx=idx,
            total=len(methods),
            modes=modes,
            shuffle_seed=config.experiment.shuffle_seed,
            retrieval_response=retrieval_responses[idx],
            retrieval_config=retrieval_config_for_inspect,
        )

    if args.json and to_show:
        m = to_show[0]
        section("RAW JSON (first sample)", color=DIM)
        raw = {
            "file_path": m.file_path,
            "class_fqn": m.class_fqn,
            "method_signature": m.method_signature,
            "method_body": m.method_body,
            "statement_count": m.statement_count,
            "category": m.category,
            "invocations": [
                {"order_index": inv.order_index, "resolution_mode": inv.resolution_mode, "signature": inv.signature}
                for inv in m.invocations
            ],
        }
        print(json.dumps(raw, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
