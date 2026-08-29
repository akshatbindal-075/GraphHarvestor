"""
resolution/resolver.py
----------------------
Entity deduplication using fuzzy string matching (``rapidfuzz``).

Algorithm
---------
1. Sort entities by name length (longer = more specific, kept as canonical).
2. For each entity, find all others whose name matches above *threshold*.
3. Assign the group a canonical name (the longest / most frequent one).
4. Return a deduplicated list with an ``aliases`` field added.

Usage
-----
    from resolution.resolver import resolve_entities

    raw = [
        {"id": "e1", "name": "Apple Inc.", "type": "Organization"},
        {"id": "e2", "name": "Apple",      "type": "Organization"},
        {"id": "e3", "name": "Tim Cook",   "type": "Person"},
    ]
    resolved = resolve_entities(raw)
    # → [{"id": "e1", "name": "Apple Inc.", "type": "Organization", "aliases": ["Apple"]},
    #    {"id": "e3", "name": "Tim Cook",   "type": "Person",        "aliases": []}]
"""

from __future__ import annotations

from rapidfuzz import fuzz

from utils.logger import get_logger

log = get_logger(__name__)

_DEFAULT_THRESHOLD = 85.0  # similarity score 0-100


def resolve_entities(
    entities: list[dict],
    threshold: float = _DEFAULT_THRESHOLD,
) -> list[dict]:
    """Deduplicate *entities* by fuzzy name similarity.

    Parameters
    ----------
    entities:
        List of entity dicts, each containing at least ``id``, ``name``,
        and ``type`` keys.
    threshold:
        Minimum ``rapidfuzz.fuzz.token_sort_ratio`` score (0–100) to
        consider two entities duplicates.

    Returns
    -------
    list[dict]
        Deduplicated entities.  Each entry gains an ``aliases`` list of
        merged-away names and a ``merged_ids`` list of absorbed entity IDs.
    """
    if not entities:
        return []

    # Sort: longer names first (prefer "Apple Inc." over "Apple")
    sorted_ents = sorted(entities, key=lambda e: len(e.get("name", "")), reverse=True)
    merged: list[dict] = []
    absorbed: set[int] = set()  # indices already merged into another entity

    for i, ent in enumerate(sorted_ents):
        if i in absorbed:
            continue

        canonical = dict(ent)
        canonical.setdefault("aliases", [])
        canonical.setdefault("merged_ids", [])

        for j, other in enumerate(sorted_ents):
            if j <= i or j in absorbed:
                continue
            # Only deduplicate within the same entity type
            if ent.get("type") != other.get("type"):
                continue
            score = fuzz.token_sort_ratio(ent["name"], other["name"])
            if score >= threshold:
                canonical["aliases"].append(other["name"])
                canonical["merged_ids"].append(other["id"])
                absorbed.add(j)
                log.debug(
                    "Merged {a!r} → {b!r} (score={s:.1f})",
                    a=other["name"],
                    b=ent["name"],
                    s=score,
                )

        merged.append(canonical)

    log.info(
        "Resolved {before} → {after} entities ({dup} duplicates removed)",
        before=len(entities),
        after=len(merged),
        dup=len(entities) - len(merged),
    )
    return merged
