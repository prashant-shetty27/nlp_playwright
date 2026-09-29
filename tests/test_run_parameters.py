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
from api.auth import INTERNAL_TOKEN  # noqa: E402

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


c = TestClient(app, headers={"X-Internal-Token": INTERNAL_TOKEN})
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

    # A missing value is now refused BEFORE the browser opens. It used to launch
    # and fail once per step with "Variable '${x}' is not stored in memory!" —
    # five failures describing one omission, none of them saying what to do.
    j = run({"project": "_paramtest", "headless": True, "platform": "website"})
    check("a run with no value for a required parameter is refused up front",
          j.get("http") == 422, str(j)[:160])
    check("the refusal names the parameter that is missing",
          "probe_url" in (j.get("detail") or ""), str(j.get("detail"))[:160])
    check("and says where to supply it",
          "Run Center" in (j.get("detail") or "") or "Test Data" in (j.get("detail") or ""),
          str(j.get("detail"))[:160])
    check("nothing was executed, so no half-run is recorded",
          "passed" not in j, str(j)[:120])

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

# ═══════════════════════════════════════════════════════════════════════════
# A generated flow must DECLARE the values it expects from its caller.
#
# Fourth defect, same family: a flow referencing ${test_url} carried the header
# "# Params : none" — emitter.render_clean read a `placeholders` dict that
# POST /generate never populated — and tools/flow_lint.py had no notion of run
# inputs at all, so it rejected the parameterised flow it had just been handed
# with E004 on the flow's own first statement. The flow was correct and runnable;
# the gate could not see it. Both halves are asserted here against the live
# emitter and the live linter, not against a fixture.
# ═══════════════════════════════════════════════════════════════════════════
print("\n[params header] declared, honoured, and not over-declared")

import subprocess  # noqa: E402
import tempfile  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from ai_flow_builder.emitter import run_parameters  # noqa: E402

check("a referenced-but-never-defined variable is a run parameter",
      run_parameters(["open ${test_url}"]) == ["test_url"])
check("order follows first use, so the header reads like the flow",
      run_parameters(["open ${b}", 'type "${a}" into f']) == ["b", "a"])
check("a variable an earlier step produces is NOT a run parameter",
      run_parameters(["store text of loc as cap",
                      'verify stored cap contains "x"']) == [])
check("create variable counts as producing it",
      run_parameters(['create variable v with value "x"', "open ${v}"]) == [])
check("fetch otp counts as producing it",
      run_parameters(['fetch otp for "9" as otp',
                      'enter otp "${otp}" into f']) == [])
check("used before the step that defines it is still a run parameter",
      run_parameters(["open ${v}", 'create variable v with value "x"']) == ["v"])
check("an unparseable line defines nothing and does not crash the deriver",
      run_parameters(["!! not a statement !!", "open ${x}"]) == ["x"])

# The linter half. Written to a real file because that is what flow_lint reads.
def _lint(text: str) -> tuple[int, str]:
    fd, path = tempfile.mkstemp(suffix=".flow", dir=os.path.join(BASE_DIR, "flows"))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        proc = subprocess.run(
            [sys.executable, "tools/flow_lint.py", path, "--platform", "website"],
            capture_output=True, text=True, cwd=BASE_DIR, timeout=120)
        return proc.returncode, proc.stdout + proc.stderr
    finally:
        os.unlink(path)

rc, out = _lint("# t\n# Params : declared_url\n\nopen ${declared_url}\n")
check("a declared parameter satisfies the linter", rc == 0, out.strip()[:160])

rc, out = _lint("# t\n# Params : none\n\nopen ${undeclared_url}\n")
check("an UNdeclared variable is still an error — no blanket amnesty",
      rc != 0 and "undeclared_url" in out, out.strip()[:160])

rc, out = _lint("# t\n\nopen ${no_header_at_all}\n")
check("a flow with no Params header is unaffected",
      rc != 0 and "no_header_at_all" in out, out.strip()[:160])

rc, out = _lint("# t\n# Params : a\n\nopen x\n# Params : b\nopen ${b}\n")
check("only the header is read — a later '# Params' comment grants nothing",
      rc != 0 and "'${b}'" in out, out.strip()[:160])

# End to end: what the emitter writes must be what the linter accepts.
from ai_flow_builder import emitter as _em  # noqa: E402
from ai_flow_builder.mapper import SUPPORTED, StepMapping  # noqa: E402

_maps = [StepMapping(statement="open ${test_url}", status=SUPPORTED, source_ref="r1"),
         StepMapping(statement='type "${test_mobile}" into mobile_number_input',
                     status=SUPPORTED, source_ref="r2")]
_text = _em.render_clean(flow_name="hdr", source_ids=["TC1"], source_desc={"title": "t"},
                         mappings=_maps, placeholders={}, map_path="")
check("emitter declares both parameters even with placeholders={}",
      "# Params : test_url test_mobile" in _text,
      [l for l in _text.splitlines() if "Params" in l])
rc, out = _lint(_text)
check("the emitter's own output passes the linter unchanged", rc == 0, out.strip()[:200])


# ═══════════════════════════════════════════════════════════════════════════
# The editor's save path must produce the same quality of file as generation.
#
# Found by driving the UI: the New Test Case dialog generates with persist=False
# and saves through PUT/POST /projects, which wrote bare statements. So a flow
# authored in the browser failed E004 while byte-identical steps written by the
# generator passed — and worse, _read_steps drops comments while update_project
# rewrites the whole file, so editing one step of a GENERATED flow deleted its
# "# Source" and "# Map" lines for good.
# ═══════════════════════════════════════════════════════════════════════════
print("\n[projects] editor saves are declared and keep their provenance")

def _lint_named(name: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "tools/flow_lint.py", f"flows/{name}.flow", "--platform", "website"],
        capture_output=True, text=True, cwd=BASE_DIR, timeout=120)
    return proc.returncode, proc.stdout + proc.stderr

_A, _B = "_pt_create", "_pt_roundtrip"
try:
    c.delete(f"/projects/{_A}")
    r = c.post("/projects", json={"name": _A, "steps": [
        "open ${page_url}", 'type "${test_mobile}" into mobile_number_input']})
    check("POST /projects accepts a parameterised flow", r.status_code == 201, r.text[:120])
    _txt = open(os.path.join(BASE_DIR, "flows", f"{_A}.flow"), encoding="utf-8").read()
    check("it declares both parameters",
          "# Params : page_url test_mobile" in _txt,
          [l for l in _txt.splitlines() if "Params" in l])
    rc, out = _lint_named(_A)
    check("a flow saved from the editor passes the linter", rc == 0, out.strip()[:160])

    # A generated flow, opened in the editor and saved with one step added.
    with open(os.path.join(BASE_DIR, "flows", f"{_B}.flow"), "w", encoding="utf-8") as f:
        f.write("# gen\n# Source : sheet | TC1\n# Params : old\n"
                "# Map    : data/drafts/x.map.json\n\nopen ${page_url}\n")
    _steps = c.get(f"/projects/{_B}").json()["steps"]
    check("the editor reads statements only (comments are not steps)",
          _steps == ["open ${page_url}"], _steps)
    c.put(f"/projects/{_B}", json={"steps": _steps + ['type "${test_mobile}" into mobile_number_input']})
    _txt = open(os.path.join(BASE_DIR, "flows", f"{_B}.flow"), encoding="utf-8").read()
    check("editing a generated flow keeps its Source line", "# Source : sheet | TC1" in _txt, _txt[:120])
    check("editing a generated flow keeps its Map line", "# Map    : data/drafts/x.map.json" in _txt, _txt[:120])
    check("the stale 'old' parameter is not carried over", "old" not in _txt, _txt[:120])
    check("the newly-referenced parameter is declared",
          "# Params : page_url test_mobile" in _txt,
          [l for l in _txt.splitlines() if "Params" in l])
    rc, out = _lint_named(_B)
    check("the edited flow passes the linter", rc == 0, out.strip()[:160])

    # Dropping the last ${variable} must drop the declaration, not keep a lie.
    c.put(f"/projects/{_B}", json={"steps": ["click login_with_otp"]})
    _txt = open(os.path.join(BASE_DIR, "flows", f"{_B}.flow"), encoding="utf-8").read()
    check("removing every variable removes the Params line", "Params" not in _txt, _txt[:120])
    check("the header still survives that write", "# Source : sheet | TC1" in _txt, _txt[:120])
finally:
    for _n in (_A, _B):
        c.delete(f"/projects/{_n}")
        _p = os.path.join(BASE_DIR, "flows", f"{_n}.flow")
        if os.path.exists(_p):
            os.unlink(_p)


print("\n" + "=" * 60)
print(f"PASSED: {_passed}  |  FAILED: {_failed}")
print("=" * 60)
sys.exit(1 if _failed else 0)
