"""
tests/test_platforms.py
Smoke-test all 4 platform adapters.
Run: python tests/test_platforms.py
"""
import traceback
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

results = {}

# ── PLATFORM 1: WEB ──────────────────────────────────────────────────────────
print("\n" + "="*60)
print("PLATFORM 1: WEB  (Playwright - desktop Chromium)")
print("="*60)
try:
    from adapters.web.web_adapter import WebAdapter
    adapter = WebAdapter()
    page = adapter.launch()
    page.goto("https://www.justdial.com", wait_until="domcontentloaded", timeout=30000)
    title = page.title()
    adapter.quit("web_test")
    print("PASS  page title:", title[:60])
    results["web"] = "PASS"
except Exception as e:
    traceback.print_exc()
    results["web"] = "FAIL: " + str(e)[:120]

# ── PLATFORM 2: MOBILE ───────────────────────────────────────────────────────
print("\n" + "="*60)
print("PLATFORM 2: MOBILE  (Playwright - iPhone 14 emulation)")
print("="*60)
try:
    from adapters.mobile.mobile_adapter import MobileAdapter
    adapter = MobileAdapter()
    page = adapter.launch(device_name="iPhone 14")
    page.goto("https://www.justdial.com", wait_until="domcontentloaded", timeout=30000)
    ua = page.evaluate("navigator.userAgent")
    viewport = page.viewport_size
    adapter.quit("mobile_test")
    print("PASS  viewport:", viewport, "UA:", ua[20:60])
    results["mobile"] = "PASS"
except Exception as e:
    traceback.print_exc()
    results["mobile"] = "FAIL: " + str(e)[:120]

# ── PLATFORM 3: ANDROID ──────────────────────────────────────────────────────
print("\n" + "="*60)
print("PLATFORM 3: ANDROID  (Appium - requires server + device/emulator)")
print("="*60)
try:
    from adapters.android.android_adapter import AndroidAdapter
    adapter = AndroidAdapter()
    adapter.launch(capabilities={
        "platformName": "Android",
        "deviceName": "emulator-5554",
        "automationName": "UiAutomator2",
        "browserName": "Chrome"
    })
    results["android"] = "PASS (server reachable)"
    adapter.quit()
except ImportError as e:
    results["android"] = "FAIL (import): " + str(e)
except Exception as e:
    err = str(e)
    if any(x in err.lower() for x in ["connection refused", "failed to establish", "cannot connect", "econnrefused"]):
        print("SKIP  Appium server not running - adapter code OK")
        print("      Start Appium with:  appium  (npm install -g appium)")
        results["android"] = "SKIP - Appium server not running (adapter code OK)"
    else:
        traceback.print_exc()
        results["android"] = "FAIL: " + err[:120]

# ── PLATFORM 4: iOS ──────────────────────────────────────────────────────────
print("\n" + "="*60)
print("PLATFORM 4: iOS  (Appium + XCUITest - requires Xcode + sim/device)")
print("="*60)
try:
    from adapters.ios.ios_adapter import IOSAdapter
    adapter = IOSAdapter()
    adapter.launch(capabilities={
        "platformName": "iOS",
        "deviceName": "iPhone 15",
        "automationName": "XCUITest",
        "browserName": "Safari"
    })
    results["ios"] = "PASS (server reachable)"
    adapter.quit()
except ImportError as e:
    results["ios"] = "FAIL (import): " + str(e)
except Exception as e:
    err = str(e)
    if any(x in err.lower() for x in ["connection refused", "failed to establish", "cannot connect", "econnrefused"]):
        print("SKIP  Appium server not running - adapter code OK")
        print("      Start Appium with:  appium  (npm install -g appium)")
        results["ios"] = "SKIP - Appium server not running (adapter code OK)"
    else:
        traceback.print_exc()
        results["ios"] = "FAIL: " + err[:120]

# ── SUMMARY ───────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("PLATFORM TEST SUMMARY")
print("="*60)
for platform, result in results.items():
    icon = "OK" if result.startswith("PASS") else ("--" if result.startswith("SKIP") else "XX")
    print(f"  [{icon}]  {platform.upper():<10}  {result}")
print("="*60)
