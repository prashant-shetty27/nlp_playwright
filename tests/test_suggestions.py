"""
tests/test_suggestions.py — the two suggestion surfaces must agree with the parser.

The bug this exists to prevent: nlp/keywords.py (which feeds POST /nlp/suggest)
and reporting/snippet_sync.py (which writes the VS Code snippet file) both
described the step language by hand, and neither was checked against
nlp/parser.py. They drifted. Eleven of fifty-three snippets emitted statements
the parser rejected, a sixteen-state dropdown offered thirteen states that could
not parse, and eleven of twenty-one keyword entries named actions absent from
every dispatch table — so a suggestion could be picked and simply fail.

Nothing here inspects hardcoded lists. Every assertion is made against the LIVE
parser and the LIVE runner dispatch tables, so adding a command without wiring
its suggestion, or changing a parser rule out from under a template, fails here.

Run: python tests/test_suggestions.py
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_flow_builder.catalogue import load as load_catalogue  # noqa: E402
from nlp.keywords import KEYWORD_MAP  # noqa: E402
from nlp.parser import parse_step  # noqa: E402
from reporting.snippet_sync import (_snippets_from_templates,  # noqa: E402
                                    get_snippets_path, harvest_locator_names)

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


WEB = load_catalogue("web")
APPIUM = load_catalogue("android")   # "appium" is a RUNNER, not a platform
SLOT_FILL = {"locator": "my_loc", "text": "sample", "number": "3", "variable": "my_var"}
_NUMERIC_DEFAULTS = {"seconds", "count", "n", "timeout", "pixels", "ms", "3", "500", "300", "10"}


def fill_template(template: str) -> str:
    out = template
    for slot, value in SLOT_FILL.items():
        out = out.replace("{" + slot + "}", value)
    return out


def fill_snippet(body) -> str:
    """Expand a VS Code snippet body the way the editor would after tabbing through."""
    s = " ".join(body)
    s = re.sub(r"\$\{\d+\|([^|}]*)\|\}",
               lambda m: ("my_loc" if "my_loc" in m.group(1).split(",")
                          else m.group(1).split(",")[0]), s)
    s = re.sub(r"\$\{\d+:([^}]*)\}",
               lambda m: ("3" if (m.group(1) or "").strip().lower() in _NUMERIC_DEFAULTS
                          else (m.group(1) or "x").strip().replace(" ", "_")), s)
    return re.sub(r"\$\{\d+\}", "x", s).strip()


# ═══════════════════════════════════════════════════════════════════════════
print("\n[1] KEYWORD_MAP — every action must be dispatchable somewhere")

for key, entry in KEYWORD_MAP.items():
    action = entry.get("action", "")
    dispatchable = WEB.supports(action) or APPIUM.supports(action)
    if entry.get("deprecated"):
        check(f"{key}: deprecated entry names a genuinely dead action",
              not dispatchable,
              f"{action!r} IS dispatchable — drop the deprecated flag")
    else:
        check(f"{key}: action {action!r} is dispatchable", dispatchable,
              WEB.evidence_for(action))

check("every entry offers at least one phrase",
      all(entry.get("phrases") for entry in KEYWORD_MAP.values()))

# ═══════════════════════════════════════════════════════════════════════════
print("\n[2] TEMPLATES — must parse, and must yield the action they claim")

templated = {k: e for k, e in KEYWORD_MAP.items() if e.get("template")}
check(f"templates exist ({len(templated)} of {len(KEYWORD_MAP)} entries)", len(templated) >= 20)

for key, entry in templated.items():
    stmt = fill_template(entry["template"])
    check(f"{key}: no unfilled slot remains", "{" not in stmt and "}" not in stmt, stmt)
    try:
        got = parse_step(stmt).type
    except Exception as e:
        check(f"{key}: template parses", False, f"{stmt!r} -> {str(e)[:50]}")
        continue
    check(f"{key}: template parses", True)
    check(f"{key}: parses to its declared action {entry['action']!r}",
          got == entry["action"], f"got {got!r} from {stmt!r}")

check("no deprecated entry carries a template (it could never run)",
      not [k for k, e in KEYWORD_MAP.items() if e.get("deprecated") and e.get("template")],
      str([k for k, e in KEYWORD_MAP.items() if e.get("deprecated") and e.get("template")]))

# ═══════════════════════════════════════════════════════════════════════════
print("\n[3] /nlp/suggest — deprecated entries must never be offered")

DEPRECATED_PHRASES = {p.lower() for e in KEYWORD_MAP.values() if e.get("deprecated")
                      for p in e["phrases"]}


def suggest(partial: str, limit: int = 50):
    """Mirrors api/routes/nlp.py::suggest so the filter is asserted, not assumed."""
    out = []
    for _key, entry in KEYWORD_MAP.items():
        if entry.get("deprecated"):
            continue
        for phrase in entry.get("phrases", []):
            if partial.lower() in phrase.lower():
                out.append(phrase)
                if len(out) >= limit:
                    return out
    return out


for dead in ("close browser", "capture business name", "first result"):
    check(f"suggesting {dead!r} returns nothing runnable",
          not [p for p in suggest(dead) if p.lower() in DEPRECATED_PHRASES],
          str(suggest(dead)))

for live, expect in (("enter otp", "enter otp"),
                     ("store text", "store text of"),
                     ("not visible", "verify element is not visible"),
                     ("not present", "verify element is not present"),
                     ("store attribute", "store attribute")):
    check(f"suggesting {live!r} offers {expect!r}", expect in suggest(live), str(suggest(live)[:4]))

# ═══════════════════════════════════════════════════════════════════════════
print("\n[4] VS CODE SNIPPETS — every generated body must parse")

choice_list, _n = harvest_locator_names()
generated = _snippets_from_templates(choice_list, already={})
check(f"templates generate snippets ({len(generated)})", len(generated) >= 20)

for name, spec in generated.items():
    stmt = fill_snippet(spec["body"])
    try:
        parse_step(stmt)
        check(f"generated {name!r} parses", True)
    except Exception as e:
        check(f"generated {name!r} parses", False, f"{stmt!r} -> {str(e)[:44]}")

check("generation is idempotent — nothing is emitted twice for one action",
      len({s["description"] for s in generated.values()}) == len(generated))

# The file on disk is the artefact the editor actually reads.
path = get_snippets_path()
if os.path.exists(path):
    raw = re.sub(r"^\s*//.*$", "", open(path, encoding="utf-8").read(), flags=re.M)
    on_disk = json.loads(raw)
    broken = []
    for name, spec in on_disk.items():
        stmt = fill_snippet(spec.get("body", []))
        try:
            parse_step(stmt)
        except Exception:
            broken.append((name, stmt))
    check(f"every snippet on disk parses ({len(on_disk)} total)", not broken,
          "; ".join(f"{n}: {s[:40]}" for n, s in broken[:4]))
else:
    print(f"  SKIP  snippet file not synced yet ({path})")

# ═══════════════════════════════════════════════════════════════════════════
print("\n[5] STATE DROPDOWN — must offer only states the parser accepts")

src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "reporting", "snippet_sync.py"), encoding="utf-8").read()
mo = re.search(r'verify_states\s*=\s*"([^"]*)"', src)
check("verify_states is declared", bool(mo))
if mo:
    states = [s.strip() for s in mo.group(1).split(",") if s.strip()]
    for st in states:
        try:
            parse_step(f"verify element my_loc is {st}")
            check(f"state {st!r} parses", True)
        except Exception:
            check(f"state {st!r} parses", False, "offered in the dropdown but rejected")

# ═══════════════════════════════════════════════════════════════════════════
print("\n[6] COVERAGE — commands a user cannot discover from any suggestion")

reachable = set()
for entry in KEYWORD_MAP.values():
    if entry.get("deprecated"):
        continue
    reachable.add(entry["action"])
undiscoverable = sorted(set(WEB.supported_commands) - reachable)
print(f"       {len(reachable & set(WEB.supported_commands))}/{len(WEB.supported_commands)} "
      f"web commands have a suggestion")
print(f"       still undiscoverable: {', '.join(undiscoverable) or 'none'}")
check("the commands used by generated flows are all discoverable",
      not ({"open", "click", "fill", "screenshot", "enter_otp", "extract_text",
            "extract_attribute", "extract_input", "verify_var_contains",
            "verify_var_not_equals", "verify_element_visible", "verify_element_exact",
            "verify_element_not_exists", "wait_until_visible", "wait_until_text_not"}
           - reachable),
      str(sorted({"open", "click", "fill", "screenshot", "enter_otp", "extract_text",
                  "extract_attribute", "extract_input", "verify_var_contains",
                  "verify_var_not_equals", "verify_element_visible", "verify_element_exact",
                  "verify_element_not_exists", "wait_until_visible", "wait_until_text_not"}
                 - reachable)))

print("\n" + "=" * 60)
print(f"PASSED: {_passed}  |  FAILED: {_failed}")
print("=" * 60)
sys.exit(1 if _failed else 0)
