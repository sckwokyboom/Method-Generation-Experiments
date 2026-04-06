"""Enrich existing sample results with coverage data, test file references,
and AST-extracted invocations from generated code.

Usage:
    python -m pipeline.enrich_samples \
        --results-dir ./results \
        --extraction ./results/extracted_methods.json \
        --jacoco-report ./results/junit5-jacoco.xml \
        --project-path ./target-project/junit5 \
        --extractor-jar ./extractor/extractor-core/build/libs/method-extractor-0.2.0.jar
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import tempfile
from pathlib import Path

from pipeline.coverage import CoverageMap, parse_jacoco_xml
from pipeline.dataset import load_extraction
from pipeline.models import ExtractionData, ExtractedMethod, SampleResult
from pipeline.report import load_sample_result, write_sample_result

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def _offset_to_line(content: str, offset: int) -> int:
    return content[:offset].count("\n") + 1


def _find_test_files(method_name: str, project_path: Path) -> list[str] | None:
    test_dirs = list(project_path.rglob("src/test"))
    if not test_dirs:
        return None
    found: list[str] = []
    for test_dir in test_dirs:
        for java_file in test_dir.rglob("*.java"):
            try:
                text = java_file.read_text(encoding="utf-8", errors="ignore")
                if method_name in text:
                    found.append(str(java_file.relative_to(project_path)))
            except OSError:
                continue
    return found if found else None


# ── tree-sitter fallback for unparseable code ────────────────────────────────

def _treesitter_extract_invocations(code: str) -> list[dict] | None:
    """Extract method call names from Java code using tree-sitter.

    Returns a list of invocation dicts with 'TREESITTER' resolution mode,
    or None if tree-sitter is not available.
    """
    try:
        import tree_sitter_java as tsjava
        from tree_sitter import Language, Parser
    except ImportError:
        return None

    JAVA_LANGUAGE = Language(tsjava.language())
    parser = Parser(JAVA_LANGUAGE)

    # Wrap the body in a minimal class/method so tree-sitter can parse it
    wrapped = f"class _Wrapper {{ void _method() {{ {code} }} }}"
    tree = parser.parse(wrapped.encode("utf-8"))

    invocations: list[dict] = []
    order = [0]

    def _visit(node):
        if node.type == "method_invocation":
            # The method name is the child named "name"
            name_node = node.child_by_field_name("name")
            if name_node:
                name = name_node.text.decode("utf-8")
                # Try to get the object for a richer signature
                obj_node = node.child_by_field_name("object")
                obj_text = obj_node.text.decode("utf-8") if obj_node else None
                sig = f"{obj_text}.{name}" if obj_text else name
                invocations.append({
                    "signature": sig,
                    "resolutionMode": "TREESITTER",
                    "orderIndex": order[0],
                })
                order[0] += 1
        elif node.type == "object_creation_expression":
            type_node = node.child_by_field_name("type")
            if type_node:
                type_name = type_node.text.decode("utf-8")
                invocations.append({
                    "signature": f"<init>({type_name})",
                    "resolutionMode": "TREESITTER",
                    "orderIndex": order[0],
                })
                order[0] += 1

        for child in node.children:
            _visit(child)

    _visit(tree.root_node)
    return invocations


# ── tree-sitter: direct test method invocation index ────────────────────────

_TEST_ANNOTATIONS = frozenset({"Test", "ParameterizedTest", "RepeatedTest"})


def _has_test_annotation(method_node) -> bool:
    """Check if a method_declaration node has a JUnit test annotation."""
    for child in method_node.children:
        if child.type == "modifiers":
            for mod in child.children:
                if mod.type in ("marker_annotation", "annotation"):
                    name_node = mod.child_by_field_name("name")
                    if name_node and name_node.text.decode("utf-8") in _TEST_ANNOTATIONS:
                        return True
    return False


def _enclosing_class_name(node) -> str:
    """Walk up the tree to find the enclosing class name."""
    cur = node.parent
    while cur is not None:
        if cur.type in ("class_declaration", "enum_declaration", "interface_declaration"):
            name_node = cur.child_by_field_name("name")
            if name_node:
                return name_node.text.decode("utf-8")
        cur = cur.parent
    return "<unknown>"


def _collect_invoked_names(body_node) -> set[str]:
    """Collect all method names invoked directly in a subtree."""
    names: set[str] = set()
    stack = [body_node]
    while stack:
        node = stack.pop()
        if node.type == "method_invocation":
            name_node = node.child_by_field_name("name")
            if name_node:
                names.add(name_node.text.decode("utf-8"))
        for child in node.children:
            stack.append(child)
    return names


def _build_test_invocation_index(project_path: Path) -> dict[str, list[dict]] | None:
    """Parse all test files with tree-sitter and build an index:
    method_name -> [{test_file, test_class, test_method}, ...]
    """
    try:
        import tree_sitter_java as tsjava
        from tree_sitter import Language, Parser
    except ImportError:
        log.warning("tree-sitter not available; skipping direct test method detection")
        return None

    parser = Parser(Language(tsjava.language()))

    test_dirs = list(project_path.rglob("src/test"))
    if not test_dirs:
        return {}

    index: dict[str, list[dict]] = {}

    for test_dir in test_dirs:
        for java_file in test_dir.rglob("*.java"):
            try:
                text = java_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            tree = parser.parse(text.encode("utf-8"))

            # Walk top-level to find method declarations inside classes
            stack = [tree.root_node]
            while stack:
                node = stack.pop()
                if node.type == "method_declaration" and _has_test_annotation(node):
                    method_name_node = node.child_by_field_name("name")
                    if not method_name_node:
                        continue
                    test_method_name = method_name_node.text.decode("utf-8")

                    body_node = node.child_by_field_name("body")
                    if not body_node:
                        continue

                    invoked = _collect_invoked_names(body_node)
                    if not invoked:
                        continue

                    test_class = _enclosing_class_name(node)
                    rel_path = str(java_file.relative_to(project_path))

                    entry = {
                        "test_file": rel_path,
                        "test_class": test_class,
                        "test_method": test_method_name,
                    }
                    for name in invoked:
                        index.setdefault(name, []).append(entry)

                for child in node.children:
                    stack.append(child)

    log.info("Test invocation index: %d unique method names from test files", len(index))
    return index


# ── JDT batch extraction via extractor JAR ───────────────────────────────────

def _discover_source_roots(project_path: Path) -> list[str]:
    """Find source root directories in a project."""
    roots = []
    for src_dir in project_path.rglob("src/main/java"):
        if src_dir.is_dir():
            roots.append(str(src_dir))
    if not roots:
        roots.append(str(project_path))
    return roots


def _extract_invocations_batch_jdt(
    samples_to_extract: list[tuple[str, SampleResult, ExtractedMethod]],
    classpath: list[str],
    source_roots: list[str],
    project_path: Path,
    extractor_jar: Path,
) -> dict[str, list[dict]]:
    """Call the extractor JAR to batch-extract invocations via JDT.

    Returns: dict mapping (mode + sample_path) key → list of invocation dicts.
    """
    if not samples_to_extract:
        return {}

    # Build input JSON
    batch_samples = []
    key_to_index: dict[int, str] = {}
    for i, (key, sample, method) in enumerate(samples_to_extract):
        batch_samples.append({
            "index": i,
            "fileContent": method.file_content,
            "bodyStartOffset": method.body_start_offset,
            "bodyEndOffset": method.body_end_offset,
            "generatedBody": sample.generated,
            "filePath": method.file_path,
        })
        key_to_index[i] = key

    input_data = {
        "classpath": classpath,
        "sourceRoots": source_roots,
        "samples": batch_samples,
    }

    result_map: dict[str, list[dict]] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / "input.json"
        output_path = Path(tmpdir) / "output.json"

        input_path.write_text(json.dumps(input_data, ensure_ascii=False), encoding="utf-8")

        cmd = [
            "java", "-jar", str(extractor_jar),
            "extract-invocations",
            "--project-path", str(project_path),
            "--input", str(input_path),
            "--output", str(output_path),
        ]

        log.info("Running extractor JAR for %d samples...", len(batch_samples))
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=600,
        )

        if proc.returncode != 0:
            log.error("Extractor JAR failed (exit %d)", proc.returncode)
            if proc.stderr:
                log.error("stderr: %s", proc.stderr[-2000:])
            return result_map

        if not output_path.exists():
            log.error("Extractor JAR did not produce output file")
            return result_map

        results = json.loads(output_path.read_text(encoding="utf-8"))
        for entry in results:
            idx = entry["index"]
            key = key_to_index.get(idx)
            if key:
                result_map[key] = entry.get("invocations", [])

    return result_map


# ── Main enrichment logic ────────────────────────────────────────────────────

def _build_method_index(extraction: ExtractionData) -> dict[str, ExtractedMethod]:
    index: dict[str, ExtractedMethod] = {}
    for m in extraction.methods:
        method_id = f"{m.class_fqn}#{m.method_name}"
        index[method_id] = m
    return index


def enrich_results(
    results_dir: Path,
    extraction_path: Path,
    jacoco_report: Path | None = None,
    project_path: Path | None = None,
    extractor_jar: Path | None = None,
) -> None:
    # Load extraction data
    log.info("Loading extraction data: %s", extraction_path)
    extraction = load_extraction(extraction_path)
    method_index = _build_method_index(extraction)
    log.info("Method index: %d methods", len(method_index))

    # Load coverage map
    coverage_map: CoverageMap | None = None
    if jacoco_report and jacoco_report.exists():
        log.info("Parsing JaCoCo report: %s", jacoco_report)
        coverage_map = parse_jacoco_xml(jacoco_report)
        log.info("Coverage map: %s", coverage_map)

    # Discover source roots
    source_roots: list[str] = []
    if project_path:
        source_roots = _discover_source_roots(project_path)

    # Discover mode directories
    mode_dirs: list[tuple[str, Path]] = []
    for entry in sorted(results_dir.iterdir()):
        samples_dir = entry / "samples"
        if entry.is_dir() and samples_dir.is_dir():
            mode_dirs.append((entry.name, samples_dir))

    if not mode_dirs:
        log.warning("No mode directories with samples/ found in %s", results_dir)
        return

    # First pass: load all samples and collect those needing invocation extraction
    all_samples: list[tuple[str, Path, SampleResult, ExtractedMethod | None]] = []
    samples_for_jdt: list[tuple[str, SampleResult, ExtractedMethod]] = []

    for mode, samples_dir in mode_dirs:
        for sample_path in sorted(samples_dir.glob("sample_*.json")):
            try:
                sample = load_sample_result(sample_path)
            except Exception as e:
                log.warning("Failed to load %s: %s", sample_path, e)
                continue

            method = method_index.get(sample.method_id)
            key = f"{mode}:{sample_path}"
            all_samples.append((key, sample_path, sample, method))

            if (method is not None
                    and sample.generated_invocations is None
                    and sample.generated):
                samples_for_jdt.append((key, sample, method))

    # Batch JDT extraction
    jdt_results: dict[str, list[dict]] = {}
    if extractor_jar and extractor_jar.exists() and samples_for_jdt:
        jdt_results = _extract_invocations_batch_jdt(
            samples_for_jdt,
            extraction.classpath,
            source_roots,
            project_path or Path("."),
            extractor_jar,
        )
        log.info("JDT extracted invocations for %d / %d samples", len(jdt_results), len(samples_for_jdt))

    # Build test invocation index (parse test files once)
    test_invocation_index: dict[str, list[dict]] | None = None
    if project_path:
        test_invocation_index = _build_test_invocation_index(project_path)

    # Second pass: enrich each sample
    enriched = 0
    skipped = 0

    for key, sample_path, sample, method in all_samples:
        changed = False

        # Coverage
        if method and coverage_map is not None and sample.coverage_ratio is None:
            ratio = coverage_map.get_method_coverage_ratio(
                method.class_fqn, method.method_name, method.parameter_types,
            )
            if ratio is not None:
                sample.coverage_ratio = ratio
                changed = True

            start_line = _offset_to_line(method.file_content, method.body_start_offset)
            end_line = _offset_to_line(method.file_content, method.body_end_offset)
            lc = coverage_map.get_line_coverage(method.class_fqn, start_line, end_line)
            if lc is not None and sample.line_coverage is None:
                gt_lines = method.file_content.splitlines()
                sample.line_coverage = []
                for lc_entry in lc:
                    src = gt_lines[lc_entry.line_number - 1] if lc_entry.line_number <= len(gt_lines) else ""
                    sample.line_coverage.append({
                        "line": lc_entry.line_number,
                        "covered": lc_entry.is_covered,
                        "source": src,
                    })
                changed = True

        # Test file references
        if method and project_path and sample.test_file_paths is None:
            test_files = _find_test_files(method.method_name, project_path)
            if test_files:
                sample.test_file_paths = test_files
                changed = True

        # Direct test method invocations
        if method and test_invocation_index is not None and sample.direct_test_methods is None:
            direct = test_invocation_index.get(method.method_name)
            if direct:
                sample.direct_test_methods = direct
                changed = True

        # Generated invocations
        if sample.generated_invocations is None and sample.generated:
            invocations = jdt_results.get(key)
            if invocations is not None:
                sample.generated_invocations = invocations
                changed = True
            else:
                # tree-sitter fallback
                ts_invocations = _treesitter_extract_invocations(sample.generated)
                if ts_invocations is not None:
                    sample.generated_invocations = ts_invocations
                    changed = True

        if changed:
            write_sample_result(sample, sample_path)
            enriched += 1
        else:
            skipped += 1

    log.info("Done. Enriched %d samples, skipped %d.", enriched, skipped)


def main():
    parser = argparse.ArgumentParser(
        description="Enrich existing sample results with coverage, test references, and generated invocations"
    )
    parser.add_argument("--results-dir", required=True,
                        help="Path to results directory containing mode subdirectories")
    parser.add_argument("--extraction", required=True,
                        help="Path to extracted_methods.json")
    parser.add_argument("--jacoco-report", default=None,
                        help="Path to JaCoCo XML report (optional)")
    parser.add_argument("--project-path", default=None,
                        help="Path to target project (for test file scanning and source roots)")
    parser.add_argument("--extractor-jar", default=None,
                        help="Path to method-extractor JAR (for JDT invocation extraction)")
    args = parser.parse_args()

    enrich_results(
        results_dir=Path(args.results_dir),
        extraction_path=Path(args.extraction),
        jacoco_report=Path(args.jacoco_report) if args.jacoco_report else None,
        project_path=Path(args.project_path) if args.project_path else None,
        extractor_jar=Path(args.extractor_jar) if args.extractor_jar else None,
    )


if __name__ == "__main__":
    main()
