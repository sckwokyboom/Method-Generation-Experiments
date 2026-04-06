"""Test runner for pass@k evaluation: replace method body, run tests, restore."""
from __future__ import annotations

import logging
import os
import platform
import shlex
import shutil
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


# ---------------------------------------------------------------------------
# Helpers: file manipulation
# ---------------------------------------------------------------------------

def _replace_method_body(method: ExtractedMethod, generated_body: str, project_path: Path) -> Path:
    """Replace method body in the source file and return the file path."""
    file_content = method.file_content
    body_start = method.body_start_offset
    body_end = method.body_end_offset

    # body_start points to '{', body_end points to one past '}'.
    # generated_body is the LLM output WITHOUT braces (FIM puts '{' in prefix, '}' in suffix).
    # Keep the '{' from the original file by slicing to body_start + 1.
    modified = file_content[:body_start + 1] + generated_body + file_content[body_end:]

    # Resolve absolute path from project root + relative file_path
    source_file = project_path / method.file_path
    if not source_file.exists():
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


# ---------------------------------------------------------------------------
# Helpers: Gradle module / test class detection
# ---------------------------------------------------------------------------

def _module_from_file_path(file_path: str) -> str | None:
    """Extract Gradle module name (first path component) from a relative file path.

    E.g. 'junit-platform-engine/src/main/java/...' → 'junit-platform-engine'
         'src/main/java/...' → None  (root project, no module prefix)
    """
    parts = file_path.replace("\\", "/").split("/")
    # First component must NOT be 'src' itself — that means there's no module prefix
    if len(parts) >= 3 and parts[0] != "src" and "src" in parts[1:]:
        return parts[0]
    return None


def _test_path_to_fqn(test_path: str) -> str | None:
    """Convert a test file path to a fully-qualified Java class name.

    E.g. 'junit-platform-engine/src/test/java/org/junit/platform/engine/FooTest.java'
       → 'org.junit.platform.engine.FooTest'

    Also handles Kotlin (.kt) and Groovy (.groovy) test files.
    """
    normalized = test_path.replace("\\", "/")

    # Find 'src/test/java/' or 'src/test/kotlin/' or 'src/test/groovy/' boundary
    for marker in ("src/test/java/", "src/test/kotlin/", "src/test/groovy/",
                    "src/testFixtures/java/"):
        idx = normalized.find(marker)
        if idx >= 0:
            rel = normalized[idx + len(marker):]
            # Strip extension and convert path separators to dots
            for ext in (".java", ".kt", ".groovy"):
                if rel.endswith(ext):
                    rel = rel[: -len(ext)]
                    break
            return rel.replace("/", ".")
    return None


# ---------------------------------------------------------------------------
# Helpers: test report management
# ---------------------------------------------------------------------------

def _clean_test_reports(project_path: Path, modules: list[str] | None = None) -> None:
    """Delete stale test result directories so Gradle/Maven re-runs tests.

    If *modules* is given, only cleans those submodules' reports (fast).
    Otherwise cleans the entire project tree (slow but thorough).
    """
    if modules:
        for mod in modules:
            d = project_path / mod / "build" / "test-results" / "test"
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
            d = project_path / mod / "target" / "surefire-reports"
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
    else:
        for d in project_path.rglob("build/test-results/test"):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
        for d in project_path.rglob("target/surefire-reports"):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)


def _parse_test_reports(
    project_path: Path, modules: list[str] | None = None,
) -> tuple[int, int, int, list[str]]:
    """Parse Surefire/JUnit XML reports. Returns (run, passed, failed, failed_names).

    If *modules* is given, only parses those submodules' reports.
    Otherwise searches the entire project tree recursively.
    """
    xml_files: list[Path] = []

    if modules:
        for mod in modules:
            gradle_dir = project_path / mod / "build" / "test-results" / "test"
            if gradle_dir.is_dir():
                xml_files.extend(gradle_dir.glob("TEST-*.xml"))
            maven_dir = project_path / mod / "target" / "surefire-reports"
            if maven_dir.is_dir():
                xml_files.extend(maven_dir.glob("TEST-*.xml"))
    else:
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


# ---------------------------------------------------------------------------
# Helpers: Gradle command construction
# ---------------------------------------------------------------------------

def _mvn_cmd(project_path: Path) -> str:
    """Return a cross-platform Maven command, preferring the wrapper if present."""
    is_windows = platform.system() == "Windows"
    wrapper = project_path / ("mvnw.cmd" if is_windows else "mvnw")
    if wrapper.exists():
        return str(wrapper)
    return "mvn.cmd" if is_windows else "mvn"


def _gradlew_cmd(project_path: Path) -> str:
    """Return a cross-platform path to the Gradle wrapper, relative to cwd."""
    is_windows = platform.system() == "Windows"
    if is_windows:
        bat = project_path / "gradlew.bat"
        return str(bat) if bat.exists() else "gradle"
    sh = project_path / "gradlew"
    return str(sh) if sh.exists() else "gradle"


def _build_targeted_gradle_cmd(
    project_path: Path,
    test_file_paths: list[str],
) -> tuple[list[str], list[str]]:
    """Build a Gradle command that runs only the specified test classes.

    Groups test classes by their module and builds a single Gradle invocation:
      ./gradlew :mod1:test --tests FQN1 :mod2:test --tests FQN2 --no-daemon

    Returns (cmd, list_of_modules) so callers know which modules to clean/parse.
    """
    from collections import defaultdict
    module_tests: dict[str, list[str]] = defaultdict(list)

    for test_path in test_file_paths:
        fqn = _test_path_to_fqn(test_path)
        module = _module_from_file_path(test_path)
        if fqn and module:
            module_tests[module].append(fqn)

    if not module_tests:
        return [], []

    gradle = _gradlew_cmd(project_path)
    cmd = [gradle]
    modules = list(module_tests.keys())
    for mod, fqns in module_tests.items():
        cmd.append(f":{mod}:test")
        for fqn in fqns:
            cmd.extend(["--tests", fqn])
    cmd.append("--no-daemon")
    return cmd, modules


def _build_full_gradle_cmd(
    project_path: Path,
    test_command: str | None,
    build_system: str,
) -> list[str]:
    """Build a full-suite test command (fallback when no targeted tests available)."""
    is_windows = platform.system() == "Windows"

    if test_command:
        if is_windows:
            cmd = test_command.split()
            if cmd and cmd[0] in ("./gradlew", "gradlew"):
                bat = project_path / "gradlew.bat"
                cmd[0] = str(bat) if bat.exists() else "gradle"
            elif cmd and cmd[0] in ("mvn", "./mvnw", "mvnw"):
                cmd[0] = _mvn_cmd(project_path)
        else:
            cmd = shlex.split(test_command)
        return cmd

    if build_system == "maven":
        mvn = _mvn_cmd(project_path)
        return [mvn, "test", "-f", str(project_path / "pom.xml"), "-q"]
    if build_system == "gradle":
        gradle = _gradlew_cmd(project_path)
        return [gradle, "test", "-q", "--no-daemon"]
    raise ValueError(f"Unknown build system: {build_system}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_test_evaluation(
    method: ExtractedMethod,
    generated_body: str,
    project_path: str | Path,
    build_system: str = "maven",
    timeout_seconds: int = 300,
    test_command: str | None = None,
    test_file_paths: list[str] | None = None,
) -> TestResult:
    """Replace method body, run tests, restore, and return results.

    If *test_file_paths* is provided, runs only those test classes in the
    relevant Gradle module instead of the full test suite.
    """
    project_path = Path(project_path).resolve()
    start = time.monotonic()

    try:
        _replace_method_body(method, generated_body, project_path)

        is_windows = platform.system() == "Windows"

        # --- Decide: targeted or full test run ---
        targeted_modules: list[str] | None = None

        if test_file_paths and build_system == "gradle":
            cmd, targeted_modules = _build_targeted_gradle_cmd(project_path, test_file_paths)
            if not targeted_modules:
                cmd = _build_full_gradle_cmd(project_path, test_command, build_system)

        if targeted_modules is None:
            cmd = _build_full_gradle_cmd(project_path, test_command, build_system)

        # Ensure gradlew is executable on Unix
        if not is_windows and cmd and cmd[0].endswith("gradlew"):
            gradlew_path = Path(cmd[0])
            if gradlew_path.exists() and not os.access(gradlew_path, os.X_OK):
                gradlew_path.chmod(gradlew_path.stat().st_mode | 0o111)

        # On Windows, .bat/.cmd files must be run through cmd.exe
        if is_windows and cmd and (cmd[0].endswith(".bat") or cmd[0].endswith(".cmd")):
            cmd = ["cmd", "/c"] + cmd

        # Remove stale reports (scoped to modules when targeted)
        _clean_test_reports(project_path, modules=targeted_modules)

        targeted = targeted_modules is not None and len(targeted_modules) > 0
        log.info("Running tests%s: %s",
                 f" (modules={targeted_modules})" if targeted else " (full suite)",
                 " ".join(cmd))
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout_seconds, cwd=str(project_path),
        )

        build_success = True
        error_messages: list[str] = []

        if result.returncode != 0:
            stderr = result.stderr or ""
            stdout = result.stdout or ""
            combined = stderr + stdout
            if "COMPILATION ERROR" in combined or "compiler" in combined.lower() \
                    or "Compilation failed" in combined:
                build_success = False
            error_messages = [l for l in combined.splitlines() if l.strip()][-20:]
            log.warning("Build failed (rc=%d). Last output:\n%s", result.returncode,
                        "\n".join(error_messages))

        # Parse reports (scoped to modules when targeted)
        tests_run, tests_passed, tests_failed, failed_names = _parse_test_reports(
            project_path, modules=targeted_modules,
        )

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
        log.warning("Test evaluation exception: %s", e)
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
