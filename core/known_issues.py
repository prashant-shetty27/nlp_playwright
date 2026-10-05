"""
core/known_issues.py — the known-issues register (data/known_issues.json).

A failure that matches an entry is reported as KNOWN (linked to its Jira /
note), counted apart from new failures, and not re-triaged until the entry
expires or is closed. Entries are edited in Reports → Known issues.

Entry fields:
  id, kind (product-defect | site-change | data | environment | flaky),
  title, jira (key or ""), note, owner, opened (date), expires (date or ""),
  match: {test_case: regex|"", step: regex|"", error: regex|"", element: regex|"", page: ""}
All non-empty match fields must match (AND). Regexes, ignoring case.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date

from config.settings import DATA_DIR

PATH = os.path.join(DATA_DIR, "known_issues.json")
KINDS = ("product-defect", "site-change", "data", "environment", "flaky")


def load() -> list[dict]:
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            return [e for e in (json.load(f) or {}).get("issues", []) if isinstance(e, dict)]
    except (OSError, ValueError):
        return []


def save(issues: list[dict]) -> None:
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"issues": issues}, f, indent=2, ensure_ascii=False)
    os.replace(tmp, PATH)


def _active(e: dict) -> bool:
    if e.get("closed"):
        return False
    exp = (e.get("expires") or "").strip()
    if exp:
        try:
            return date.fromisoformat(exp) >= date.today()
        except ValueError:
            return True
    return True


def _rx(pattern: str, text: str) -> bool:
    if not pattern:
        return True
    try:
        return re.search(pattern, text or "", re.I | re.S) is not None
    except re.error:
        return pattern.lower() in (text or "").lower()


def match(test_case: str, step: str = "", error: str = "", element: str = "", page: str = "") -> dict | None:
    """The first active entry whose match fields all match this failure."""
    for e in load():
        if not _active(e):
            continue
        m = e.get("match") or {}
        if not any(m.get(k) for k in ("test_case", "step", "error", "element", "page")):
            continue
        if (_rx(m.get("test_case", ""), test_case) and _rx(m.get("step", ""), step)
                and _rx(m.get("error", ""), error) and _rx(m.get("element", ""), element)
                and (not m.get("page") or m.get("page") == page)):
            return e
    return None


def element_in(text: str) -> str:
    m = re.search(r"'([a-z_][a-z0-9_]*)'", text or "")
    return m.group(1) if m else ""


def annotate(items: list[dict]) -> int:
    """Mark failed plan items that match a known issue (item['known'] = entry
    summary). Returns how many were marked."""
    n = 0
    for it in items:
        if it.get("status") != "failed":
            it.pop("known", None)
            continue
        ff = it.get("first_failure") or ""
        m = re.match(r"line \d+: (.*?) — (.*)", ff, re.S)
        step, err = (m.group(1), m.group(2)) if m else ("", ff)
        e = match(it.get("test_case", ""), step, err, element_in(ff))
        if e:
            it["known"] = {"id": e.get("id"), "kind": e.get("kind"), "title": e.get("title"),
                           "jira": e.get("jira", ""), "note": e.get("note", "")}
            n += 1
        else:
            it.pop("known", None)
    return n
