"""
scrapers/base.py
----------------
Abstract base class that every scraper must implement.

A scraper has two responsibilities:
1. ``fetch(url)``  – retrieve raw content from the URL.
2. ``parse(html)`` – extract a list of meaningful text chunks from the raw content.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseScraper(ABC):
    """Abstract base for all GraphHarvestor scrapers."""

    @abstractmethod
    def fetch(self, url: str) -> str:
        """Retrieve raw content from *url*.

        Parameters
        ----------
        url:
            The URL to fetch.

        Returns
        -------
        str
            Raw page content (HTML, JSON, plain text, …).
        """

    @abstractmethod
    def parse(self, raw: str) -> list[str]:
        """Extract clean text paragraphs from *raw* content.

        Parameters
        ----------
        raw:
            Content returned by :meth:`fetch`.

        Returns
        -------
        list[str]
            A list of non-empty cleaned text chunks ready for LLM processing.
        """

    def scrape(self, url: str) -> list[str]:
        """Convenience method: fetch then parse in one call.

        Parameters
        ----------
        url:
            The URL to scrape.

        Returns
        -------
        list[str]
            Parsed text chunks.
        """
        raw = self.fetch(url)
        return self.parse(raw)
