"""
scrapers/http_scraper.py
------------------------
HTTP scraper for static (non-JS) pages using ``httpx`` and ``BeautifulSoup``.

Features
--------
- Configurable User-Agent header
- Automatic retries on 5xx / connection errors (via Tenacity)
- robots.txt awareness (opt-in)
- Extracts <p>, <li>, <h1>–<h6> text and deduplicates

Usage
-----
    from scrapers.http_scraper import HttpScraper

    scraper = HttpScraper()
    chunks = scraper.scrape("https://en.wikipedia.org/wiki/Knowledge_graph")
"""

from __future__ import annotations

from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from scrapers.base import BaseScraper
from utils.logger import get_logger
from utils.text import clean_text

log = get_logger(__name__)

_DEFAULT_UA = (
    "Mozilla/5.0 (compatible; GraphHarvestor/1.0; +https://github.com/your-org/GraphHarvestor)"
)
_TEXT_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "td"}
_MIN_CHUNK_LEN = 40  # characters


class HttpScraper(BaseScraper):
    """Fetch static web pages with ``httpx``; parse text with ``BeautifulSoup``."""

    def __init__(
        self,
        user_agent: str = _DEFAULT_UA,
        timeout: float = 20.0,
        respect_robots: bool = True,
    ) -> None:
        self._ua = user_agent
        self._timeout = timeout
        self._respect_robots = respect_robots
        self._robots_cache: dict[str, RobotFileParser] = {}

    # ── robots.txt ───────────────────────────────────────────────────────────

    def _is_allowed(self, url: str) -> bool:
        if not self._respect_robots:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots_cache:
            rp = RobotFileParser()
            rp.set_url(f"{origin}/robots.txt")
            try:
                rp.read()
            except Exception:
                # If robots.txt is unreachable, assume allowed
                self._robots_cache[origin] = None  # type: ignore[assignment]
                return True
            self._robots_cache[origin] = rp
        rp = self._robots_cache[origin]
        return rp is None or rp.can_fetch(self._ua, url)

    # ── Fetch ────────────────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        reraise=True,
    )
    def fetch(self, url: str) -> str:
        """Fetch *url* and return raw HTML."""
        if not self._is_allowed(url):
            log.warning("robots.txt disallows {url}", url=url)
            return ""

        log.info("Fetching {url}", url=url)
        with httpx.Client(
            headers={"User-Agent": self._ua},
            follow_redirects=True,
            timeout=self._timeout,
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            log.debug("Response {status} ({n} bytes)", status=response.status_code, n=len(response.content))
            return response.text

    # ── Parse ────────────────────────────────────────────────────────────────

    def parse(self, raw: str) -> list[str]:
        """Extract clean text chunks from HTML."""
        if not raw:
            return []
        soup = BeautifulSoup(raw, "lxml")

        # Remove boilerplate tags
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        chunks: list[str] = []
        seen: set[str] = set()
        for tag in soup.find_all(_TEXT_TAGS):
            text = clean_text(tag.get_text(separator=" "))
            if len(text) >= _MIN_CHUNK_LEN and text not in seen:
                seen.add(text)
                chunks.append(text)

        log.debug("Parsed {n} text chunks", n=len(chunks))
        return chunks
