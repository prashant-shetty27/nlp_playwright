"""
tests/test_otp_portal.py — fetching a live OTP into a runtime variable.

The requirement this implements: the run never stops and nobody is prompted. The
flow submits the mobile number, then reads the OTP from the QA portal into a
variable and carries on.

  click popup_submit_button
  fetch otp for "${jd_test_mobile}" as otp
  enter otp "${otp}" into otp_input

Offline by default — the portal is driven through a stub so this runs anywhere.
Set OTP_PORTAL_LIVE=1 to additionally hit the real portal.

Run: python tests/test_otp_portal.py
"""
import inspect
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Fixtures are DELIBERATELY fake. These tests assert that values never reach a
#: log or an API response — putting the live test mobile or the live static OTP
#: here would write the very secrets under test into source control, which is
#: exactly the leak the assertions exist to prevent.

_passed = _failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}   {detail}")


print("\n[1] LANGUAGE — parses, dispatches, and is discoverable")

from ai_flow_builder.catalogue import load as load_catalogue  # noqa: E402
from nlp.keywords import KEYWORD_MAP  # noqa: E402
from nlp.parser import parse_step  # noqa: E402

for step, var, after in (
    ('fetch otp for "5550001111" as otp', "otp", ""),
    ('fetch otp for "${mob}" as code', "code", ""),
    ('fetch otp for "${mob}" as otp after "${otp_before}"', "otp", "${otp_before}"),
):
    c = parse_step(step)
    check(f"{step[:46]} -> fetch_otp", c.type == "fetch_otp", c.type)
    check(f"    saves into {var!r}", c.variable_name == var, str(c.variable_name))
    check(f"    after={after!r}", (c.target or "") == after, str(c.target))

check("the mobile number is captured, not the variable name",
      parse_step('fetch otp for "5550001111" as otp').text == "5550001111")
check("`enter otp` still parses (not shadowed by the new rule)",
      parse_step('enter otp "123456" into otp_input').type == "enter_otp")

load_catalogue.cache_clear()
check("web runner dispatches fetch_otp", load_catalogue("website").supports("fetch_otp"))
check("KEYWORD_MAP offers it", "fetch_otp_from_portal" in KEYWORD_MAP)
check("its template parses to fetch_otp",
      parse_step(KEYWORD_MAP["fetch_otp_from_portal"]["template"]
                 .replace("{text}", "5550001111").replace("{variable}", "otp")).type == "fetch_otp")

from registry import ACTION_REGISTRY  # noqa: E402

check("codeless registry exposes it", "Fetch OTP From Portal" in ACTION_REGISTRY)
check("    and takes page first (run_json_flow contract)",
      list(inspect.signature(ACTION_REGISTRY["Fetch OTP From Portal"]).parameters)[0] == "page")

from tools import flow_lint  # noqa: E402

check("linter treats the save target as a variable, not a locator",
      "fetch_otp" in flow_lint._TARGET_IS_VARIABLE)

print("\n[2] CONFIGURATION — refuses clearly when unset")

from execution.otp_portal import OtpPortalError, _config, fetch_otp  # noqa: E402

saved = {k: os.environ.pop(k, None) for k in
         ("OTP_PORTAL_URL", "OTP_PORTAL_USERNAME", "OTP_PORTAL_PIN")}
try:
    _config()
    check("missing config raises", False, "no exception")
except OtpPortalError as e:
    check("missing config raises", True)
    check("    the message names every missing key",
          all(k in str(e) for k in saved), str(e)[:90])
    check("    and never prints a value", "0000" not in str(e))
finally:
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v

print("\n[3] PORTAL LOGIC — against a stub, no network")

os.environ.setdefault("OTP_PORTAL_URL", "http://stub/login")
os.environ.setdefault("OTP_PORTAL_USERNAME", "tester")
os.environ.setdefault("OTP_PORTAL_PIN", "0000")


class _Loc:
    def __init__(self, texts):
        self._texts = texts

    def count(self):
        return len(self._texts)

    @property
    def first(self):
        return self

    def inner_text(self):
        return self._texts[0] if self._texts else ""


class _Page:
    """Serves a scripted sequence of OTP values, one per fetch click."""

    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.clicks = 0
        self.filled = {}

    def goto(self, *_a, **_k):
        pass

    def wait_for_timeout(self, _ms):
        pass

    def fill(self, sel, val):
        self.filled[sel] = val

    def click(self, sel):
        if sel == "#fetchOtpBtn":
            self.clicks += 1

    def locator(self, sel):
        if sel in ("#pin", "#otpMobile"):
            return _Loc(["x"])
        if sel == "span.otp-highlight":
            i = min(self.clicks - 1, len(self.sequence) - 1) if self.clicks else 0
            val = self.sequence[i] if self.sequence else ""
            return _Loc([f"OTP {val} DO NOT SHARE"] if val else [])
        return _Loc([])


class _Ctx:
    def __init__(self, page):
        self._p = page

    def new_page(self):
        return self._p

    def close(self):
        pass


class _Browser:
    def __init__(self, page):
        self._p = page

    def new_context(self):
        return _Ctx(self._p)


pg = _Page(["111222"])
check("a plain fetch returns the displayed code",
      fetch_otp(_Browser(pg), "5550001111", timeout_s=5, poll_s=0, settle_ms=0) == "111222")
check("    the mobile number was entered", pg.filled.get("#otpMobile") == "5550001111")

pg = _Page(["  "])
try:
    fetch_otp(_Browser(pg), "5550001111", timeout_s=1, poll_s=0, settle_ms=0)
    check("no OTP displayed -> raises", False, "no exception")
except OtpPortalError as e:
    check("no OTP displayed -> raises", True)
    check("    the error explains the timeout", "within" in str(e), str(e)[:70])

pg = _Page(["111111", "111111", "222222"])
check("`after` polls until the code changes",
      fetch_otp(_Browser(pg), "5550001111", after="111111",
                timeout_s=10, poll_s=0, settle_ms=0) == "222222")

pg = _Page(["111111"])
try:
    fetch_otp(_Browser(pg), "5550001111", after="111111", timeout_s=1, poll_s=0, settle_ms=0)
    check("`after` with no new message -> raises", False, "no exception")
except OtpPortalError as e:
    check("`after` with no new message -> raises", True)
    check("    the error says the SMS likely was not sent",
          "not sent" in str(e), str(e)[:80])

for bad in ("12345", "", "not-a-number", "55500011111"):
    try:
        fetch_otp(_Browser(_Page(["1"])), bad, timeout_s=1, poll_s=0, settle_ms=0)
        check(f"mobile {bad!r} refused", False, "accepted")
    except OtpPortalError:
        check(f"mobile {bad!r} refused", True)

print("\n[4] THE OTP VALUE IS NEVER LOGGED")

import io  # noqa: E402

import execution.action_service as svc  # noqa: E402
from execution.otp_portal import logger as portal_logger  # noqa: E402

buf = io.StringIO()
h = logging.StreamHandler(buf)
for lg in (portal_logger, svc.logger):
    lg.handlers = [h]
    lg.propagate = False
    lg.setLevel(logging.DEBUG)


class _PageWithBrowser(_Page):
    @property
    def context(self):
        outer = self

        class _C:
            browser = _Browser(outer)
        return _C()


from nlp.variable_manager import RUNTIME_VARIABLES  # noqa: E402

svc.fetch_otp_from_portal(_PageWithBrowser(["333444"]), "5550001111", "otp_v")
out = buf.getvalue()
check("the OTP reaches the runtime variable", RUNTIME_VARIABLES.get("otp_v") == "333444")
check("the OTP value never appears in the log", "333444" not in out, out[:120])
check("the log still confirms a code arrived", "digits" in out or "OTP" in out, out[:120])
check("the portal PIN never appears in the log", "0000" not in out)

print("\n[5] LIVE PORTAL (set OTP_PORTAL_LIVE=1 to include)")

if os.getenv("OTP_PORTAL_LIVE") == "1":
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            code = fetch_otp(b, "5550001111", timeout_s=45)
            check("live portal returns a numeric OTP", code.isdigit() and 4 <= len(code) <= 8, code)
        finally:
            b.close()
else:
    print("  SKIP  live portal not requested")

print("\n" + "=" * 60)
print(f"PASSED: {_passed}  |  FAILED: {_failed}")
print("=" * 60)
sys.exit(1 if _failed else 0)
