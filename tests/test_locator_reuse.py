"""
tests/test_locator_reuse.py  —  reuse-first capture, operator referral, and the
recorded-alternate fallback rung.

Covers:
  1. find_existing() locates a saved locator by name, and by selector under a
     DIFFERENT name (the duplicate-locator check)
  2. looks_dynamic() flags generated/volatile selectors
  3. get_alternate_selectors() returns [] for single-selector entries — the
     guarantee that resolution is unchanged for everything captured earlier
  4. get_alternate_selectors() returns the runner-ups for multi-selector entries
  5. promote_selector() swaps custom_xpath and PRESERVES the displaced value
  6. _resolve_live() short-circuits (no page probe) when there are no alternates
  7. _resolve_live() falls through to an alternate when the primary misses
  8. _resolve_live() still returns the primary when nothing matches, so each
     caller's own recovery path (innerText retry / ML heal) still runs

No browser required — the page is a stub that counts selector matches.

Run: python tests/test_locator_reuse.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings  # noqa: E402

_passed = 0
_failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}   {detail}")


# ── A throwaway locator DB so the real one is never touched ──────────────────
FIXTURE = {
    "fixture_group": {
        "single_selector_locator": {          # pre-alternates shape
            "custom_xpath": "div.only-one",
            "selectors": [{"type": "css", "value": "div.only-one"}],
        },
        "multi_selector_locator": {
            "custom_xpath": "div.primary",
            "selectors": [
                {"type": "css", "value": "div.primary"},
                {"type": "css", "value": "div.backup", "weight": 2.3},
                {"type": "xpath", "value": "//div[@data-role='x']", "weight": 1.5},
            ],
        },
    }
}

_tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
json.dump(FIXTURE, _tmp)
_tmp.close()

_orig_manual = settings.MANUAL_LOCATORS_FILE
_orig_recorded = settings.RECORDED_ELEMENTS_FILE
settings.MANUAL_LOCATORS_FILE = _tmp.name
settings.RECORDED_ELEMENTS_FILE = _tmp.name + ".absent"

import execution.action_service as svc  # noqa: E402
from locators import manager  # noqa: E402
from locators.auto_capture import find_existing, looks_dynamic  # noqa: E402


class StubLocator:
    def __init__(self, n):
        self._n = n

    def count(self):
        return self._n


class StubPage:
    """Counts a match only for selectors listed in `present`."""

    def __init__(self, present):
        self.present = set(present)
        self.probed = []

    def locator(self, selector):
        self.probed.append(selector)
        return StubLocator(1 if selector in self.present else 0)


print("\n1-2. reuse lookup and dynamic detection")
hit = find_existing("multi_selector_locator")
check("find_existing finds a saved locator by name",
      hit["by_name"] and hit["by_name"]["selector"] == "div.primary", str(hit))
check("find_existing returns None for an unsaved name",
      find_existing("never_saved_anywhere")["by_name"] is None)
dup = find_existing("some_new_name", "div.primary")["by_selector"]
check("find_existing flags the same selector saved under another name",
      any(d["name"] == "multi_selector_locator" for d in dup), str(dup))
check("a selector already saved under its OWN name is not self-reported as a duplicate",
      find_existing("multi_selector_locator", "div.primary")["by_selector"] == [])

for sel, expected in [("div:nth-child(3)", True), ("//div[@id='a1b2c3d4e5f6']", True),
                      ("#item-1234567", True), ("div.photoreqwrap", False),
                      ("input[placeholder='Mobile Number']", False), ("h1", False)]:
    check(f"looks_dynamic({sel!r}) == {expected}", looks_dynamic(sel) is expected)

print("\n3-4. alternates")
check("single-selector entry yields NO alternates (resolution unchanged)",
      manager.get_alternate_selectors("single_selector_locator") == [],
      str(manager.get_alternate_selectors("single_selector_locator")))
alts = manager.get_alternate_selectors("multi_selector_locator")
check("multi-selector entry yields its runner-ups",
      [a["value"] for a in alts] == ["div.backup", "//div[@data-role='x']"], str(alts))
check("the resolved primary is never returned as its own alternate",
      all(a["value"] != "div.primary" for a in alts))
check("alternate carries its type", alts[1]["type"] == "xpath", str(alts[1]))

print("\n5. promotion")
ok = manager.promote_selector("multi_selector_locator", "div.backup", "css")
entry = json.load(open(_tmp.name))["fixture_group"]["multi_selector_locator"]
check("promote_selector reports success", ok is True)
check("custom_xpath now points at the winner", entry["custom_xpath"] == "div.backup",
      entry["custom_xpath"])
check("last_success records the winning type", entry["last_success"] == "css")
check("the displaced selector is preserved, not discarded",
      entry["_promoted_from"] == "div.primary"
      and any(s["value"] == "div.primary" for s in entry["selectors"]), str(entry["selectors"]))
check("promotion is stamped for auditability", bool(entry.get("_promoted_at")))

# restore the fixture for the resolution tests
json.dump(FIXTURE, open(_tmp.name, "w"))

print("\n6-8. _resolve_live")
page = StubPage(present=["div.only-one"])
sel, _dna = svc._resolve_live(page, "single_selector_locator")
check("no alternates -> primary returned", sel == "div.only-one", sel)
check("no alternates -> the page is never probed (zero added cost)",
      page.probed == [], str(page.probed))

page = StubPage(present=["div.primary"])
sel, _dna = svc._resolve_live(page, "multi_selector_locator")
check("primary matches -> primary used", sel == "div.primary", sel)
check("primary matches -> alternates are not probed",
      page.probed == ["div.primary"], str(page.probed))

json.dump(FIXTURE, open(_tmp.name, "w"))
page = StubPage(present=["div.backup"])
sel, _dna = svc._resolve_live(page, "multi_selector_locator")
check("primary misses -> first matching alternate is used", sel == "div.backup", sel)
promoted = json.load(open(_tmp.name))["fixture_group"]["multi_selector_locator"]
check("a winning alternate is promoted for next time",
      promoted["custom_xpath"] == "div.backup", promoted["custom_xpath"])

json.dump(FIXTURE, open(_tmp.name, "w"))
page = StubPage(present=["//div[@data-role='x']"])
sel, _dna = svc._resolve_live(page, "multi_selector_locator")
check("alternates are tried in recorded order until one matches",
      sel == "//div[@data-role='x']", sel)

json.dump(FIXTURE, open(_tmp.name, "w"))
page = StubPage(present=[])
sel, _dna = svc._resolve_live(page, "multi_selector_locator")
check("nothing matches -> primary returned so ML heal still runs on the real name",
      sel == "div.primary", sel)
check("nothing matches -> every alternate was actually attempted",
      page.probed == ["div.primary", "div.backup", "//div[@data-role='x']"], str(page.probed))
unchanged = json.load(open(_tmp.name))["fixture_group"]["multi_selector_locator"]
check("nothing matches -> no bogus promotion is recorded",
      "_promoted_from" not in unchanged, str(unchanged.get("_promoted_from")))

try:
    svc._resolve_live(StubPage([]), "locator_that_does_not_exist")
    check("unknown locator raises", False, "no exception")
except Exception as e:
    check("unknown locator still raises the original clear error",
          "not found in any page" in str(e), str(e))

# ── cleanup ──────────────────────────────────────────────────────────────────
settings.MANUAL_LOCATORS_FILE = _orig_manual
settings.RECORDED_ELEMENTS_FILE = _orig_recorded
os.unlink(_tmp.name)

print("\n" + "=" * 60)
print(f"PASSED: {_passed}  |  FAILED: {_failed}")
print("=" * 60)
sys.exit(1 if _failed else 0)
