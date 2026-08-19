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
    #: Which environment's saved Test Data counts as supplied for this run.
    environment: str = ""
    #: "allow" | "deny" | "" — how to answer BROWSER permission prompts
    #: (geolocation, notifications, camera…). Site popups are not covered; those
    #: are page content and belong to the test.
    browser_permissions: str = ""
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
                   secret_parameters: list | None = None,
                   environment: str = "") -> dict:
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
    # Saved Test Data first, then this run's own values on top.
    #
    # The pre-flight check already counted the store as a source, so a flow
    # whose values were all saved was allowed to start — and then failed on
    # every step with "Variable '${username}' is not stored in memory!", because
    # nothing ever put the store INTO the run. Validated as available and never
    # supplied is the worst of both.
    try:
        from execution.test_data import resolved

        for name, value in resolved(environment).items():
            RUNTIME_VARIABLES[name] = str(value)
        logger.info("📦 Test Data loaded: %s", ", ".join(sorted(resolved(environment)))
                    or "nothing stored")
    except Exception as e:  # noqa: BLE001 — a run must not die on the store
        logger.warning("Could not load Test Data for this run: %s", e)

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
        "browser_permissions": body.browser_permissions or "",
    }

    # A flow that needs values must not be launched without them. Starting anyway
    # produced one cryptic "Variable '${x}' is not stored in memory!" per step —
    # five failures describing the same single omission, none of them saying what
    # to do. Say it once, before the browser opens.
    try:
        with open(flow_path, "r", encoding="utf-8") as f:
            flow_text = f.read()
        from ai_flow_builder.emitter import run_parameters
        from execution.test_data import get_all

        needed = run_parameters([ln for ln in flow_text.splitlines()
                                 if ln.strip() and not ln.strip().startswith("#")])
        supplied = set(body.parameters or {}) | set(get_all(body.environment).keys())
        missing = [n for n in needed if n not in supplied]
    except Exception:  # noqa: BLE001 — never block a run on this check failing
        missing = []

    if missing:
        raise HTTPException(
            status_code=422,
            detail=(f"This test needs a value for: {', '.join(missing)}. "
                    f"Enter them in Run Center, or save them under Test Data so "
                    f"every run picks them up."),
        )

    # Remember how this flow was launched so it can be repeated without
    # re-answering every question. Secret VALUES are never written — only the
    # names, so Quick Run knows what to ask for again rather than storing it.
    _remember_setup(body)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")

    _remember(run_id, {"status": "running", "result": None})

    def _task():
        _run_flow_sync(run_id, flow_path, body.headless, capabilities=caps,
                       parameters=body.parameters,
                       secret_parameters=body.secret_parameters,
                       environment=getattr(body, "environment", ""))

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


#: Last-used launch settings per flow, so a repeat run needs no setup.
LAST_SETUP_PATH = os.path.join(LOGS_DIR, "_last_run_setup.json")


def _remember_setup(body) -> None:
    """Persist how a flow was launched. Never stores a secret value."""
    try:
        current = {}
        if os.path.exists(LAST_SETUP_PATH):
            with open(LAST_SETUP_PATH, "r", encoding="utf-8") as f:
                current = json.load(f) or {}
        secret = set(body.secret_parameters or [])
        current[body.project] = {
            "platform": body.platform,
            "headless": body.headless,
            "device_name": body.device_name,
            "browser": body.browser,
            "environment": getattr(body, "environment", ""),
            "browser_permissions": getattr(body, "browser_permissions", ""),
            # Non-secret values are kept so a repeat is genuinely one click.
            # A secret is recorded by NAME only and must be supplied again.
            "parameters": {k: v for k, v in (body.parameters or {}).items()
                           if k not in secret and not _is_secret(k)},
            "needs_secrets": sorted(n for n in (body.parameters or {})
                                    if n in secret or _is_secret(n)),
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        tmp = LAST_SETUP_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
        os.replace(tmp, LAST_SETUP_PATH)
    except Exception as e:  # noqa: BLE001 — never fail a run over bookkeeping
        logger.debug("Could not record last-run setup: %s", e)


@router.get("/last-setup/{flow}")
def last_setup(flow: str):
    """
    How this flow was launched last time, for Quick Run.

    Returns {} when it has never been run, so the caller shows the full setup
    form rather than a Quick Run button that would launch something unspecified.
    """
    try:
        with open(LAST_SETUP_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get(flow, {})
    except (OSError, json.JSONDecodeError):
        return {}


@router.get("/history")
def run_history(limit: int = 50):
    """
    Past runs, newest first, with enough detail to scan them.

    /results returns bare filenames, which is why nothing could show a history:
    the caller would have had to fetch every report just to learn what flow it
    was for and whether it passed. This reads each report's summary once.
    """
    rows = []
    for fname in sorted(os.listdir(LOGS_DIR), reverse=True):
        if not fname.endswith(".json"):
            continue
        # Reports for fixture flows (a leading underscore) come from the tool's
        # own test suite. They are not runs the author made and burying real
        # runs under dozens of them makes the screen useless.
        if fname.startswith("report___") or fname.startswith("report__"):
            continue
        run_id = fname[:-len(".json")]
        try:
            with open(os.path.join(LOGS_DIR, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            # A half-written or hand-edited report must not hide the rest.
            rows.append({"run_id": run_id, "flow": "", "started_at": "",
                         "summary": {}, "status": "unreadable", "steps": 0})
            continue
        summary = data.get("summary", {}) or {}
        failed = int(summary.get("failed", 0) or 0)
        total = int(summary.get("total", 0) or 0)
        rows.append({
            "run_id": run_id,
            "flow": data.get("testplan", ""),
            "executer": data.get("executer", ""),
            "started_at": data.get("started_at", "") or data.get("generated_at", ""),
            "summary": summary,
            "steps": total,
            "status": "passed" if total and not failed else ("failed" if failed else "empty"),
        })
        if len(rows) >= limit:
            break
    return {"runs": rows}


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
