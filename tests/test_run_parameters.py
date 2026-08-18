"""
tests/test_run_parameters.py — values reach a run, and secrets do not reach the log.

Three defects this locks down, all found while wiring the UI's Run button:

  every API-run step failed.  _run_flow_sync() read `runner._VARIABLES`, an
      attribute that does not exist, so every step of every run launched over
      HTTP died with AttributeError before the step itself was attempted.

  no values could be supplied.  /tests/run accepted only {project, headless}, so
      a generated flow saying `open ${product_url}` had nothing to open. The UI
      could collect values and had nowhere to send them.

  parameter values were logged in plaintext.  plan_runner._inject_parameters
      printed every value, so a password, OTP or test mobile ended up in the log
      and in any report embedding it.

Run: python tests/test_run_parameters.py
"""
import io
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402
from api.routes.tests import _is_secret  # noqa: E402
from config.settings import FLOWS_DIR  # noqa: E402

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


c = TestClient(app)
PROBE = os.path.join(FLOWS_DIR, "_paramtest.flow")
with open(PROBE, "w", encoding="utf-8") as f:
    f.write('# parameter probe\ncreate variable seen with value "${probe_url}"\n'
            'verify stored seen contains "example.com"\n')


def run(payload, timeout_s=45):
    r = c.post("/tests/run", json=payload)
    if r.status_code != 200:
        return {"http": r.status_code, "detail": r.json().get("detail")}
    rid = r.json()["run_id"]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        j = c.get(f"/tests/results/{rid}").json()
        if "passed" in j:
            j["http"] = 200
            j["launch"] = r.json()
            return j
        time.sleep(0.4)
    return {"http": 200, "timeout": True}


try:
    print("\n[1] A RUN CAN EXECUTE AT ALL")

    j = run({"project": "_paramtest", "headless": True, "platform": "website",
             "parameters": {"probe_url": "https://example.com/x"}})
    check("steps actually execute (no runner._VARIABLES AttributeError)",
          not any("_VARIABLES" in (l.get("error") or "") for l in j.get("log", [])),
          str([l.get("error") for l in j.get("log", [])][:1]))
    check("all steps pass when the value is supplied",
          j.get("passed") == 2 and j.get("failed") == 0, str(j.get("log")))

    print("\n[2] VALUES REACH THE FLOW")

    j = run({"project": "_paramtest", "headless": True, "platform": "website"})
    check("without the parameter the run fails", j.get("failed", 0) > 0)
    check("and it fails for the RIGHT reason (variable not stored)",
          any("not stored in memory" in (l.get("error") or "") for l in j.get("log", [])),
          str([l.get("error") for l in j.get("log", [])][:1]))

    j = run({"project": "_paramtest", "headless": True, "platform": "website",
             "parameters": {"probe_url": "https://example.com/ok"}})
    check("with the parameter the same flow passes", j.get("passed") == 2, str(j.get("log")))

    print("\n[3] THE LAUNCH RESPONSE NEVER ECHOES A VALUE")

    r = c.post("/tests/run", json={
        "project": "_paramtest", "headless": True, "platform": "website",
        "parameters": {"probe_url": "https://example.com/x", "jd_test_static_otp": "111222"},
        "secret_parameters": ["jd_test_static_otp"]})
    body = r.text
    check("response lists parameter NAMES", "probe_url" in body)
    check("response contains no parameter VALUE", "111222" not in body and "example.com" not in body,
          body[:120])

    print("\n[4] SECRETS ARE NOT LOGGED")

    import plan_runner

    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    saved, prop = plan_runner.logger.handlers, plan_runner.logger.propagate
    plan_runner.logger.handlers = [h]
    plan_runner.logger.propagate = False
    plan_runner.logger.setLevel(logging.DEBUG)
    try:
        plan_runner._inject_parameters([
            {"name": "product_url", "value": "https://example.com/x", "type": "string"},
            {"name": "jd_test_mobile", "value": "5550001111", "type": "string"},
            {"name": "jd_test_static_otp", "value": "111222", "type": "string"},
            {"name": "api_password", "value": "hunter2", "type": "secret"},
        ])
        out = buf.getvalue()
    finally:
        plan_runner.logger.handlers, plan_runner.logger.propagate = saved, prop

    check("log is non-empty (the capture actually worked)", bool(out.strip()))
    for leak in ("5550001111", "111222", "hunter2"):
        check(f"{leak[:4]}… never appears in the log", leak not in out)
    check("a non-secret value is still shown (masking is not blanket)",
          "https://example.com/x" in out)

    from nlp.variable_manager import RUNTIME_VARIABLES
    check("masked values still reach the flow",
          RUNTIME_VARIABLES.get("jd_test_static_otp") == "111222")

    for name in ("otp", "user_password", "JD_TEST_MOBILE", "auth_token", "pin"):
        check(f"{name!r} is treated as secret by default", _is_secret(name, set()))
    for name in ("product_url", "search_term", "expected_title"):
        check(f"{name!r} is not needlessly masked", not _is_secret(name, set()))

    print("\n[5] PLATFORM AND DEVICE REACH THE BROWSER")

    r = c.post("/tests/run", json={"project": "_paramtest", "headless": True,
                                   "platform": "mobilesite",
                                   "parameters": {"probe_url": "https://example.com/x"}})
    check("mobilesite is accepted", r.status_code == 200, r.text[:80])
    check("mobilesite supplies its default device",
          r.json().get("device_name") == "Pixel 7", str(r.json().get("device_name")))
    r = c.post("/tests/run", json={"project": "_paramtest", "headless": True,
                                   "platform": "mobilesite", "device_name": "iPhone 15",
                                   "parameters": {"probe_url": "https://example.com/x"}})
    check("an explicit device overrides the default",
          r.json().get("device_name") == "iPhone 15", str(r.json().get("device_name")))
    r = c.post("/tests/run", json={"project": "_paramtest", "platform": "website"})
    check("website needs no device", r.json().get("device_name") is None)

    for bad, code in (("banana", 422), ("android", 422), ("ios", 422)):
        r = c.post("/tests/run", json={"project": "_paramtest", "platform": bad})
        check(f"platform {bad!r} refused with {code}", r.status_code == code,
              f"got {r.status_code}")
    check("masked-platform refusal explains itself",
          "not enabled" in c.post("/tests/run",
                                  json={"project": "_paramtest", "platform": "ios"}).text)
finally:
    if os.path.exists(PROBE):
        os.unlink(PROBE)

print("\n" + "=" * 60)
print(f"PASSED: {_passed}  |  FAILED: {_failed}")
print("=" * 60)
sys.exit(1 if _failed else 0)
