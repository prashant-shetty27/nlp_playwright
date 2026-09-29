"""
api/routes/websocket.py
WS /ws/test-stream

Connect, then send a JSON message:
  {"project": "steps", "headless": true}

The server streams one JSON message per step:
  {"type": "step",    "line": 1, "step": "open justdial", "status": "running"}
  {"type": "result",  "line": 1, "step": "open justdial", "status": "passed"}
  {"type": "result",  "line": 2, "step": "...",           "status": "failed", "error": "..."}
  {"type": "summary", "total": 4, "passed": 3, "failed": 1}
  {"type": "done"}
"""
import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from config.settings import FLOWS_DIR

router = APIRouter(tags=["websocket"])
_executor = ThreadPoolExecutor(max_workers=4)


def _flow_path(project: str) -> str:
    safe = os.path.basename(project)
    if not safe.endswith(".flow"):
        safe += ".flow"
    return os.path.join(FLOWS_DIR, safe)


def _run_flow_streaming(flow_path: str, headless: bool, send_fn):
    """
    Blocking Playwright execution that calls send_fn(dict) for every event.
    Runs in a thread-pool thread so the event loop stays free.
    """
    import os as _os
    _os.environ["HEADLESS"] = "true" if headless else "false"

    import execution.action_service  # noqa — registers @codeless_snippet
    from locators.cleaner import sanitize_database
    from execution.browser_manager import open_browser, close_browser
    from execution.session import TestSession
    from runner import _interpret

    # One browser run at a time in this process — the same lock Run Center
    # and plans take. Without it this stream ran alongside a plan, both
    # sharing the process-wide session state and HEADLESS flag.
    from api.routes.tests import _EXEC_LOCK
    if not _EXEC_LOCK.acquire(blocking=False):
        send_fn({"type": "error", "message": "Another run is in progress — wait for it to finish."})
        send_fn({"type": "done"})
        return
    page = None
    session = None
    passed = failed = 0

    try:
        sanitize_database()
        session = TestSession()
        page = open_browser(session)
        with open(flow_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line_num, raw in enumerate(lines, 1):
            step = raw.strip()
            if not step or step.startswith("#"):
                continue

            send_fn({"type": "step", "line": line_num, "step": step, "status": "running"})

            try:
                _interpret(step, page)
                send_fn({"type": "result", "line": line_num, "step": step, "status": "passed"})
                passed += 1
            except Exception as e:
                send_fn({
                    "type": "result",
                    "line": line_num,
                    "step": step,
                    "status": "failed",
                    "error": str(e).strip(),
                })
                failed += 1

    except Exception as e:
        send_fn({"type": "error", "message": str(e)})
        failed += 1

    finally:
        try:
            if page is not None:
                project_name = os.path.basename(flow_path).replace(".flow", "")
                close_browser(page, project_name, session)
        except Exception:
            pass
        _EXEC_LOCK.release()

    send_fn({"type": "summary", "total": passed + failed, "passed": passed, "failed": failed})
    send_fn({"type": "done"})


@router.websocket("/ws/test-stream")
async def test_stream(websocket: WebSocket):
    """
    WebSocket endpoint for live test execution streaming.

    Protocol:
      1. Client connects.
      2. Client sends: {"project": "steps", "headless": true}
      3. Server streams step events until {"type": "done"}.
    """
    await websocket.accept()

    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=30)
        config = json.loads(raw)
    except (asyncio.TimeoutError, json.JSONDecodeError) as e:
        await websocket.send_json({"type": "error", "message": f"Bad handshake: {e}"})
        await websocket.close()
        return

    # This stream runs flows. The portal does not use it; scripts may, with the
    # API_TOKEN from .env in the handshake: {"project": …, "token": "<API_TOKEN>"}.
    # Without that any web page could open it and run tests.
    import hmac
    from api.auth import _api_token
    expected = _api_token()
    if not expected or not hmac.compare_digest(str(config.get("token") or ""), expected):
        await websocket.send_json({"type": "error",
                                   "message": "Not authorised: send the API_TOKEN from .env as 'token'."})
        await websocket.close(code=1008)
        return

    project = config.get("project", "steps")
    headless = bool(config.get("headless", True))
    flow_path = _flow_path(project)

    if not os.path.exists(flow_path):
        await websocket.send_json({"type": "error", "message": f"Project '{project}' not found."})
        await websocket.close()
        return

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _send(msg: dict):
        """Thread-safe bridge: puts message into the async queue."""
        loop.call_soon_threadsafe(queue.put_nowait, msg)

    # Launch blocking Playwright run in thread pool
    future = loop.run_in_executor(_executor, _run_flow_streaming, flow_path, headless, _send)

    try:
        while True:
            msg = await asyncio.wait_for(queue.get(), timeout=120)
            await websocket.send_json(msg)
            if msg.get("type") == "done":
                break
    except asyncio.TimeoutError:
        await websocket.send_json({"type": "error", "message": "Execution timed out."})
    except WebSocketDisconnect:
        pass
    finally:
        future.cancel()
