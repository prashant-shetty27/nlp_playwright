"""
tests/test_special_characters.py — hostile text must not break anything, anywhere.

What this covers
----------------
Every boundary where free text becomes something structural: a step's quoted
argument, a variable name, a variable VALUE, a locator name, a selector, a
filename, a content address. Four real defects were found by probing these and
are pinned here so they cannot come back.

1. Four different ideas of what a variable name is.
   The runtime resolves ${anything-up-to-a-brace}; the linter only recognised
   [A-Za-z_][A-Za-z0-9_]*. So ${product.id} and ${user-name} were invisible to
   the very check that exists to catch undefined variables — such a flow passed
   the gate and failed mid-run. Now every module shares nlp/variables.

2. VariableManager rewrote names on the way in but not on the way out.
   save("user-name") stored "username"; the next ${user-name} raised "not found
   in runtime memory". Normalisation is now symmetric.

3. A screenshot label reached a filesystem path unsanitised.
   nlp/parser stripped separators, but it is one caller of several — a codeless
   JSON step or a plan would not have been. The writer now sanitises its own
   filename.

4. /projects accepted names that produced junk files: "" wrote a hidden
   ".flow", ".." wrote "...flow".

Run: python tests/test_special_characters.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "tools"))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402
from nlp import variables as V  # noqa: E402
from nlp.parser import parse_step  # noqa: E402

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


c = TestClient(app)

#: Text that has broken parsers, shells, XPath engines and JSON writers before.
NASTY = {
    "apostrophe":   "O'Brien",
    "double quote": 'He said "stop"',
    "ampersand":    "Tom & Jerry",
    "percent":      "50% off",
    "unicode":      "café ☕ ₹500",
    "cjk":          "搜索测试",
    "backslash":    r"C:\Users\test",
    "html":         "<script>alert(1)</script>",
    "sql-ish":      "'; DROP TABLE x; --",
    "braces":       "a{b}c",
    "dollar":       "cost $100",
    "newline-ish":  r"line1\nline2",
    "emoji only":   "🎉",
    "spaces":       "  padded  ",
}

# ═══════════════════════════════════════════════════════════════════════════
print("\n[1] STEP TEXT — a quoted argument survives verbatim")

for label, value in NASTY.items():
    if '"' in value:
        continue                      # cannot appear raw inside a "…" argument
    stmt = f'type "{value}" into search_box'
    try:
        got = parse_step(stmt).text
        check(f"{label}: round-trips unchanged", got == value, f"{got!r} != {value!r}")
    except Exception as e:  # noqa: BLE001
        check(f"{label}: parses", False, f"{type(e).__name__}: {str(e)[:60]}")

check("a quoted argument may be empty", parse_step('type "" into search_box').text == "")
check("an apostrophe does not terminate the argument",
      parse_step("""verify text "it's fine" on page""").text == "it's fine")

# ═══════════════════════════════════════════════════════════════════════════
print("\n[2] VARIABLE VALUES — substitution is literal, never re-interpreted")

from nlp.variable_manager import RUNTIME_VARIABLES, resolve_variables  # noqa: E402

for label, value in NASTY.items():
    RUNTIME_VARIABLES["probe"] = value
    check(f"{label}: substitutes exactly",
          resolve_variables("A ${probe} B") == f"A {value} B",
          repr(resolve_variables("A ${probe} B")))

RUNTIME_VARIABLES["recursive"] = "${recursive}"
check("a value that looks like a reference is not expanded again (no loop)",
      resolve_variables("${recursive}") == "${recursive}")
RUNTIME_VARIABLES["points_at"] = "${probe}"
check("a value naming another variable is left literal",
      resolve_variables("${points_at}") == "${probe}")

# ═══════════════════════════════════════════════════════════════════════════
print("\n[3] VARIABLE NAMES — one grammar, shared by every module")

from ai_flow_builder.bundle import referenced_variables  # noqa: E402
from ai_flow_builder.emitter import run_parameters  # noqa: E402
import flow_lint  # noqa: E402

for name in ("plain", "with_underscore", "with-hyphen", "with.dot", "CAPS", "_leading", "a1"):
    text = "x ${" + name + "} y"
    seen_by = {
        "runtime":  name in V.referenced_names(text),
        "bundle":   name in referenced_variables(text),
        "linter":   name in flow_lint._VAR_REF_RE.findall(text),
    }
    check(f"${{{name}}}: every module sees it", all(seen_by.values()), str(seen_by))

check("the linter recognises exactly what the runtime resolves",
      flow_lint._VAR_REF_RE is V.REFERENCE_RE)
check("a name with a space cannot be declared, so it is not emitted as a param",
      run_parameters(["open ${a b}"]) == [],
      str(run_parameters(["open ${a b}"])))
check("but a hyphen or dot IS emitted, because it is declarable",
      run_parameters(["open ${a-b}", "open ${a.b}"]) == ["a-b", "a.b"],
      str(run_parameters(["open ${a-b}", "open ${a.b}"])))

# ═══════════════════════════════════════════════════════════════════════════
print("\n[4] VARIABLE STORAGE — the name written is the name read")

from nlp.variable_manager import VariableManager  # noqa: E402

for raw in ("user-name", "user.name", "user name", "User_Name", "123abc", "a--b"):
    vm = VariableManager(strict_mode=True)
    vm.save(raw, "VALUE")
    try:
        out = vm.resolve_parameters({"field": "${" + raw + "}"})
        check(f"saved as {raw!r} then resolved by the same name",
              out == {"field": "VALUE"}, f"{out}  memory={list(vm.memory)}")
    except Exception as e:  # noqa: BLE001
        check(f"saved as {raw!r} then resolved by the same name", False,
              f"{type(e).__name__}: {str(e)[:60]}  memory={list(vm.memory)}")

vm = VariableManager(strict_mode=True)
vm.save("a-b", "HYPHEN")
vm.save("a.b", "DOT")
check("two names that normalise alike do collapse — deliberately, not silently",
      len(vm.memory) == 1 and vm.memory["a_b"] == "DOT", str(vm.memory))

vm = VariableManager(strict_mode=True)
vm.save("", "ignored")
vm.save("!!!", "ignored")
check("an unusable name is refused rather than stored under a blank key",
      vm.memory == {}, str(vm.memory))

vm = VariableManager(strict_mode=True)
vm.save("keep", "V")
check("the parameter KEYS of a step are never rewritten by resolution",
      vm.resolve_parameters({"locator": "${keep}", "timeout": 5, "plain": "no vars"})
      == {"locator": "V", "timeout": 5, "plain": "no vars"})
try:
    vm.resolve_parameters({"x": "${never_defined}"})
    check("an undefined variable still fails fast", False, "no error raised")
except ValueError:
    check("an undefined variable still fails fast", True)

check("normalise is idempotent", all(V.normalise(V.normalise(n)) == V.normalise(n)
                                     for n in ("a-b", "a.b", "  x  ", "123", "a--b")))

# ═══════════════════════════════════════════════════════════════════════════
print("\n[5] LOCATORS — names normalised, selectors kept verbatim")

from locators.manager import _resolve_selector, normalise_name  # noqa: E402

for raw, expect in (("Web_B2B_HomePage", "web_b2b_homepage"), ("search box", "search_box"),
                    ("search-box", "search_box"), ("../evil", "evil"),
                    ("name with 'quote'", "name_with_quote"), ("123start", "item_123start")):
    check(f"locator name {raw!r} normalises to {expect!r}",
          normalise_name(raw, kind="locator") == expect,
          normalise_name(raw, kind="locator"))

for bad in ("", "   ", "搜索框", "☕"):
    try:
        normalise_name(bad, kind="locator")
        check(f"an unusable locator name {bad!r} is rejected", False, "accepted")
    except ValueError:
        check(f"an unusable locator name {bad!r} is rejected", True)

for sel in ("""//div[text()="O'Brien"]""", """input[placeholder='Search "all"']""",
            "//button[contains(.,'50% & up')]", "div[data-x='café']"):
    check(f"selector kept byte-for-byte: {sel[:34]}…",
          _resolve_selector({"selectors": [{"type": "css", "value": sel}]}) == sel)

r = c.post("/locators", json={"page": "../evil", "name": "../evil", "xpath": "//div"})
check("the API normalises a traversal attempt in page and name",
      r.status_code == 201 and r.json()["normalised"] == {"page": "evil", "name": "evil"},
      r.text[:120])
if r.status_code == 201:
    c.delete("/locators/evil/evil")
check("a locator name that normalises to nothing is a 422, not a crash",
      c.post("/locators", json={"page": "p", "name": "☕", "xpath": "//div"}).status_code == 422)

# ═══════════════════════════════════════════════════════════════════════════
print("\n[6] FILENAMES — nothing escapes its directory, nothing junk is written")

FLOWS = os.path.join(BASE_DIR, "flows")
for name in ("../../etc/evil", "a/b", "name with spaces", "good_name-2"):
    r = c.post("/projects", json={"name": name, "steps": ["click search_box"]})
    check(f"{name!r} gets a definite answer, not an error",
          r.status_code in (201, 422), f"got {r.status_code}: {r.text[:80]}")
    if r.status_code == 201:
        path = r.json()["path"]
        check(f"{name!r} stays inside flows/",
              os.path.realpath(path).startswith(os.path.realpath(FLOWS) + os.sep), path)
        check(f"{name!r} produces a plain filename",
              " " not in os.path.basename(path) and os.path.basename(path).endswith(".flow"),
              os.path.basename(path))
        c.delete(f"/projects/{os.path.basename(path)[:-5]}")
        if os.path.exists(path):
            os.unlink(path)
    else:
        check(f"{name!r} is either written safely or refused clearly",
              r.status_code == 422, r.text[:100])

for junk in ("", "   ", "..", "///"):
    check(f"{junk!r} is refused with a 422 instead of writing junk",
          c.post("/projects", json={"name": junk, "steps": ["click x"]}).status_code == 422)
check("no hidden '.flow' file was created", not os.path.exists(os.path.join(FLOWS, ".flow")))
check("no '...flow' file was created", not os.path.exists(os.path.join(FLOWS, "...flow")))

import re  # noqa: E402

_sanitise = lambda l: (re.sub(r"[^A-Za-z0-9_.-]+", "_", str(l or "")).strip("._-")  # noqa: E731
                       or "capture")
src = open(os.path.join(BASE_DIR, "execution", "action_service.py"), encoding="utf-8").read()
check("take_screenshot sanitises its own label rather than trusting the caller",
      "safe_label" in src and "os.path.join(settings.SCREENSHOTS_DIR, f\"{safe_label}_" in src)
for label in ("../../etc/passwd", "a/b", "name with spaces", "", None):
    out = _sanitise(label)
    check(f"screenshot label {str(label)[:18]!r} yields a plain filename",
          "/" not in out and ".." not in out and out != "", out)

# ═══════════════════════════════════════════════════════════════════════════
print("\n[7] CONTENT ADDRESSES AND FILE CONTENT — unicode is safe end to end")

from ai_flow_builder.storage import prompt_source_id  # noqa: E402

for text in ("café ☕ test", "搜索测试", "quote\"'s", "line\nbreak", "a" * 30000):
    sid = prompt_source_id(text, "website", 1)
    check(f"prompt id for {text[:14]!r} is filesystem-safe",
          sid.startswith("src_") and sid[4:].isalnum() and len(sid) == 16, sid)
check("different unicode prompts do not collide",
      prompt_source_id("café", "website", 1) != prompt_source_id("cafe", "website", 1))

steps = ['type "café ☕ ₹500" into search_box', """verify text "O'Brien & Co." on page"""]
c.post("/projects", json={"name": "_charset_probe", "steps": steps})
try:
    check("unicode step content survives write then read",
          c.get("/projects/_charset_probe").json()["steps"] == steps)
    raw = open(os.path.join(FLOWS, "_charset_probe.flow"), encoding="utf-8").read()
    check("the file on disk is real UTF-8, not escapes", "café ☕ ₹500" in raw)
    check("and it still parses", all(parse_step(s) for s in steps))
finally:
    c.delete("/projects/_charset_probe")
    p = os.path.join(FLOWS, "_charset_probe.flow")
    if os.path.exists(p):
        os.unlink(p)

print("\n" + "=" * 60)
print(f"PASSED: {_passed}  |  FAILED: {_failed}")
print("=" * 60)
sys.exit(1 if _failed else 0)
