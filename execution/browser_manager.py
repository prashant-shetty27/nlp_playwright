"""
execution/browser_manager.py
Browser lifecycle management — extracted from actions.py.
Uses TestSession to hold state instead of module-level globals.
"""
import os
import json
import logging
from datetime import datetime

from playwright.sync_api import sync_playwright
from execution.session import TestSession
from config import settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def get_system_resolution() -> tuple[int, int]:
    return 1920, 1080


def load_playwright_config() -> dict:
    path = settings.PLAYWRIGHT_CONFIG_FILE
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error("❌ Failed to load playwright.config.json: %s", e)
    return {
        "use": {
            "headless": settings.HEADLESS,
            "actionTimeout": settings.ACTION_TIMEOUT_MS,
            "navigationTimeout": settings.NAVIGATION_TIMEOUT_MS,
        },
        "run": {
            "default_scroll_count": settings.DEFAULT_SCROLL_COUNT,
        },
    }


def get_standard_timeout_ms() -> int:
    cfg = load_playwright_config()
    return int(cfg.get("use", {}).get("actionTimeout", settings.ACTION_TIMEOUT_MS))


def get_default_scroll_count() -> int:
    cfg = load_playwright_config()
    try:
        return int(cfg.get("run", {}).get("default_scroll_count", settings.DEFAULT_SCROLL_COUNT))
    except Exception:
        return settings.DEFAULT_SCROLL_COUNT


# ─────────────────────────────────────────────────────────────────────────────
# BROWSER LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────
def open_browser(session: TestSession | None = None):
    """
    Launches Chromium and returns the Playwright Page object.
    If a TestSession is provided, stores state on it.
    For backward compatibility, also works without a session (uses module-level state).
    """
    full_config = load_playwright_config()
    use = full_config.get("use", {})

    w, h = get_system_resolution()
    logger.info("🖥️ Desktop Resolution: %sx%s.", w, h)

    playwright_instance = sync_playwright().start()
    browser = playwright_instance.chromium.launch(
        headless=use.get("headless", settings.HEADLESS),
        args=["--start-maximized", "--disable-infobars"],
    )
    context = browser.new_context(
        no_viewport=True,
        permissions=use.get("permissions", []),
    )
    context.set_default_timeout(use.get("actionTimeout", settings.ACTION_TIMEOUT_MS))
    page = context.new_page()

    if session is not None:
        session.playwright_instance = playwright_instance
        session.browser = browser
        session.context = context
        session.page = page

    logger.info("🚀 Session Started | Browser Ready")
    return page


def close_browser(page, test_name: str = "test_run", session: TestSession | None = None):
    """
    Closes the browser and handles video file moving.
    """
    video_path = None
    try:
        if page and page.video:
            video_path = page.video.path()
    except Exception as e:
        logger.debug("No video found or error accessing video path: %s", e)

    try:
        if session is not None:
            if session.context:
                session.context.close()
            if session.browser:
                session.browser.close()
            if session.playwright_instance:
                session.playwright_instance.stop()
        # Legacy path — close directly via the page's context/browser if no session
        elif page:
            try:
                page.context.close()
            except Exception:
                pass
    except Exception as e:
        logger.warning("Browser close issue: %s", e)

    if video_path and os.path.exists(video_path):
        completed_dir = os.path.join(settings.VIDEOS_DIR, "completed")
        _ensure_dir(completed_dir)
        new_path = os.path.join(completed_dir, f"run_{test_name}_{_timestamp()}.webm")
        os.rename(video_path, new_path)
        logger.info("🎥 Final Video: %s", new_path)
