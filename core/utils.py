"""
core/utils.py
Shared utility functions used across all platforms and adapters.
"""
import time
import logging
from functools import wraps
from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)


def retry(max_attempts: int = 3, delay: float = 2.0, exceptions=(Exception,)):
    """
    Generic retry decorator. Works for any adapter (web, mobile, etc.).
    Reads max_attempts and delay from config if not overridden.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    logger.warning(
                        "⚠️ Attempt %d/%d failed for '%s': %s",
                        attempt, max_attempts, func.__name__, e
                    )
                    if attempt < max_attempts:
                        time.sleep(delay)
            raise last_exc
        return wrapper
    return decorator


def setup_logging(level: str = "INFO"):
    """Centralized logging setup for the entire project."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


def is_playwright_timeout(e: Exception) -> bool:
    return isinstance(e, (PlaywrightTimeoutError, PlaywrightError))


def format_summary(stats: dict, label: str) -> str:
    """Generates a clean test summary string."""
    lines = [
        "\n" + "=" * 80,
        f"📊 TEST SUMMARY: {label.upper()}",
        "=" * 80,
    ]
    for entry in stats.get("log", []):
        lines.append(entry)
    lines.append("=" * 80)
    total = stats.get("passed", 0) + stats.get("failed", 0)
    lines.append(
        f"TOTAL: {total} | PASSED: {stats.get('passed', 0)} | FAILED: {stats.get('failed', 0)}"
    )
    lines.append("=" * 80 + "\n")
    return "\n".join(lines)
