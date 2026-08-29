"""
src/resolution/entity_resolver.py
-----------------------------------
Resolve raw extracted entity names to canonical names.

Algorithm:
  1. Exact match against all canonical names + aliases in seed_entities.json.
  2. Fuzzy match via rapidfuzz.fuzz.ratio with a configurable threshold (default 85).
  3. If no match, return the raw name unchanged (canonical = None).

Every resolution attempt is logged:
  {raw_name, canonical_name, match_type, confidence_score}

This produces the "Entity Mapping Log" output for the Sheets writer.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import NamedTuple

from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)

_SEED_PATH = Path(__file__).parent / "seed_entities.json"
_DEFAULT_THRESHOLD = 85.0


class ResolutionResult(NamedTuple):
    raw_name: str
    canonical_name: str | None   # None if no match
    entity_id: str | None
    match_type: str              # "exact", "alias", "fuzzy", "none"
    confidence: float            # 0–100


def _load_seed() -> list[dict]:
    return json.loads(_SEED_PATH.read_text(encoding="utf-8"))


class EntityResolver:
    """Resolve raw entity names to canonical seed-list entries.

    Parameters
    ----------
    threshold:
        Minimum rapidfuzz ratio score (0–100) for a fuzzy match to be accepted.
    """

    def __init__(self, threshold: float = _DEFAULT_THRESHOLD) -> None:
        self.threshold = threshold
        self._seed = _load_seed()

        # Build lookup structures
        # exact_map: lowercase name/alias → (canonical, entity_id, match_type)
        self._exact_map: dict[str, tuple[str, str, str]] = {}
        self._canonical_names: list[str] = []
        self._canonical_ids: list[str] = []

        for entry in self._seed:
            cid = entry["id"]
            canonical = entry["canonical"]
            self._canonical_names.append(canonical)
            self._canonical_ids.append(cid)

            self._exact_map[canonical.lower()] = (canonical, cid, "exact")
            for alias in entry.get("aliases", []):
                self._exact_map[alias.lower()] = (canonical, cid, "alias")

        logger.debug("EntityResolver loaded %d seed entities", len(self._seed))

    def resolve(self, raw_name: str) -> ResolutionResult:
        """Resolve *raw_name* to its canonical form.

        Returns a :class:`ResolutionResult` with all match metadata.
        """
        if not raw_name or not raw_name.strip():
            return ResolutionResult(raw_name, None, None, "none", 0.0)

        key = raw_name.strip().lower()

        # 1. Exact / alias match
        if key in self._exact_map:
            canonical, entity_id, match_type = self._exact_map[key]
            logger.debug("Exact match: %r → %r", raw_name, canonical)
            return ResolutionResult(raw_name, canonical, entity_id, match_type, 100.0)

        # 2. Fuzzy match against canonical names
        best = process.extractOne(
            raw_name,
            self._canonical_names,
            scorer=fuzz.ratio,
            score_cutoff=self.threshold,
        )
        if best is not None:
            matched_name, score, idx = best
            entity_id = self._canonical_ids[idx]
            logger.debug("Fuzzy match: %r → %r (score=%.1f)", raw_name, matched_name, score)
            return ResolutionResult(raw_name, matched_name, entity_id, "fuzzy", float(score))

        logger.debug("No match found for: %r", raw_name)
        return ResolutionResult(raw_name, None, None, "none", 0.0)

    def resolve_batch(self, raw_names: list[str]) -> list[ResolutionResult]:
        """Resolve a list of entity names. Returns one result per input."""
        return [self.resolve(n) for n in raw_names]

    def to_log_record(self, result: ResolutionResult) -> dict:
        """Convert a ResolutionResult to a Sheets-friendly dict for the Entity Mapping Log."""
        return {
            "raw_name": result.raw_name,
            "canonical_name": result.canonical_name or result.raw_name,
            "match_type": result.match_type,
            "confidence_score": round(result.confidence, 1),
            "resolved": result.canonical_name is not None,
        }
