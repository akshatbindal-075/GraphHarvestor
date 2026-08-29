"""
src/scrapers/product_scraper.py
--------------------------------
Async scraper for product listings from two sources:

1. **Product Hunt GraphQL API** (primary) — requires PRODUCT_HUNT_TOKEN in .env.
   Pulls name, tagline, website, topics, pricing model, vote count, launch date.
   API docs: https://api.producthunt.com/v2/docs

2. **Hacker News "Show HN" posts via Algolia API** (fallback / supplement) —
   completely free, no auth. Extracts product name + URL from Show HN titles.
   API: https://hn.algolia.com/api/v1/search

Pricing model inference (PRODUCT_HUNT):
  - FREE if no pricing topics and no "paid" keywords
  - FREEMIUM if "freemium" in topics
  - PAID / ENTERPRISE inferred from tagline/description keywords

Engineering decision: Both sources are combined and deduplicated by website URL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import aiohttp

from src.config import DEFAULT_SEMAPHORE, PRODUCT_HUNT_TOKEN, RAW_DIR, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

PH_GRAPHQL = "https://api.producthunt.com/v2/api/graphql"
HN_ALGOLIA = "https://hn.algolia.com/api/v1/search"

# Keywords used to infer pricing model from product description
_PAID_RE = re.compile(r"\$\d+|per month|per seat|subscription|paid plan|pricing", re.I)
_ENTERPRISE_RE = re.compile(r"enterprise|custom pricing|contact sales|request demo", re.I)
_FREE_RE = re.compile(r"\bfree\b|open.?source|no credit card|forever free", re.I)
_FREEMIUM_RE = re.compile(r"freemium|free plan|free tier|upgrade|premium", re.I)


def _infer_pricing(text: str, topics: list[str]) -> str:
    topic_str = " ".join(topics).lower()
    combined = f"{text} {topic_str}"
    if _ENTERPRISE_RE.search(combined):
        return "ENTERPRISE"
    if _PAID_RE.search(combined):
        return "PAID"
    if _FREEMIUM_RE.search(combined):
        return "FREEMIUM"
    return "FREE"


# ── Product Hunt ──────────────────────────────────────────────────────────────

_PH_QUERY = """
query($after: String, $first: Int!) {
  posts(first: $first, after: $after, order: VOTES) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id name tagline description website
        votesCount reviewsCount
        thumbnail { url }
        topics { edges { node { name } } }
        createdAt
      }
    }
  }
}
"""


async def _fetch_ph_page(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    after: str | None,
    page_size: int = 50,
) -> dict:
    headers = {
        "Authorization": f"Bearer {PRODUCT_HUNT_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {"query": _PH_QUERY, "variables": {"first": page_size, "after": after}}
    async with sem:
        async with session.post(
            PH_GRAPHQL,
            json=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


async def _collect_from_product_hunt(
    max_results: int, sem: asyncio.Semaphore, session: aiohttp.ClientSession
) -> list[dict]:
    if not PRODUCT_HUNT_TOKEN:
        logger.warning("PRODUCT_HUNT_TOKEN not set — skipping Product Hunt source")
        return []

    records: list[dict] = []
    cursor: str | None = None
    now = datetime.now(timezone.utc).isoformat()

    while len(records) < max_results:
        try:
            data = await _fetch_ph_page(session, sem, cursor)
        except Exception as exc:
            logger.error("Product Hunt API error: %s", exc)
            break

        posts_data = data.get("data", {}).get("posts", {})
        edges = posts_data.get("edges", [])
        page_info = posts_data.get("pageInfo", {})

        for edge in edges:
            node = edge.get("node", {})
            topics = [t["node"]["name"] for t in node.get("topics", {}).get("edges", [])]
            desc = node.get("description", "") or node.get("tagline", "")
            pricing = _infer_pricing(desc, topics)

            records.append({
                "schemaVersion": "1.0",
                "recordType": "PRODUCT",
                "source": {
                    "name": "Product Hunt",
                    "url": f"https://www.producthunt.com/posts/{node.get('id', '')}",
                },
                "content": {
                    "startupName": node.get("name", ""),
                    "tagline": node.get("tagline", ""),
                    "website": node.get("website", ""),
                    "pricingModel": pricing,
                    "topics": topics,
                    "votesCount": node.get("votesCount", 0),
                },
                "collectedAt": now,
            })

        if not page_info.get("hasNextPage") or not edges:
            break
        cursor = page_info.get("endCursor")

    logger.info("Product Hunt: collected %d products", len(records))
    return records[:max_results]


# ── HN Show HN fallback ───────────────────────────────────────────────────────

async def _fetch_hn_page(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    page: int,
) -> dict:
    params = {
        "tags": "show_hn",
        "hitsPerPage": 100,
        "page": page,
        "numericFilters": "points>10",
    }
    async with sem:
        async with session.get(
            HN_ALGOLIA, params=params,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


def _parse_hn_hit(hit: dict) -> dict | None:
    title: str = hit.get("title", "")
    url: str = hit.get("url", "")
    # Show HN titles: "Show HN: ProductName – tagline"
    match = re.match(r"Show HN:\s*(.+?)(?:\s*[–—-]\s*(.+))?$", title, re.I)
    if not match:
        return None
    name = match.group(1).strip()
    tagline = (match.group(2) or "").strip()
    if not name:
        return None

    created = hit.get("created_at", "")
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        created_iso = dt.isoformat()
    except ValueError:
        created_iso = created

    return {
        "schemaVersion": "1.0",
        "recordType": "PRODUCT",
        "source": {
            "name": "Hacker News (Show HN)",
            "url": f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}",
        },
        "content": {
            "startupName": name,
            "tagline": tagline,
            "website": url,
            "pricingModel": _infer_pricing(f"{name} {tagline}", []),
            "hnPoints": hit.get("points", 0),
            "launchedAt": created_iso,
        },
        "collectedAt": datetime.now(timezone.utc).isoformat(),
    }


async def _collect_from_hn(
    max_results: int, sem: asyncio.Semaphore, session: aiohttp.ClientSession
) -> list[dict]:
    records: list[dict] = []
    pages_needed = (max_results // 100) + 2

    tasks = [_fetch_hn_page(session, sem, p) for p in range(pages_needed)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, Exception):
            logger.debug("HN page failed: %s", r)
            continue
        for hit in r.get("hits", []):
            parsed = _parse_hn_hit(hit)
            if parsed:
                records.append(parsed)

    logger.info("HN Show HN: collected %d products", len(records))
    return records[:max_results]


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(max_results: int = 1000, concurrency: int = DEFAULT_SEMAPHORE) -> list[dict]:
    """Collect product records from Product Hunt + HN Show HN.

    Product Hunt is primary when PRODUCT_HUNT_TOKEN is set.
    HN Algolia supplements (or replaces) to hit the max_results target.
    """
    sem = asyncio.Semaphore(concurrency)
    headers = {"User-Agent": "GraphHarvester/1.0"}

    async with aiohttp.ClientSession(headers=headers) as session:
        ph_records, hn_records = await asyncio.gather(
            _collect_from_product_hunt(max_results, sem, session),
            _collect_from_hn(max_results, sem, session),
        )

    # Merge and deduplicate by website URL
    combined: list[dict] = []
    seen_urls: set[str] = set()
    for rec in ph_records + hn_records:
        website = rec["content"].get("website", "")
        key = website.rstrip("/").lower() if website else id(rec)
        if key not in seen_urls:
            seen_urls.add(str(key))
            combined.append(rec)
        if len(combined) >= max_results:
            break

    # Save
    out_dir = RAW_DIR / "products"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_path = out_dir / f"products_{ts}.json"
    out_path.write_text(json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("Products: %d records → %s", len(combined), out_path)
    return combined


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(run())
    print(f"Collected {len(result)} products")
