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
import sys
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
    """Start an Appium session and return the driver."""
    try:
        from appium import webdriver as appium_webdriver
        from appium.options.common.base import AppiumOptions
    except ImportError:
        logger.error("❌ Appium Python client not installed. Run: pip install Appium-Python-Client")
        sys.exit(1)

    sys.path.insert(0, BASE_DIR)
    from config import settings

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

    server_url = settings.APPIUM_SERVER_URL
    logger.info("🔗 Connecting to Appium at %s ...", server_url)
    logger.info("📱 Platform   : %s", platform.upper())
    logger.info("📱 Device     : %s", caps.get("deviceName", caps.get("appium:deviceName", "unknown")))

    options = AppiumOptions().load_capabilities(caps)
    driver  = appium_webdriver.Remote(server_url, options=options)

    logger.info("✅ Session started — session_id: %s", driver.session_id)
    return driver


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
        print("  <number>       — record element by number")
        print("  screenshot     — take a screenshot of current screen")
        print("  source         — dump raw page source to /tmp/page_source.xml")
        print("  refresh        — reload page source (after navigating to new screen)")
        print("  done / exit    — finish recording this screen")
        print()

        try:
            cmd = input("▶  Enter command: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Recording cancelled.")
            break

        if cmd in ("done", "exit", "quit", "q"):
            print(f"\n✅ Finished recording '{screen_name}' — {len(recorded)} elements saved.")
            break

        elif cmd == "refresh":
            print("🔄 Refreshing page source...")
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
    args = parser.parse_args()

    # Load capabilities from JSON file if provided
    caps_override = None
    if args.caps:
        if not os.path.exists(args.caps):
            logger.error("❌ Caps file not found: %s", args.caps)
            sys.exit(1)
        with open(args.caps, "r") as f:
            caps_override = json.load(f)

    print("\n" + "═" * 70)
    print("  🕵️  Appium Element Spy")
    print(f"  Platform : {args.platform.upper()}")
    print("═" * 70)

    # Start Appium session
    driver = _start_appium_session(args.platform, caps_override)

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
