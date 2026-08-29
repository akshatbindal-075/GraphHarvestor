"""
src/llm/extractor.py
---------------------
LLM extraction engine using litellm with multi-provider fallback.

Fallback order: Gemini Flash → Groq Llama 3 → DeepSeek

Retry policy (tenacity):
  - Retries ONLY on HTTP 429 (rate limit) errors.
  - Exponential backoff with jitter: starts at 2s, caps at 60s.
  - Fails fast (no retry) on all other errors; moves to next provider.

Chunking:
  - Token budget: ~6000 tokens (~24,000 chars) per call.
  - If text exceeds budget, smart truncation keeps:
      * First 500 words (usually contains title/author/price info)
      * Any paragraph containing extraction-signal keywords.

Logging:
  - Every call logged as a JSON line to logs/pipeline.jsonl:
    {source_url, provider_used, schema_type, success, latency_ms, error}
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import litellm
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    wait_random,
    before_sleep_log,
)

import sys

from src.config import DEEPSEEK_API_KEY, GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY, LOGS_DIR, LLM_MODELS
from src.llm.prompts import get_prompt

logger = logging.getLogger(__name__)

# Fix Windows CP1252 console crashing on unicode arrows in log messages
if sys.stdout.encoding and sys.stdout.encoding.lower() in ("cp1252", "mbcs"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Configure litellm providers via environment (litellm reads standard env vars)
if GEMINI_API_KEY:
    os.environ.setdefault("GEMINI_API_KEY", GEMINI_API_KEY)
if GROQ_API_KEY:
    os.environ.setdefault("GROQ_API_KEY", GROQ_API_KEY)
if DEEPSEEK_API_KEY:
    os.environ.setdefault("DEEPSEEK_API_KEY", DEEPSEEK_API_KEY)
if OPENROUTER_API_KEY:
    os.environ.setdefault("OPENROUTER_API_KEY", OPENROUTER_API_KEY)

# Approx chars per token (conservative estimate for English text)
_CHARS_PER_TOKEN = 4
_TOKEN_BUDGET = 6000
_CHAR_BUDGET = _TOKEN_BUDGET * _CHARS_PER_TOKEN

# Keywords that signal extraction-relevant paragraphs
_SIGNAL_WORDS = re.compile(
    r"founded|pricing|author|published|employee|revenue|funding|series|"
    r"remote|salary|location|github|arxiv|abstract|company|product|launch",
    re.I,
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


# ── Structured logging ────────────────────────────────────────────────────────

def _log_call(
    source_url: str,
    schema_type: str,
    provider: str,
    success: bool,
    latency_ms: float,
    error: str | None = None,
) -> None:
    """Append one JSON line to logs/pipeline.jsonl."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_url": source_url,
        "schema_type": schema_type,
        "provider_used": provider,
        "success": success,
        "latency_ms": round(latency_ms, 1),
        "error": error,
    }
    with open(LOGS_DIR / "pipeline.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── Smart text truncation ─────────────────────────────────────────────────────

def _smart_truncate(text: str, char_budget: int = _CHAR_BUDGET) -> str:
    """Truncate *text* to *char_budget* chars, keeping signal paragraphs."""
    if len(text) <= char_budget:
        return text

    # Always keep first 500 words
    words = text.split()
    head = " ".join(words[:500])

    # Collect signal paragraphs from the rest
    paragraphs = re.split(r"\n\s*\n|\.\s{2,}", text[len(head):])
    signal_paras = [
        p.strip() for p in paragraphs
        if _SIGNAL_WORDS.search(p) and p.strip()
    ]

    combined = head + "\n\n" + "\n\n".join(signal_paras)
    return combined[:char_budget]


# ── Per-provider retry (only on 429) ─────────────────────────────────────────

def _is_rate_limit(exc: BaseException) -> bool:
    """True only for HTTP 429 rate-limit errors."""
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "too many requests" in msg


def _make_retry_call(model: str, messages: list[dict]) -> str:
    """Attempt one litellm call with tenacity retry on 429 only."""

    @retry(
        retry=retry_if_exception(_is_rate_limit),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=60) + wait_random(0, 3),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _call() -> str:
        response = litellm.completion(model=model, messages=messages, max_tokens=1024, temperature=0.0)
        return response.choices[0].message.content or ""

    return _call()


# ── Public API ────────────────────────────────────────────────────────────────

def extract(
    raw_text: str,
    schema_type: str,
    source_url: str = "",
) -> dict[str, Any] | None:
    """Extract structured data from *raw_text* using the LLM fallback chain.

    Parameters
    ----------
    raw_text:
        Raw scraped text (will be truncated if over token budget).
    schema_type:
        One of STARTUP, PRODUCT, RESEARCH_PAPER, JOB, NEWS.
    source_url:
        Original source URL — embedded in every output record + log.

    Returns
    -------
    dict | None
        Parsed extraction result, or None if all providers failed.
    """
    truncated = _smart_truncate(raw_text)
    prompt = get_prompt(schema_type, truncated)
    messages = [{"role": "user", "content": prompt}]

    for model in LLM_MODELS:
        t0 = time.monotonic()
        try:
            raw_response = _make_retry_call(model, messages)
            latency_ms = (time.monotonic() - t0) * 1000

            # Extract JSON from response (strip any accidental markdown)
            match = _JSON_RE.search(raw_response)
            if not match:
                raise ValueError(f"No JSON object in response: {raw_response[:200]}")

            result = json.loads(match.group())

            # Inject source_url to guarantee traceability
            if source_url:
                result.setdefault("source", {})
                result["source"].setdefault("url", source_url)

            _log_call(source_url, schema_type, model, True, latency_ms)
            logger.debug("Extracted %s via %s (%.0f ms)", schema_type, model, latency_ms)
            return result

        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            error_str = str(exc)[:200]
            _log_call(source_url, schema_type, model, False, latency_ms, error_str)

            if _is_rate_limit(exc):
                logger.warning("Rate limited on %s — trying next provider", model)
            else:
                logger.warning("Provider %s failed (%s) — trying next", model, error_str[:80])

    logger.error("All LLM providers failed for %s [%s]", schema_type, source_url)
    return None
