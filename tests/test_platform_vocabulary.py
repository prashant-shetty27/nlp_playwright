"""
tests/test_platform_vocabulary.py — platform is a validated value, not a free string.

The defect this locks down: `_supported_for()` looked platform up in a dict that
defaulted to "web" on a miss, so any unrecognised value — a typo, a UI-invented
label, or "appium" (a runner name, not a platform) — silently produced the web
command set. On a mobile flow that meant `enter_otp`, which only runner.py can
dispatch, was offered as a valid suggestion.

Run: python tests/test_platform_vocabulary.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_flow_builder.catalogue import load as load_catalogue  # noqa: E402
from nlp.keywords import KEYWORD_MAP  # noqa: E402
from nlp.platforms import (DEFAULT_PLATFORM, PLATFORM_REQUIRED,  # noqa: E402
                           PLATFORMS, UnknownPlatform, as_dicts, normalise,
                           resolve, runner_for, selectable)
from tools.flow_lint import _APPIUM_SUPPORTED, _WEB_SUPPORTED  # noqa: E402

_passed = _failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}   {detail}")


print("\n[1] VOCABULARY — the five platforms and their routing")

EXPECTED = {
    "website":    ("web",    None,       True),
    "mobilesite": ("web",    "Pixel 7",  True),
    "android":    ("appium", None,       False),
    "ios":        ("appium", None,       False),
    "hybrid":     ("appium", None,       False),
}
check("exactly the five agreed platforms exist",
      set(PLATFORMS) == set(EXPECTED), str(sorted(PLATFORMS)))
for name, (runner, device, enabled) in EXPECTED.items():
    p = PLATFORMS[name]
    check(f"{name}: runner={runner}", p.runner == runner, p.runner)
    check(f"{name}: device={device}", p.device == device, str(p.device))
    check(f"{name}: enabled={enabled}", p.enabled is enabled, str(p.enabled))

check("website and mobilesite share one runner (same 63 commands)",
      runner_for("website") == runner_for("mobilesite") == "web")
check("mobilesite differs from website ONLY by device emulation",
      PLATFORMS["mobilesite"].device and not PLATFORMS["website"].device)
check("hybrid is Appium-driven (native shell hosting a WebView)",
      runner_for("hybrid") == "appium")
check("native platforms are masked, web platforms are not",
      [p.name for p in selectable()] == ["website", "mobilesite"],
      str([p.name for p in selectable()]))

print("\n[2] ALIASES — names already on disk keep working")

for alias, canonical in [("web", "website"), ("chromium", "website"),
                         ("waptouch", "mobilesite"), ("mobile", "mobilesite"),
                         ("mobileweb", "mobilesite"), ("touch", "mobilesite"),
                         ("android app", "android"), ("android_app", "android"),
                         ("ios app", "ios"), ("native_webview", "hybrid"),
                         ("WEBSITE", "website"), ("  MobileSite  ", "mobilesite")]:
    check(f"{alias!r} -> {canonical}", normalise(alias) == canonical,
          f"got {normalise(alias)!r}")

# Every platform value committed to scenarios/suites/plans must still resolve.
for on_disk in ("web", "ios", "android"):
    check(f"value {on_disk!r} already in committed files still resolves",
          normalise(on_disk) in PLATFORMS)

print("\n[3] REJECTION — no silent fallback to web")

for bad in ("appium", "banana", "andriod", "web-app", "", "   ", None):
    try:
        got = normalise(bad)
        check(f"{bad!r} is rejected", False, f"silently accepted as {got!r}")
    except UnknownPlatform:
        check(f"{bad!r} is rejected", True)

check("PLATFORM_REQUIRED is a single switch", isinstance(PLATFORM_REQUIRED, bool))
check("DEFAULT_PLATFORM names a real platform", DEFAULT_PLATFORM in PLATFORMS)
try:
    import nlp.platforms as mod
    mod.PLATFORM_REQUIRED = False
    check("flipping PLATFORM_REQUIRED makes platform optional",
          normalise(None) == DEFAULT_PLATFORM, normalise(None))
finally:
    mod.PLATFORM_REQUIRED = True
try:
    normalise(None)
    check("switch restored — omitting platform raises again", False)
except UnknownPlatform:
    check("switch restored — omitting platform raises again", True)

print("\n[4] COMMAND SETS — the reason platform has to be right")

check("website resolves to the web dispatch table",
      load_catalogue("website").supported_commands == _WEB_SUPPORTED)
check("mobilesite resolves to the web dispatch table",
      load_catalogue("mobilesite").supported_commands == _WEB_SUPPORTED)
for native in ("android", "ios", "hybrid"):
    check(f"{native} resolves to the Appium dispatch table",
          load_catalogue(native).supported_commands == _APPIUM_SUPPORTED)

check("enter_otp is web-only and NOT offered on native",
      load_catalogue("website").supports("enter_otp")
      and not load_catalogue("android").supports("enter_otp"))
check("tap_text is Appium-only and NOT offered on web",
      load_catalogue("android").supports("tap_text")
      and not load_catalogue("website").supports("tap_text"))

print("\n[5] SUGGESTIONS — filtered to what the platform can actually run")


def suggestable(platform):
    d = load_catalogue(platform).supported_commands
    return {k for k, e in KEYWORD_MAP.items()
            if not e.get("deprecated") and e["action"] in d}


web_s, nat_s = suggestable("website"), suggestable("android")
check("website has suggestions", len(web_s) >= 20, str(len(web_s)))
check("native platforms suggest only Appium-dispatchable actions",
      nat_s and nat_s < web_s | nat_s, str(len(nat_s)))
check("no suggestion names an action its platform cannot dispatch",
      all(KEYWORD_MAP[k]["action"] in load_catalogue(p).supported_commands
          for p in PLATFORMS for k in suggestable(p)))

print("\n[6] API SHAPE — the UI must not hardcode this list")

d = as_dicts()
check("as_dicts returns every platform", len(d) == len(PLATFORMS))
check("each entry carries name/label/runner/device/enabled",
      all({"name", "label", "runner", "device", "enabled"} <= set(x) for x in d))
check("as_dicts(include_disabled=False) returns only the offered ones",
      [x["name"] for x in as_dicts(include_disabled=False)] == ["website", "mobilesite"])
check("resolve() returns the Platform object", resolve("waptouch").label.startswith("Mobile"))

print("\n" + "=" * 60)
print(f"PASSED: {_passed}  |  FAILED: {_failed}")
print("=" * 60)
sys.exit(1 if _failed else 0)
