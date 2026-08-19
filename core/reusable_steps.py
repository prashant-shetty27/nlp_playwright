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
_NAME_RE = re.compile(r'^[a-z][a-z0-9_]{1,49}$')


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
    if name not in data:
        raise KeyError(
            f"Reusable steps '{name}' not found. "
            f"Available: {', '.join(sorted(data.keys())) or '(none)'}"
        )
    return data[name]["steps"]


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
    name = (name or "").strip()
    if not name:
        raise ValueError("Name cannot be empty")
    if not _NAME_RE.match(name):
        raise ValueError(
            "Name must start with a letter and contain only lowercase "
            "letters, digits, or underscores (2–50 chars total)"
        )

    # ── Steps validation ─────────────────────────────────────────────────────
    clean_steps = [s.strip() for s in (steps or []) if s.strip()]
    if not clean_steps:
        raise ValueError("Select at least one step to save")

    # ── Self-reference / circular check ──────────────────────────────────────
    call_pattern = re.compile(r'^call\s+(\S+)$', re.I)
    for step in clean_steps:
        m = call_pattern.match(step)
        if m and m.group(1).lower() == name.lower():
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
