"""
locators/naming.py — propose a name for an element, from the element itself.

An author pasting a selector should not also have to invent a name that matches
a convention they have not memorised. But a suggestion is only useful if it is
DERIVED from the thing being named: "element_1" teaches nothing and gets
accepted out of fatigue, which is how a repository fills up with names that
describe nothing.

So the name is read out of the selector — its id, test id, aria-label,
placeholder, visible text or class — and combined with what the step is doing
with it. `//button[@id='submit-otp']` in a `click` step becomes
`submit_otp_button`, not `element_1`.

Everything returned is already normalised to the project convention
(lowercase, underscores, no leading digit), so accepting a suggestion cannot
produce a name the database would reject.
"""
from __future__ import annotations

import re

#: Attributes worth naming an element after, best first. An id or a test id was
#: chosen by a developer to identify this element; a class was chosen to style a
#: hundred of them.
_ATTR_PRIORITY = ("data-testid", "data-test-id", "data-qa", "data-cy",
                  "id", "name", "aria-label", "placeholder", "title", "alt")

#: What the element IS, appended so two elements with the same label are still
#: distinguishable — "search" as a box and "search" as a button.
_TAG_SUFFIX = {
    "button": "button", "a": "link", "input": "input", "textarea": "input",
    "select": "dropdown", "img": "image", "form": "form", "label": "label",
    "table": "table", "ul": "list", "ol": "list", "li": "item",
    "h1": "heading", "h2": "heading", "h3": "heading", "span": "text",
    "p": "text", "div": "",
}

#: Common abbreviations expanded, so "continue_btn" plus a <button> tag does not
#: become "continue_btn_button".
_ABBREV = {"btn": "button", "lnk": "link", "img": "image", "txt": "text",
           "inp": "input", "fld": "field", "chk": "checkbox", "dd": "dropdown",
           "hdr": "heading", "lbl": "label", "msg": "message", "num": "number"}

#: Words that add nothing to a name — every element is an element.
_NOISE = {"the", "a", "an", "of", "for", "and", "or", "to", "in", "on",
          "element", "elem", "el", "node", "item0", "root", "wrapper",
          "container", "js", "css", "xpath"}


def _clean(raw: str) -> list[str]:
    """Split an attribute value into meaningful words."""
    # camelCase and kebab/snake both become word boundaries.
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw or "")
    words = re.split(r"[^A-Za-z0-9]+", spaced)
    out = []
    for w in words:
        lw = w.strip().lower()
        if not lw or lw in _NOISE:
            continue
        # A long digit run is an id, not a description — "btn-48213" says nothing.
        if lw.isdigit() and len(lw) > 2:
            continue
        out.append(_ABBREV.get(lw, lw))
    return out[:4]


def _tag_of(selector: str) -> str:
    sel = _target_part(selector).strip()
    mo = re.match(r"^//(?:\*|([a-zA-Z][\w-]*))", sel) or \
        re.match(r"^([a-zA-Z][\w-]*)[\.\[#:]", sel) or \
        re.match(r"^([a-zA-Z][\w-]*)$", sel)
    return (mo.group(1) or "").lower() if mo and mo.lastindex else ""


def _target_part(selector: str) -> str:
    """
    The part of the selector that names the ELEMENT, not its ancestors.

    "form[name='UserQuickInfo'] button.continue_btn" targets the button; naming
    it after the form describes the wrong thing. The last segment is the target
    in both CSS (space/child separated) and XPath (slash separated).
    """
    sel = (selector or "").strip()
    if sel.startswith("//") or sel.startswith("("):
        # Split on / but not inside brackets, then take the last non-empty step.
        depth, part, parts = 0, [], []
        for ch in sel:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            if ch == "/" and depth == 0:
                if part:
                    parts.append("".join(part))
                part = []
            else:
                part.append(ch)
        if part:
            parts.append("".join(part))
        return parts[-1] if parts else sel
    # CSS: last descendant/child segment.
    tail = re.split(r"\s*[>+~]\s*|\s+", sel)
    return tail[-1] if tail else sel


def _attr_words(selector: str) -> list[str]:
    """The most identifying attribute value in the selector, as words."""
    sel = _target_part(selector) or ""
    for attr in _ATTR_PRIORITY:
        for pat in (rf"@{re.escape(attr)}\s*=\s*['\"]([^'\"]+)['\"]",
                    rf"\[{re.escape(attr)}\s*=\s*['\"]([^'\"]+)['\"]\]"):
            mo = re.search(pat, sel, re.I)
            if mo:
                words = _clean(mo.group(1))
                if words:
                    return words
    # A CSS id or class shorthand: #submit-otp / .continue-btn
    mo = re.search(r"#([A-Za-z][\w-]*)", sel)
    if mo:
        words = _clean(mo.group(1))
        if words:
            return words
    # Visible text: //button[text()='Continue'] or contains(., 'Continue')
    mo = re.search(r"(?:text\(\)\s*=|contains\s*\(\s*\.?\s*,)\s*['\"]([^'\"]+)['\"]", sel)
    if mo:
        words = _clean(mo.group(1))
        if words:
            return words
    mo = re.search(r"\.([A-Za-z][\w-]{2,})", sel)
    if mo:
        words = _clean(mo.group(1))
        if words:
            return words
    # Only now consider the ancestors — better a scoped name than none at all.
    whole = selector or ""
    if whole != sel:
        mo = re.search(r"@(?:id|name|data-testid)\s*=\s*['\"]([^'\"]+)['\"]|"
                       r"\[(?:id|name|data-testid)\s*=\s*['\"]([^'\"]+)['\"]\]", whole, re.I)
        if mo:
            words = _clean(mo.group(1) or mo.group(2) or "")
            if words:
                return words
    return []


def suggest_names(selector: str, *, step: str = "", group: str = "",
                  taken: set[str] | None = None, limit: int = 3) -> list[str]:
    """
    Names worth offering for this element, best first.

    `step` is the step being written — "click …", "type … into …" — which tells
    us what the element is FOR when the selector alone is uninformative.
    `taken` are names already in use; a suggestion that collides is not offered,
    because accepting it would just move the problem to the save button.
    """
    from nlp.variables import normalise

    taken = {t.lower() for t in (taken or set())}
    words = _attr_words(selector)
    tag = _tag_of(selector)
    suffix = _TAG_SUFFIX.get(tag, "")

    # What the step does with it, when the selector says little.
    verb = ""
    low = (step or "").strip().lower()
    if low.startswith(("type", "enter", "fill")):
        verb = "input"
    elif low.startswith(("verify", "check", "assert")):
        verb = ""
    elif low.startswith(("click", "tap", "press")):
        verb = "button" if not suffix else ""

    candidates: list[str] = []

    def add(parts: list[str]) -> None:
        name = normalise("_".join(p for p in parts if p))
        if not name or name.lower() in taken:
            return
        if name not in candidates:
            candidates.append(name)

    if words:
        add(words + ([suffix] if suffix and suffix not in words else []))
        add(words)                                   # without the tag suffix
        if group:
            add(words + [normalise(group).split("_")[0]])
    if words and verb and verb not in words:
        add(words + [verb])
    if not candidates and suffix:
        add([suffix])
    if not candidates:
        # Nothing identifying in the selector at all. Say that plainly rather
        # than inventing element_1 — a name that describes nothing is worse than
        # no suggestion, because it gets accepted.
        return []
    return candidates[:limit]


def explain() -> str:
    """One line the UI can show about what a good name looks like here."""
    return ("Lowercase words joined by underscores, describing what the element "
            "IS — submit_otp_button, mobile_number_input — not where it sits.")
