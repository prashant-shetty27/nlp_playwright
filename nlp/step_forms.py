"""
Form definitions for editing a step without knowing its grammar.

The editor's pencil used to open the step as one line of text: to add a
second popup to a swipe you had to already know that the wording is
", closing <element>". A form built from the step TYPE shows every part the
step can carry — direction, element, popups, counts — as its own field, and
the text is composed from the fields, so nobody types grammar.

Each spec is:
  title   — what the step does, in plain words
  fields  — the parts, in the order they read:
            key      value name (see `decompose` for where it comes from)
            label    field caption
            kind     locator | locators | number | text | choice | url
            help     one line under the field
            choices  for kind=choice
            required True when the step is meaningless without it
            default  what an empty field composes as
  compose — values dict -> step text, in the parser's own wording

Only step types with a spec get a form; the rest keep the text editor.
Keep `compose` in step with nlp/parser.py — a form that writes a line the
parser rejects is worse than no form, so tests/unit/test_step_forms.py
round-trips every spec through parse_step.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Callable

SWIPE_SPANS = ["bottom to top", "top to bottom", "bottom to middle",
               "middle to top", "top to middle", "middle to bottom"]


def _span_words(span: str) -> str:
    """'bottom_top' (runner form) -> 'bottom to top' (step form)."""
    return {"bottom_top": "bottom to top", "top_bottom": "top to bottom"}.get(
        span, span.replace("_", " to "))


def _num(v: Any, default: Any) -> str:
    if v in (None, ""):
        return str(default)
    try:
        f = float(v)
        return str(int(f)) if f.is_integer() else str(f)
    except (TypeError, ValueError):
        return str(v)


def _closing(v: list[str] | None) -> str:
    names = [n for n in (v or []) if n]
    return f", closing {' and '.join(names)}" if names else ""


def _swipe_until(v: dict) -> str:
    return (f"swipe {v.get('span') or 'bottom to top'} until element {v['target']} is visible"
            f"{_closing(v.get('closers'))}, max {_num(v.get('count'), 15)} times,"
            f" wait {_num(v.get('wait'), 1)}")


def _swipe_screen(v: dict) -> str:
    n = int(float(v.get("count") or 1))
    out = f"swipe {v.get('span') or 'bottom to top'}"
    if n != 1:
        out += f" {n} times"
    return out


def _scroll_until(v: dict) -> str:
    return (f"scroll until element {v['target']} visible, scroll by {_num(v.get('pixels'), 500)} pixels,"
            f" scroll count {_num(v.get('count'), 10)}, scroll wait {_num(v.get('wait'), 1)}")


def _if_visible(verb: str) -> Callable[[dict], str]:
    def go(v: dict) -> str:
        out = f"{verb} if visible {v['target']}"
        if v.get("wait") not in (None, ""):
            out += f" wait {_num(v['wait'], 0)} seconds"
        return out
    return go


def _fill(v: dict) -> str:
    return f'type "{v.get("text", "")}" into {v["target"]}'


def _wait_until_visible(v: dict) -> str:
    out = f"wait until element {v['target']} is visible"
    if v.get("timeout") not in (None, ""):
        out += f" up to {_num(v['timeout'], 30)} seconds"
    return out


LOC = "locator"
FORMS: dict[str, dict] = {
    "swipe_until_visible": {
        "title": "Swipe until an element is visible",
        "fields": [
            {"key": "span", "label": "Swipe direction", "kind": "choice",
             "choices": SWIPE_SPANS, "default": "bottom to top",
             "help": "bottom to top = finger drags up = page moves down"},
            {"key": "target", "label": "Stop when this element is visible", "kind": LOC,
             "required": True},
            {"key": "closers", "label": "Popups to close if they appear", "kind": "locators",
             "help": "Any of these — each is closed only when it shows; a popup that never "
                     "appears is ignored and the step still passes"},
            {"key": "count", "label": "Maximum swipes", "kind": "number", "default": 15},
            {"key": "wait", "label": "Wait between swipes (seconds)", "kind": "number",
             "default": 1},
        ],
        "compose": _swipe_until,
    },
    "swipe_screen": {
        "title": "Swipe the screen",
        "fields": [
            {"key": "span", "label": "Swipe direction", "kind": "choice",
             "choices": SWIPE_SPANS, "default": "bottom to top"},
            {"key": "count", "label": "How many times", "kind": "number", "default": 1},
        ],
        "compose": _swipe_screen,
    },
    "scroll_until_element_visible": {
        "title": "Scroll until an element is visible",
        "fields": [
            {"key": "target", "label": "Stop when this element is visible", "kind": LOC,
             "required": True},
            {"key": "pixels", "label": "Pixels per scroll", "kind": "number", "default": 500},
            {"key": "count", "label": "Maximum scrolls", "kind": "number", "default": 10},
            {"key": "wait", "label": "Wait between scrolls (seconds)", "kind": "number",
             "default": 1},
        ],
        "compose": _scroll_until,
    },
    "scroll_to": {
        "title": "Scroll to an element",
        "fields": [{"key": "target", "label": "Element", "kind": LOC, "required": True}],
        "compose": lambda v: f"scroll to {v['target']}",
    },
    "click": {
        "title": "Click an element",
        "fields": [{"key": "target", "label": "Element", "kind": LOC, "required": True}],
        "compose": lambda v: f"click {v['target']}",
    },
    "tap": {
        "title": "Tap an element",
        "fields": [{"key": "target", "label": "Element", "kind": LOC, "required": True}],
        "compose": lambda v: f"tap {v['target']}",
    },
    "tap_if_visible": {
        "title": "Click an element only if it is showing",
        "fields": [
            {"key": "target", "label": "Element", "kind": LOC, "required": True,
             "help": "Skipped silently when the element is not on the page"},
            {"key": "wait", "label": "Wait after clicking (seconds, optional)", "kind": "number"},
        ],
        "compose": _if_visible("click"),
    },
    "fill": {
        "title": "Type into a field",
        "fields": [
            {"key": "text", "label": "Text to type", "kind": "text", "required": True,
             "help": "${variable} references are allowed"},
            {"key": "target", "label": "Field", "kind": LOC, "required": True},
        ],
        "compose": _fill,
    },
    "wait": {
        "title": "Wait a fixed time",
        "fields": [{"key": "wait", "label": "Seconds", "kind": "number", "default": 1,
                    "required": True,
                    "help": "Prefer 'wait until element … is visible' when there is "
                            "something on the page to wait for"}],
        "compose": lambda v: f"wait {_num(v.get('wait'), 1)} seconds",
    },
    "wait_until_visible": {
        "title": "Wait until an element is visible",
        "fields": [
            {"key": "target", "label": "Element", "kind": LOC, "required": True},
            {"key": "timeout", "label": "Give up after (seconds, optional)", "kind": "number",
             "help": "Leave empty for the default action timeout"},
        ],
        "compose": _wait_until_visible,
    },
    "wait_until_not_visible": {
        "title": "Wait until an element is gone",
        "fields": [{"key": "target", "label": "Element", "kind": LOC, "required": True}],
        "compose": lambda v: f"wait until element {v['target']} is not visible",
    },
    "verify_element_visible": {
        "title": "Check an element is visible",
        "fields": [{"key": "target", "label": "Element", "kind": LOC, "required": True}],
        "compose": lambda v: f"verify element {v['target']} is visible",
    },
    "verify_inside": {
        "title": "Check an element sits inside another (icon on a photo)",
        "fields": [{"key": "target", "label": "Element (e.g. the icon)", "kind": LOC, "required": True},
                   {"key": "container", "label": "Inside (e.g. the photo)", "kind": LOC, "required": True,
                    "help": "For a picture the check uses the photo as drawn, not its frame."}],
        "compose": lambda v: f"verify element {v['target']} is inside {v['container']}",
    },
    "verify_inside_every": {
        "title": "Check an element sits inside EVERY one of another (icon on every photo)",
        "fields": [{"key": "target", "label": "Element (e.g. the icon)", "kind": LOC, "required": True},
                   {"key": "container", "label": "Inside every (e.g. photo tile)", "kind": LOC, "required": True}],
        "compose": lambda v: f"verify element {v['target']} is inside every {v['container']}",
    },
    "verify_same_place_every": {
        "title": "Check an element is at the same place on every one of another",
        "fields": [{"key": "target", "label": "Element (e.g. the icon)", "kind": LOC, "required": True},
                   {"key": "container", "label": "On every (e.g. photo tile)", "kind": LOC, "required": True}],
        "compose": lambda v: f"verify element {v['target']} is at the same place in every {v['container']}",
    },
    "verify_same_size": {
        "title": "Check two elements are the same size",
        "fields": [{"key": "target", "label": "Element", "kind": LOC, "required": True},
                   {"key": "container", "label": "Same size as", "kind": LOC, "required": True}],
        "compose": lambda v: f"verify element {v['target']} is the same size as {v['container']}",
    },
    "store_position": {
        "title": "Store where an element sits on another (size and offsets)",
        "fields": [{"key": "target", "label": "Element (e.g. the icon)", "kind": LOC, "required": True},
                   {"key": "container", "label": "On (e.g. the photo)", "kind": LOC, "required": True},
                   {"key": "variable", "label": "Store as", "kind": "variable", "required": True}],
        "compose": lambda v: f"store position of {v['target']} in {v['container']} as {v['variable']}",
    },
    "verify_element_not_visible": {
        "title": "Check an element is NOT visible",
        "fields": [{"key": "target", "label": "Element", "kind": LOC, "required": True}],
        "compose": lambda v: f"verify element {v['target']} is not visible",
    },
    "verify_element_contains": {
        "title": "Check an element's text contains",
        "fields": [
            {"key": "target", "label": "Element", "kind": LOC, "required": True},
            {"key": "text", "label": "Text it should contain", "kind": "text", "required": True},
        ],
        "compose": lambda v: f'verify element {v["target"]} contains "{v.get("text", "")}"',
    },
    "verify_element_exact": {
        "title": "Check an element's exact text",
        "fields": [
            {"key": "target", "label": "Element", "kind": LOC, "required": True},
            {"key": "text", "label": "Exact text", "kind": "text", "required": True},
        ],
        "compose": lambda v: f'verify element {v["target"]} has text "{v.get("text", "")}"',
    },
    "select_option": {
        "title": "Choose a dropdown option",
        "fields": [
            {"key": "text", "label": "Option (value or label)", "kind": "text", "required": True},
            {"key": "target", "label": "Dropdown", "kind": LOC, "required": True},
        ],
        "compose": lambda v: f'select option "{v.get("text", "")}" in {v["target"]}',
    },
    "open": {
        "title": "Open a URL",
        "fields": [{"key": "target", "label": "URL", "kind": "url", "required": True}],
        "compose": lambda v: f"open {v['target']}",
    },
    "verify_text": {
        "title": "Check the page shows text",
        "fields": [{"key": "text", "label": "Text that must be on the page", "kind": "text",
                    "required": True}],
        "compose": lambda v: f'verify text "{v.get("text", "")}" on page',
    },
    "scroll": {
        "title": "Scroll the page",
        "fields": [
            {"key": "direction", "label": "Direction", "kind": "choice", "choices": ["down", "up"],
             "default": "down"},
            {"key": "pixels", "label": "Pixels", "kind": "number", "default": 500},
        ],
        "compose": lambda v: f"scroll {v.get('direction') or 'down'} {_num(v.get('pixels'), 500)}",
    },
    "refresh": {"title": "Refresh the page", "fields": [], "compose": lambda v: "refresh page"},
    "wait_page_load": {"title": "Wait for the page to load", "fields": [],
                       "compose": lambda v: "wait for page to load"},
    "delete_all_cookies": {"title": "Delete all cookies", "fields": [],
                           "compose": lambda v: "delete all cookies"},
    "extract_text": {
        "title": "Store an element's text in a variable",
        "fields": [
            {"key": "target", "label": "Element", "kind": LOC, "required": True},
            {"key": "variable_name", "label": "Variable name", "kind": "variable", "required": True,
             "help": "Use it later as ${name}"},
        ],
        "compose": lambda v: f"store text of {v['target']} as {v['variable_name']}",
    },
    "verify_var_equals": {
        "title": "Check a stored variable equals",
        "fields": [
            {"key": "target", "label": "Variable name", "kind": "variable", "required": True},
            {"key": "text", "label": "Expected value", "kind": "text", "required": True},
        ],
        "compose": lambda v: f'verify stored {v["target"]} equals "{v.get("text", "")}"',
    },
    "verify_var_contains": {
        "title": "Check a stored variable contains",
        "fields": [
            {"key": "target", "label": "Variable name", "kind": "variable", "required": True},
            {"key": "text", "label": "Text it should contain", "kind": "text", "required": True},
        ],
        "compose": lambda v: f'verify stored {v["target"]} contains "{v.get("text", "")}"',
    },
    "run_javascript": {
        "title": "Run JavaScript on the page",
        "fields": [{"key": "text", "label": "JavaScript", "kind": "text", "required": True}],
        "compose": lambda v: f'run javascript "{v.get("text", "")}"',
    },
    "js_click": {
        "title": "Click an element via JavaScript",
        "fields": [{"key": "target", "label": "Element", "kind": LOC, "required": True,
                    "help": "For elements a normal click cannot reach (covered, off-screen)"}],
        "compose": lambda v: f"js click {v['target']}",
    },
    "press_key": {
        "title": "Press a keyboard key",
        "fields": [{"key": "text", "label": "Key", "kind": "choice",
                    "choices": ["Enter", "Tab", "Escape", "Space", "Backspace", "ArrowDown",
                                "ArrowUp", "ArrowLeft", "ArrowRight", "PageDown", "PageUp"],
                    "default": "Enter"}],
        "compose": lambda v: f"press key {v.get('text') or 'Enter'}",
    },
    "type_focused": {
        "title": "Type into whatever has focus",
        "fields": [{"key": "text", "label": "Text to type", "kind": "text", "required": True}],
        "compose": lambda v: f'type "{v.get("text", "")}" into focused field',
    },
    "call_reusable": {
        "title": "Call a step group",
        "fields": [{"key": "target", "label": "Step group", "kind": "group", "required": True}],
        "compose": lambda v: f"call {v['target']}",
    },
}

#: Reading order for the Action dropdown: what a tester reaches for most.
FORM_ORDER = [
    "open", "click", "tap_if_visible", "fill", "type_focused", "select_option", "clear_field",
    "press_key", "scroll", "scroll_to", "scroll_until_element_visible", "swipe_screen",
    "swipe_until_visible", "wait", "wait_until_visible", "wait_until_not_visible",
    "wait_page_load", "verify_element_visible", "verify_element_not_visible",
    "verify_inside", "verify_inside_every", "verify_same_place_every", "verify_same_size", "store_position",
    "verify_element_contains", "verify_element_exact", "verify_text", "extract_text",
    "verify_var_equals", "verify_var_contains", "call_reusable", "js_click",
    "run_javascript", "refresh", "delete_all_cookies", "tap",
]


def catalogue() -> list[dict]:
    """Every form, in reading order — for the editor's Action dropdown."""
    order = [t for t in FORM_ORDER if t in FORMS] + [t for t in FORMS if t not in FORM_ORDER]
    return [{"type": t, "title": FORMS[t]["title"], "fields": FORMS[t]["fields"]} for t in order]


def decompose(parsed: Any) -> dict:
    """Current field values for a parsed Command (dataclass or dict)."""
    d = dataclasses.asdict(parsed) if dataclasses.is_dataclass(parsed) else dict(parsed or {})
    t = d.get("type", "")
    vals = d.get("values") if isinstance(d.get("values"), list) else []
    out: dict[str, Any] = {"target": d.get("target") or "", "text": d.get("text") or "",
                           "count": d.get("count"), "wait": d.get("wait")}
    if t in ("swipe_until_visible", "swipe_screen"):
        out["span"] = _span_words(vals[0]) if vals else "bottom to top"
        out["closers"] = list(vals[1:]) if t == "swipe_until_visible" else []
    if t == "scroll_until_element_visible":
        out["pixels"] = vals[0] if vals else 500
    if t == "wait_until_visible":
        out["timeout"] = d.get("wait")
    if t == "scroll":
        n = int(d.get("count") or 500)
        out["direction"] = "up" if n < 0 else "down"
        out["pixels"] = abs(n)
    if t in ("verify_inside", "verify_inside_every", "verify_same_place_every",
             "verify_same_size", "store_position"):
        out["container"] = vals[0] if vals else ""
        out["variable"] = d.get("variable_name") or ""
    out["variable_name"] = d.get("variable_name") or ""
    return out


def form_for(parsed: Any) -> dict | None:
    """The form spec plus current values, or None when this type has no form."""
    d = dataclasses.asdict(parsed) if dataclasses.is_dataclass(parsed) else dict(parsed or {})
    spec = FORMS.get(d.get("type", ""))
    if not spec:
        return None
    return {"type": d["type"], "title": spec["title"], "fields": spec["fields"],
            "values": decompose(d)}


def compose(step_type: str, values: dict) -> str:
    """Step text for `values` — raises KeyError/ValueError on a missing required part."""
    spec = FORMS[step_type]
    v = dict(values or {})
    for f in spec["fields"]:
        if f.get("required") and not str(v.get(f["key"]) or "").strip():
            raise ValueError(f"{f['label']} is required")
        if f["kind"] in ("locator", "url", "text", "variable", "group") and isinstance(v.get(f["key"]), str):
            v[f["key"]] = v[f["key"]].strip()
    return spec["compose"](v)
