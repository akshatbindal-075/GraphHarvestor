"""
src/utils/rate_limiter.py
--------------------------
Token-bucket rate limiter using asyncio for async scrapers.

Usage:
    limiter = RateLimiter(rate=5, per=1.0)  # 5 requests per second
    async with limiter:
        await do_request()
"""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Async token-bucket rate limiter.

    Parameters
    ----------
    rate:
        Number of tokens (requests) allowed per *per* seconds.
    per:
        Time window in seconds.
    """

    def __init__(self, rate: float, per: float = 1.0) -> None:
        self.rate = rate
        self.per = per
        self._tokens = rate
        self._last_check = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume one."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_check
            self._last_check = now
            # Refill tokens proportional to elapsed time
            self._tokens = min(self.rate, self._tokens + elapsed * (self.rate / self.per))
            if self._tokens < 1:
                wait = (1 - self._tokens) * (self.per / self.rate)
                await asyncio.sleep(wait)
                self._tokens = 0
            else:
                self._tokens -= 1

    async def __aenter__(self) -> "RateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *args) -> None:
        pass
