"""
src/output/sheets_writer.py
-----------------------------
Batch-write canonical records to the correct Google Sheets tab.

Column headers exactly match the schema from the design doc:

  Startups          → schemaVersion, recordType, source.name, source.url,
                       content.entityName, content.data.employeeCount, collectedAt
  Products          → schemaVersion, recordType, source.name, source.url,
                       content.startupName, content.pricingModel, collectedAt
  Research Papers   → schemaVersion, recordType, content.title, content.authors,
                       content.paper_url, content.github_url, content.github_stars,
                       content.published_date
  Jobs              → schemaVersion, recordType, content.company, content.date,
                       content.is_remote, content.role_family
  News              → schemaVersion, recordType, content.title, content.summary,
                       content.publishedAt, content.url, content.entities_mentioned
  Entity Mapping Log → raw_name, canonical_name, match_type, confidence_score, resolved

Writes in batches of 500 rows (Sheets quota: 60 write requests/min).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import gspread
from gspread.exceptions import APIError

from src.config import GOOGLE_SHEET_ID, get_sheets_client

logger = logging.getLogger(__name__)

BATCH_SIZE = 500
_WRITE_DELAY_SEC = 1.2   # stay well under 60 req/min

# ── Tab headers — match design-doc schema exactly ─────────────────────────────

_HEADERS: dict[str, list[str]] = {
    "Startups": [
        "schemaVersion",
        "recordType",
        "source.name",
        "source.url",
        "content.entityName",
        "content.data.employeeCount",
        "collectedAt",
    ],
    "Products": [
        "schemaVersion",
        "recordType",
        "source.name",
        "source.url",
        "content.startupName",
        "content.pricingModel",
        "collectedAt",
    ],
    "Research Papers": [
        "schemaVersion",
        "recordType",
        "content.title",
        "content.authors",
        "content.paper_url",
        "content.github_url",
        "content.github_stars",
        "content.published_date",
    ],
    "Jobs": [
        "schemaVersion",
        "recordType",
        "content.company",
        "content.date",
        "content.is_remote",
        "content.role_family",
    ],
    "News": [
        "schemaVersion",
        "recordType",
        "content.title",
        "content.summary",
        "content.publishedAt",
        "content.url",
        "content.entities_mentioned",
    ],
    "Entity Mapping Log": [
        "raw_name",
        "canonical_name",
        "match_type",
        "confidence_score",
        "resolved",
    ],
}


# ── Row flatteners — one per tab ──────────────────────────────────────────────

def _flatten(record: dict[str, Any], tab_name: str) -> list[Any]:
    """Map a canonical record dict to an ordered row matching _HEADERS[tab_name]."""
    c = record.get("content", {})
    s = record.get("source", {})
    d = c.get("data", {})
    sv = record.get("schemaVersion", "1.0")
    rt = record.get("recordType", "")
    ts = record.get("collectedAt", datetime.now(timezone.utc).isoformat())

    if tab_name == "Startups":
        return [
            sv,
            rt,
            s.get("name", ""),
            s.get("url", ""),
            c.get("entityName", ""),
            d.get("employeeCount", "") or "",
            ts,
        ]

    if tab_name == "Products":
        return [
            sv,
            rt,
            s.get("name", ""),
            s.get("url", ""),
            c.get("startupName", "") or c.get("entityName", ""),
            c.get("pricingModel", ""),
            ts,
        ]

    if tab_name == "Research Papers":
        authors = c.get("authors", [])
        return [
            sv,
            rt,
            c.get("title", ""),
            ", ".join(authors) if isinstance(authors, list) else str(authors),
            c.get("paper_url", ""),
            c.get("github_url", "") or "",
            c.get("github_stars", "") or "",
            c.get("published_date", ""),
        ]

    if tab_name == "Jobs":
        return [
            sv,
            rt,
            c.get("company", ""),
            c.get("date", ""),
            c.get("is_remote", ""),
            c.get("role_family", ""),
        ]

    if tab_name == "News":
        entities = c.get("entities_mentioned", [])
        return [
            sv,
            rt,
            c.get("title", ""),
            (c.get("summary", "") or c.get("body", ""))[:300],
            c.get("publishedAt", "") or c.get("published_date", ""),
            c.get("url", "") or s.get("url", ""),
            ", ".join(entities) if isinstance(entities, list) else "",
        ]

    if tab_name == "Entity Mapping Log":
        # record is the flat dict from entity_resolver.to_log_record()
        return [
            record.get("raw_name", ""),
            record.get("canonical_name", ""),
            record.get("match_type", ""),
            record.get("confidence_score", ""),
            record.get("resolved", ""),
        ]

    raise ValueError(f"Unknown tab: {tab_name!r}")


def _record_type_to_tab(record_type: str) -> str:
    return {
        "STARTUP":        "Startups",
        "PRODUCT":        "Products",
        "RESEARCH_PAPER": "Research Papers",
        "JOB":            "Jobs",
        "NEWS":           "News",
        "ENTITY_MAP":     "Entity Mapping Log",
    }.get(record_type.upper(), "")


def _ensure_headers(ws: gspread.Worksheet, tab_name: str) -> None:
    """Write header row if the worksheet is empty or headers changed."""
    expected = _HEADERS.get(tab_name, [])
    try:
        current = ws.row_values(1)
    except Exception:
        current = []
    if current != expected:
        if current:
            # Headers changed — clear and rewrite (data will be re-appended)
            ws.clear()
            logger.info("Cleared [%s] to refresh headers", tab_name)
        ws.append_row(expected, value_input_option="RAW")
        time.sleep(_WRITE_DELAY_SEC)


# ── Public API ────────────────────────────────────────────────────────────────

def write_records(
    records: list[dict[str, Any]],
    record_type: str,
    sheet_id: str | None = None,
) -> int:
    """Write *records* to the correct tab of the Google Sheet.

    Parameters
    ----------
    records:
        List of canonical schema dicts (or entity-map dicts for ENTITY_MAP).
    record_type:
        One of: STARTUP, PRODUCT, RESEARCH_PAPER, JOB, NEWS, ENTITY_MAP.
    sheet_id:
        Google Sheet ID. Defaults to GOOGLE_SHEET_ID from config.

    Returns
    -------
    int — number of rows successfully written.
    """
    if not records:
        logger.info("No %s records to write.", record_type)
        return 0

    sheet_id = sheet_id or GOOGLE_SHEET_ID
    if not sheet_id:
        logger.error("GOOGLE_SHEET_ID not set — skipping Sheets write for %s", record_type)
        return 0

    tab_name = _record_type_to_tab(record_type)
    if not tab_name:
        logger.error("Unknown record_type: %s", record_type)
        return 0

    client = get_sheets_client()
    spreadsheet = client.open_by_key(sheet_id)

    try:
        ws = spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(
            title=tab_name,
            rows=50000,
            cols=len(_HEADERS.get(tab_name, [])) + 2,
        )
        logger.info("Created new sheet tab: %s", tab_name)

    _ensure_headers(ws, tab_name)

    rows_written = 0
    batches = [records[i: i + BATCH_SIZE] for i in range(0, len(records), BATCH_SIZE)]

    for batch_num, batch in enumerate(batches, 1):
        rows = []
        for rec in batch:
            try:
                rows.append(_flatten(rec, tab_name))
            except Exception as exc:
                logger.warning("Flatten error: %s", exc)

        if not rows:
            continue

        for attempt in range(3):
            try:
                ws.append_rows(rows, value_input_option="USER_ENTERED")
                rows_written += len(rows)
                logger.info(
                    "Sheets [%s] batch %d/%d: wrote %d rows",
                    tab_name, batch_num, len(batches), len(rows),
                )
                time.sleep(_WRITE_DELAY_SEC)
                break
            except APIError as exc:
                if "429" in str(exc) and attempt < 2:
                    wait = 30 * (attempt + 1)
                    logger.warning("Sheets rate limit — waiting %ds", wait)
                    time.sleep(wait)
                else:
                    logger.error("Sheets write failed: %s", exc)
                    break

    logger.info("Total rows written to [%s]: %d", tab_name, rows_written)
    return rows_written
