"""
src/pipeline/orchestrator.py
-----------------------------
Master orchestrator: ties together all scraping, LLM extraction,
entity resolution, and Google Sheets output.

Run as:
    python -m src.pipeline.orchestrator

Stages:
  1. Scrape (bulk: arxiv, paperswithcode, startups, products)
  2. Scrape (fresh: news, jobs) — filtered to <24hrs
  3. LLM extraction on raw text that wasn't already schema-structured
  4. Entity resolution across all extracted entity names
  5. Write all results to Google Sheets
  6. Print summary counts

Logging: structured JSON to logs/pipeline.jsonl via LLM extractor.
         human-readable to stdout via Python logging.
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.config
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import LOGS_DIR, RAW_DIR

# ── Logging setup (must happen before other imports that use logging) ──────────
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("orchestrator")

# ── Lazy imports (heavy; only load when orchestrator runs) ────────────────────
from src.scrapers import arxiv_scraper, paperswithcode_scraper
from src.scrapers import startup_scraper, product_scraper
from src.scrapers import news_scraper, jobs_scraper
from src.llm.extractor import extract
from src.resolution.entity_resolver import EntityResolver
from src.output.sheets_writer import write_records


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_entity_names(records: list[dict]) -> list[str]:
    """Pull all entity names from a mixed list of records."""
    names: list[str] = []
    for r in records:
        c = r.get("content", {})
        for field in ("entityName", "startupName", "company", "title"):
            val = c.get(field)
            if val and isinstance(val, str):
                names.append(val)
        for ent in c.get("entities_mentioned", []):
            if isinstance(ent, str):
                names.append(ent)
    return [n for n in names if n.strip()]


def _count_summary(label: str, records: list[dict]) -> None:
    logger.info("%-25s %d records", f"[{label}]", len(records))


def _load_latest_raw(category_dir: str) -> list[dict]:
    """Fallback: load the most recent non-empty JSON file from raw/<category_dir>/."""
    cat_path = RAW_DIR / category_dir
    if not cat_path.exists():
        return []
    files = sorted(cat_path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, list) and len(data) > 0:
                logger.info("Loaded %d cached records from %s", len(data), f)
                return data
        except Exception as exc:
            logger.warning("Failed to load raw cache from %s: %s", f, exc)
    return []


# ── Pipeline stages ───────────────────────────────────────────────────────────

async def stage_scrape_bulk() -> dict[str, list[dict]]:
    """Run all bulk scrapers concurrently."""
    logger.info("=" * 60)
    logger.info("STAGE 1 — Bulk Scraping")
    logger.info("=" * 60)

    arxiv_task = asyncio.create_task(arxiv_scraper.run())
    pwc_task = asyncio.create_task(paperswithcode_scraper.run())
    startup_task = asyncio.create_task(startup_scraper.run())
    product_task = asyncio.create_task(product_scraper.run())

    arxiv_records, pwc_records, startup_records, product_records = await asyncio.gather(
        arxiv_task, pwc_task, startup_task, product_task,
        return_exceptions=True,
    )

    def _safe(r, label):
        if isinstance(r, Exception):
            logger.error("%s scraper failed: %s", label, r)
            return []
        return r

    papers = _safe(arxiv_records, "ArXiv") + _safe(pwc_records, "PwC")
    if not papers:
        papers = _load_latest_raw("research_papers")

    startups = _safe(startup_records, "YC Startups")
    if not startups:
        startups = _load_latest_raw("startups")

    products = _safe(product_records, "Products")
    if not products:
        products = _load_latest_raw("products")

    return {
        "RESEARCH_PAPER": papers,
        "STARTUP": startups,
        "PRODUCT": products,
    }


async def stage_scrape_fresh() -> dict[str, list[dict]]:
    """Run fresh-content scrapers (news + jobs)."""
    logger.info("=" * 60)
    logger.info("STAGE 2 — Fresh Content Scraping (<24hrs)")
    logger.info("=" * 60)

    news_task = asyncio.create_task(news_scraper.run())
    jobs_task = asyncio.create_task(jobs_scraper.run())

    news_records, jobs_records = await asyncio.gather(
        news_task, jobs_task, return_exceptions=True
    )

    def _safe(r, label):
        if isinstance(r, Exception):
            logger.error("%s scraper failed: %s", label, r)
            return []
        return r

    news = _safe(news_records, "News")
    if not news:
        news = _load_latest_raw("news")

    jobs = _safe(jobs_records, "Jobs")
    if not jobs:
        jobs = _load_latest_raw("jobs")

    return {
        "NEWS": news,
        "JOB": jobs,
    }


def stage_llm_extraction(
    bulk: dict[str, list[dict]],
    fresh: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    """Run LLM extraction on raw text records that lack structured content.

    Records from the API scrapers (ArXiv, PwC, Remotive) are already
    schema-structured. Only news body text needs LLM extraction.
    """
    logger.info("=" * 60)
    logger.info("STAGE 3 — LLM Extraction")
    logger.info("=" * 60)

    extracted: dict[str, list[dict]] = {**bulk, **fresh}

    # News articles: extract structured fields from body text
    enriched_news: list[dict] = []
    for article in fresh.get("NEWS", []):
        body = article.get("content", {}).get("body", "")
        url = article.get("content", {}).get("url", "")
        if not body:
            enriched_news.append(article)
            continue
        result = extract(body, "NEWS", source_url=url)
        if result:
            enriched_news.append(result)
        else:
            enriched_news.append(article)  # fallback to raw

    extracted["NEWS"] = enriched_news

    # Jobs: extract role/company from raw text when not already structured
    # (Remotive/RemoteOK return structured data — only HN needs LLM)
    enriched_jobs: list[dict] = []
    for job in fresh.get("JOB", []):
        src = job.get("source", {}).get("name", "")
        if src == "HN Who's Hiring" and not job.get("content", {}).get("company"):
            url = job.get("source", {}).get("url", "")
            raw_text = str(job.get("content", {}))
            result = extract(raw_text, "JOB", source_url=url)
            enriched_jobs.append(result or job)
        else:
            enriched_jobs.append(job)

    extracted["JOB"] = enriched_jobs
    return extracted


def stage_entity_resolution(all_records: dict[str, list[dict]]) -> list[dict]:
    """Resolve all entity names across every record type.

    Returns a flat list of entity-map records for the Entity Mapping Log tab.
    """
    logger.info("=" * 60)
    logger.info("STAGE 4 — Entity Resolution")
    logger.info("=" * 60)

    resolver = EntityResolver()
    all_names: list[str] = []
    for records in all_records.values():
        all_names.extend(_extract_entity_names(records))

    unique_names = list(dict.fromkeys(all_names))  # preserve order, deduplicate
    logger.info("Resolving %d unique entity names …", len(unique_names))

    results = resolver.resolve_batch(unique_names)
    log_records = [resolver.to_log_record(r) for r in results]

    matched = sum(1 for r in results if r.canonical_name is not None)
    logger.info(
        "Entity resolution: %d/%d matched (threshold=%.0f%%)",
        matched, len(results), resolver.threshold
    )
    return log_records


def stage_write_sheets(
    all_records: dict[str, list[dict]],
    entity_map: list[dict],
) -> dict[str, int]:
    """Write all record types to Google Sheets. Returns {tab: rows_written}."""
    logger.info("=" * 60)
    logger.info("STAGE 5 — Writing to Google Sheets")
    logger.info("=" * 60)

    counts: dict[str, int] = {}
    for record_type, records in all_records.items():
        if records:
            n = write_records(records, record_type)
            counts[record_type] = n

    if entity_map:
        n = write_records(entity_map, "ENTITY_MAP")
        counts["ENTITY_MAP"] = n

    return counts


# ── Main entry point ──────────────────────────────────────────────────────────

async def run_pipeline() -> None:
    t_start = time.monotonic()
    logger.info("GraphHarvester pipeline starting — %s", datetime.now(timezone.utc).isoformat())

    # Stage 1 + 2: Scrape
    bulk = await stage_scrape_bulk()
    fresh = await stage_scrape_fresh()

    for label, recs in {**bulk, **fresh}.items():
        _count_summary(label, recs)

    # Stage 3: LLM extraction
    all_records = stage_llm_extraction(bulk, fresh)

    # Stage 4: Entity resolution
    entity_map = stage_entity_resolution(all_records)

    # Stage 5: Sheets output
    sheet_counts = stage_write_sheets(all_records, entity_map)

    # Final summary
    elapsed = time.monotonic() - t_start
    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE in %.1fs", elapsed)
    logger.info("=" * 60)
    total = 0
    for record_type, records in all_records.items():
        n = len(records)
        total += n
        logger.info("  %-20s %5d records  (Sheets: %d rows)", record_type, n, sheet_counts.get(record_type, 0))
    logger.info("  %-20s %5d entries", "ENTITY_MAP", len(entity_map))
    logger.info("  TOTAL                %5d records", total)


def main() -> None:
    asyncio.run(run_pipeline())


if __name__ == "__main__":
    main()
