"""
config/settings.py
All project-wide configuration loaded from environment variables / .env.
ZERO hardcoded values — everything comes from here.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Browser ────────────────────────────────────────────────────────────────
HEADLESS: bool = os.getenv("HEADLESS", "false").lower() in ("1", "true", "yes")
ACTION_TIMEOUT_MS: int = int(os.getenv("ACTION_TIMEOUT_MS", "15000"))
NAVIGATION_TIMEOUT_MS: int = int(os.getenv("NAVIGATION_TIMEOUT_MS", "30000"))
DEFAULT_SCROLL_COUNT: int = int(os.getenv("DEFAULT_SCROLL_COUNT", "20"))
WAIT_TIMEOUT_MS: int = int(os.getenv("WAIT_TIMEOUT_MS", "3000"))

# ── Retry ───────────────────────────────────────────────────────────────────
RETRY_MAX_ATTEMPTS: int = int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))
RETRY_DELAY_SECONDS: float = float(os.getenv("RETRY_DELAY_SECONDS", "2.0"))

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR: str = os.path.join(BASE_DIR, "data")
SCREENSHOTS_DIR: str = os.path.join(DATA_DIR, "screenshots")
VIDEOS_DIR: str = os.path.join(DATA_DIR, "videos")
LOGS_DIR: str = os.path.join(DATA_DIR, "logs")
MANUAL_LOCATORS_FILE: str = os.path.join(DATA_DIR, "locators_manual.json")
RECORDED_ELEMENTS_FILE: str = os.path.join(DATA_DIR, "recorded_elements.json")
SITES_FILE: str = os.path.join(os.path.dirname(__file__), "sites.json")
PLAYWRIGHT_CONFIG_FILE: str = os.path.join(os.path.dirname(__file__), "playwright.config.json")
FLOWS_DIR: str = os.path.join(BASE_DIR, "flows")

# ── Auth registry ─────────────────────────────────────────────────────────────
def get_auth_registry() -> dict:
    """Builds HTTP-auth registry from environment variables. Extensible — add new domains below."""
    registry = {}
    u = os.getenv("STAGING2_USERNAME")
    p = os.getenv("STAGING2_PASSWORD")
    if u and p:
        registry["staging2.justdial.com"] = {"username": u, "password": p}
    return registry
