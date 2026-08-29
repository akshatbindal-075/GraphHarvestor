"""
src/scrapers/jobs_scraper.py
-----------------------------
Async jobs scraper for AI/tech job boards.

Sources:
  1. Remotive API        — https://remotive.com/api/remote-jobs  (JSON API, free)
  2. We Work Remotely    — https://weworkremotely.com/remote-jobs.rss (RSS feed)
  3. RemoteOK API        — https://remoteok.com/api  (JSON API, free)
  4. Arbeitnow API       — https://arbeitnow.com/api/job-board-api (JSON API, free)
  5. Jobspresso RSS      — https://jobspresso.co/feed/ (RSS feed)
  6. HN Who's Hiring     — Algolia API, hiring thread extraction

Filters by publication date and infers role family from job title.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import aiohttp

from src.config import DEFAULT_SEMAPHORE, RAW_DIR, REQUEST_TIMEOUT
from src.utils.date_parser import normalize_date, is_fresh, mark_seen, was_seen

logger = logging.getLogger(__name__)

# ── Role family inference ─────────────────────────────────────────────────────
_ROLE_PATTERNS = [
    (re.compile(r"machine learning|ml engineer|mlops", re.I), "ML Engineering"),
    (re.compile(r"data scientist|data science", re.I), "Data Science"),
    (re.compile(r"research scientist|research engineer|ai researcher", re.I), "AI Research"),
    (re.compile(r"data engineer|data platform|etl|pipeline", re.I), "Data Engineering"),
    (re.compile(r"llm|large language|prompt engineer|genai|generative ai", re.I), "LLM/GenAI"),
    (re.compile(r"software engineer|swe|backend|frontend|fullstack|full.stack", re.I), "Software Engineering"),
    (re.compile(r"product manager|pm |head of product", re.I), "Product"),
    (re.compile(r"devops|sre|platform|infrastructure|cloud", re.I), "DevOps/Infra"),
    (re.compile(r"designer|ux|ui |design lead", re.I), "Design"),
    (re.compile(r"sales|account executive|ae |business development", re.I), "Sales/BD"),
]


def _infer_role_family(title: str) -> str:
    for pattern, family in _ROLE_PATTERNS:
        if pattern.search(title):
            return family
    return "Software Engineering"


def _is_remote(text: str) -> bool:
    return bool(re.search(r"\bremote\b|\banywhere\b|work from home|wfh", text, re.I))


def _build_job_record(
    company: str,
    title: str,
    url: str,
    date_iso: str | None,
    source_name: str,
    is_remote_flag: bool | None = None,
) -> dict:
    remote = is_remote_flag if is_remote_flag is not None else _is_remote(f"{title} {company}")
    return {
        "schemaVersion": "1.0",
        "recordType": "JOB",
        "source": {"name": source_name, "url": url},
        "content": {
            "company": company or "Stealth / Confidential",
            "title": title,
            "date": date_iso or datetime.now(timezone.utc).isoformat(),
            "is_remote": remote,
            "role_family": _infer_role_family(title),
            "url": url,
        },
        "collectedAt": datetime.now(timezone.utc).isoformat(),
    }


# ── Source 1: Remotive API ────────────────────────────────────────────────────
async def _scrape_remotive(
    session: aiohttp.ClientSession, sem: asyncio.Semaphore, max_age_hours: int
) -> list[dict]:
    url = "https://remotive.com/api/remote-jobs"
    params = {"category": "software-dev", "limit": 500}
    async with sem:
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as r:
                r.raise_for_status()
                data = await r.json()
        except Exception as exc:
            logger.error("Remotive API: %s", exc)
            return []

    records = []
    for job in data.get("jobs", []):
        job_url = job.get("url", "")
        if was_seen(job_url):
            continue
        pub_iso = normalize_date(job.get("publication_date", ""))
        if pub_iso and not is_fresh(pub_iso, max_age_hours):
            continue
        mark_seen(job_url)
        records.append(_build_job_record(
            company=job.get("company_name", ""),
            title=job.get("title", ""),
            url=job_url,
            date_iso=pub_iso,
            source_name="Remotive",
            is_remote_flag=True,
        ))
    logger.info("Remotive: %d jobs", len(records))
    return records


# ── Source 2: We Work Remotely RSS ───────────────────────────────────────────
async def _scrape_wwr(
    session: aiohttp.ClientSession, sem: asyncio.Semaphore, max_age_hours: int
) -> list[dict]:
    feed_url = "https://weworkremotely.com/remote-jobs.rss"
    async with sem:
        try:
            async with session.get(feed_url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as r:
                r.raise_for_status()
                xml_text = await r.text()
        except Exception as exc:
            logger.error("WWR RSS: %s", exc)
            return []

    records = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    for item in root.findall(".//item"):
        link = (item.find("link").text or "").strip() if item.find("link") is not None else ""
        if was_seen(link):
            continue
        title_el = item.find("title")
        title_text = (title_el.text or "").strip() if title_el is not None else ""
        pub_el = item.find("pubDate")
        pub_iso = normalize_date((pub_el.text or "").strip() if pub_el is not None else "")
        if pub_iso and not is_fresh(pub_iso, max_age_hours):
            continue
        parts = title_text.split(":", 1)
        company = parts[0].strip() if len(parts) > 1 else ""
        job_title = parts[1].strip() if len(parts) > 1 else title_text
        mark_seen(link)
        records.append(_build_job_record(
            company=company, title=job_title, url=link,
            date_iso=pub_iso, source_name="We Work Remotely", is_remote_flag=True
        ))
    logger.info("We Work Remotely: %d jobs", len(records))
    return records


# ── Source 3: RemoteOK API ────────────────────────────────────────────────────
async def _scrape_remoteok(
    session: aiohttp.ClientSession, sem: asyncio.Semaphore, max_age_hours: int
) -> list[dict]:
    url = "https://remoteok.com/api"
    async with sem:
        try:
            async with session.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as r:
                r.raise_for_status()
                data = await r.json()
        except Exception as exc:
            logger.error("RemoteOK API: %s", exc)
            return []

    records = []
    for job in data[1:]:  # first item is a legal notice
        if not isinstance(job, dict):
            continue
        job_url = job.get("url", "") or f"https://remoteok.com/remote-jobs/{job.get('id','')}"
        if was_seen(job_url):
            continue
        epoch = job.get("date", 0)
        try:
            dt = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
            pub_iso = dt.isoformat()
        except Exception:
            pub_iso = None
        if pub_iso and not is_fresh(pub_iso, max_age_hours):
            continue
        mark_seen(job_url)
        records.append(_build_job_record(
            company=job.get("company", ""),
            title=job.get("position", ""),
            url=job_url,
            date_iso=pub_iso,
            source_name="RemoteOK",
            is_remote_flag=True,
        ))
    logger.info("RemoteOK: %d jobs", len(records))
    return records


# ── Source 4: Arbeitnow API ──────────────────────────────────────────────────
async def _scrape_arbeitnow(
    session: aiohttp.ClientSession, sem: asyncio.Semaphore, max_age_hours: int
) -> list[dict]:
    url = "https://arbeitnow.com/api/job-board-api"
    async with sem:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as r:
                r.raise_for_status()
                data = await r.json()
        except Exception as exc:
            logger.error("Arbeitnow API: %s", exc)
            return []

    records = []
    for job in data.get("data", []):
        job_url = job.get("url", "")
        if was_seen(job_url):
            continue
        created_at = job.get("created_at", 0)
        try:
            dt = datetime.fromtimestamp(int(created_at), tz=timezone.utc)
            pub_iso = dt.isoformat()
        except Exception:
            pub_iso = None
        if pub_iso and not is_fresh(pub_iso, max_age_hours):
            continue
        mark_seen(job_url)
        records.append(_build_job_record(
            company=job.get("company_name", ""),
            title=job.get("title", ""),
            url=job_url,
            date_iso=pub_iso,
            source_name="Arbeitnow",
            is_remote_flag=job.get("remote", True),
        ))
    logger.info("Arbeitnow: %d jobs", len(records))
    return records


# ── Source 5: Jobspresso RSS ─────────────────────────────────────────────────
async def _scrape_jobspresso(
    session: aiohttp.ClientSession, sem: asyncio.Semaphore, max_age_hours: int
) -> list[dict]:
    feed_url = "https://jobspresso.co/feed/"
    async with sem:
        try:
            async with session.get(
                feed_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as r:
                r.raise_for_status()
                xml_text = await r.text()
        except Exception as exc:
            logger.error("Jobspresso RSS: %s", exc)
            return []

    records = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    for item in root.findall(".//item"):
        link = (item.find("link").text or "").strip() if item.find("link") is not None else ""
        if was_seen(link):
            continue
        title_el = item.find("title")
        title_text = (title_el.text or "").strip() if title_el is not None else ""
        pub_el = item.find("pubDate")
        pub_iso = normalize_date((pub_el.text or "").strip() if pub_el is not None else "")
        if pub_iso and not is_fresh(pub_iso, max_age_hours):
            continue
        parts = title_text.split("at", 1)
        job_title = parts[0].strip() if parts else title_text
        company = parts[1].strip() if len(parts) > 1 else ""
        mark_seen(link)
        records.append(_build_job_record(
            company=company, title=job_title, url=link,
            date_iso=pub_iso, source_name="Jobspresso", is_remote_flag=True
        ))
    logger.info("Jobspresso: %d jobs", len(records))
    return records


# ── Source 6: HN Who's Hiring (Algolia) ──────────────────────────────────────
async def _scrape_hn_hiring(
    session: aiohttp.ClientSession, sem: asyncio.Semaphore, max_age_hours: int
) -> list[dict]:
    search_url = "https://hn.algolia.com/api/v1/search"
    params = {
        "query": "hiring ML AI remote",
        "tags": "comment",
        "hitsPerPage": 100,
    }
    async with sem:
        try:
            async with session.get(search_url, params=params, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as r:
                r.raise_for_status()
                data = await r.json()
        except Exception as exc:
            logger.error("HN hiring: %s", exc)
            return []

    records = []
    for hit in data.get("hits", []):
        comment_text: str = hit.get("comment_text", "") or ""
        url = f"https://news.ycombinator.com/item?id={hit.get('objectID','')}"
        if was_seen(url) or not comment_text:
            continue
        first_line = comment_text.split("\n")[0]
        parts = [p.strip() for p in first_line.split("|")]
        company = parts[0] if parts else ""
        title = parts[1] if len(parts) > 1 else "Software Engineer"
        created_at = hit.get("created_at", "")
        pub_iso = normalize_date(created_at)
        if pub_iso and not is_fresh(pub_iso, max_age_hours):
            continue
        mark_seen(url)
        records.append(_build_job_record(
            company=company, title=title, url=url,
            date_iso=pub_iso, source_name="HN Who's Hiring"
        ))
    logger.info("HN hiring: %d jobs", len(records))
    return records


# ── Main ──────────────────────────────────────────────────────────────────────
async def run(max_age_hours: int = 720, concurrency: int = DEFAULT_SEMAPHORE) -> list[dict]:
    """Collect fresh job listings (default within 30 days) from all sources."""
    sem = asyncio.Semaphore(concurrency)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GraphHarvester/1.0"}

    async with aiohttp.ClientSession(headers=headers) as session:
        results = await asyncio.gather(
            _scrape_remotive(session, sem, max_age_hours),
            _scrape_wwr(session, sem, max_age_hours),
            _scrape_remoteok(session, sem, max_age_hours),
            _scrape_arbeitnow(session, sem, max_age_hours),
            _scrape_jobspresso(session, sem, max_age_hours),
            _scrape_hn_hiring(session, sem, max_age_hours),
            return_exceptions=True,
        )

    all_jobs: list[dict] = []
    for r in results:
        if isinstance(r, list):
            all_jobs.extend(r)

    out_dir = RAW_DIR / "jobs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_path = out_dir / f"jobs_{ts}.json"
    out_path.write_text(json.dumps(all_jobs, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("Jobs: %d total listings → %s", len(all_jobs), out_path)
    return all_jobs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(run())
    print(f"Collected {len(result)} job listings")
