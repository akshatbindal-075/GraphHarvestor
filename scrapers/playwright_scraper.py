"""
scrapers/playwright_scraper.py
------------------------------
Async scraper for JavaScript-rendered pages using Microsoft Playwright.

Use this when ``HttpScraper`` returns empty or incomplete content because
the target page relies on client-side rendering.

Usage (sync wrapper)
--------------------
    from scrapers.playwright_scraper import PlaywrightScraper

    scraper = PlaywrightScraper()
    chunks = scraper.scrape("https://example.com/spa-page")

Usage (async)
-------------
    import asyncio
    scraper = PlaywrightScraper()
    chunks = asyncio.run(scraper.async_scrape("https://example.com/spa-page"))
"""

from __future__ import annotations

import asyncio

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, Page

from scrapers.base import BaseScraper
from utils.logger import get_logger
from utils.text import clean_text

log = get_logger(__name__)

_TEXT_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}
_MIN_CHUNK_LEN = 40


class PlaywrightScraper(BaseScraper):
    """Headless-browser scraper for JS-heavy pages (async-first)."""

    def __init__(
        self,
        browser_type: str = "chromium",
        headless: bool = True,
        wait_until: str = "networkidle",
        timeout_ms: int = 30_000,
    ) -> None:
        self._browser_type = browser_type
        self._headless = headless
        self._wait_until = wait_until
        self._timeout_ms = timeout_ms

    # ── Async core ───────────────────────────────────────────────────────────

    async def _fetch_async(self, url: str) -> str:
        async with async_playwright() as pw:
            browser_factory = getattr(pw, self._browser_type)
            browser: Browser = await browser_factory.launch(headless=self._headless)
            try:
                page: Page = await browser.new_page()
                log.info("Playwright fetching {url}", url=url)
                await page.goto(url, wait_until=self._wait_until, timeout=self._timeout_ms)
                html = await page.content()
                log.debug("Playwright got {n} bytes", n=len(html))
                return html
            finally:
                await browser.close()

    async def async_scrape(self, url: str) -> list[str]:
        """Async convenience: fetch + parse in one call."""
        html = await self._fetch_async(url)
        return self.parse(html)

    # ── BaseScraper interface (sync wrappers) ────────────────────────────────

    def fetch(self, url: str) -> str:
        """Synchronous wrapper around :meth:`_fetch_async`."""
        return asyncio.run(self._fetch_async(url))

    def parse(self, raw: str) -> list[str]:
        """Extract clean text chunks from rendered HTML."""
        if not raw:
            return []
        soup = BeautifulSoup(raw, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        chunks: list[str] = []
        seen: set[str] = set()
        for tag in soup.find_all(_TEXT_TAGS):
            text = clean_text(tag.get_text(separator=" "))
            if len(text) >= _MIN_CHUNK_LEN and text not in seen:
                seen.add(text)
                chunks.append(text)

        log.debug("Playwright parsed {n} chunks", n=len(chunks))
        return chunks
