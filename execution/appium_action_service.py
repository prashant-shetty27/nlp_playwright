"""
execution/appium_action_service.py
NLP action handlers for Android & iOS apps via Appium.

Mirrors execution/action_service.py but operates on an Appium WebDriver
instead of a Playwright Page. All locators come from the 'android' or 'ios'
section of data/locators_manual.json (recorded via spy/appium_spy.py).

Locator resolution priority:
  Android: accessibility_id > resource_id > xpath > class_name
           (text is ONLY used as fallback for non-input elements)
  iOS    : accessibility_id > xpath > class_name
           (label is ONLY used as fallback for non-input elements)
"""

import logging
import os
import re
import time
from datetime import datetime

from nlp.variable_manager import RUNTIME_VARIABLES, resolve_variables
from config import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# LOCATOR LOOKUP
# ─────────────────────────────────────────────────────────────────────────────

_locator_cache: dict | None = None
_locator_cache_mtime: float = 0.0


def invalidate_locator_cache():
    """Force the next call to _load_app_locators() to re-read the file."""
    global _locator_cache, _locator_cache_mtime
    _locator_cache = None
    _locator_cache_mtime = 0.0


def _load_app_locators() -> dict:
    global _locator_cache, _locator_cache_mtime
    import json
    path = settings.MANUAL_LOCATORS_FILE
    try:
        mtime = os.path.getmtime(path) if os.path.exists(path) else 0.0
    except OSError:
        mtime = 0.0
    if _locator_cache is None or mtime > _locator_cache_mtime:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    _locator_cache = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("⚠️  Could not reload locators (%s) — using cached copy", e)
                if _locator_cache is None:
                    _locator_cache = {}
        else:
            _locator_cache = {}
        _locator_cache_mtime = mtime
    return _locator_cache


def _get_appium_locator(name: str, platform: str) -> dict:
    """
    Look up a locator by name under the platform section.
    Supports index suffix in name: "login_btn[last]" → looks up "login_btn".
    Searches all screen groups under platform key.

    Returns a dict with keys like accessibility_id, resource_id, xpath, etc.
    Raises ValueError if not found.
    """
    # Strip optional [index] suffix before lookup
    base_name = re.sub(r'\[.*?\]$', '', name.strip())

    data = _load_app_locators()
    platform_data = data.get(platform, {})

    # Search flat (top-level name) using base_name
    if base_name in platform_data:
        return platform_data[base_name]

    # Search inside screen groups
    for screen_group in platform_data.values():
        if isinstance(screen_group, dict) and base_name in screen_group:
            return screen_group[base_name]

    raise ValueError(
        f"Locator '{base_name}' not found in platform '{platform}' section of locators_manual.json.\n"
        f"  Run:  python spy/appium_spy.py --platform {platform}  to record it."
    )


def _parse_index_suffix(name: str) -> tuple[str, str]:
    """Extract base name and index suffix from 'element_name[last]' style strings."""
    m = re.match(r'^(.+?)\[([^\]]+)\]$', name.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return name.strip(), "any"


_INDEX_MAP = {
    "1st": 0, "2nd": 1, "3rd": 2, "4th": 3, "5th": 4,
    "last": -1, "last-2": -2, "last-3": -3, "last-4": -4,
}


def _find_element(driver, name: str, platform: str):
    """
    Find an Appium element by locator name. Tries strategies in priority order.
    Supports [index] suffix for multi-match disambiguation (e.g. 'btn[last]').
    text/label are skipped for input-type elements (dynamic placeholder values).
    """
    from appium.webdriver.common.appiumby import AppiumBy

    base_name, el_index = _parse_index_suffix(name)

    _INPUT_CLASS_NAMES = {
        "android.widget.EditText",
        "android.widget.MultiAutoCompleteTextView",
        "android.widget.AutoCompleteTextView",
        "android.widget.SearchView",
        "XCUIElementTypeTextField",
        "XCUIElementTypeSecureTextField",
        "XCUIElementTypeSearchField",
    }

    locator    = _get_appium_locator(base_name, platform)
    errors     = []
    cls_name   = locator.get("class_name", "")
    is_input   = cls_name in _INPUT_CLASS_NAMES

    # Priority order per platform — text/label only for non-input elements
    if platform == "android":
        strategies = [
            (AppiumBy.ACCESSIBILITY_ID, locator.get("accessibility_id")),
            (AppiumBy.ID,               locator.get("resource_id")),
            (AppiumBy.XPATH,            locator.get("xpath")),
            (AppiumBy.CLASS_NAME,       locator.get("class_name")),
            # text is a last-resort fallback only for non-input elements
            (AppiumBy.ANDROID_UIAUTOMATOR,
             f'new UiSelector().text("{locator["text"]}")'
             if (not is_input and locator.get("text")) else None),
        ]
    else:  # ios
        strategies = [
            (AppiumBy.ACCESSIBILITY_ID, locator.get("accessibility_id")),
            (AppiumBy.XPATH,            locator.get("xpath")),
            (AppiumBy.CLASS_NAME,       locator.get("class_name")),
            # label only for non-input elements
            (AppiumBy.IOS_PREDICATE,
             f'label == "{locator["label"]}"'
             if (not is_input and locator.get("label")) else None),
        ]

    # When el_index is "any" use find_element (first match); otherwise find_elements + index
    if el_index == "any":
        for by, value in strategies:
            if not value:
                continue
            try:
                el = driver.find_element(by, value)
                logger.debug("  ✅ Found '%s' via %s='%s'", base_name, by, value)
                return el
            except Exception as e:
                errors.append(f"{by}: {e}")
    else:
        # Try each strategy with find_elements, pick by index
        num_idx = _INDEX_MAP.get(el_index, 0)
        for by, value in strategies:
            if not value:
                continue
            try:
                els = driver.find_elements(by, value)
                if els:
                    el = els[num_idx]
                    logger.debug("  ✅ Found '%s'[%s] via %s", base_name, el_index, by)
                    return el
            except Exception as e:
                errors.append(f"{by}[{el_index}]: {e}")

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
# VIDEO RECORDING
# ─────────────────────────────────────────────────────────────────────────────

def start_recording(driver, platform: str = "android") -> None:
    """
    Start Appium screen recording.
    Android: records mp4 via UiAutomator2 (max 3 min by default).
    iOS    : records mp4 via XCUITest.
    """
    try:
        if platform == "android":
            driver.start_recording_screen(
                options={
                    "videoQuality": "high",
                    "timeLimit": "1800",     # 30 min hard cap
                    "bitRate": "4000000",
                }
            )
        else:
            driver.start_recording_screen(
                options={
                    "videoQuality": "medium",
                    "timeLimit": "1800",
                    "videoFps": "30",
                }
            )
        logger.info("🎥 Screen recording started [%s]", platform.upper())
    except Exception as e:
        logger.warning("⚠️  Could not start screen recording: %s", e)


def stop_recording(driver, label: str = "run", platform: str = "android") -> str | None:
    """
    Stop Appium screen recording and save the video to data/videos/.
    Returns the saved file path, or None on failure.
    """
    import base64
    try:
        raw = driver.stop_recording_screen()
        if not raw:
            logger.warning("⚠️  stop_recording_screen returned empty data")
            return None
        _ensure_dir(settings.VIDEOS_DIR)
        filename = os.path.join(
            settings.VIDEOS_DIR, f"{label}_{_timestamp()}.mp4"
        )
        with open(filename, "wb") as fh:
            fh.write(base64.b64decode(raw))
        logger.info("🎬 Video saved: %s", filename)
        return filename
    except Exception as e:
        logger.warning("⚠️  Could not stop/save screen recording: %s", e)
        return None


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


def tap_element(driver, name: str, platform: str, el_index: str = "any"):
    """Tap/click an element by locator name. el_index: 'any','1st','2nd','last','last-2' etc."""
    name = resolve_variables(name)
    lookup = f"{name}[{el_index}]" if el_index and el_index != "any" else name
    el   = _find_element(driver, lookup, platform)
    el.click()
    logger.info("👆 Tapped: '%s'%s", name, f"[{el_index}]" if el_index != "any" else "")


def fill_element(driver, name: str, text: str, platform: str, el_index: str = "any"):
    """Clear and type text into an element. el_index: 'any','1st','last' etc."""
    name = resolve_variables(name)
    text = resolve_variables(text)
    lookup = f"{name}[{el_index}]" if el_index and el_index != "any" else name
    el   = _find_element(driver, lookup, platform)
    el.clear()
    el.send_keys(text)
    logger.info("⌨️  Filled '%s'%s with '%s'", name, f"[{el_index}]" if el_index != "any" else "", text)


def clear_element(driver, name: str, platform: str, el_index: str = "any"):
    """Clear text from an input element. el_index: 'any','1st','last' etc."""
    name = resolve_variables(name)
    lookup = f"{name}[{el_index}]" if el_index and el_index != "any" else name
    el   = _find_element(driver, lookup, platform)
    el.clear()
    logger.info("🗑️  Cleared: '%s'%s", name, f"[{el_index}]" if el_index != "any" else "")


def tap_coordinates(driver, x: int, y: int):
    """Tap at raw screen coordinates (works on iOS and Android)."""
    try:
        # iOS: mobile:tap with x,y coordinates
        driver.execute_script("mobile: tap", {"x": int(x), "y": int(y)})
    except Exception:
        # Fallback: W3C Appium tap
        driver.tap([(int(x), int(y))])
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

    for _ in range(max_swipes):
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
    for _ in range(max_swipes):
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
    """Assert that text appears anywhere on the current screen.

    Strategy: XPath find_elements first (evaluated natively on-device by WDA/UIA2,
    fast regardless of page size), then page_source as a fallback.
    Avoids downloading megabytes of XML for pages with hundreds of list cells.
    """
    text = resolve_variables(text)
    from appium.webdriver.common.appiumby import AppiumBy

    # Primary: native XPath — server-side evaluation, no XML transfer
    try:
        els = driver.find_elements(
            AppiumBy.XPATH,
            f"//*[contains(@text,'{text}') or contains(@content-desc,'{text}') "
            f"or contains(@label,'{text}') or contains(@value,'{text}') "
            f"or contains(@name,'{text}')]"
        )
        if els:
            logger.info("✅ Text verified: '%s'", text)
            return
    except Exception:
        pass

    # Fallback: page_source string scan (slow on large pages — avoid for results lists)
    try:
        if text in driver.page_source:
            logger.info("✅ Text verified (page source): '%s'", text)
            return
    except Exception:
        pass

    raise AssertionError(f"❌ Text not found on screen: '{text}'")


def verify_texts(driver, texts: list[str]):
    """Assert multiple texts appear on screen."""
    for t in texts:
        verify_text(driver, t)


def verify_element_exists(driver, name: str, platform: str, el_index: str = "any"):
    """Assert that a named element exists and is displayed. el_index: 'any','1st','last' etc."""
    name = resolve_variables(name)
    lookup = f"{name}[{el_index}]" if el_index and el_index != "any" else name
    el   = _find_element(driver, lookup, platform)
    if not el.is_displayed():
        raise AssertionError(f"❌ Element '{name}' found but not visible")
    logger.info("✅ Element verified: '%s'%s", name, f"[{el_index}]" if el_index != "any" else "")


def verify_element_not_exists(driver, name: str, platform: str, el_index: str = "any"):
    """Assert that a named element does NOT exist. el_index: 'any','1st','last' etc."""
    name = resolve_variables(name)
    lookup = f"{name}[{el_index}]" if el_index and el_index != "any" else name
    try:
        el = _find_element(driver, lookup, platform)
        if el.is_displayed():
            raise AssertionError(f"❌ Element '{name}' is visible but should not be")
    except (RuntimeError, ValueError):
        pass  # Not found = expected
    logger.info("✅ Element '%s' correctly absent", name)


def double_tap(driver, name: str, platform: str, el_index: str = "any"):
    """Double-tap an element. el_index: 'any','1st','last' etc."""
    name = resolve_variables(name)
    lookup = f"{name}[{el_index}]" if el_index and el_index != "any" else name
    el   = _find_element(driver, lookup, platform)
    try:
        driver.execute_script("mobile: doubleTap", {"element": el.id})
    except Exception:
        el.click()
        import time as _t; _t.sleep(0.1)
        el.click()
    logger.info("👆👆 Double-tapped: '%s'%s", name, f"[{el_index}]" if el_index != "any" else "")


def long_press(driver, name: str, platform: str, duration_s: float = 1.5, el_index: str = "any"):
    """Long-press (hold) an element. el_index: 'any','1st','last' etc."""
    name = resolve_variables(name)
    lookup = f"{name}[{el_index}]" if el_index and el_index != "any" else name
    el   = _find_element(driver, lookup, platform)
    try:
        driver.execute_script("mobile: touchAndHold", {"element": el.id, "duration": duration_s})
    except Exception:
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(driver).click_and_hold(el).pause(duration_s).release().perform()
    logger.info("🤙 Long-pressed: '%s'%s (%.1fs)", name, f"[{el_index}]" if el_index != "any" else "", duration_s)


def wait_for_element(driver, name: str, platform: str, timeout_s: float = 15, el_index: str = "any"):
    """Wait up to timeout_s for a named element to become visible. el_index: 'any','1st','last' etc."""
    import time as _t
    name     = resolve_variables(name)
    lookup   = f"{name}[{el_index}]" if el_index and el_index != "any" else name
    deadline = _t.time() + timeout_s
    last_err = None
    while _t.time() < deadline:
        try:
            el = _find_element(driver, lookup, platform)
            if el.is_displayed():
                logger.info("✅ Element appeared: '%s'%s", name, f"[{el_index}]" if el_index != "any" else "")
                return
        except Exception as e:
            last_err = e
        _t.sleep(0.5)
    raise AssertionError(
        f"❌ Element '{name}' did not appear within {timeout_s:.0f}s. Last error: {last_err}"
    )


def store_element_text(driver, name: str, platform: str, variable: str, el_index: str = "any"):
    """Read text from element and store in RUNTIME_VARIABLES. el_index: 'any','1st','last' etc."""
    name     = resolve_variables(name)
    variable = resolve_variables(variable)
    lookup   = f"{name}[{el_index}]" if el_index and el_index != "any" else name
    el       = _find_element(driver, lookup, platform)
    text     = el.text or el.get_attribute("label") or el.get_attribute("value") or ""
    RUNTIME_VARIABLES[variable] = text
    logger.info("💾 Stored text of '%s'%s → $%s = '%s'", name, f"[{el_index}]" if el_index != "any" else "", variable, text)


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


def dismiss_play_rating(driver) -> bool:
    """
    Dismiss a Google Play In-App Review (rating) popup.

    The Play rating dialog is rendered by the Play Store process, NOT the app,
    so driver.switch_to.alert and mobile:alert both miss it.
    UiAutomator2 can reach across process boundaries and find the dismiss button.

    Dismiss button text varies by locale/Play version — we try all known variants.
    Falls back to pressing BACK if no button is found.

    Returns True if something was dismissed, False if no popup was detected.
    """
    from appium.webdriver.common.appiumby import AppiumBy

    # Known dismiss button labels across Play Store versions and locales
    _DISMISS_TEXTS = [
        "Maybe later",
        "Not now",
        "No thanks",
        "Remind me later",
        "Later",
        "Cancel",
        "Dismiss",
    ]

    # 1. Try clicking a dismiss button by text (UiAutomator2 finds cross-process overlays)
    for label in _DISMISS_TEXTS:
        try:
            el = driver.find_element(
                AppiumBy.ANDROID_UIAUTOMATOR,
                f'new UiSelector().textContains("{label}")',
            )
            el.click()
            logger.info("⭐ Google Play rating popup dismissed via '%s' button", label)
            time.sleep(0.5)
            return True
        except Exception:
            pass

    # 2. Check if the Play Store activity is in the foreground (reliable on most devices)
    try:
        current_activity = driver.current_activity or ""
        current_package  = driver.current_package  or ""
        play_packages = ("com.android.vending", "com.google.android.play")
        play_activities = ("ReviewActivity", "InAppReview", "RatingDialog")
        is_play_overlay = any(p in current_package for p in play_packages) or \
                          any(a in current_activity for a in play_activities)
        if is_play_overlay:
            driver.back()
            logger.info("⭐ Google Play rating overlay detected — pressed BACK to dismiss")
            time.sleep(0.5)
            return True
    except Exception:
        pass

    # 3. XPath fallback: look for a dialog containing a "Rate" button alongside a dismiss option
    try:
        rate_btn = driver.find_element(
            AppiumBy.XPATH,
            '//*[contains(@text,"Rate") or contains(@text,"rate")]'
        )
        if rate_btn.is_displayed():
            # A "Rate" button visible → we're in the rating popup; press back to close
            driver.back()
            logger.info("⭐ Rating popup detected via 'Rate' button — pressed BACK to dismiss")
            time.sleep(0.5)
            return True
    except Exception:
        pass

    logger.debug("ℹ️  No Play rating popup detected.")
    return False


def dismiss_alerts(driver, attempts: int = 3):
    """
    Dismiss any visible system alerts, permission dialogs, or Google Play rating popups.
    Tries (in order): Play rating popup → system alert dismiss → system alert accept.
    Safe to call even when no alert is showing.
    """
    # Always try Play rating first — it's invisible to mobile:alert
    dismiss_play_rating(driver)

    for _ in range(attempts):
        dismissed = False
        for action in ("dismiss", "accept"):
            try:
                driver.execute_script("mobile: alert", {"action": action})
                logger.info("🔔 System alert %sed", action)
                dismissed = True
                time.sleep(1)
                break
            except Exception:
                pass
        if not dismissed:
            break


def tap_by_text(driver, text: str):
    """Tap the first visible element whose label / text / value contains the given text."""
    text = resolve_variables(text)
    from appium.webdriver.common.appiumby import AppiumBy

    strategies = [
        (AppiumBy.ACCESSIBILITY_ID,
         text),
        (AppiumBy.XPATH,
         f"//*[contains(@label,'{text}') or contains(@text,'{text}') "
         f"or contains(@value,'{text}') or contains(@name,'{text}')]"),
    ]

    errors = []
    for by, value in strategies:
        try:
            el = driver.find_element(by, value)
            el.click()
            logger.info("👆 Tapped by text: '%s'", text)
            return
        except Exception as e:
            errors.append(f"{by}: {e}")

    raise RuntimeError(
        f"Could not tap text '{text}' on screen.\n"
        + "\n".join(f"  • {e}" for e in errors)
    )


def type_into_active(driver, text: str):
    """Type text into whatever element is currently focused (no element lookup needed)."""
    text = resolve_variables(text)
    try:
        # iOS preferred: native typeText avoids keyboard lag
        driver.execute_script("mobile: typeText", {"text": text})
        logger.info("⌨️  Typed into active element: '%s'", text)
    except Exception:
        # Fallback: send_keys on the active element (works on Android too)
        driver.switch_to.active_element.send_keys(text)
        logger.info("⌨️  Typed (send_keys) into active element: '%s'", text)


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
    """Press enter/return/search key on the keyboard (iOS and Android)."""
    from appium.webdriver.common.appiumby import AppiumBy

    # iOS: HID keyboard Return key event (works on real device with XCUITest)
    try:
        driver.execute_script("mobile: performIoHidEvent", {
            "page": 0x07, "usage": 0x28, "durationSeconds": 0.005
        })
        logger.info("↩️  Pressed enter (HID Return key)")
        return
    except Exception:
        pass

    # iOS: tap the visible keyboard Return/Search/Go/Done button
    for key_name in ("Search", "Go", "Done", "return", "Return"):
        try:
            el = driver.find_element(
                AppiumBy.XPATH,
                f"//XCUIElementTypeKeyboard//XCUIElementTypeButton[@name='{key_name}']",
            )
            el.click()
            logger.info("↩️  Pressed keyboard key: %s", key_name)
            return
        except Exception:
            pass

    # iOS fallback: typeText newline
    try:
        driver.execute_script("mobile: typeText", {"text": "\n"})
        logger.info("↩️  Pressed enter (typeText \\n)")
        return
    except Exception:
        pass

    # Android: KEYCODE_ENTER
    try:
        driver.execute_script("mobile: pressKey", {"keycode": 66})
        logger.info("↩️  Pressed enter (Android keycode)")
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


# ─────────────────────────────────────────────────────────────────────────────
# WINDOW / TAB MANAGEMENT  (Appium window handles — hybrid/browser contexts)
# driver.window_handles returns a list of handle strings for each open tab/window.
# In a purely native app there is usually one handle. Hybrid apps or in-app
# browsers may have several.
# ─────────────────────────────────────────────────────────────────────────────

def switch_window(driver, index: int):
    """Switch to a window/tab by 0-based index."""
    handles = driver.window_handles
    if index < 0 or index >= len(handles):
        raise AssertionError(
            f"❌ Window index {index} out of range. "
            f"Open windows: {len(handles)}  (valid: 0–{len(handles) - 1})"
        )
    driver.switch_to.window(handles[index])
    logger.info("🪟 Switched to window %d — handle: %s", index, handles[index])


def close_window(driver, index=None):
    """Close a window by index (or the current window when index is None), then focus window 0."""
    handles = driver.window_handles
    if index is not None:
        if index < 0 or index >= len(handles):
            raise AssertionError(f"❌ Window index {index} out of range (0–{len(handles) - 1})")
        driver.switch_to.window(handles[index])
    driver.close()
    remaining = driver.window_handles
    if remaining:
        driver.switch_to.window(remaining[0])
        logger.info("🗑️  Window closed. Back to window 0.")
    else:
        logger.warning("⚠️  No windows remaining after close.")


def close_all_windows(driver):
    """Close every window except window 0, then focus window 0."""
    handles = driver.window_handles
    for h in handles[1:]:
        driver.switch_to.window(h)
        driver.close()
    remaining = driver.window_handles
    if remaining:
        driver.switch_to.window(remaining[0])
    logger.info("🗑️  Closed all windows. Back to window 0.")


def list_windows(driver):
    """Log all open window handles with their index."""
    handles = driver.window_handles
    logger.info("📋 Open windows (%d):", len(handles))
    for i, h in enumerate(handles):
        logger.info("  [%d] %s", i, h)


# ─────────────────────────────────────────────────────────────────────────────
# IFRAME / FRAME MANAGEMENT  (WebView / mobile-browser context only)
# Works when the driver has already switched into a WEBVIEW context.
# In NATIVE_APP context, switch_to.frame() has no effect.
# ─────────────────────────────────────────────────────────────────────────────

def switch_iframe(driver, selector: str):
    """Switch driver context into an iframe.
    selector can be: a digit (0-based index), an XPath, or a name/id string.
    """
    if selector.strip().isdigit():
        driver.switch_to.frame(int(selector))
        logger.info("🖼️  Switched to iframe index %s", selector)
    else:
        from appium.webdriver.common.appiumby import AppiumBy
        try:
            el = driver.find_element(AppiumBy.XPATH, selector)
            driver.switch_to.frame(el)
            logger.info("🖼️  Switched to iframe by xpath: %s", selector)
        except Exception:
            driver.switch_to.frame(selector)   # fall back: name or id
            logger.info("🖼️  Switched to iframe by name/id: %s", selector)


def exit_iframe(driver):
    """Return to the main document (default content), exiting any active iframe."""
    driver.switch_to.default_content()
    logger.info("🖼️  Exited iframe — back to default content.")


def scroll_to_element(driver, name: str, platform: str, max_swipes: int = 10):
    """
    Scroll the named element into the visible centre of the screen.

    Android: uses UiScrollable.scrollIntoView (fastest, native).
    iOS    : uses mobile:scroll with toVisible=true after element lookup.
    Falls back to repeated swipe-up if the native strategies fail.
    """
    from appium.webdriver.common.appiumby import AppiumBy
    name = resolve_variables(name)
    locator = _get_appium_locator(name, platform)

    if platform == "android":
        # ── Try UiScrollable (works for any scrollable container) ─────────────
        for attr, uia_key in [
            ("resource_id",     "resourceId"),
            ("accessibility_id", "description"),
            ("text",             "text"),
        ]:
            val = locator.get(attr, "")
            if not val:
                continue
            uia_expr = (
                f'new UiScrollable(new UiSelector().scrollable(true))'
                f'.scrollIntoView(new UiSelector().{uia_key}("{val}"))'
            )
            try:
                driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, uia_expr)
                logger.info("📜 Scrolled to '%s' via UiScrollable (%s)", name, uia_key)
                return
            except Exception:
                pass

    elif platform == "ios":
        # ── Try mobile:scroll with element reference ──────────────────────────
        try:
            el = _find_element(driver, name, platform)
            driver.execute_script("mobile: scroll", {"element": el.id, "toVisible": True})
            logger.info("📜 Scrolled to '%s' via mobile:scroll", name)
            return
        except Exception:
            pass

    # ── Fallback: swipe-up until element found ────────────────────────────────
    for attempt in range(max_swipes):
        try:
            el = _find_element(driver, name, platform)
            if el.is_displayed():
                logger.info("📜 Element '%s' visible after %d swipes", name, attempt)
                return
        except Exception:
            pass
        swipe_up(driver)
        time.sleep(0.4)
    raise AssertionError(
        f"❌ Could not scroll element '{name}' into view after {max_swipes} swipes"
    )
