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
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
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
    #: Stop at the first failing step. On by default: once a step fails the page
    #: is no longer where the test believes it is, so what follows tests nothing
    #: and can act on the wrong screen. Set false to run everything regardless.
    stop_on_failure: bool = True
    #: Attach this domain's HTTP Basic credentials to the browser context rather
    #: than embedding them in the URL. Empty (the default) keeps the URL path,
    #: which is what existing setups are proven against. The domains that
    #: have credentials come from AUTH_<NAME>_DOMAIN entries in .env.
    http_auth_domain: str = ""
    #: "allow" | "deny" | "" — how to answer BROWSER permission prompts
    #: (geolocation, notifications, camera…). Site popups are not covered; those
    #: are page content and belong to the test.
    browser_permissions: str = ""
    #: all | key | failure | off — see reporting/step_capture.py. A screenshot
    #: per step is what makes a failed run readable; the mode is what stops it
    #: costing tens of gigabytes a month.
    screenshot_mode: str = "all"
    #: For screenshot_mode="failure": how many steps BEFORE the failure to keep.
    screenshot_context: int = 5
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


def _runnable(lines: list, after: int):
    """(line number, step) for every real step after `after` — comments skipped."""
    for n, raw in enumerate(lines, 1):
        if n <= after:
            continue
        step = raw.strip()
        if step and not step.startswith("#"):
            yield n, step


def _rest(lines: list, after: int) -> list:
    return list(_runnable(lines, after))


def _remaining(lines: list, after: int) -> int:
    return len(_rest(lines, after))


def _run_flow_sync(run_id: str, flow_path: str, headless: bool,
                   capabilities: dict | None = None,
                   parameters: dict | None = None,
                   secret_parameters: list | None = None,
                   environment: str = "",
                   stop_on_failure: bool = True,
                   screenshot_mode: str = "all",
                   screenshot_context: int = 5) -> dict:
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
    passed = failed = skipped_count = 0
    from reporting.step_capture import StepCapture

    shots = StepCapture(run_id, mode=screenshot_mode, context=screenshot_context)

    try:
        page = open_browser(session, capabilities=capabilities or None)

        with open(flow_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line_num, raw in enumerate(lines, 1):
            step = raw.strip()
            if not step or step.startswith("#"):
                continue

            entry: dict = {"line": line_num, "step": step}
            started = time.perf_counter()

            try:
                # Same interpreter the CLI uses, so a step behaves identically
                # whether it was launched from the terminal or over HTTP.
                # (Previously this also read runner._VARIABLES, which does not
                # exist — the AttributeError failed EVERY step of every API run.)
                from runner import _interpret

                _interpret(step, page)
                entry["status"] = "passed"
                entry["duration_ms"] = round((time.perf_counter() - started) * 1000)
                passed += 1
                # The report row is created FIRST and handed to the capture, so
                # a frame promoted later — "failure" mode only learns a step
                # mattered when a later one fails — can still be written into
                # the row that was already recorded.
                row = report.add_result(step, "passed",
                                        duration_ms=entry["duration_ms"])
                shots.after_step(page, entry, step, row)
            except Exception as e:
                entry["status"] = "failed"
                entry["error"] = str(e).strip()
                entry["duration_ms"] = round((time.perf_counter() - started) * 1000)
                failed += 1
                row = report.add_result(step, "failed", reason=str(e).strip(),
                                        duration_ms=entry["duration_ms"])
                shots.after_step(page, entry, step, row)
                log.append(entry)
                if stop_on_failure:
                    # Everything after a failure is running against a page that
                    # is not where the test thinks it is. Those steps do not
                    # test anything — they produce a second, unrelated failure
                    # that buries the first, and on a form they can submit
                    # half-entered data. Stop, and say what was not reached.
                    logger.error("⛔ Step %d failed — stopping. %d step(s) not run.",
                                 line_num, _remaining(lines, line_num))
                    for skipped_no, skipped in _rest(lines, line_num):
                        log.append({"line": skipped_no, "step": skipped,
                                    "status": "skipped",
                                    "error": "not run — an earlier step failed"})
                        report.add_result(skipped, "skipped",
                                          reason="not run — an earlier step failed")
                        skipped_count += 1
                    break

            log.append(entry)

    except Exception as e:
        log.append({"line": 0, "step": "ENGINE", "status": "failed", "error": str(e)})
        failed += 1

    finally:
        # Before the browser closes: in failure mode this deletes the rolling
        # window that no failure ever claimed.
        shots.finish()
        if page is not None:
            try:
                close_browser(page, project_name, session)
            except Exception as e:  # noqa: BLE001
                # Not fatal to the run's result, but a browser that would not
                # close leaks a process — record it rather than losing it.
                logger.warning("Browser cleanup failed for run %s: %s", run_id, e)

    # Recorded on the report itself, not just on the in-memory summary: the
    # report file outlives the process, and it is the file the detail page reads
    # when it explains why a step has no image.
    report.meta["screenshots"] = shots.summary()
    json_path, _ = report.generate_report(LOGS_DIR)

    summary = {
        "run_id": run_id,
        "project": project_name,
        "total": passed + failed + skipped_count,
        "passed": passed,
        "failed": failed,
        # Counted separately from failed: a step that never ran did not fail,
        # and a run that stops at step 3 of 14 should not read as 11 defects.
        "skipped": skipped_count,
        "stopped_early": bool(skipped_count),
        # Which capture mode ran, how many frames it kept, and whether the cap
        # was hit. Without it, "there is no screenshot for step 12" is
        # indistinguishable from a bug.
        "screenshots": shots.summary(),
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

def _auth_domain_for(flow_path: str) -> str:
    """
    The first domain this flow opens that we hold HTTP Basic credentials for.

    Offered as a SUGGESTION — Run Center shows it so the option is discoverable
    — never applied on its own. A Basic-auth prompt is drawn by the browser, not
    the page, so no locator can reach it; but which of the two ways of answering
    it works is a property of the server, not something to decide for someone.
    """
    from urllib.parse import urlparse

    try:
        from config.settings import get_auth_registry

        registry = get_auth_registry()
        if not registry:
            return ""
        with open(flow_path, "r", encoding="utf-8") as f:
            text = f.read()
        for raw in re.findall(r"https?://[^\s\"']+", text):
            host = urlparse(raw).netloc.split("@")[-1]
            if host in registry:
                return host
    except Exception:  # noqa: BLE001 — auth discovery must never block a run
        return ""
    return ""


def _declared_platform(flow_path: str) -> str | None:
    """
    The platform a flow declares in its "# Platform:" header, or None when the
    file has no header. Unlike projects._platform_of this never guesses from
    the file name, so a header-less flow keeps whatever platform was requested.
    """
    try:
        with open(flow_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                if not line.startswith("#"):
                    return None
                low = line.lower()
                if low.startswith("#") and "platform" in low and ":" in low:
                    from nlp.platforms import normalise
                    try:
                        return normalise(low.split(":", 1)[1].strip())
                    except Exception:  # noqa: BLE001
                        return None
    except OSError:
        return None
    return None


def _start_run_thread(run_id: str, flow_path: str, body: RunRequest, caps: dict) -> None:
    """
    Start the run on a worker thread and return immediately to the caller.

    FastAPI background tasks run after the response is prepared, but with the
    in-process ASGI client the UI waits for the whole request lifecycle. That
    kept Run Center on-screen until execution finished, so the live "running"
    page never appeared when the operator clicked Run.
    """

    thread = threading.Thread(
        target=_run_flow_sync,
        args=(run_id, flow_path, body.headless),
        kwargs={
            "capabilities": caps,
            "parameters": body.parameters,
            "secret_parameters": body.secret_parameters,
            "environment": getattr(body, "environment", ""),
            "stop_on_failure": getattr(body, "stop_on_failure", True),
            "screenshot_mode": getattr(body, "screenshot_mode", "all"),
            "screenshot_context": getattr(body, "screenshot_context", 5),
        },
        name=f"flow-run-{run_id}",
        daemon=True,
    )
    thread.start()


@router.post("/run")
def run_test(body: RunRequest):
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

    # The flow's own "# Platform:" header is authoritative. A mobilesite flow
    # was being launched as website whenever the caller omitted the platform
    # (the request model defaults to "website") or carried a stale value from
    # another page, and Playwright then opened a desktop context with no device
    # emulation. The header is what the author declared, so it wins; the
    # requested value is only used when the file does not declare one.
    declared = _declared_platform(flow_path)
    requested = (body.platform or "").strip()
    platform_source = "request"
    try:
        if declared and (not requested or normalise(requested) != declared):
            if requested:
                logger.info("Flow '%s' declares platform '%s'; request said '%s' - "
                            "using the flow's declaration", body.project, declared, requested)
            platform = resolve(declared)
            platform_source = "flow header"
        else:
            platform = resolve(normalise(requested))
    except UnknownPlatform as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    body.platform = platform.name
    if not platform.enabled:
        raise HTTPException(
            status_code=422,
            detail=f"Platform '{platform.name}' ({platform.label}) is not enabled yet.",
        )

    # Context-level HTTP auth is OPT-IN, and deliberately so.
    #
    # It was briefly automatic: any flow opening a domain in the auth registry
    # got credentials attached to the browser context. That is the better
    # mechanism on paper — nothing lands in a URL — but it changes how the very
    # first navigation is made, and it stopped a staging site loading that had
    # been loading fine on the URL-embedded path. A theoretical improvement is
    # not worth a working run, so the old path stays the default and this is
    # asked for explicitly, per run.
    auth_domain = (body.http_auth_domain or "").strip()

    device = body.device_name or platform.default_device or ""
    caps = {
        "headless": body.headless,
        "mobile_web": bool(device),
        "device_name": device,
        "browser": body.browser or "",
        "browser_permissions": body.browser_permissions or "",
    }
    if auth_domain:
        caps["http_auth_domain"] = auth_domain

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

    _start_run_thread(run_id, flow_path, body, caps)

    return {
        "run_id": run_id,
        "status": "running",
        "poll_url": f"/tests/results/{run_id}",
        "platform": platform.name,
        "platform_source": platform_source,
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
    # Sorted by the report's own timestamp, not by filename. The name begins
    # with the FLOW, so a reverse filename sort ordered alphabetically by test
    # case and only then by date — "newest first" put a week-old tc_search run
    # above one from this morning, and anything reading row 0 as "the last run"
    # got the wrong one.
    def _stamp(fn: str) -> str:
        m = re.search(r"(\d{8}_\d{6})", fn)
        return m.group(1) if m else ""

    for fname in sorted(os.listdir(LOGS_DIR), key=_stamp, reverse=True):
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
