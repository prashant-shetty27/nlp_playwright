"""
core/reusable_steps.py
CRUD + validation for named reusable step groups.

Storage: data/reusable_steps.json
DSL:     call <name>   (executed inline by runner.py)
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import List

from locators.io_utils import atomic_write_json, file_lock, read_json

logger = logging.getLogger(__name__)

REUSABLE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "reusable_steps.json"
)
#: A group name has to start with a letter and be at least 3 characters. That
#: is the whole rule.
#:
#: It used to also demand lowercase, digits and underscores only. Every other
#: character — a capital, a space, a hyphen — was rejected with a paragraph
#: telling the author to go and retype it, which is work the machine can do and
#: the author cannot be expected to remember. The name is a label; the runner
#: resolves it by lookup, not by parsing it into fields.
_NAME_RE = re.compile(r'^[A-Za-z].{2,49}$', re.S)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_raw() -> dict:
    """Load the JSON file; return {} on missing/corrupt file."""
    try:
        return read_json(REUSABLE_PATH)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("reusable_steps.json load error (treating as empty): %s", e)
        return {}


def _write(data: dict) -> None:
    with file_lock(REUSABLE_PATH, exclusive=True):
        atomic_write_json(REUSABLE_PATH, data)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def load_all() -> dict:
    """Return {name: {steps, created_at, step_count}}."""
    return _load_raw()


def list_names() -> List[str]:
    """Return sorted list of stored reusable step names."""
    return sorted(_load_raw().keys())


def get(name: str) -> List[str]:
    """
    Return the step list for `name`.
    Raises KeyError with a clear message if not found.
    """
    data = _load_raw()
    if name in data:
        return data[name]["steps"]
    # Forgiving lookup: `call SG - Store PRP URL` should find
    # 'sg_store_prp_url' — case, spaces, hyphens/dashes/underscores and a
    # leading "SG" tag are how people write the same name differently, not
    # different groups. Only an unambiguous match is accepted.
    want = _loose(name)
    hits = [k for k in data if _loose(k) == want]
    if len(hits) == 1:
        return data[hits[0]]["steps"]
    raise KeyError(
        f"Reusable steps '{name}' not found. "
        f"Available: {', '.join(sorted(data.keys())) or '(none)'}"
    )


def _loose(name: str) -> str:
    import re as _re
    n = _re.sub(r"[\s\-\u2013\u2014_]+", "_", (name or "").strip().lower()).strip("_")
    n = _re.sub(r"^sg_", "", n)
    return n


def list_for(platform: str = "") -> List[str]:
    """
    Names available on `platform`.

    Groups are platform-scoped because the steps inside them are: a group that
    taps an Android element is meaningless on the website, and offering it there
    would suggest a step that cannot run. Groups saved before scoping existed
    carry no platform and stay visible everywhere.
    """
    data = _load_raw()
    if not platform:
        return sorted(data)
    return sorted(n for n, rec in data.items()
                  if not rec.get("platform") or rec.get("platform") == platform)


def describe(platform: str = "") -> List[dict]:
    """Each group with its steps and platform, for a list screen."""
    data = _load_raw()
    return [{"name": n, "steps": rec.get("steps", []),
             "step_count": rec.get("step_count", len(rec.get("steps", []))),
             "platform": rec.get("platform", ""),
             "created_at": rec.get("created_at", "")}
            for n, rec in sorted(data.items())
            if not platform or not rec.get("platform") or rec.get("platform") == platform]


def save(name: str, steps: List[str], overwrite: bool = False,
         platform: str = "") -> None:
    """
    Persist a named group of steps.

    Raises ValueError for all validation failures.
    ValueError starting with 'DUPLICATE:' signals an existing name — callers
    should offer an overwrite confirmation before calling again with overwrite=True.
    """
    # ── Name validation ──────────────────────────────────────────────────────
    # Surrounding whitespace is trimmed rather than reported. A trailing space
    # is invisible on screen, so "the name is wrong" is the least useful thing
    # that can be said about it.
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the group a name")
    if not _NAME_RE.match(name):
        raise ValueError(
            f"'{name}' cannot be used as a name: it must start with a letter "
            f"and be at least 3 characters."
        )

    # ── Steps validation ─────────────────────────────────────────────────────
    clean_steps = [s.strip() for s in (steps or []) if s.strip()]
    if not clean_steps:
        raise ValueError("Select at least one step to save")

    # ── Self-reference / circular check ──────────────────────────────────────
    call_pattern = re.compile(r'^call\s+(.+)$', re.I)
    for step in clean_steps:
        m = call_pattern.match(step)
        if m and m.group(1).strip().lower() == name.lower():
            raise ValueError(
                f"Circular reference: step '{step}' calls itself"
            )

    # ── Duplicate check ───────────────────────────────────────────────────────
    with file_lock(REUSABLE_PATH, exclusive=True):
        data = _load_raw()
        if name in data and not overwrite:
            raise ValueError(f"DUPLICATE:{name}")

        data[name] = {
            "steps": clean_steps,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "step_count": len(clean_steps),
            "platform": platform,
        }
        atomic_write_json(REUSABLE_PATH, data)

    logger.info("Saved reusable steps '%s' (%d steps)", name, len(clean_steps))


def delete(name: str) -> None:
    """Remove a stored group. Silent no-op if name doesn't exist."""
    with file_lock(REUSABLE_PATH, exclusive=True):
        data = _load_raw()
        if name in data:
            del data[name]
            atomic_write_json(REUSABLE_PATH, data)
            logger.info("Deleted reusable steps '%s'", name)
