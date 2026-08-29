"""
utils/logger.py
---------------
Centralised Loguru logger configuration.

Usage
-----
    from utils.logger import get_logger
    log = get_logger(__name__)
    log.info("hello {name}", name="world")
"""

from __future__ import annotations

import sys
from loguru import logger as _logger


def _configure(level: str = "INFO") -> None:
    """Set up Loguru with a human-friendly format."""
    _logger.remove()  # Remove the default stderr sink
    _logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> – "
            "<level>{message}</level>"
        ),
        colorize=True,
        backtrace=True,
        diagnose=True,
    )
    _logger.add(
        "output/graphharvestor.log",
        level=level,
        rotation="10 MB",
        retention="7 days",
        compression="gz",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} – {message}",
        encoding="utf-8",
    )


def get_logger(name: str):
    """Return a Loguru logger bound to *name*.

    The logger is initialised once from ``utils.config.settings`` on first
    call; subsequent calls just add a context binding.
    """
    return _logger.bind(name=name)


# Initialise with defaults; pipeline/run.py will call _configure() again
# once it has parsed the CLI/env log level.
try:
    from utils.config import settings as _s
    _configure(_s.log_level)
except Exception:
    _configure("INFO")
