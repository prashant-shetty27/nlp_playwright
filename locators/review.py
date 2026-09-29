"""
Element review — the things that make an element list untrustworthy.

Testsigma's element screen tells you when a name is used twice or a locator
is empty; the portal's grew a warning banner for duplicates but nothing for
the rest. This runs the checks in one place so both the Elements page and a
test case's Review panel can show them with the same wording:

  duplicate_name      the same name in more than one place — the runner takes
                      the first, so a step may click the wrong thing and pass
  duplicate_selector  the same selector under two names — the two drift apart
  blank_selector      an element with nothing to find it by
  junk_name           an auto-generated or meaningless name (text_8d5ae1,
                      element_3, x, new_element…) — the step reads as nothing
  bad_name            not the portal's naming convention (spaces, capitals)
  fragile_selector    an absolute /html/body/… path or an index-only XPath
  unused              nothing in any test case or step group uses it

Each finding is a plain dict so it travels over the API unchanged.
"""
from __future__ import annotations

import os
import re

#: Names that say nothing about what the element is.
_JUNK = re.compile(
    r"^(?:"
    r"(?:text|element|el|elem|div|span|button|btn|a|link|input|img|icon|item|node|new_element|untitled)"
    r"[_-]?[0-9a-f]{4,}"                  # text_8d5ae1, element_3f9a
    r"|(?:element|el|elem|div|span|button|btn|link|input|item|node)[_-]?\d{1,3}"   # element_3
    r"|[a-z]{1,2}\d*"                      # x, ab, a1
    r"|(?:test|temp|tmp|new|xyz|abc|asdf|qwerty|untitled|unknown|todo|dummy|sample|new_element)(?:[_-]?\d+)?"
    r"|(.)\1{2,}"                          # aaa, ____
    r")$", re.I)
_CONVENTION = re.compile(r"^[a-z][a-z0-9_]*$")
_ABSOLUTE = re.compile(r"^/html(/|\[)", re.I)
_INDEX_ONLY = re.compile(r"^(//?\*?[a-z]*\[\d+\])+/?$", re.I)


def _selector_of(rec) -> str:
    if isinstance(rec, str):
        return rec
    if not isinstance(rec, dict):
        return ""
    for k in ("custom_xpath", "xpath", "value"):
        if rec.get(k):
            return str(rec[k])
    sels = rec.get("selectors")
    if isinstance(sels, list) and sels and isinstance(sels[0], dict):
        return str(sels[0].get("value") or "")
    return ""


def _norm(sel: str) -> str:
    return re.sub(r"\s+", " ", (sel or "").strip().replace('"', "'"))


def usage_counts(names: set[str]) -> dict[str, int]:
    """How many flow / step-group lines mention each name (whole word)."""
    from config.settings import FLOWS_DIR

    counts = {n: 0 for n in names}
    if not names:
        return counts
    # One regex for every name is far cheaper than one scan per name.
    rx = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True))
                    + r")(?![A-Za-z0-9_])")
    texts: list[str] = []
    try:
        for fn in os.listdir(FLOWS_DIR):
            if fn.endswith(".flow"):
                try:
                    with open(os.path.join(FLOWS_DIR, fn), encoding="utf-8") as f:
                        texts.append(f.read())
                except OSError:
                    continue
    except OSError:
        pass
    try:
        from core.reusable_steps import describe
        for g in describe():
            texts.append("\n".join(g.get("steps") or []))
    except Exception:  # noqa: BLE001
        pass
    for text in texts:
        for line in text.splitlines():
            if line.strip().startswith("#") and not line.strip().startswith("# OFF:"):
                continue
            for m in rx.finditer(line):
                counts[m.group(1)] += 1
    return counts


def name_problem(name: str) -> tuple[str, str] | None:
    """(kind, message) when the name itself is a problem, else None."""
    n = (name or "").strip()
    if not n:
        return "junk_name", "The element has no name."
    if _JUNK.match(n):
        return "junk_name", f"'{n}' is an auto-generated or meaningless name."
    if not _CONVENTION.match(n):
        return "bad_name", f"'{n}' is not in the naming convention (lower-case, digits, underscores)."
    return None


def review_elements(platform: str = "website", *, with_usage: bool = True) -> list[dict]:
    from locators.sources import entries

    out: list[dict] = []
    ents = entries(platform)
    by_name: dict[str, list] = {}
    by_sel: dict[str, list] = {}
    for e in ents:
        by_name.setdefault(e.name, []).append(e)
        sel = _norm(_selector_of(e.record))
        if sel:
            by_sel.setdefault(sel, []).append(e)

    def finding(kind, severity, e, message, why, **extra) -> dict:
        d = {"kind": kind, "severity": severity, "name": e.name, "group": e.group,
             "source": e.source_id, "selector": _selector_of(e.record)[:160],
             "message": message, "why": why}
        d.update(extra)
        return d

    for name, es in by_name.items():
        if len(es) > 1:
            where = ", ".join(f"{x.source_id}:{x.group}" for x in es)
            for e in es:
                out.append(finding(
                    "duplicate_name", "high", e,
                    f"'{name}' is defined {len(es)} times ({where}). The runner uses "
                    f"{es[0].source_id}:{es[0].group}.",
                    "A step naming it binds to whichever copy is found first, so it can "
                    "click the wrong element and still pass. Keep one, or rename the others.",
                    others=[{"source": x.source_id, "group": x.group} for x in es if x is not e]))

    for sel, es in by_sel.items():
        if len(es) > 1 and len({x.name for x in es}) > 1:
            names = ", ".join(sorted({x.name for x in es}))
            for e in es:
                out.append(finding(
                    "duplicate_selector", "medium", e,
                    f"The same selector is saved under {len(es)} names: {names}.",
                    "Two names for one element drift apart: when the page changes, "
                    "whoever fixes one will not know the other exists. Point the steps at "
                    "one name and delete the rest.",
                    others=sorted({x.name for x in es} - {e.name})))

    for e in ents:
        sel = _selector_of(e.record)
        if not sel.strip():
            out.append(finding("blank_selector", "high", e,
                               f"'{e.name}' has no selector.",
                               "Nothing can find it on the page; every step using it fails."))
        elif _ABSOLUTE.match(sel.strip()) or _INDEX_ONLY.match(sel.strip()):
            out.append(finding("fragile_selector", "low", e,
                               f"'{e.name}' uses a position-only path.",
                               "An absolute or index-only XPath breaks as soon as the page "
                               "gains or loses a wrapper. Prefer an id, a data attribute, "
                               "text or a stable class."))
        prob = name_problem(e.name)
        if prob:
            kind, msg = prob
            out.append(finding(kind, "medium" if kind == "junk_name" else "low", e, msg,
                               "A step reads as 'click text_8d5ae1' — nobody can tell what it "
                               "does without opening the element. Rename it to what it is "
                               "(e.g. login_continue_button); every step is rewritten for you."
                               if kind == "junk_name" else
                               "Names are normalised on save; mixed styles make the same "
                               "element easy to save twice."))

    if with_usage:
        counts = usage_counts({e.name for e in ents})
        for e in ents:
            if counts.get(e.name, 0) == 0:
                out.append(finding("unused", "low", e,
                                   f"'{e.name}' is not used by any test case or step group.",
                                   "Unused elements pile up and hide the ones that matter. "
                                   "Delete it if it is not needed — it can be recorded again."))
        for f in out:
            f["used_by"] = counts.get(f["name"], 0)

    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda f: (order.get(f["severity"], 9), f["kind"], f["name"]))
    return out
