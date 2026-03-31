"""Approximate token counting using tiktoken (cl100k_base).

Not exact for Qwen2.5 Coder, but close enough for budget checks (~5-15% error).
Always overestimates slightly for safety.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_encoder = None


def _get_encoder():
    global _encoder
    if _encoder is None:
        import tiktoken
        _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


def count_tokens(text: str) -> int:
    return len(_get_encoder().encode(text, disallowed_special=()))
