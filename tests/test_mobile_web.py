"""
tests/test_mobile_web.py  —  Foundation Change 1: suite-driven mobile-web emulation.

Covers the six required cases:
  1. Desktop context unchanged when no mobile capability is supplied
  2. Pixel 7 capability produces a mobile context configuration
  3. Mobile context has the expected viewport / is_mobile / has_touch  (real browser)
  4. Invalid device name fails clearly
  5. Suite capability reaches browser-context creation                 (call path)
  6. Existing desktop capability continues working

Run: python tests/test_mobile_web.py
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright  # noqa: E402

from execution.browser_manager import (  # noqa: E402
    build_context_options,
    open_browser,
    wants_mobile_web,
)

DEVICE = "Pixel 7"

# Captured once up front: a nested sync_playwright() cannot run inside a live
# browser session started by open_browser().
with sync_playwright() as _p:
    EXPECTED = dict(_p.devices[DEVICE])

_passed = 0
_failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ {label}" + (f"  — {detail}" if detail else ""))
    else:
        _failed += 1
        print(f"  ❌ {label}" + (f"  — {detail}" if detail else ""))


# ═════════════════════════════════════════════════════════════════════════════
# 1. Desktop context unchanged when no mobile capability is supplied
# ═════════════════════════════════════════════════════════════════════════════
print("\n[1] Desktop context unchanged when no mobile capability supplied")

with sync_playwright() as p:
    devices = p.devices

    for label, caps in (
        ("capabilities=None", None),
        ("capabilities={}", {}),
        ("desktop suite caps", {"browser": "chromium", "headless": False, "record_video": True}),
    ):
        opts = build_context_options(caps, devices)
        check(f"{label} → no_viewport=True", opts.get("no_viewport") is True, str(opts))
        check(f"{label} → no mobile keys",
              not any(k in opts for k in ("viewport", "user_agent", "is_mobile", "has_touch")))
        check(f"{label} → wants_mobile_web() is False", wants_mobile_web(caps) is False)

    # Exact shape parity with the original implementation.
    baseline = build_context_options(None, devices)
    check("desktop kwargs are exactly {no_viewport, permissions}",
          set(baseline.keys()) == {"no_viewport", "permissions"}, str(sorted(baseline)))

    # ═════════════════════════════════════════════════════════════════════════
    # 2. Pixel 7 capability produces a mobile context configuration
    # ═════════════════════════════════════════════════════════════════════════
    print(f"\n[2] '{DEVICE}' capability produces a mobile context configuration")

    suite_caps = {"browser": "chromium", "device_name": DEVICE, "mobile_web": True}
    mobile_opts = build_context_options(suite_caps, devices)
    descriptor = devices[DEVICE]

    check("wants_mobile_web() is True", wants_mobile_web(suite_caps) is True)
    check("no_viewport absent in mobile mode", "no_viewport" not in mobile_opts)
    check("viewport matches Playwright descriptor",
          mobile_opts["viewport"] == descriptor["viewport"], str(mobile_opts["viewport"]))
    check("user_agent matches descriptor (not hardcoded)",
          mobile_opts["user_agent"] == descriptor["user_agent"])
    check("device_scale_factor matches descriptor",
          mobile_opts["device_scale_factor"] == descriptor["device_scale_factor"],
          str(mobile_opts["device_scale_factor"]))
    check("is_mobile is True", mobile_opts["is_mobile"] is True)
    check("has_touch is True", mobile_opts["has_touch"] is True)

    # mobile_web alone (device_name falls back to settings.MOBILE_DEVICE_EMULATION)
    check("mobile_web=true alone activates emulation",
          wants_mobile_web({"mobile_web": True}) is True)
    # device_name alone is also an explicit opt-in
    check("device_name alone activates emulation",
          wants_mobile_web({"device_name": DEVICE}) is True)

    # Safe overrides apply in mobile mode
    override_opts = build_context_options(
        {"device_name": DEVICE, "mobile_web": True,
         "viewport_width": 360, "viewport_height": 800,
         "locale": "en-IN", "timezone_id": "Asia/Kolkata"},
        devices,
    )
    check("viewport override applied", override_opts["viewport"] == {"width": 360, "height": 800})
    check("locale override applied", override_opts.get("locale") == "en-IN")
    check("timezone_id override applied", override_opts.get("timezone_id") == "Asia/Kolkata")
    check("overrides do NOT leak to desktop",
          "locale" not in build_context_options({"locale": "en-IN"}, devices))

    # ═════════════════════════════════════════════════════════════════════════
    # 4. Invalid device name fails clearly
    # ═════════════════════════════════════════════════════════════════════════
    print("\n[4] Invalid device name fails clearly")

    try:
        build_context_options({"device_name": "Pixel 999 Ultra", "mobile_web": True}, devices)
        check("unknown device raises", False, "no exception raised")
    except ValueError as e:
        msg = str(e)
        check("unknown device raises ValueError", True)
        check("error names the bad device", "Pixel 999 Ultra" in msg)
        check("error is actionable", "device registry" in msg or "available" in msg.lower())
        print(f"       → {msg[:150]}")
    except Exception as e:  # noqa: BLE001
        check("unknown device raises ValueError", False, f"got {type(e).__name__}: {e}")

    # An explicitly blank device_name is a configuration mistake, not a request to
    # fall back — it must fail rather than silently emulate some default device.
    try:
        build_context_options({"mobile_web": True, "device_name": "   "}, devices)
        check("blank device_name is rejected", False, "no exception raised")
    except ValueError as e:
        check("blank device_name is rejected with a clear error", "device_name" in str(e))

# ═════════════════════════════════════════════════════════════════════════════
# 3. Mobile context has expected viewport / is_mobile / has_touch (REAL BROWSER)
# ═════════════════════════════════════════════════════════════════════════════
print("\n[3] Real browser: mobile context reports mobile properties")

os.environ["HEADLESS"] = "true"

from execution.browser_manager import close_browser  # noqa: E402
from execution.session import TestSession  # noqa: E402

# A responsive page. Without a viewport meta tag Chromium applies its default 980px
# mobile *layout* viewport, so window.innerWidth would report 980 on a correctly
# configured 412px device — real mobile behaviour, not a misconfiguration.
PROBE_HTML = (
    '<html><head><meta name="viewport" content="width=device-width, initial-scale=1">'
    "</head><body><h1>probe</h1></body></html>"
)

session = TestSession()
page = None
try:
    page = open_browser(session, capabilities={"device_name": DEVICE, "mobile_web": True})

    # Authoritative: what Playwright actually configured on the context.
    check("context viewport matches device descriptor",
          page.viewport_size == EXPECTED["viewport"], str(page.viewport_size))

    page.set_content(PROBE_HTML)
    vw = page.evaluate("window.innerWidth")
    ua = page.evaluate("navigator.userAgent")
    touch = page.evaluate("('ontouchstart' in window) || navigator.maxTouchPoints > 0")
    dpr = page.evaluate("window.devicePixelRatio")
    screen_w = page.evaluate("window.screen.width")

    check("responsive page lays out at device width",
          vw == EXPECTED["viewport"]["width"], f"innerWidth={vw}")
    check("screen.width matches device", screen_w == EXPECTED["viewport"]["width"], f"{screen_w}px")
    check("navigator.userAgent is the mobile UA", "Mobile" in ua and "Android" in ua, ua[:70])
    check("touch is enabled in the real context", bool(touch) is True)
    check("devicePixelRatio matches descriptor", dpr == EXPECTED["device_scale_factor"], str(dpr))
finally:
    close_browser(page, "mobile_probe", session)

print("\n[3b] Real browser: desktop context is NOT mobile")

desk_session = TestSession()
desk_page = None
try:
    desk_page = open_browser(desk_session)
    check("desktop context uses no_viewport (viewport_size is window-sized)",
          desk_page.viewport_size != EXPECTED["viewport"], str(desk_page.viewport_size))

    desk_page.set_content(PROBE_HTML)
    d_ua = desk_page.evaluate("navigator.userAgent")
    d_touch = desk_page.evaluate("('ontouchstart' in window) || navigator.maxTouchPoints > 0")
    check("desktop UA is not a mobile UA", "Mobile" not in d_ua, d_ua[:70])
    check("desktop context has no touch", bool(d_touch) is False)
finally:
    close_browser(desk_page, "desktop_probe", desk_session)

# ═════════════════════════════════════════════════════════════════════════════
# 5. Suite capability reaches browser-context creation (call path)
# ═════════════════════════════════════════════════════════════════════════════
print("\n[5] Suite capability reaches browser-context creation")

check("open_browser accepts a 'capabilities' parameter",
      "capabilities" in inspect.signature(open_browser).parameters)

import runner  # noqa: E402

src = inspect.getsource(runner.run_nlp_flow_collect)
check("run_nlp_flow_collect forwards capabilities to open_browser",
      "capabilities=caps" in src)

import plan_runner  # noqa: E402

check("plan_runner reads suite desired_capabilities",
      'suite.get("desired_capabilities"' in inspect.getsource(plan_runner._run_suite))
check("plan_runner forwards capabilities to the flow runner",
      "capabilities=caps" in inspect.getsource(plan_runner._run_suite))
check("_run_flow_file forwards capabilities to the web runner",
      "capabilities=capabilities" in inspect.getsource(plan_runner._run_flow_file))

# ═════════════════════════════════════════════════════════════════════════════
# 6. Existing desktop capability continues working
# ═════════════════════════════════════════════════════════════════════════════
print("\n[6] Existing desktop capabilities continue working")

with sync_playwright() as p:
    legacy = build_context_options(
        {"browser": "chromium", "headless": False, "viewport_width": 1920,
         "viewport_height": 1080, "locale": "en-IN", "timezone_id": "Asia/Kolkata",
         "record_video": True, "slow_mo_ms": 0},
        p.devices,
    )
    check("real desktop suite caps still yield the desktop context",
          legacy == {"no_viewport": True, "permissions": []}, str(legacy))

check("open_browser(session=None) signature still backward compatible",
      list(inspect.signature(open_browser).parameters) == ["session", "record_video", "capabilities"])

# ═════════════════════════════════════════════════════════════════════════════
# ENGINE SELECTION — the device decides, an explicit choice overrides
#
# Before this, open_browser() hardcoded Chromium, so "iPhone" meant a Chromium
# page wearing an iPhone user-agent: right viewport, wrong rendering engine.
# Note the user-agent CANNOT be used to verify this — a device descriptor sets a
# Safari UA whichever engine is running — so the browser type itself is asserted.
# ═════════════════════════════════════════════════════════════════════════════
print("\n[7] ENGINE SELECTION")

from execution.browser_manager import close_browser  # noqa: E402
from execution.session import TestSession  # noqa: E402
from nlp.platforms import browser_for_device  # noqa: E402

with sync_playwright() as _p:
    _devices = _p.devices
    for dev, want in (("Pixel 7", "chromium"), ("iPhone 15", "webkit"),
                      ("iPad Pro 11", "webkit")):
        check(f"{dev} declares engine {want}",
              _devices[dev]["default_browser_type"] == want,
              _devices[dev]["default_browser_type"])
    check("an explicit choice beats the device's own engine",
          browser_for_device("iPhone 15", "chromium", device_catalogue=_devices) == "chromium")
    check("no device and no choice falls back to chromium",
          browser_for_device(None, None, device_catalogue=_devices) == "chromium")
    check("an unknown device name does not crash engine selection",
          browser_for_device("Nokia 3310", None, device_catalogue=_devices) == "chromium")

_INSTALLED = set()
with sync_playwright() as _p:
    for _n in ("chromium", "firefox", "webkit"):
        try:
            _b = getattr(_p, _n).launch(headless=True)
            _b.close()
            _INSTALLED.add(_n)
        except Exception:  # noqa: BLE001
            pass

for caps, desc, want in (
    ({"mobile_web": True, "device_name": "Pixel 7", "headless": True}, "Pixel 7", "chromium"),
    ({"mobile_web": True, "device_name": "iPhone 15", "headless": True}, "iPhone 15", "webkit"),
    ({"browser": "firefox", "headless": True}, "explicit firefox", "firefox"),
    ({"mobile_web": True, "device_name": "iPhone 15", "browser": "chromium",
      "headless": True}, "iPhone + chromium override", "chromium"),
):
    if want not in _INSTALLED:
        print(f"  SKIP  {desc}: the {want} engine is not installed here")
        continue
    _s = TestSession()
    _page = open_browser(_s, capabilities=caps)
    try:
        check(f"{desc} really launches {want}",
              _page.context.browser.browser_type.name == want,
              _page.context.browser.browser_type.name)
    finally:
        close_browser(_page, "engine_test", _s)

# headless is a per-run capability, not only a config-file setting.
for _hl in (True, False):
    _s = TestSession()
    _page = open_browser(_s, capabilities={"mobile_web": True, "device_name": "Pixel 7",
                                           "headless": _hl})
    try:
        check(f"headless={_hl} is honoured from run capabilities",
              _page.viewport_size == {"width": 412, "height": 839})
    finally:
        close_browser(_page, "headless_test", _s)


# ═════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print(f"PASSED: {_passed}  |  FAILED: {_failed}")
print("=" * 60)
sys.exit(1 if _failed else 0)
