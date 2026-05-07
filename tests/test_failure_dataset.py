from __future__ import annotations

import json
from pathlib import Path

from pipeline.failure_dataset import mine_failure_dataset


def _write_sample(
    root: Path,
    mode: str,
    idx: int,
    *,
    compilable: bool = True,
    em: bool = False,
    test_success: bool = False,
    build_success: bool = True,
    tests_failed: int = 1,
    failed_test_names: list[str] | None = None,
    extra: dict | None = None,
) -> Path:
    sample_dir = root / mode / "samples"
    sample_dir.mkdir(parents=True, exist_ok=True)
    raw = {
        "method_id": "org.example.Foo#compute",
        "file_path": "src/main/java/org/example/Foo.java",
        "mode": mode,
        "method_signature": "public int compute(int x)",
        "ground_truth": "return x + 1;",
        "generated": "return x - 1;",
        "normalized_ground_truth": "return x + 1 ;",
        "normalized_generated": "return x - 1 ;",
        "metrics": {
            "em": em,
            "es": 0.8,
            "iou": 0.5,
            "lcs_length": 3,
            "lcs_ratio": 0.6,
            "compilable": compilable,
            "compile_errors": [],
            "compile_exit_code": 0 if compilable else 1,
        },
        "test_eval": {
            "success": test_success,
            "tests_run": 10,
            "tests_passed": 9 if tests_failed else 10,
            "tests_failed": tests_failed,
            "failed_test_names": failed_test_names or ["org.example.FooTests.shouldCompute"],
            "build_success": build_success,
            "error_messages": [],
            "duration_ms": 123.0,
        },
        "invocations_ordered": [],
        "invocations_as_used": [],
        "llm_response": {},
    }
    if extra:
        raw.update(extra)
    path = sample_dir / f"sample_{idx:03d}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_mine_failure_dataset_keeps_compilable_non_em_test_failures(tmp_path):
    _write_sample(
        tmp_path,
        "no_augmentation",
        0,
        extra={
            "test_file_paths": ["src/test/java/org/example/FooTests.java"],
            "direct_test_methods": [
                {
                    "test_file": "src/test/java/org/example/FooTests.java",
                    "test_class": "FooTests",
                    "test_method": "shouldCompute",
                }
            ],
        },
    )

    records, summary = mine_failure_dataset(tmp_path)

    assert summary["candidate_count"] == 1
    assert summary["related_candidate_count"] == 1
    assert records[0]["method"]["method_id"] == "org.example.Foo#compute"
    assert records[0]["verdict"]["compiles"] is True
    assert records[0]["verdict"]["exact_match"] is False
    assert records[0]["verdict"]["tests_pass"] is False
    assert records[0]["verdict"]["related_failed_tests"][0]["reasons"] == [
        "direct_test_method",
        "referenced_test_file",
        "target_class_test_class_match",
        "method_name_overlap",
    ]


def test_mine_failure_dataset_rejects_wrong_verdicts(tmp_path):
    _write_sample(tmp_path, "mode", 0, compilable=False)
    _write_sample(tmp_path, "mode", 1, em=True)
    _write_sample(tmp_path, "mode", 2, test_success=True, tests_failed=0, failed_test_names=[])
    _write_sample(tmp_path, "mode", 3, build_success=False)

    records, summary = mine_failure_dataset(tmp_path)

    assert records == []
    assert summary["candidate_count"] == 0
    assert summary["rejected"] == {
        "not_compilable_or_unchecked": 1,
        "exact_match_or_unchecked": 1,
        "tests_passed": 1,
        "test_build_failed": 1,
    }


def test_mine_failure_dataset_can_require_related_failures(tmp_path):
    _write_sample(tmp_path, "mode", 0, failed_test_names=["org.example.OtherTests.unrelated"])
    _write_sample(
        tmp_path,
        "mode",
        1,
        failed_test_names=["org.example.FooTests.shouldCompute"],
        extra={"test_file_paths": ["src/test/java/org/example/FooTests.java"]},
    )

    records, summary = mine_failure_dataset(tmp_path, require_related_failure=True)

    assert len(records) == 1
    assert records[0]["source"]["sample_index"] == 1
    assert summary["candidate_count"] == 1
    assert summary["rejected"]["unrelated_test_failure"] == 1


def test_mine_failure_dataset_dedupes_same_method_and_generation(tmp_path):
    _write_sample(tmp_path, "no_augmentation", 0)
    _write_sample(tmp_path, "retrieval_augmentation", 0)

    records, summary = mine_failure_dataset(tmp_path, dedupe="method_generated")

    assert len(records) == 1
    assert summary["candidate_count"] == 1
    assert summary["rejected"]["duplicate"] == 1


def test_mine_failure_dataset_dedupe_none_keeps_same_generation_rows(tmp_path):
    _write_sample(tmp_path, "mode", 0)
    _write_sample(tmp_path, "mode", 1)

    records, summary = mine_failure_dataset(tmp_path, dedupe="none")

    assert len(records) == 2
    assert summary["candidate_count"] == 2
    assert "duplicate" not in summary["rejected"]
