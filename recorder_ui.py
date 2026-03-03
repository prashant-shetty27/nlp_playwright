"""
recorder_ui.py — Visual Appium Element Recorder
================================================
A NiceGUI web app that shows:
  • Live device screenshot (auto-refreshes)
  • Element list parsed from page source
  • One-click element selection + naming
  • Action picker (tap / type / verify / swipe / wait / screenshot …)
  • Live flow builder with edit / delete / reorder / undo
  • Save flow → flows/<name>.flow  +  locators → data/locators_manual.json

Usage:
    python recorder_ui.py --platform android
    python recorder_ui.py --platform ios
    python recorder_ui.py --platform android --caps suites/android_suite.json
    python recorder_ui.py --platform ios     --caps suites/ios_suite.json
"""

import argparse
import asyncio
import base64
import io
import json
import logging
import os
import re
import signal
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime

from nicegui import ui, run

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
LOCATORS_FILE = os.path.join(BASE_DIR, "data", "locators_manual.json")
FLOWS_DIR     = os.path.join(BASE_DIR, "flows")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

# ─── Shared state ─────────────────────────────────────────────────────────────
_state = {
    "driver":         None,
    "platform":       "android",
    "caps":           {},
    "elements":       [],
    "flow_steps":     [],
    "recorded":       {},        # {name: locator}
    "screen_name":    "home_screen",
    "selected_el":    None,      # currently selected element dict
    "status":         "Disconnected",
    "screenshot_b64": None,
    "screenshot_size": (1080, 1920),  # (w, h) of actual screenshot PNG
    "refreshing":     False,
    "auto_refresh":   True,
    "el_filter":      "",         # live search text
    # ── New feature state ─────────────────────────────────────────────────
    "edit_step_idx":  None,       # int index when editing existing step from flow list
    "known_names":    [],         # list of recorded element names for autocomplete
    "latest_vars":    [],         # last 3 RUNTIME_VARIABLES keys (for live panel)
    "latest_names":  [],          # last 3 recorded element names (for live panel)
}
_lock = threading.Lock()

# Input/editable widget classes whose `text` attribute is a dynamic placeholder —
# never use text as a locator key for these, since the value rotates at runtime.
_INPUT_TAGS = {
    "android.widget.EditText",
    "android.widget.MultiAutoCompleteTextView",
    "android.widget.AutoCompleteTextView",
    "android.widget.SearchView",
    "XCUIElementTypeTextField",
    "XCUIElementTypeSecureTextField",
    "XCUIElementTypeSearchField",
}

def _best_locator(el: ET.Element, platform: str) -> dict:
    attrib = el.attrib
    locator: dict = {}
    if platform == "android":
        rid   = attrib.get("resource-id","").strip()
        acc   = attrib.get("content-desc","").strip()
        text  = attrib.get("text","").strip()
        cls   = attrib.get("class","").strip()
        hint  = attrib.get("hint","").strip()       # stable hint/placeholder label

        # For input fields, text is a dynamic placeholder — skip it as a locator key
        is_input = cls in _INPUT_TAGS
        if acc:               locator["accessibility_id"] = acc
        if rid:               locator["resource_id"]      = rid
        if not is_input and text:
            locator["text"]   = text
        if hint:              locator["hint"]             = hint
        if cls:               locator["class_name"]       = cls

        # Build the most reliable xpath (prefer rid for inputs)
        parts = []
        if rid:
            parts.append(f"[@resource-id='{rid}']")
        elif not is_input and text:
            parts.append(f"[@text='{text}']")
        elif acc:
            parts.append(f"[@content-desc='{acc}']")
        locator["xpath"] = f"//{cls}{''.join(parts)}" if cls else f"//*{''.join(parts)}"

    elif platform == "ios":
        acc   = attrib.get("name","").strip()
        label = attrib.get("label","").strip()
        value = attrib.get("value","").strip()
        etype = el.tag.strip()
        is_input = etype in _INPUT_TAGS

        if acc:   locator["accessibility_id"] = acc
        # For input fields, label/value are often the current typed value — skip them
        if not is_input and label: locator["label"] = label
        if not is_input and value: locator["value"] = value
        locator["class_name"] = etype

        if acc:        locator["xpath"] = f"//{etype}[@name='{acc}']"
        elif not is_input and label: locator["xpath"] = f"//{etype}[@label='{label}']"
        else:          locator["xpath"] = f"//{etype}"
    return locator

# Tags that are pure structural containers with no visual content — skip these
_SKIP_TAGS = {
    "hierarchy", "AppiumAUT", "android.widget.Toast",
    "XCUIElementTypeApplication", "XCUIElementTypeWindow",
}

def _parse_elements(xml_source: str, platform: str) -> list:
    try:
        root = ET.fromstring(xml_source)
    except ET.ParseError:
        return []
    candidates = []

    def _parse_bounds(attrib):
        """Return [x1,y1,x2,y2] or None."""
        raw = attrib.get("bounds", "")  # Android: [x1,y1][x2,y2]
        if raw:
            nums = re.findall(r"\d+", raw)
            if len(nums) == 4:
                x1,y1,x2,y2 = map(int, nums)
                if (x2-x1) > 0 and (y2-y1) > 0:
                    return [x1, y1, x2, y2]
        else:
            try:  # iOS: x,y,width,height
                x = int(attrib.get("x",0)); y = int(attrib.get("y",0))
                w = int(attrib.get("width",0)); h = int(attrib.get("height",0))
                if w > 0 and h > 0:
                    return [x, y, x+w, y+h]
            except Exception:
                pass
        return None

    def _walk(node, depth=0):
        if depth > 120:   # safety: avoid RecursionError on pathologically nested XML
            return
        attrib = node.attrib
        tag    = node.tag

        # Skip pure structural roots
        if tag in _SKIP_TAGS or depth == 0:
            for child in node: _walk(child, depth+1)
            return

        bounds    = _parse_bounds(attrib)
        displayed = attrib.get("displayed", "true").lower() != "false"
        visible   = attrib.get("visible", "true").lower() != "false"
        enabled   = attrib.get("enabled", "true").lower() != "false"
        clickable = attrib.get("clickable", "false").lower() == "true"

        # Include element if it is visible on screen (has non-zero bounds)
        if bounds and displayed and visible:
            loc  = _best_locator(node, platform)
            rid  = attrib.get("resource-id","").strip()
            acc  = attrib.get("content-desc","").strip() or attrib.get("name","").strip()
            txt  = attrib.get("text","").strip() or attrib.get("label","").strip()
            hint = (acc or txt or rid.split("/")[-1] or tag.split(".")[-1]).strip()[:60]
            # tag_short for display
            tag_short = tag.split(".")[-1].replace("XCUIElementType","iOS/")
            candidates.append({
                "index":     len(candidates)+1,
                "tag":       tag,
                "tag_short": tag_short,
                "hint":      hint,
                "locator":   loc,
                "bounds":    bounds,
                "clickable": clickable,
                "enabled":   enabled,
                "resource_id": rid,
                "text":      txt,
                "acc":       acc,
            })

        for child in node:
            _walk(child, depth+1)

    _walk(root)
    return candidates

def _find_live_element(driver, locator: dict, platform: str):
    from appium.webdriver.common.appiumby import AppiumBy
    tries = []
    # Priority order: accessibility_id > resource_id > xpath
    # NOTE: we intentionally skip "text" and "label" as locator strategies because
    # input fields have dynamic placeholder text that changes at runtime.
    if locator.get("accessibility_id"):
        tries.append((AppiumBy.ACCESSIBILITY_ID, locator["accessibility_id"]))
    if platform == "android" and locator.get("resource_id"):
        tries.append((AppiumBy.ID, locator["resource_id"]))
    if platform == "ios" and locator.get("label") and \
            locator.get("class_name","") not in _INPUT_TAGS:
        lbl = locator["label"].replace('"','\\"')
        tries.append((AppiumBy.IOS_PREDICATE, f'label == "{lbl}"'))
    if locator.get("xpath"):
        tries.append((AppiumBy.XPATH, locator["xpath"]))
    # Last resort: UiAutomator text search only for non-input elements
    if platform == "android" and locator.get("text") and \
            locator.get("class_name","") not in _INPUT_TAGS:
        txt = locator["text"].replace('"','\\"')
        tries.append((AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{txt}")'))
    for by, val in tries:
        try:
            return driver.find_element(by, val)
        except Exception:
            pass
    raise RuntimeError("Element not found on device")

def _load_locators() -> dict:
    if os.path.exists(LOCATORS_FILE):
        try:
            with open(LOCATORS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("locators_manual.json corrupt/unreadable (%s) — treating as empty", e)
    return {}

def _save_locators(data: dict):
    os.makedirs(os.path.dirname(LOCATORS_FILE), exist_ok=True)
    with open(LOCATORS_FILE,"w",encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ─── Screenshot + refresh thread ─────────────────────────────────────────────

def _grab_screenshot():
    driver = _state["driver"]
    if not driver:
        return
    try:
        raw = driver.get_screenshot_as_base64()
        with _lock:
            _state["screenshot_b64"] = raw
        # Store actual PNG dimensions for coordinate mapping
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(base64.b64decode(raw)))
            _state["screenshot_size"] = img.size  # (width, height)
        except Exception:
            pass
    except Exception as e:
        logger.warning("Screenshot failed: %s", e)

def _highlight_screenshot(b64: str, bounds: list, index: int, label: str) -> str:
    """Draw a numbered red rectangle on the screenshot around the given element bounds.
    Returns a new base64 PNG string."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        img_data = base64.b64decode(b64)
        img = Image.open(io.BytesIO(img_data)).convert("RGBA")
        iw, ih = img.size

        # Normalise bounds — they may be in device pixels (higher res than screenshot)
        # Try to detect scale by comparing screenshot width to a known device width from state
        x1, y1, x2, y2 = bounds

        # Clamp to image dimensions
        x1 = max(0, min(x1, iw-1))
        y1 = max(0, min(y1, ih-1))
        x2 = max(x1+1, min(x2, iw))
        y2 = max(y1+1, min(y2, ih))

        # Draw overlay
        overlay = Image.new("RGBA", img.size, (0,0,0,0))
        draw = ImageDraw.Draw(overlay)

        # Red rectangle with 40% opacity fill
        draw.rectangle([x1, y1, x2, y2], fill=(220, 50, 50, 80), outline=(255, 60, 60, 255), width=3)

        # Badge circle with index number at top-left corner
        badge_r = 14
        bx, by = max(x1, badge_r), max(y1, badge_r)
        draw.ellipse([bx-badge_r, by-badge_r, bx+badge_r, by+badge_r],
                     fill=(255, 60, 60, 230))
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
        except Exception:
            font = ImageFont.load_default()
        txt = str(index)
        bbox = draw.textbbox((0,0), txt, font=font)
        tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
        draw.text((bx - tw//2, by - th//2), txt, fill=(255,255,255,255), font=font)

        # Merge overlay
        combined = Image.alpha_composite(img, overlay).convert("RGB")
        buf = io.BytesIO()
        combined.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.warning("Highlight failed: %s", e)
        return b64


def _refresh_elements():
    driver = _state["driver"]
    if not driver:
        return
    try:
        xml = driver.page_source
        els = _parse_elements(xml, _state["platform"])
        with _lock:
            _state["elements"] = els
        _grab_screenshot()
        _state["status"] = f"✅ Connected — {len(els)} elements"
    except Exception as e:
        _state["status"] = f"⚠️ Refresh failed: {e}"

# ─── Popup / permission auto-dismiss ────────────────────────────────────────

# Android: text labels on permission / popup buttons to auto-tap
_ALLOW_LABELS = [
    "allow", "allow all the time", "allow only while using the app",
    "allow once", "only this time", "ok", "got it", "accept",
    "agree", "continue", "proceed", "yes", "confirm", "done",
    "next", "i understand", "start", "enable",
]
_DENY_LABELS = [
    "deny", "don't allow", "no thanks", "not now", "skip",
    "cancel", "close", "maybe later", "decline",
]

def _dismiss_popups(driver=None, platform: str = "") -> str:
    """
    Try every known strategy to dismiss popups/permissions.
    Returns a short string describing what was dismissed, or '' if nothing found.
    """
    driver   = driver or _state.get("driver")
    platform = platform or _state.get("platform", "android")
    if not driver:
        return ""

    dismissed = []

    # ── 1. Native alert (WebDriver alert API) ─────────────────────────────────
    try:
        alert = driver.switch_to.alert
        alert.accept()
        dismissed.append("native alert")
        return ", ".join(dismissed)
    except Exception:
        pass

    # ── 2. Appium mobile: alert APIs ─────────────────────────────────────────
    try:
        driver.execute_script("mobile: acceptAlert")
        dismissed.append("mobile alert")
        return ", ".join(dismissed)
    except Exception:
        pass

    # ── 3. Scan current page_source for known dialog button text ─────────────
    try:
        from appium.webdriver.common.appiumby import AppiumBy
        xml = driver.page_source
        root = ET.fromstring(xml)

        # Collect all button/text-view elements that are clickable
        def _find_dialog_buttons(node, depth=0):
            tag    = node.tag.lower()
            attrib = node.attrib
            text   = (attrib.get("text","") or attrib.get("label","") or
                      attrib.get("name","") or attrib.get("content-desc","")).strip().lower()
            clickable = attrib.get("clickable","false").lower() == "true"
            displayed = attrib.get("displayed","true").lower() != "false"

            # Check if parent is a dialog/alert-like container
            if displayed and clickable and text:
                if any(lbl == text or text.startswith(lbl) for lbl in _ALLOW_LABELS + _DENY_LABELS):
                    rid = attrib.get("resource-id","").strip()
                    acc = attrib.get("content-desc","").strip() or attrib.get("name","").strip()
                    yield (text, rid, acc, node.tag)
            for child in node:
                yield from _find_dialog_buttons(child, depth+1)

        btns = list(_find_dialog_buttons(root))
        # Prefer Allow-type first
        for (text, rid, acc, tag_) in btns:
            if any(lbl == text for lbl in _ALLOW_LABELS):
                try:
                    if rid:
                        driver.find_element(AppiumBy.ID, rid).click()
                    elif acc:
                        driver.find_element(AppiumBy.ACCESSIBILITY_ID, acc).click()
                    else:
                        xpath = f"//{tag_}[@text='{text}'" + \
                                f" or @label='{text}' or @name='{text}']"
                        driver.find_element(AppiumBy.XPATH, xpath).click()
                    dismissed.append(f"'{text}'")
                    break
                except Exception:
                    continue
    except Exception:
        pass

    # ── 4. Android UiAutomator2: look for permission controller ──────────────
    if platform == "android" and not dismissed:
        try:
            from appium.webdriver.common.appiumby import AppiumBy
            for pkg_btn in [
                "com.android.permissioncontroller:id/permission_allow_button",
                "com.android.permissioncontroller:id/permission_allow_foreground_only_button",
                "com.android.permissioncontroller:id/permission_allow_one_time_button",
                "com.android.packageinstaller:id/permission_allow_button",
                "android:id/button1",   # generic OK
            ]:
                try:
                    el = driver.find_element(AppiumBy.ID, pkg_btn)
                    if el.is_displayed():
                        el.click()
                        dismissed.append(f"permission:{pkg_btn.split(':')[-1]}")
                        break
                except Exception:
                    continue
        except Exception:
            pass

    # ── 5. iOS XCUITest: look for standard alert button ───────────────────────
    if platform == "ios" and not dismissed:
        try:
            from appium.webdriver.common.appiumby import AppiumBy
            for label in ["Allow", "OK", "Continue", "Accept", "Allow Once",
                          "Allow While Using App", "Don't Allow"]:
                try:
                    el = driver.find_element(
                        AppiumBy.XPATH,
                        f'//XCUIElementTypeButton[@name="{label}" or @label="{label}"]'
                    )
                    if el.is_displayed():
                        el.click()
                        dismissed.append(f"ios:'{label}'")
                        break
                except Exception:
                    continue
        except Exception:
            pass

    return ", ".join(dismissed) if dismissed else ""


def _auto_refresh_loop():
    while _state["driver"] and _state["auto_refresh"]:
        try:
            _grab_screenshot()
        except Exception as e:
            logger.warning("Auto-refresh error: %s", e)
        time.sleep(3)

# ─── Appium session ───────────────────────────────────────────────────────────

def _start_session(caps: dict, platform: str):
    from appium import webdriver as aw
    if platform == "android":
        from appium.options.android.uiautomator2.base import UiAutomator2Options
        options = UiAutomator2Options()
    else:
        from appium.options.ios.xcuitest.base import XCUITestOptions
        options = XCUITestOptions()

    for key, val in caps.items():
        if val is None or val == "" or str(key).startswith("_comment"):
            continue
        clean = key.replace("appium:","")
        prop  = getattr(type(options), clean, None)
        if prop and isinstance(prop, property) and prop.fset:
            try:
                setattr(options, clean, val)
                continue
            except Exception:
                pass
        options.set_capability(key, val)

    sys.path.insert(0, BASE_DIR)
    from config import settings
    driver = aw.Remote(settings.APPIUM_SERVER_URL, options=options)
    return driver

# ─── Save helpers ─────────────────────────────────────────────────────────────

def _save_flow(name: str, steps: list, platform: str) -> str:
    os.makedirs(FLOWS_DIR, exist_ok=True)
    safe = re.sub(r"[^a-z0-9_]","_", name.lower().strip("_"))
    safe = re.sub(r"_+", "_", safe).strip("_") or "recorded_flow"
    path = os.path.join(FLOWS_DIR, f"{safe}.flow")
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(path,"w",encoding="utf-8") as f:
        f.write(f"# flows/{safe}.flow\n")
        f.write(f"# Recorded {ts} — platform: {platform.upper()}\n")
        f.write("# ─────────────────────────────────────────\n\n")
        for s in steps:
            f.write(s+"\n")
    return path

def _persist_locators(screen: str, recorded: dict, platform: str):
    import fcntl
    os.makedirs(os.path.dirname(LOCATORS_FILE), exist_ok=True)
    lock_path = LOCATORS_FILE + ".lock"
    with open(lock_path, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX)
            data = _load_locators()
            data.setdefault(platform, {}).setdefault(screen, {}).update(recorded)
            _save_locators(data)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    # Tell appium_action_service to reload on next use
    try:
        import execution.appium_action_service as _svc
        _svc.invalidate_locator_cache()
    except Exception:
        pass
    # Update known_names for autocomplete
    for n in recorded:
        if n not in _state["known_names"]:
            _state["known_names"].append(n)
    # Update latest panel
    for n in recorded:
        lst = _state["latest_names"]
        if n in lst:
            lst.remove(n)
        lst.insert(0, n)
        _state["latest_names"] = lst[:3]

# ─────────────────────────────────────────────────────────────────────────────
# NiceGUI PAGE
# ─────────────────────────────────────────────────────────────────────────────

def build_ui():
    # ── Seed known_names from existing locators file on startup ───────────────
    try:
        existing = _load_locators()
        plat_data = existing.get(_state["platform"], {})
        for group in plat_data.values():
            if isinstance(group, dict):
                for n in group:
                    if n not in _state["known_names"]:
                        _state["known_names"].append(n)
    except Exception:
        pass

    ui.page_title("📱 Appium Recorder")
    # ── Theme ──────────────────────────────────────────────────────────────────
    ui.add_head_html("""
    <style>
      body { background:#0f172a; color:#e2e8f0; font-family:'Inter',sans-serif; }
      .q-card { background:#1e293b!important; border:1px solid #334155; }
      .el-row:hover { background:#1e3a5f!important; cursor:pointer; }
      .el-row.selected { background:#1d4ed8!important; }
      .step-row { border-bottom:1px solid #334155; padding:4px 8px; }
      .step-row:hover { background:#1e293b; }
      ::-webkit-scrollbar { width:6px; } ::-webkit-scrollbar-thumb { background:#475569; border-radius:3px; }
    </style>
    """)

    # ── Layout ────────────────────────────────────────────────────────────────
    with ui.row().classes("w-full h-screen gap-0"):

        # ── LEFT: Device screenshot ───────────────────────────────────────────
        with ui.column().classes("w-72 bg-slate-900 border-r border-slate-700 p-3 gap-2"):
            ui.label("📱 Device Screen").classes("text-blue-400 font-bold text-sm")
            status_lbl = ui.label(_state["status"]).classes("text-xs text-slate-400")
            screenshot_img = ui.image("").classes("w-full rounded-lg border border-slate-700").style(
                "min-height:400px; object-fit:contain; background:#0f172a; cursor:crosshair;"
            )

            def on_screenshot_click(e):
                """Bidirectional: tap on screenshot → find + select element in list."""
                if not _state["elements"] or not _state["screenshot_b64"]:
                    return
                try:
                    ox = float(e.args.get("offsetX", 0))
                    oy = float(e.args.get("offsetY", 0))
                    dw_display = float(e.args.get("target", {}).get("clientWidth", 0) or
                                       e.args.get("currentTarget", {}).get("clientWidth", 288))
                    dh_display = float(e.args.get("target", {}).get("clientHeight", 0) or
                                       e.args.get("currentTarget", {}).get("clientHeight", 600))
                    if dw_display == 0 or dh_display == 0:
                        dw_display, dh_display = 288, 600

                    ss_w, ss_h = _state["screenshot_size"]
                    # Scale display pixels → screenshot pixels
                    dev_x = int(ox * ss_w / dw_display)
                    dev_y = int(oy * ss_h / dh_display)

                    # Find the smallest element whose bounds contain (dev_x, dev_y)
                    best = None
                    best_area = float("inf")
                    for el in _state["elements"]:
                        b = el.get("bounds")
                        if not b: continue
                        x1,y1,x2,y2 = b
                        if x1 <= dev_x <= x2 and y1 <= dev_y <= y2:
                            area = (x2-x1)*(y2-y1)
                            if area < best_area:
                                best_area = area
                                best = el

                    if best:
                        _state["selected_el"] = best
                        # Draw highlight
                        highlighted = _highlight_screenshot(
                            _state["screenshot_b64"], best["bounds"], best["index"], best["hint"]
                        )
                        screenshot_img.set_source(f"data:image/png;base64,{highlighted}")
                        # Update detail panel
                        sel_lbl.set_text(
                            f"#{best['index']}  {best['tag'].split('.')[-1]}  —  \"{best['hint']}\""
                            f"\nLocator: {json.dumps(best['locator'])}"
                        )
                        name_input.set_value(
                            re.sub(r"[^a-z0-9_]","_",
                                   best["hint"].lower().replace(" ","_"))[:30].strip("_")
                        )
                        # Re-render list to show selection highlighted
                        _update_element_list()
                        # JS scroll the row into view
                        ui.run_javascript(
                            f'var el=document.getElementById("elrow-{best["index"]}");'
                            f'if(el)el.scrollIntoView({{behavior:"smooth",block:"center"}});'
                        )
                except Exception as ex:
                    logger.warning("Screenshot click failed: %s", ex)

            screenshot_img.on("click", on_screenshot_click)

            with ui.row().classes("gap-2 w-full"):
                async def on_refresh():
                    status_lbl.set_text("🔄 Refreshing…")
                    _state["selected_el"] = None   # clear highlight on manual refresh
                    await run.io_bound(_refresh_elements)
                    _update_element_list()
                    if _state["screenshot_b64"]:
                        screenshot_img.set_source(f"data:image/png;base64,{_state['screenshot_b64']}")
                    status_lbl.set_text(_state["status"])

                ui.button("🔄 Refresh", on_click=on_refresh).classes(
                    "flex-1 bg-blue-700 hover:bg-blue-600 text-white text-xs rounded"
                ).props("flat dense")

                async def on_back():
                    if _state["driver"]:
                        try: _state["driver"].back()
                        except: pass
                        await on_refresh()
                ui.button("◀ Back", on_click=on_back).classes(
                    "flex-1 bg-slate-700 hover:bg-slate-600 text-white text-xs rounded"
                ).props("flat dense")

            # Auto-refresh toggle
            auto_toggle = ui.switch("Auto-refresh (3s)", value=True).classes("text-xs text-slate-300")
            def toggle_auto(e):
                _state["auto_refresh"] = e.value
            auto_toggle.on("update:model-value", toggle_auto)

            # ── Popup / Permission quick-dismiss panel ────────────────────────
            with ui.card().classes("w-full p-2 mt-1 bg-amber-950 border border-amber-700"):
                with ui.row().classes("justify-between items-center mb-1"):
                    ui.label("🚫 Popups & Permissions").classes("text-amber-400 font-bold text-xs")
                    popup_status_lbl = ui.label("").classes("text-xs text-green-400")

                async def _do_dismiss_and_refresh(label: str):
                    """Dismiss a popup/permission and fully refresh the UI."""
                    driver = _state.get("driver")
                    if not driver:
                        ui.notify("Not connected", color="negative")
                        return
                    popup_status_lbl.set_text("⏳…")
                    result = await run.io_bound(_dismiss_popups, driver, _state["platform"])
                    await asyncio.sleep(0.6)
                    await run.io_bound(_refresh_elements)
                    _state["selected_el"] = None
                    if _state["screenshot_b64"]:
                        screenshot_img.set_source(f"data:image/png;base64,{_state['screenshot_b64']}")
                    _update_element_list()
                    if result:
                        popup_status_lbl.set_text(f"✅ {result}")
                        ui.notify(f"Dismissed: {result}", color="positive")
                    else:
                        popup_status_lbl.set_text("⚠️ nothing found")
                        ui.notify("No popup/alert detected", color="warning")

                async def _tap_allow():
                    driver = _state.get("driver")
                    if not driver: return
                    popup_status_lbl.set_text("⏳…")
                    from appium.webdriver.common.appiumby import AppiumBy
                    tapped = False
                    for label in ["Allow","Allow all the time","Allow only while using the app",
                                  "Allow Once","Only this time","OK","Got it","Accept","Continue","Yes"]:
                        for by, val in [
                            (AppiumBy.ACCESSIBILITY_ID, label),
                            (AppiumBy.XPATH,
                             f'//*[@text="{label}" or @label="{label}" or @name="{label}"]'),
                        ]:
                            try:
                                el = driver.find_element(by, val)
                                if el.is_displayed():
                                    el.click(); tapped = True; break
                            except Exception: pass
                        if tapped: break
                    if not tapped:
                        try: driver.switch_to.alert.accept(); tapped = True
                        except: pass
                    await asyncio.sleep(0.6)
                    await run.io_bound(_refresh_elements)
                    if _state["screenshot_b64"]:
                        _state["selected_el"] = None
                        screenshot_img.set_source(f"data:image/png;base64,{_state['screenshot_b64']}")
                    _update_element_list()
                    popup_status_lbl.set_text("✅ Allow tapped" if tapped else "⚠️ not found")

                async def _tap_deny():
                    driver = _state.get("driver")
                    if not driver: return
                    popup_status_lbl.set_text("⏳…")
                    from appium.webdriver.common.appiumby import AppiumBy
                    tapped = False
                    for label in ["Deny","Don't Allow","No thanks","Not now","Skip","Cancel","Close","Maybe later"]:
                        for by, val in [
                            (AppiumBy.ACCESSIBILITY_ID, label),
                            (AppiumBy.XPATH,
                             f'//*[@text="{label}" or @label="{label}" or @name="{label}"]'),
                        ]:
                            try:
                                el = driver.find_element(by, val)
                                if el.is_displayed():
                                    el.click(); tapped = True; break
                            except Exception: pass
                        if tapped: break
                    if not tapped:
                        try: driver.switch_to.alert.dismiss(); tapped = True
                        except: pass
                    await asyncio.sleep(0.6)
                    await run.io_bound(_refresh_elements)
                    if _state["screenshot_b64"]:
                        _state["selected_el"] = None
                        screenshot_img.set_source(f"data:image/png;base64,{_state['screenshot_b64']}")
                    _update_element_list()
                    popup_status_lbl.set_text("✅ Deny tapped" if tapped else "⚠️ not found")

                async def _tap_outside():
                    driver = _state.get("driver")
                    if not driver: return
                    try:
                        size = driver.get_window_size()
                        # Tap top-left corner — usually outside any dialog
                        driver.tap([(20, 20)])
                    except Exception:
                        pass
                    await asyncio.sleep(0.6)
                    await run.io_bound(_refresh_elements)
                    if _state["screenshot_b64"]:
                        _state["selected_el"] = None
                        screenshot_img.set_source(f"data:image/png;base64,{_state['screenshot_b64']}")
                    _update_element_list()
                    popup_status_lbl.set_text("↩ tapped outside")

                with ui.row().classes("gap-1 flex-wrap"):
                    ui.button("✅ Allow / OK",
                        on_click=_tap_allow
                    ).props("flat dense").classes("text-xs bg-green-800 text-white rounded")
                    ui.button("❌ Deny / Skip",
                        on_click=_tap_deny
                    ).props("flat dense").classes("text-xs bg-red-900 text-white rounded")
                    ui.button("🙅 Auto Dismiss",
                        on_click=lambda: _do_dismiss_and_refresh("auto")
                    ).props("flat dense").classes("text-xs bg-amber-800 text-white rounded")
                    ui.button("👆 Tap Outside",
                        on_click=_tap_outside
                    ).props("flat dense").classes("text-xs bg-slate-700 text-white rounded")

                # Auto-dismiss toggle
                _state["auto_dismiss"] = False
                auto_dismiss_toggle = ui.switch("Auto-dismiss popups after each action", value=False
                ).classes("text-xs text-amber-300 mt-1")
                def on_auto_dismiss(e):
                    _state["auto_dismiss"] = e.value
                auto_dismiss_toggle.on("update:model-value", on_auto_dismiss)

        # ── MIDDLE: Elements + Actions ────────────────────────────────────────
        with ui.column().classes("flex-1 p-3 gap-3 overflow-hidden"):

            # ── Connection bar ─────────────────────────────────────────────────
            with ui.card().classes("w-full p-3"):
                ui.label("🔌 Connection").classes("text-blue-400 font-bold text-sm mb-1")
                with ui.row().classes("gap-3 items-center flex-wrap"):
                    plat_select = ui.select(
                        ["android","ios"], value=_state["platform"], label="Platform"
                    ).classes("w-32")
                    screen_input = ui.input(label="Screen name", value=_state["screen_name"]
                    ).classes("w-40")
                    conn_btn = ui.button("▶ Connect", color="green").classes("text-sm")
                    disconn_btn = ui.button("⏹ Disconnect", color="red").classes("text-sm")
                    conn_status = ui.label("").classes("text-xs text-slate-400")

                async def do_connect():
                    conn_status.set_text("⏳ Connecting…")
                    conn_btn.props("disabled")
                    _state["platform"]    = plat_select.value
                    _state["screen_name"] = screen_input.value or "home_screen"
                    try:
                        driver = await run.io_bound(_start_session, _state["caps"], _state["platform"])
                        _state["driver"] = driver
                        _state["status"] = "✅ Connected"
                        conn_status.set_text("✅ Connected")
                        await run.io_bound(_refresh_elements)
                        _update_element_list()
                        if _state["screenshot_b64"]:
                            screenshot_img.set_source(
                                f"data:image/png;base64,{_state['screenshot_b64']}"
                            )
                        # Start auto-refresh thread
                        t = threading.Thread(target=_auto_refresh_loop, daemon=True)
                        t.start()
                    except Exception as e:
                        conn_status.set_text(f"❌ {e}")
                        _state["status"] = f"❌ {e}"
                    finally:
                        conn_btn.props(remove="disabled")

                def do_disconnect():
                    _state["auto_refresh"] = False
                    if _state["driver"]:
                        try: _state["driver"].quit()
                        except: pass
                        _state["driver"] = None
                    conn_status.set_text("Disconnected")
                    _state["status"] = "Disconnected"

                conn_btn.on("click", do_connect)
                disconn_btn.on("click", do_disconnect)

            # ── Element list ───────────────────────────────────────────────────
            with ui.card().classes("w-full p-3").style("flex:1; overflow:hidden;"):
                with ui.row().classes("justify-between items-center mb-1"):
                    ui.label("🔍 Elements on Screen").classes("text-blue-400 font-bold text-sm")
                    el_count_lbl = ui.label("0 elements").classes("text-xs text-slate-400")

                # ── Search / filter bar ────────────────────────────────────────
                with ui.row().classes("w-full gap-1 items-center mb-1"):
                    filter_input = ui.input(placeholder="🔎 filter by name / tag / id…").classes(
                        "flex-1 text-xs"
                    ).props("dense clearable")
                    type_filter = ui.select(
                        ["All", "Clickable", "Input", "Text", "Image", "Button"],
                        value="All", label="Type"
                    ).classes("w-24").props("dense")

                def on_filter_change(_=None):
                    _state["el_filter"] = filter_input.value or ""
                    _state["el_type_filter"] = type_filter.value
                    _update_element_list()

                filter_input.on("update:model-value", on_filter_change)
                type_filter.on("update:model-value", on_filter_change)

                el_scroll = ui.scroll_area().classes("w-full").style("height:300px;")
                el_container = el_scroll

                # Selected element detail
                with ui.card().classes("w-full p-2 mt-2 bg-slate-800"):
                    sel_lbl = ui.label("No element selected").classes("text-xs text-slate-300")

                # ── Name + Action form ─────────────────────────────────────────
                with ui.card().classes("w-full p-3 mt-2"):
                    with ui.row().classes("justify-between items-center mb-2"):
                        ui.label("✏️ Record Element").classes("text-blue-400 font-bold text-sm")
                        if_visible_toggle = ui.switch("If Visible").classes("text-xs text-yellow-300")

                    # Row 1: Element name (large, autocomplete) + Action + Execute button
                    with ui.row().classes("gap-2 flex-wrap items-end w-full"):
                        name_input = ui.select(
                            options=list(_state["known_names"]),
                            value=None, label="Element name",
                            new_value_mode="add-unique", with_input=True,
                        ).classes("flex-1 text-sm").props("dense clearable")

                        action_select = ui.select(
                            # ── Element actions ─────────────────────────────────
                            ["tap", "type", "verify exists", "verify text",
                             "double tap", "long press", "store text",
                             "scroll to", "wait for element",
                             # ── Gestures / Device ─────────────────────────────
                             "scroll down", "scroll up",
                             "swipe left", "swipe right",
                             "press back", "press enter", "dismiss alerts",
                             # ── Page ──────────────────────────────────────────
                             "screenshot", "wait", "go to url",
                             # ── Test data (Faker) ────────────────────────────
                             "fake name", "fake email", "fake phone",
                             "fake uuid", "fake number", "fake address",
                             "fake company", "fake username", "fake password",
                             "random number", "random string",
                             # ── Date / Time ──────────────────────────────────
                             "get today", "get timestamp", "get date offset",
                             # ── HTTP / API ───────────────────────────────────
                             "api get", "api post", "store json path",
                             # ── Excel / CSV ──────────────────────────────────
                             "read excel cell", "read excel row", "read csv cell",
                             # ── JavaScript (WebView / mobile-browser context) ─────
                             "js click", "js scroll to",
                             "js scroll down", "js scroll up",
                             "js scroll top", "js scroll bottom",
                             "js type", "js focus", "js submit", "js dispatch"],
                            value="tap", label="Action"
                        ).classes("w-44").props("dense").props("use-input input-debounce=0 hide-selected fill-input")

                        record_btn = ui.button("⚡ Execute + Add Step", color="blue").classes("text-sm font-bold")

                    # Row 2: Extra / var (autocomplete from RUNTIME_VARIABLES) + index picker + scroll toggle + wait
                    with ui.row().classes("gap-2 flex-wrap items-end w-full mt-1"):
                        from nlp.variable_manager import RUNTIME_VARIABLES
                        _var_names = [f"${{{k}}}" for k in RUNTIME_VARIABLES]
                        extra_input = ui.select(
                            options=_var_names,
                            value=None, label="Extra (text / ${var} / seconds)",
                            new_value_mode="add-unique", with_input=True,
                        ).classes("flex-1 text-sm").props("dense clearable")

                    # Contextual hint — updates when action changes
                    _MOB_HINTS = {
                        # Element
                        "tap":             "Element name → tap it on device",
                        "type":            "Extra = text to type  |  Element name = target field",
                        "verify exists":   "Element name → assert it is present on screen",
                        "verify text":     "Extra = expected text  |  taps anywhere on screen",
                        "store text":      "Element name = source  |  Extra = variable name to save into",
                        "double tap":      "Element name → double-tap gesture",
                        "long press":      "Element name → long-press / hold gesture",
                        "scroll to":       "Element name → scroll until it is visible",
                        "wait for element":"Element name → wait until it appears (15s timeout)",
                        # Gestures
                        "scroll down":     "No inputs needed — swipe up on screen",
                        "scroll up":       "No inputs needed — swipe down on screen",
                        "swipe left":      "No inputs needed — swipe left gesture",
                        "swipe right":     "No inputs needed — swipe right gesture",
                        "press back":      "No inputs needed — device back button",
                        "press enter":     "No inputs needed — keyboard return/go key",
                        "dismiss alerts":  "No inputs needed — dismiss permission dialogs",
                        # Page
                        "screenshot":      "Extra = label for the screenshot file",
                        "wait":            "Extra = seconds  e.g.  2",
                        "go to url":       "Extra = full URL  e.g.  https://example.com",
                        # Fake data
                        "fake name":       "Extra = variable name  e.g.  user_name",
                        "fake email":      "Extra = variable name  e.g.  reg_email",
                        "fake phone":      "Extra = variable name  e.g.  phone_num",
                        "fake uuid":       "Extra = variable name  e.g.  request_id",
                        "fake number":     "Extra = variable name  e.g.  item_count",
                        "fake address":    "Extra = variable name  e.g.  user_address",
                        "fake company":    "Extra = variable name  e.g.  company_name",
                        "fake username":   "Extra = variable name  e.g.  login_user",
                        "fake password":   "Extra = variable name  e.g.  login_pass",
                        "random number":   "Extra = min max  e.g.  1000 9999  |  Element = variable name",
                        "random string":   "Extra = length  e.g.  8  |  Element = variable name",
                        # Date / Time
                        "get today":       "Extra = variable name  e.g.  today_date  → stores 03/03/2026",
                        "get timestamp":   "Extra = variable name  e.g.  ts  → stores 2026-03-03 05:50:08",
                        "get date offset": "Extra = +N or -N  e.g.  +7  |  Element = variable name",
                        # HTTP / API
                        "api get":         "Extra = URL  |  Element = variable to store response",
                        "api post":        "Extra = URL  |  Element = variable name  (body is a template)",
                        "store json path": "Extra = source variable  |  Element = dot-path  e.g.  data.0.email",
                        # Excel / CSV
                        "read excel cell": "Extra = file path  e.g.  data/sheet.xlsx  |  Element = variable name",
                        "read excel row":  "Extra = file path  e.g.  data/sheet.xlsx  |  Element = variable name",
                        "read csv cell":   "Extra = file path  e.g.  data/users.csv  |  Element = variable name",
                        # JavaScript
                        "js click":        "Element = target  — JS el.click() bypasses overlays / pointer-events:none",
                        "js scroll to":    "Element = target  — scrollIntoView (smooth)",
                        "js scroll down":  "Extra = pixels  e.g.  300  — window.scrollBy(0, N)",
                        "js scroll up":    "Extra = pixels  e.g.  300  — window.scrollBy(0, -N)",
                        "js scroll top":   "No inputs needed  — scroll to very top",
                        "js scroll bottom":"No inputs needed  — scroll to very bottom",
                        "js type":         "Extra = text  |  Element = field  — React-aware value setter",
                        "js focus":        "Element = target  — el.focus() + focus events",
                        "js submit":       "Element = form  — el.submit() or dispatch submit event",
                        "js dispatch":     "Extra = event name  e.g.  change  |  Element = target",
                    }
                    mob_hint_lbl = ui.label("").classes(
                        "text-xs text-amber-300 w-full px-1 py-0.5 rounded"
                    ).style("background:#1c1917;min-height:18px;font-style:italic;")

                    def _update_mob_hint(e=None):
                        act = action_select.value or ""
                        mob_hint_lbl.set_text(_MOB_HINTS.get(act, ""))
                    action_select.on("update:model-value", _update_mob_hint)

                    with ui.row().classes("gap-2 flex-wrap items-end w-full mt-1"):
                        index_select = ui.select(
                            ["any", "1st", "2nd", "3rd", "4th", "5th",
                             "last", "last-2", "last-3", "last-4"],
                            value="any", label="Index"
                        ).classes("w-28").props("dense")

                        scroll_toggle = ui.switch("Scroll to").classes("text-xs text-cyan-300")

                        wait_secs_input = ui.number(
                            label="If-Vis wait (s)", value=0, min=0, max=60, step=1
                        ).classes("w-28").props("dense")
                        wait_secs_input.set_visibility(False)

                    def on_if_visible_toggle(e):
                        wait_secs_input.set_visibility(e.value)
                    if_visible_toggle.on("update:model-value", on_if_visible_toggle)

                    def _refresh_name_options():
                        """Sync autocomplete options with current known_names."""
                        name_input.options = list(_state["known_names"])
                        name_input.update()

                    def _refresh_var_options():
                        """Sync extra field options with current RUNTIME_VARIABLES."""
                        from nlp.variable_manager import RUNTIME_VARIABLES
                        opts = [f"${{{k}}}" for k in RUNTIME_VARIABLES]
                        extra_input.options = opts
                        extra_input.update()

                    def _sanitise_name(raw: str) -> str:
                        return re.sub(r"[^a-z0-9_]", "_",
                                      (raw or "").strip().lower().replace("-","_"))[:40].strip("_")

                    async def do_record():
                        el         = _state.get("selected_el")
                        raw_name   = (name_input.value or "").strip()
                        name       = _sanitise_name(raw_name)
                        action     = action_select.value
                        extra      = (extra_input.value or "").strip()
                        if_visible = if_visible_toggle.value
                        wait_secs  = float(wait_secs_input.value or 0) if if_visible else 0
                        el_index   = index_select.value        # "any","1st","last",…
                        do_scroll  = scroll_toggle.value       # bool

                        if not name:
                            ui.notify("Enter an element name first", color="negative")
                            return

                        # Inject index into name suffix if not "any"
                        name_with_idx = name
                        if el_index != "any":
                            # Store index hint alongside element name in step comment
                            # We encode it as a name suffix for the locator key
                            pass  # index is passed to _build_step

                        step = _build_step(el, name, action, extra,
                                           if_visible=if_visible, wait_secs=wait_secs,
                                           el_index=el_index, scroll_first=do_scroll)
                        if step:
                            record_btn.props("disabled")
                            status_lbl.set_text("⚡ Executing…")

                            # _build_step may return "scroll to X\ntap X" — split into two steps
                            step_lines = [ln for ln in step.split("\n") if ln.strip()]

                            edit_idx = _state.get("edit_step_idx")
                            if edit_idx is not None:
                                # Update existing step (replace with potentially 2 steps)
                                if 0 <= edit_idx < len(_state["flow_steps"]):
                                    _state["flow_steps"][edit_idx:edit_idx+1] = step_lines
                                _state["edit_step_idx"] = None
                                record_btn.set_text("⚡ Execute + Add Step")
                                ui.notify(f"✏️ Updated step {edit_idx+1}: {step_lines[-1]}", color="info")
                            else:
                                # Execute on device then append
                                await run.io_bound(_execute_step_on_device, el, action, extra, name,
                                                   scroll_first=do_scroll, el_index=el_index)
                                _state["flow_steps"].extend(step_lines)
                                tag = " ⚠️ if-visible" if if_visible else ""
                                ui.notify(f"✅ Added{tag}: {step_lines[-1]}", color="positive")

                            # Save locator
                            if el:
                                _state["recorded"][name] = el["locator"]
                                _persist_locators(_state["screen_name"], {name: el["locator"]}, _state["platform"])

                            _update_flow_list()
                            _refresh_name_options()
                            name_input.set_value(None)
                            extra_input.set_value(None)
                            index_select.set_value("any")
                            scroll_toggle.set_value(False)

                            # Auto-dismiss any popup that appeared after the action
                            if _state.get("auto_dismiss"):
                                await run.io_bound(_dismiss_popups)
                            # Wait for app to settle, then full refresh (screenshot + elements)
                            await asyncio.sleep(0.8)
                            await run.io_bound(_refresh_elements)
                            _state["selected_el"] = None
                            if _state["screenshot_b64"]:
                                screenshot_img.set_source(
                                    f"data:image/png;base64,{_state['screenshot_b64']}"
                                )
                            _update_element_list()
                            _update_live_panel()
                            status_lbl.set_text(_state["status"])
                            record_btn.props(remove="disabled")

                    record_btn.on("click", do_record)

                # ── Live Variables + Elements panel ────────────────────────────
                with ui.card().classes("w-full p-2 mt-2 bg-indigo-950 border border-indigo-700"):
                    ui.label("📡 Live State").classes("text-indigo-300 font-bold text-xs mb-1")
                    live_panel = ui.column().classes("gap-0 w-full")

                def _update_live_panel():
                    """Refresh the live vars+names mini-panel."""
                    from nlp.variable_manager import RUNTIME_VARIABLES
                    live_panel.clear()
                    with live_panel:
                        # Last 3 stored variables
                        var_items = list(RUNTIME_VARIABLES.items())[-3:]
                        if var_items:
                            ui.label("Variables:").classes("text-xs text-indigo-400 font-semibold")
                            for k, v in reversed(var_items):
                                ui.label(f"  ${{{k}}} = {str(v)[:40]}").classes("text-xs text-green-300 font-mono")
                        else:
                            ui.label("  No variables stored yet").classes("text-xs text-slate-500")
                        # Last 3 recorded element names
                        names = _state.get("latest_names", [])
                        if names:
                            ui.label("Elements:").classes("text-xs text-indigo-400 font-semibold mt-1")
                            for n in names:
                                ui.label(f"  🔖 {n}").classes("text-xs text-cyan-300 font-mono")



        # ── RIGHT: Flow builder ───────────────────────────────────────────────
        with ui.column().classes("w-96 bg-slate-900 border-l border-slate-700 p-3 gap-2"):
            ui.label("📋 Flow Steps").classes("text-blue-400 font-bold text-sm")
            step_count_lbl = ui.label("0 steps").classes("text-xs text-slate-400")

            flow_scroll = ui.scroll_area().classes("w-full").style("height:480px;")

            # ── Quick-add non-element steps ────────────────────────────────────
            with ui.card().classes("w-full p-2 mt-2"):
                ui.label("➕ Quick Add").classes("text-xs text-slate-400 mb-1")
                with ui.row().classes("gap-1 flex-wrap"):
                    def qadd(step):
                        _state["flow_steps"].append(step)
                        _update_flow_list()
                        ui.notify(f"Added: {step}", color="positive")

                    ui.button("↩ Back",    on_click=lambda: qadd("press back")).props("flat dense").classes("text-xs bg-slate-700")
                    ui.button("↵ Enter",   on_click=lambda: qadd("press enter")).props("flat dense").classes("text-xs bg-slate-700")
                    ui.button("🙅 Dismiss", on_click=lambda: qadd("dismiss alerts")).props("flat dense").classes("text-xs bg-slate-700")
                    ui.button("⬇ Scroll",  on_click=lambda: qadd("scroll down")).props("flat dense").classes("text-xs bg-slate-700")
                    ui.button("⬆ Scroll",  on_click=lambda: qadd("scroll up")).props("flat dense").classes("text-xs bg-slate-700")

                with ui.row().classes("gap-1 mt-1 items-center"):
                    wait_input = ui.number(label="Wait (s)", value=1, min=0.5, step=0.5).classes("w-24")
                    ui.button("⏱ Add Wait",
                        on_click=lambda: qadd(f"wait {int(wait_input.value) if float(wait_input.value)==int(wait_input.value) else wait_input.value} seconds")
                    ).props("flat dense").classes("text-xs bg-slate-700")

            # ── Flow actions ───────────────────────────────────────────────────
            with ui.row().classes("gap-1 mt-1 flex-wrap"):
                def do_undo():
                    if _state["flow_steps"]:
                        removed = _state["flow_steps"].pop()
                        _update_flow_list()
                        ui.notify(f"Undone: {removed}", color="warning")
                    else:
                        ui.notify("Nothing to undo", color="negative")

                def do_clear():
                    _state["flow_steps"].clear()
                    _update_flow_list()
                    ui.notify("Flow cleared", color="warning")

                ui.button("↩ Undo", on_click=do_undo).props("flat dense").classes("text-xs bg-slate-700")
                ui.button("🗑 Clear All", on_click=do_clear).props("flat dense").classes("text-xs bg-red-900")

            # ── Open existing flow ─────────────────────────────────────────────
            with ui.card().classes("w-full p-2 mt-2"):
                ui.label("📂 Open Flow File").classes("text-xs text-slate-400 mb-1")

                def _list_flows():
                    os.makedirs(FLOWS_DIR, exist_ok=True)
                    return sorted([
                        f for f in os.listdir(FLOWS_DIR) if f.endswith(".flow")
                    ])

                def _parse_flow_file(path: str) -> list:
                    """Read a .flow file and return non-comment, non-blank lines."""
                    steps = []
                    with open(path, "r", encoding="utf-8") as fh:
                        for line in fh:
                            stripped = line.strip()
                            if stripped and not stripped.startswith("#"):
                                steps.append(stripped)
                    return steps

                flow_files = _list_flows()
                open_select = ui.select(
                    flow_files or ["(no flows saved yet)"],
                    value=flow_files[0] if flow_files else "(no flows saved yet)",
                    label="Select .flow"
                ).classes("w-full").props("dense")

                def refresh_flow_list_select():
                    flist = _list_flows()
                    open_select.options = flist or ["(no flows saved yet)"]
                    if flist:
                        open_select.set_value(flist[0])
                    open_select.update()

                with ui.row().classes("gap-1 mt-1"):
                    def do_open_flow():
                        fname = open_select.value
                        if not fname or fname == "(no flows saved yet)":
                            ui.notify("No flow selected", color="negative")
                            return
                        path = os.path.join(FLOWS_DIR, fname)
                        if not os.path.exists(path):
                            ui.notify(f"File not found: {fname}", color="negative")
                            return
                        try:
                            steps = _parse_flow_file(path)
                            _state["flow_steps"] = steps
                            _update_flow_list()
                            ui.notify(f"✅ Loaded {len(steps)} steps from {fname}", color="positive")
                            logger.info("Loaded %d steps from %s", len(steps), fname)
                        except Exception as ex:
                            ui.notify(f"❌ Load failed: {ex}", color="negative")
                            logger.exception("do_open_flow error")

                    ui.button("📂 Load into editor", on_click=do_open_flow, color="blue"
                    ).props("flat dense").classes("text-xs")
                    ui.button("🔃 Refresh list", on_click=refresh_flow_list_select
                    ).props("flat dense").classes("text-xs bg-slate-700")

            # ── Save flow ──────────────────────────────────────────────────────
            with ui.card().classes("w-full p-2 mt-2"):
                ui.label("💾 Save Flow").classes("text-xs text-slate-400 mb-1")
                flow_name_input = ui.input(
                    label="Flow filename", value=f"{_state['platform']}_recorded"
                ).classes("w-full")

                def do_save():
                    name  = flow_name_input.value.strip()
                    steps = _state["flow_steps"]
                    if not steps:
                        ui.notify("No steps to save", color="negative")
                        return
                    if not name:
                        ui.notify("Enter a flow name", color="negative")
                        return
                    path = _save_flow(name, steps, _state["platform"])
                    ui.notify(f"✅ Saved: {path}", color="positive")

                ui.button("💾 Save .flow file", on_click=do_save, color="green").classes("w-full text-sm mt-1")
                # After saving, refresh the open-flow dropdown
                def do_save_and_refresh():
                    do_save()
                    refresh_flow_list_select()

                # Re-wire save button to also refresh dropdown
                # (replace inline lambda with wrapper — we patch via a second on-click)

            # ── Run flow on device ─────────────────────────────────────────────
            with ui.card().classes("w-full p-2 mt-2 border border-green-800"):
                with ui.row().classes("justify-between items-center mb-1"):
                    ui.label("▶ Run Flow on Device").classes("text-green-400 font-bold text-xs")
                    run_status_lbl = ui.label("").classes("text-xs text-slate-400")

                run_log_area = ui.log(max_lines=80).classes("w-full text-xs font-mono").style(
                    "height:160px; background:#0f172a; color:#86efac; border:1px solid #166534;"
                )

                with ui.row().classes("gap-1 mt-1 flex-wrap items-center"):
                    run_btn      = ui.button("▶ Run editor steps", color="green").props("flat dense").classes("text-sm font-bold")
                    run_file_btn = ui.button("▶ Run selected file", color="teal").props("flat dense").classes("text-sm font-bold")
                    stop_btn     = ui.button("⏹ Stop", color="red").props("flat dense").classes("text-sm")
                    stop_btn.set_visibility(False)

                _state["run_cancelled"] = False

                async def _run_steps(steps: list):
                    """Core execution loop — shared by both run buttons."""
                    driver = _state.get("driver")
                    if not driver:
                        ui.notify("Connect to a device first", color="negative")
                        return

                    platform = _state["platform"]
                    _state["run_cancelled"] = False
                    run_btn.set_visibility(False)
                    run_file_btn.set_visibility(False)
                    stop_btn.set_visibility(True)
                    run_log_area.clear()
                    run_status_lbl.set_text(f"▶ Running {len(steps)} steps…")

                    import runner_appium as _runner

                    passed = 0
                    failed = 0
                    for i, step in enumerate(steps, 1):
                        if _state["run_cancelled"]:
                            run_log_area.push(f"⏹  Cancelled at step {i}")
                            break
                        run_log_area.push(f"[{i:2d}/{len(steps)}] ▶ {step}")
                        try:
                            await run.io_bound(
                                _runner._execute_single_step, step, driver, platform
                            )
                            run_log_area.push(f"        ✅ OK")
                            passed += 1
                        except Exception as ex:
                            run_log_area.push(f"        ❌ {ex}")
                            failed += 1
                        if _state.get("auto_dismiss"):
                            await run.io_bound(_dismiss_popups)
                        await asyncio.sleep(0.5)
                        await run.io_bound(_grab_screenshot)
                        if _state["screenshot_b64"] and not _state.get("selected_el"):
                            screenshot_img.set_source(
                                f"data:image/png;base64,{_state['screenshot_b64']}"
                            )

                    summary = f"✅ {passed} passed  ❌ {failed} failed  out of {len(steps)} steps"
                    run_log_area.push(f"\n{'─'*40}\n{summary}")
                    run_status_lbl.set_text(summary)
                    run_btn.set_visibility(True)
                    run_file_btn.set_visibility(True)
                    stop_btn.set_visibility(False)
                    await run.io_bound(_refresh_elements)
                    _update_element_list()
                    _update_live_panel()
                    if _state["screenshot_b64"]:
                        screenshot_img.set_source(
                            f"data:image/png;base64,{_state['screenshot_b64']}"
                        )

                async def do_run_flow():
                    """Run whatever is currently in the editor step list."""
                    steps = _state["flow_steps"]
                    if not steps:
                        ui.notify(
                            "No steps in editor — use '▶ Run selected file' to run a saved flow directly",
                            color="warning", timeout=5000
                        )
                        return
                    await _run_steps(steps)

                async def do_run_file():
                    """Load the selected .flow file from the dropdown and run it immediately."""
                    fname = open_select.value
                    if not fname or fname == "(no flows saved yet)":
                        ui.notify("Select a .flow file from the dropdown above first", color="negative")
                        return
                    path = os.path.join(FLOWS_DIR, fname)
                    if not os.path.exists(path):
                        ui.notify(f"File not found: {fname}", color="negative")
                        return
                    try:
                        steps = _parse_flow_file(path)
                    except Exception as ex:
                        ui.notify(f"❌ Could not read file: {ex}", color="negative")
                        return
                    if not steps:
                        ui.notify(f"Flow file is empty: {fname}", color="negative")
                        return
                    # Also load into editor so user can see steps
                    _state["flow_steps"] = steps
                    _update_flow_list()
                    run_log_area.push(f"📂 Loaded {len(steps)} steps from {fname}")
                    await _run_steps(steps)

                def do_stop():
                    _state["run_cancelled"] = True
                    run_status_lbl.set_text("⏹ Stop requested…")

                run_btn.on("click", do_run_flow)
                run_file_btn.on("click", do_run_file)
                stop_btn.on("click", do_stop)

            # ── Env Setup ────────────────────────────────────────────────────
            with ui.card().classes("w-full p-3 mt-2 border border-yellow-700"):
                with ui.row().classes("justify-between items-center mb-2"):
                    ui.label("\u2699\ufe0f Environment Setup").classes("text-yellow-400 font-bold text-xs")
                    mob_env_status = ui.label("").classes("text-xs text-slate-400")
                from config import environment_manager as _em
                _mob_env_names  = _em.list_envs()
                _mob_active_env = _em.get_active_env_name()
                with ui.row().classes("gap-2 items-end flex-wrap w-full"):
                    mob_env_select = ui.select(
                        _mob_env_names or ["local"], value=_mob_active_env, label="Active Env",
                    ).classes("w-36").props("dense")
                    mob_new_env_input = ui.input(label="New env name").classes("w-28").props("dense clearable")
                    def _mob_add_env():
                        n_ = (mob_new_env_input.value or "").strip()
                        if not n_: return
                        _em.save_env(n_, {})
                        opts_ = _em.list_envs()
                        mob_env_select.options = opts_; mob_env_select.set_value(n_); mob_env_select.update()
                        mob_new_env_input.set_value("")
                        ui.notify(f"Created: {n_}", color="positive")
                        _mob_reload_env()
                    ui.button("+ New", on_click=_mob_add_env).props("flat dense").classes("text-xs bg-blue-900")
                    def _mob_del_env():
                        n_ = mob_env_select.value
                        if n_ == "local": ui.notify("Cannot delete 'local'", color="negative"); return
                        _em.delete_env(n_)
                        opts_ = _em.list_envs()
                        mob_env_select.options = opts_ or ["local"]; mob_env_select.set_value("local"); mob_env_select.update()
                        ui.notify(f"Deleted: {n_}", color="warning")
                        _mob_reload_env()
                    ui.button("\U0001f5d1", on_click=_mob_del_env, color="red").props("flat dense").classes("text-xs")
                with ui.row().classes("gap-2 items-end flex-wrap w-full mt-1"):
                    mob_src_input = ui.input(label="Domain Source", value="").classes("flex-1 min-w-32").props("dense clearable")
                    mob_dst_input = ui.input(label="Domain Override", value="").classes("flex-1 min-w-32").props("dense clearable")
                    mob_src_input.tooltip("e.g. www.justdial.com \u2014 URLs containing this will be rewritten")
                    mob_dst_input.tooltip("e.g. staging2.justdial.com \u2014 replacement host")
                with ui.row().classes("gap-2 items-end flex-wrap w-full mt-1"):
                    mob_auth_select = ui.select(["none", "basic", "popup"], value="none", label="Auth").classes("w-24").props("dense")
                    mob_user_input  = ui.input(label="Username", value="").classes("w-24").props("dense clearable")
                    mob_pass_input  = ui.input(label="Password", value="").classes("w-24").props("dense clearable password")
                    mob_user_input.set_visibility(False)
                    mob_pass_input.set_visibility(False)
                def _mob_auth_change(e=None):
                    show = (mob_auth_select.value or "none") != "none"
                    mob_user_input.set_visibility(show)
                    mob_pass_input.set_visibility(show)
                mob_auth_select.on("update:model-value", _mob_auth_change)
                def _mob_reload_env():
                    n_  = mob_env_select.value or "local"
                    e_  = _em.get_env(n_)
                    mob_src_input.set_value(e_.get("domain_source", ""))
                    mob_dst_input.set_value(e_.get("domain_override", ""))
                    mob_auth_select.set_value(e_.get("auth_type", "none"))
                    mob_user_input.set_value(e_.get("username", ""))
                    mob_pass_input.set_value(e_.get("password", ""))
                    _mob_auth_change()
                    act_ = _em.get_active_env_name()
                    _msym = "\u2705 active" if n_ == act_ else "\u2b55 inactive"
                    mob_env_status.set_text(f"{_msym} \u2014 {n_}")
                mob_env_select.on("update:model-value", lambda e: _mob_reload_env())
                _mob_reload_env()
                with ui.row().classes("gap-2 mt-2"):
                    def _mob_save_env():
                        n_ = mob_env_select.value or "local"
                        _em.save_env(n_, {
                            "domain_source":   mob_src_input.value  or "",
                            "domain_override": mob_dst_input.value  or "",
                            "auth_type":       mob_auth_select.value or "none",
                            "username":        mob_user_input.value or "",
                            "password":        mob_pass_input.value or "",
                        })
                        ui.notify(f"\U0001f4be Saved env: {n_}", color="positive")
                    def _mob_apply_env():
                        _mob_save_env()
                        n_ = mob_env_select.value or "local"
                        _em.set_active_env(n_)
                        mob_env_status.set_text(f"\u2705 active \u2014 {n_}")
                        ui.notify(f"\u2705 Active env \u2192 {n_}", color="positive")
                    ui.button("\U0001f4be Save", on_click=_mob_save_env, color="teal").props("flat dense").classes("text-xs")
                    ui.button("\u2705 Apply (set active)", on_click=_mob_apply_env, color="green").props("flat dense").classes("text-xs font-bold")
                    ui.label("scope: all URL steps").classes("text-xs text-slate-500 self-center ml-2")

    # ── Dynamic element list renderer ─────────────────────────────────────────
    # ── Dynamic element list renderer ─────────────────────────────────────────
    def _update_element_list():
        all_elements = _state["elements"]
        ftext  = (_state.get("el_filter") or "").lower().strip()
        ftype  = _state.get("el_type_filter", "All")

        def _matches(el):
            if ftext and not any([
                ftext in el["hint"].lower(),
                ftext in el["tag"].lower(),
                ftext in el.get("resource_id","").lower(),
                ftext in el.get("text","").lower(),
                ftext in el.get("acc","").lower(),
            ]):
                return False
            if ftype != "All":
                t = el["tag"].lower()
                if ftype == "Clickable" and not el["clickable"]: return False
                if ftype == "Input"    and not any(x in t for x in ["edittext","textfield","searchfield"]): return False
                if ftype == "Text"     and not any(x in t for x in ["textview","statictext"]): return False
                if ftype == "Image"    and not any(x in t for x in ["imageview","imagebutton","image"]): return False
                if ftype == "Button"   and not any(x in t for x in ["button"]): return False
            return True

        elements = [el for el in all_elements if _matches(el)]
        el_count_lbl.set_text(f"{len(elements)} / {len(all_elements)} elements")
        el_container.clear()
        with el_container:
            if not all_elements:
                ui.label("No elements — connect and press Refresh").classes("text-slate-400 text-xs p-4")
                return
            if not elements:
                ui.label(f'No match for "{ftext}"').classes("text-slate-400 text-xs p-4")
                return
            for el in elements:
                tag_short = el.get("tag_short", el["tag"].split(".")[-1])[:20]
                hint      = el["hint"][:34]
                is_sel    = bool(_state.get("selected_el") and
                                 _state["selected_el"]["index"] == el["index"])
                row_cls   = "el-row w-full flex gap-1 items-center p-1 rounded text-xs"
                if is_sel:
                    row_cls += " selected"

                with ui.row().classes(row_cls).props(f'id="elrow-{el["index"]}"') as row:
                    ui.label(str(el["index"])).classes(
                        "w-7 text-center rounded text-xs font-bold "
                        + ("bg-blue-600 text-white" if is_sel else "text-slate-500")
                    )
                    ui.label("✓" if el["clickable"] else "·").classes(
                        "w-3 " + ("text-green-400" if el["clickable"] else "text-slate-600")
                    )
                    t = el["tag"].lower()
                    if   any(x in t for x in ["edittext","textfield","searchfield"]):
                        tag_col = "text-cyan-400"
                    elif any(x in t for x in ["imagebutton","button"]):
                        tag_col = "text-orange-400"
                    elif any(x in t for x in ["imageview","image"]):
                        tag_col = "text-purple-400"
                    elif any(x in t for x in ["textview","statictext"]):
                        tag_col = "text-green-300"
                    else:
                        tag_col = "text-slate-300"
                    ui.label(tag_short).classes(f"w-24 truncate text-xs {tag_col}")
                    ui.label(hint).classes("flex-1 text-white truncate")

                def make_click(e=el):
                    def _on_click():
                        _state["selected_el"] = e
                        bounds = e.get("bounds")
                        tag_name = e['tag'].split('.')[-1]
                        hint_txt = e['hint']
                        loc_str  = json.dumps(e['locator'])
                        detail   = f"#{e['index']}  {tag_name}  —  \"{hint_txt}\"\nLocator: {loc_str}"
                        if bounds:
                            detail += f"\nbounds: {bounds}"
                        sel_lbl.set_text(detail)
                        suggested = re.sub(r"[^a-z0-9_]","_",
                                           e["hint"].lower().replace(" ","_"))[:30].strip("_")
                        # Populate name field; also persist to known_names so tick sync keeps it
                        if suggested and suggested not in _state["known_names"]:
                            _state["known_names"].append(suggested)
                        if suggested not in (name_input.options or []):
                            name_input.options = list(name_input.options or []) + [suggested]
                        name_input.set_value(suggested)
                        name_input.update()
                        if bounds and _state.get("screenshot_b64"):
                            highlighted = _highlight_screenshot(
                                _state["screenshot_b64"], bounds, e["index"], e["hint"]
                            )
                            screenshot_img.set_source(f"data:image/png;base64,{highlighted}")
                        _update_element_list()
                    return _on_click

                row.on("click", make_click())

    def _update_flow_list():
        steps = _state["flow_steps"]
        step_count_lbl.set_text(f"{len(steps)} steps")
        flow_scroll.clear()
        with flow_scroll:
            if not steps:
                ui.label("No steps yet — record elements or use Quick Add").classes("text-slate-400 text-xs p-4")
                return
            for i, step in enumerate(steps, 1):
                is_editing = (_state.get("edit_step_idx") == i - 1)
                row_bg = "bg-yellow-900 border border-yellow-600" if is_editing else ""
                with ui.row().classes(f"step-row w-full flex gap-2 items-center {row_bg}"):
                    ui.label(f"{i:2d}.").classes("w-6 text-slate-400 text-xs")
                    step_lbl = ui.label(step).classes(
                        "flex-1 text-white text-xs truncate cursor-pointer"
                        + (" text-yellow-300" if is_editing else "")
                    )
                    step_lbl.tooltip("Click to edit this step")

                    def make_edit(idx=i-1, s=step):
                        def _edit():
                            # Parse step back into form fields
                            _state["edit_step_idx"] = idx
                            record_btn.set_text(f"✏️ Update Step {idx+1}")
                            # Try to reverse-parse common patterns
                            _populate_form_from_step(s)
                            _update_flow_list()
                            ui.notify(f"✏️ Editing step {idx+1} — modify fields and click Update Step", color="info", timeout=4000)
                        return _edit

                    def make_del(idx=i-1):
                        def _del():
                            if 0 <= idx < len(_state["flow_steps"]):
                                removed = _state["flow_steps"].pop(idx)
                                if _state.get("edit_step_idx") == idx:
                                    _state["edit_step_idx"] = None
                                    record_btn.set_text("⚡ Execute + Add Step")
                                _update_flow_list()
                                ui.notify(f"Deleted: {removed}", color="warning")
                        return _del

                    def make_up(idx=i-1):
                        def _up():
                            if idx > 0:
                                s = _state["flow_steps"]
                                s[idx-1], s[idx] = s[idx], s[idx-1]
                                _update_flow_list()
                        return _up

                    def make_down(idx=i-1):
                        def _dn():
                            s = _state["flow_steps"]
                            if idx < len(s)-1:
                                s[idx], s[idx+1] = s[idx+1], s[idx]
                                _update_flow_list()
                        return _dn

                    step_lbl.on("click", make_edit())
                    ui.button("▲", on_click=make_up()).props("flat dense").classes("text-xs text-slate-400 p-0")
                    ui.button("▼", on_click=make_down()).props("flat dense").classes("text-xs text-slate-400 p-0")
                    ui.button("✕", on_click=make_del()).props("flat dense").classes("text-xs text-red-400 p-0")

    # ── Helper: parse a step string back into the record form fields ─────────
    def _populate_form_from_step(step: str):
        """
        Reverse-parse a flow step string and populate the Record Element form.
        Only updates fields we can safely infer; does not touch unknown patterns.
        Used by the tap-to-edit feature in the flow list.
        """
        s = step.strip()
        # Detect if-visible / wait suffix
        iv = bool(re.search(r'\bif\s+visible\b', s, re.I))
        if_visible_toggle.set_value(iv)
        wait_secs_input.set_visibility(iv)
        wm = re.search(r'\bwait\s+(\d+(?:\.\d+)?)\s+seconds?\s*$', s, re.I)
        if wm and iv:
            wait_secs_input.set_value(float(wm.group(1)))
        else:
            wait_secs_input.set_value(0)

        # Detect scroll_first prefix
        if s.lower().startswith("scroll to "):
            scroll_toggle.set_value(True)
        else:
            scroll_toggle.set_value(False)

        # Try to extract action + name + extra from common patterns
        action_found  = None
        name_found    = None
        extra_found   = None

        # tap / click
        m = re.match(r'^(?:tap|click)(?:\s+if\s+visible)?\s+(\S+)', s, re.I)
        if m and "double" not in s.lower() and "long" not in s.lower():
            action_found = "tap"; name_found = m.group(1).strip()

        # double tap
        m2 = re.match(r'^double\s+(?:tap|click)(?:\s+if\s+visible)?\s+(\S+)', s, re.I)
        if m2:
            action_found = "double tap"; name_found = m2.group(1).strip()

        # long press
        m3 = re.match(r'^long\s+press(?:\s+if\s+visible)?\s+(\S+)', s, re.I)
        if m3:
            action_found = "long press"; name_found = m3.group(1).strip()

        # type / fill
        m4 = re.match(r'^(?:type|fill)(?:\s+if\s+visible)?\s+"(.*?)"\s+(?:into|in)\s+(\S+)', s, re.I)
        if m4:
            action_found = "type"; extra_found = m4.group(1); name_found = m4.group(2).strip()

        # verify element exists
        m5 = re.match(r'^verify(?:\s+if\s+visible)?\s+(?:element\s+)?(?:exists\s+)?(\S+)', s, re.I)
        if m5 and "text" not in s.lower()[:20]:
            action_found = "verify exists"; name_found = m5.group(1).strip()

        # verify text
        m6 = re.match(r'^verify\s+text\s+"(.*?)"', s, re.I)
        if m6:
            action_found = "verify text"; extra_found = m6.group(1)

        # store text
        m7 = re.match(r'^store\s+text(?:\s+if\s+visible)?(?:\s+from)?\s+(\S+)\s+as\s+(\S+)', s, re.I)
        if m7:
            action_found = "store text"; name_found = m7.group(1).strip(); extra_found = m7.group(2).strip()

        # wait for element
        m8 = re.match(r'^wait\s+(?:for|until)\s+(?:element\s+)?(\S+)', s, re.I)
        if m8:
            action_found = "wait for element"; name_found = m8.group(1).strip()

        # scroll to
        m9 = re.match(r'^scroll\s+to\s+(?:element\s+)?(\S+)', s, re.I)
        if m9:
            action_found = "scroll to"; name_found = m9.group(1).strip()

        # screenshot
        m10 = re.match(r'^take\s+screenshot\s+as\s+(\S+)', s, re.I)
        if m10:
            action_found = "screenshot"; extra_found = m10.group(1)

        # wait N seconds
        m11 = re.fullmatch(r'wait\s+(\d+(?:\.\d+)?)\s*seconds?', s, re.I)
        if m11:
            action_found = "wait"; extra_found = m11.group(1)

        # scroll down/up, swipe, press back/enter, dismiss
        for act, pat in [
            ("scroll down",    r'^scroll\s+down'),
            ("scroll up",      r'^scroll\s+up'),
            ("swipe left",     r'^swipe\s+left'),
            ("swipe right",    r'^swipe\s+right'),
            ("press back",     r'^press\s+back'),
            ("press enter",    r'^press\s+enter'),
            ("dismiss alerts", r'^dismiss\s+alerts?'),
        ]:
            if re.match(pat, s, re.I):
                action_found = act; break

        # Apply to form
        if action_found and action_found in action_select.options:
            action_select.set_value(action_found)
        if name_found:
            opts = list(name_input.options or [])
            if name_found not in opts:
                opts.append(name_found)
                name_input.options = opts
            name_input.set_value(name_found)
            name_input.update()
        if extra_found is not None:
            opts2 = list(extra_input.options or [])
            if extra_found not in opts2:
                opts2.append(extra_found)
                extra_input.options = opts2
            extra_input.set_value(extra_found)
            extra_input.update()

    # Initial render
    _update_element_list()
    _update_flow_list()

    # ── Screenshot auto-update timer ──────────────────────────────────────────
    async def _tick():
        if not _state["driver"] or not _state["auto_refresh"]:
            return
        _state["tick_count"] = _state.get("tick_count", 0) + 1
        # Update screenshot display (only if no element selected — preserve highlight)
        if _state["screenshot_b64"] and not _state.get("selected_el"):
            screenshot_img.set_source(
                f"data:image/png;base64,{_state['screenshot_b64']}"
            )
        status_lbl.set_text(_state["status"])
        # Every 4th tick (~12s): also refresh element list to catch page navigations
        if _state["tick_count"] % 4 == 0 and not _state.get("selected_el"):
            await run.io_bound(_refresh_elements)
            _update_element_list()
            if _state["screenshot_b64"]:
                screenshot_img.set_source(
                    f"data:image/png;base64,{_state['screenshot_b64']}"
                )
        # Every 2nd tick: refresh live vars panel + name options
        if _state["tick_count"] % 2 == 0:
            _update_live_panel()
            _refresh_var_options()
            _refresh_name_options()

    ui.timer(3.0, _tick)


# ─── Step builder ─────────────────────────────────────────────────────────────

def _build_step(el, name: str, action: str, extra: str,
                if_visible: bool = False, wait_secs: float = 0,
                el_index: str = "any", scroll_first: bool = False) -> str | None:
    """
    Build a flow step string.
    el_index: "any"|"1st"|"2nd"|"last"|"last-2" etc — encoded as a leading
              'scroll to <name>' prefix step OR as a comment hint in the step.
    scroll_first: if True, prepend a 'scroll to <name>' step so the element is
                  scrolled into view before the action is executed.
    if_visible: wraps action in the 'if visible' conditional form.
    """
    # Suffix appended when if_visible + wait requested
    wait_sfx = f" wait {int(wait_secs) if wait_secs == int(wait_secs) else wait_secs} seconds" \
               if (if_visible and wait_secs and wait_secs > 0) else ""
    vis = " if visible" if if_visible else ""

    # Index suffix embedded in element name for locator disambiguation
    # e.g. "login_btn[last]" — the runner uses it as a hint but actual
    # locator lookup uses the base name; runner_appium also resolves by index.
    idx_suffix = "" if (not el_index or el_index == "any") else f"[{el_index}]"

    # For actions that use element name, append index suffix
    def _n(base): return f"{base}{idx_suffix}" if idx_suffix else base

    # scroll_first prefix: emit 'scroll to <name>' BEFORE the action step
    # We return a special sentinel so do_record can split it into two steps.
    # To keep _build_step returning a single string we encode scroll as prefix.
    scroll_pfx = f"scroll to {name}\n" if scroll_first and name else ""

    if action == "tap":
        return f"{scroll_pfx}tap{vis} {_n(name)}{wait_sfx}"
    elif action == "type":
        var = extra or "input_text"
        if if_visible:
            return f'{scroll_pfx}type if visible "{{{var}}}" into {_n(name)}{wait_sfx}'
        return f'{scroll_pfx}type "${{{var}}}" into {_n(name)}'
    elif action == "verify exists":
        if if_visible:
            return f"{scroll_pfx}verify if visible {_n(name)}{wait_sfx}"
        return f"{scroll_pfx}verify element exists {_n(name)}"
    elif action == "verify text":
        text = extra or name
        return f'verify text "{text}"'
    elif action == "double tap":
        return f"{scroll_pfx}double tap{vis} {_n(name)}{wait_sfx}"
    elif action == "long press":
        return f"{scroll_pfx}long press{vis} {_n(name)}{wait_sfx}"
    elif action == "scroll down":
        return "scroll down"
    elif action == "scroll up":
        return "scroll up"
    elif action == "swipe left":
        return "swipe left"
    elif action == "swipe right":
        return "swipe right"
    elif action == "wait for element":
        return f"wait for element {_n(name)}"
    elif action == "scroll to":
        return f"scroll to {name}"
    elif action == "store text":
        var = extra or f"{name}_text"
        if if_visible:
            return f"{scroll_pfx}store text if visible from {_n(name)} as {var}{wait_sfx}"
        return f"{scroll_pfx}store text from {_n(name)} as {var}"
    elif action == "screenshot":
        label = extra or name
        return f"take screenshot as {label}"
    elif action == "wait":
        secs = extra or "1"
        return f"wait {secs} seconds"
    elif action == "press back":
        return "press back"
    elif action == "press enter":
        return "press enter"
    elif action == "dismiss alerts":
        return "dismiss alerts"
    elif action == "go to url":
        return f"go to url {extra or 'https://example.com'}"
    # ── Fake data ──────────────────────────────────────────────────────────
    elif action in ("fake name", "fake email", "fake phone", "fake uuid",
                    "fake number", "fake address", "fake company",
                    "fake username", "fake password"):
        kind = action.replace("fake ", "")
        var  = extra or f"{kind.replace(' ', '_')}_val"
        return f"generate fake {kind} as {var}"
    elif action == "random number":
        parts = (extra or "1 1000").split()
        mn = parts[0] if len(parts) > 0 else "1"
        mx = parts[1] if len(parts) > 1 else "1000"
        var = name or "rand_num"
        return f"generate random number {mn} {mx} as {var}"
    elif action == "random string":
        length = extra or "8"
        var = name or "rand_str"
        return f"generate random string {length} as {var}"
    # ── Date / Time ──────────────────────────────────────────────────────────
    elif action == "get today":
        return f"get today as {extra or 'today_date'}"
    elif action == "get timestamp":
        return f"get timestamp as {extra or 'ts'}"
    elif action == "get date offset":
        offset = extra or "+1"
        var = name or "offset_date"
        return f"get date {offset} days as {var}"
    # ── HTTP / API ───────────────────────────────────────────────────────────
    elif action == "api get":
        url = extra or "https://api.example.com/endpoint"
        var = name or "api_response"
        return f'api get "{url}" as {var}'
    elif action == "api post":
        url  = extra or "https://api.example.com/endpoint"
        body = '{"key": "value"}'
        var  = name or "api_response"
        return f"api post \"{url}\" with body '{body}' as {var}"
    elif action == "store json path":
        src  = extra or "api_response"
        path = name or "data.0.id"
        var  = extra or "extracted_val"
        return f"store json {src} path {path} as {var}"
    # ── Excel / CSV ──────────────────────────────────────────────────────────
    elif action == "read excel cell":
        file = extra or "data/test_data.xlsx"
        var  = name or "cell_val"
        return f'read excel "{file}" row 1 col 1 as {var}'
    elif action == "read excel row":
        file = extra or "data/test_data.xlsx"
        var  = name or "row_data"
        return f'read excel "{file}" row 1 as {var}'
    elif action == "read csv cell":
        file = extra or "data/test_data.csv"
        var  = name or "csv_val"
        return f'read csv "{file}" row 1 col 1 as {var}'
    # ── JavaScript Actions (WebView / mobile-browser) ────────────────────────
    elif action == "js click":
        return f"js click {name}"
    elif action == "js scroll to":
        return f"js scroll to {name}"
    elif action == "js scroll down":
        return f"js scroll down {extra or '300'}"
    elif action == "js scroll up":
        return f"js scroll up {extra or '300'}"
    elif action == "js scroll top":
        return "js scroll top"
    elif action == "js scroll bottom":
        return "js scroll bottom"
    elif action == "js type":
        return f'js type "{extra or "text"}" into {name}'
    elif action == "js focus":
        return f"js focus {name}"
    elif action == "js submit":
        return f"js submit {name}"
    elif action == "js dispatch":
        return f"js dispatch {extra or 'change'} on {name}"
    return None


def _resolve_element_by_index(driver, locator: dict, platform: str, el_index: str):
    """
    Find the Nth occurrence of an element matching the locator.
    el_index: "any"/"1st"/"2nd"/"last"/"last-2" etc.
    Returns a single WebElement.
    """
    from appium.webdriver.common.appiumby import AppiumBy
    if not el_index or el_index == "any":
        return _find_live_element(driver, locator, platform)

    # Build a list of all matching elements via xpath (most reliable multi-match strategy)
    xpath = locator.get("xpath")
    if not xpath:
        return _find_live_element(driver, locator, platform)
    try:
        els = driver.find_elements(AppiumBy.XPATH, xpath)
    except Exception:
        return _find_live_element(driver, locator, platform)
    if not els:
        raise RuntimeError(f"No elements found for xpath: {xpath}")

    idx_map = {
        "1st": 0, "2nd": 1, "3rd": 2, "4th": 3, "5th": 4,
        "last": -1, "last-2": -2, "last-3": -3, "last-4": -4,
    }
    idx = idx_map.get(el_index, 0)
    try:
        return els[idx]
    except IndexError:
        raise RuntimeError(
            f"Index '{el_index}' out of range — found {len(els)} elements for {xpath}"
        )


def _execute_step_on_device(el, action: str, extra: str, name: str,
                             scroll_first: bool = False, el_index: str = "any"):
    driver = _state["driver"]
    platform = _state["platform"]
    if not driver:
        return
    try:
        # Scroll element into view first if requested
        if scroll_first and el:
            try:
                import execution.appium_action_service as _svc
                _svc.scroll_to_element(driver, name, platform)
                time.sleep(0.3)
            except Exception as se:
                logger.warning("scroll_first failed: %s", se)

        if action in ("tap","double tap","long press","verify exists","wait for element","store text") and el:
            live = _resolve_element_by_index(driver, el["locator"], platform, el_index)
            if action == "tap":
                live.click()
            elif action == "double tap":
                try:
                    driver.execute_script("mobile: doubleTap", {"element": live.id})

                except Exception:
                    live.click(); time.sleep(0.1); live.click()
            elif action == "long press":
                try:
                    driver.execute_script("mobile: touchAndHold", {"element": live.id, "duration": 1.5})
                except Exception:
                    pass
            elif action == "verify exists":
                pass  # just finding it counts as verify
            elif action == "store text":
                txt = live.text or live.get_attribute("label") or ""
                logger.info("Stored text: %s", txt)
        elif action == "type" and el:
            live = _resolve_element_by_index(driver, el["locator"], platform, el_index)
            live.click()
            try: live.clear()
            except: pass
            live.send_keys(extra or "")
        elif action == "scroll to":
            try:
                import execution.appium_action_service as _svc
                _svc.scroll_to_element(driver, name, platform)
            except Exception as se:
                logger.warning("scroll to failed: %s", se)
        elif action == "scroll down":
            size = driver.get_window_size()
            driver.swipe(size["width"]//2, int(size["height"]*0.7),
                         size["width"]//2, int(size["height"]*0.3), 400)
        elif action == "scroll up":
            size = driver.get_window_size()
            driver.swipe(size["width"]//2, int(size["height"]*0.3),
                         size["width"]//2, int(size["height"]*0.7), 400)
        elif action == "swipe left":
            size = driver.get_window_size()
            driver.swipe(int(size["width"]*0.8), size["height"]//2,
                         int(size["width"]*0.2), size["height"]//2, 400)
        elif action == "swipe right":
            size = driver.get_window_size()
            driver.swipe(int(size["width"]*0.2), size["height"]//2,
                         int(size["width"]*0.8), size["height"]//2, 400)
        elif action == "press back":
            driver.back()
        elif action == "press enter":
            try: driver.execute_script("mobile: pressKey", {"keycode": 66})
            except: pass
        elif action == "dismiss alerts":
            # Try full popup sweep, not just native alert
            _dismiss_popups(driver, platform)
        elif action == "wait":
            try: time.sleep(float(extra or 1))
            except: pass
    except Exception as e:
        logger.warning("Device action failed (%s): %s", action, e)


# ─── CLI entry ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Visual Appium Element Recorder")
    parser.add_argument("--platform","-p", choices=["android","ios"], default="android")
    parser.add_argument("--caps","-c", default=None,
                        help="Suite JSON or raw capabilities JSON file")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()

    _state["platform"] = args.platform

    # Load caps
    if args.caps and os.path.exists(args.caps):
        with open(args.caps) as f:
            data = json.load(f)
        # Support suite JSON (has desired_capabilities key) or raw caps
        _state["caps"] = data.get("desired_capabilities", data)
        logger.info("Loaded caps from: %s", args.caps)
    else:
        sys.path.insert(0, BASE_DIR)
        from config import settings
        _state["caps"] = dict(
            settings.ANDROID_CAPABILITIES if args.platform == "android"
            else settings.IOS_CAPABILITIES
        )

    @ui.page("/")
    def index():
        build_ui()

    print(f"""
╔══════════════════════════════════════════════════╗
║   📱  Appium Visual Recorder                    ║
║   Platform : {args.platform.upper():<36}║
║   Open in browser → http://localhost:{args.port}    ║
╚══════════════════════════════════════════════════╝
""")

    # ── Single-instance lockfile ──────────────────────────────────────────────
    lockfile = os.path.join(BASE_DIR, ".recorder_ui.lock")

    def _cleanup_lock(*_):
        try: os.remove(lockfile)
        except: pass

    # Check if another instance is already running
    if os.path.exists(lockfile):
        try:
            old_pid = int(open(lockfile).read().strip())
            # Check if that PID is actually alive
            os.kill(old_pid, 0)          # raises if dead
            print(f"\n⚠️  Recorder is already running (PID {old_pid}) at http://localhost:{args.port}")
            print(f"   Open http://localhost:{args.port} in your browser.")
            print(f"   To force-restart: kill {old_pid} && python recorder_ui.py ...\n")
            sys.exit(0)
        except (OSError, ValueError):
            # Stale lockfile — previous process died without cleanup
            os.remove(lockfile)

    # Write our own PID
    with open(lockfile, "w") as lf:
        lf.write(str(os.getpid()))

    # Register cleanup on normal exit and signals
    import atexit
    atexit.register(_cleanup_lock)
    signal.signal(signal.SIGTERM, _cleanup_lock)
    signal.signal(signal.SIGINT,  _cleanup_lock)

    # show=False: do NOT auto-open a browser tab on every restart
    ui.run(host="0.0.0.0", port=args.port, title="Appium Recorder",
           dark=True, reload=False, show=False)


if __name__ == "__main__":
    main()
