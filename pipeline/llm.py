from __future__ import annotations

import logging
import re
import time

import requests

from pipeline.config import LLMConfig
from pipeline.models import CompletionResult

log = logging.getLogger(__name__)

FIM_TOKENS = {"<|fim_prefix|>", "<|fim_suffix|>", "<|fim_middle|>", "<|fim_pad|>", "<|endoftext|>"}

MAX_RETRIES = 3
BACKOFF_BASE = 2.0
# Extra headroom applied to read timeout on each retry after a ReadTimeout,
# in case the server is slow or under load.
TIMEOUT_GROWTH_FACTOR = 1.5

_CONTEXT_RE = re.compile(
    r"(\d+)\s*input\s*tokens.*?(\d+)\s*output\s*tokens.*?"
    r"context\s*length\s*(?:is\s*(?:only\s*)?)?(\d+)",
    re.IGNORECASE | re.DOTALL,
)


def _parse_context_error(body: str) -> tuple[int, int, int] | None:
    """Parse (prompt_tokens, requested_max, context_length) from an error response."""
    m = _CONTEXT_RE.search(body)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def generate_completion(prompt: str, config: LLMConfig) -> CompletionResult:
    payload = {
        "model": config.model_name,
        "prompt": prompt,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "stop": config.stop_sequences,
        "n": config.num_generations,
    }
    if config.seed is not None:
        payload["seed"] = config.seed

    last_error = None
    current_read_timeout = config.timeout_seconds
    for attempt in range(MAX_RETRIES):
        try:
            start_time = time.monotonic()
            response = requests.post(
                config.endpoint_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=(10, current_read_timeout),
            )
            latency_ms = (time.monotonic() - start_time) * 1000

            response.raise_for_status()
            data = response.json()

            choice = data["choices"][0]
            text = choice.get("text", "")
            text = postprocess_completion(text)

            return CompletionResult(
                text=text,
                finish_reason=choice.get("finish_reason", "unknown"),
                usage=data.get("usage", {}),
                latency_ms=latency_ms,
                raw_response=data,
            )
        except requests.HTTPError as e:
            last_error = e
            resp_body = e.response.text if e.response is not None else ""
            log.error("HTTP %d from %s | body: %.500s",
                      e.response.status_code if e.response is not None else 0,
                      config.endpoint_url, resp_body)

            # Context length exceeded — retry with reduced max_tokens
            if e.response is not None and e.response.status_code == 400:
                parsed = _parse_context_error(resp_body)
                if parsed:
                    prompt_tokens, _, context_length = parsed
                    reduced = context_length - prompt_tokens
                    if reduced > 0:
                        log.warning(
                            "Context exceeded: prompt=%d tokens, context=%d. "
                            "Retrying with max_tokens=%d (was %d).",
                            prompt_tokens, context_length, reduced, payload["max_tokens"],
                        )
                        payload["max_tokens"] = reduced
                        continue
                    else:
                        log.error(
                            "Prompt (%d tokens) exceeds context window (%d). Cannot generate.",
                            prompt_tokens, context_length,
                        )
                        raise

            if attempt < MAX_RETRIES - 1:
                wait = BACKOFF_BASE ** attempt
                log.warning("Retrying in %.1fs (attempt %d/%d)", wait, attempt + 1, MAX_RETRIES)
                time.sleep(wait)
            else:
                log.error("LLM request failed after %d attempts", MAX_RETRIES)
        except requests.exceptions.ReadTimeout as e:
            last_error = e
            # Grow the read timeout on each retry — server may be slow/overloaded.
            new_timeout = int(current_read_timeout * TIMEOUT_GROWTH_FACTOR)
            log.warning(
                "LLM read timeout after %ds (attempt %d/%d). Server may be slow. "
                "Increasing timeout to %ds for retry.",
                current_read_timeout, attempt + 1, MAX_RETRIES, new_timeout,
            )
            current_read_timeout = new_timeout
            if attempt < MAX_RETRIES - 1:
                time.sleep(2.0)  # brief pause before retry
            else:
                log.error("LLM request timed out after %d attempts (final timeout=%ds). "
                          "Consider increasing llm.timeout_seconds in config.",
                          MAX_RETRIES, current_read_timeout)
        except (requests.RequestException, KeyError, IndexError) as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                wait = BACKOFF_BASE ** attempt
                log.warning("LLM request failed (attempt %d/%d): %s. Retrying in %.1fs",
                            attempt + 1, MAX_RETRIES, e, wait)
                time.sleep(wait)
            else:
                log.error("LLM request failed after %d attempts: %s", MAX_RETRIES, e)

    raise RuntimeError(f"LLM request failed after {MAX_RETRIES} attempts") from last_error


def postprocess_completion(text: str) -> str:
    for token in FIM_TOKENS:
        idx = text.find(token)
        if idx != -1:
            text = text[:idx]
    return text.rstrip()
