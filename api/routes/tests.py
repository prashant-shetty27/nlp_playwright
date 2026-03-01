"""
api/routes/tests.py
POST /tests/run              — run a .flow file synchronously; returns full report
GET  /tests/results          — list all saved reports from data/logs/
GET  /tests/results/{run_id} — return one saved JSON report by run_id (timestamp)
"""
import json
import os
import re
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from config.settings import LOGS_DIR

router = APIRouter(prefix="/tests", tags=["tests"])

os.makedirs(LOGS_DIR, exist_ok=True)

# In-memory run registry  {run_id → status dict}  — cleared on restart
_runs: dict[str, dict] = {}
_runs_lock = threading.Lock()


# ── Request models ─────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    project: str          # e.g. "steps"  (maps to flows/steps.flow)
    headless: bool = True


# ── Helpers ────────────────────────────────────────────────────────────────────

def _flow_path(project: str) -> str:
    from config.settings import FLOWS_DIR
    safe = os.path.basename(project)
    if not safe.endswith(".flow"):
        safe += ".flow"
    return os.path.join(FLOWS_DIR, safe)


def _run_flow_sync(run_id: str, flow_path: str, headless: bool) -> dict:
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

    sanitize_database()

    project_name = os.path.basename(flow_path).replace(".flow", "")
    report = TestReportManager(testplan_name=project_name, executer_name="api")

    session = TestSession()
    page = open_browser(session)
    log: list[dict] = []
    passed = failed = 0

    try:
        with open(flow_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line_num, raw in enumerate(lines, 1):
            step = raw.strip()
            if not step or step.startswith("#"):
                continue

            entry: dict = {"line": line_num, "step": step}

            try:
                # variable resolution is skipped here for simplicity;
                # runner.py handles it for CLI; API uses the same logic:
                from runner import _interpret
                _variables_backup: dict = {}
                import runner as _runner
                _variables_backup = dict(_runner._VARIABLES)
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
        try:
            close_browser(page, project_name, session)
        except Exception:
            pass

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
        _runs[run_id]["status"] = "done"
        _runs[run_id]["result"] = summary

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

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")

    with _runs_lock:
        _runs[run_id] = {"status": "running", "result": None}

    def _task():
        _run_flow_sync(run_id, flow_path, body.headless)

    background_tasks.add_task(_task)

    return {
        "run_id": run_id,
        "status": "running",
        "poll_url": f"/tests/results/{run_id}",
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
