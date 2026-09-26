"""
api/routes/system.py — restart the server from inside the server.

The awkward part of this endpoint is that the process it restarts is the one
answering the request. There is no moment at which it can bind the port twice,
so the sequence has to be:

  1. hand the work to a DETACHED relauncher — a child that outlives its parent,
     so killing the parent does not kill it;
  2. the relauncher waits for the port to actually free, because the successor
     cannot bind while the old socket is still held;
  3. only then does this process exit.

Spawning the successor directly and exiting would race: the new process would
try to bind a port the old one has not released yet, fail, and leave nothing
listening at all — the one outcome worse than not restarting.

A restart is destructive in a way that is easy to miss: the in-memory run
registry goes with it, and a test executing in a background thread is killed
mid-step with its browser still open. So /system/restart REFUSES while a run is
in progress unless it is told to go ahead anyway, and says what it would
interrupt.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])

#: How long the relauncher waits for the old process to release the port before
#: giving up. Generous: uvicorn's graceful shutdown can take a few seconds when
#: a websocket is still open, which is exactly the case here — the page that
#: asked for the restart is still connected.
_PORT_WAIT_S = 30


class RestartRequest(BaseModel):
    #: Restart even though a test is running. The UI asks first and names it.
    force: bool = False


def _running_runs() -> list[str]:
    """run_ids currently executing, so a restart can refuse to orphan them."""
    try:
        from api.routes.tests import _runs, _runs_lock

        with _runs_lock:
            return [rid for rid, info in _runs.items()
                    if (info or {}).get("status") == "running"]
    except Exception:  # noqa: BLE001 — never block a restart on bookkeeping
        return []


def _port_free(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _spawn_relauncher(host: str, port: int) -> int:
    """
    Start a detached process that waits for the port, then starts the successor.

    start_new_session detaches it from this process group, so it survives this
    process exiting — which is the entire point.
    """
    script = (
        "import socket,subprocess,sys,time\n"
        f"host,port={host!r},{port}\n"
        f"deadline=time.time()+{_PORT_WAIT_S}\n"
        "while time.time()<deadline:\n"
        "    s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
        "    try:\n"
        "        s.bind((host,port)); s.close(); break\n"
        "    except OSError:\n"
        "        s.close(); time.sleep(0.3)\n"
        "subprocess.Popen([sys.executable,'-m','ui.app','--port',str(port),\n"
        f"                  '--host',host], cwd={_project_root()!r},\n"
        "                 start_new_session=True)\n"
    )
    child = subprocess.Popen([sys.executable, "-c", script],
                             cwd=_project_root(), start_new_session=True)
    return child.pid


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _current_port() -> int:
    """
    The port this process is serving on.

    Read from the command line it was started with rather than a setting: the
    successor has to come back on the SAME port, and a default would silently
    move the UI somewhere the operator is not looking.
    """
    argv = sys.argv
    for i, a in enumerate(argv):
        if a == "--port" and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except ValueError:
                break
        if a.startswith("--port="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                break
    return int(os.getenv("UI_PORT", "8080"))


def _current_host() -> str:
    argv = sys.argv
    for i, a in enumerate(argv):
        if a == "--host" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--host="):
            return a.split("=", 1)[1]
    return os.getenv("UI_HOST", "127.0.0.1")


@router.get("/info")
def info():
    """Where this process is serving, and whether it is safe to restart."""
    running = _running_runs()
    return {
        "pid": os.getpid(),
        "host": _current_host(),
        "port": _current_port(),
        "running_runs": running,
        "safe_to_restart": not running,
    }


@router.post("/restart")
def restart(body: RestartRequest):
    """
    Replace this process with a fresh one on the same host and port.

    Returns BEFORE the restart happens — the response has to reach the browser
    while this process can still send it. The caller then polls /health until
    the successor answers.
    """
    running = _running_runs()
    try:
        from execution import plan_engine
        plan = plan_engine.active_run()
    except Exception:  # noqa: BLE001
        plan = ""
    if plan and not body.force:
        # Between test cases, or while it writes the report / posts to Slack, a
        # plan has no run in the registry above — it was killed silently.
        raise HTTPException(
            status_code=409,
            detail=(f"Test plan run {plan} is still in progress. Restarting stops it and "
                    f"marks it interrupted. Send force=true to restart anyway."))
    if running and not body.force:
        raise HTTPException(
            status_code=409,
            detail=(f"{len(running)} test run(s) still executing. Restarting kills "
                    f"them mid-step and leaves their browsers open. Send "
                    f"force=true to restart anyway."),
        )

    host, port = _current_host(), _current_port()
    pid = _spawn_relauncher(host, port)
    logger.warning("♻️  Restart requested — relauncher pid %s will take %s:%s",
                   pid, host, port)

    def _bow_out() -> None:
        # Long enough for this response to reach the browser. os._exit rather
        # than a graceful shutdown because uvicorn will not close while the
        # page's websocket is open, and that page is the one that asked.
        time.sleep(1.0)
        logger.warning("♻️  Exiting for restart.")
        os._exit(0)

    threading.Thread(target=_bow_out, daemon=True).start()
    return {"restarting": True, "host": host, "port": port,
            "relauncher_pid": pid, "interrupted_runs": running,
            "poll": "/health"}
