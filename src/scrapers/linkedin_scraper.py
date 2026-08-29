"""
src/scrapers/startup_scraper_playwright.py   ← Phase 5: Anti-bot scraper
---------------------------------------------------------------------------
High-value source: LinkedIn Jobs / Company pages (JS-heavy + anti-bot).

Strategy:
  - Playwright async with full stealth measures:
      * Random user-agent from a pool of real Chrome/Firefox strings
      * Randomised delays (0.5–3s) between every page interaction
      * Realistic viewport (1366x768, 1440x900, 1920x1080 — rotated)
      * Disabling the webdriver flag (`navigator.webdriver = undefined`)
      * Accepting cookies automatically (bypasses cookie banner)
  - Targets LinkedIn's "AI" company search (publicly visible, no login required
    for basic company info), not the login-gated feed.

Anti-bot write-up (for README):
  See README.md → "Anti-Bot Strategy" section.

Note: This scraper collects COMPANY INFO from public LinkedIn pages, which
is legally distinct from bulk-downloading user PII. Still, respect robots.txt
and LinkedIn ToS in production — prefer LinkedIn's official Partner API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from datetime import datetime, timezone
from typing import Any

from playwright.async_api import async_playwright, Page, BrowserContext

from src.config import DEFAULT_SEMAPHORE, RAW_DIR

logger = logging.getLogger(__name__)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
]

_VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1920, "height": 1080},
    {"width": 1280, "height": 800},
]

_LINKEDIN_AI_SEARCH = (
    "https://www.linkedin.com/search/results/companies/"
    "?keywords=artificial%20intelligence&origin=SWITCH_SEARCH_VERTICAL"
)


async def _stealth_context(playwright_instance) -> BrowserContext:
    """Create a stealthed Playwright browser context."""
    ua = random.choice(_USER_AGENTS)
    viewport = random.choice(_VIEWPORTS)

    browser = await playwright_instance.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    context = await browser.new_context(
        user_agent=ua,
        viewport=viewport,
        locale="en-US",
        timezone_id="America/New_York",
        permissions=["geolocation"],
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
        },
    )

    # Spoof navigator.webdriver to undefined
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        window.chrome = { runtime: {} };
    """)
    return context, browser


async def _random_delay(min_s: float = 0.5, max_s: float = 3.0) -> None:
    """Sleep for a random duration to mimic human browsing pace."""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def _scrape_linkedin_company_page(page: Page, url: str) -> dict | None:
    """Extract basic company info from a LinkedIn company page."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await _random_delay(1.0, 2.5)

        # Accept cookie banner if present
        try:
            await page.click("[data-tracking-control-name='cookie-banner-accept']", timeout=3000)
            await _random_delay(0.3, 0.8)
        except Exception:
            pass

        # Extract company name
        name_el = await page.query_selector("h1.org-top-card-summary__title")
        name = (await name_el.inner_text()).strip() if name_el else ""

        # Tagline / industry
        tagline_el = await page.query_selector(".org-top-card-summary__tagline")
        tagline = (await tagline_el.inner_text()).strip() if tagline_el else ""

        # Employee count (visible on public pages)
        emp_el = await page.query_selector("a[href*='currentCompany'] .t-normal")
        emp_text = (await emp_el.inner_text()).strip() if emp_el else ""
        emp_count: int | None = None
        m = re.search(r"([\d,]+)", emp_text.replace(",", ""))
        if m:
            try:
                emp_count = int(m.group(1))
            except ValueError:
                pass

        if not name:
            return None

        return {
            "schemaVersion": "1.0",
            "recordType": "STARTUP",
            "source": {"name": "LinkedIn", "url": url},
            "content": {
                "entityName": name,
                "description": tagline,
                "website": "",
                "data": {"employeeCount": emp_count},
            },
            "collectedAt": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.debug("LinkedIn page failed (%s): %s", url, exc)
        return None


async def run(max_results: int = 100) -> list[dict]:
    """Scrape LinkedIn AI company search results with stealth measures.

    NOTE: This is intentionally capped at 100 to be respectful. For
    production scale, use the LinkedIn Partner API or a proxy network.
    """
    records: list[dict] = []

    async with async_playwright() as pw:
        context, browser = await _stealth_context(pw)
        page = await context.new_page()

        logger.info("LinkedIn stealth scraper starting …")
        try:
            await page.goto(_LINKEDIN_AI_SEARCH, wait_until="domcontentloaded", timeout=45_000)
            await _random_delay(2.0, 4.0)

            # Check if we hit a login wall (common on LinkedIn)
            if "authwall" in page.url or "login" in page.url:
                logger.warning(
                    "LinkedIn redirected to login wall — auth required. "
                    "Consider the LinkedIn Partner API for production use."
                )
                return []

            # Collect company links from search results
            company_links = await page.eval_on_selector_all(
                "a.app-aware-link[href*='/company/']",
                "els => els.map(e => e.href)"
            )
            # Deduplicate and normalise URLs
            seen: set[str] = set()
            clean_links: list[str] = []
            for link in company_links:
                base = re.sub(r"\?.*", "", link.split("?")[0])
                if "linkedin.com/company/" in base and base not in seen:
                    seen.add(base)
                    clean_links.append(base + "/about/")

            logger.info("Found %d company links on LinkedIn search", len(clean_links))

            for link in clean_links[:max_results]:
                await _random_delay(1.5, 3.5)  # polite delay between pages
                rec = await _scrape_linkedin_company_page(page, link)
                if rec:
                    records.append(rec)

        finally:
            await browser.close()

    out_dir = RAW_DIR / "startups"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_path = out_dir / f"linkedin_{ts}.json"
    out_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("LinkedIn: %d companies scraped → %s", len(records), out_path)
    return records


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(run())
    print(f"Collected {len(result)} LinkedIn companies")
