"""
execution/appium_action_service.py
NLP action handlers for Android & iOS apps via Appium.

Mirrors execution/action_service.py but operates on an Appium WebDriver
instead of a Playwright Page. All locators come from the 'android' or 'ios'
section of data/locators_manual.json (recorded via spy/appium_spy.py).

Locator resolution priority:
  Android: accessibility_id > resource_id > text > xpath > class_name
  iOS    : accessibility_id > label > xpath > class_name
"""

import logging
import os
import time
from datetime import datetime

from nlp.variable_manager import RUNTIME_VARIABLES, resolve_variables
from config import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# LOCATOR LOOKUP
# ─────────────────────────────────────────────────────────────────────────────

_locator_cache: dict | None = None


def _load_app_locators() -> dict:
    global _locator_cache
    if _locator_cache is None:
        import json
        path = settings.MANUAL_LOCATORS_FILE
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                _locator_cache = json.load(f)
        else:
            _locator_cache = {}
    return _locator_cache


def _get_appium_locator(name: str, platform: str) -> dict:
    """
    Look up a locator by name under the platform section.
    Searches all screen groups under platform key.

    Returns a dict with keys like accessibility_id, resource_id, xpath, etc.
    Raises ValueError if not found.
    """
    data = _load_app_locators()
    platform_data = data.get(platform, {})

    # Search flat (top-level name)
    if name in platform_data:
        return platform_data[name]

    # Search inside screen groups
    for screen_group in platform_data.values():
        if isinstance(screen_group, dict) and name in screen_group:
            return screen_group[name]

    raise ValueError(
        f"Locator '{name}' not found in platform '{platform}' section of locators_manual.json.\n"
        f"  Run:  python spy/appium_spy.py --platform {platform}  to record it."
    )


def _find_element(driver, name: str, platform: str):
    """
    Find an Appium element by locator name. Tries strategies in priority order.
    """
    from appium.webdriver.common.appiumby import AppiumBy

    locator = _get_appium_locator(name, platform)
    errors  = []

    # Priority order per platform
    if platform == "android":
        strategies = [
            (AppiumBy.ACCESSIBILITY_ID,     locator.get("accessibility_id")),
            (AppiumBy.ID,                   locator.get("resource_id")),
            (AppiumBy.ANDROID_UIAUTOMATOR,
             f'new UiSelector().text("{locator["text"]}")' if locator.get("text") else None),
            (AppiumBy.XPATH,                locator.get("xpath")),
            (AppiumBy.CLASS_NAME,           locator.get("class_name")),
        ]
    else:  # ios
        strategies = [
            (AppiumBy.ACCESSIBILITY_ID,  locator.get("accessibility_id")),
            (AppiumBy.IOS_PREDICATE,
             f'label == "{locator["label"]}"' if locator.get("label") else None),
            (AppiumBy.XPATH,             locator.get("xpath")),
            (AppiumBy.CLASS_NAME,        locator.get("class_name")),
        ]

    for by, value in strategies:
        if not value:
            continue
        try:
            el = driver.find_element(by, value)
            logger.debug("  ✅ Found '%s' via %s='%s'", name, by, value)
            return el
        except Exception as e:
            errors.append(f"{by}: {e}")

    raise RuntimeError(
        f"Could not find element '{name}' on {platform} using any strategy.\n"
        + "\n".join(f"  • {e}" for e in errors)
    )


# ─────────────────────────────────────────────────────────────────────────────
# SCREENSHOT / PATH HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ─────────────────────────────────────────────────────────────────────────────
# ACTION IMPLEMENTATIONS
# ─────────────────────────────────────────────────────────────────────────────

def launch_app(driver, fallback_caps: dict | None = None):
    """Bring app to foreground (activate the app)."""
    try:
        caps = getattr(driver, "capabilities", {}) or {}
        merged = dict(fallback_caps or {})
        merged.update(caps)

        app_package = merged.get("appPackage") or merged.get("appium:appPackage")
        app_activity = merged.get("appActivity") or merged.get("appium:appActivity")
        bundle_id = merged.get("bundleId") or merged.get("appium:bundleId")

        if app_package and app_activity:
            try:
                driver.start_activity(app_package, app_activity)
                logger.info("🚀 App started via activity: %s/%s", app_package, app_activity)
                return
            except Exception:
                # fallback to activate_app below
                pass

        app_id = app_package or bundle_id or ""
        if app_id:
            driver.activate_app(app_id)
            logger.info("🚀 App launched / brought to foreground: %s", app_id)
        else:
            logger.warning("⚠️  launch_app: no appPackage/bundleId in capabilities")
    except Exception as e:
        logger.warning("⚠️  launch_app: %s", e)


def tap_element(driver, name: str, platform: str):
    """Tap/click an element by locator name."""
    name = resolve_variables(name)
    el   = _find_element(driver, name, platform)
    el.click()
    logger.info("👆 Tapped: '%s'", name)


def fill_element(driver, name: str, text: str, platform: str):
    """Clear and type text into an element."""
    name = resolve_variables(name)
    text = resolve_variables(text)
    el   = _find_element(driver, name, platform)
    el.clear()
    el.send_keys(text)
    logger.info("⌨️  Filled '%s' with '%s'", name, text)


def clear_element(driver, name: str, platform: str):
    """Clear text from an input element."""
    name = resolve_variables(name)
    el   = _find_element(driver, name, platform)
    el.clear()
    logger.info("🗑️  Cleared: '%s'", name)


def tap_coordinates(driver, x: int, y: int):
    """Tap at raw screen coordinates."""
    driver.execute_script("mobile: clickGesture", {"x": int(x), "y": int(y)})
    logger.info("👆 Tapped at (%d, %d)", x, y)


def swipe_up(driver, duration_ms: int = 600):
    """Swipe up (scroll down content)."""
    size = driver.get_window_size()
    w, h = size["width"], size["height"]
    driver.swipe(w // 2, int(h * 0.75), w // 2, int(h * 0.25), duration_ms)
    logger.info("🔼 Swiped up (scroll down)")


def swipe_down(driver, duration_ms: int = 600):
    """Swipe down (scroll up content)."""
    size = driver.get_window_size()
    w, h = size["width"], size["height"]
    driver.swipe(w // 2, int(h * 0.25), w // 2, int(h * 0.75), duration_ms)
    logger.info("🔽 Swiped down (scroll up)")


def swipe_left(driver, duration_ms: int = 400):
    """Swipe left."""
    size = driver.get_window_size()
    w, h = size["width"], size["height"]
    driver.swipe(int(w * 0.8), h // 2, int(w * 0.2), h // 2, duration_ms)
    logger.info("◀️  Swiped left")


def swipe_right(driver, duration_ms: int = 400):
    """Swipe right."""
    size = driver.get_window_size()
    w, h = size["width"], size["height"]
    driver.swipe(int(w * 0.2), h // 2, int(w * 0.8), h // 2, duration_ms)
    logger.info("▶️  Swiped right")


def scroll_until_text_visible(driver, text: str, max_swipes: int = 8, wait_s: float = 0.5):
    """Swipe up repeatedly until text appears on screen."""
    text = resolve_variables(text)
    from appium.webdriver.common.appiumby import AppiumBy

    for i in range(max_swipes):
        try:
            el = driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR,
                                     f'new UiScrollable(new UiSelector().scrollable(true))'
                                     f'.scrollIntoView(new UiSelector().textContains("{text}"))')
            if el:
                logger.info("📜 Scrolled to text: '%s'", text)
                return
        except Exception:
            pass

        # Fallback: try XPATH
        try:
            els = driver.find_elements(AppiumBy.XPATH, f"//*[contains(@text,'{text}') or contains(@content-desc,'{text}')]")
            if els:
                logger.info("📜 Found text via XPath: '%s'", text)
                return
        except Exception:
            pass

        swipe_up(driver)
        time.sleep(wait_s)

    logger.warning("⚠️  Text '%s' not found after %d swipes", text, max_swipes)


def scroll_until_element_visible(driver, name: str, platform: str, max_swipes: int = 8):
    """Swipe until a named element appears."""
    name = resolve_variables(name)
    for i in range(max_swipes):
        try:
            el = _find_element(driver, name, platform)
            if el.is_displayed():
                logger.info("📜 Scrolled to element: '%s'", name)
                return
        except Exception:
            pass
        swipe_up(driver)
        time.sleep(0.5)
    logger.warning("⚠️  Element '%s' not visible after %d swipes", name, max_swipes)


def verify_text(driver, text: str):
    """Assert that text appears anywhere on the current screen."""
    text = resolve_variables(text)
    from appium.webdriver.common.appiumby import AppiumBy

    page_source = driver.page_source
    if text in page_source:
        logger.info("✅ Text verified: '%s'", text)
        return

    # Try element search
    try:
        els = driver.find_elements(
            AppiumBy.XPATH,
            f"//*[contains(@text,'{text}') or contains(@content-desc,'{text}') "
            f"or contains(@label,'{text}') or contains(@value,'{text}')]"
        )
        if els:
            logger.info("✅ Text verified (element): '%s'", text)
            return
    except Exception:
        pass

    raise AssertionError(f"❌ Text not found on screen: '{text}'")


def verify_texts(driver, texts: list[str]):
    """Assert multiple texts appear on screen."""
    for t in texts:
        verify_text(driver, t)


def verify_element_exists(driver, name: str, platform: str):
    """Assert that a named element exists and is displayed."""
    name = resolve_variables(name)
    el   = _find_element(driver, name, platform)
    if not el.is_displayed():
        raise AssertionError(f"❌ Element '{name}' found but not visible")
    logger.info("✅ Element verified: '%s'", name)


def verify_element_not_exists(driver, name: str, platform: str):
    """Assert that a named element does NOT exist."""
    name = resolve_variables(name)
    try:
        el = _find_element(driver, name, platform)
        if el.is_displayed():
            raise AssertionError(f"❌ Element '{name}' is visible but should not be")
    except (RuntimeError, ValueError):
        pass  # Not found = expected
    logger.info("✅ Element '{name}' correctly absent")


def store_element_text(driver, name: str, platform: str, variable: str):
    """Read text from element and store in RUNTIME_VARIABLES."""
    name     = resolve_variables(name)
    variable = resolve_variables(variable)
    el       = _find_element(driver, name, platform)
    text     = el.text or el.get_attribute("label") or el.get_attribute("value") or ""
    RUNTIME_VARIABLES[variable] = text
    logger.info("💾 Stored text of '%s' → $%s = '%s'", name, variable, text)


def store_variable(value: str, variable: str):
    """Store a literal value into RUNTIME_VARIABLES."""
    value    = resolve_variables(value)
    variable = resolve_variables(variable)
    RUNTIME_VARIABLES[variable] = value
    logger.info("💾 Stored '%s' → $%s", value, variable)


def take_screenshot(driver, name: str):
    """Save a screenshot to data/screenshots/."""
    name = resolve_variables(name)
    if not settings.ENABLE_SCREENSHOTS:
        logger.info("📵 Screenshots are disabled (ENABLE_SCREENSHOTS=false). Skipping capture '%s'.", name)
        return
    _ensure_dir(settings.SCREENSHOTS_DIR)
    filename = os.path.join(settings.SCREENSHOTS_DIR, f"{name}_{_timestamp()}.png")
    driver.save_screenshot(filename)
    logger.info("📸 Screenshot saved: %s", filename)


def wait_seconds(driver, seconds: float):
    """Wait for N seconds."""
    logger.info("⏳ Waiting %.1f seconds...", seconds)
    time.sleep(float(seconds))


def press_back(driver):
    """Press the Android back button (or iOS swipe back)."""
    try:
        driver.back()
        logger.info("⬅️  Pressed back")
    except Exception as e:
        logger.warning("⚠️  back(): %s", e)


def press_home(driver):
    """Press the home button / go to home screen."""
    try:
        driver.execute_script("mobile: pressKey", {"keycode": 3})  # KEYCODE_HOME on Android
        logger.info("🏠 Pressed home")
    except Exception as e:
        logger.warning("⚠️  home(): %s", e)


def press_enter(driver):
    """Press enter/return key on the keyboard."""
    try:
        from appium.webdriver.common.appiumby import AppiumBy
        driver.execute_script("mobile: pressKey", {"keycode": 66})  # KEYCODE_ENTER on Android
        logger.info("↩️  Pressed enter")
    except Exception as e:
        logger.warning("⚠️  enter(): %s", e)


def hide_keyboard(driver):
    """Dismiss the on-screen keyboard."""
    try:
        driver.hide_keyboard()
        logger.info("⌨️  Keyboard hidden")
    except Exception:
        pass


def open_url(driver, url: str):
    """Open a URL (for hybrid/WebView or mobile browser contexts)."""
    url = resolve_variables(url)
    driver.get(url)
    logger.info("🌐 Opened URL: %s", url)
