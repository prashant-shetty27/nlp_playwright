"""Every step form composes a line the parser reads back as the same type."""
import dataclasses

import pytest

from nlp.parser import parse_step
from nlp.step_forms import FORMS, compose, decompose, form_for

SAMPLE = {"target": "some_el", "text": "hello", "closers": ["pop_a", "pop_b"], "container": "photo_el",
          "span": "top to bottom", "count": 7, "wait": 2, "pixels": 300, "timeout": 40,
          "variable_name": "my_var", "variable": "my_var", "direction": "up"}


@pytest.mark.parametrize("step_type", sorted(FORMS))
def test_round_trip(step_type):
    text = compose(step_type, SAMPLE)
    cmd = parse_step(text)
    assert cmd.type == step_type, text
    # Composing again from the parsed values gives the same line (the parser
    # may normalise a value's case, e.g. key names).
    again = compose(step_type, decompose(cmd))
    assert again.lower() == text.lower()


def test_swipe_closers_and_span():
    cmd = parse_step("swipe bottom to top until element gvs is visible, closing a, closing b, max 3 times, wait 0.5")
    vals = decompose(cmd)
    assert vals["closers"] == ["a", "b"] and vals["span"] == "bottom to top"
    assert compose("swipe_until_visible", vals) == \
        "swipe bottom to top until element gvs is visible, closing a and b, max 3 times, wait 0.5"


def test_required_field():
    with pytest.raises(ValueError):
        compose("click", {"target": ""})


def test_no_form_for_unknown_type():
    assert form_for({"type": "go_forward"}) is None
