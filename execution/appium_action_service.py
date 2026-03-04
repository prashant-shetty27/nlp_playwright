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
import threading
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
_locator_cache_lock = threading.Lock()


def invalidate_locator_cache():
    """Force the next call to _load_app_locators() to re-read the file."""
    global _locator_cache, _locator_cache_mtime
    with _locator_cache_lock:
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
    with _locator_cache_lock:
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


def _xpath_literal(value: str) -> str:
    """Safe XPath literal builder for values containing quotes."""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    out = []
    for i, part in enumerate(parts):
        if part:
            out.append(f"'{part}'")
        if i < len(parts) - 1:
            out.append('"\'"')
    return f"concat({', '.join(out)})"


def _pick_indexed_element(elements: list, el_index: str):
    """Pick the requested index token from an element list; returns None if out of range."""
    if not elements:
        return None
    if el_index == "any":
        return elements[0]
    num_idx = _INDEX_MAP.get(el_index, 0)
    try:
        return elements[num_idx]
    except Exception:
        return None


def _parse_bounds(bounds_str: str) -> tuple[int, int] | None:
    """
    Parse Appium bounds string '[x1,y1][x2,y2]' into center coordinates.
    Returns (cx, cy) or None if parsing fails.
    Used as last-resort tap fallback when all element strategies fail (Android only).
    """
    m = re.findall(r'\[(\d+),(\d+)\]', bounds_str)
    if len(m) == 2:
        cx = (int(m[0][0]) + int(m[1][0])) // 2
        cy = (int(m[0][1]) + int(m[1][1])) // 2
        return cx, cy
    return None


def _w3c_swipe(driver, start_x: int, start_y: int, end_x: int, end_y: int,
               duration_ms: int = 600) -> None:
    """
    W3C Actions API swipe — industry-standard replacement for deprecated driver.swipe().
    Works on both iOS (XCUITest) and Android (UiAutomator2) with Appium 2.x.
    """
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.actions.action_builder import ActionBuilder
    from selenium.webdriver.common.actions.pointer_input import PointerInput
    from selenium.webdriver.common.actions import interaction

    actions = ActionChains(driver)
    actions.w3c_actions = ActionBuilder(
        driver, mouse=PointerInput(interaction.POINTER_TOUCH, "touch")
    )
    (actions.w3c_actions.pointer_action
        .move_to_location(start_x, start_y)
        .pointer_down()
        .pause(duration_ms / 1000)
        .move_to_location(end_x, end_y)
        .pointer_up()
    )
    actions.perform()


def _w3c_tap(driver, x: int, y: int) -> None:
    """
    W3C Actions API tap at coordinates — industry-standard replacement for deprecated driver.tap().
    Works on both iOS (XCUITest) and Android (UiAutomator2) with Appium 2.x.
    """
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.actions.action_builder import ActionBuilder
    from selenium.webdriver.common.actions.pointer_input import PointerInput
    from selenium.webdriver.common.actions import interaction

    actions = ActionChains(driver)
    actions.w3c_actions = ActionBuilder(
        driver, mouse=PointerInput(interaction.POINTER_TOUCH, "touch")
    )
    (actions.w3c_actions.pointer_action
        .move_to_location(x, y)
        .pointer_down()
        .pause(0.05)
        .pointer_up()
    )
    actions.perform()


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
            # name predicate — matches elements whose `name` attr differs from accessibility_id
            (AppiumBy.IOS_PREDICATE,
             f'name == "{locator["name"]}"'
             if locator.get("name") else None),
            # label predicate only for non-input elements (avoids placeholder text matches)
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
        num_idx = _INDEX_MAP.get(el_index)
        if num_idx is None:
            logger.warning("⚠️  Unknown el_index '%s' — defaulting to first match (0)", el_index)
            num_idx = 0
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

    # ── Basic healing fallback chain ─────────────────────────────────────────
    # 1) broad text/label/name/value XPath search
    text_candidates = [
        locator.get("text"),
        locator.get("label"),
        locator.get("name"),
        locator.get("accessibility_id"),
    ]
    for token in [t for t in text_candidates if t]:
        try:
            lit = _xpath_literal(str(token))
            xp = (
                f"//*[contains(@text, {lit}) or contains(@content-desc, {lit}) "
                f"or contains(@label, {lit}) or contains(@name, {lit}) "
                f"or contains(@value, {lit})]"
            )
            els = driver.find_elements(AppiumBy.XPATH, xp)
            picked = _pick_indexed_element(els, el_index)
            if picked is not None:
                logger.info("🏥 Appium fallback healed '%s' via text token", base_name)
                return picked
        except Exception as e:
            errors.append(f"fallback[text]: {e}")

    # 2) class-only fallback as last non-ML attempt
    class_name = locator.get("class_name")
    if class_name:
        try:
            els = driver.find_elements(AppiumBy.CLASS_NAME, class_name)
            picked = _pick_indexed_element(els, el_index)
            if picked is not None:
                logger.info("🏥 Appium fallback healed '%s' via class_name", base_name)
                return picked
        except Exception as e:
            errors.append(f"fallback[class_name]: {e}")

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
            # Appium-Python-Client 3.x+: pass recording options as kwargs (not options={})
            driver.start_recording_screen(
                videoQuality="high",
                timeLimit="1800",   # 30 min hard cap
                bitRate=4000000,
            )
        else:
            driver.start_recording_screen(
                videoQuality="medium",
                timeLimit="1800",
                videoFps="30",
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
    try:
        el = _find_element(driver, lookup, platform)
        el.click()
        logger.info("👆 Tapped: '%s'%s", name, f"[{el_index}]" if el_index != "any" else "")
    except RuntimeError:
        # Last-resort: coordinate tap via stored bounds (Android only — screen-size-dependent)
        if platform == "android":
            try:
                locator = _get_appium_locator(name, platform)
                coords  = _parse_bounds(locator.get("bounds", ""))
                if coords:
                    _w3c_tap(driver, *coords)
                    logger.info("👆 Tapped '%s' via bounds coordinates %s", name, coords)
                    return
            except Exception:
                pass
        raise


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


def tap_coordinates(driver, x: int, y: int, platform: str = "android"):
    """Tap at raw screen coordinates. Uses the native gesture API per platform."""
    x, y = int(x), int(y)
    try:
        if platform == "ios":
            # XCUITest: mobile:tap with absolute coordinates
            driver.execute_script("mobile: tap", {"x": x, "y": y})
        else:
            # UiAutomator2: mobile:clickGesture with absolute coordinates
            driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
    except Exception:
        # Universal W3C Actions API fallback (Appium 2.x, both platforms)
        _w3c_tap(driver, x, y)
    logger.info("👆 Tapped at (%d, %d)", x, y)


def swipe_up(driver, duration_ms: int = 600):
    """Swipe up (scroll down content). Uses W3C Actions API (Appium 2.x standard)."""
    size = driver.get_window_size()
    w, h = size["width"], size["height"]
    _w3c_swipe(driver, w // 2, int(h * 0.75), w // 2, int(h * 0.25), duration_ms)
    logger.info("🔼 Swiped up (scroll down)")


def swipe_down(driver, duration_ms: int = 600):
    """Swipe down (scroll up content). Uses W3C Actions API (Appium 2.x standard)."""
    size = driver.get_window_size()
    w, h = size["width"], size["height"]
    _w3c_swipe(driver, w // 2, int(h * 0.25), w // 2, int(h * 0.75), duration_ms)
    logger.info("🔽 Swiped down (scroll up)")


def swipe_left(driver, duration_ms: int = 400):
    """Swipe left. Uses W3C Actions API (Appium 2.x standard)."""
    size = driver.get_window_size()
    w, h = size["width"], size["height"]
    _w3c_swipe(driver, int(w * 0.8), h // 2, int(w * 0.2), h // 2, duration_ms)
    logger.info("◀️  Swiped left")


def swipe_right(driver, duration_ms: int = 400):
    """Swipe right. Uses W3C Actions API (Appium 2.x standard)."""
    size = driver.get_window_size()
    w, h = size["width"], size["height"]
    _w3c_swipe(driver, int(w * 0.2), h // 2, int(w * 0.8), h // 2, duration_ms)
    logger.info("▶️  Swiped right")


def scroll_until_text_visible(driver, text: str, max_swipes: int = 8, wait_s: float = 0.5,
                              platform: str = "android"):
    """Swipe up repeatedly until text appears on screen. Platform-aware."""
    text = resolve_variables(text)
    from appium.webdriver.common.appiumby import AppiumBy

    for _ in range(max_swipes):
        if platform == "android":
            # Android: UiScrollable scrollIntoView (native, fast)
            try:
                el = driver.find_element(
                    AppiumBy.ANDROID_UIAUTOMATOR,
                    f'new UiScrollable(new UiSelector().scrollable(true))'
                    f'.scrollIntoView(new UiSelector().textContains("{text}"))'
                )
                if el:
                    logger.info("📜 Scrolled to text: '%s'", text)
                    return
            except Exception:
                pass
        else:
            # iOS: mobile:scroll with NSPredicate text matching
            try:
                driver.execute_script("mobile: scroll", {
                    "direction": "down",
                    "predicateString": f'label CONTAINS "{text}" OR name CONTAINS "{text}" OR value CONTAINS "{text}"',
                })
                logger.info("📜 Scrolled to text: '%s'", text)
                return
            except Exception:
                pass

        # Fallback: XPath scan (works on both platforms)
        try:
            els = driver.find_elements(
                AppiumBy.XPATH,
                f"//*[contains(@text,'{text}') or contains(@content-desc,'{text}') "
                f"or contains(@label,'{text}') or contains(@value,'{text}')]"
            )
            if els:
                logger.info("📜 Found text via XPath: '%s'", text)
                return
        except Exception:
            pass

        swipe_up(driver)
        time.sleep(wait_s)

    logger.warning("⚠️  Text '%s' not found after %d swipes", text, max_swipes)


def scroll_until_element_visible(driver, name: str, platform: str, max_swipes: int = 8):
    """Scroll until a named element appears. Uses native platform strategies first."""
    name = resolve_variables(name)
    from appium.webdriver.common.appiumby import AppiumBy

    if platform == "android":
        # Android: UiScrollable.scrollIntoView is native and crosses all containers
        try:
            locator = _get_appium_locator(name, platform)
            for attr, uia_key in [("resource_id", "resourceId"), ("accessibility_id", "description")]:
                val = locator.get(attr, "")
                if not val:
                    continue
                try:
                    driver.find_element(
                        AppiumBy.ANDROID_UIAUTOMATOR,
                        f'new UiScrollable(new UiSelector().scrollable(true))'
                        f'.scrollIntoView(new UiSelector().{uia_key}("{val}"))'
                    )
                    logger.info("📜 Scrolled to element '%s' via UiScrollable (%s)", name, uia_key)
                    return
                except Exception:
                    pass
        except ValueError:
            pass  # locator not in DB — fall through to generic swipe

    elif platform == "ios":
        # iOS: mobile:scroll with toVisible=True — WDA scrolls the element into viewport
        try:
            el = _find_element(driver, name, platform)
            driver.execute_script("mobile: scroll", {"element": el, "toVisible": True})
            logger.info("📜 Scrolled to element '%s' via mobile:scroll", name)
            return
        except Exception:
            pass

    # Fallback: swipe-up until element becomes visible (both platforms)
    for i in range(max_swipes):
        try:
            el = _find_element(driver, name, platform)
            if el.is_displayed():
                logger.info("📜 Element '%s' visible after %d swipes", name, i)
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
        if platform == "ios":
            # XCUITest: mobile:doubleTap (iOS-only)
            driver.execute_script("mobile: doubleTap", {"element": el.id})
        else:
            # UiAutomator2: mobile:doubleClickGesture (Android-only)
            driver.execute_script("mobile: doubleClickGesture", {"elementId": el.id})
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
        if platform == "ios":
            # XCUITest: mobile:touchAndHold with duration in seconds (iOS-only)
            driver.execute_script("mobile: touchAndHold", {"element": el.id, "duration": duration_s})
        else:
            # UiAutomator2: mobile:longClickGesture with duration in milliseconds (Android-only)
            driver.execute_script("mobile: longClickGesture", {
                "elementId": el.id, "duration": int(duration_s * 1000)
            })
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
    if platform == "ios":
        # XCUITest attribute priority:
        #   label  = visible text shown on screen (most useful)
        #   .text  = maps to 'value' (typed content in input fields)
        #   value  = input field current value
        #   name   = accessibility identifier (rarely the display text)
        text = (el.get_attribute("label") or el.text or el.get_attribute("value") or "")
    else:
        # UiAutomator2 attribute priority:
        #   .text        = the element's visible text (primary)
        #   content-desc = accessibility description (TalkBack label)
        text = (el.text or el.get_attribute("content-desc") or "")
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


def dismiss_play_rating(driver, platform: str = "android") -> bool:
    """
    Dismiss a Google Play In-App Review (rating) popup. Android-only.

    The Play rating dialog is rendered by the Play Store process, NOT the app,
    so driver.switch_to.alert and mobile:alert both miss it.
    UiAutomator2 can reach across process boundaries and find the dismiss button.

    Dismiss button text varies by locale/Play version — we try all known variants.
    Falls back to pressing BACK if no button is found.

    Returns True if something was dismissed, False if no popup detected or not Android.
    """
    if platform != "android":
        return False  # Play Store ratings are Android-only; skip on iOS entirely

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


def _normalize_alert_label(value: str) -> str:
    """Normalize alert button labels for robust matching."""
    cleaned = (value or "").strip().lower().replace("’", "'")
    return re.sub(r"\s+", " ", cleaned)


def _pick_ios_alert_button(buttons: list[str], prefer: str = "dismiss") -> str | None:
    """
    Pick the most appropriate iOS alert button label.
    prefer:
      - dismiss: deny/cancel style
      - accept : allow/ok style
    """
    if not buttons:
        return None

    normalized = [(raw, _normalize_alert_label(raw)) for raw in buttons if isinstance(raw, str)]
    if not normalized:
        return None

    negative_tokens = (
        "don't allow", "dont allow", "not now", "no thanks", "deny",
        "cancel", "later", "skip", "close", "don't share", "dont share",
    )
    positive_tokens = (
        "allow", "ok", "continue", "always allow", "allow once",
        "allow while using app", "yes", "share",
    )

    def _find(tokens: tuple[str, ...]) -> str | None:
        for raw, norm in normalized:
            if any(token in norm for token in tokens):
                return raw
        return None

    if prefer == "dismiss":
        return _find(negative_tokens) or _find(positive_tokens) or normalized[0][0]
    return _find(positive_tokens) or _find(negative_tokens) or normalized[-1][0]


def _dismiss_ios_alert_once(driver, prefer: str = "dismiss") -> bool:
    """
    Attempt to dismiss one visible iOS alert/sheet.
    Returns True if an alert button was tapped, else False.
    """
    from appium.webdriver.common.appiumby import AppiumBy

    prefer_action = "dismiss" if prefer == "dismiss" else "accept"
    fallback_action = "accept" if prefer_action == "dismiss" else "dismiss"

    # 1) WDA-native button enumeration + explicit button tap
    try:
        buttons = driver.execute_script("mobile: alert", {"action": "getButtons"}) or []
        if isinstance(buttons, list) and buttons:
            chosen = _pick_ios_alert_button(buttons, prefer=prefer)
            if chosen:
                try:
                    driver.execute_script(
                        "mobile: alert",
                        {"action": "accept", "buttonLabel": chosen},
                    )
                    logger.info("🔔 iOS alert handled via button '%s'", chosen)
                    return True
                except Exception:
                    pass
    except Exception:
        pass

    # 2) WDA generic action fallback
    for action in (prefer_action, fallback_action):
        try:
            driver.execute_script("mobile: alert", {"action": action})
            logger.info("🔔 iOS alert %sed via mobile: alert", action)
            return True
        except Exception:
            pass

    # 3) Selenium alert fallback
    for action in (prefer_action, fallback_action):
        try:
            alert = driver.switch_to.alert
            if action == "dismiss":
                alert.dismiss()
            else:
                alert.accept()
            logger.info("🔔 iOS alert %sed via switch_to.alert", action)
            return True
        except Exception:
            pass

    # 4) SpringBoard/dialog button fallback when alert APIs miss overlays
    button_candidates = [
        "Don't Allow",
        "Dont Allow",
        "Do Not Allow",
        "Not Now",
        "No Thanks",
        "Cancel",
        "Later",
        "Not Allow",
        "Allow",
        "Allow Once",
        "Allow While Using App",
        "Allow While Using",
        "Allow Full Access",
        "OK",
        "Continue",
        "Paste",
        "Join",
    ]
    if prefer != "dismiss":
        button_candidates = button_candidates[6:] + button_candidates[:6]

    for label in button_candidates:
        lit = _xpath_literal(label)
        xpaths = [
            f"//XCUIElementTypeButton[@label={lit} or @name={lit}]",
            f"//XCUIElementTypeButton[contains(@label,{lit}) or contains(@name,{lit})]",
        ]
        for xp in xpaths:
            try:
                elements = driver.find_elements(AppiumBy.XPATH, xp)
                for el in elements:
                    if el.is_displayed():
                        el.click()
                        logger.info("🔔 iOS alert handled via button text '%s'", label)
                        return True
            except Exception:
                pass

    # 5) Generic visible alert/sheet button fallback (no hardcoded labels)
    try:
        all_buttons = driver.find_elements(
            AppiumBy.XPATH,
            "//XCUIElementTypeAlert//XCUIElementTypeButton | "
            "//XCUIElementTypeSheet//XCUIElementTypeButton | "
            "//XCUIElementTypeDialog//XCUIElementTypeButton"
        )
        visible_buttons = []
        for btn in all_buttons:
            try:
                if btn.is_displayed():
                    label = (
                        btn.get_attribute("label")
                        or btn.get_attribute("name")
                        or btn.text
                        or ""
                    ).strip()
                    visible_buttons.append((label, btn))
            except Exception:
                pass

        if visible_buttons:
            labels = [lbl for lbl, _ in visible_buttons]
            chosen_label = _pick_ios_alert_button(labels, prefer=prefer) or labels[0]
            for lbl, btn in visible_buttons:
                if lbl == chosen_label:
                    btn.click()
                    logger.info("🔔 iOS alert handled via generic dialog button '%s'", chosen_label)
                    return True
    except Exception:
        pass

    # 6) Diagnostics: log visible iOS buttons to help tune matching quickly
    try:
        raw_buttons = driver.find_elements(AppiumBy.XPATH, "//XCUIElementTypeButton")
        visible = []
        for btn in raw_buttons[:40]:
            try:
                if btn.is_displayed():
                    label = (
                        btn.get_attribute("label")
                        or btn.get_attribute("name")
                        or btn.text
                        or ""
                    ).strip()
                    if label:
                        visible.append(label)
            except Exception:
                pass
        if visible:
            logger.info("ℹ️  iOS visible button labels: %s", sorted(set(visible))[:20])
    except Exception:
        pass

    return False


def dismiss_alerts(driver, platform: str = "android", attempts: int = 3) -> bool:
    """
    Dismiss any visible system alerts, permission dialogs, or Google Play rating popups.
    Android: Play rating popup → UiAutomator2 alert dismiss → mobile:alert.
    iOS    : multi-stage chain (mobile:alert buttons/actions, switch_to.alert, visible button fallback).
    Safe to call even when no alert is showing.
    """
    any_dismissed = False

    if platform == "android":
        # Play rating dialog is Android-only and invisible to mobile:alert
        any_dismissed = dismiss_play_rating(driver, platform) or any_dismissed

    for _ in range(attempts):
        dismissed = False
        if platform == "ios":
            dismissed = _dismiss_ios_alert_once(driver, prefer="dismiss")
            if not dismissed:
                dismissed = _dismiss_ios_alert_once(driver, prefer="accept")
        else:
            for action in ("dismiss", "accept"):
                try:
                    driver.execute_script("mobile: alert", {"action": action})
                    logger.info("🔔 System alert %sed", action)
                    dismissed = True
                    break
                except Exception:
                    pass

        if not dismissed:
            break
        any_dismissed = True
        time.sleep(0.6)

    return any_dismissed


def tap_by_text(driver, text: str, platform: str = "android"):
    """Tap the first visible element whose label / text / value contains the given text."""
    text = resolve_variables(text)
    from appium.webdriver.common.appiumby import AppiumBy

    if platform == "ios":
        # iOS: NSPredicate is server-side evaluated (fastest); XPath is fallback
        strategies = [
            (AppiumBy.IOS_PREDICATE,
             f'label CONTAINS "{text}" OR name CONTAINS "{text}" OR value CONTAINS "{text}"'),
            (AppiumBy.ACCESSIBILITY_ID, text),
            (AppiumBy.XPATH,
             f"//*[contains(@label,'{text}') or contains(@value,'{text}') "
             f"or contains(@name,'{text}')]"),
        ]
    else:
        # Android: UiSelector.textContains is server-side and crosses containers (fastest)
        strategies = [
            (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{text}")'),
            (AppiumBy.ACCESSIBILITY_ID, text),
            (AppiumBy.XPATH,
             f"//*[contains(@text,'{text}') or contains(@content-desc,'{text}')]"),
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


def type_into_active(driver, text: str, platform: str = "android"):
    """Type text into whatever element is currently focused (no element lookup needed)."""
    text = resolve_variables(text)
    if platform == "ios":
        # iOS: mobile:typeText is WDA-native — avoids keyboard lag and handles special chars
        try:
            driver.execute_script("mobile: typeText", {"text": text})
            logger.info("⌨️  Typed into active element: '%s'", text)
            return
        except Exception:
            pass
        # iOS fallback: send_keys on active element (may 404 if nothing focused)
        try:
            driver.switch_to.active_element.send_keys(text)
            logger.info("⌨️  Typed (send_keys) into active element: '%s'", text)
        except Exception as err:
            logger.warning("⚠️  type_into_active iOS fallback failed (no focused element?): %s", err)
            raise
    else:
        # Android: mobile:typeText is iOS-only — go directly to send_keys on active element
        try:
            driver.switch_to.active_element.send_keys(text)
            logger.info("⌨️  Typed into active element: '%s'", text)
        except Exception as err:
            logger.warning("⚠️  type_into_active Android send_keys failed: %s", err)
            raise


def press_back(driver):
    """Press the Android back button (or iOS swipe back)."""
    try:
        driver.back()
        logger.info("⬅️  Pressed back")
    except Exception as e:
        logger.warning("⚠️  back(): %s", e)


def press_home(driver, platform: str = "android"):
    """Press the home button / go to home screen."""
    try:
        if platform == "ios":
            # iOS XCUITest: mobile:pressButton is the correct API
            driver.execute_script("mobile: pressButton", {"name": "home"})
        else:
            # Android UiAutomator2: KEYCODE_HOME = 3
            driver.execute_script("mobile: pressKey", {"keycode": 3})
        logger.info("🏠 Pressed home")
    except Exception as e:
        logger.warning("⚠️  home(): %s", e)


def press_enter(driver, platform: str = "android"):
    """Press enter/return/search key on the keyboard."""
    from appium.webdriver.common.appiumby import AppiumBy

    if platform == "android":
        # Android: KEYCODE_ENTER (66) via UiAutomator2 — go directly, skip iOS attempts
        try:
            driver.execute_script("mobile: pressKey", {"keycode": 66})
            logger.info("↩️  Pressed enter (Android KEYCODE_ENTER)")
        except Exception as e:
            logger.warning("⚠️  enter() Android: %s", e)
        return

    # ── iOS cascade ───────────────────────────────────────────────────────────
    # 1. HID keyboard Return key (real device, XCUITest — most reliable)
    try:
        driver.execute_script("mobile: performIoHidEvent", {
            "page": 0x07, "usage": 0x28, "durationSeconds": 0.005
        })
        logger.info("↩️  Pressed enter (HID Return key)")
        return
    except Exception:
        pass

    # 2. Tap visible keyboard action button (simulator or no HID support)
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

    # 3. iOS typeText newline (last resort — triggers keyboard action)
    try:
        driver.execute_script("mobile: typeText", {"text": "\n"})
        logger.info("↩️  Pressed enter (typeText \\n)")
    except Exception as e:
        logger.warning("⚠️  enter() iOS all strategies failed: %s", e)


def hide_keyboard(driver, platform: str = "android"):
    """Dismiss the on-screen keyboard."""
    if platform == "ios":
        # iOS: tap the Done / Hide-keyboard button on the keyboard toolbar first
        from appium.webdriver.common.appiumby import AppiumBy
        for btn_name in ("Done", "Hide keyboard", "Return"):
            try:
                el = driver.find_element(
                    AppiumBy.XPATH,
                    f"//XCUIElementTypeKeyboard//XCUIElementTypeButton[@name='{btn_name}']",
                )
                el.click()
                logger.info("⌨️  Keyboard hidden via '%s' button", btn_name)
                return
            except Exception:
                pass
    # Both platforms: standard Appium hide_keyboard (taps a Done/Hide button if visible)
    try:
        driver.hide_keyboard()
        logger.info("⌨️  Keyboard hidden")
    except Exception:
        pass  # keyboard may already be dismissed — not an error


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
