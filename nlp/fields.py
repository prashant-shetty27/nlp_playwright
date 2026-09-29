"""
nlp/fields.py — which part of a step is a locator, a value, a variable, a file.

Two consumers need this and must not disagree:

  tools/flow_lint.py  decides whether `target` names a locator it should check
                      against the database, a variable, or free text.
  the step editor      decides which fragments of a written step are directly
                      clickable, and what to offer when one is clicked.

The linter grew these sets first. Left there, the editor would have had to keep
a second copy — and the moment a command was added to one and not the other, the
editor would offer the wrong picker for a field the linter was checking as
something else.

segment() turns a step into a list of pieces, marking each editable one with a
role, so a caller can render `click maybe_later_link` with the locator clickable
without knowing anything about the grammar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Command types whose `target` names a locator in the element database.
TARGET_IS_LOCATOR = {
    "click", "tap", "double_tap", "long_press", "fill",
    "tap_if_visible", "click_if_visible", "fill_if_visible", "verify_if_visible",
    "double_tap_if_visible", "long_press_if_visible", "store_text_if_visible",
    "click_if_exists", "tap_if_exists", "fill_if_exists", "type_if_exists",
    "verify_element_exists", "verify_element_not_exists",
    "verify_element_exact", "verify_element_contains",
    "extract_text", "store_text", "extract_attribute", "extract_input", "extract_count",
    "wait_for_element", "scroll_to", "scroll_until_element_visible", "clear",
    "verify_element_visible", "verify_element_not_visible", "wait_until_visible",
    "wait_until_text_not", "enter_otp",
    "js_click", "js_scroll_to", "js_type", "js_set_value", "js_focus", "js_submit",
    "js_dispatch",
    "swipe_until_visible", "wait_until_not_visible", "select_option",
    "clear_field", "scroll_element_x",
    "verify_element_starts", "verify_element_ends", "verify_element_matches",
    "verify_element_not_contains", "upload_file", "switch_iframe", "tap",
    "double_tap", "long_press", "wait_for_element", "verify_element_exists",
}

#: Command types whose `target` is a variable name, not a locator.
TARGET_IS_VARIABLE = {"create_variable", "verify_var_contains", "verify_var_not_equals", "verify_var_equals",
                      "extract_json",
                      "verify_recommended_order_api", "extract_regex",
                      "verify_var_compare"}

#: Command types that read a file path from `text`.
TEXT_IS_FILE = {"read_excel_cell", "read_excel_row", "read_csv_cell"}

#: Command types whose `target` is a URL.
TARGET_IS_URL = {"open", "goto", "navigate"}

#: An unfilled slot in a suggestion template: `click {locator}`.
#:
#: The lookbehind matters. Without it this also matched the "{justdial_url}"
#: INSIDE "${justdial_url}", so a variable reference rendered as a fixed "$"
#: followed by a slot. Filling that slot produced "open $baldev engineering" —
#: the dollar left stranded, the variable destroyed, and the run failing on a
#: step that looked right on screen.
SLOT_RE = re.compile(r"(?<!\$)\{(\w+)\}")

#: Slot name -> what to offer when it is clicked.
SLOT_ROLE = {
    "locator": "locator", "element": "locator",
    "variable": "variable", "var": "variable",
    "number": "number", "seconds": "number", "count": "number", "n": "number",
    "text": "text", "value": "text", "url": "url", "attribute": "text",
}


@dataclass
class Segment:
    """One piece of a rendered step."""

    text: str
    #: "fixed" — plain words, not editable.
    #: "slot"  — an unfilled {placeholder} from a template.
    #: "value" — a filled-in value that can be changed on its own.
    kind: str = "fixed"
    #: For slot/value: what the editor should offer — locator | variable | number
    #: | text | url | file.
    role: str = ""
    #: Character range in the original step, so an edit can be spliced back in.
    start: int = 0
    end: int = 0
    #: For a slot, its declared name ("locator" in `{locator}`).
    slot: str = ""


#: Commands whose values[] holds element names after a fixed prefix:
#: {command type: index of the first element name}. swipe_until_visible keeps
#: the swipe span at values[0] and the closers after it.
EXTRA_LOCATORS = {"swipe_until_visible": 1}


def _role_for_target(command_type: str) -> str:
    if command_type in TARGET_IS_LOCATOR:
        return "locator"
    if command_type in TARGET_IS_VARIABLE:
        return "variable"
    if command_type in TARGET_IS_URL:
        return "url"
    return "text"


def _find(haystack: str, needle: str, taken: list[tuple[int, int]],
          whole_number: bool = False, whole_word: bool = False) -> tuple[int, int] | None:
    """
    Locate `needle` in `haystack`, skipping regions already claimed.

    whole_number: the match must not be part of a longer digit run. Without it
    "wait 1" claimed the "1" inside "scroll count 15", and the editor drew the
    count as an editable "1" followed by a stray "5".
    """
    if not needle:
        return None
    start = 0
    while True:
        i = haystack.find(needle, start)
        if i < 0:
            return None
        j = i + len(needle)
        inside_digits = whole_number and (
            (i > 0 and haystack[i - 1].isdigit())
            or (j < len(haystack) and (haystack[j].isdigit() or haystack[j] == ".")))
        # A short name must not be found inside another word: "s" in
        # "string", "up" in "upload", "a" in "as".
        def _wordch(c: str) -> bool:
            return c.isalnum() or c == "_"
        inside_word = whole_word and (
            (i > 0 and _wordch(haystack[i - 1]))
            or (j < len(haystack) and _wordch(haystack[j])))
        if not inside_digits and not inside_word and not any(i < e and s < j for s, e in taken):
            return i, j
        start = i + 1


def segment(step: str, parsed: dict | None = None) -> list[Segment]:
    """
    Split `step` into fixed words and separately-editable values.

    `parsed` is the Command dict from nlp.parser.parse_step, when the step
    parses. Without it only unfilled {slots} are marked — which is exactly the
    right behaviour for a template that has just been inserted and does not
    parse yet.

    Never raises: an unparseable step comes back as a single fixed segment, so
    the editor can always render something.
    """
    step = step or ""
    marks: list[tuple[int, int, str, str, str]] = []   # start, end, kind, role, slot
    taken: list[tuple[int, int]] = []

    # 1. Unfilled template slots always win — they are literally asking to be filled.
    for mo in SLOT_RE.finditer(step):
        marks.append((mo.start(), mo.end(), "slot",
                      SLOT_ROLE.get(mo.group(1).lower(), "text"), mo.group(1)))
        taken.append((mo.start(), mo.end()))

    # 2. Values the parser identified.
    if parsed:
        ctype = str(parsed.get("type") or "")

        # Quoted text is claimed WITH its quotes so replacing it stays quoted.
        text_val = parsed.get("text")
        if isinstance(text_val, str) and text_val:
            role = "file" if ctype in TEXT_IS_FILE else "text"
            span = _find(step, f'"{text_val}"', taken)
            if span:
                marks.append((span[0] + 1, span[1] - 1, "value", role, ""))
                taken.append(span)
            else:
                span = _find(step, text_val, taken)
                if span:
                    marks.append((span[0], span[1], "value", role, ""))
                    taken.append(span)

        for field, role in (("target", _role_for_target(ctype)),
                            ("variable_name", "variable")):
            val = parsed.get(field)
            if isinstance(val, str) and val:
                span = _find(step, val, taken, whole_word=role in ("locator", "variable"))
                if span:
                    marks.append((span[0], span[1], "value", role, ""))
                    taken.append(span)

        # Extra element names a command carries beyond its target — the
        # popups a swipe closes on the way ("…, closing X and Y"). They are
        # locators as much as the target is, so they get the same blue chip
        # and the same picker instead of reading as plain text.
        if ctype in EXTRA_LOCATORS:
            vals = parsed.get("values")
            for v in (vals[EXTRA_LOCATORS[ctype]:] if isinstance(vals, list) else []):
                if isinstance(v, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v):
                    span = _find(step, v, taken, whole_word=True)
                    if span:
                        marks.append((span[0], span[1], "value", "locator", ""))
                        taken.append(span)

        # Numbers live in their own fields ("wait 5 seconds" -> wait=5.0), and a
        # float renders as "5.0" while the step says "5", so the digits are found
        # in the text rather than matched against the parsed value verbatim.
        # Longest numbers first, so "15" is claimed before "1" can steal its
        # first digit; and each must be a whole number in the text.
        numbers: list[str] = []
        for field in ("wait", "count", "timeout", "index", "seconds"):
            val = parsed.get(field)
            if val is None or isinstance(val, bool):
                continue
            if isinstance(val, (int, float)):
                numbers.append(str(int(val)) if float(val).is_integer() else str(val))
        # values[] carries per-command numbers too (scroll-by pixels).
        vals = parsed.get("values")
        for v in (vals if isinstance(vals, list) else [vals] if vals else []):
            if isinstance(v, (int, float)) or (isinstance(v, str) and v.replace(".", "", 1).isdigit()):
                numbers.append(str(v))
        for written in sorted(set(numbers), key=len, reverse=True):
            span = _find(step, written, taken, whole_number=True)
            if span:
                marks.append((span[0], span[1], "value", "number", ""))
                taken.append(span)

    if not marks:
        return [Segment(text=step, kind="fixed", start=0, end=len(step))]

    marks.sort(key=lambda m: m[0])
    out: list[Segment] = []
    cursor = 0
    for start, end, kind, role, slot in marks:
        if start > cursor:
            out.append(Segment(text=step[cursor:start], kind="fixed",
                               start=cursor, end=start))
        out.append(Segment(text=step[start:end], kind=kind, role=role,
                           start=start, end=end, slot=slot))
        cursor = end
    if cursor < len(step):
        out.append(Segment(text=step[cursor:], kind="fixed", start=cursor, end=len(step)))
    return out


def splice(step: str, start: int, end: int, replacement: str) -> str:
    """Replace one segment's characters, leaving the rest of the step untouched."""
    return step[:start] + replacement + step[end:]
