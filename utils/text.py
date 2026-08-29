"""
utils/text.py
-------------
Text cleaning and chunking helpers shared across scrapers and LLM modules.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterator

import tiktoken

# Default tokeniser (cl100k_base is used by GPT-4 / most modern models).
_ENC = tiktoken.get_encoding("cl100k_base")


# ── Cleaning ─────────────────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(text: str) -> str:
    """Strip HTML tags, control characters, and normalise whitespace.

    Parameters
    ----------
    text:
        Raw string, possibly containing HTML markup.

    Returns
    -------
    str
        Clean, whitespace-normalised UTF-8 string.
    """
    # Normalise unicode (NFC form)
    text = unicodedata.normalize("NFC", text)
    # Strip HTML tags
    text = _TAG_RE.sub(" ", text)
    # Remove control characters
    text = _CONTROL_RE.sub("", text)
    # Collapse whitespace
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


# ── Chunking ─────────────────────────────────────────────────────────────────

_SENTENCE_DELIMITERS = re.compile(r"(?<=[.!?])\s+")


def _sentences(text: str) -> list[str]:
    """Split *text* on sentence boundaries."""
    return [s.strip() for s in _SENTENCE_DELIMITERS.split(text) if s.strip()]


def chunk_text(
    text: str,
    max_tokens: int = 1500,
    overlap_sentences: int = 1,
) -> Iterator[str]:
    """Split *text* into token-bounded chunks with sentence-level overlap.

    Parameters
    ----------
    text:
        Input text (should already be cleaned).
    max_tokens:
        Maximum number of tokens per chunk.
    overlap_sentences:
        Number of trailing sentences from the previous chunk to prepend to
        the next chunk for context continuity.

    Yields
    ------
    str
        Successive chunks of text, each ≤ *max_tokens* tokens.
    """
    sentences = _sentences(text)
    current: list[str] = []
    current_tokens = 0
    tail: list[str] = []  # overlap buffer

    for sentence in sentences:
        s_tokens = len(_ENC.encode(sentence))

        if current_tokens + s_tokens > max_tokens and current:
            yield " ".join(current)
            tail = current[-overlap_sentences:] if overlap_sentences else []
            current = list(tail)
            current_tokens = sum(len(_ENC.encode(s)) for s in current)

        current.append(sentence)
        current_tokens += s_tokens

    if current:
        yield " ".join(current)


def count_tokens(text: str) -> int:
    """Return the token count for *text* using cl100k_base."""
    return len(_ENC.encode(text))
