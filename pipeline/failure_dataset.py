"""Mine compilable-but-wrong generated methods from experiment results.

The miner expects samples that already contain:
  * metrics.compilable from the javac check
  * metrics.em from Exact Match evaluation
  * test_eval from the project test runner

It exports candidates where generated code compiles, is not an Exact Match, and
causes test failures. Related failing tests are detected heuristically from the
optional enrichment fields produced by ``pipeline.enrich_samples``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class RelatedFailure:
    name: str
    reasons: list[str]
    score: int


@dataclass
class MineStats:
    total_seen: int = 0
    candidates: int = 0
    related_candidates: int = 0
    by_mode: Counter[str] = field(default_factory=Counter)
    rejected: Counter[str] = field(default_factory=Counter)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, suffix=".tmp", prefix=path.stem
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _sample_index(path: Path) -> int | None:
    match = re.search(r"sample_(\d+)\.json$", path.name)
    return int(match.group(1)) if match else None


def _discover_sample_files(results_dir: Path, modes: set[str] | None = None) -> list[Path]:
    """Return sample JSON files under a results directory or a single samples directory."""
    if results_dir.name == "samples":
        return sorted(results_dir.glob("sample_*.json"))

    sample_files: list[Path] = []
    for child in sorted(results_dir.iterdir()):
        samples_dir = child / "samples"
        if not child.is_dir() or not samples_dir.is_dir():
            continue
        if modes is not None and child.name not in modes:
            continue
        sample_files.extend(sorted(samples_dir.glob("sample_*.json")))
    return sample_files


def _test_path_to_fqn(test_path: str) -> str | None:
    normalized = test_path.replace("\\", "/")
    for marker in (
        "src/test/java/",
        "src/test/kotlin/",
        "src/test/groovy/",
        "src/testFixtures/java/",
    ):
        idx = normalized.find(marker)
        if idx < 0:
            continue
        rel = normalized[idx + len(marker):]
        for ext in (".java", ".kt", ".groovy"):
            if rel.endswith(ext):
                rel = rel[: -len(ext)]
                break
        return rel.replace("/", ".")
    return None


def _class_simple_name(class_name: str) -> str:
    return class_name.rsplit(".", 1)[-1].split("$", 1)[0]


def _strip_test_suffix(name: str) -> str:
    for suffix in ("Tests", "Test", "IT", "Spec"):
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def _split_failed_test_name(name: str) -> tuple[str, str | None]:
    """Split a report name like ``pkg.FooTests.test[1]`` into class and method."""
    if "." not in name:
        return name, None
    class_name, method_name = name.rsplit(".", 1)
    method_name = re.sub(r"\[.*$", "", method_name)
    method_name = re.sub(r"\(.*$", "", method_name)
    return class_name, method_name or None


def _method_name_from_sample(raw: dict[str, Any]) -> str:
    method_id = str(raw.get("method_id", ""))
    if "#" in method_id:
        return method_id.rsplit("#", 1)[-1]
    signature = str(raw.get("method_signature", ""))
    match = re.search(r"\b([A-Za-z_$][\w$]*)\s*\(", signature)
    return match.group(1) if match else ""


def _class_name_from_sample(raw: dict[str, Any]) -> str:
    method_id = str(raw.get("method_id", ""))
    if "#" in method_id:
        return method_id.split("#", 1)[0]
    return ""


def _direct_test_keys(raw: dict[str, Any]) -> set[tuple[str | None, str | None, str | None]]:
    keys: set[tuple[str | None, str | None, str | None]] = set()
    for entry in raw.get("direct_test_methods") or []:
        test_class = entry.get("test_class")
        test_method = entry.get("test_method")
        test_file = entry.get("test_file")
        test_fqn = _test_path_to_fqn(test_file) if test_file else None
        keys.add((test_fqn, test_class, test_method))
    return keys


def _referenced_test_classes(raw: dict[str, Any]) -> set[str]:
    classes: set[str] = set()
    for test_path in raw.get("test_file_paths") or []:
        fqn = _test_path_to_fqn(test_path)
        if fqn:
            classes.add(fqn)
            classes.add(_class_simple_name(fqn))
    return classes


def _related_failures(raw: dict[str, Any]) -> list[RelatedFailure]:
    test_eval = raw.get("test_eval") or {}
    failed_names = test_eval.get("failed_test_names") or []
    method_name = _method_name_from_sample(raw)
    target_class = _class_name_from_sample(raw)
    target_class_simple = _class_simple_name(target_class) if target_class else ""
    target_class_base = _strip_test_suffix(target_class_simple)
    direct_keys = _direct_test_keys(raw)
    referenced_test_classes = _referenced_test_classes(raw)

    related: list[RelatedFailure] = []
    for failed_name in failed_names:
        failed_class, failed_method = _split_failed_test_name(str(failed_name))
        failed_class_simple = _class_simple_name(failed_class)
        failed_class_base = _strip_test_suffix(failed_class_simple)
        reasons: list[str] = []

        for test_fqn, test_class, test_method in direct_keys:
            class_matches = (
                (test_fqn and failed_class == test_fqn)
                or (test_class and failed_class_simple == test_class)
                or (test_class and failed_class.endswith("." + test_class))
            )
            method_matches = (
                test_method is not None
                and failed_method is not None
                and (
                    failed_method == test_method
                    or failed_method.startswith(test_method + "[")
                    or failed_method.startswith(test_method + "(")
                )
            )
            if class_matches and method_matches:
                reasons.append("direct_test_method")
                break

        if failed_class in referenced_test_classes or failed_class_simple in referenced_test_classes:
            reasons.append("referenced_test_file")

        if target_class_base and failed_class_base == target_class_base:
            reasons.append("target_class_test_class_match")

        if method_name and method_name.lower() in str(failed_name).lower():
            reasons.append("method_name_overlap")

        if reasons:
            score = 0
            if "direct_test_method" in reasons:
                score += 4
            if "referenced_test_file" in reasons:
                score += 2
            if "target_class_test_class_match" in reasons:
                score += 1
            if "method_name_overlap" in reasons:
                score += 1
            related.append(RelatedFailure(str(failed_name), reasons, score))

    return related


def _metric(raw: dict[str, Any], name: str, default: Any = None) -> Any:
    return (raw.get("metrics") or {}).get(name, default)


def _is_candidate(raw: dict[str, Any], stats: MineStats) -> bool:
    metrics = raw.get("metrics") or {}
    test_eval = raw.get("test_eval") or {}

    if metrics.get("compilable") is not True:
        stats.rejected["not_compilable_or_unchecked"] += 1
        return False
    if metrics.get("em") is not False:
        stats.rejected["exact_match_or_unchecked"] += 1
        return False
    if not test_eval:
        stats.rejected["missing_test_eval"] += 1
        return False
    if test_eval.get("success") is True:
        stats.rejected["tests_passed"] += 1
        return False
    if test_eval.get("build_success") is False:
        stats.rejected["test_build_failed"] += 1
        return False
    tests_failed = int(test_eval.get("tests_failed") or 0)
    failed_names = test_eval.get("failed_test_names") or []
    if tests_failed <= 0 and not failed_names:
        stats.rejected["test_command_failed_without_failed_tests"] += 1
        return False
    return True


def _candidate_hash(raw: dict[str, Any]) -> str:
    payload = {
        "method_id": raw.get("method_id"),
        "generated": raw.get("normalized_generated") or raw.get("generated"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _dedupe_key(raw: dict[str, Any], mode: str, sample_path: Path, dedupe: str) -> str:
    if dedupe == "none":
        return str(sample_path)
    if dedupe == "method_generated":
        return _candidate_hash(raw)
    if dedupe == "generated":
        generated = raw.get("normalized_generated") or raw.get("generated") or ""
        return hashlib.sha256(str(generated).encode("utf-8")).hexdigest()
    raise ValueError(f"Unknown dedupe mode: {dedupe}")


def _build_record(
    raw: dict[str, Any],
    sample_path: Path,
    related: list[RelatedFailure],
    include_prompts: bool,
) -> dict[str, Any]:
    mode = raw.get("mode") or sample_path.parent.parent.name
    candidate_hash = _candidate_hash(raw)
    test_eval = raw.get("test_eval") or {}
    metrics = raw.get("metrics") or {}
    relation_score = max((r.score for r in related), default=0)

    record: dict[str, Any] = {
        "candidate_id": f"{mode}:{sample_path.stem}:{candidate_hash[:12]}",
        "candidate_hash": candidate_hash,
        "source": {
            "sample_path": str(sample_path),
            "mode": mode,
            "sample_index": _sample_index(sample_path),
        },
        "method": {
            "method_id": raw.get("method_id"),
            "file_path": raw.get("file_path"),
            "signature": raw.get("method_signature"),
            "method_name": _method_name_from_sample(raw),
            "class_fqn": _class_name_from_sample(raw),
        },
        "verdict": {
            "compiles": True,
            "exact_match": False,
            "tests_pass": False,
            "build_success": test_eval.get("build_success", True),
            "tests_run": test_eval.get("tests_run", 0),
            "tests_passed": test_eval.get("tests_passed", 0),
            "tests_failed": test_eval.get("tests_failed", 0),
            "failed_test_names": test_eval.get("failed_test_names", []),
            "related_failed_tests": [
                {"name": r.name, "reasons": r.reasons, "score": r.score}
                for r in related
            ],
            "relation_score": relation_score,
        },
        "metrics": {
            "em": metrics.get("em"),
            "es": metrics.get("es"),
            "iou": metrics.get("iou"),
            "lcs_ratio": metrics.get("lcs_ratio"),
            "lcs_no_ident_ratio": metrics.get("lcs_no_ident_ratio"),
            "lcsubstring_no_ident_ratio": metrics.get("lcsubstring_no_ident_ratio"),
            "codebleu": metrics.get("codebleu"),
            "compilable": metrics.get("compilable"),
            "compile_exit_code": metrics.get("compile_exit_code"),
        },
        "code": {
            "ground_truth": raw.get("ground_truth"),
            "generated": raw.get("generated"),
            "normalized_ground_truth": raw.get("normalized_ground_truth"),
            "normalized_generated": raw.get("normalized_generated"),
        },
        "context": {
            "invocations_ordered": raw.get("invocations_ordered", []),
            "invocations_as_used": raw.get("invocations_as_used", []),
            "generated_invocations": raw.get("generated_invocations"),
            "coverage_ratio": raw.get("coverage_ratio"),
            "line_coverage": raw.get("line_coverage"),
            "test_file_paths": raw.get("test_file_paths"),
            "direct_test_methods": raw.get("direct_test_methods"),
        },
        "llm_response": raw.get("llm_response", {}),
    }

    if raw.get("retrieval_results") is not None or raw.get("retrieval_query") is not None:
        record["retrieval"] = {
            "query": raw.get("retrieval_query"),
            "results": raw.get("retrieval_results"),
            "augmentation_block": raw.get("augmentation_block"),
            "recall_at_k": _metric(raw, "recall_at_k"),
            "api_coverage_at_k": _metric(raw, "api_coverage_at_k"),
            "mrr": _metric(raw, "mrr"),
            "retrieval_precision_at_k": _metric(raw, "retrieval_precision_at_k"),
            "retrieval_ndcg_at_k": _metric(raw, "retrieval_ndcg_at_k"),
            "retrieval_type_iou": _metric(raw, "retrieval_type_iou"),
            "owner_type_recall": _metric(raw, "owner_type_recall"),
        }

    if include_prompts and raw.get("prompt") is not None:
        record["prompt"] = raw.get("prompt")

    return record


def mine_failure_dataset(
    results_dir: Path,
    modes: set[str] | None = None,
    require_related_failure: bool = False,
    dedupe: str = "none",
    include_prompts: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sample_files = _discover_sample_files(results_dir, modes)
    stats = MineStats()
    records: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for sample_path in sample_files:
        stats.total_seen += 1
        try:
            raw = _load_json(sample_path)
        except Exception as e:
            stats.rejected["invalid_json"] += 1
            log.warning("Skipping %s: %s", sample_path, e)
            continue

        mode = str(raw.get("mode") or sample_path.parent.parent.name)
        if modes is not None and mode not in modes:
            continue

        if not _is_candidate(raw, stats):
            continue

        related = _related_failures(raw)
        if require_related_failure and not related:
            stats.rejected["unrelated_test_failure"] += 1
            continue

        key = _dedupe_key(raw, mode, sample_path, dedupe)
        if key in seen_keys:
            stats.rejected["duplicate"] += 1
            continue
        seen_keys.add(key)

        record = _build_record(raw, sample_path, related, include_prompts)
        records.append(record)
        stats.candidates += 1
        stats.by_mode[mode] += 1
        if related:
            stats.related_candidates += 1

    records.sort(
        key=lambda r: (
            r["verdict"]["relation_score"],
            len(r["verdict"]["related_failed_tests"]),
            r["verdict"]["tests_failed"] or 0,
            r["context"]["coverage_ratio"] or 0.0,
            r["metrics"]["es"] or 0.0,
        ),
        reverse=True,
    )

    summary = {
        "total_samples_seen": stats.total_seen,
        "candidate_count": stats.candidates,
        "related_candidate_count": stats.related_candidates,
        "by_mode": dict(stats.by_mode),
        "rejected": dict(stats.rejected),
        "criteria": {
            "compilable": True,
            "exact_match": False,
            "test_eval.success": False,
            "test_eval.build_success": True,
            "requires_failed_tests": True,
            "require_related_failure": require_related_failure,
            "dedupe": dedupe,
        },
    }
    return records, summary


def write_records(records: list[dict[str, Any]], output: Path, output_format: str) -> None:
    if output_format == "jsonl":
        text = "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=False) + "\n"
            for record in records
        )
    elif output_format == "json":
        text = json.dumps(records, ensure_ascii=False, indent=2)
    else:
        raise ValueError(f"Unknown output format: {output_format}")
    _atomic_write_text(output, text)


def _infer_format(output: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    return "jsonl" if output.suffix == ".jsonl" else "json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Export generated Java methods that compile, are not Exact Match, "
            "and fail project tests."
        )
    )
    parser.add_argument(
        "results_dir",
        type=Path,
        help="Experiment results directory containing <mode>/samples/sample_*.json",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=Path("results/failure_dataset.jsonl"),
        help="Output dataset path (default: results/failure_dataset.jsonl)",
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=None,
        help="Optional summary JSON path (default: <output>.summary.json)",
    )
    parser.add_argument(
        "--format",
        choices=("json", "jsonl"),
        default=None,
        help="Output format; inferred from extension when omitted",
    )
    parser.add_argument(
        "--mode",
        nargs="+",
        default=None,
        help="Restrict mining to these experiment modes",
    )
    parser.add_argument(
        "--require-related-failure",
        action="store_true",
        help="Keep only candidates with a related failed test heuristic match",
    )
    parser.add_argument(
        "--dedupe",
        choices=("none", "method_generated", "generated"),
        default="none",
        help=(
            "Deduplication strategy: none, same method+generated body, "
            "or same generated body globally"
        ),
    )
    parser.add_argument(
        "--include-prompts",
        action="store_true",
        help="Include full prompts in exported records",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    modes = set(args.mode) if args.mode else None
    records, summary = mine_failure_dataset(
        args.results_dir,
        modes=modes,
        require_related_failure=args.require_related_failure,
        dedupe=args.dedupe,
        include_prompts=args.include_prompts,
    )

    output_format = _infer_format(args.output, args.format)
    write_records(records, args.output, output_format)

    summary_output = args.summary_output
    if summary_output is None:
        summary_output = args.output.with_suffix(args.output.suffix + ".summary.json")
    _atomic_write_text(summary_output, json.dumps(summary, ensure_ascii=False, indent=2))

    log.info(
        "Exported %d candidates (%d related) to %s",
        summary["candidate_count"],
        summary["related_candidate_count"],
        args.output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
