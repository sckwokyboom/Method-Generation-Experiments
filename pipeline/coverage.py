"""JaCoCo coverage analysis: run tests with coverage, parse reports, map to extracted methods."""
from __future__ import annotations

import logging
import platform
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


# ── JVM descriptor → FQN conversion ──────────────────────────────────────────

_PRIMITIVE_MAP = {
    "Z": "boolean",
    "B": "byte",
    "C": "char",
    "S": "short",
    "I": "int",
    "J": "long",
    "F": "float",
    "D": "double",
    "V": "void",
}


def _parse_jvm_type(desc: str, pos: int) -> tuple[str, int]:
    """Parse one JVM type from *desc* starting at *pos*. Return (fqn, next_pos)."""
    ch = desc[pos]
    if ch in _PRIMITIVE_MAP:
        return _PRIMITIVE_MAP[ch], pos + 1
    if ch == "L":
        end = desc.index(";", pos)
        return desc[pos + 1 : end].replace("/", "."), end + 1
    if ch == "[":
        inner, next_pos = _parse_jvm_type(desc, pos + 1)
        return inner + "[]", next_pos
    raise ValueError(f"Unknown JVM type descriptor char '{ch}' at pos {pos} in '{desc}'")


def jvm_descriptor_to_param_types(descriptor: str) -> list[str]:
    """Convert a JVM method descriptor like ``(Ljava/lang/String;I)Z`` to a list of FQN param types."""
    if not descriptor.startswith("("):
        raise ValueError(f"Invalid method descriptor: {descriptor}")
    params_end = descriptor.index(")")
    params_str = descriptor[1:params_end]
    result: list[str] = []
    pos = 0
    while pos < len(params_str):
        t, pos = _parse_jvm_type(params_str, pos)
        result.append(t)
    return result


def jvm_descriptor_return_type(descriptor: str) -> str:
    """Extract return type FQN from a JVM method descriptor."""
    ret_start = descriptor.index(")") + 1
    t, _ = _parse_jvm_type(descriptor, ret_start)
    return t


# ── Coverage data model ──────────────────────────────────────────────────────

@dataclass
class LineCoverage:
    line_number: int
    covered_instructions: int
    missed_instructions: int

    @property
    def is_covered(self) -> bool:
        return self.covered_instructions > 0


@dataclass
class MethodCoverage:
    method_name: str
    descriptor: str
    param_types: list[str]
    return_type: str
    lines_covered: int
    lines_missed: int

    @property
    def is_covered(self) -> bool:
        return self.lines_covered > 0

    @property
    def coverage_ratio(self) -> float:
        total = self.lines_covered + self.lines_missed
        return self.lines_covered / total if total > 0 else 0.0


@dataclass
class CoverageMap:
    """Maps class_fqn → list of covered methods."""
    _data: dict[str, list[MethodCoverage]] = field(default_factory=dict)
    # Keyed by "package.name/SourceFile.java" → {line_number → LineCoverage}
    _source_lines: dict[str, dict[int, LineCoverage]] = field(default_factory=dict)

    def add(self, class_fqn: str, method: MethodCoverage) -> None:
        self._data.setdefault(class_fqn, []).append(method)

    def is_method_covered(
        self,
        class_fqn: str,
        method_name: str,
        param_types: list[str] | None = None,
    ) -> bool:
        """Check if a method is covered by tests.

        Matches by class_fqn and method_name. If *param_types* is provided,
        also matches parameter types (stripping generics for comparison).
        """
        methods = self._data.get(class_fqn, [])
        for mc in methods:
            if mc.method_name != method_name:
                continue
            if not mc.is_covered:
                continue
            if param_types is not None:
                if not _types_match(mc.param_types, param_types):
                    continue
            return True
        return False

    def get_method_coverage_ratio(
        self,
        class_fqn: str,
        method_name: str,
        param_types: list[str] | None = None,
    ) -> float | None:
        """Return the line coverage ratio for a method, or None if not found."""
        methods = self._data.get(class_fqn, [])
        for mc in methods:
            if mc.method_name != method_name:
                continue
            if param_types is not None:
                if not _types_match(mc.param_types, param_types):
                    continue
            return mc.coverage_ratio
        return None

    def covered_count(self) -> int:
        return sum(
            1 for methods in self._data.values()
            for m in methods if m.is_covered
        )

    def total_count(self) -> int:
        return sum(len(methods) for methods in self._data.values())

    def add_source_line(self, source_key: str, line: LineCoverage) -> None:
        self._source_lines.setdefault(source_key, {})[line.line_number] = line

    def get_line_coverage(
        self,
        class_fqn: str,
        start_line: int,
        end_line: int,
    ) -> list[LineCoverage] | None:
        """Return per-line coverage for a class within [start_line, end_line].

        Maps *class_fqn* (e.g. ``com.example.Foo``) to the JaCoCo source-file
        key ``com/example/Foo.java`` and looks up cached line data.
        """
        # class_fqn → source key: replace dots with /, append .java
        # Inner classes: com.example.Outer.Inner → com/example/Outer.java
        parts = class_fqn.split(".")
        # Find boundary between package and class: first segment starting with uppercase
        pkg_parts: list[str] = []
        cls_parts: list[str] = []
        found_class = False
        for p in parts:
            if not found_class and p and p[0].isupper():
                found_class = True
            if found_class:
                cls_parts.append(p)
            else:
                pkg_parts.append(p)
        if not cls_parts:
            # Fallback: treat last segment as class name
            cls_parts = [parts[-1]]
            pkg_parts = parts[:-1]

        source_file = cls_parts[0] + ".java"
        source_key = "/".join(pkg_parts) + "/" + source_file if pkg_parts else source_file

        lines_map = self._source_lines.get(source_key)
        if lines_map is None:
            return None

        result = []
        for ln in range(start_line, end_line + 1):
            if ln in lines_map:
                result.append(lines_map[ln])
        return result if result else None

    def __repr__(self) -> str:
        return f"CoverageMap(covered={self.covered_count()}, total={self.total_count()})"


def _strip_generics(type_fqn: str) -> str:
    """Strip generic parameters: ``java.util.List<java.lang.String>`` → ``java.util.List``."""
    idx = type_fqn.find("<")
    return type_fqn[:idx] if idx >= 0 else type_fqn


def _types_match(jacoco_types: list[str], extractor_types: list[str]) -> bool:
    """Compare JaCoCo param types with extractor param types, ignoring generics."""
    if len(jacoco_types) != len(extractor_types):
        return False
    for jt, et in zip(jacoco_types, extractor_types):
        if _strip_generics(jt) != _strip_generics(et):
            return False
    return True


# ── JaCoCo XML parsing ───────────────────────────────────────────────────────

def parse_jacoco_xml(xml_path: str | Path) -> CoverageMap:
    """Parse a JaCoCo XML report into a CoverageMap."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    coverage = CoverageMap()

    for pkg_elem in root.iter("package"):
        pkg_name = pkg_elem.get("name", "").replace("/", ".")
        for cls_elem in pkg_elem.iter("class"):
            cls_name = cls_elem.get("name", "").replace("/", ".")
            for method_elem in cls_elem.iter("method"):
                m_name = method_elem.get("name", "")
                m_desc = method_elem.get("desc", "")

                lines_covered = 0
                lines_missed = 0
                for counter in method_elem.iter("counter"):
                    if counter.get("type") == "LINE":
                        lines_covered = int(counter.get("covered", "0"))
                        lines_missed = int(counter.get("missed", "0"))

                try:
                    param_types = jvm_descriptor_to_param_types(m_desc)
                    ret_type = jvm_descriptor_return_type(m_desc)
                except (ValueError, IndexError):
                    param_types = []
                    ret_type = "void"

                coverage.add(
                    cls_name,
                    MethodCoverage(
                        method_name=m_name,
                        descriptor=m_desc,
                        param_types=param_types,
                        return_type=ret_type,
                        lines_covered=lines_covered,
                        lines_missed=lines_missed,
                    ),
                )

    # Parse per-line coverage from <sourcefile> elements
    for pkg_elem in root.iter("package"):
        pkg_name = pkg_elem.get("name", "")
        for sf_elem in pkg_elem.iter("sourcefile"):
            sf_name = sf_elem.get("name", "")
            source_key = pkg_name + "/" + sf_name if pkg_name else sf_name
            for line_elem in sf_elem.iter("line"):
                nr = int(line_elem.get("nr", "0"))
                ci = int(line_elem.get("ci", "0"))
                mi = int(line_elem.get("mi", "0"))
                coverage.add_source_line(
                    source_key,
                    LineCoverage(line_number=nr, covered_instructions=ci, missed_instructions=mi),
                )

    log.info("Parsed JaCoCo report: %s", coverage)
    return coverage


# ── Run tests with JaCoCo ────────────────────────────────────────────────────

def run_jacoco_maven(
    project_path: str | Path,
    timeout_seconds: int = 600,
) -> Path:
    """Run Maven tests with JaCoCo and return the path to the XML report."""
    project_path = Path(project_path)
    report_path = project_path / "target" / "site" / "jacoco" / "jacoco.xml"

    cmd = [
        "mvn",
        "-f", str(project_path / "pom.xml"),
        "org.jacoco:jacoco-maven-plugin:prepare-agent",
        "test",
        "org.jacoco:jacoco-maven-plugin:report",
        "-Djacoco.skip=false",
        "-Dmaven.test.failure.ignore=true",
        "-q",
    ]

    log.info("Running Maven + JaCoCo: %s", " ".join(cmd))
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout_seconds, cwd=str(project_path),
    )

    if result.returncode != 0:
        log.warning("Maven + JaCoCo exited with code %d (test failures are ignored)", result.returncode)

    if not report_path.exists():
        raise FileNotFoundError(
            f"JaCoCo report not found at {report_path}. "
            f"Maven stderr: {result.stderr[-500:]}"
        )

    log.info("JaCoCo report generated at %s", report_path)
    return report_path


def run_jacoco_gradle(
    project_path: str | Path,
    timeout_seconds: int = 600,
) -> Path:
    """Run Gradle tests with JaCoCo and return the path to the XML report."""
    project_path = Path(project_path).resolve()

    # Create a temporary init script to inject JaCoCo
    init_script = project_path / "jacoco-init.gradle"
    init_script.write_text("""\
allprojects {
    pluginManager.withPlugin('java') {
        apply plugin: 'jacoco'
        tasks.withType(Test) {
            jacoco { enabled = true }
        }
        tasks.named('jacocoTestReport') {
            reports {
                xml.required = true
                html.required = false
            }
        }
    }
}
""")

    try:
        if platform.system() == "Windows":
            wrapper = project_path / "gradlew.bat"
        else:
            wrapper = project_path / "gradlew"

        if not project_path.exists():
            raise FileNotFoundError(f"Project path does not exist: {project_path}")
        if not init_script.exists():
            raise FileNotFoundError(f"Init script was not created: {init_script}")

        gradle_cmd = str(wrapper) if wrapper.exists() else "gradle"
        log.info("Gradle wrapper: %s (exists=%s)", wrapper, wrapper.exists())
        log.info("Init script: %s (exists=%s)", init_script, init_script.exists())

        cmd = [
            gradle_cmd,
            "--init-script", str(init_script),
            "test", "jacocoTestReport",
            "--continue", "--no-configuration-cache", "--stacktrace",
        ]
        log.info("Running Gradle + JaCoCo: %s", " ".join(cmd))
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout_seconds, cwd=str(project_path),
        )
        if result.returncode != 0:
            log.warning("Gradle exited with code %d", result.returncode)
            if result.stdout:
                log.warning("Gradle stdout:\n%s", result.stdout[-3000:])
            if result.stderr:
                log.warning("Gradle stderr:\n%s", result.stderr[-3000:])
    finally:
        init_script.unlink(missing_ok=True)

    # Search for generated JaCoCo reports (multi-module projects put them in subproject dirs)
    # jacocoTestReport puts XML at build/reports/jacoco/test/jacocoTestReport.xml
    report_candidates = list(project_path.rglob("jacocoTestReport.xml"))
    if not report_candidates:
        # Fallback: look for any JaCoCo XML report
        report_candidates = [p for p in project_path.rglob("jacoco*.xml") if p.stat().st_size > 1000]
    if not report_candidates:
        raise FileNotFoundError(
            f"No JaCoCo report found under {project_path}. "
            "Check that the project compiles and has tests."
        )
    report_path = max(report_candidates, key=lambda p: p.stat().st_size)
    log.info("Using JaCoCo report: %s", report_path)
    return report_path


def _find_jacoco_reports(project_path: Path) -> list[Path]:
    """Search for existing JaCoCo XML reports in standard locations."""
    candidates = list(project_path.rglob("jacoco*.xml"))
    # Filter to actual JaCoCo reports (not config files)
    return [p for p in candidates if p.stat().st_size > 1000]


def build_coverage_map(
    project_path: str | Path,
    build_system: str = "maven",
    timeout_seconds: int = 600,
    cached_report: str | Path | None = None,
) -> CoverageMap:
    """Build a coverage map for a project. Uses cached report if available."""
    if cached_report and Path(cached_report).exists():
        log.info("Using cached JaCoCo report: %s", cached_report)
        return parse_jacoco_xml(cached_report)

    # Check for existing reports before running tests
    existing = _find_jacoco_reports(Path(project_path))
    if existing:
        log.info("Found %d existing JaCoCo report(s), using largest: %s", len(existing), existing)
        largest = max(existing, key=lambda p: p.stat().st_size)
        return parse_jacoco_xml(largest)

    if build_system == "maven":
        report = run_jacoco_maven(project_path, timeout_seconds)
    elif build_system == "gradle":
        report = run_jacoco_gradle(project_path, timeout_seconds)
    else:
        raise ValueError(f"Unknown build system: {build_system}")

    return parse_jacoco_xml(report)
