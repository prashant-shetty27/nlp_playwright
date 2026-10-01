"""
execution/step_flags.py — "Ignore result" on a step (Testsigma's "Ignore step result").

A step marked this way still runs. If it fails, the test does not fail: the step
is reported amber "Ignored" with the reason, and the next step runs. Typical use
is a popup that may or may not appear (Location Allow, Consent, a rating sheet).

In the .flow file it is a small prefix the editor shows as a chip:

    [ignore] click Location_Allow          waits up to 5 s (the default)
    [ignore 10s] click Consent_button      waits up to 10 s

The wait bounds how long the step may look for its element — a popup that never
appears costs seconds, not the normal 15 s timeout.
"""
from __future__ import annotations

import re
from contextlib import contextmanager

DEFAULT_WAIT_S = 5.0
_RX = re.compile(r"^\s*\[ignore(?:\s+(\d+(?:\.\d+)?)\s*s(?:ec(?:onds?)?)?)?\]\s*", re.I)

#: How long the current ignored step may wait (None outside an ignored step).
CURRENT_WAIT_S: float | None = None


class IgnoredFailure(Exception):
    """An ignored step failed — reported amber, the test carries on."""


def split(step: str) -> tuple[bool, float, str]:
    """'[ignore 10s] click X' → (True, 10.0, 'click X'); plain steps → (False, 0, step)."""
    m = _RX.match(step or "")
    if not m:
        return False, 0.0, step or ""
    return True, float(m.group(1)) if m.group(1) else DEFAULT_WAIT_S, (step or "")[m.end():]


def strip(step: str) -> str:
    return split(step)[2]


def join(text: str, ignore: bool, wait_s: float | None = None) -> str:
    """The step written back with (or without) the marker."""
    body = strip(text).strip()
    if not ignore:
        return body
    w = wait_s if wait_s is not None else DEFAULT_WAIT_S
    tag = "[ignore]" if abs(w - DEFAULT_WAIT_S) < 1e-9 else f"[ignore {w:g}s]"
    return f"{tag} {body}"


@contextmanager
def short_waits(seconds: float):
    """Bound element look-ups / waits of the step to `seconds` (web settings)."""
    global CURRENT_WAIT_S
    from config import settings
    saved = {k: getattr(settings, k) for k in ("ACTION_TIMEOUT_MS", "WAIT_TIMEOUT_MS")
             if hasattr(settings, k)}
    ms = int(seconds * 1000)
    try:
        CURRENT_WAIT_S = seconds
        for k in saved:
            setattr(settings, k, min(saved[k], ms) if k == "WAIT_TIMEOUT_MS" else ms)
        yield
    finally:
        for k, v in saved.items():
            setattr(settings, k, v)
        CURRENT_WAIT_S = None


def run_ignorable(step: str, execute) -> None:
    """Run `execute(text)`; for an ignored step, bound its waits and turn a failure
    into IgnoredFailure (the caller reports it amber and continues)."""
    ignore, wait_s, text = split(step)
    if not ignore:
        execute(text)
        return
    try:
        with short_waits(wait_s):
            execute(text)
    except IgnoredFailure:
        raise
    except Exception as e:  # noqa: BLE001 — that is the point of "ignore result"
        raise IgnoredFailure(f"ignored — {str(e).strip()[:400]}") from e
