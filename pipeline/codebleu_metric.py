from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_CODEBLEU_AVAILABLE: bool | None = None


def compute_codebleu(generated: str, reference: str) -> float | None:
    """Compute CodeBLEU score between generated and reference Java code.

    Returns None if the codebleu package is not installed or computation fails.
    """
    global _CODEBLEU_AVAILABLE

    if _CODEBLEU_AVAILABLE is False:
        return None

    try:
        from codebleu import calc_codebleu

        _CODEBLEU_AVAILABLE = True
    except ImportError:
        if _CODEBLEU_AVAILABLE is None:
            log.warning(
                "codebleu package not installed. Install with: pip install 'codebleu>=0.7'. "
                "CodeBLEU metrics will be skipped."
            )
        _CODEBLEU_AVAILABLE = False
        return None

    try:
        result = calc_codebleu(
            references=[[reference]],
            predictions=[generated],
            lang="java",
            weights=(0.25, 0.25, 0.25, 0.25),
        )
        return round(result["codebleu"], 6)
    except Exception:
        log.warning("CodeBLEU computation failed", exc_info=True)
        return None
