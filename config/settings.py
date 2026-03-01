"""
config/settings.py
Unified project-wide configuration for ALL platforms.
Loaded entirely from environment variables / .env — ZERO hardcoded values.
Covers: web, mobile, android, ios, hybrid, device adapters.
"""
import os
import json
from dotenv import load_dotenv

load_dotenv()

# ── Active Platform ────────────────────────────────────────────────────────
# Options: web | mobile | android | ios | hybrid
PLATFORM: str = os.getenv("PLATFORM", "web").lower()

# ── Browser (Web / Mobile Web) ─────────────────────────────────────────────
HEADLESS: bool = os.getenv("HEADLESS", "false").lower() in ("1", "true", "yes")
ACTION_TIMEOUT_MS: int = int(os.getenv("ACTION_TIMEOUT_MS", "15000"))
NAVIGATION_TIMEOUT_MS: int = int(os.getenv("NAVIGATION_TIMEOUT_MS", "30000"))
DEFAULT_SCROLL_COUNT: int = int(os.getenv("DEFAULT_SCROLL_COUNT", "20"))
WAIT_TIMEOUT_MS: int = int(os.getenv("WAIT_TIMEOUT_MS", "3000"))

# ── Retry ──────────────────────────────────────────────────────────────────
RETRY_MAX_ATTEMPTS: int = int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))
RETRY_DELAY_SECONDS: float = float(os.getenv("RETRY_DELAY_SECONDS", "2.0"))

# ── Mobile Web Emulation ───────────────────────────────────────────────────
MOBILE_DEVICE_EMULATION: str = os.getenv("MOBILE_DEVICE_EMULATION", "iPhone 14")
MOBILE_BROWSER_TYPE: str = os.getenv("MOBILE_BROWSER_TYPE", "chromium")
MOBILE_USER_AGENT: str = os.getenv("MOBILE_USER_AGENT", "")

# ── Appium (Android / iOS / Hybrid) ───────────────────────────────────────
APPIUM_SERVER_URL: str = os.getenv("APPIUM_SERVER_URL", "http://localhost:4723")

def _load_caps(env_key: str) -> dict:
    """Loads Appium capabilities from a JSON env var or returns empty dict."""
    raw = os.getenv(env_key, "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}

ANDROID_CAPABILITIES: dict = _load_caps("ANDROID_CAPABILITIES")
IOS_CAPABILITIES: dict = _load_caps("IOS_CAPABILITIES")
HYBRID_CAPABILITIES: dict = _load_caps("HYBRID_CAPABILITIES")

# ── Paths ──────────────────────────────────────────────────────────────────
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
PLANS_DIR: str = os.path.join(BASE_DIR, "plans")
SUITES_DIR: str = os.path.join(BASE_DIR, "suites")

# ── Notifications ──────────────────────────────────────────────────────────────
NOTIFY_ON_SLACK: bool = os.getenv("NOTIFY_ON_SLACK", "false").lower() in ("1", "true", "yes")
SLACK_WEBHOOK_URL: str = os.getenv("SLACK_WEBHOOK_URL", "")
SLACK_CHANNEL: str = os.getenv("SLACK_CHANNEL", "#test-results")
SLACK_USERNAME: str = os.getenv("SLACK_USERNAME", "NLP-Playwright Bot")
SLACK_ICON_EMOJI: str = os.getenv("SLACK_ICON_EMOJI", ":robot_face:")

# Email (future — stub ready, SMTP not yet wired)
NOTIFY_ON_EMAIL: bool = os.getenv("NOTIFY_ON_EMAIL", "false").lower() in ("1", "true", "yes")
EMAIL_SMTP_HOST: str = os.getenv("EMAIL_SMTP_HOST", "")
EMAIL_SMTP_PORT: int = int(os.getenv("EMAIL_SMTP_PORT", "587"))
EMAIL_SENDER: str = os.getenv("EMAIL_SENDER", "")
EMAIL_PASSWORD: str = os.getenv("EMAIL_PASSWORD", "")
EMAIL_RECIPIENTS: str = os.getenv("EMAIL_RECIPIENTS", "")  # comma-separated

# ── Sites Registry ─────────────────────────────────────────────────────────
def load_sites() -> dict:
    """Loads site URL registry from sites.json — no hardcoded URLs."""
    if os.path.exists(SITES_FILE):
        try:
            with open(SITES_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

SITES: dict = load_sites()

# ── Auth Registry ──────────────────────────────────────────────────────────
def get_auth_registry() -> dict:
    """
    Builds HTTP-auth registry from environment variables.
    Add new domains by setting env vars — no code changes needed.
    Pattern: AUTH_<DOMAIN_SNAKE>_USERNAME / AUTH_<DOMAIN_SNAKE>_PASSWORD
    """
    registry = {}
    # Scan all env vars for AUTH_ prefix pattern
    for key, val in os.environ.items():
        if key.startswith("AUTH_") and key.endswith("_USERNAME"):
            domain_key = key[5:-9]  # strip AUTH_ prefix and _USERNAME suffix
            password_key = f"AUTH_{domain_key}_PASSWORD"
            domain = os.getenv(f"AUTH_{domain_key}_DOMAIN", "")
            password = os.getenv(password_key, "")
            if domain and password:
                registry[domain] = {"username": val, "password": password}
    return registry
