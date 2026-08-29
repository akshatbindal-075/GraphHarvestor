"""
src/scrapers/arxiv_scraper.py
------------------------------
Async scraper for the official ArXiv Atom API.

Uses NO HTML scraping — calls export.arxiv.org/api/query directly.
Paginates in batches of 100 until ARXIV_MAX_RESULTS papers are collected.
Saves raw output to raw/research_papers/arxiv_<timestamp>.json.

Decision: XML parsed with xml.etree.ElementTree (stdlib, no extra dep).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from src.config import (
    ARXIV_MAX_RESULTS,
    ARXIV_QUERY,
    DEFAULT_SEMAPHORE,
    RAW_DIR,
    REQUEST_TIMEOUT,
)

logger = logging.getLogger(__name__)

ARXIV_API = "https://export.arxiv.org/api/query"
BATCH_SIZE = 100  # ArXiv API max per request
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}


def _parse_entry(entry: ET.Element) -> dict:
    """Parse a single <entry> element into a structured dict."""
    def text(tag: str, ns_key: str = "atom") -> str:
        el = entry.find(f"{ns_key}:{tag}", NS)
        return (el.text or "").strip() if el is not None else ""

    title = text("title").replace("\n", " ")
    summary = text("summary").replace("\n", " ")

    authors = [
        (a.find("atom:name", NS).text or "").strip()
        for a in entry.findall("atom:author", NS)
        if a.find("atom:name", NS) is not None
    ]

    # PDF link
    pdf_url = ""
    for link in entry.findall("atom:link", NS):
        if link.attrib.get("title") == "pdf":
            pdf_url = link.attrib.get("href", "")
            break

    published = text("published")
    # Normalise to ISO-8601
    try:
        dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        published_iso = dt.isoformat()
    except ValueError:
        published_iso = published

    arxiv_id_raw = text("id")
    # id is like http://arxiv.org/abs/2301.00001v1 — strip trailing version
    paper_url = re.sub(r"v\d+$", "", arxiv_id_raw) if arxiv_id_raw else ""

    return {
        "schemaVersion": "1.0",
        "recordType": "RESEARCH_PAPER",
        "content": {
            "title": title,
            "authors": authors,
            "abstract": summary,
            "paper_url": paper_url,
            "pdf_url": pdf_url,
            "github_url": None,       # enriched by paperswithcode_scraper
            "github_stars": None,     # enriched by paperswithcode_scraper
            "published_date": published_iso,
        },
        "collectedAt": datetime.now(timezone.utc).isoformat(),
    }


async def _fetch_batch(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    query: str,
    start: int,
    max_results: int,
) -> list[dict]:
    """Fetch one paginated batch from the ArXiv API."""
    params = {
        "search_query": f"all:{query}",
        "start": start,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    async with sem:
        async with session.get(
            ARXIV_API, params=params, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        ) as resp:
            resp.raise_for_status()
            xml_text = await resp.text()

    root = ET.fromstring(xml_text)
    entries = root.findall("atom:entry", NS)
    logger.info("ArXiv batch start=%d returned %d entries", start, len(entries))
    return [_parse_entry(e) for e in entries]


async def run(
    query: str = ARXIV_QUERY,
    max_results: int = ARXIV_MAX_RESULTS,
    concurrency: int = DEFAULT_SEMAPHORE,
) -> list[dict]:
    """Collect up to *max_results* papers matching *query* from ArXiv.

    Returns the list of structured paper dicts and saves them to disk.
    """
    sem = asyncio.Semaphore(concurrency)
    papers: list[dict] = []

    # Build list of (start, batch_size) tuples for full pagination
    offsets = list(range(0, max_results, BATCH_SIZE))
    batch_sizes = [
        min(BATCH_SIZE, max_results - start) for start in offsets
    ]

    async with aiohttp.ClientSession(
        headers={"User-Agent": "GraphHarvester/1.0 (research; contact@example.com)"}
    ) as session:
        tasks = [
            _fetch_batch(session, sem, query, start, bs)
            for start, bs in zip(offsets, batch_sizes)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, Exception):
            logger.error("ArXiv batch failed: %s", r)
        else:
            papers.extend(r)

    # Deduplicate by paper_url
    seen: set[str] = set()
    unique: list[dict] = []
    for p in papers:
        url = p["content"]["paper_url"]
        if url not in seen:
            seen.add(url)
            unique.append(p)

    # Save raw output
    out_dir = RAW_DIR / "research_papers"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_path = out_dir / f"arxiv_{ts}.json"
    out_path.write_text(json.dumps(unique, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("ArXiv: collected %d papers → %s", len(unique), out_path)
    return unique


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(run())
    print(f"Collected {len(result)} ArXiv papers")
