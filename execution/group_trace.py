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
