"""
tests/test_afp_commands.py — focused tests for the four commands added for the
Ask More Photos source testcases (TC_AFP_C01/C02/C03).

Scope is deliberately narrow: these commands only. Existing fill/verify/wait
behaviour is covered by regression assertions at the end, which must not change.

Run: python tests/test_afp_commands.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nlp.parser import parse_step  # noqa: E402

_passed = _failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✅ {label}" + (f"  — {detail}" if detail else ""))
    else:
        _failed += 1
        print(f"  ❌ {label}" + (f"  — {detail}" if detail else ""))


# ═══════════════════════════════════════════════════════════════════════════
print("\n[1] PARSER — new commands")

for step, want, extra in [
    ("verify element mobile_number_input is visible", "verify_element_visible",
     {"target": "mobile_number_input"}),
    ("wait until element otp_input is visible", "wait_until_visible",
     {"target": "otp_input"}),
    ('enter otp "123456" into otp_input', "enter_otp",
     {"target": "otp_input", "text": "123456"}),
    ('verify stored updated_cta_text is not "Ask More Photos"', "verify_var_not_equals",
     {"target": "updated_cta_text", "text": "Ask More Photos"}),
    ('verify variable x is not "y"', "verify_var_not_equals", {"target": "x", "text": "y"}),
    ('enter otp "${jd_test_static_otp}" in otp_input', "enter_otp",
     {"target": "otp_input", "text": "${jd_test_static_otp}"}),
    # Negative assertions — the two wordings mean different things and must not
    # collapse into one another.
    ("verify element success_toaster is not visible", "verify_element_not_visible",
     {"target": "success_toaster"}),
    ("verify element popup_text_area is not present", "verify_element_not_exists",
     {"target": "popup_text_area"}),
    ("assert element x is not visible", "verify_element_not_visible", {"target": "x"}),
]:
    cmd = parse_step(step)
    check(f"{step[:52]} → {want}", cmd.type == want, f"got {cmd.type}")
    for k, v in extra.items():
        check(f"    .{k} == {v!r}", getattr(cmd, k) == v, f"got {getattr(cmd, k)!r}")

# ═══════════════════════════════════════════════════════════════════════════
print("\n[2] PARSER — existing rules must be unchanged (no shadowing)")

for step, want in [
    ("wait for element first_result", "wait_for_element"),
    ("wait until element first_result", "wait_for_element"),
    ("wait until first_result visible", "wait_for_element"),
    ("verify element exists first_result", "verify_element_exists"),
    ("verify element not exists first_result", "verify_element_not_exists"),
    ('verify element search_box has text "hi"', "verify_element_exact"),
    ('verify element search_box contains "hi"', "verify_element_contains"),
    ('verify stored total contains "15"', "verify_var_contains"),
    ('type "abc" into search_box', "fill"),
    ('fill "abc" in search_box', "fill"),
    ("wait 3 seconds", "wait"),
]:
    check(f"{step[:52]} → {want}", parse_step(step).type == want,
          f"got {parse_step(step).type}")

# ═══════════════════════════════════════════════════════════════════════════
print("\n[3] RUNNER DISPATCH + CATALOGUE")

from ai_flow_builder.catalogue import load  # noqa: E402

load.cache_clear()
cat = load("web")
for c in ("verify_element_visible", "wait_until_visible", "enter_otp", "verify_var_not_equals",
          "verify_element_not_visible", "verify_element_not_exists"):
    check(f"web runner dispatches {c}", cat.supports(c), cat.evidence_for(c))

import runner  # noqa: E402

check("verify_var_not_equals target kept as a NAME (not resolved to its value)",
      "verify_var_not_equals" in runner._VARIABLE_NAME_TARGETS)

# ═══════════════════════════════════════════════════════════════════════════
print("\n[4] LINTER argument classification")

from tools import flow_lint  # noqa: E402

for c in ("verify_element_visible", "wait_until_visible", "enter_otp"):
    check(f"{c} target treated as a locator", c in flow_lint._TARGET_IS_LOCATOR)
check("verify_var_not_equals target treated as a variable",
      "verify_var_not_equals" in flow_lint._TARGET_IS_VARIABLE)

# ═══════════════════════════════════════════════════════════════════════════
print("\n[5] ACTION — enter_otp fails safely, never leaks the value")

import logging  # noqa: E402

logging.disable(logging.CRITICAL)
import execution.action_service as svc  # noqa: E402
from nlp.variable_manager import RUNTIME_VARIABLES  # noqa: E402

SECRET = "537391"


class _FakeLoc:
    def __init__(self, n):
        self._n = n
        self.filled = []

    def count(self):
        return self._n

    def nth(self, i):
        return self

    def click(self):
        pass

    def fill(self, v):
        self.filled.append(v)


class _FakePage:
    def __init__(self, n):
        self.loc = _FakeLoc(n)

    def locator(self, _sel):
        return self.loc


_orig = svc._resolve_locator_or_raise
svc._resolve_locator_or_raise = lambda name, page=None: "#otpfield input.pin"

try:
    # happy path — 6 digits into 6 inputs
    page = _FakePage(6)
    svc.enter_otp(page, SECRET, "otp_input")
    check("6 digits distributed across 6 inputs", page.loc.filled == list(SECRET),
          f"{len(page.loc.filled)} inputs filled")

    # count mismatch must refuse rather than partially fill
    page = _FakePage(4)
    try:
        svc.enter_otp(page, SECRET, "otp_input")
        check("mismatched input count refuses", False, "no exception raised")
    except Exception as e:
        check("mismatched input count refuses", True)
        check("error names counts but NOT the OTP value", SECRET not in str(e), str(e)[:70])
        check("nothing was typed on refusal", page.loc.filled == [])

    # zero inputs
    page = _FakePage(0)
    try:
        svc.enter_otp(page, SECRET, "otp_input")
        check("zero inputs refuses", False)
    except Exception as e:
        check("zero inputs refuses", True)
        check("zero-input error omits the value", SECRET not in str(e))

    # non-numeric
    page = _FakePage(6)
    try:
        svc.enter_otp(page, "abc123", "otp_input")
        check("non-digit value refuses", False)
    except Exception as e:
        check("non-digit value refuses", True)
        check("non-digit error omits the value", "abc123" not in str(e), str(e)[:70])
finally:
    svc._resolve_locator_or_raise = _orig

# ═══════════════════════════════════════════════════════════════════════════
print("\n[6] ACTION — verify_stored_variable_not_equals")

RUNTIME_VARIABLES.clear()
RUNTIME_VARIABLES["updated_cta_text"] = "Request has been sent successfully!"
try:
    svc.verify_stored_variable_not_equals("updated_cta_text", "Ask More Photos")
    check("passes when the stored value differs", True)
except Exception as e:
    check("passes when the stored value differs", False, str(e)[:60])

RUNTIME_VARIABLES["updated_cta_text"] = "Ask More Photos"
try:
    svc.verify_stored_variable_not_equals("updated_cta_text", "Ask More Photos")
    check("fails when the stored value is unchanged", False, "no exception")
except Exception:
    check("fails when the stored value is unchanged", True)

try:
    svc.verify_stored_variable_not_equals("never_stored", "x")
    check("fails clearly when the variable was never stored", False)
except Exception as e:
    check("fails clearly when the variable was never stored", "not stored" in str(e))

# end-to-end through the runner, proving the name is not resolved to its value
RUNTIME_VARIABLES["updated_cta_text"] = "Request has been sent successfully!"
try:
    runner._execute_step_from_command(
        parse_step('verify stored updated_cta_text is not "Ask More Photos"'), None)
    check("dispatches end-to-end via runner (name not value-resolved)", True)
except Exception as e:
    check("dispatches end-to-end via runner (name not value-resolved)", False, str(e)[:70])

# ═══════════════════════════════════════════════════════════════════════════
print("\n[7] NEGATIVE ASSERTIONS — suggestions, registry surface, settle semantics")

import inspect  # noqa: E402

from config import settings  # noqa: E402
from nlp.keywords import KEYWORD_MAP  # noqa: E402
from registry import ACTION_REGISTRY  # noqa: E402

for entry, action in (("verify_element_not_visible", "verify_element_not_visible"),
                      ("verify_element_not_present", "verify_element_not_exists")):
    check(f"KEYWORD_MAP exposes {entry} to /nlp/suggest",
          entry in KEYWORD_MAP and KEYWORD_MAP[entry]["action"] == action,
          str(KEYWORD_MAP.get(entry)))
    check(f"    {entry} offers phrases", len(KEYWORD_MAP[entry]["phrases"]) >= 5)

for label in ("Verify Element Is Not Visible", "Verify Element Is Not Present"):
    check(f"codeless registry exposes {label!r}", label in ACTION_REGISTRY)
    check(f"    {label!r} takes page first (run_json_flow contract)",
          list(inspect.signature(ACTION_REGISTRY[label]).parameters)[0] == "page")

check("absence settle is configurable and non-zero", settings.ABSENCE_SETTLE_MS > 0,
      str(settings.ABSENCE_SETTLE_MS))


class _NLoc:
    def __init__(self, n, vis=True):
        self._n, self._vis = n, vis
        self.first = self

    def count(self):
        return self._n

    def is_visible(self):
        return self._vis


class _NPage:
    def __init__(self, n, vis=True):
        self._loc = _NLoc(n, vis)
        self.settled = 0

    def wait_for_timeout(self, ms):
        self.settled += ms

    def locator(self, _sel):
        return self._loc


_r2 = svc._resolve_locator_or_raise
svc._resolve_locator_or_raise = lambda name, page=None: "div.x"
try:
    pg = _NPage(0)
    svc.verify_element_not_exists(pg, "gone")
    check("not_exists passes when nothing matches", True)
    check("not_exists settles BEFORE asserting (an instant check always passes)",
          pg.settled == settings.ABSENCE_SETTLE_MS, str(pg.settled))
    try:
        svc.verify_element_not_exists(_NPage(1), "still_there")
        check("not_exists fails when the element is present", False, "no exception")
    except Exception as e:
        check("not_exists fails when the element is present", "should be absent" in str(e))

    svc.verify_element_not_visible(_NPage(1, vis=False), "hidden")
    check("not_visible passes for a present-but-hidden element", True)
    svc.verify_element_not_visible(_NPage(0), "absent")
    check("not_visible passes for an absent element", True)
    try:
        svc.verify_element_not_visible(_NPage(1, vis=True), "shown")
        check("not_visible fails for a visible element", False, "no exception")
    except Exception as e:
        check("not_visible fails for a visible element", "is visible" in str(e))
    svc.verify_element_not_exists(_NPage(0), "gone", settle_ms=0)
    check("settle period is overridable per call", True)
finally:
    svc._resolve_locator_or_raise = _r2


print("\n" + "=" * 60)
print(f"PASSED: {_passed}  |  FAILED: {_failed}")
print("=" * 60)
sys.exit(1 if _failed else 0)
