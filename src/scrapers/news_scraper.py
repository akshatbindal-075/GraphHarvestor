"""
src/scrapers/news_scraper.py
-----------------------------
Async news scraper for 5 AI-focused RSS feeds.

Sources:
  1. TechCrunch AI     — https://techcrunch.com/category/artificial-intelligence/feed/
  2. VentureBeat AI    — https://venturebeat.com/ai/feed/
  3. MIT Tech Review   — https://www.technologyreview.com/feed/
  4. The Verge AI      — https://www.theverge.com/rss/ai-artificial-intelligence/index.xml
  5. AI News           — https://artificialintelligence-news.com/feed/

All articles are filtered to <24 hours old BEFORE reaching the LLM extraction stage.
Seen URLs are cached to logs/seen_urls.json to avoid reprocessing.

Engineering decisions:
  - Parses RSS/Atom XML with the stdlib (no feedparser dep) using ET.
  - Falls back to <pubDate> → <updated> → <dc:date> for date extraction.
  - Full article text fetched via a secondary aiohttp GET when the RSS
    description is < 200 chars (summarised feeds).
"""

from __future__ import annotations

import asyncio
import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import aiohttp

from src.config import DEFAULT_SEMAPHORE, RAW_DIR, REQUEST_TIMEOUT
from src.utils.date_parser import normalize_date, is_fresh, mark_seen, was_seen

logger = logging.getLogger(__name__)

NEWS_SOURCES = [
    {
        "name": "TechCrunch AI",
        "feed_url": "https://techcrunch.com/category/artificial-intelligence/feed/",
    },
    {
        "name": "VentureBeat AI",
        "feed_url": "https://venturebeat.com/feed/",
    },
    {
        "name": "MIT Technology Review",
        "feed_url": "https://www.technologyreview.com/feed/",
    },
    {
        "name": "The Verge AI",
        "feed_url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    },
    {
        "name": "AI News",
        "feed_url": "https://artificialintelligence-news.com/feed/",
    },
]

NS_MAP = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "atom": "http://www.w3.org/2005/Atom",
}


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _extract_items(xml_text: str, source_name: str) -> list[dict[str, Any]]:
    """Parse RSS/Atom XML and return raw item dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.error("XML parse error for %s: %s", source_name, exc)
        return []

    items: list[dict] = []
    # RSS 2.0
    for item in root.findall(".//item"):
        title = _text(item.find("title"))
        link = _text(item.find("link"))
        description = _text(item.find("description"))
        pub_date = (
            _text(item.find("pubDate"))
            or _text(item.find("dc:date", NS_MAP))
        )
        content_encoded = _text(item.find("content:encoded", NS_MAP))
        body = content_encoded or description
        items.append({
            "title": title,
            "url": link,
            "body": body,
            "raw_date": pub_date,
            "source": source_name,
        })

    # Atom feeds
    for entry in root.findall("atom:entry", NS_MAP):
        title = _text(entry.find("atom:title", NS_MAP))
        link_el = entry.find("atom:link", NS_MAP)
        link = link_el.attrib.get("href", "") if link_el is not None else ""
        summary = _text(entry.find("atom:summary", NS_MAP))
        updated = _text(entry.find("atom:updated", NS_MAP)) or _text(entry.find("atom:published", NS_MAP))
        content_el = entry.find("atom:content", NS_MAP)
        body = _text(content_el) if content_el is not None else summary
        items.append({
            "title": title,
            "url": link,
            "body": body,
            "raw_date": updated,
            "source": source_name,
        })

    return items


async def _fetch_full_text(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    url: str,
) -> str:
    """Fetch full article text for short-description RSS feeds."""
    async with sem:
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as resp:
                resp.raise_for_status()
                html = await resp.text()
            # Very rough extraction: grab all <p> text
            import re
            paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL | re.I)
            clean = re.sub(r"<[^>]+>", " ", " ".join(paragraphs))
            return " ".join(clean.split())[:5000]
        except Exception:
            return ""


async def _scrape_source(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    source: dict,
    max_age_hours: int,
) -> list[dict]:
    """Fetch one RSS feed, filter fresh items, enrich if needed."""
    feed_url = source["feed_url"]
    source_name = source["name"]
    results: list[dict] = []

    async with sem:
        try:
            async with session.get(
                feed_url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as resp:
                resp.raise_for_status()
                xml_text = await resp.text()
        except Exception as exc:
            logger.error("Failed to fetch feed %s: %s", feed_url, exc)
            return []

    raw_items = _extract_items(xml_text, source_name)
    now_iso = datetime.now(timezone.utc).isoformat()

    enrichment_tasks = []
    fresh_items = []

    for item in raw_items:
        url = item.get("url", "")
        if not url or was_seen(url):
            continue

        pub_iso = normalize_date(item.get("raw_date"))
        # If date is unparseable, treat as potentially fresh and include
        if pub_iso and not is_fresh(pub_iso, max_age_hours):
            continue  # Too old — skip before LLM

        fresh_items.append((item, pub_iso))

        # Enrich short bodies
        if len(item.get("body", "")) < 200:
            enrichment_tasks.append(_fetch_full_text(session, sem, url))
        else:
            enrichment_tasks.append(asyncio.sleep(0, result=item["body"]))

    full_texts = await asyncio.gather(*enrichment_tasks, return_exceptions=True)

    for (item, pub_iso), full_text in zip(fresh_items, full_texts):
        url = item["url"]
        mark_seen(url)
        body = full_text if isinstance(full_text, str) and full_text else item.get("body", "")

        results.append({
            "schemaVersion": "1.0",
            "recordType": "NEWS",
            "source": {"name": item["source"], "url": feed_url},
            "content": {
                "title": item["title"],
                "url": url,
                "body": body[:5000],
                "publishedAt": pub_iso or now_iso,
            },
            "collectedAt": now_iso,
        })

    logger.info("%s: %d fresh articles", source_name, len(results))
    return results


async def run(max_age_hours: int = 24, concurrency: int = DEFAULT_SEMAPHORE) -> list[dict]:
    """Scrape all news sources and return fresh articles (<24 hrs)."""
    sem = asyncio.Semaphore(concurrency)
    headers = {"User-Agent": "GraphHarvester/1.0 News Collector"}

    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = [
            _scrape_source(session, sem, src, max_age_hours)
            for src in NEWS_SOURCES
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_articles: list[dict] = []
    for r in results:
        if isinstance(r, list):
            all_articles.extend(r)
        elif isinstance(r, Exception):
            logger.error("News source failed: %s", r)

    out_dir = RAW_DIR / "news"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_path = out_dir / f"news_{ts}.json"
    out_path.write_text(json.dumps(all_articles, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("News: %d fresh articles total → %s", len(all_articles), out_path)
    return all_articles


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(run())
    print(f"Collected {len(result)} fresh news articles")
