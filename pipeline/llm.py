from __future__ import annotations

import logging
import time

import requests

from pipeline.config import LLMConfig
from pipeline.models import CompletionResult

log = logging.getLogger(__name__)

FIM_TOKENS = {"<|fim_prefix|>", "<|fim_suffix|>", "<|fim_middle|>", "<|fim_pad|>", "<|endoftext|>"}

MAX_RETRIES = 3
BACKOFF_BASE = 2.0


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
                timeout=(10, config.timeout_seconds),
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
            log.error("HTTP %d from %s | headers: %s | body: %.500s",
                       e.response.status_code, config.endpoint_url,
                       dict(e.response.headers), e.response.text)
            if attempt < MAX_RETRIES - 1:
                wait = BACKOFF_BASE ** attempt
                log.warning("Retrying in %.1fs (attempt %d/%d)", wait, attempt + 1, MAX_RETRIES)
                time.sleep(wait)
            else:
                log.error("LLM request failed after %d attempts", MAX_RETRIES)
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
