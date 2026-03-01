"""
config/settings.py
Unified project-wide configuration for ALL platforms.
Loaded from config/controllers.json with optional overrides from environment/.env.
Covers: web, mobile, android, ios, hybrid, device adapters.
"""
import os
import json
from dotenv import load_dotenv

load_dotenv()

CONFIG_DIR: str = os.path.dirname(os.path.abspath(__file__))
BASE_DIR: str = os.path.dirname(CONFIG_DIR)
CONTROLLERS_FILE: str = os.path.join(CONFIG_DIR, "controllers.json")


def _as_bool(val, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    return str(val).strip().lower() in ("1", "true", "yes", "y", "on")


def _load_controllers() -> dict:
    if os.path.exists(CONTROLLERS_FILE):
        try:
            with open(CONTROLLERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}


_CTRL: dict = _load_controllers()


def _ctrl(path: str, default=None):
    cur = _CTRL
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur

# ── Active Platform ────────────────────────────────────────────────────────
# Options: web | mobile | android | ios | hybrid
PLATFORM: str = os.getenv("PLATFORM", str(_ctrl("runtime.platform", "web"))).lower()
EXECUTION_TARGET: str = os.getenv("EXECUTION_TARGET", str(_ctrl("runtime.execution_target", "local"))).lower()
if EXECUTION_TARGET not in {"local", "cloud"}:
    EXECUTION_TARGET = "local"
RUN_ON_CLOUD: bool = EXECUTION_TARGET == "cloud"

# ── Browser (Web / Mobile Web) ─────────────────────────────────────────────
HEADLESS: bool = _as_bool(os.getenv("HEADLESS", _ctrl("browser.headless", False)), False)
ACTION_TIMEOUT_MS: int = int(os.getenv("ACTION_TIMEOUT_MS", str(_ctrl("browser.action_timeout_ms", 15000))))
NAVIGATION_TIMEOUT_MS: int = int(os.getenv("NAVIGATION_TIMEOUT_MS", str(_ctrl("browser.navigation_timeout_ms", 30000))))
DEFAULT_SCROLL_COUNT: int = int(os.getenv("DEFAULT_SCROLL_COUNT", str(_ctrl("browser.default_scroll_count", 20))))
WAIT_TIMEOUT_MS: int = int(os.getenv("WAIT_TIMEOUT_MS", str(_ctrl("browser.wait_timeout_ms", 3000))))

# ── Capture Controls ────────────────────────────────────────────────────────
ENABLE_SCREENSHOTS: bool = _as_bool(os.getenv("ENABLE_SCREENSHOTS", _ctrl("capture.screenshots_enabled", False)), False)
ENABLE_VIDEO_RECORDING: bool = _as_bool(os.getenv("ENABLE_VIDEO_RECORDING", _ctrl("capture.video_enabled", False)), False)
ENABLE_REPORTING: bool = _as_bool(os.getenv("ENABLE_REPORTING", _ctrl("reporting.enabled", False)), False)
RERUN_ON_FAILURE: bool = _as_bool(os.getenv("RERUN_ON_FAILURE", _ctrl("execution_defaults.rerun_on_failure", False)), False)

# ── Retry ──────────────────────────────────────────────────────────────────
RETRY_MAX_ATTEMPTS: int = int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))
RETRY_DELAY_SECONDS: float = float(os.getenv("RETRY_DELAY_SECONDS", "2.0"))

# ── Mobile Web Emulation ───────────────────────────────────────────────────
MOBILE_DEVICE_EMULATION: str = os.getenv("MOBILE_DEVICE_EMULATION", "iPhone 14")
MOBILE_BROWSER_TYPE: str = os.getenv("MOBILE_BROWSER_TYPE", "chromium")
MOBILE_USER_AGENT: str = os.getenv("MOBILE_USER_AGENT", "")

# ── Appium (Android / iOS / Hybrid) ───────────────────────────────────────
APPIUM_SERVER_URL: str = os.getenv("APPIUM_SERVER_URL", str(_ctrl("mobile.appium_server_url", "http://localhost:4723")))

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
DATA_DIR: str = os.path.join(BASE_DIR, "data")
SCREENSHOTS_DIR: str = os.path.join(DATA_DIR, "screenshots")
VIDEOS_DIR: str = os.path.join(DATA_DIR, "videos")
LOGS_DIR: str = os.path.join(DATA_DIR, "logs")
MANUAL_LOCATORS_FILE: str = os.path.join(DATA_DIR, "locators_manual.json")
RECORDED_ELEMENTS_FILE: str = os.path.join(DATA_DIR, "recorded_elements.json")
SITES_FILE: str = os.path.join(CONFIG_DIR, "sites.json")
PLAYWRIGHT_CONFIG_FILE: str = os.path.join(CONFIG_DIR, "playwright.config.json")
FLOWS_DIR: str = os.path.join(BASE_DIR, "flows")
PLANS_DIR: str = os.path.join(BASE_DIR, "plans")
SUITES_DIR: str = os.path.join(BASE_DIR, "suites")

# ── Notifications ──────────────────────────────────────────────────────────────
NOTIFY_ON_SLACK: bool = _as_bool(os.getenv("NOTIFY_ON_SLACK", _ctrl("notifications.slack_enabled", False)), False)
SLACK_WEBHOOK_URL: str = os.getenv("SLACK_WEBHOOK_URL", "")
SLACK_CHANNEL: str = os.getenv("SLACK_CHANNEL", "#test-results")
SLACK_USERNAME: str = os.getenv("SLACK_USERNAME", "NLP-Playwright Bot")
SLACK_ICON_EMOJI: str = os.getenv("SLACK_ICON_EMOJI", ":robot_face:")

# Email (future — stub ready, SMTP not yet wired)
NOTIFY_ON_EMAIL: bool = _as_bool(os.getenv("NOTIFY_ON_EMAIL", _ctrl("notifications.email_enabled", False)), False)
EMAIL_SMTP_HOST: str = os.getenv("EMAIL_SMTP_HOST", "")
EMAIL_SMTP_PORT: int = int(os.getenv("EMAIL_SMTP_PORT", "587"))
EMAIL_SENDER: str = os.getenv("EMAIL_SENDER", "")
EMAIL_PASSWORD: str = os.getenv("EMAIL_PASSWORD", "")
EMAIL_RECIPIENTS: str = os.getenv("EMAIL_RECIPIENTS", "")  # comma-separated

# ── Android Lifecycle Defaults (overridable in suite desired_capabilities) ──
ANDROID_APP_INSTALL_DEFAULT: bool = _as_bool(_ctrl("android_lifecycle_defaults.app_install", False), False)
ANDROID_APP_UPDATE_DEFAULT: bool = _as_bool(_ctrl("android_lifecycle_defaults.app_update", False), False)
ANDROID_NEW_APK_SHARED_DEFAULT: bool = _as_bool(_ctrl("android_lifecycle_defaults.new_apk_shared", False), False)
ANDROID_EXISTING_APP_PRESENT_DEFAULT: bool = _as_bool(_ctrl("android_lifecycle_defaults.existing_app_present", True), True)
ANDROID_CLEAR_CACHE_DEFAULT: bool = _as_bool(_ctrl("android_lifecycle_defaults.clear_cache", False), False)
ANDROID_CLEAR_STORAGE_DEFAULT: bool = _as_bool(_ctrl("android_lifecycle_defaults.clear_storage", False), False)
ANDROID_RESET_DEVICE_PERMISSION_DEFAULT: bool = _as_bool(_ctrl("android_lifecycle_defaults.reset_device_permission", False), False)

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
