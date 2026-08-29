"""
src/scrapers/startup_scraper.py
--------------------------------
Async scraper for Y Combinator companies using the YC Algolia search API.

YC migrated away from Next.js __NEXT_DATA__ (no longer embedded in page HTML).
Their companies directory is powered by Algolia — the same API that drives
the search bar at ycombinator.com/companies.

Algolia endpoint (public, no auth):
  https://45bwzj1sgc-dsn.algolia.net/1/indexes/*/queries
  App ID:  45BWZJ1SGC
  API Key: be96dfb9oof0c9f31fca4b80e0e48ec7  (public search-only key, visible in
           browser devtools on ycombinator.com/companies)

Pagination: Algolia hitsPerPage max = 1000, use page param.
Target: 4,000+ companies (full YC corpus).

Each record maps to the canonical STARTUP schema:
  entityName, description, website, employeeCount, founded, location.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import aiohttp

from src.config import DEFAULT_SEMAPHORE, RAW_DIR, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

# YC Algolia config extracted live from browser devtools (ycombinator.com/companies)
_ALGOLIA_APP_ID = "45BWZJ1SGC"
_ALGOLIA_API_KEY = (
    "NzllNTY5MzJiZGM2OTY2ZTQwMDEzOTNhYWZiZGRjODlhYzVkNjBmOGRjNzJiMWM4"
    "ZTU0ZDlhYTZjOTJiMjlhMWFuYWx5dGljc1RhZ3M9eWNkYyZyZXN0cmljdEluZGlj"
    "ZXM9WUNDb21wYW55X3Byb2R1Y3Rpb24lMkNZQ0NvbXBhbnlfQnlfTGF1bmNoX0RhdGVf"
    "cHJvZHVjdGlvbiZ0YWdGaWx0ZXJzPSU1QiUyMnljZGNfcHVibGljJTIyJTVE"
)
# Key is sent as URL query param (not header) — matches browser behaviour exactly
_ALGOLIA_URL = (
    f"https://{_ALGOLIA_APP_ID.lower()}-dsn.algolia.net/1/indexes/*/queries"
    f"?x-algolia-agent=Algolia%20for%20JavaScript%20(3.35.1)%3B%20Browser"
    f"&x-algolia-application-id={_ALGOLIA_APP_ID}"
    f"&x-algolia-api-key={_ALGOLIA_API_KEY}"
)
_ALGOLIA_HEADERS = {"Content-Type": "application/json"}

_TEAM_SIZE_MAP = {
    "1-10": 5,
    "11-50": 30,
    "51-200": 125,
    "201-500": 350,
    "501-1000": 750,
    "1001-5000": 3000,
    "5001-10000": 7500,
    "10001+": 15000,
}


def _parse_hit(hit: dict) -> dict:
    """Convert one Algolia hit to the canonical STARTUP schema."""
    team_size = hit.get("teamSize", "") or ""
    emp_count = _TEAM_SIZE_MAP.get(team_size)

    slug = hit.get("slug", "") or hit.get("objectID", "")
    return {
        "schemaVersion": "1.0",
        "recordType": "STARTUP",
        "source": {
            "name": "Y Combinator",
            "url": f"https://www.ycombinator.com/companies/{slug}",
        },
        "content": {
            "entityName": hit.get("name", ""),
            "description": hit.get("oneLiner", "") or hit.get("longDescription", ""),
            "website": hit.get("website", ""),
            "batch": hit.get("batch", ""),
            "industries": hit.get("industries", []),
            "tags": hit.get("tags", []),
            "status": hit.get("status", ""),
            "data": {
                "employeeCount": emp_count,
                "teamSizeLabel": team_size,
                "founded": hit.get("yearFounded"),
                "location": hit.get("location", ""),
                "country": hit.get("country", ""),
            },
        },
        "collectedAt": datetime.now(timezone.utc).isoformat(),
    }


async def _fetch_page(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    page: int,
    hits_per_page: int = 1000,
) -> list[dict]:
    """Fetch one page of YC companies from the Algolia API."""
    body = {
        "requests": [
            {
                "indexName": "YCCompany_production",
                "params": (
                    f"query=&hitsPerPage={hits_per_page}&page={page}"
                    "&tagFilters=%5B%22ycdc_public%22%5D"
                    "&attributesToRetrieve=%5B%22name%22%2C%22slug%22%2C%22oneLiner%22%2C%22website%22%2C%22teamSize%22%2C%22yearFounded%22%2C%22location%22%2C%22country%22%2C%22batch%22%2C%22industries%22%2C%22tags%22%2C%22status%22%5D"
                ),
            }
        ]
    }
    async with sem:
        try:
            async with session.post(
                _ALGOLIA_URL,
                json=body,
                headers=_ALGOLIA_HEADERS,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                results = data.get("results", [])
                if results:
                    hits = results[0].get("hits", [])
                    nb_pages = results[0].get("nbPages", 1)
                    logger.info("YC Algolia page=%d → %d companies (total pages=%d)", page, len(hits), nb_pages)
                    return hits, nb_pages
                return [], 1
        except Exception as exc:
            logger.error("YC Algolia page=%d failed: %s", page, exc)
            return [], 1


async def run(max_results: int = 5000, concurrency: int = DEFAULT_SEMAPHORE) -> list[dict]:
    """Fetch all YC companies via Algolia API."""
    sem = asyncio.Semaphore(concurrency)
    headers = {"User-Agent": "GraphHarvester/1.0"}

    async with aiohttp.ClientSession(headers=headers) as session:
        # Fetch page 0 first to discover total page count
        hits_p0, nb_pages = await _fetch_page(session, sem, page=0)
        if not hits_p0:
            logger.error("YC Algolia returned no results on page 0 — API key may have changed")
            return []

        # Fetch remaining pages in parallel
        remaining_tasks = [
            _fetch_page(session, sem, page=p)
            for p in range(1, nb_pages)
        ]
        remaining_results = await asyncio.gather(*remaining_tasks, return_exceptions=True)

    all_hits = list(hits_p0)
    for r in remaining_results:
        if isinstance(r, tuple):
            all_hits.extend(r[0])
        elif isinstance(r, Exception):
            logger.warning("Page fetch failed: %s", r)

    # Deduplicate by objectID
    seen: set[str] = set()
    unique_hits: list[dict] = []
    for h in all_hits:
        oid = h.get("objectID", id(h))
        if str(oid) not in seen:
            seen.add(str(oid))
            unique_hits.append(h)

    unique_hits = unique_hits[:max_results]
    records = [_parse_hit(h) for h in unique_hits]

    # Save
    out_dir = RAW_DIR / "startups"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_path = out_dir / f"yc_{ts}.json"
    out_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("YC Algolia: %d companies → %s", len(records), out_path)
    return records


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(run())
    print(f"Collected {len(result)} YC companies")
