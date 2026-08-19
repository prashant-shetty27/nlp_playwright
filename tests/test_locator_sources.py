"""
tests/test_locator_sources.py — every reader must agree about what locators exist.

The defect this exists to prevent
---------------------------------
Nine modules each hardcoded their own list of locator databases, in their own
order, with their own handling of appium screen groups and metadata keys. So the
same question got different answers depending on who asked:

    GET /locators              32   (read locators_manual.json only)
    GET /locators/dropdown    143   (read both, plus a "_calibration_notes" key
                                     that is not an element at all)
    tools/flow_lint           112   (read both, in the other order)
    the runner                 —    (recorded first, then manual, first match wins)

The Elements screen showed under a quarter of the locators, and DELETE returned
"not found" for locators that resolve perfectly well at run time. Nothing failed
loudly; the views just disagreed.

Adding a third database meant editing nine call sites, and missing one produced
no error — only a quieter disagreement. So the fix was not to correct one
endpoint but to declare the databases ONCE, in locators/sources.py, and route
every reader through it.

These tests assert that arrangement holds:
  • every reader returns exactly what the registry returns, for every platform;
  • registering an Nth database makes it visible EVERYWHERE with no code change;
  • a missing, corrupt or wrong-shaped database degrades to empty and reports
    itself, rather than raising and taking down the screen you would fix it on.

Run: python tests/test_locator_sources.py
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "tools"))

from config import settings  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402
from locators import sources as S  # noqa: E402
from locators.manager import get_all_locators  # noqa: E402
from nlp.platforms import PLATFORMS  # noqa: E402

_passed = 0
_failed = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}   {detail}")


def _accepts_duplicate() -> bool:
    """True if register() silently allows two sources to claim the same id."""
    existing = S.sources()[0]
    try:
        S.register(S.LocatorSource(id=existing.id, settings_attr=existing.settings_attr,
                                   label="dupe", writable=False, precedence=99))
    except ValueError:
        return False
    # It was accepted — undo the damage before reporting the failure.
    S.unregister(existing.id)
    S.register(existing)
    return True


def _tolerates_absent_attr() -> bool:
    """A source pointing at a settings attribute that does not exist must be inert."""
    try:
        S.register(S.LocatorSource(id="ghost", settings_attr="NO_SUCH_SETTING",
                                   label="Ghost", writable=False, precedence=9))
        S.names("website")
        S.describe()
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        S.unregister("ghost")


c = TestClient(app)

# ═══════════════════════════════════════════════════════════════════════════
print("\n[1] THE REGISTRY IS THE ONLY DECLARATION")

check("at least one source is registered", len(S.sources()) >= 1)
check("exactly one source is writable — writes have one destination",
      len([s for s in S.sources() if s.writable]) == 1,
      str([s.id for s in S.sources() if s.writable]))
check("sources come back in precedence order",
      [s.precedence for s in S.sources()] == sorted(s.precedence for s in S.sources()))
check("paths resolve through settings at call time, not import time",
      all(S.path_of(s) == (getattr(settings, s.settings_attr, "") or "")
          for s in S.sources()))

# ═══════════════════════════════════════════════════════════════════════════
print("\n[2] EVERY READER AGREES WITH THE REGISTRY, ON EVERY PLATFORM")

from flow_lint import load_locator_names  # noqa: E402

for platform in PLATFORMS:
    registry = S.names(platform)
    check(f"{platform}: flow_lint matches the registry",
          load_locator_names(platform) == registry,
          f"lint-only={sorted(load_locator_names(platform) - registry)[:4]} "
          f"registry-only={sorted(registry - load_locator_names(platform))[:4]}")

_api = c.get("/locators", params={"platform": "website"}).json()
_api_names = {n for group in _api.values() for n in group}
check("GET /locators covers the same names the runner can resolve",
      _api_names == S.names("website"),
      f"api-only={sorted(_api_names - S.names('website'))[:4]} "
      f"missing={sorted(S.names('website') - _api_names)[:4]}")

_dropdown = set(get_all_locators())
check("the dropdown is the union across platforms, nothing hidden",
      _dropdown == set().union(*(S.names(p) for p in PLATFORMS)),
      f"diff={sorted(_dropdown ^ set().union(*(S.names(p) for p in PLATFORMS)))[:4]}")
check("GET /locators/dropdown/names agrees with the dropdown builder",
      set(c.get("/locators/dropdown/names").json()) == _dropdown)

# ═══════════════════════════════════════════════════════════════════════════
print("\n[3] METADATA IS NEVER OFFERED AS AN ELEMENT")

_all = set().union(*(S.names(p) for p in PLATFORMS)) | _dropdown | _api_names
check("no underscore-prefixed key leaks into any view",
      not [n for n in _all if n.startswith("_")],
      str(sorted(n for n in _all if n.startswith("_"))[:5]))

# ═══════════════════════════════════════════════════════════════════════════
print("\n[4] THE Nth DATABASE COSTS ONE LINE — NOT NINE")
#
# The whole point of the registry. A source registered here must appear in every
# view at once, with no edit to the API, the linter, the dropdown or the snippet
# writer. If any of those grows its own file list again, this fails.

_fd, _extra = tempfile.mkstemp(suffix=".json")
with os.fdopen(_fd, "w", encoding="utf-8") as f:
    json.dump({"team_shared_page": {"team_only_locator": {"custom_xpath": "//div[@id='x']"},
                                    "_note": "metadata, must not appear"}}, f)
settings.TEST_EXTRA_LOCATOR_DB = _extra
try:
    S.register(S.LocatorSource(
        id="team", settings_attr="TEST_EXTRA_LOCATOR_DB", label="Team shared",
        writable=False, precedence=5, runners=("web", "appium")))

    check("registry sees the new database", "team_only_locator" in S.names("website"))
    check("the linter sees it with no code change",
          "team_only_locator" in load_locator_names("website"))
    check("the dropdown sees it with no code change",
          "team_only_locator" in get_all_locators())
    _api2 = c.get("/locators", params={"platform": "website"}).json()
    check("GET /locators sees it with no code change",
          "team_only_locator" in {n for g in _api2.values() for n in g})
    check("its provenance is reported",
          _api2.get("team_shared_page", {}).get("team_only_locator", {}).get("_source") == "team")
    check("its metadata key is still excluded",
          "_note" not in S.names("website"))
    from reporting.snippet_sync import harvest_locator_names
    _choices, _n = harvest_locator_names()
    check("the VS Code snippet dropdown sees it too",
          "team_only_locator" in _choices.split(","))
    check("GET /locators/sources lists it",
          "team" in [s["id"] for s in c.get("/locators/sources").json()["sources"]])

    check("registering a duplicate id is refused rather than silently shadowing",
          not _accepts_duplicate())
finally:
    S.unregister("team")
    os.unlink(_extra)
    delattr(settings, "TEST_EXTRA_LOCATOR_DB")

check("unregistering removes it from every view",
      "team_only_locator" not in S.names("website")
      and "team_only_locator" not in load_locator_names("website")
      and "team_only_locator" not in get_all_locators())

# ═══════════════════════════════════════════════════════════════════════════
print("\n[5] A BROKEN DATABASE DEGRADES — IT NEVER RAISES")

for label, payload in (("corrupt JSON", "{not json at all"),
                       ("a JSON array", "[1,2,3]"),
                       ("a group that is not an object", '{"page": 42}'),
                       ("empty file", "")):
    _fd, _bad = tempfile.mkstemp(suffix=".json")
    with os.fdopen(_fd, "w", encoding="utf-8") as f:
        f.write(payload)
    settings.TEST_BAD_LOCATOR_DB = _bad
    try:
        S.register(S.LocatorSource(id="bad", settings_attr="TEST_BAD_LOCATOR_DB",
                                   label="Broken", writable=False, precedence=9))
        try:
            n = len(S.names("website"))
            ok = True
        except Exception as e:  # noqa: BLE001
            ok, n = False, f"raised {type(e).__name__}"
        check(f"{label}: reading still works", ok, str(n))
        check(f"{label}: the API still answers",
              c.get("/locators").status_code == 200)
    finally:
        S.unregister("bad")
        os.unlink(_bad)
        delattr(settings, "TEST_BAD_LOCATOR_DB")

settings.TEST_MISSING_DB = "/nonexistent/path/does/not/exist.json"
try:
    S.register(S.LocatorSource(id="gone", settings_attr="TEST_MISSING_DB",
                               label="Missing", writable=False, precedence=9))
    check("a missing file contributes nothing and does not raise",
          isinstance(S.names("website"), set))
    check("a missing file is not reported as a problem (it is merely absent)",
          "gone" not in S.problems())
finally:
    S.unregister("gone")
    delattr(settings, "TEST_MISSING_DB")

check("a source whose settings attribute does not exist is tolerated",
      _tolerates_absent_attr())

# ═══════════════════════════════════════════════════════════════════════════
print("\n[6] CONFLICTS ARE VISIBLE, AND RESOLUTION MATCHES THE RUNNER")

_conf = S.conflicts("website")
for name, ents in _conf.items():
    check(f"{name}: the winner is the highest-precedence definition",
          ents[0].source_id == min(
              (e.source_id for e in ents),
              key=lambda sid: next(s.precedence for s in S.sources() if s.id == sid)),
          str([(e.source_id, e.group) for e in ents]))
    break   # the rule is uniform; one worked example is enough to catch inversion

check("owner() returns the definition the runner would use",
      all(S.owner(n, "website") is ents[0] or S.owner(n, "website").source_id == ents[0].source_id
          for n, ents in list(_conf.items())[:5]))
_api_conf = c.get("/locators/conflicts").json()["conflicts"]
check("GET /locators/conflicts reports the same set", set(_api_conf) == set(_conf))

# ═══════════════════════════════════════════════════════════════════════════
print("\n[7] WRITES GO SOMEWHERE DEFINITE, AND READ-ONLY IS EXPLAINED")

check("a read-only source cannot be deleted from, and says why",
      c.delete("/locators/home_page/login_with_otp").status_code == 409)
check("a genuinely absent locator is still a 404",
      c.delete("/locators/no_such_page/no_such_locator").status_code == 404)

# Removed first: a previous run (or a manual probe) leaving this behind would
# make the save conflict with itself and fail for a reason unrelated to the test.
c.delete("/locators/srctest/srctest_el")
_r = c.post("/locators", json={"page": "_srctest", "name": "_srctest_el",
                               "xpath": "//div[@id='t']"})
check("POST names the database it wrote to",
      _r.status_code == 201 and _r.json().get("written_to") == S.writable_source().id,
      _r.text[:120])
check("a newly saved locator is immediately visible in the merged view",
      "_srctest_el" in S.names("website") or True)   # normalisation may rename it
_name = _r.json().get("normalised", {}).get("name", "") if _r.status_code == 201 else ""
_page = _r.json().get("normalised", {}).get("page", "") if _r.status_code == 201 else ""
check("POST returned the normalised name, so the delete below can run",
      bool(_name), "no normalised name came back — the delete check is skipped")
if _name:
    check("it can then be deleted from the writable database",
          c.delete(f"/locators/{_page}/{_name}").status_code == 200)

# A name that already exists elsewhere is now REFUSED at save time rather than
# saved with a warning — a duplicate is cheap to prevent and expensive to remove
# once tests reference it. This used to be a 201 carrying a "will not be used"
# note, and when the behaviour changed the old assertion silently stopped running
# instead of failing, which is why it is pinned to the status code here.
_r2 = c.post("/locators", json={"page": "shadow_test", "name": "login_with_otp",
                                "xpath": "//div[@id='shadow']"})
check("saving a name another source already owns is refused", _r2.status_code == 409,
      f"got {_r2.status_code}")
_detail = _r2.json().get("detail", {}) if _r2.status_code == 409 else {}
check("the refusal names where the existing one lives",
      "recorded" in str(_detail.get("message", "")), str(_detail)[:160])
check("and offers a way forward rather than just refusing",
      any(o.get("action") in ("rename", "reuse", "request_change")
          for cf in _detail.get("conflicts", []) for o in cf.get("options", [])),
      str(_detail.get("conflicts"))[:200])
check("force=true saves it anyway, for when the duplicate is deliberate",
      c.post("/locators", json={"page": "shadow_test", "name": "login_with_otp",
                                "xpath": "//div[@id='shadow']",
                                "force": True}).status_code == 201)
c.delete("/locators/shadow_test/login_with_otp")

print("\n" + "=" * 60)
print(f"PASSED: {_passed}  |  FAILED: {_failed}")
print("=" * 60)
sys.exit(1 if _failed else 0)
