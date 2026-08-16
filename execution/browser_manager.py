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
# MOBILE-WEB EMULATION
#
# Mobile emulation is suite-driven and opt-in. A suite requests it via:
#
#     "desired_capabilities": {
#       "browser": "chromium",
#       "device_name": "Pixel 7",
#       "mobile_web": true
#     }
#
# The device descriptor is read from Playwright's installed registry at runtime
# (playwright.devices[...]), so no user-agent string or browser version is ever
# hardcoded here. When neither capability is supplied the desktop context options
# are returned byte-identical to the pre-existing behaviour.
# ─────────────────────────────────────────────────────────────────────────────

def wants_mobile_web(capabilities: dict | None) -> bool:
    """True only when a suite explicitly opts in to mobile emulation."""
    caps = capabilities or {}
    if caps.get("mobile_web"):
        return True
    device_name = caps.get("device_name")
    return isinstance(device_name, str) and device_name.strip() != ""


def _suggest_devices(device_name: str, devices) -> str:
    """Build a short 'did you mean' list for an unknown device name."""
    try:
        names = list(devices.keys())
    except AttributeError:
        return ""
    needle = device_name.strip().lower().split()[0] if device_name.strip() else ""
    near = [n for n in names if needle and needle in n.lower()][:8]
    if near:
        return f" Closest available: {', '.join(near)}."
    return f" {len(names)} devices are available in this Playwright build."


def build_context_options(capabilities: dict | None, devices) -> dict:
    """
    Translate suite desired_capabilities into Playwright new_context() kwargs.

    Args:
        capabilities: the suite's desired_capabilities dict (may be None/empty).
        devices:      Playwright's device registry (playwright_instance.devices).

    Returns:
        kwargs for browser.new_context().

    Raises:
        ValueError: if mobile emulation is requested with an unknown/blank device name.
    """
    caps = capabilities or {}
    use = load_playwright_config().get("use", {})
    permissions = use.get("permissions", [])

    # ── Desktop: unchanged from the original implementation ──────────────────
    if not wants_mobile_web(caps):
        return dict(no_viewport=True, permissions=permissions)

    # ── Mobile: resolve the device descriptor from Playwright's registry ─────
    device_name = str(caps.get("device_name") or settings.MOBILE_DEVICE_EMULATION or "").strip()
    if not device_name:
        raise ValueError(
            "Mobile web emulation was requested (mobile_web=true) but no 'device_name' was "
            "supplied and settings.MOBILE_DEVICE_EMULATION is empty. Set "
            "desired_capabilities.device_name to a Playwright device, e.g. \"Pixel 7\"."
        )

    if device_name not in devices:
        raise ValueError(
            f"Unknown mobile device_name {device_name!r} — not present in this Playwright "
            f"installation's device registry.{_suggest_devices(device_name, devices)}"
        )

    descriptor = dict(devices[device_name])
    logger.info("📱 Mobile web emulation: %s", device_name)

    default_browser = descriptor.get("default_browser_type", "chromium")
    if default_browser != "chromium":
        logger.warning(
            "⚠️  Device '%s' expects browser type '%s' but this runner launches Chromium. "
            "Rendering may differ from a real device.", device_name, default_browser,
        )

    options: dict = {
        "viewport": descriptor["viewport"],
        "user_agent": descriptor["user_agent"],
        "device_scale_factor": descriptor["device_scale_factor"],
        "is_mobile": descriptor["is_mobile"],
        "has_touch": descriptor["has_touch"],
        "permissions": permissions,
    }

    # ── Explicit suite overrides (mobile mode only) ──────────────────────────
    # Deliberately not applied on desktop: existing desktop suites already declare
    # viewport/locale/timezone that are currently ignored, and honouring them there
    # would silently change established desktop runs.
    width, height = caps.get("viewport_width"), caps.get("viewport_height")
    if width and height:
        options["viewport"] = {"width": int(width), "height": int(height)}
        logger.info("   viewport override: %sx%s", width, height)
    for cap_key, ctx_key in (("locale", "locale"), ("timezone_id", "timezone_id"),
                             ("user_agent", "user_agent")):
        if caps.get(cap_key):
            options[ctx_key] = caps[cap_key]
            logger.info("   %s override: %s", cap_key, caps[cap_key])

    logger.info(
        "   viewport=%(viewport)s scale=%(device_scale_factor)s "
        "is_mobile=%(is_mobile)s has_touch=%(has_touch)s", options,
    )
    return options


# ─────────────────────────────────────────────────────────────────────────────
# BROWSER LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────
def open_browser(session: TestSession | None = None, record_video: bool = False,
                 capabilities: dict | None = None):
    """
    Launches Chromium and returns the Playwright Page object.
    If a TestSession is provided, stores state on it.
    For backward compatibility, also works without a session (uses module-level state).
    Pass record_video=True to enable Playwright video recording; files land in
    data/videos/raw/ and are moved to data/videos/completed/ on close_browser().
    Pass capabilities (a suite's desired_capabilities dict) to enable mobile-web
    emulation — see build_context_options(). Omitting it keeps the desktop context.
    """
    full_config = load_playwright_config()
    use = full_config.get("use", {})

    w, h = get_system_resolution()

    playwright_instance = sync_playwright().start()
    try:
        ctx_kwargs: dict = build_context_options(capabilities, playwright_instance.devices)
    except Exception:
        # Never leak a driver process when the configuration is rejected.
        playwright_instance.stop()
        raise

    if not wants_mobile_web(capabilities):
        logger.info("🖥️ Desktop Resolution: %sx%s.", w, h)

    browser = playwright_instance.chromium.launch(
        headless=use.get("headless", settings.HEADLESS),
        args=["--start-maximized", "--disable-infobars"],
    )

    if record_video:
        raw_dir = os.path.join(settings.VIDEOS_DIR, "raw")
        _ensure_dir(raw_dir)
        ctx_kwargs["record_video_dir"] = raw_dir
        ctx_kwargs["record_video_size"] = {"width": w, "height": h}
        logger.info("🎥 Video recording ON — raw dir: %s", raw_dir)

    context = browser.new_context(**ctx_kwargs)
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
            session.active_page = None
            session.active_frame = None
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
