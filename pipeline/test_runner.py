"""Test runner for pass@k evaluation: replace method body, run tests, restore."""
from __future__ import annotations

import logging
import os
import platform
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.models import ExtractedMethod

log = logging.getLogger(__name__)


@dataclass
class TestResult:
    success: bool
    tests_run: int
    tests_passed: int
    tests_failed: int
    tests_errored: int
    failed_test_names: list[str]
    build_success: bool
    error_messages: list[str]
    duration_ms: float


def _replace_method_body(method: ExtractedMethod, generated_body: str, project_path: Path) -> Path:
    """Replace method body in the source file and return the file path."""
    file_content = method.file_content
    body_start = method.body_start_offset
    body_end = method.body_end_offset

    modified = file_content[:body_start + 1] + generated_body + file_content[body_end:]

    # Resolve absolute path from project root + relative file_path
    source_file = project_path / method.file_path
    if not source_file.exists():
        # Try finding via src/main/java pattern
        parts = method.file_path.replace("\\", "/").split("/")
        try:
            src_idx = parts.index("src")
            rel = "/".join(parts[src_idx:])
            source_file = project_path / rel
        except ValueError:
            pass

    source_file.write_text(modified, encoding="utf-8")
    return source_file


def _restore_file(method: ExtractedMethod, project_path: Path) -> None:
    """Restore the original file content."""
    source_file = project_path / method.file_path
    if not source_file.exists():
        parts = method.file_path.replace("\\", "/").split("/")
        try:
            src_idx = parts.index("src")
            rel = "/".join(parts[src_idx:])
            source_file = project_path / rel
        except ValueError:
            pass
    source_file.write_text(method.file_content, encoding="utf-8")


def _clean_test_reports(project_path: Path) -> None:
    """Delete stale test XML reports so we only parse fresh results."""
    for xml_file in project_path.rglob("build/test-results/test/TEST-*.xml"):
        xml_file.unlink()
    for xml_file in project_path.rglob("target/surefire-reports/TEST-*.xml"):
        xml_file.unlink()


def _parse_surefire_reports(project_path: Path) -> tuple[int, int, int, list[str]]:
    """Parse Surefire/JUnit XML reports. Returns (run, passed, failed, failed_names).

    Searches recursively to support multi-module Gradle/Maven projects where
    each submodule produces its own test report directory.
    """
    # Recursive search: finds reports in both root and submodule directories
    xml_files: list[Path] = []
    xml_files.extend(project_path.rglob("build/test-results/test/TEST-*.xml"))
    xml_files.extend(project_path.rglob("target/surefire-reports/TEST-*.xml"))

    total_run = 0
    total_failed = 0
    total_errored = 0
    failed_names: list[str] = []

    for xml_file in xml_files:
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
            tests = int(root.get("tests", "0"))
            failures = int(root.get("failures", "0"))
            errors = int(root.get("errors", "0"))
            total_run += tests
            total_failed += failures
            total_errored += errors

            for testcase in root.iter("testcase"):
                if testcase.find("failure") is not None or testcase.find("error") is not None:
                    name = f"{testcase.get('classname', '')}.{testcase.get('name', '')}"
                    failed_names.append(name)
        except ET.ParseError:
            log.warning("Failed to parse test report: %s", xml_file)

    log.debug("Found %d test XML reports, tests_run=%d", len(xml_files), total_run)
    total_passed = total_run - total_failed - total_errored
    return total_run, total_passed, total_failed + total_errored, failed_names


def run_test_evaluation(
    method: ExtractedMethod,
    generated_body: str,
    project_path: str | Path,
    build_system: str = "maven",
    timeout_seconds: int = 300,
    test_command: str | None = None,
) -> TestResult:
    """Replace method body, run tests, restore, and return results."""
    project_path = Path(project_path)
    start = time.monotonic()

    try:
        _replace_method_body(method, generated_body, project_path)

        is_windows = platform.system() == "Windows"

        if test_command:
            if is_windows:
                cmd = test_command.split()
                # Replace Unix-style ./gradlew with gradlew.bat on Windows
                if cmd and cmd[0] in ("./gradlew", "gradlew"):
                    gradlew_bat = project_path / "gradlew.bat"
                    cmd[0] = str(gradlew_bat) if gradlew_bat.exists() else "gradle"
            else:
                cmd = shlex.split(test_command)
        elif build_system == "maven":
            cmd = ["mvn", "test", "-f", str(project_path / "pom.xml"), "-q"]
        elif build_system == "gradle":
            if is_windows:
                gradlew = project_path / "gradlew.bat"
            else:
                gradlew = project_path / "gradlew"
            gradle_cmd = str(gradlew) if gradlew.exists() else "gradle"
            cmd = [gradle_cmd, "test", "-q", "--no-daemon"]
        else:
            raise ValueError(f"Unknown build system: {build_system}")

        # Ensure gradlew is executable on Unix
        if not is_windows and cmd and cmd[0].endswith("gradlew"):
            gradlew_path = Path(cmd[0]) if os.path.isabs(cmd[0]) else project_path / cmd[0]
            if gradlew_path.exists() and not os.access(gradlew_path, os.X_OK):
                gradlew_path.chmod(gradlew_path.stat().st_mode | 0o111)
                log.debug("Made %s executable", gradlew_path)

        # Remove stale reports so we only parse results from THIS run
        _clean_test_reports(project_path)

        log.debug("Running tests: %s", " ".join(cmd))
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout_seconds, cwd=str(project_path),
        )

        build_success = True
        error_messages: list[str] = []

        if result.returncode != 0:
            # Check if it's a compilation error vs test failure
            stderr = result.stderr or ""
            if "COMPILATION ERROR" in stderr or "compiler" in stderr.lower():
                build_success = False
                error_messages = [l for l in stderr.splitlines() if l.strip()][:10]

        tests_run, tests_passed, tests_failed, failed_names = _parse_surefire_reports(project_path)

        duration = (time.monotonic() - start) * 1000

        return TestResult(
            success=result.returncode == 0 and tests_failed == 0,
            tests_run=tests_run,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            tests_errored=0,
            failed_test_names=failed_names,
            build_success=build_success,
            error_messages=error_messages,
            duration_ms=duration,
        )

    except subprocess.TimeoutExpired:
        duration = (time.monotonic() - start) * 1000
        return TestResult(
            success=False, tests_run=0, tests_passed=0, tests_failed=0,
            tests_errored=0, failed_test_names=[],
            build_success=False, error_messages=["Test execution timed out"],
            duration_ms=duration,
        )
    except Exception as e:
        duration = (time.monotonic() - start) * 1000
        return TestResult(
            success=False, tests_run=0, tests_passed=0, tests_failed=0,
            tests_errored=0, failed_test_names=[],
            build_success=False, error_messages=[str(e)],
            duration_ms=duration,
        )
    finally:
        try:
            _restore_file(method, project_path)
        except Exception as e:
            log.error("Failed to restore file after test run: %s. Using git checkout.", e)
            subprocess.run(
                ["git", "checkout", "--", "."],
                cwd=str(project_path), capture_output=True,
            )
