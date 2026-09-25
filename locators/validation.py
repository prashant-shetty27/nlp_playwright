"""
locators/validation.py — refuse junk and duplicates BEFORE they enter the repository.

The element database is the one store everything else resolves against, and it
degrades quietly. Two failure modes, both already present in this repository:

  SAME NAME, TWO DEFINITIONS   Eight names are defined more than once. The runner
      takes whichever it scans first, so a step can click the right-looking wrong
      element and still pass. Nothing warned when the second one was saved.

  SAME SELECTOR, TWO NAMES     The same CSS or XPath saved twice under different
      names. Both work, so nothing ever fails — but a change to that element now
      needs finding in two places, and neither name tells you the other exists.

Checking at save time is the only point where it is cheap. Afterwards the
duplicate is referenced by tests, and removing it means editing them.

Nothing here refuses on its own. It reports what it found, with the existing
entry, so the caller can offer the real choice: reuse what is there, save anyway
under a different name, or ask for the original to be changed. A validator that
silently blocks is one people work around.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


@dataclass
class Conflict:
    kind: str          # duplicate_name | duplicate_selector | similar_name | shadowed
    severity: str      # "blocking" | "warning"
    message: str
    why: str
    existing_name: str = ""
    existing_group: str = ""
    existing_source: str = ""
    existing_selector: str = ""
    #: What the caller can offer to do about it.
    options: list = field(default_factory=list)


def _normalise_selector(sel: str) -> str:
    """Whitespace and quote style differ; the element does not."""
    s = (sel or "").strip().replace('"', "'")
    return re.sub(r"\s+", " ", s)


def _similar(a: str, b: str) -> float:
    """Cheap similarity, enough to catch search_box vs searchbox vs search_bx."""
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a.replace("_", ""), b.replace("_", "")).ratio()


def check_locator(name: str, group: str, selector: str, *,
                  platform: str = "website") -> list[Conflict]:
    """
    Everything wrong with saving this element, most serious first.

    `severity` is advice to the caller, not enforcement:
      blocking  saving this creates a genuine ambiguity — the same name resolving
                to two elements, or the same element under two names.
      warning   worth a look: a near-identical name is usually a typo of one that
                already exists.
    """
    out: list[Conflict] = []
    try:
        from locators.sources import entries
    except Exception:  # noqa: BLE001
        return out

    all_entries = entries(platform)
    incoming_sel = _normalise_selector(selector)

    # ── the same NAME already exists ─────────────────────────────────────────
    same_name = [e for e in all_entries if e.name == name]
    if same_name:
        first = same_name[0]
        existing_sel = _selector_of(first.record)
        identical = _normalise_selector(existing_sel) == incoming_sel
        out.append(Conflict(
            kind="duplicate_name", severity="blocking",
            message=(f"'{name}' already exists in {first.source_id}:{first.group}"
                     + (" with the SAME selector." if identical
                        else " with a DIFFERENT selector.")),
            why=("Saving it again means one name resolves to two elements. The "
                 "runner takes whichever it scans first, so a step can click the "
                 "wrong thing and still pass — the hardest kind of failure to "
                 "notice." if not identical else
                 "The element you are saving is already there, under this exact "
                 "name and selector. Saving again just adds a second copy to keep "
                 "in step."),
            existing_name=first.name, existing_group=first.group,
            existing_source=first.source_id, existing_selector=existing_sel,
            options=([{"action": "reuse", "label": f"Use the existing '{name}'",
                       "best": True,
                       "note": "It already points at this element."}]
                     if identical else
                     [{"action": "rename", "label": "Save under a different name",
                       "best": True,
                       "note": "Two different elements need two different names."},
                      {"action": "request_change", "label": "Ask for the existing one to be changed",
                       "best": False,
                       "note": "If the existing entry is the wrong one, it should be "
                               "corrected rather than shadowed."}])))

    # ── the same SELECTOR already exists under another name ──────────────────
    if incoming_sel:
        for e in all_entries:
            if e.name == name:
                continue
            if _normalise_selector(_selector_of(e.record)) == incoming_sel:
                out.append(Conflict(
                    kind="duplicate_selector", severity="blocking",
                    message=f"This selector is already saved as '{e.name}' "
                            f"({e.source_id}:{e.group}).",
                    why="The same element under two names means a change to the page "
                        "has to be fixed twice, and neither name tells you the other "
                        "exists. Reusing the existing name keeps one element, one "
                        "definition.",
                    existing_name=e.name, existing_group=e.group,
                    existing_source=e.source_id,
                    existing_selector=_selector_of(e.record),
                    options=[{"action": "reuse", "label": f"Use '{e.name}' instead",
                              "best": True,
                              "note": "Same element, already named."},
                             {"action": "save_anyway",
                              "label": "Save under this name as well", "best": False,
                              "note": "Only if the two names mean genuinely different "
                                      "things to your tests."}]))
                break

    # ── a name close enough to be a typo ─────────────────────────────────────
    if not same_name:
        # An exact match AFTER normalisation — search_box vs searchbox — is the
        # single most likely duplicate there is, and the earlier `< 1.0` filter
        # excluded precisely that case while catching weaker resemblances.
        twin = next((e for e in all_entries
                     if e.name != name
                     and e.name.replace("_", "") == name.replace("_", "")), None)
        if twin:
            out.append(Conflict(
                kind="duplicate_name", severity="blocking",
                message=f"'{twin.name}' is the same name in a different style "
                        f"({twin.source_id}:{twin.group}).",
                why="Underscores are the only difference, so these are almost "
                    "certainly the same element. Two spellings means neither "
                    "reader knows which one their test should use.",
                existing_name=twin.name, existing_group=twin.group,
                existing_source=twin.source_id,
                existing_selector=_selector_of(twin.record),
                options=[{"action": "reuse", "label": f"Use '{twin.name}'",
                          "best": True, "note": "Same element, already named."},
                         {"action": "save_anyway", "label": "They are different",
                          "best": False,
                          "note": "Only if the two really are separate elements."}]))
        near = [(e, _similar(name, e.name)) for e in all_entries if e.name != name]
        near = [(e, r) for e, r in near if 0.86 <= r < 1.0]
        if near and not twin:
            e, ratio = max(near, key=lambda kv: kv[1])
            out.append(Conflict(
                kind="similar_name", severity="warning",
                message=f"'{e.name}' already exists and is very close to this name.",
                why="Two names this similar are usually the same element saved "
                    "twice, and later readers cannot tell which to use.",
                existing_name=e.name, existing_group=e.group,
                existing_source=e.source_id,
                existing_selector=_selector_of(e.record),
                options=[{"action": "reuse", "label": f"Use '{e.name}'", "best": True,
                          "note": "If it is the same element."},
                         {"action": "save_anyway", "label": "They are different",
                          "best": False, "note": "Saved as typed."}]))
    return out


def _selector_of(record) -> str:
    if isinstance(record, str):
        return record
    if not isinstance(record, dict):
        return ""
    if record.get("custom_xpath"):
        return record["custom_xpath"]
    if record.get("xpath"):
        return record["xpath"]
    sels = record.get("selectors")
    if isinstance(sels, list) and sels:
        first = sels[0]
        if isinstance(first, dict):
            return first.get("value", "")
    return ""


def check_variable(name: str, *, environment: str = "", value: str | None = None,
                   updating: bool = False) -> list[Conflict]:
    """
    Whether a test-data name is already taken, or nearly — and, when `value`
    is given, whether that VALUE is already stored under another name.

    The same reasoning as elements: a second ${test_mobile} under a slightly
    different name means two values that drift apart, and steps that look alike
    but read different data. The same URL saved twice under two names is the
    same problem from the other side: change one and the other silently keeps
    the old page.

    `updating=True` means the caller is deliberately changing the value of a
    name it already knows exists (the Change-value dialog), so the name itself
    is not reported as a clash — only a value that belongs to some OTHER name.
    """
    out: list[Conflict] = []
    try:
        from execution.test_data import get_all

        current = get_all(environment)
    except Exception:  # noqa: BLE001
        return out

    # ── the same VALUE already stored under another name ─────────────────────
    incoming = (value or "").strip()
    if incoming:
        for other, entry in current.items():
            if other == name or entry.get("is_secret"):
                continue
            if str(entry.get("value", "")).strip() == incoming:
                out.append(Conflict(
                    kind="duplicate_value", severity="blocking",
                    message=f"This exact value is already stored as ${{{other}}} "
                            f"({entry.get('scope', 'global')}).",
                    why="Two names for one value drift apart: the day the URL "
                        "or number changes, whoever edits one of them will not "
                        "know the other exists, and half the tests keep using "
                        "the old value.",
                    existing_name=other, existing_selector=entry.get("display", ""),
                    options=[{"action": "reuse", "label": f"Use ${{{other}}} instead",
                              "best": True, "note": "Same value, already named."},
                             {"action": "save_anyway", "label": "Save it under this name too",
                              "best": False,
                              "note": "Only if the two names are meant to move "
                                      "independently later."}]))
                break

    if name in current and updating:
        return out

    if name in current:
        e = current[name]
        out.append(Conflict(
            kind="duplicate_name", severity="blocking",
            message=f"'{name}' is already stored ({e.get('scope','global')}).",
            why="Saving it again replaces the existing value. Every test using "
                "${" + name + "} starts reading the new one, which may not be what "
                "those tests meant.",
            existing_name=name, existing_selector=e.get("display", ""),
            options=[{"action": "reuse", "label": "Use the existing value",
                      "best": True, "note": e.get("display", "")},
                     {"action": "overwrite", "label": "Replace it", "best": False,
                      "note": "Every test using it changes with it."}]))
        return out

    for other in current:
        if 0.86 <= _similar(name, other) < 1.0:
            out.append(Conflict(
                kind="similar_name", severity="warning",
                message=f"'{other}' already exists and is very close to this name.",
                why="Two values this similarly named drift apart, and a step "
                    "reading the wrong one looks identical to a step reading the "
                    "right one.",
                existing_name=other,
                existing_selector=current[other].get("display", ""),
                options=[{"action": "reuse", "label": f"Use '{other}'", "best": True,
                          "note": current[other].get("display", "")},
                         {"action": "save_anyway", "label": "They are different",
                          "best": False, "note": "Saved as typed."}]))
            break
    return out


def as_dicts(conflicts: list[Conflict]) -> list[dict]:
    return [asdict(c) for c in conflicts]
