"""Everyday web steps: hover, right click, drag, checkboxes, state, URL, attributes, counts, keys."""
import pytest

from nlp.parser import parse_step
from execution.action_service import key_combo


@pytest.mark.parametrize("step,ctype,extra", [
    ("hover over search_box", "hover", {"target": "search_box"}),
    ("mouse over menu", "hover", {"target": "menu"}),
    ("right click card", "right_click", {"target": "card"}),
    ("double click card", "double_tap", {"target": "card"}),
    ("drag card_1 to done_column", "drag_drop", {"target": "card_1", "values": ["done_column"]}),
    ("check terms", "check", {"target": "terms"}),
    ("untick remember_me", "uncheck", {"target": "remember_me"}),
    ("verify element terms is checked", "verify_state", {"values": ["checked", ""]}),
    ("verify submit is not enabled", "verify_state", {"values": ["enabled", "not"]}),
    ('verify url contains "photos"', "verify_url", {"values": ["contains"], "text": "photos"}),
    ('verify current url does not contain "login"', "verify_url", {"values": ["does not contain"]}),
    ('wait for url to contain "step=2" within 20 seconds', "wait_for_url", {"wait": 20.0}),
    ("store current url as here", "extract_url", {"variable_name": "here"}),
    ('verify attribute href of logo ends with "/"', "verify_attribute", {"attribute": "href", "values": ["ends with"]}),
    ('verify value of name is "Ravi"', "verify_attribute", {"attribute": "value", "text": "Ravi"}),
    ("verify value of name is empty", "verify_attribute", {"values": ["is empty"]}),
    ('verify box has placeholder "Search"', "verify_attribute", {"attribute": "placeholder"}),
    ("verify count of results is 5", "verify_count", {"values": ["is"], "count": 5}),
    ("verify results count is at least 3", "verify_count", {"values": ["at least"], "count": 3}),
    ("press key ctrl+a", "press_key", {"text": "ctrl+a"}),
    ("press keys cmd+shift+k", "press_key", {"text": "cmd+shift+k"}),
    ('click text "Search"', "tap_text", {"text": "Search"}),
])
def test_parse(step, ctype, extra):
    c = parse_step(step)
    assert c.type == ctype
    for k, v in extra.items():
        assert getattr(c, k) == v, (k, getattr(c, k))


def test_tab_count_not_taken_for_an_element():
    for step in ("verify tab count is 3", "verify number of tabs is 2", "verify 2 tabs are open",
                 "verify window count is 1"):
        c = parse_step(step)
        assert c.type == "verify_tab_count", step


def test_variable_checks_unchanged():
    assert parse_step('verify stored x contains "a"').type == "verify_var_contains"
    assert parse_step('verify price contains "1"').type == "verify_var_contains"


@pytest.mark.parametrize("raw,combo", [
    ("ctrl+a", "Control+a"), ("Control+A", "Control+a"), ("cmd+shift+k", "Meta+Shift+k"),
    ("shift+tab", "Shift+Tab"), ("F5", "F5"), ("Enter", "Enter"), ("alt + f4", "Alt+F4"),
])
def test_key_combo(raw, combo):
    assert key_combo(raw) == combo
