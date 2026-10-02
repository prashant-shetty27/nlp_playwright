"""
execution/step_watchdog.py — stop a frozen browser from blocking the portal.

A Playwright call has no timeout of its own when the page's renderer stops
answering (a CDP touch event that is never acknowledged, an evaluate on a hung
page). The step then never returns: "Stop" is only checked between steps, the
run shows "running" forever and the next run is refused.

`guard(step)` arms a timer for one step. If the step is still running after
STEP_HARD_TIMEOUT_S (default 240 s), the timer closes the test browser started
by THIS portal process (Chrome for Testing / Chromium / WebKit / Firefox —
never the user's own Chrome, which is not our child process). The blocked call
then raises "Target closed", the step fails with a clear message and the run
ends normally.
"""
from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import threading
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_BROWSER = re.compile(r"(chrome|chromium|headless_shell|webkit|minibrowser|firefox|playwright.*browser)", re.I)
_local = threading.local()
#: Set by the timer so the step that fails can say why.
FIRED: dict[int, str] = {}


def _timeout_s() -> float:
    try:
        return float(os.getenv("STEP_HARD_TIMEOUT_S", "240"))
    except ValueError:
        return 240.0


def _descendant_browsers(root: int) -> list[int]:
    """Browser processes below `root` (this portal), via `ps` (macOS + Linux)."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,comm="], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return []
    kids: dict[int, list[tuple[int, str]]] = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        kids.setdefault(int(parts[1]), []).append((int(parts[0]), parts[2]))
    found, stack = [], [root]
    while stack:
        p = stack.pop()
        for pid, comm in kids.get(p, []):
            stack.append(pid)
            if _BROWSER.search(os.path.basename(comm)):
                found.append(pid)
    return found


def _fire(step: str, thread_id: int) -> None:
    pids = _descendant_browsers(os.getpid())
    FIRED[thread_id] = step
    logger.error("⏱️ Step still running after %.0fs — closing the frozen test browser (%s): %s",
                 _timeout_s(), ", ".join(map(str, pids)) or "none found", step[:120])
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:  # noqa: BLE001
            pass


@contextmanager
def guard(step: str):
    """Arm the watchdog for one step (nested steps share the outer timer)."""
    if getattr(_local, "armed", False) or _timeout_s() <= 0:
        yield
        return
    tid = threading.get_ident()
    FIRED.pop(tid, None)
    timer = threading.Timer(_timeout_s(), _fire, args=(step, tid))
    timer.daemon = True
    _local.armed = True
    timer.start()
    try:
        yield
    except Exception as e:  # noqa: BLE001
        if FIRED.pop(tid, None):
            raise RuntimeError(
                f"The browser stopped responding during this step for over {_timeout_s():.0f}s "
                f"and was closed so the run could finish. ({e.__class__.__name__})") from e
        raise
    finally:
        timer.cancel()
        _local.armed = False
