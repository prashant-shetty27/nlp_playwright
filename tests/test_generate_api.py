"""
tests/test_generate_api.py — the upload → generate path over HTTP.

Two properties matter most and are asserted rather than assumed:

  a preview writes NOTHING.  A UI regenerates on every change of testcase
  selection or platform. If generation wrote a file each time, data/drafts/
  would fill with junk and earlier work would be clobbered.

  a preview is still LINTED.  "Preview" must not quietly mean "unchecked", so
  the flow is validated through a temporary file that is removed afterwards —
  and the throwaway filename must never appear in output the operator reads.

Run: python tests/test_generate_api.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402

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
XLSX = os.path.expanduser("~/Downloads/GJDT-21944_Ask_For_Photos_AI_Products_Testcases.xlsx")
DRAFTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "data", "drafts")

if not os.path.exists(XLSX):
    print(f"  SKIP  source workbook not present at {XLSX}")
    sys.exit(0)

print("\n[1] UPLOAD")

with open(XLSX, "rb") as f:
    r = c.post("/sources/upload", files={"file": (os.path.basename(XLSX), f, "application/vnd.ms-excel")})
check("upload returns 201", r.status_code == 201, str(r.status_code))
up = r.json()
sid = up["source_id"]
check("source_id is content-addressed", sid.startswith("src_"), sid)
check("all four tabs read", len(up["tabs_read"]) == 4, str(up["tabs_read"]))
check("74 testcases ingested", up["counts"]["testcases"] == 74, str(up["counts"]))
check("no rows silently rejected", up["counts"]["rejected_rows"] == 0)
check("each testcase carries id/title/steps",
      all({"id", "title", "steps"} <= set(t) for t in up["testcases"]))

with open(XLSX, "rb") as f:
    r2 = c.post("/sources/upload", files={"file": (os.path.basename(XLSX), f, "application/vnd.ms-excel")})
check("re-uploading the same bytes reuses the same id (no duplicates)",
      r2.json()["source_id"] == sid)

r = c.post("/sources/upload", files={"file": ("notes.txt", b"hello", "text/plain")})
check("unsupported extension is refused with 415", r.status_code == 415, str(r.status_code))
r = c.post("/sources/upload", files={"file": ("empty.xlsx", b"", "application/vnd.ms-excel")})
check("empty file is refused with 422", r.status_code == 422, str(r.status_code))

check("uploaded source is listed",
      sid in [s["source_id"] for s in c.get("/sources").json()["sources"]])
check("unknown source returns 404", c.get("/sources/src_nope").status_code == 404)

print("\n[2] GENERATE — preview writes nothing but is still validated")

before = set(os.listdir(DRAFTS))
body = {"source_id": sid, "platform": "website",
        "testcase_ids": ["TC_AFP_C01", "TC_AFP_C02", "TC_AFP_C03"],
        "flow_name": "Ask More Photos", "persist": False}
r = c.post("/generate", json=body)
check("generate returns 200", r.status_code == 200, r.text[:120])
g = r.json()

check("flow_text is returned", bool(g["flow_text"].strip()))
check("nothing was persisted", g["persisted"] == {"flow_path": None, "map_path": None},
      str(g["persisted"]))
check("no file appeared in data/drafts", set(os.listdir(DRAFTS)) == before,
      str(set(os.listdir(DRAFTS)) - before))
check("the preview WAS linted", g["validation"]["lint_exit"] is not None)
check("the parser ran", g["validation"]["parser_ok"] is True,
      str(g["validation"]["parser_errors"])[:100])
check("temp filename never leaks into lint output",
      "preview_" not in g["validation"]["lint_output"],
      g["validation"]["lint_output"][:80])

check("summary counts every mapping",
      sum(g["summary"].values()) == len(g["steps"]), f"{g['summary']} vs {len(g['steps'])}")
emitted = [s for s in g["steps"] if s["emits"]]
check("emitted steps are numbered from 1 with no gaps",
      [s["step"] for s in emitted] == list(range(1, len(emitted) + 1)))
check("every step reports its source row", all(s["source"] for s in g["steps"]))
check("every step reports a status", all(s["status"] for s in g["steps"]))
check("steps needing a locator name it",
      all(s["locator"] for s in g["steps"] if s["status"] == "NEEDS_LOCATOR"))
check("missing_locators is derivable from the steps",
      set(g["missing_locators"]) <= {s["locator"] for s in g["steps"] if s["locator"]},
      str(g["missing_locators"]))
check("regenerating is idempotent",
      c.post("/generate", json=body).json()["flow_text"] == g["flow_text"])

print("\n[3] GENERATE — platform is honoured")

r = c.post("/generate", json={**body, "platform": "mobilesite"})
check("mobilesite generates", r.status_code == 200, r.text[:100])
check("response echoes the resolved platform", r.json()["platform"] == "mobilesite")
r = c.post("/generate", json={**body, "platform": "waptouch"})
check("an alias resolves to its canonical platform",
      r.status_code == 200 and r.json()["platform"] == "mobilesite")

print("\n[4] ERRORS — refuse clearly rather than guess")

for label, payload, code in [
    ("unknown platform", {**body, "platform": "banana"}, 422),
    ("missing platform", {k: v for k, v in body.items() if k != "platform"}, 422),
    ("unknown testcase", {**body, "testcase_ids": ["TC_NOPE"]}, 422),
    ("empty selection",  {**body, "testcase_ids": []}, 422),
    ("unknown source",   {**body, "source_id": "src_zzz"}, 404),
]:
    rr = c.post("/generate", json=payload)
    check(f"{label} -> {code}", rr.status_code == code,
          f"got {rr.status_code}: {rr.text[:70]}")

print("\n[5] PERSIST — writing is deliberate")

out = os.path.join(DRAFTS, "_apitest_persist.flow")
for p in (out, out.replace(".flow", ".map.json")):
    if os.path.exists(p):
        os.unlink(p)
r = c.post("/generate", json={**body, "persist": True, "out_path": out, "overwrite": True})
check("persist:true returns 200", r.status_code == 200, r.text[:100])
pj = r.json()["persisted"]
check("flow file written", pj["flow_path"] and os.path.exists(pj["flow_path"]), str(pj))
check("traceability map written", pj["map_path"] and os.path.exists(pj["map_path"]), str(pj))
persisted_text = open(pj["flow_path"], encoding="utf-8").read()
check("persisted text differs from preview ONLY by the map header",
      [ln for ln in persisted_text.splitlines() if not ln.startswith("# Map")]
      == [ln for ln in g["flow_text"].splitlines() if not ln.startswith("# Map")])
check("a preview does not claim a map file that was never written",
      "not written (preview)" in g["flow_text"], g["flow_text"].splitlines()[3])
check("a persisted flow points at its real map file",
      pj["map_path"].endswith(".map.json")
      and os.path.basename(pj["map_path"]) in persisted_text)
r = c.post("/generate", json={**body, "persist": True, "out_path": out, "overwrite": False})
check("refuses to clobber without overwrite (409)", r.status_code == 409, str(r.status_code))
for p in (out, out.replace(".flow", ".map.json")):
    if os.path.exists(p):
        os.unlink(p)

print("\n" + "=" * 60)
print(f"PASSED: {_passed}  |  FAILED: {_failed}")
print("=" * 60)
sys.exit(1 if _failed else 0)
