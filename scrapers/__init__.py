"""
scrapers/__init__.py
--------------------
Public API for the scrapers package.
"""

from scrapers.base import BaseScraper
from scrapers.http_scraper import HttpScraper
from scrapers.playwright_scraper import PlaywrightScraper

__all__ = ["BaseScraper", "HttpScraper", "PlaywrightScraper"]
