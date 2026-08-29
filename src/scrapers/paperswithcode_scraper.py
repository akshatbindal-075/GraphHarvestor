"""
src/scrapers/paperswithcode_scraper.py
---------------------------------------
Pulls AI/ML research papers with GitHub links and star counts.

** API: OpenAlex (https://api.openalex.org) **
   - Free, no key required (add email for polite-pool: 10 req/s vs 1 req/s)
   - 200M+ scholarly works indexed
   - Cursor-based pagination — efficient for large datasets
   - Set OPENALEX_EMAIL in .env to access the polite pool

Strategy:
  1. Query OpenAlex /works filtered to AI/ML concept IDs, cursor-paginated.
  2. Extract any GitHub URLs from open-access landing pages / abstract text.
  3. Call GitHub API for star count on found repos.

Saves to raw/research_papers/pwc_<timestamp>.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone

import aiohttp

from src.config import GITHUB_TOKEN, RAW_DIR, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

OPENALEX_API = "https://api.openalex.org"
GITHUB_API = "https://api.github.com"

# OpenAlex concept IDs for AI/ML (see https://openalex.org/concepts)
_AI_CONCEPTS = [
    "C154945302",   # Artificial Intelligence
    "C119857082",   # Machine Learning
    "C2522767166",  # Data Science
    "C108827166",   # Deep Learning
    "C11413529",    # Natural Language Processing
]

PAGE_SIZE = 200  # OpenAlex max per page
_GITHUB_RE = re.compile(r"https?://github\.com/([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)")

# Email for OpenAlex polite pool (10 req/s vs anonymous 1 req/s)
OPENALEX_EMAIL = os.getenv("OPENALEX_EMAIL", "")


# ── OpenAlex helpers ──────────────────────────────────────────────────────────

def _build_headers() -> dict:
    headers = {"User-Agent": "GraphHarvester/1.0"}
    if OPENALEX_EMAIL:
        headers["User-Agent"] = f"GraphHarvester/1.0 (mailto:{OPENALEX_EMAIL})"
    return headers


async def _fetch_page(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    concept_id: str,
    cursor: str,
) -> tuple[list[dict], str | None]:
    """Fetch one cursor page from OpenAlex /works.

    Returns (results, next_cursor). next_cursor is None on the last page.
    """
    params = {
        "filter": f"concepts.id:{concept_id},publication_year:>2019",
        "select": "id,title,authorships,publication_date,doi,open_access,locations,cited_by_count,abstract_inverted_index",
        "sort": "cited_by_count:desc",
        "per_page": PAGE_SIZE,
        "cursor": cursor,
    }
    url = f"{OPENALEX_API}/works"
    async with sem:
        try:
            async with session.get(
                url, params=params,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as resp:
                if resp.status == 429:
                    wait = int(resp.headers.get("Retry-After", 10))
                    logger.warning("OpenAlex 429 — waiting %ds", wait)
                    await asyncio.sleep(wait)
                    return [], cursor
                resp.raise_for_status()
                data = await resp.json()
                results = data.get("results", [])
                next_cursor = data.get("meta", {}).get("next_cursor")
                logger.info(
                    "OpenAlex concept=%s cursor=%s → %d works",
                    concept_id, cursor[:8], len(results)
                )
                return results, next_cursor
        except Exception as exc:
            logger.warning("OpenAlex page failed: %s", exc)
            return [], None


def _reconstruct_abstract(inverted_index: dict | None) -> str:
    """Reconstruct abstract text from OpenAlex's inverted index format."""
    if not inverted_index:
        return ""
    # inverted_index: {"word": [position, ...], ...}
    positions: list[tuple[int, str]] = []
    for word, pos_list in inverted_index.items():
        for pos in pos_list:
            positions.append((pos, word))
    positions.sort(key=lambda x: x[0])
    return " ".join(w for _, w in positions)


def _extract_github_url(paper: dict) -> str | None:
    """Look for a GitHub URL in locations or abstract."""
    # Check open-access landing page URLs
    for loc in paper.get("locations", []):
        url = loc.get("landing_page_url", "") or loc.get("pdf_url", "") or ""
        m = _GITHUB_RE.search(url)
        if m:
            return f"https://github.com/{m.group(1).rstrip('/')}"

    # Check abstract text
    abstract = _reconstruct_abstract(paper.get("abstract_inverted_index"))
    m = _GITHUB_RE.search(abstract)
    if m:
        return f"https://github.com/{m.group(1).rstrip('/')}"

    return None


def _parse_github_repo(url: str) -> tuple[str, str] | None:
    m = re.match(r"https?://github\.com/([^/]+)/([^/?#]+)", url)
    if m:
        return m.group(1), m.group(2).rstrip(".git")
    return None


async def _fetch_github_stars(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    owner: str,
    repo: str,
) -> int | None:
    url = f"{GITHUB_API}/repos/{owner}/{repo}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    async with sem:
        try:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as resp:
                if resp.status in (404, 451):
                    return None
                resp.raise_for_status()
                return (await resp.json()).get("stargazers_count")
        except Exception:
            return None


def _build_record(paper: dict, github_url: str | None, stars: int | None) -> dict:
    """Convert an OpenAlex work into the canonical RESEARCH_PAPER schema."""
    authors = [
        a.get("author", {}).get("display_name", "")
        for a in paper.get("authorships", [])
    ]

    raw_date = paper.get("publication_date", "")
    try:
        dt = datetime.fromisoformat(raw_date)
        published_iso = dt.replace(tzinfo=timezone.utc).isoformat() if not dt.tzinfo else dt.isoformat()
    except (ValueError, TypeError):
        published_iso = None

    # Best available paper URL: DOI > OpenAlex ID
    doi = paper.get("doi") or ""
    paper_url = doi if doi.startswith("http") else (f"https://doi.org/{doi}" if doi else paper.get("id", ""))

    return {
        "schemaVersion": "1.0",
        "recordType": "RESEARCH_PAPER",
        "source": {
            "name": "OpenAlex",
            "url": paper.get("id", ""),
        },
        "content": {
            "title": paper.get("title", ""),
            "authors": [a for a in authors if a],
            "paper_url": paper_url,
            "github_url": github_url,
            "github_stars": stars,
            "published_date": published_iso,
            "citation_count": paper.get("cited_by_count"),
        },
        "collectedAt": datetime.now(timezone.utc).isoformat(),
    }


async def _noop() -> None:
    """No-op coroutine used as a placeholder in gather() calls."""
    return None


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(max_papers: int = 1000) -> list[dict]:
    """Collect up to *max_papers* AI/ML papers from OpenAlex with GitHub stars."""
    # OpenAlex allows 1 req/s unauthenticated, 10 req/s in polite pool (email set)
    concurrency = 3 if OPENALEX_EMAIL else 1
    oa_sem = asyncio.Semaphore(concurrency)
    gh_sem = asyncio.Semaphore(5)

    headers = _build_headers()
    papers_per_concept = max(max_papers // len(_AI_CONCEPTS), PAGE_SIZE)

    raw_papers: list[dict] = []
    seen_ids: set[str] = set()

    async with aiohttp.ClientSession(headers=headers) as session:
        # 1. Paginate through each concept until we have enough papers
        for concept_id in _AI_CONCEPTS:
            if len(raw_papers) >= max_papers:
                break
            cursor = "*"  # OpenAlex cursor pagination starts with "*"
            collected_this_concept = 0

            while collected_this_concept < papers_per_concept and len(raw_papers) < max_papers:
                page, cursor = await _fetch_page(session, oa_sem, concept_id, cursor)
                for p in page:
                    pid = p.get("id", "")
                    if pid and pid not in seen_ids:
                        seen_ids.add(pid)
                        raw_papers.append(p)
                        collected_this_concept += 1
                if not cursor or len(page) < PAGE_SIZE:
                    break
                await asyncio.sleep(0.5)  # small delay between pages

        raw_papers = raw_papers[:max_papers]
        logger.info("OpenAlex: %d unique papers fetched", len(raw_papers))

        # 2. Extract GitHub URLs
        github_urls = [_extract_github_url(p) for p in raw_papers]

        # 3. Fetch star counts in parallel (GitHub allows more concurrency)
        star_tasks = []
        for gh_url in github_urls:
            parsed = _parse_github_repo(gh_url) if gh_url else None
            star_tasks.append(
                _fetch_github_stars(session, gh_sem, *parsed) if parsed
                else _noop()
            )
        star_results = await asyncio.gather(*star_tasks, return_exceptions=True)

    # 4. Build canonical records
    records = [
        _build_record(p, gh, s if isinstance(s, int) else None)
        for p, gh, s in zip(raw_papers, github_urls, star_results)
    ]

    # 5. Save
    out_dir = RAW_DIR / "research_papers"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_path = out_dir / f"pwc_{ts}.json"
    out_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    has_github = sum(1 for r in records if r["content"]["github_url"])
    has_stars = sum(1 for r in records if r["content"]["github_stars"] is not None)
    logger.info(
        "OpenAlex: %d papers, %d with GitHub, %d with stars → %s",
        len(records), has_github, has_stars, out_path
    )
    return records


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(run())
    print(f"Collected {len(result)} papers (OpenAlex)")
