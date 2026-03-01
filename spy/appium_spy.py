"""
spy/appium_spy.py
Interactive element recorder for Android & iOS apps using Appium.

Flow:
  1. Connects to Appium + launches the app
  2. Fetches page source (XML)
  3. Parses and lists interactive elements numbered
  4. You type the element number + give it a friendly name
  5. Saves locators to data/locators_manual.json under "android" or "ios" key
  6. Repeat for each screen — type 'refresh' to reload page source, 'done' to quit

Usage:
  # Terminal 1 — start Appium (if not running)
  appium

  # Terminal 2 — record elements
  python spy/appium_spy.py --platform android
  python spy/appium_spy.py --platform ios

  # Or run with custom caps JSON file
  python spy/appium_spy.py --platform android --caps caps/my_device.json
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCATORS_FILE = os.path.join(BASE_DIR, "data", "locators_manual.json")

# ─────────────────────────────────────────────────────────────────────────────
# LOCATOR FILE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _load_locators() -> dict:
    if os.path.exists(LOCATORS_FILE):
        with open(LOCATORS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_locators(data: dict) -> None:
    os.makedirs(os.path.dirname(LOCATORS_FILE), exist_ok=True)
    with open(LOCATORS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# XML PAGE SOURCE PARSER
# ─────────────────────────────────────────────────────────────────────────────

# Tags considered interactive (worth recording)
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

        # Build XPath as fallback
        parts = [f"[@class='{class_name}']" if class_name else ""]
        if resource_id:
            parts.append(f"[@resource-id='{resource_id}']")
        elif text:
            parts.append(f"[@text='{text}']")
        elif acc_id:
            parts.append(f"[@content-desc='{acc_id}']")
        xpath_tag = class_name.split(".")[-1] if "." in class_name else class_name
        locator["xpath"] = f"//{class_name}{''.join(parts[1:])}" if parts[1:] else f"//{class_name}"

        if bounds:
            locator["bounds"] = bounds

    elif platform == "ios":
        acc_id   = attrib.get("name", "").strip()
        label    = attrib.get("label", "").strip()
        value    = attrib.get("value", "").strip()
        el_type  = el.tag.strip()

        if acc_id:
            locator["accessibility_id"] = acc_id
        if label:
            locator["label"] = label
        if value:
            locator["value"] = value
        locator["class_name"] = el_type

        # Build XPath
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
        tag = node.tag
        attrib = node.attrib

        # Check if element has useful identifiers
        has_resource_id = bool(attrib.get("resource-id", "").strip())
        has_acc_id      = bool(attrib.get("content-desc", "").strip()) or bool(attrib.get("name", "").strip())
        has_text        = bool(attrib.get("text", "").strip()) or bool(attrib.get("label", "").strip())
        is_clickable    = attrib.get("clickable", "false").lower() == "true"
        is_enabled      = attrib.get("enabled", "true").lower() == "true"
        is_interesting  = any([has_resource_id, has_acc_id, has_text]) and is_enabled

        # Only collect if interactive tag OR has useful identifier
        if tag in _INTERACTIVE_TAGS or is_interesting or is_clickable:
            locator = _best_locator(node, platform)
            if locator:
                # Build display label
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
            "   Set ANDROID_CAPABILITIES or IOS_CAPABILITIES in .env as a JSON string.\n"
            "   Example: ANDROID_CAPABILITIES='{\"platformName\":\"Android\",\"deviceName\":\"emulator-5554\",...}'",
            platform,
        )
        sys.exit(1)

    # ── Auto-set ANDROID_HOME if missing (prefer full SDK with build-tools) ─
    if platform == "android" and not os.environ.get("ANDROID_HOME"):
        preferred_roots = [
            "/usr/local/share/android-commandlinetools",  # brew android-commandlinetools cask
            os.path.expanduser("~/Library/Android/sdk"),   # Android Studio default
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
    logger.info("📱 Device     : %s", caps.get("deviceName", caps.get("appium:deviceName", caps.get("udid", caps.get("appium:udid", "unknown")))))

    # ── Build options using platform-specific Options class (Appium 5.x) ───
    if platform == "android":
        from appium.options.android.uiautomator2.base import UiAutomator2Options
        options = UiAutomator2Options()
    else:
        from appium.options.ios.xcuitest.base import XCUITestOptions
        options = XCUITestOptions()

    for key, val in caps.items():
        clean = key.replace("appium:", "")
        try:
            setattr(options, clean, val)
        except Exception:
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

    # Device override
    if args.udid:
        caps["appium:udid"] = args.udid
        caps["appium:deviceName"] = args.udid

    # App binary override (APK/IPA path)
    if args.app_file:
        app_abs = os.path.abspath(os.path.expanduser(args.app_file))
        if not os.path.exists(app_abs):
            logger.error("❌ App file not found: %s", app_abs)
            sys.exit(1)

        caps["appium:app"] = app_abs
        # For file-based install/launch we should not preserve stale app state.
        caps["appium:noReset"] = False
        caps.setdefault("appium:autoGrantPermissions", True)

        # Let Appium infer launch info from app manifest unless explicitly passed.
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

    # Explicit app identifiers
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
        caps.get("appium:udid")
        or caps.get("udid")
        or caps.get("appium:deviceName")
        or caps.get("deviceName")
        or ""
    ).strip()

    deadline = time.time() + max(wait_seconds, 0)
    while True:
        connected = _get_connected_adb_devices()
        if connected:
            if not target_udid or target_udid in connected:
                logger.info("✅ ADB connected device(s): %s", ", ".join(connected))
                return
            logger.warning("⚠️ Connected device(s): %s (target '%s' not present)", ", ".join(connected), target_udid)

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
# INTERACTIVE RECORDER
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


def _find_live_element(driver, locator: dict, platform: str):
    """Resolve an element from a recorded locator dict with fallback strategies."""
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

    raise RuntimeError(f"Could not resolve element with known locator strategies. Last error: {last_error}")


def _get_element_by_index(elements: list[dict], idx_text: str) -> dict | None:
    """Return element dict by 1-based index string."""
    if not idx_text.isdigit():
        return None
    idx = int(idx_text)
    return next((e for e in elements if e["index"] == idx), None)


def _record_session(driver, platform: str, screen_name: str) -> dict:
    """
    Interactive recording loop for one screen.
    Returns dict of {element_name: locator_dict} recorded in this session.
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
            print("\n⚠️  No elements found on current screen. Navigate to a screen with content.")
        else:
            _print_elements(elements)

        print("Commands:")
        print("  <number>               — record element by number")
        print("  tap <number>           — tap element by number (user-like navigation)")
        print("  type <number> <text>   — type into element by number")
        print("  back                   — press Android back")
        print("  wait <seconds>         — pause briefly")
        print("  screenshot             — take a screenshot of current screen")
        print("  source                 — dump raw page source to /tmp/page_source.xml")
        print("  refresh                — reload page source (after navigating to new screen)")
        print("  done / exit            — finish recording this screen")
        print()

        try:
            cmd_raw = input("▶  Enter command: ").strip()
            cmd = cmd_raw.lower()
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Recording cancelled.")
            break

        if cmd in ("done", "exit", "quit", "q"):
            print(f"\n✅ Finished recording '{screen_name}' — {len(recorded)} elements saved.")
            break

        elif cmd == "refresh":
            print("🔄 Refreshing page source...")
            continue

        elif cmd.startswith("tap "):
            parts = cmd_raw.split(maxsplit=1)
            target = parts[1].strip() if len(parts) > 1 else ""
            match = _get_element_by_index(elements, target)
            if not match:
                print("⚠️  Usage: tap <number>   (example: tap 13)")
                continue
            try:
                live_el = _find_live_element(driver, match["locator"], platform)
                live_el.click()
                print(f"👆 Tapped #{match['index']} ({match['hint']})")
            except Exception as e:
                print(f"⚠️  Tap failed for #{match['index']}: {e}")
            continue

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
                live_el = _find_live_element(driver, match["locator"], platform)
                live_el.click()
                try:
                    live_el.clear()
                except Exception:
                    pass
                live_el.send_keys(text_value)
                print(f"⌨️  Typed into #{match['index']}: {text_value}")
            except Exception as e:
                print(f"⚠️  Type failed for #{match['index']}: {e}")
            continue

        elif cmd == "back":
            try:
                driver.back()
                print("↩️  Back pressed")
            except Exception as e:
                print(f"⚠️  Back failed: {e}")
            continue

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

        elif cmd == "screenshot":
            ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = f"/tmp/appium_spy_{platform}_{ts}.png"
            driver.save_screenshot(path)
            print(f"📸 Screenshot saved: {path}")
            continue

        elif cmd == "source":
            with open("/tmp/page_source.xml", "w", encoding="utf-8") as f:
                f.write(xml_source)
            print("📄 Page source saved to /tmp/page_source.xml")
            continue

        elif cmd.isdigit():
            idx = int(cmd)
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

            # Normalise to snake_case
            name = name.lower().replace(" ", "_").replace("-", "_")
            recorded[name] = match["locator"]
            print(f"  ✅ Recorded '{name}'")

        else:
            print(f"  ❓ Unknown command '{cmd}'. Use a number, 'refresh', 'screenshot', 'source', or 'done'.")

    return recorded


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Appium Element Spy — record element locators from Android/iOS apps"
    )
    parser.add_argument(
        "--platform", "-p",
        choices=["android", "ios"],
        required=True,
        help="Target platform",
    )
    parser.add_argument(
        "--screen", "-s",
        default="",
        help="Screen/page name to group elements under (e.g. 'home_screen', 'login_screen'). "
             "Leave blank to enter interactively.",
    )
    parser.add_argument(
        "--caps", "-c",
        default=None,
        help="Path to a JSON file with Appium capabilities (overrides .env).",
    )
    parser.add_argument(
        "--app-file", "-a",
        default=None,
        help="Path to app binary to install+launch for recording (.apk for Android, .ipa for iOS).",
    )
    parser.add_argument(
        "--app-id",
        default=None,
        help="Android appPackage or iOS bundleId to activate/open after session starts.",
    )
    parser.add_argument(
        "--app-activity",
        default=None,
        help="Android appActivity (optional, used with --app-id).",
    )
    parser.add_argument(
        "--udid",
        default=None,
        help="Device UDID override (adb devices / xcrun simctl list).",
    )
    parser.add_argument(
        "--wait-device-seconds",
        type=int,
        default=25,
        help="Wait time for adb device to appear before failing (Android only).",
    )
    args = parser.parse_args()

    # Load capabilities from JSON file if provided
    caps_override = None
    if args.caps:
        if not os.path.exists(args.caps):
            logger.error("❌ Caps file not found: %s", args.caps)
            sys.exit(1)
        with open(args.caps, "r") as f:
            caps_override = json.load(f)

    # Build effective capabilities with runtime overrides (APK/IPA, app id, udid)
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
    print("  🕵️  Appium Element Spy")
    print(f"  Platform : {args.platform.upper()}")
    print("═" * 70)

    # Start Appium session
    driver, effective_caps = _start_appium_session(args.platform, caps_override)
    _maybe_activate_target_app(driver, args.platform, effective_caps)

    try:
        # Ask for screen name if not provided
        screen_name = args.screen
        if not screen_name:
            try:
                screen_name = input("\n📱 Enter a screen name to group elements under (e.g. 'home_screen'): ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\n👋 Exiting.")
                driver.quit()
                sys.exit(0)
            if not screen_name:
                screen_name = f"{args.platform}_screen"
        screen_name = screen_name.lower().replace(" ", "_").replace("-", "_")

        all_recorded: dict = {}

        # Multi-screen recording loop
        while True:
            print(f"\n📱 Recording screen: '{screen_name}'")
            recorded = _record_session(driver, args.platform, screen_name)
            all_recorded.update(recorded)

            # Merge into locators file
            locators = _load_locators()
            platform_key = args.platform  # "android" or "ios"
            if platform_key not in locators:
                locators[platform_key] = {}
            if screen_name not in locators[platform_key]:
                locators[platform_key][screen_name] = {}
            locators[platform_key][screen_name].update(recorded)
            _save_locators(locators)
            print(f"\n💾 Saved {len(recorded)} element(s) under '{platform_key}.{screen_name}' → {LOCATORS_FILE}")

            # Ask to record another screen
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

    # Summary
    print("\n" + "═" * 70)
    print(f"  ✅ Recording complete — {len(all_recorded)} element(s) recorded")
    print(f"  📁 Locators saved to: {LOCATORS_FILE}")
    print()
    print("  Next steps:")
    print(f"  1. Write your .flow file using these element names")
    print(f"  2. Create/update suites/{args.platform}_suite.json with your device caps")
    print(f"  3. Run: python plan_runner.py plans/{args.platform}_plan.json")
    print("═" * 70 + "\n")


if __name__ == "__main__":
    main()
