"""
tests/test_prompt_determinism.py — the same request must produce the same test.

The behaviour this exists to protect
------------------------------------
Drafting the identical prompt twice produced different flows:

    first draft   open ${test_url}   … click login_with_otp
    second draft  open ${page_url}   … click popup_submit_button

Each draft was internally consistent, so nothing failed — but two flows for the
same scenario then demanded differently-named inputs, and a suite could not
supply one value to both. Worse, the store is content-addressed on the prompt,
so the second draft silently OVERWROTE the first under a shared id, leaving
already-generated flows referencing variables their own source no longer had.
Drafting one prompt for two platforms collided the same way.

A model cannot promise to name things the same way twice, so the guarantee is
made in code, two ways:

  1. canonicalise_variables() renames drafted variables to a fixed vocabulary
     after the model has answered — deterministic, provider-independent.
  2. the prompt route returns the draft already on file for an identical request
     instead of asking again, so a repeat is reproducible and free. Asking again
     is possible, but has to be requested (redraft=true).

Locator CHOICE is not fixed by either — picking between two real elements is a
judgement, so it stays visible on the review screen before anything is saved.
The cache is what stops it changing under you.

Run: python tests/test_prompt_determinism.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import ai_flow_builder.prompt_source as PS  # noqa: E402
from ai_flow_builder.storage import prompt_source_id  # noqa: E402
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


def draft_of(*steps, values=None, inputs=None):
    return {"testcases": [{"testcase_id": "TC1", "title": "t", "steps": list(steps),
                           "expected": "", "priority": "High", "classification": "Positive",
                           "preconditions": []}],
            "values_found": dict(values or {}),
            "inputs_needed": list(inputs or []),
            "assumptions": [], "unclear": []}


# ═══════════════════════════════════════════════════════════════════════════
print("\n[1] TWO DRAFTS THAT NAMED THINGS DIFFERENTLY MUST CONVERGE")

a = PS.canonicalise_variables(draft_of(
    "Navigate to ${page_url}", "Enter ${test_mobile} in element M",
    values={"page_url": "https://x", "test_mobile": "9324751210"}))
b = PS.canonicalise_variables(draft_of(
    "Navigate to ${test_url}", "Enter ${mobile_number} in element M",
    values={"test_url": "https://x", "mobile_number": "9324751210"}))

check("the two drafts now produce identical steps",
      a["testcases"][0]["steps"] == b["testcases"][0]["steps"],
      f"{a['testcases'][0]['steps']} vs {b['testcases'][0]['steps']}")
check("and identical parameter names",
      set(a["values_found"]) == set(b["values_found"]) == {"url", "mobile"},
      f"{sorted(a['values_found'])} vs {sorted(b['values_found'])}")
check("the values themselves are carried across the rename",
      a["values_found"]["mobile"] == "9324751210", str(a["values_found"]))
check("what was renamed is recorded, so the change is not silent",
      a.get("renamed_variables") == {"page_url": "url", "test_mobile": "mobile"},
      str(a.get("renamed_variables")))

# ═══════════════════════════════════════════════════════════════════════════
print("\n[2] RENAMING MUST NOT MERGE THINGS THAT ARE GENUINELY DIFFERENT")

d = PS.canonicalise_variables(draft_of(
    "open ${page_url}", "open ${product_url}",
    values={"page_url": "https://a", "product_url": "https://b"}))
check("two different URLs stay two different parameters",
      d["testcases"][0]["steps"] == ["open ${url}", "open ${url_2}"],
      str(d["testcases"][0]["steps"]))
check("both values survive", sorted(d["values_found"]) == ["url", "url_2"],
      str(d["values_found"]))
check("first use wins the unsuffixed name",
      d["values_found"]["url"] == "https://a", str(d["values_found"]))

u = PS.canonicalise_variables(draft_of(
    "open ${some_bespoke_thing}", values={"some_bespoke_thing": "x"}))
check("a name outside the vocabulary is left exactly as written",
      u["testcases"][0]["steps"] == ["open ${some_bespoke_thing}"]
      and "some_bespoke_thing" in u["values_found"],
      str(u["testcases"][0]["steps"]))
check("nothing is reported as renamed when nothing was",
      not u.get("renamed_variables"), str(u.get("renamed_variables")))

n = PS.canonicalise_variables(draft_of("click a_button", values={}))
check("a draft with no variables at all is untouched",
      n["testcases"][0]["steps"] == ["click a_button"])

i = PS.canonicalise_variables(draft_of(
    "Enter ${test_mobile} in M", values={},
    inputs=[{"name": "test_mobile", "type": "mobile", "required": True}]))
check("an outstanding input is renamed along with the steps",
      i["inputs_needed"][0]["name"] == "mobile", str(i["inputs_needed"]))

check("the vocabulary maps every synonym it declares",
      all(PS.canonical_name_for(syn) == canonical
          for canonical, syns in PS._CANONICAL_VARIABLES.items() for syn in syns))
check("canonical names are themselves stable under a second pass",
      all(PS.canonical_name_for(k) == k for k in PS._CANONICAL_VARIABLES))

# ═══════════════════════════════════════════════════════════════════════════
print("\n[3] THE CONTENT ADDRESS COVERS THE WHOLE REQUEST")

P = "Test the login popup on the product page and enter a mobile number."
check("the same request maps to the same id",
      prompt_source_id(P, "website", 3) == prompt_source_id(P, "website", 3))
check("leading/trailing whitespace does not make a new draft",
      prompt_source_id("  " + P + "  ", "website", 3) == prompt_source_id(P, "website", 3))
check("a different platform is a different draft (this used to collide)",
      prompt_source_id(P, "website", 3) != prompt_source_id(P, "mobilesite", 3))
check("a different testcase count is a different draft",
      prompt_source_id(P, "website", 3) != prompt_source_id(P, "website", 10))
check("a different prompt is a different draft",
      prompt_source_id(P, "website", 3) != prompt_source_id(P + " twice", "website", 3))
check("ids are filesystem-safe and recognisable",
      prompt_source_id(P, "website", 3).startswith("src_")
      and prompt_source_id(P, "website", 3)[4:].isalnum())

# ═══════════════════════════════════════════════════════════════════════════
print("\n[4] AN IDENTICAL REQUEST DOES NOT ASK THE MODEL AGAIN")
#
# The provider is stubbed so the count is exact and no credential is needed.

_calls = {"n": 0}


class _StubProvider:
    name = "stub"
    model = "stub-1"

    def complete_structured(self, *, system, user, schema):
        from ai_flow_builder.llm import Completion

        _calls["n"] += 1
        # Name variables DIFFERENTLY on each call — exactly the drift being
        # defended against. If the cache is bypassed, the second answer differs
        # and the assertions below catch it.
        var = "page_url" if _calls["n"] == 1 else "test_url"
        return Completion(
            data={"testcases": [{"testcase_id": "TC_STUB_01", "title": "stub case",
                                 "steps": [f"Navigate to ${{{var}}}"], "expected": "",
                                 "priority": "High", "classification": "Positive",
                                 "preconditions": []}],
                  "inputs_needed": [], "assumptions": [], "unclear": []},
            provider=self.name, model=self.model,
            usage={"input_tokens": 1, "output_tokens": 1})


_real = PS.draft_testcases.__globals__.get("get_provider")
import ai_flow_builder.llm as _llm  # noqa: E402

_orig_get_provider = _llm.get_provider
_llm.get_provider = lambda *a, **k: _StubProvider()

PROMPT = ("Determinism probe: open https://example.com/thing and check the popup "
          "appears for a signed-out visitor on this page.")
try:
    r1 = c.post("/sources/prompt", json={"prompt": PROMPT, "platform": "website",
                                         "max_testcases": 1})
    check("first request drafts", r1.status_code == 201 and r1.json().get("cached") is False,
          r1.text[:140])
    r2 = c.post("/sources/prompt", json={"prompt": PROMPT, "platform": "website",
                                         "max_testcases": 1})
    check("second identical request is served from the store",
          r2.status_code == 201 and r2.json().get("cached") is True, r2.text[:140])
    check("the model was asked exactly once", _calls["n"] == 1, f"called {_calls['n']}x")
    check("both responses carry the same id",
          r1.json()["source_id"] == r2.json()["source_id"])
    check("both responses describe the same testcases",
          r1.json()["testcases"] == r2.json()["testcases"],
          f"{r1.json()['testcases']} vs {r2.json()['testcases']}")

    r3 = c.post("/sources/prompt", json={"prompt": PROMPT, "platform": "website",
                                         "max_testcases": 1, "redraft": True})
    check("redraft=true does ask again", _calls["n"] == 2, f"called {_calls['n']}x")
    check("a deliberate redraft is not reported as cached",
          r3.json().get("cached") is False, r3.text[:140])
    check("even though the model named the variable differently, the draft "
          "canonicalises to the same parameter",
          set(r3.json()["values_found"]) == set(r1.json()["values_found"]),
          f"{sorted(r3.json()['values_found'])} vs {sorted(r1.json()['values_found'])}")

    r4 = c.post("/sources/prompt", json={"prompt": PROMPT, "platform": "mobilesite",
                                         "max_testcases": 1})
    check("the other platform drafts separately rather than overwriting",
          r4.json()["source_id"] != r1.json()["source_id"] and _calls["n"] == 3,
          f"called {_calls['n']}x")
finally:
    _llm.get_provider = _orig_get_provider
    for _sid in {prompt_source_id(PROMPT, "website", 1),
                 prompt_source_id(PROMPT, "mobilesite", 1)}:
        c.delete(f"/sources/{_sid}")

print("\n" + "=" * 60)
print(f"PASSED: {_passed}  |  FAILED: {_failed}")
print("=" * 60)
sys.exit(1 if _failed else 0)
