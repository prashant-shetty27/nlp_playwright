"""
execution/group_trace.py — what happened INSIDE a step group, for the report.

A `call <group>` line is one step in the test, but the report should let you
open it and see each of its steps — passed, failed (with the reason), ignored or
not run — whether the group passed or failed. run_lines() records each inner
step here; the run loop collects them as the step's "children". Nested groups
nest. Thread-local, so parallel runs never mix.
"""
from __future__ import annotations

import threading

_tl = threading.local()


def _stack() -> list:
    st = getattr(_tl, "stack", None)
    if st is None:
        st = _tl.stack = []
    return st


def begin() -> None:
    _stack().append([])


def end() -> list:
    st = _stack()
    return st.pop() if st else []


def active() -> bool:
    return bool(_stack())


def record(item: dict) -> None:
    st = _stack()
    if st:
        st[-1].append(item)


# ── screenshots for inner steps ──────────────────────────────────────────────
# The run loop installs a capture function for the duration of a run; the group
# driver calls capture() after each inner step so a step inside a group gets its
# own frame (clickable in the live view / report) like a top-level step does.
def set_capture(fn) -> None:
    _tl.capture = fn
    _tl.frame_no = 0


def clear_capture() -> None:
    _tl.capture = None


def capture(page, row: dict) -> None:
    fn = getattr(_tl, "capture", None)
    if fn is None or page is None:
        return
    _tl.frame_no = getattr(_tl, "frame_no", 0) + 1
    try:
        fn(page, row, f"_g{_tl.frame_no:03d}")
    except Exception:  # noqa: BLE001 — a picture never fails a step
        pass
