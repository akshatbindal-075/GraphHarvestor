"""
src/utils/date_parser.py
-------------------------
Normalize arbitrary date strings to ISO-8601 using `dateparser`.

Handles:
  - Absolute dates: "2024-01-15", "January 15, 2024", "15 Jan 2024"
  - Relative dates: "2 hours ago", "yesterday", "last week"
  - Missing dates: returns None (caller decides freshness policy)

Seen-URL cache:
  Stored at logs/seen_urls.json as a simple {url: first_seen_iso} dict.
  If a URL has been seen before, we return its cached first-seen date
  instead of treating it as "new". This prevents re-processing across runs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import dateparser

logger = logging.getLogger(__name__)

_SEEN_CACHE_PATH = Path("logs/seen_urls.json")
_seen_cache: dict[str, str] = {}
_cache_loaded = False


def _load_cache() -> None:
    global _cache_loaded, _seen_cache
    if _cache_loaded:
        return
    if _SEEN_CACHE_PATH.exists():
        try:
            _seen_cache = json.loads(_SEEN_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _seen_cache = {}
    _cache_loaded = True


def _save_cache() -> None:
    _SEEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SEEN_CACHE_PATH.write_text(
        json.dumps(_seen_cache, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def normalize_date(raw_date: str | None) -> str | None:
    """Parse *raw_date* into an ISO-8601 string.

    Returns None if the string cannot be parsed.
    All datetimes are returned in UTC.
    """
    if not raw_date or not raw_date.strip():
        return None

    parsed = dateparser.parse(
        raw_date.strip(),
        settings={
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DAY_OF_MONTH": "first",
            "TO_TIMEZONE": "UTC",
        },
    )
    if parsed is None:
        logger.debug("dateparser could not parse: %r", raw_date)
        return None

    return parsed.isoformat()


def is_fresh(date_iso: str | None, max_age_hours: int = 24) -> bool:
    """Return True if *date_iso* is within *max_age_hours* of now (UTC)."""
    if not date_iso:
        return False
    try:
        dt = datetime.fromisoformat(date_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return age_hours <= max_age_hours
    except ValueError:
        return False


def mark_seen(url: str) -> bool:
    """Mark *url* as seen. Returns True if this is the FIRST time we've seen it."""
    _load_cache()
    if url in _seen_cache:
        return False  # already seen
    _seen_cache[url] = datetime.now(timezone.utc).isoformat()
    _save_cache()
    return True


def was_seen(url: str) -> bool:
    """Return True if *url* has been seen in a previous run."""
    _load_cache()
    return url in _seen_cache


def get_first_seen(url: str) -> str | None:
    """Return the ISO-8601 timestamp when *url* was first seen, or None."""
    _load_cache()
    return _seen_cache.get(url)
