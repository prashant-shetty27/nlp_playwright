"""
tests/test_rename.py — renaming must not orphan the things that point at a name.

Renaming did not exist at all: a test case, an element or a test-data value kept
whatever name it was given at creation, and the only workaround was delete and
re-create. That is worse than it sounds, because three kinds of reference are
stored BY NAME and nothing would have updated them:

    suites/*.json and plans/*.json  list flows by name
    a flow's steps                  name the elements they click
    a flow's steps and its header   name variables as ${name} and under "# Params"

So the tests here are less about renaming and more about the references: a rename
that leaves a suite pointing at a flow that no longer exists has not renamed
anything, it has broken something quietly.

Every rename is preview-first. Called without apply it must report what WOULD
change and touch nothing — asserted here, because a preview that has already
written is worse than no preview.

Run: python tests/test_rename.py
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402

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
FLOWS = os.path.join(BASE_DIR, "flows")
SUITES = os.path.join(BASE_DIR, "suites")
FLOW = os.path.join(FLOWS, "_rn_a.flow")
SUITE = os.path.join(SUITES, "_rn_suite.json")


def _cleanup() -> None:
    for p in (FLOW, os.path.join(FLOWS, "_rn_b.flow"), SUITE,
              os.path.join(FLOWS, "_rn_var.flow"), os.path.join(FLOWS, "_rn_loc.flow")):
        if os.path.exists(p):
            os.unlink(p)


_cleanup()

# ═══════════════════════════════════════════════════════════════════════════
print("\n[1] A TEST CASE — file, sidecar, and every suite naming it")

os.makedirs(SUITES, exist_ok=True)
with open(FLOW, "w", encoding="utf-8") as f:
    f.write("# Params : none\n\nclick search_box\n")
with open(os.path.join(FLOWS, "_rn_a.map.json"), "w", encoding="utf-8") as f:
    json.dump({"steps": []}, f)
with open(SUITE, "w", encoding="utf-8") as f:
    json.dump({"name": "s", "flows": ["_rn_a", "other_flow"]}, f)

try:
    r = c.post("/projects/_rn_a/rename", json={"new_name": "_rn_b"})
    changes = r.json().get("changes", [])
    check("preview lists the file rename", any(ch["kind"] == "rename" for ch in changes))
    check("preview lists the sidecar map",
          any("map.json" in ch.get("detail", "") for ch in changes), str(changes))
    check("preview lists the suite that names it",
          any("_rn_suite.json" in ch["file"] for ch in changes), str(changes))
    check("PREVIEW CHANGES NOTHING — the file is still at its old name",
          os.path.exists(FLOW) and not os.path.exists(os.path.join(FLOWS, "_rn_b.flow")))
    check("preview did not touch the suite either",
          json.load(open(SUITE))["flows"] == ["_rn_a", "other_flow"])

    c.post("/projects/_rn_a/rename", json={"new_name": "_rn_b", "apply": True})
    check("the flow moved", not os.path.exists(FLOW)
          and os.path.exists(os.path.join(FLOWS, "_rn_b.flow")))
    check("the sidecar moved with it",
          os.path.exists(os.path.join(FLOWS, "_rn_b.map.json")))
    check("the suite now points at the new name",
          json.load(open(SUITE))["flows"] == ["_rn_b", "other_flow"],
          str(json.load(open(SUITE))["flows"]))
    check("an unrelated entry in the suite was left alone",
          "other_flow" in json.load(open(SUITE))["flows"])
    check("the renamed test case is listed under its new name",
          "_rn_b" in c.get("/projects").json()["projects"])

    check("renaming onto a name that exists is refused",
          c.post("/projects/_rn_b/rename",
                 json={"new_name": "web_movie_flow", "apply": True}).status_code == 409)
    check("an unusable new name is refused",
          c.post("/projects/_rn_b/rename",
                 json={"new_name": "..", "apply": True}).status_code == 409)
    check("renaming something that does not exist is refused",
          c.post("/projects/_rn_nope/rename", json={"new_name": "x"}).status_code == 409)
    check("a refused rename left the file where it was",
          os.path.exists(os.path.join(FLOWS, "_rn_b.flow")))
finally:
    for p in (os.path.join(FLOWS, "_rn_b.flow"), os.path.join(FLOWS, "_rn_b.map.json"),
              os.path.join(FLOWS, "_rn_a.map.json"), SUITE):
        if os.path.exists(p):
            os.unlink(p)

# ═══════════════════════════════════════════════════════════════════════════
print("\n[2] A TEST-DATA VALUE — its ${references} and its Params header")

with open(os.path.join(FLOWS, "_rn_var.flow"), "w", encoding="utf-8") as f:
    f.write("# Params : rn_city other_var\n\nsearch for ${rn_city}\nopen ${other_var}\n")
c.put("/testdata", json={"name": "rn_city", "value": "Mumbai", "scope": "global"})
try:
    r = c.post("/testdata/rn_city/rename", json={"new_name": "rn_search_city"})
    check("preview names the flow that uses it",
          any("_rn_var.flow" in ch["file"] for ch in r.json()["changes"]), r.text[:140])
    check("preview changed nothing",
          "${rn_city}" in open(os.path.join(FLOWS, "_rn_var.flow"), encoding="utf-8").read())

    c.post("/testdata/rn_city/rename", json={"new_name": "rn_search_city", "apply": True})
    text = open(os.path.join(FLOWS, "_rn_var.flow"), encoding="utf-8").read()
    check("the step now references the new name", "${rn_search_city}" in text, text)
    check("the old reference is gone", "${rn_city}" not in text, text)
    check("the Params header was updated too", "# Params : rn_search_city" in text, text)
    check("another variable on that header was left alone", "other_var" in text, text)
    check("the value itself carried across",
          c.get("/testdata").json()["values"].get("rn_search_city", {}).get("value") == "Mumbai")
    check("the old value is gone", "rn_city" not in c.get("/testdata").json()["values"])
    check("renaming onto an existing value is refused",
          c.post("/testdata/rn_search_city/rename",
                 json={"new_name": "username", "apply": True}).status_code
          in (409, 404))
finally:
    c.delete("/testdata/rn_search_city")
    c.delete("/testdata/rn_city")
    if os.path.exists(os.path.join(FLOWS, "_rn_var.flow")):
        os.unlink(os.path.join(FLOWS, "_rn_var.flow"))

# ═══════════════════════════════════════════════════════════════════════════
print("\n[3] AN ELEMENT — every step that clicks it")

# The API normalises names on save (a leading underscore is stripped), so the
# test must use the names it was actually given back, not the ones it sent.
_saved = c.post("/locators", json={"page": "rn_page", "name": "rn_old_button",
                                   "xpath": "//button[@id='x']"}).json()
_PAGE = _saved["normalised"]["page"]
_OLD = _saved["normalised"]["name"]
with open(os.path.join(FLOWS, "_rn_loc.flow"), "w", encoding="utf-8") as f:
    f.write("# Params : none\n\nclick rn_old_button\nclick rn_old_button_extra\n")
try:
    r = c.post(f"/locators/{_PAGE}/{_OLD}/rename", json={"new_name": "rn_new_button"})
    check("preview names the flow that clicks it",
          any("_rn_loc.flow" in ch["file"] for ch in r.json()["changes"]), r.text[:160])
    check("preview changed nothing",
          _OLD in open(os.path.join(FLOWS, "_rn_loc.flow"), encoding="utf-8").read())

    c.post(f"/locators/{_PAGE}/{_OLD}/rename",
           json={"new_name": "rn_new_button", "apply": True})
    text = open(os.path.join(FLOWS, "_rn_loc.flow"), encoding="utf-8").read()
    check("the step now clicks the new name", "click rn_new_button\n" in text, text)
    check("a longer name that merely CONTAINS the old one is untouched",
          f"{_OLD}_extra" in text, text)
    check("the element is stored under its new name",
          "rn_new_button" in c.get(f"/locators/{_PAGE}").json())

    check("renaming a recorded element is refused with a reason",
          c.post("/locators/home_page/login_with_otp/rename",
                 json={"new_name": "whatever", "apply": True}).status_code == 409)
finally:
    c.delete(f"/locators/{_PAGE}/rn_new_button")
    c.delete(f"/locators/{_PAGE}/{_OLD}")
    if os.path.exists(os.path.join(FLOWS, "_rn_loc.flow")):
        os.unlink(os.path.join(FLOWS, "_rn_loc.flow"))

_cleanup()
print("\n" + "=" * 60)
print(f"PASSED: {_passed}  |  FAILED: {_failed}")
print("=" * 60)
sys.exit(1 if _failed else 0)
