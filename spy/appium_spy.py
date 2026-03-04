"""
spy/appium_spy.py
Interactive element recorder for Android & iOS apps using Appium.

Flow:
  1. Connects to Appium + launches the app
  2. Fetches page source (XML)
  3. Parses and lists interactive elements numbered
  4. You type the element number + give it a friendly name
  5. Pick an action → step added to the running flow AND executed on device
  6. Use add/edit/delete/move/undo commands to manage flow steps
  7. Saves locators to data/locators_manual.json under "android" or "ios" key
  8. At the end, saves a ready-to-run .flow file (+ optional .flow.json)

Usage:
  # Terminal 1 — start Appium (if not running)
  appium

  # Terminal 2 — record elements + flow
  python spy/appium_spy.py --platform android
  python spy/appium_spy.py --platform ios

  # Or run with custom caps JSON file
  python spy/appium_spy.py --platform android --caps caps/my_device.json
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

BASE_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCATORS_FILE = os.path.join(BASE_DIR, "data", "locators_manual.json")


# ─────────────────────────────────────────────────────────────────────────────
# LOCATOR FILE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _load_locators() -> dict:
    if os.path.exists(LOCATORS_FILE):
        try:
            with open(LOCATORS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️  locators_manual.json unreadable ({e}) — starting with empty database.")
    return {}


def _save_locators(data: dict) -> None:
    os.makedirs(os.path.dirname(LOCATORS_FILE), exist_ok=True)
    with open(LOCATORS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# XML PAGE SOURCE PARSER
# ─────────────────────────────────────────────────────────────────────────────

_INTERACTIVE_TAGS = {
    # Android
    "android.widget.Button",
    "android.widget.EditText",
    "android.widget.TextView",
    "android.widget.ImageButton",
    "android.widget.ImageView",
    "android.widget.CheckBox",
    "android.widget.RadioButton",
    "android.widget.Switch",
    "android.widget.Spinner",
    "android.widget.ListView",
    "android.view.View",
    "android.widget.FrameLayout",
    "android.widget.LinearLayout",
    "android.widget.RelativeLayout",
    # iOS
    "XCUIElementTypeButton",
    "XCUIElementTypeTextField",
    "XCUIElementTypeSecureTextField",
    "XCUIElementTypeSearchField",
    "XCUIElementTypeStaticText",
    "XCUIElementTypeImage",
    "XCUIElementTypeCell",
    "XCUIElementTypeSwitch",
    "XCUIElementTypeLink",
    "XCUIElementTypeOther",
    "XCUIElementTypeNavigationBar",
    "XCUIElementTypeTabBar",
}


def _best_locator(el: ET.Element, platform: str) -> dict:
    """Extract the best available locator strategies from an XML element."""
    attrib = el.attrib
    locator: dict = {}

    if platform == "android":
        resource_id = attrib.get("resource-id", "").strip()
        acc_id      = attrib.get("content-desc", "").strip()
        text        = attrib.get("text", "").strip()
        class_name  = attrib.get("class", "").strip()
        bounds      = attrib.get("bounds", "").strip()

        if acc_id:
            locator["accessibility_id"] = acc_id
        if resource_id:
            locator["resource_id"] = resource_id
        if text:
            locator["text"] = text
        if class_name:
            locator["class_name"] = class_name

        parts = []
        if resource_id:
            parts.append(f"[@resource-id='{resource_id}']")
        elif text:
            parts.append(f"[@text='{text}']")
        elif acc_id:
            parts.append(f"[@content-desc='{acc_id}']")
        locator["xpath"] = f"//{class_name}{''.join(parts)}" if parts else f"//{class_name}"

        if bounds:
            locator["bounds"] = bounds

    elif platform == "ios":
        acc_id  = attrib.get("name", "").strip()
        label   = attrib.get("label", "").strip()
        value   = attrib.get("value", "").strip()
        el_type = el.tag.strip()

        if acc_id:
            locator["accessibility_id"] = acc_id
        if label:
            locator["label"] = label
        if value:
            locator["value"] = value
        locator["class_name"] = el_type

        if acc_id:
            locator["xpath"] = f"//{el_type}[@name='{acc_id}']"
        elif label:
            locator["xpath"] = f"//{el_type}[@label='{label}']"
        elif value:
            locator["xpath"] = f"//{el_type}[@value='{value}']"
        else:
            locator["xpath"] = f"//{el_type}"

    return locator


def _parse_elements(xml_source: str, platform: str) -> list[dict]:
    """Parse page source XML and return list of candidate elements."""
    try:
        root = ET.fromstring(xml_source)
    except ET.ParseError as e:
        logger.error("❌ Failed to parse page source XML: %s", e)
        return []

    candidates = []

    def _walk(node: ET.Element, depth: int = 0):
        tag    = node.tag
        attrib = node.attrib

        has_resource_id = bool(attrib.get("resource-id", "").strip())
        has_acc_id      = bool(attrib.get("content-desc", "").strip()) or bool(attrib.get("name", "").strip())
        has_text        = bool(attrib.get("text", "").strip()) or bool(attrib.get("label", "").strip())
        is_clickable    = attrib.get("clickable", "false").lower() == "true"
        is_enabled      = attrib.get("enabled", "true").lower() == "true"
        is_interesting  = any([has_resource_id, has_acc_id, has_text]) and is_enabled

        if tag in _INTERACTIVE_TAGS or is_interesting or is_clickable:
            locator = _best_locator(node, platform)
            if locator:
                name_hint = (
                    attrib.get("content-desc")
                    or attrib.get("name")
                    or attrib.get("text")
                    or attrib.get("label")
                    or attrib.get("resource-id", "").split("/")[-1]
                    or tag.split(".")[-1]
                )
                candidates.append({
                    "index":    len(candidates) + 1,
                    "tag":      tag,
                    "hint":     name_hint.strip()[:60],
                    "locator":  locator,
                    "clickable": is_clickable,
                })

        for child in node:
            _walk(child, depth + 1)

    _walk(root)
    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# APPIUM CONNECTION
# ─────────────────────────────────────────────────────────────────────────────

def _start_appium_session(platform: str, caps_override: dict | None = None):
    """Start an Appium session and return (driver, effective_caps)."""
    try:
        from appium import webdriver as appium_webdriver
    except ImportError:
        logger.error("❌ Appium Python client not installed. Run: pip install Appium-Python-Client")
        sys.exit(1)

    sys.path.insert(0, BASE_DIR)
    from config import settings
    import glob

    caps = caps_override or (
        settings.ANDROID_CAPABILITIES if platform == "android"
        else settings.IOS_CAPABILITIES
    )

    if not caps:
        logger.error(
            "❌ No capabilities configured for platform '%s'.\n"
            "   Set ANDROID_CAPABILITIES or IOS_CAPABILITIES in .env as a JSON string.",
            platform,
        )
        sys.exit(1)

    if platform == "android" and not os.environ.get("ANDROID_HOME"):
        preferred_roots = [
            "/usr/local/share/android-commandlinetools",
            os.path.expanduser("~/Library/Android/sdk"),
        ]
        sdk_root = ""
        for root in preferred_roots:
            if os.path.exists(os.path.join(root, "build-tools")):
                sdk_root = root
                break
        if not sdk_root:
            candidates = glob.glob("/usr/local/Caskroom/android-platform-tools/*/platform-tools/adb")
            if candidates:
                sdk_root = os.path.dirname(os.path.dirname(sorted(candidates)[-1]))
        if sdk_root:
            os.environ["ANDROID_HOME"] = sdk_root
            os.environ["ANDROID_SDK_ROOT"] = sdk_root
            logger.info("✅ ANDROID_HOME auto-set: %s", sdk_root)

    server_url = settings.APPIUM_SERVER_URL
    logger.info("🔗 Connecting to Appium at %s ...", server_url)
    logger.info("📱 Platform   : %s", platform.upper())
    logger.info("📱 Device     : %s", caps.get("deviceName", caps.get("appium:deviceName",
                caps.get("udid", caps.get("appium:udid", "unknown")))))

    if platform == "android":
        from appium.options.android.uiautomator2.base import UiAutomator2Options
        options = UiAutomator2Options()
    else:
        from appium.options.ios.xcuitest.base import XCUITestOptions
        options = XCUITestOptions()

    for key, val in caps.items():
        if val == "" or val is None or key.startswith("_comment"):
            continue
        clean = key.replace("appium:", "")
        prop = getattr(type(options), clean, None)
        if prop is not None and isinstance(prop, property) and prop.fset is not None:
            try:
                setattr(options, clean, val)
                continue
            except Exception:
                pass
        options.set_capability(key, val)

    driver = appium_webdriver.Remote(server_url, options=options)
    logger.info("✅ Session started — session_id: %s", driver.session_id)
    return driver, caps


def _maybe_activate_target_app(driver, platform: str, caps: dict) -> None:
    """Try to bring the intended app to foreground after session starts."""
    try:
        if platform == "android":
            app_pkg = caps.get("appium:appPackage") or caps.get("appPackage")
            if app_pkg:
                driver.activate_app(app_pkg)
                logger.info("✅ Activated app package: %s", app_pkg)
            try:
                logger.info("📌 Current package: %s", driver.current_package)
            except Exception:
                pass
        else:
            bundle_id = caps.get("appium:bundleId") or caps.get("bundleId")
            if bundle_id:
                driver.activate_app(bundle_id)
                logger.info("✅ Activated iOS bundle: %s", bundle_id)
    except Exception as e:
        logger.warning("⚠️ Could not activate target app automatically: %s", e)


def _apply_runtime_cap_overrides(platform: str, base_caps: dict, args) -> dict:
    """Merge CLI app/runtime overrides into capabilities for recording."""
    caps = dict(base_caps or {})

    if args.udid:
        caps["appium:udid"] = args.udid
        caps["appium:deviceName"] = args.udid

    if args.app_file:
        app_abs = os.path.abspath(os.path.expanduser(args.app_file))
        if not os.path.exists(app_abs):
            logger.error("❌ App file not found: %s", app_abs)
            sys.exit(1)
        caps["appium:app"] = app_abs
        caps["appium:noReset"] = False
        caps.setdefault("appium:autoGrantPermissions", True)
        if platform == "android":
            if not args.app_id:
                caps.pop("appium:appPackage", None)
                caps.pop("appPackage", None)
            if not args.app_activity:
                caps.pop("appium:appActivity", None)
                caps.pop("appActivity", None)
        else:
            if not args.app_id:
                caps.pop("appium:bundleId", None)
                caps.pop("bundleId", None)
        if platform == "android" and not app_abs.lower().endswith(".apk"):
            logger.warning("⚠️ Android app file usually should be .apk (got: %s)", os.path.basename(app_abs))
        if platform == "ios" and not app_abs.lower().endswith(".ipa"):
            logger.warning("⚠️ iOS app file usually should be .ipa (got: %s)", os.path.basename(app_abs))

    if platform == "android":
        if args.app_id:
            caps["appium:appPackage"] = args.app_id
        if args.app_activity:
            caps["appium:appActivity"] = args.app_activity
    else:
        if args.app_id:
            caps["appium:bundleId"] = args.app_id

    return caps


def _get_connected_adb_devices() -> list[str]:
    """Return list of adb-connected device IDs in 'device' state."""
    try:
        out = subprocess.check_output(["adb", "devices"], text=True, stderr=subprocess.STDOUT)
    except Exception:
        return []

    devices: list[str] = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or "\t" not in line:
            continue
        serial, state = line.split("\t", 1)
        if state.strip() == "device":
            devices.append(serial.strip())
    return devices


def _ensure_android_device_available(caps: dict, wait_seconds: int = 0) -> None:
    """Ensure an Android device is connected before Appium session start."""
    target_udid = (
        caps.get("appium:udid") or caps.get("udid")
        or caps.get("appium:deviceName") or caps.get("deviceName") or ""
    ).strip()

    deadline = time.time() + max(wait_seconds, 0)
    while True:
        connected = _get_connected_adb_devices()
        if connected:
            if not target_udid or target_udid in connected:
                logger.info("✅ ADB connected device(s): %s", ", ".join(connected))
                return
            logger.warning("⚠️ Connected device(s): %s (target '%s' not present)",
                           ", ".join(connected), target_udid)
        if time.time() >= deadline:
            break
        logger.info("⏳ Waiting for Android device via ADB...")
        time.sleep(2)

    logger.error("❌ No usable Android device connected.")
    logger.error("   adb devices should show at least one '<serial>\tdevice'")
    logger.error("   Current connected: %s", ", ".join(_get_connected_adb_devices()) or "none")
    logger.error("   If using USB: keep screen unlocked and accept USB debugging prompt.")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# DISPLAY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _print_elements(elements: list[dict]) -> None:
    print("\n" + "═" * 70)
    print(f"  {'#':<5} {'TYPE':<30} {'HINT':<30} {'C'}")
    print("─" * 70)
    for el in elements:
        tag_short = el["tag"].split(".")[-1][:28]
        hint      = el["hint"][:28]
        clickable = "✓" if el["clickable"] else " "
        print(f"  {el['index']:<5} {tag_short:<30} {hint:<30} {clickable}")
    print("═" * 70)
    print(f"  Total: {len(elements)} elements   (C = clickable)\n")


def _print_flow(flow_steps: list[str]) -> None:
    if not flow_steps:
        print("  (no steps recorded yet)")
        return
    print("\n  📋 Recorded flow steps:")
    print("  " + "─" * 52)
    for i, s in enumerate(flow_steps, 1):
        print(f"  {i:3d}. {s}")
    print("  " + "─" * 52 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# FLOW STEP BUILDER
# ─────────────────────────────────────────────────────────────────────────────

# Keyword → auto-suggested variable names for 'type' action
_VAR_SUGGESTIONS = [
    (re.compile(r"search|query|keyword|find", re.I), "search_term"),
    (re.compile(r"password|passwd|pwd|secret", re.I), "password"),
    (re.compile(r"email|mail", re.I), "email"),
    (re.compile(r"phone|mobile|number|otp", re.I), "phone_number"),
    (re.compile(r"user|login|account", re.I), "username"),
    (re.compile(r"city|location|address|area|place", re.I), "location"),
    (re.compile(r"amount|price|cost|value", re.I), "amount"),
    (re.compile(r"date", re.I), "date"),
]


def _suggest_var_name(hint: str) -> str:
    """Suggest a snake_case variable name from element hint."""
    for pattern, suggestion in _VAR_SUGGESTIONS:
        if pattern.search(hint):
            return suggestion
    clean = re.sub(r"[^a-zA-Z0-9]+", "_", hint).strip("_").lower()
    return clean[:30] if clean else "input_value"


def _find_live_element(driver, locator: dict, platform: str):
    """Resolve an element from a locator dict with fallback strategies."""
    from appium.webdriver.common.appiumby import AppiumBy

    tries: list[tuple[str, str]] = []

    if locator.get("accessibility_id"):
        tries.append((AppiumBy.ACCESSIBILITY_ID, locator["accessibility_id"]))
    if platform == "android" and locator.get("resource_id"):
        tries.append((AppiumBy.ID, locator["resource_id"]))
    if platform == "android" and locator.get("text"):
        text = locator["text"].replace('"', '\\"')
        tries.append((AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{text}")'))
    if platform == "ios" and locator.get("label"):
        label = locator["label"].replace('"', '\\"')
        tries.append((AppiumBy.IOS_PREDICATE, f'label == "{label}"'))
    if locator.get("xpath"):
        tries.append((AppiumBy.XPATH, locator["xpath"]))
    if locator.get("class_name"):
        tries.append((AppiumBy.CLASS_NAME, locator["class_name"]))

    last_error = None
    for by, value in tries:
        try:
            return driver.find_element(by, value)
        except Exception as e:
            last_error = e

    raise RuntimeError(f"Could not resolve element. Last error: {last_error}")


def _safe_exec(action_fn, step: str) -> bool:
    """
    Execute action_fn on device. On failure, prompt user to discard or keep step.
    Returns True if the step should be added to the flow.
    """
    try:
        action_fn()
        return True
    except Exception as e:
        print(f"  ⚠️  Device action failed: {e}")
        try:
            choice = input(f"  Discard step '{step}'? [y=discard / n=keep anyway]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            return False
        return choice != "y"


_ACTION_MENU = """\
  Pick action for this element:
  [1] tap              — tap / click element
  [2] type             — type parameterized text  "${var}"
  [3] verify exists    — verify element exists on screen
  [4] verify text      — verify text is present on screen
  [5] double tap       — double tap element
  [6] long press       — long press / hold element
  [7] swipe            — swipe in a direction (scroll)
  [8] wait for element — wait until element is visible
  [9] store text       — capture element text into a variable
  [s] skip             — record locator only, no flow step"""


def _prompt_and_build_step(
    element_name: str,
    hint: str,
    locator: dict,
    driver,
    platform: str,
    flow_steps: list[str],
) -> None:
    """
    Show action menu, collect extra info, execute on device, append NLP step.
    Modifies flow_steps in-place.
    """
    print(_ACTION_MENU)
    try:
        choice = input("  Action [1-9/s]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return

    if not choice or choice in ("s", "skip"):
        return

    # ── [1] tap ───────────────────────────────────────────────────────────────
    if choice == "1":
        step = f"tap {element_name}"
        def _do():
            _find_live_element(driver, locator, platform).click()
        if _safe_exec(_do, step):
            flow_steps.append(step)
            print(f"  📝 Step added: {step}")

    # ── [2] type ──────────────────────────────────────────────────────────────
    elif choice == "2":
        suggested = _suggest_var_name(hint)
        try:
            var_raw = input(f"  Variable name [suggested: {suggested}]: ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        var_name = re.sub(r"[^a-zA-Z0-9_]", "_", var_raw or suggested).strip("_").lower()
        try:
            sample = input("  Sample value to type on device now: ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if not sample:
            print("  ⚠️  No sample value — skipping.")
            return
        step = f'type "${{{var_name}}}" into {element_name}'
        def _do():
            live = _find_live_element(driver, locator, platform)
            live.click()
            try:
                live.clear()
            except Exception:
                pass
            live.send_keys(sample)
        if _safe_exec(_do, step):
            flow_steps.append(step)
            print(f"  📝 Step added: {step}")

    # ── [3] verify exists ─────────────────────────────────────────────────────
    elif choice == "3":
        step = f"verify element exists {element_name}"
        def _do():
            _find_live_element(driver, locator, platform)
        if _safe_exec(_do, step):
            flow_steps.append(step)
            print(f"  📝 Step added: {step}")

    # ── [4] verify text ───────────────────────────────────────────────────────
    elif choice == "4":
        try:
            raw = input("  Variable name or literal text (e.g. 'verify_keyword' or 'Justdial'): ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if not raw:
            print("  ⚠️  No text specified — skipping.")
            return
        # single-word identifier → treat as variable name, else use as literal
        text_arg = f"${{{raw}}}" if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', raw) else raw
        step = f'verify text "{text_arg}"'
        def _do():
            _find_live_element(driver, locator, platform)
        if _safe_exec(_do, step):
            flow_steps.append(step)
            print(f"  📝 Step added: {step}")

    # ── [5] double tap ────────────────────────────────────────────────────────
    elif choice == "5":
        step = f"double tap {element_name}"
        def _do():
            live = _find_live_element(driver, locator, platform)
            try:
                driver.execute_script("mobile: doubleTap", {"element": live.id})
            except Exception:
                live.click()
                time.sleep(0.1)
                live.click()
        if _safe_exec(_do, step):
            flow_steps.append(step)
            print(f"  📝 Step added: {step}")

    # ── [6] long press ────────────────────────────────────────────────────────
    elif choice == "6":
        step = f"long press {element_name}"
        def _do():
            live = _find_live_element(driver, locator, platform)
            try:
                driver.execute_script("mobile: touchAndHold", {"element": live.id, "duration": 1.5})
            except Exception:
                from selenium.webdriver.common.action_chains import ActionChains
                ActionChains(driver).click_and_hold(live).pause(1.5).release().perform()
        if _safe_exec(_do, step):
            flow_steps.append(step)
            print(f"  📝 Step added: {step}")

    # ── [7] swipe ─────────────────────────────────────────────────────────────
    elif choice == "7":
        print("  Direction: [1] scroll down  [2] scroll up  [3] swipe left  [4] swipe right")
        try:
            dc = input("  Direction [1-4]: ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        dir_map = {
            "1": ("scroll down",  "down"),
            "2": ("scroll up",    "up"),
            "3": ("swipe left",   "left"),
            "4": ("swipe right",  "right"),
        }
        if dc not in dir_map:
            print("  ⚠️  Invalid direction — skipping.")
            return
        step, swipe_dir = dir_map[dc]
        def _do():
            size = driver.get_window_size()
            w, h, cx = size["width"], size["height"], size["width"] // 2
            if swipe_dir == "down":
                driver.swipe(cx, int(h * 0.3), cx, int(h * 0.7), 400)
            elif swipe_dir == "up":
                driver.swipe(cx, int(h * 0.7), cx, int(h * 0.3), 400)
            elif swipe_dir == "left":
                driver.swipe(int(w * 0.8), h // 2, int(w * 0.2), h // 2, 400)
            elif swipe_dir == "right":
                driver.swipe(int(w * 0.2), h // 2, int(w * 0.8), h // 2, 400)
        if _safe_exec(_do, step):
            flow_steps.append(step)
            print(f"  📝 Step added: {step}")

    # ── [8] wait for element ──────────────────────────────────────────────────
    elif choice == "8":
        step = f"wait for element {element_name}"
        def _do():
            _find_live_element(driver, locator, platform)
        if _safe_exec(_do, step):
            flow_steps.append(step)
            print(f"  📝 Step added: {step}")

    # ── [9] store text ────────────────────────────────────────────────────────
    elif choice == "9":
        default_var = re.sub(r"[^a-zA-Z0-9_]", "_", element_name).strip("_") + "_text"
        try:
            var_raw = input(f"  Variable name [suggested: {default_var}]: ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        var_name = re.sub(r"[^a-zA-Z0-9_]", "_", var_raw or default_var).strip("_").lower()
        step = f"store text from {element_name} as {var_name}"
        def _do():
            live = _find_live_element(driver, locator, platform)
            text = live.text or live.get_attribute("label") or live.get_attribute("value") or ""
            print(f"  📌 Current element text: '{text}'")
        if _safe_exec(_do, step):
            flow_steps.append(step)
            print(f"  📝 Step added: {step}")

    else:
        print(f"  ⚠️  Unknown action '{choice}' — skipping.")


def _parse_add_command(sub: str) -> str | None:
    """Convert 'add <subcommand>' into an NLP flow step string."""
    s = sub.strip().lower()
    if s == "back":
        return "press back"
    if s == "enter":
        return "press enter"
    if s == "dismiss":
        return "dismiss alerts"
    if s in ("scroll", "scroll down"):
        return "scroll down"
    if s == "scroll up":
        return "scroll up"
    if s == "swipe left":
        return "swipe left"
    if s == "swipe right":
        return "swipe right"
    if s.startswith("wait "):
        rest = s[5:].strip()
        try:
            n = float(rest)
            return f"wait {int(n)} seconds" if n == int(n) else f"wait {n} seconds"
        except ValueError:
            return "wait 1 seconds"
    if s.startswith("screenshot"):
        rest = sub.strip()[10:].strip()          # preserve original case for name
        name = rest.replace(" ", "_") if rest else "screenshot"
        return f"take screenshot as {name}"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# INTERACTIVE RECORDER
# ─────────────────────────────────────────────────────────────────────────────

def _get_element_by_index(elements: list[dict], idx_text: str) -> dict | None:
    if not idx_text.isdigit():
        return None
    idx = int(idx_text)
    return next((e for e in elements if e["index"] == idx), None)


def _record_session(driver, platform: str, screen_name: str, flow_steps: list) -> dict:
    """
    Interactive recording loop for one screen.
    Returns dict of {element_name: locator_dict} recorded in this session.
    flow_steps is mutated in-place with new NLP steps.
    """
    recorded: dict = {}

    while True:
        try:
            xml_source = driver.page_source
        except Exception as e:
            logger.error("❌ Failed to get page source: %s", e)
            break

        elements = _parse_elements(xml_source, platform)

        if not elements:
            print("\n⚠️  No elements found. Navigate to a screen with content.")
        else:
            _print_elements(elements)

        print("── Element recording ──────────────────────────────────────────────")
        print("  <number>                 — record element + pick action (executes on device)")
        print("── Navigation (no flow step) ───────────────────────────────────────")
        print("  tap <number>             — tap element")
        print("  type <number> <text>     — type into element")
        print("  back                     — press back")
        print("  wait <seconds>           — pause")
        print("  screenshot               — save screenshot to /tmp")
        print("  source                   — dump page source to /tmp/page_source.xml")
        print("  refresh                  — reload page source")
        print("── Flow management ─────────────────────────────────────────────────")
        print("  add back/enter/dismiss   — add structural step")
        print("  add scroll down/up       — add scroll step")
        print("  add swipe left/right     — add swipe step")
        print("  add wait <n>             — add wait step")
        print("  add screenshot <name>    — add screenshot step")
        print("  flow                     — show all recorded flow steps")
        print("  edit <n>                 — edit step #n")
        print("  delete <n>               — remove step #n")
        print("  move <m> <n>             — move step #m before step #n")
        print("  undo                     — remove last step")
        print("── Saved elements ──────────────────────────────────────────────────")
        print("  saved                    — list elements saved for this screen")
        print("  remove <name>            — delete element from saved file")
        print("  rename <old> <new>       — rename element in saved file")
        print("  done / exit              — finish recording this screen")
        print()

        try:
            cmd_raw = input("▶  Enter command: ").strip()
            cmd = cmd_raw.lower()
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Recording cancelled.")
            break

        # ── Done ─────────────────────────────────────────────────────────────
        if cmd in ("done", "exit", "quit", "q"):
            print(f"\n✅ Finished recording '{screen_name}' — {len(recorded)} elements, "
                  f"{len(flow_steps)} flow steps total.")
            break

        # ── Refresh ───────────────────────────────────────────────────────────
        elif cmd == "refresh":
            print("🔄 Refreshing page source...")
            continue

        # ── Navigate: tap ──────────────────────────────────────────────────────
        elif cmd.startswith("tap "):
            parts = cmd_raw.split(maxsplit=1)
            target = parts[1].strip() if len(parts) > 1 else ""
            match = _get_element_by_index(elements, target)
            if not match:
                print("⚠️  Usage: tap <number>   (example: tap 13)")
                continue
            try:
                _find_live_element(driver, match["locator"], platform).click()
                print(f"👆 Tapped #{match['index']} ({match['hint']})")
            except Exception as e:
                print(f"⚠️  Tap failed for #{match['index']}: {e}")
            continue

        # ── Navigate: type ─────────────────────────────────────────────────────
        elif cmd.startswith("type "):
            parts = cmd_raw.split(maxsplit=2)
            if len(parts) < 3:
                print("⚠️  Usage: type <number> <text>   (example: type 5 hello)")
                continue
            match = _get_element_by_index(elements, parts[1].strip())
            if not match:
                print("⚠️  Invalid element number for type command.")
                continue
            text_value = parts[2]
            try:
                live = _find_live_element(driver, match["locator"], platform)
                live.click()
                try:
                    live.clear()
                except Exception:
                    pass
                live.send_keys(text_value)
                print(f"⌨️  Typed into #{match['index']}: {text_value}")
            except Exception as e:
                print(f"⚠️  Type failed for #{match['index']}: {e}")
            continue

        # ── Navigate: back ─────────────────────────────────────────────────────
        elif cmd == "back":
            try:
                driver.back()
                print("↩️  Back pressed")
            except Exception as e:
                print(f"⚠️  Back failed: {e}")
            continue

        # ── Navigate: wait ─────────────────────────────────────────────────────
        elif cmd.startswith("wait "):
            parts = cmd.split(maxsplit=1)
            try:
                sec = float(parts[1]) if len(parts) > 1 else 1.0
                sec = max(0.1, sec)
            except Exception:
                sec = 1.0
            print(f"⏱️  Waiting {sec:.1f}s ...")
            time.sleep(sec)
            continue

        # ── Navigate: screenshot ───────────────────────────────────────────────
        elif cmd == "screenshot":
            ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = f"/tmp/appium_spy_{platform}_{ts}.png"
            driver.save_screenshot(path)
            print(f"📸 Screenshot saved: {path}")
            continue

        # ── Navigate: source ───────────────────────────────────────────────────
        elif cmd == "source":
            with open("/tmp/page_source.xml", "w", encoding="utf-8") as f:
                f.write(xml_source)
            print("📄 Page source saved to /tmp/page_source.xml")
            continue

        # ── Flow: show steps ───────────────────────────────────────────────────
        elif cmd == "flow":
            _print_flow(flow_steps)
            continue

        # ── Flow: add non-element step ─────────────────────────────────────────
        elif cmd.startswith("add "):
            sub  = cmd_raw[4:].strip()
            step = _parse_add_command(sub)
            if step:
                flow_steps.append(step)
                print(f"  📝 Step added: {step}")
            else:
                print(f"  ⚠️  Unknown add command '{sub}'.")
                print("  Available: back, enter, dismiss, scroll down, scroll up,")
                print("             swipe left, swipe right, wait <n>, screenshot <name>")
            continue

        # ── Flow: edit step ────────────────────────────────────────────────────
        elif cmd.startswith("edit "):
            parts   = cmd_raw.split(maxsplit=1)
            idx_str = parts[1].strip() if len(parts) > 1 else ""
            if not idx_str.isdigit() or not flow_steps:
                print("  ⚠️  Usage: edit <step_number>")
                _print_flow(flow_steps)
                continue
            idx = int(idx_str) - 1
            if idx < 0 or idx >= len(flow_steps):
                print(f"  ⚠️  Step #{idx+1} does not exist (valid: 1–{len(flow_steps)})")
                continue
            print(f"  Current: {flow_steps[idx]}")
            try:
                new_step = input("  New step text: ").strip()
            except (KeyboardInterrupt, EOFError):
                continue
            if new_step:
                flow_steps[idx] = new_step
                print(f"  ✏️  Step #{idx+1} updated: {new_step}")
            else:
                print("  ⚠️  Empty input — step unchanged.")
            continue

        # ── Flow: delete step ──────────────────────────────────────────────────
        elif cmd.startswith("delete "):
            parts   = cmd_raw.split(maxsplit=1)
            idx_str = parts[1].strip() if len(parts) > 1 else ""
            if not idx_str.isdigit() or not flow_steps:
                print("  ⚠️  Usage: delete <step_number>")
                _print_flow(flow_steps)
                continue
            idx = int(idx_str) - 1
            if idx < 0 or idx >= len(flow_steps):
                print(f"  ⚠️  Step #{idx+1} does not exist (valid: 1–{len(flow_steps)})")
                continue
            removed = flow_steps.pop(idx)
            print(f"  🗑️  Deleted step #{idx+1}: {removed}")
            continue

        # ── Flow: move step ────────────────────────────────────────────────────
        elif cmd.startswith("move "):
            parts = cmd_raw.split()
            if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
                print("  ⚠️  Usage: move <from> <to>   (example: move 3 1)")
                continue
            m = int(parts[1]) - 1
            n = int(parts[2]) - 1
            if m < 0 or m >= len(flow_steps):
                print(f"  ⚠️  Step #{m+1} does not exist.")
                continue
            n = max(0, min(n, len(flow_steps) - 1))
            step_to_move = flow_steps.pop(m)
            flow_steps.insert(n, step_to_move)
            print(f"  🔀 Moved '{step_to_move}' to position #{n+1}")
            _print_flow(flow_steps)
            continue

        # ── Flow: undo ─────────────────────────────────────────────────────────
        elif cmd == "undo":
            if flow_steps:
                removed = flow_steps.pop()
                print(f"  ↩️  Undone: {removed}")
            else:
                print("  ⚠️  No steps to undo.")
            continue

        # ── Saved: list elements saved for this screen ─────────────────────────
        elif cmd == "saved":
            _show_saved_elements(platform, screen_name)
            continue

        # ── Remove: delete a saved element by name ─────────────────────────────
        elif cmd.startswith("remove ") or cmd.startswith("del "):
            parts        = cmd_raw.split(maxsplit=1)
            name_to_del  = parts[1].strip() if len(parts) > 1 else ""
            if not name_to_del:
                print("  ⚠️  Usage: remove <element_name>")
                continue
            data          = _load_locators()
            screen_store  = data.get(platform, {}).get(screen_name, {})
            in_file       = screen_store.pop(name_to_del, None)
            in_memory     = recorded.pop(name_to_del, None)
            if in_file is not None:
                _save_locators(data)
            if in_file is not None or in_memory is not None:
                print(f"  🗑️  Removed '{name_to_del}' from '{screen_name}'")
            else:
                print(f"  ⚠️  Element '{name_to_del}' not found.")
                _show_saved_elements(platform, screen_name)
            continue

        # ── Rename: rename a saved element ─────────────────────────────────────
        elif cmd.startswith("rename "):
            parts = cmd_raw.split()
            if len(parts) != 3:
                print("  ⚠️  Usage: rename <old_name> <new_name>")
                continue
            old_name = parts[1].strip()
            new_name = parts[2].strip().lower().replace(" ", "_").replace("-", "_")
            data         = _load_locators()
            screen_store = data.get(platform, {}).get(screen_name, {})
            if old_name not in screen_store:
                print(f"  ⚠️  Element '{old_name}' not found in saved elements.")
                _show_saved_elements(platform, screen_name)
                continue
            screen_store[new_name] = screen_store.pop(old_name)
            _save_locators(data)
            if old_name in recorded:
                recorded[new_name] = recorded.pop(old_name)
            print(f"  ✏️  Renamed '{old_name}' → '{new_name}'")
            continue

        # ── Record element ─────────────────────────────────────────────────────
        elif cmd.isdigit():
            idx   = int(cmd)
            match = next((e for e in elements if e["index"] == idx), None)
            if not match:
                print(f"⚠️  No element #{idx}. Choose a number from the list above.")
                continue

            print(f"\n  Selected: [{match['tag'].split('.')[-1]}]  \"{match['hint']}\"")
            print(f"  Locator : {json.dumps(match['locator'], indent=4)}")

            try:
                name = input("  Give this element a name (snake_case): ").strip()
            except (KeyboardInterrupt, EOFError):
                break

            if not name:
                print("  ⚠️  Name cannot be empty. Skipping.")
                continue

            name = name.lower().replace(" ", "_").replace("-", "_")
            recorded[name] = match["locator"]
            print(f"  ✅ Recorded '{name}'")

            # Prompt for flow action + execute on device
            _prompt_and_build_step(name, match["hint"], match["locator"], driver, platform, flow_steps)

        else:
            print(f"  ❓ Unknown command '{cmd}'. Enter a number, or see commands above.")

    return recorded


# ─────────────────────────────────────────────────────────────────────────────
# LOAD / EDIT HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _list_flow_files() -> list:
    """Return sorted list of .flow filenames found in flows/."""
    flows_dir = os.path.join(BASE_DIR, "flows")
    if not os.path.exists(flows_dir):
        return []
    return sorted(f for f in os.listdir(flows_dir) if f.endswith(".flow"))


def _load_flow_steps(flow_path: str) -> list:
    """Parse a .flow file → list of NLP step strings (strips # comments and blanks)."""
    steps = []
    with open(flow_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                steps.append(line)
    return steps


def _show_saved_elements(platform: str, screen_name: str) -> None:
    """Print all elements saved for a screen in locators_manual.json."""
    data  = _load_locators()
    screen = data.get(platform, {}).get(screen_name, {})
    if not screen:
        print(f"  (no elements saved for '{screen_name}' yet)")
        return
    print(f"\n  💾 Saved elements for '{screen_name}' ({len(screen)} total):")
    print("  " + "─" * 62)
    for name, loc in screen.items():
        primary = (
            loc.get("accessibility_id")
            or loc.get("resource_id")
            or loc.get("text")
            or loc.get("xpath", "—")
        )
        print(f"  {name:<32} {str(primary)[:38]}")
    print("  " + "─" * 62 + "\n")


def _startup_mode_menu(platform: str, args_screen: str) -> tuple:
    """
    Interactive startup menu.
    Returns (screen_name, preloaded_flow_steps, loaded_flow_path_or_None).
    loaded_flow_path is set when user loaded an existing .flow file.
    """
    saved_screens = list((_load_locators()).get(platform, {}).keys())

    print("\n" + "─" * 62)
    print("  What would you like to do?")
    print("  [1] New session   — record fresh elements + build a new flow")
    print("  [2] Load & edit   — open a saved screen + continue an existing flow")
    print("─" * 62)
    try:
        mode = input("  Mode [1/2, default 1]: ").strip()
    except (KeyboardInterrupt, EOFError):
        mode = "1"

    preloaded_steps = []
    loaded_flow_path = None

    # ── New session ───────────────────────────────────────────────────────────
    if mode != "2":
        screen_name = args_screen
        if not screen_name:
            try:
                screen_name = input("\n📱 Screen name (e.g. 'home_screen'): ").strip()
            except (KeyboardInterrupt, EOFError):
                screen_name = ""
        screen_name = (screen_name or f"{platform}_screen").lower().replace(" ", "_").replace("-", "_")
        return screen_name, preloaded_steps, loaded_flow_path

    # ── Load & edit ───────────────────────────────────────────────────────────
    # 1. Pick screen
    if saved_screens:
        print(f"\n  💾 Saved screens ({platform}):")
        for i, sc in enumerate(saved_screens, 1):
            n_el = len((_load_locators()).get(platform, {}).get(sc, {}))
            print(f"    [{i}] {sc}  ({n_el} element(s))")
        print("    or type a new screen name")
        try:
            pick = input(f"\n  Pick screen [1–{len(saved_screens)} / name]: ").strip()
        except (KeyboardInterrupt, EOFError):
            pick = "1"
        if pick.isdigit() and 1 <= int(pick) <= len(saved_screens):
            screen_name = saved_screens[int(pick) - 1]
        else:
            screen_name = pick or saved_screens[0]
    else:
        print("  (no saved screens yet — enter a new screen name)")
        try:
            screen_name = input("  Screen name: ").strip()
        except (KeyboardInterrupt, EOFError):
            screen_name = ""

    screen_name = (screen_name or f"{platform}_screen").lower().replace(" ", "_").replace("-", "_")
    _show_saved_elements(platform, screen_name)

    # 2. Pick flow file
    flow_files = _list_flow_files()
    if flow_files:
        try:
            load_flow = input("  📂 Load an existing .flow file to continue editing? (y/n): ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            load_flow = "n"
        if load_flow == "y":
            print("\n  Available .flow files:")
            for i, fn in enumerate(flow_files, 1):
                flows_dir = os.path.join(BASE_DIR, "flows")
                size = os.path.getsize(os.path.join(flows_dir, fn))
                print(f"    [{i}] {fn}  ({size} bytes)")
            try:
                fpick = input(f"\n  File [1–{len(flow_files)}]: ").strip()
            except (KeyboardInterrupt, EOFError):
                fpick = ""
            if fpick.isdigit() and 1 <= int(fpick) <= len(flow_files):
                chosen = flow_files[int(fpick) - 1]
                loaded_flow_path = os.path.join(BASE_DIR, "flows", chosen)
                preloaded_steps  = _load_flow_steps(loaded_flow_path)
                print(f"\n  ✅ Loaded {len(preloaded_steps)} step(s) from '{chosen}'")
                _print_flow(preloaded_steps)
            else:
                print("  ⚠️  Invalid selection — starting with empty flow.")

    return screen_name, preloaded_steps, loaded_flow_path


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Appium Element Spy — record locators + generate .flow files from Android/iOS apps"
    )
    parser.add_argument("--platform", "-p", choices=["android", "ios"], required=True,
                        help="Target platform")
    parser.add_argument("--screen", "-s", default="",
                        help="Screen/page name (e.g. 'home_screen'). Leave blank to enter interactively.")
    parser.add_argument("--caps", "-c", default=None,
                        help="Path to a JSON file with Appium capabilities (overrides .env).")
    parser.add_argument("--app-file", "-a", default=None,
                        help="App binary to install+launch (.apk / .ipa).")
    parser.add_argument("--app-id", default=None,
                        help="Android appPackage or iOS bundleId.")
    parser.add_argument("--app-activity", default=None,
                        help="Android appActivity (optional).")
    parser.add_argument("--udid", default=None,
                        help="Device UDID override.")
    parser.add_argument("--wait-device-seconds", type=int, default=25,
                        help="Wait time for adb device (Android only).")
    args = parser.parse_args()

    caps_override = None
    if args.caps:
        if not os.path.exists(args.caps):
            logger.error("❌ Caps file not found: %s", args.caps)
            sys.exit(1)
        with open(args.caps, "r") as f:
            caps_override = json.load(f)

    if caps_override is None:
        sys.path.insert(0, BASE_DIR)
        from config import settings
        caps_override = (
            dict(settings.ANDROID_CAPABILITIES or {})
            if args.platform == "android"
            else dict(settings.IOS_CAPABILITIES or {})
        )
    caps_override = _apply_runtime_cap_overrides(args.platform, caps_override, args)

    if args.platform == "android":
        _ensure_android_device_available(caps_override, wait_seconds=args.wait_device_seconds)

    print("\n" + "═" * 70)
    print("  🕵️  Appium Element Spy  +  Flow Recorder")
    print(f"  Platform : {args.platform.upper()}")
    print("═" * 70)

    driver, effective_caps = _start_appium_session(args.platform, caps_override)
    _maybe_activate_target_app(driver, args.platform, effective_caps)

    # Startup mode — new session or load & edit an existing one
    screen_name, all_flow_steps, loaded_flow_path = _startup_mode_menu(args.platform, args.screen)

    try:
        all_recorded: dict = {}

        # Multi-screen recording loop
        while True:
            print(f"\n📱 Recording screen: '{screen_name}'")
            recorded = _record_session(driver, args.platform, screen_name, all_flow_steps)
            all_recorded.update(recorded)

            # Merge into locators file
            locators = _load_locators()
            plat_key = args.platform
            if plat_key not in locators:
                locators[plat_key] = {}
            if screen_name not in locators[plat_key]:
                locators[plat_key][screen_name] = {}
            locators[plat_key][screen_name].update(recorded)
            _save_locators(locators)
            print(f"\n💾 Saved {len(recorded)} element(s) under '{plat_key}.{screen_name}' → {LOCATORS_FILE}")

            try:
                more = input("\n🔄 Record another screen? (y/n): ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                more = "n"
            if more != "y":
                break

            try:
                screen_name = input("📱 New screen name: ").strip()
            except (KeyboardInterrupt, EOFError):
                break
            if not screen_name:
                break
            screen_name = screen_name.lower().replace(" ", "_").replace("-", "_")

    finally:
        driver.quit()
        logger.info("🔌 Appium session closed.")

    # ── Save flow file ────────────────────────────────────────────────────────
    if all_flow_steps:
        print("\n" + "═" * 70)
        print(f"  📋 {len(all_flow_steps)} flow step(s):")
        _print_flow(all_flow_steps)

        try:
            save = input("💾 Save flow? (y/n): ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            save = "n"

        if save == "y":
            flows_dir = os.path.join(BASE_DIR, "flows")
            os.makedirs(flows_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

            # Smart save: if a file was loaded, offer to overwrite it
            flow_path = None
            flow_name = None
            if loaded_flow_path:
                orig_name = os.path.basename(loaded_flow_path)
                print(f"\n  Loaded file: {orig_name}")
                print("  [1] Overwrite original file")
                print("  [2] Save as a new file")
                try:
                    overwrite_choice = input("  [1/2, default 1]: ").strip()
                except (KeyboardInterrupt, EOFError):
                    overwrite_choice = "1"
                if overwrite_choice != "2":
                    flow_path = loaded_flow_path
                    flow_name = os.path.splitext(orig_name)[0]

            if flow_path is None:
                default_name = f"{args.platform}_recorded"
                try:
                    flow_name_raw = input(f"  Flow file name [default: {default_name}]: ").strip()
                except (KeyboardInterrupt, EOFError):
                    flow_name_raw = ""
                flow_name = (flow_name_raw or default_name).replace(" ", "_").replace("-", "_")
                flow_path = os.path.join(flows_dir, f"{flow_name}.flow")

            with open(flow_path, "w", encoding="utf-8") as f:
                f.write(f"# flows/{flow_name}.flow\n")
                f.write(f"# Recorded {timestamp} — platform: {args.platform.upper()}\n")
                f.write("# ──────────────────────────────────────────────────────────────\n\n")
                for step in all_flow_steps:
                    f.write(step + "\n")
            print(f"  ✅ Flow saved: {flow_path}")

            try:
                export_json = input("  Also export as .flow.json? (y/n): ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                export_json = "n"
            if export_json == "y":
                json_path = os.path.join(flows_dir, f"{flow_name}.flow.json")
                payload = {
                    "platform": args.platform,
                    "recorded": datetime.now().isoformat(timespec="seconds"),
                    "steps": [{"index": i + 1, "step": s} for i, s in enumerate(all_flow_steps)],
                }
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
                print(f"  ✅ JSON exported: {json_path}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "═" * 70)
    print(f"  ✅ Recording complete — {len(all_recorded) if 'all_recorded' in dir() else 0} element(s) recorded")
    print(f"  📁 Locators saved to: {LOCATORS_FILE}")
    print()
    print("  Next steps:")
    print("  1. Review / edit your .flow file in flows/")
    print(f"  2. Create/update suites/{args.platform}_suite.json with your device caps")
    print(f"  3. Run: python plan_runner.py plans/{args.platform}_plan.json")
    print("═" * 70 + "\n")


if __name__ == "__main__":
    main()
