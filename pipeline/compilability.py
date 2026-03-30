from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from pipeline.models import CompilabilityResult, ExtractedMethod

log = logging.getLogger(__name__)


def validate_classpath(classpath: list[str]) -> list[str]:
    """Filter classpath entries to only those that exist on disk."""
    valid = []
    missing = 0
    for entry in classpath:
        if Path(entry).exists():
            valid.append(entry)
        else:
            missing += 1
    if missing > 0:
        log.warning(
            "Classpath: %d of %d entries missing on disk (filtered out). "
            "Are you running on a different machine than extraction?",
            missing, len(classpath),
        )
    return valid


def check_compilability(
    method: ExtractedMethod,
    generated_body: str,
    classpath: list[str],
    java_home: str | None = None,
    source_version: str = "21",
) -> CompilabilityResult:
    file_content = method.file_content
    body_start = method.body_start_offset
    body_end = method.body_end_offset

    # body_start points to '{', body_end points to one past '}'.
    # ground_truth = file_content[body_start+1 : body_end] (includes '}').
    # We replace the same range with the generated body.
    modified = file_content[:body_start + 1] + generated_body + file_content[body_end:]

    file_rel_path = method.file_path
    parts = file_rel_path.replace("\\", "/").split("/")
    try:
        src_idx = parts.index("java")
        package_path = "/".join(parts[src_idx + 1:])
    except ValueError:
        package_path = parts[-1]

    with tempfile.TemporaryDirectory() as tmpdir:
        target_file = Path(tmpdir) / package_path
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(modified, encoding="utf-8")

        javac = "javac"
        if java_home:
            javac = str(Path(java_home) / "bin" / "javac")

        cmd = [javac]
        valid_cp = validate_classpath(classpath)
        if valid_cp:
            sep = ";" if sys.platform == "win32" else ":"
            cmd.extend(["-cp", sep.join(valid_cp)])
        cmd.extend([
            "-source", source_version,
            "-target", source_version,
            "-nowarn",
            "-implicit:none",
            str(target_file),
        ])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            errors = []
            if result.returncode != 0:
                errors = [line for line in result.stderr.splitlines() if line.strip()]
                if log.isEnabledFor(logging.DEBUG):
                    log.debug(
                        "Compilation failed for %s#%s:\n%s",
                        method.class_fqn, method.method_name,
                        "\n".join(errors[:10]),
                    )

            return CompilabilityResult(
                success=result.returncode == 0,
                error_messages=errors,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return CompilabilityResult(
                success=False, error_messages=["Compilation timed out"], exit_code=-1,
            )
        except FileNotFoundError:
            return CompilabilityResult(
                success=False,
                error_messages=[f"javac not found (tried: {javac})"],
                exit_code=-1,
            )
