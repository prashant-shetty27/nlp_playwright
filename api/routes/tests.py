"""
api/routes/tests.py
POST /tests/run              — run a .flow file synchronously; returns full report
GET  /tests/results          — list all saved reports from data/logs/
GET  /tests/results/{run_id} — return one saved JSON report by run_id (timestamp)
"""
import json
import os
from collections import OrderedDict
import re
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, field_validator

from config.settings import LOGS_DIR

import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tests", tags=["tests"])

os.makedirs(LOGS_DIR, exist_ok=True)

# In-memory run registry  {run_id → status dict}  — cleared on restart
#: Completed runs are kept so the caller can poll for a result — but every entry
#: holds the full per-step log, so an unbounded dict grows for the life of the
#: process and is never reclaimed. A long-lived server running thousands of flows
#: would hold every step of every one of them. Bounded, oldest evicted first.
MAX_RETAINED_RUNS = int(os.getenv("MAX_RETAINED_RUNS", "200"))
_runs: "OrderedDict[str, dict]" = OrderedDict()


def _remember(run_id: str, payload: dict) -> None:
    """Record a run and evict the oldest once the cap is reached."""
    with _runs_lock:
        _runs[run_id] = payload
        _runs.move_to_end(run_id)
        while len(_runs) > MAX_RETAINED_RUNS:
            _runs.popitem(last=False)
_runs_lock = threading.Lock()


# ── Request models ─────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    project: str          # e.g. "steps"  (maps to flows/steps.flow)
    headless: bool = True
    # Which runner and browser context to execute in. Without this the run always
    # used a desktop Chromium context regardless of what the flow was written for.
    platform: str = "website"
    device_name: str = ""      # overrides the platform's default device
    browser: str = ""          # overrides the engine the device would choose

    @field_validator("browser")
    @classmethod
    def _known_engine(cls, v: str) -> str:
        """Reject an unknown engine at the edge, with the valid set named."""
        if v and v.strip().lower() not in {"chromium", "firefox", "webkit"}:
            raise ValueError(
                f"unknown browser {v!r}; valid engines: chromium, firefox, webkit"
            )
        return v.strip().lower()
    # Values for the ${...} placeholders the flow references. A generated flow is
    # not executable without these — `open ${product_url}` has nothing to open
    # until product_url has a value.
    parameters: dict[str, str] = {}
    # Names within `parameters` whose values must never be logged or echoed back.
    secret_parameters: list[str] = []


# ── Helpers ────────────────────────────────────────────────────────────────────

def _flow_path(project: str) -> str:
    from config.settings import FLOWS_DIR
    safe = os.path.basename(project)
    if not safe.endswith(".flow"):
        safe += ".flow"
    return os.path.join(FLOWS_DIR, safe)


#: Masking policy lives in config.settings so the CLI runner can share it without
#: importing this module (and with it, FastAPI) into a terminal code path.
from config.settings import is_secret_name as _is_secret  # noqa: E402


def _run_flow_sync(run_id: str, flow_path: str, headless: bool,
                   capabilities: dict | None = None,
                   parameters: dict | None = None,
                   secret_parameters: list | None = None) -> dict:
    """
    Runs the NLP flow in a thread, captures step results,
    persists a JSON report, and returns the summary dict.
    """
    import os as _os
    _os.environ["HEADLESS"] = "true" if headless else "false"

    # Lazy import here so the module is loaded in this thread's context
    import execution.action_service  # noqa — registers @codeless_snippet
    from nlp.parser import parse_step
    from locators.cleaner import sanitize_database
    from execution.browser_manager import open_browser, close_browser
    from execution.session import TestSession
    from reporting.report_manager import TestReportManager

    from nlp.variable_manager import RUNTIME_VARIABLES, bind_runtime_variables

    sanitize_database()

    project_name = os.path.basename(flow_path).replace(".flow", "")
    report = TestReportManager(testplan_name=project_name, executer_name="api")

    session = TestSession()
    # Bind the session's own variable store BEFORE injecting, so values land where
    # the running flow will look for them.
    bind_runtime_variables(session.runtime_variables)

    declared = set(secret_parameters or [])
    injected = []
    for name, value in (parameters or {}).items():
        key = str(name).strip()
        if not key:
            continue
        RUNTIME_VARIABLES[key] = str(value)
        injected.append(key)
        # Never log the value — only that a value arrived.
        logger.info("🔑 Parameter set: ${%s} = %s", key,
                    "<hidden>" if _is_secret(key, declared) else f"'{value}'")

    # Inside the try: launching can fail on a bad capability (an unknown browser
    # engine raises), and a failure out here left _runs[run_id] pinned at
    # "running" forever — the caller polls a run that will never finish.
    page = None
    log: list[dict] = []
    passed = failed = 0

    try:
        page = open_browser(session, capabilities=capabilities or None)

        with open(flow_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line_num, raw in enumerate(lines, 1):
            step = raw.strip()
            if not step or step.startswith("#"):
                continue

            entry: dict = {"line": line_num, "step": step}

            try:
                # Same interpreter the CLI uses, so a step behaves identically
                # whether it was launched from the terminal or over HTTP.
                # (Previously this also read runner._VARIABLES, which does not
                # exist — the AttributeError failed EVERY step of every API run.)
                from runner import _interpret

                _interpret(step, page)
                entry["status"] = "passed"
                passed += 1
                report.add_result(step, "passed")
            except Exception as e:
                entry["status"] = "failed"
                entry["error"] = str(e).strip()
                failed += 1
                report.add_result(step, "failed", reason=str(e).strip())

            log.append(entry)

    except Exception as e:
        log.append({"line": 0, "step": "ENGINE", "status": "failed", "error": str(e)})
        failed += 1

    finally:
        if page is not None:
            try:
                close_browser(page, project_name, session)
            except Exception as e:  # noqa: BLE001
                # Not fatal to the run's result, but a browser that would not
                # close leaks a process — record it rather than losing it.
                logger.warning("Browser cleanup failed for run %s: %s", run_id, e)

    json_path, _ = report.generate_report(LOGS_DIR)

    summary = {
        "run_id": run_id,
        "project": project_name,
        "total": passed + failed,
        "passed": passed,
        "failed": failed,
        "log": log,
        "report_file": json_path,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }

    with _runs_lock:
        # The entry may have been evicted while this run was executing; recording
        # it again is correct — a finished run is more useful than a lost one.
        _runs[run_id] = {"status": "done", "result": summary}
        _runs.move_to_end(run_id)
        while len(_runs) > MAX_RETAINED_RUNS:
            _runs.popitem(last=False)

    return summary


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/run")
def run_test(body: RunRequest, background_tasks: BackgroundTasks):
    """
    Launch a .flow run.  Returns run_id immediately; result available via
    GET /tests/results/{run_id}.

    For synchronous blocking execution (small flows) the result is also
    returned directly once the background task completes — use
    GET /tests/results/{run_id} to poll.
    """
    flow_path = _flow_path(body.project)
    if not os.path.exists(flow_path):
        raise HTTPException(status_code=404, detail=f"Project '{body.project}' not found.")

    from nlp.platforms import UnknownPlatform, normalise, resolve

    try:
        platform = resolve(normalise(body.platform))
    except UnknownPlatform as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if not platform.enabled:
        raise HTTPException(
            status_code=422,
            detail=f"Platform '{platform.name}' ({platform.label}) is not enabled yet.",
        )

    device = body.device_name or platform.default_device or ""
    caps = {
        "headless": body.headless,
        "mobile_web": bool(device),
        "device_name": device,
        "browser": body.browser or "",
    }

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")

    _remember(run_id, {"status": "running", "result": None})

    def _task():
        _run_flow_sync(run_id, flow_path, body.headless, capabilities=caps,
                       parameters=body.parameters,
                       secret_parameters=body.secret_parameters)

    background_tasks.add_task(_task)

    return {
        "run_id": run_id,
        "status": "running",
        "poll_url": f"/tests/results/{run_id}",
        "platform": platform.name,
        "device_name": device or None,
        # Names only — a value echoed back is a value leaked.
        "parameters_set": sorted(body.parameters or {}),
    }


@router.get("/results")
def list_results():
    """
    List saved report files from data/logs/ plus any in-memory runs
    from the current server session.
    """
    saved = []
    for fname in sorted(os.listdir(LOGS_DIR), reverse=True):
        if fname.endswith(".json"):
            saved.append(fname.replace(".json", ""))

    in_memory = []
    with _runs_lock:
        for run_id, info in _runs.items():
            in_memory.append({"run_id": run_id, "status": info["status"]})

    return {"saved_reports": saved, "session_runs": in_memory}


@router.get("/results/{run_id}")
def get_result(run_id: str):
    """
    Return the result of a run by run_id.
    Checks in-memory first, then falls back to the saved JSON report.
    """
    # Check in-memory session runs first
    with _runs_lock:
        info = _runs.get(run_id)

    if info:
        if info["status"] == "running":
            return {"run_id": run_id, "status": "running"}
        return info["result"]

    # Fall back to persisted report file
    # run_id doubles as the timestamp portion of the filename
    for fname in os.listdir(LOGS_DIR):
        if fname.endswith(".json") and run_id in fname:
            path = os.path.join(LOGS_DIR, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to read report: {e}")

    raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
