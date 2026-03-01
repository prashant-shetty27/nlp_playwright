"""
tests/test_parser.py  —  Parser unit tests for all 25 command types.
Run: python tests/test_parser.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from nlp.parser import parse_step

CASES = [
    # (step_text, expected_type, extra_checks)
    # ── Existing ──────────────────────────────────────────────────────────────
    ("open justdial",                                   "open",                  {"target": "justdial"}),
    ("search for Restaurants",                          "search",                {"text": "Restaurants"}),
    ("wait 3 seconds",                                  "wait",                  {"wait": 3.0}),
    ("wait for result page load",                       "wait_for_result_page_load", {}),
    ('verify text "Restaurants"',                       "verify_text",           {"text": "Restaurants"}),
    ("refresh",                                         "refresh",               {}),
    ('click on element submit_btn',                     "click",                 {"target": "submit_btn"}),
    ('fill "hello" in search_box',                      "fill",                  {"text": "hello", "target": "search_box"}),
    # ── New: Screenshot ───────────────────────────────────────────────────────
    ("take screenshot as home_page",                    "screenshot",            {"target": "home_page"}),
    ("screenshot",                                      "screenshot",            {"target": "capture"}),
    ("capture screenshot",                              "screenshot",            {}),
    # ── New: Scroll ───────────────────────────────────────────────────────────
    ("scroll down",                                     "scroll",                {"count": 500}),
    ("scroll up",                                       "scroll",                {"count": -500}),
    ("scroll down 2 times",                             "scroll",                {"count": 1000}),
    ("scroll down 800",                                 "scroll",                {"count": 800}),
    # ── New: Verify element ───────────────────────────────────────────────────
    ('verify element search_box has text "hello"',      "verify_element_exact",  {"target": "search_box", "text": "hello"}),
    ('verify element search_box contains "hello"',      "verify_element_contains", {"target": "search_box", "text": "hello"}),
    # ── New: Verify exact text (full match) ───────────────────────────────────
    ('verify exact text "Sort by"',                     "verify_exact_text",     {"text": "Sort by"}),
    ('verify exact text "Leads"',                       "verify_exact_text",     {"text": "Leads"}),
    # ── New: Verify multiple global texts ─────────────────────────────────────
    ('verify texts "Restaurants", "Mumbai"',            "verify_multiple_texts", {"text": "Restaurants,Mumbai"}),
    # ── New: Extract ──────────────────────────────────────────────────────────
    ("store text of search_box as my_var",              "extract_text",          {"target": "search_box", "variable_name": "my_var"}),
    ("store page url as current_url",                   "extract_url",           {"variable_name": "current_url"}),
    ("store page title as page_title",                  "extract_title",         {"variable_name": "page_title"}),
    ("store attribute href of link_elem as my_href",    "extract_attribute",     {"target": "link_elem", "attribute": "href", "variable_name": "my_href"}),
    ("store value of input_elem as my_val",             "extract_input",         {"target": "input_elem", "variable_name": "my_val"}),
    ("store count of items as my_count",                "extract_count",         {"target": "items", "variable_name": "my_count"}),
    # ── New: Create variable ──────────────────────────────────────────────────
    ('store "10" as base_count',                        "create_variable",       {"target": "base_count", "text": "10"}),
    ('create variable greeting with value "hi"',        "create_variable",       {"target": "greeting", "text": "hi"}),
    # ── New: Math ─────────────────────────────────────────────────────────────
    ("calculate base_count + 5 as total_count",         "math",                  {"target": "base_count", "text": "+", "values": ["5"], "variable_name": "total_count"}),
    ("calculate 3 * 4 as result",                       "math",                  {"target": "3", "text": "*", "values": ["4"], "variable_name": "result"}),
    # ── New: Verify variable contains ─────────────────────────────────────────
    ('verify stored total_count contains "15"',         "verify_var_contains",   {"target": "total_count", "text": "15"}),
    ('verify my_var contains "hello"',                  "verify_var_contains",   {"target": "my_var", "text": "hello"}),
]

ok = fail = 0
for step, expected_type, checks in CASES:
    try:
        cmd = parse_step(step)
        errors = []
        if cmd.type != expected_type:
            errors.append(f"type={cmd.type!r} (expected {expected_type!r})")
        for field, expected_val in checks.items():
            actual = getattr(cmd, field)
            if actual != expected_val:
                errors.append(f"{field}={actual!r} (expected {expected_val!r})")

        if errors:
            print(f"⚠️  [{cmd.type:26s}] {step}")
            for e in errors:
                print(f"       {e}")
            fail += 1
        else:
            print(f"✅  [{cmd.type:26s}] {step}")
            ok += 1
    except Exception as e:
        print(f"❌  [ERROR              ] {step} -> {e}")
        fail += 1

print(f"\n{'='*60}")
print(f"PASSED: {ok}/{ok+fail}  |  FAILED: {fail}")
sys.exit(0 if fail == 0 else 1)
