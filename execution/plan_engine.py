"""
execution/plan_engine.py — runs a Test Plan, now or on schedule.

Each test case goes through exactly the path Run Center uses
(api.routes.tests._prepare_run + _run_flow_sync), so a scheduled run opens
the same device, loads the same Test Data, takes the same screenshots and
lands in History / Reports like any other run. `# OFF:` lines are comments
to the runner, so switched-off steps (e.g. lead submission) never run.

One plan runs at a time on this machine (one browser, one VPN); a second
request waits its turn and shows as "queued".

A plan run is recorded in data/plan_runs/<id>.json and updated as it goes,
so the Plan Run page can follow it live.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

from config.settings import DATA_DIR

logger = logging.getLogger(__name__)
RUNS_DIR = os.path.join(DATA_DIR, "plan_runs")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
_exec_lock = threading.Lock()          # one plan executes at a time
_file_lock = threading.Lock()
_abort: set[str] = set()
#: "Stop now" (not "after the current test case") — parallel workers are terminated.
_abort_now: set[str] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(run_id: str) -> str:
    return os.path.join(RUNS_DIR, f"{os.path.basename(run_id)}.json")


def _save(rec: dict) -> None:
    os.makedirs(RUNS_DIR, exist_ok=True)
    with _file_lock:
        tmp = _path(rec["id"]) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=2, ensure_ascii=False)
        os.replace(tmp, _path(rec["id"]))


def get_run(run_id: str) -> dict:
    with open(_path(run_id), "r", encoding="utf-8") as f:
        return json.load(f)


def list_runs(plan_id: str = "", limit: int = 50) -> list[dict]:
    if not os.path.isdir(RUNS_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(RUNS_DIR), reverse=True):
        if not fn.endswith(".json"):
            continue
        try:
            r = get_run(fn[:-5])
        except (ValueError, OSError):
            continue
        if plan_id and r.get("plan_id") != plan_id:
            continue
        out.append({k: r.get(k) for k in ("id", "plan_id", "plan_name", "status", "trigger",
                                         "triggered_by", "started_at", "finished_at",
                                         "totals", "queued_at", "duration_s", "run_type")})
        if len(out) >= limit:
            break
    return out


def active_run() -> str:
    """The run that is actually executing; a queued one only when nothing runs."""
    runs = list_runs(limit=20)
    for status in ("running", "queued"):
        for r in runs:
            if r["status"] == status:
                return r["id"]
    return ""


def request_stop(run_id: str, now: bool = False) -> None:
    """Stop after the current test case — or, with now=True, at the next step."""
    _abort.add(run_id)
    if now:
        _abort_now.add(run_id)
        try:
            rec = get_run(run_id)
        except (OSError, ValueError):
            return
        from api.routes import tests as T
        for it in rec.get("items") or []:
            if it.get("status") == "running" and it.get("run_id"):
                T.cancel(it["run_id"])


# ── health: is a running plan actually moving? ─────────────────────────────
#: A single step running longer than this is treated as stuck. Real steps
#: (open, wait, verify) finish in seconds; the slowest waits are ~1 minute.
STUCK_STEP_MIN = float(os.getenv("STUCK_STEP_MINUTES", "5"))
#: Waiting in the queue longer than this is worth a warning.
STUCK_QUEUE_MIN = float(os.getenv("STUCK_QUEUE_MINUTES", "30"))


def _age_min(iso: str) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 60
    except (TypeError, ValueError):
        return 0.0


def health(rec: dict) -> dict:
    """
    What the running test case is doing right now, and whether it looks stuck.

    kinds: "ok", "step" (one step running > STUCK_STEP_MIN), "orphaned" (the
    record says running but this server is not executing it — it was
    restarted), "queued" (waiting behind another plan for too long).
    """
    out: dict = {"stuck": False, "kind": "ok"}
    st = rec.get("status")
    if st == "queued":
        age = _age_min(rec.get("queued_at", ""))
        if age > STUCK_QUEUE_MIN:
            out.update(stuck=True, kind="queued", minutes=round(age, 1),
                       message=f"Waiting in the queue for {age:.0f} min — another plan is still running.")
        return out
    if st != "running":
        return out
    item = next((i for i in rec.get("items") or [] if i.get("status") == "running"), None)
    if not item:
        return out
    out.update(test_case=item.get("test_case"), run_id=item.get("run_id", ""),
               platform=item.get("platform") or "website")
    from api.routes import tests as T
    prog = T.progress_of(item.get("run_id", "")) if item.get("run_id") else None
    if prog is None:
        # Nothing in this process is running it. Either it has only just been
        # queued (seconds), or the server was restarted mid-run.
        if _age_min(item.get("started_at", "")) > 1:
            out.update(stuck=True, kind="orphaned",
                       message="This server is no longer executing the run (it was restarted "
                               "or crashed while the plan was running).")
        return out
    log = prog.get("log") or []
    cur = next((e for e in reversed(log) if e.get("status") == "running"), None)
    done = sum(1 for e in log if e.get("status") != "running")
    out.update(done=done, total=prog.get("total") or 0,
               passed=prog.get("passed", 0), failed=prog.get("failed", 0))
    if cur:
        mins = (time.time() - float(cur.get("since") or time.time())) / 60
        out.update(step=cur.get("step", ""), line=cur.get("line"), step_minutes=round(mins, 1))
        if mins > STUCK_STEP_MIN:
            out.update(stuck=True, kind="step", minutes=round(mins, 1),
                       message=(f"Step {done + 1} (line {cur.get('line')}) of "
                                f"{item.get('test_case')} has been running for {mins:.0f} min."))
    return out


def recover_orphans() -> int:
    """On server start: close records left 'running'/'queued' by a restart."""
    if not os.path.isdir(RUNS_DIR):
        return 0
    n = 0
    for fn in os.listdir(RUNS_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            rec = get_run(fn[:-5])
        except (OSError, ValueError):
            continue
        if rec.get("status") not in ("running", "queued"):
            continue
        for it in rec.get("items") or []:
            if it.get("status") in ("running", "pending"):
                was = it["status"]
                it["status"] = "not_run"
                it["reason"] = ("interrupted — the server restarted while this test case was running"
                                if was == "running" else "not reached — the server restarted")
                it.setdefault("finished_at", _now())
        t = rec.setdefault("totals", {})
        items = rec.get("items") or []
        t["passed"] = sum(1 for i in items if i["status"] == "passed")
        t["failed"] = sum(1 for i in items if i["status"] == "failed")
        t["not_run"] = sum(1 for i in items if i["status"] == "not_run")
        rec["status"] = "error"
        rec["error"] = ("Interrupted: the server was restarted (or crashed) while this plan was "
                        "running. Re-run the plan.")
        rec["finished_at"] = _now()
        if rec.get("started_at"):
            try:
                rec["duration_s"] = int((datetime.fromisoformat(rec["finished_at"])
                                         - datetime.fromisoformat(rec["started_at"])).total_seconds())
            except ValueError:
                pass
        _save(rec)
        n += 1
    if n:
        logger.warning("📋 Closed %d plan run(s) interrupted by a restart", n)
    return n


# ── start ───────────────────────────────────────────────────────────────────
def _is_positive_case(path: str) -> bool:
    """True when the flow's '# Tags:' line carries smoke or sanity."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("# Tags:"):
                    tags = {t.strip().lower() for t in line[7:].split(",")}
                    return bool(tags & {"smoke", "sanity", "positive"})
                if line.strip() and not line.startswith("#"):
                    break
    except OSError:
        pass
    return False


def start(plan_id: str, *, trigger: str = "manual", user: str = "", run_type: str = "",
          only_failed_from: str = "") -> dict:
    """Create the run record and execute it on a worker thread.

    run_type overrides the plan's execution type for this one run (e.g. "Run now
    as Smoke"); the type's defaults (stop / retry / screenshots) come with it.
    only_failed_from = a previous run id: run only the test cases (per device)
    that failed or did not run there — passed ones are left out.
    """
    from core import plans, run_types, suites

    plan = plans.get(plan_id)
    execution = dict(plan.get("execution") or {})
    plan_type = run_types.normalise(execution.get("run_type")) or "full"
    rt = run_types.normalise(run_type) or plan_type
    if rt != plan_type:
        execution.update({k: v for k, v in run_types.DEFAULTS[rt].items()})
    execution["run_type"] = rt
    run_id = datetime.now(timezone.utc).strftime("PR_%Y%m%d_%H%M%S_") + plan_id[:30]
    if os.path.exists(_path(run_id)):          # double-click / schedule in the same second
        run_id += "_" + datetime.now(timezone.utc).strftime("%f")
    items = []
    disabled = []          # cases kept on a suite but switched off, shown on the report
    for s in plan["suites"]:
        try:
            suite = suites.get(s["id"])
            for x in suite.get("disabled") or []:
                disabled.append({"suite": s["name"], "suite_id": s["id"], **x})
        except suites.SuiteError:
            items.append({"suite": s["name"], "suite_id": s["id"], "test_case": "(missing suite)",
                          "status": "not_run", "reason": "suite no longer exists"})
            continue
        # One item per test case per device profile ("devices" in the plan's
        # execution settings); no profiles = the platform's default device.
        profiles = [d for d in (execution.get("devices") or []) if isinstance(d, dict)] or [{}]
        for tc in suite["test_cases"]:
          tc_platform = (tc.get("platform") or suite["platform"] or "website").lower()
          for prof in (profiles if tc_platform == "mobilesite" else [{}]):
            # A "positive only" browser runs just the smoke/sanity-tagged cases:
            # the main browsers get the full suite, the rest a happy-path check.
            if prof.get("coverage") == "positive" and not _is_positive_case(suites.script_path(tc["script"])):
                continue
            # "sample": this browser runs only the test cases named on it
            # (one per page type, say) — the main browser runs the full suite.
            if prof.get("coverage") == "sample" and tc["name"] not in (prof.get("only") or []):
                continue
            item = {"suite": suite["name"], "suite_id": suite["id"],
                    "test_case": tc["name"], "script": tc["script"],
                    "platform": tc.get("platform") or suite["platform"],
                    "status": "pending"}
            if prof:
                item["device"] = {k: prof.get(k, "") for k in ("device_name", "browser", "browser_identity")}
                item["device_label"] = prof.get("label") or " / ".join(
                    x for x in (prof.get("device_name"), prof.get("browser_identity") or prof.get("browser")) if x)
            if rt != "full":
                try:
                    pv = run_types.preview(suites.script_path(tc["script"]), rt)
                    item["planned_steps"] = pv["steps"]
                    item["bands_in"] = pv["bands_in"]
                    if pv["warnings"]:
                        item["warnings"] = pv["warnings"]
                    if not pv["steps"]:
                        item.update(status="not_run", out_of_scope=True,
                                    reason=f"no steps tagged for {run_types.LABEL[rt]} in this test case")
                except OSError:
                    pass
            items.append(item)
    if only_failed_from:
        # Re-run of the failed cases INSIDE the original run: same record, same
        # report. Each failed / not-run item goes back to pending (its earlier
        # result is kept under "reruns"); passed items are untouched. The plan
        # then has ONE result: first pass + re-run outcome.
        return rerun_failed(only_failed_from, user=user)
    rec = {"id": run_id, "plan_id": plan_id, "plan_name": plan["name"],
           "trigger": trigger, "triggered_by": user or ("scheduler" if trigger == "schedule" else "system"),
           "status": "queued", "queued_at": _now(), "started_at": "", "finished_at": "",
           "execution": execution, "run_type": rt, "notify": plan["notify"], "items": items,
           "disabled": disabled,
           "totals": {"test_cases": len(items), "passed": 0, "failed": 0, "not_run": 0}}
    _save(rec)
    threading.Thread(target=_execute, args=(rec,), name=f"plan-{run_id}", daemon=True).start()
    return rec


def rerun_failed(run_id: str, *, user: str = "") -> dict:
    """Re-run the failed / not-run test cases of a finished run, in that same
    run record. Earlier outcomes of each re-run item are kept in item["reruns"]
    (list of {status, run_id, first_failure, finished_at}) so the report can
    show 'failed first, passed on re-run'. Returns the updated record."""
    rec = get_run(run_id)
    if rec.get("status") in ("running", "queued"):
        raise ValueError("This run is still going.")
    redo = 0
    off, gone = {}, set()
    for sid in {it.get("suite_id") for it in rec.get("items") or [] if it.get("suite_id")}:
        try:
            sv = suites.get(sid)
        except suites.SuiteError:
            continue
        for x in sv.get("disabled") or []:
            off[(sid, x.get("test_case"))] = x
        now_on = {tc["name"] for tc in sv.get("test_cases") or []}
        for it in rec.get("items") or []:
            if it.get("suite_id") == sid and it.get("test_case") not in now_on \
                    and (sid, it.get("test_case")) not in off:
                gone.add((sid, it.get("test_case")))
                off[(sid, it.get("test_case"))] = {"test_case": it.get("test_case"),
                                                   "reason": "removed from the suite after the first pass"}
    rec["disabled"] = [{"suite_id": k[0], **x} for k, x in off.items() if k not in gone]
    for it in rec.get("items") or []:
        if it.get("status") in ("failed", "not_run") and not it.get("out_of_scope") \
                and (it.get("suite_id"), it.get("test_case")) in off:
            x = off[(it.get("suite_id"), it.get("test_case"))]
            it.setdefault("reruns", []).append({
                "status": it.get("status"), "run_id": it.get("run_id", ""),
                "first_failure": it.get("first_failure") or it.get("reason", ""),
                "finished_at": it.get("finished_at", ""), "duration_s": it.get("duration_s")})
            it["status"] = "not_run"
            it["out_of_scope"] = True
            it["reason"] = (x["reason"] if (it.get("suite_id"), it.get("test_case")) in gone
                            else f"disabled on the suite: {x.get('reason', '')}")
            continue
        if it.get("status") in ("failed", "not_run") and not it.get("out_of_scope"):
            it.setdefault("reruns", []).append({
                "status": it.get("status"), "run_id": it.get("run_id", ""),
                "first_failure": it.get("first_failure") or it.get("reason", ""),
                "finished_at": it.get("finished_at", ""), "duration_s": it.get("duration_s")})
            for k in ("run_id", "first_failure", "reason", "report_file", "finished_at", "started_at",
                      "duration_s", "passed_steps", "failed_steps", "skipped_steps", "note", "attempt",
                      "attempts", "attempts_s", "known", "lane_pid"):
                it.pop(k, None)
            it["status"] = "pending"
            redo += 1
    if not redo:
        raise ValueError("Nothing to re-run — no failed test case in this run.")
    rec["status"] = "queued"
    rec["finished_at"] = ""
    rec.setdefault("rerun_history", []).append({"at": _now(), "by": user, "test_cases": redo})
    rec["rerun_count"] = len(rec["rerun_history"])
    _save(rec)
    threading.Thread(target=_execute, args=(rec,), name=f"plan-{rec['id']}-rerun", daemon=True).start()
    return rec


#: Errors a second attempt can plausibly fix: timing, network, browser.
_FLAKY = ("timeout", "timed out", "net::", "err_", "target closed", "has been closed",
          "not visible", "detached", "navigation error", "econnreset", "max retries exceeded",
          "connection", "503", "502", "504", "crash")


#: A check that RAN and saw the wrong thing — re-running will not change it.
#: (Playwright assertion messages also say "with timeout 5000ms", so these win.)
_REAL = ("assertionerror", "expected", "match failed", "priority order broken", "mismatch",
         "is not stored in memory", "not found in any page", "invalid syntax", "unknown command")


def _looks_flaky(summary: dict) -> bool:
    errs = [str(e.get("error") or "").lower() for e in summary.get("log") or []
            if e.get("status") == "failed"]
    if not errs:
        return True             # failed with no step error (e.g. nothing ran) — worth one more go
    for err in errs:
        if any(k in err for k in _REAL):
            continue
        if any(k in err for k in _FLAKY):
            return True
    return False


def _execute(rec: dict) -> None:
    with _exec_lock:                       # wait for any running plan to finish
        try:
            _run(rec)
        except Exception as e:  # noqa: BLE001 — a crash must still close the record
            logger.exception("Plan run %s crashed", rec["id"])
            rec["status"] = "error"
            rec["error"] = str(e)[:500]
            rec["finished_at"] = _now()
            # Close every item too, or the report counts 'running' items as
            # neither passed, failed nor not run.
            for it in rec.get("items") or []:
                if it.get("status") in ("running", "pending"):
                    it["status"] = "not_run"
                    it["reason"] = "the plan run crashed before this test case finished"
                    it.setdefault("finished_at", _now())
            items = rec.get("items") or []
            t = rec.setdefault("totals", {})
            t["passed"] = sum(1 for i in items if i["status"] == "passed")
            t["failed"] = sum(1 for i in items if i["status"] == "failed")
            t["not_run"] = sum(1 for i in items if i["status"] == "not_run" and not i.get("out_of_scope"))
            _save(rec)
        finally:
            _abort.discard(rec["id"])
            _abort_now.discard(rec["id"])
    try:
        from core import plans
        plans.record_run(rec["plan_id"], rec)
    except Exception:  # noqa: BLE001
        logger.exception("Could not record plan run on the plan")
    try:
        from reporting.plan_report import generate
        rec["report"] = generate(rec)          # HTML + PDF, reused by Slack and email
        _save(rec)
    except Exception as e:  # noqa: BLE001
        logger.exception("Plan report failed")
        rec["report"] = {"error": str(e)[:300]}
    try:
        from reporting.plan_slack import notify
        notify(rec)
    except Exception as e:  # noqa: BLE001
        logger.exception("Slack notification failed")
        rec["slack"] = {"sent": False, "error": str(e)[:300]}
        _save(rec)
    try:
        from reporting.plan_email import send
        send(rec)
    except Exception as e:  # noqa: BLE001
        logger.exception("Email report failed")
        rec["email"] = {"sent": False, "error": str(e)[:300]}
        _save(rec)


def _run_item(rec: dict, item: dict, ex: dict) -> None:
    """One test case of a plan run, with its retries — used by the sequential
    loop and by execution/plan_worker.py (one process per test case)."""
    from fastapi import HTTPException

    from api.routes import tests as T
    from core import suites

    attempts = 1 + (1 if ex.get("retry_failed") else 0)
    retry_mode = ex.get("retry_mode") or "flaky"      # flaky | always
    for attempt in range(1, attempts + 1):
        dev = item.get("device") or {}
        body = T.RunRequest(project=item["test_case"], headless=bool(ex.get("headless", False)),
                            platform=item.get("platform") or "website",
                            stop_on_failure=bool(ex.get("stop_on_failure", False)),
                            screenshot_mode=ex.get("screenshot_mode") or "all",
                            device_name=dev.get("device_name") or "",
                            browser=dev.get("browser") or "",
                            browser_identity=dev.get("browser_identity") or "",
                            site_env=ex.get("site_env") or "")
        item.update(status="running", attempt=attempt, started_at=_now())
        _save(rec)
        try:
            flow_path, caps, *_ = T._prepare_run(body, suites.script_path(item["script"]))
        except HTTPException as e:
            item.update(status="not_run", reason=str(e.detail), finished_at=_now())
            break
        except Exception as e:  # noqa: BLE001
            item.update(status="not_run", reason=str(e)[:300], finished_at=_now())
            break
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        item["run_id"] = run_id
        item.setdefault("run_ids", []).append(run_id)
        _save(rec)
        T._remember(run_id, {"status": "running", "result": None})
        summary = T._run_flow_sync(run_id, flow_path, body.headless, capabilities=caps,
                                   triggered_by=rec["triggered_by"], plan_run=rec["id"],
                                   stop_on_failure=body.stop_on_failure,
                                   screenshot_mode=ex.get("screenshot_mode") or "all",
                                   run_type=rec.get("run_type") or "")
        try:
            item["duration_s"] = int((datetime.now(timezone.utc)
                                      - datetime.fromisoformat(item["started_at"])).total_seconds())
            item.setdefault("attempts_s", []).append(item["duration_s"])
        except (KeyError, ValueError):
            pass
        T._cancelled.discard(run_id)
        # Keep every attempt: the row shows the last one, but the first
        # attempt's report and failure are not lost to a retry.
        item.setdefault("attempts", []).append({
            "attempt": attempt, "run_id": run_id,
            "passed": summary.get("passed", 0), "failed": summary.get("failed", 0),
            "report_file": os.path.basename(summary.get("report_file") or ""),
            "first_failure": next((f"line {e.get('line')}: {e.get('step')} — {e.get('error', '')}"[:400]
                                   for e in summary.get("log", []) if e.get("status") == "failed"), "")})
        item.update(passed_steps=summary.get("passed", 0), failed_steps=summary.get("failed", 0),
                    skipped_steps=summary.get("skipped", 0), finished_at=_now(),
                    report_file=os.path.basename(summary.get("report_file") or ""),
                    first_failure=next((f"line {e.get('line')}: {e.get('step')} — {e.get('error', '')}"[:400]
                                        for e in summary.get("log", [])
                                        if e.get("status") == "failed"), ""))
        ok = summary.get("failed", 0) == 0 and summary.get("passed", 0) > 0
        stopped = rec["id"] in _abort or bool(summary.get("stopped_early"))
        if ok and stopped:
            # "Stop now" cut the run short with no failure yet. That is
            # not a pass — 3 of 10 steps ran — and it is not a defect.
            item["status"] = "not_run"
            item["reason"] = (f"stopped by user after step "
                              f"{summary.get('passed', 0)}")
            ok = False
        elif summary.get("total", 0) == 0:
            # Nothing runnable (every line is "# OFF:" or a comment) —
            # not a failure, and nothing a retry could change.
            item.update(status="not_run", out_of_scope=True,
                        reason="no runnable step (all lines are # OFF / comments)")
            _save(rec)
            break
        else:
            item["status"] = "passed" if ok else "failed"
        if ok and attempt > 1:
            item["note"] = "passed on retry"
        _save(rec)
        if ok or stopped:
            break
        if attempt < attempts and retry_mode != "always" and not _looks_flaky(summary):
            # A check that ran and found wrong data fails the same way on a
            # re-run — re-running a 129-step test case for it cost 7 min.
            item["note"] = "not retried — a check failed on real data, not a timeout / network error"
            _save(rec)
            break


#: Upper bound for execution.parallel — each lane is a browser (~1 core, ~0.7 GB).
MAX_PARALLEL = 6


def _expected_seconds(item: dict) -> float:
    """Median of this test case's recent report durations (unknown -> 60 s)."""
    import glob
    import json as _json
    import statistics
    name = item.get("test_case") or ""
    vals = []
    for f in sorted(glob.glob(os.path.join(LOGS_DIR, f"report_{name}_*.json")))[-6:]:
        try:
            with open(f, encoding="utf-8") as fh:
                d = _json.load(fh)
        except (OSError, ValueError):
            continue
        if d.get("testplan") != name:
            continue
        t = (d.get("timing") or {}).get("total_s")
        if t:
            vals.append(float(t))
    return statistics.median(vals) if vals else 60.0


def _run_parallel(rec: dict, ex: dict, lanes: int) -> None:
    """
    Run the plan's test cases `lanes` at a time, each in its own process
    (execution/plan_worker.py). Processes, not threads: a run keeps
    process-wide state (HEADLESS, runtime variables, the last typed mobile for
    ${otp}, caches) that two runs in one process would overwrite. Slowest test
    cases start first so the run is not left waiting on one long test at the end.
    """
    import json as _json
    import subprocess
    import sys
    import time

    from core import suites

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    lane_dir = os.path.join(RUNS_DIR, rec["id"] + "_lanes")
    os.makedirs(lane_dir, exist_ok=True)
    todo = [i for i, it in enumerate(rec["items"]) if it["status"] == "pending"]
    todo.sort(key=lambda i: -_expected_seconds(rec["items"][i]))
    # Test cases that share a login / session ("# Serial: <group>" header in
    # the .flow, e.g. the B2B Assist chat cases on one test number) must not
    # run at the same time: a second login logs the first one out. Items of
    # one group run one after another, in any free lane.
    serial_of: dict[int, str] = {}
    for i in todo:
        try:
            with open(suites.script_path(rec["items"][i]["script"]), encoding="utf-8") as fh:
                for raw in fh:
                    m = re.match(r"^#\s*Serial\s*:\s*(\S+)", raw.strip(), re.I)
                    if m:
                        serial_of[i] = m.group(1).lower()
                        break
                    if raw.strip() and not raw.startswith("#"):
                        break
        except OSError:
            pass
    busy_groups: set[str] = set()
    rec["parallel"] = lanes
    _save(rec)
    running: dict[int, tuple] = {}            # item index -> (Popen, out path, log handle)
    stop_rest = False

    def _merge(i: int, out: str) -> None:
        try:
            with open(out, encoding="utf-8") as fh:
                rec["items"][i].update(_json.load(fh))
        except (OSError, ValueError):
            pass

    while todo or running:
        aborted = rec["id"] in _abort or stop_rest
        while todo and len(running) < lanes and not aborted:
            i = next((x for x in todo if serial_of.get(x, "") not in busy_groups), None)
            if i is None:
                break                      # every waiting item's group is busy
            todo.remove(i)
            if serial_of.get(i):
                busy_groups.add(serial_of[i])
            out = os.path.join(lane_dir, f"item_{i}.json")
            log = open(os.path.join(lane_dir, f"item_{i}.log"), "w", encoding="utf-8")
            proc = subprocess.Popen([sys.executable, "-m", "execution.plan_worker", rec["id"], str(i), out],
                                    cwd=base, stdout=log, stderr=subprocess.STDOUT)
            rec["items"][i].update(status="running", started_at=_now(), lane_pid=proc.pid)
            running[i] = (proc, out, log)
        if aborted:
            for i in todo:
                rec["items"][i].update(status="not_run", reason=(
                    "plan stopped by user" if rec["id"] in _abort
                    else "an earlier test case failed (stop on first failure)"))
            todo = []
        for i, (proc, out, log) in list(running.items()):
            if proc.poll() is None:
                _merge(i, out)                 # attempt / run_id while it runs
                if rec["id"] in _abort and rec["id"] in _abort_now:
                    proc.terminate()
                continue
            log.close()
            _merge(i, out)
            it = rec["items"][i]
            if it.get("status") in ("running", "pending"):
                it.update(status="not_run", finished_at=_now(),
                          reason=("stopped by user" if rec["id"] in _abort
                                  else f"worker exited with code {proc.returncode} — see {os.path.basename(out)[:-5]}.log"))
            it.pop("lane_pid", None)
            busy_groups.discard(serial_of.get(i, ""))
            del running[i]
            if it.get("status") == "failed" and ex.get("stop_on_first_failure"):
                stop_rest = True
        _save(rec)
        time.sleep(1.5)


def _run(rec: dict) -> None:
    from fastapi import HTTPException

    from api.routes import tests as T
    from core import suites

    ex = rec.get("execution") or {}
    rec["status"] = "running"
    if not rec.get("started_at"):
        rec["started_at"] = _now()
    rec["pass_started_at"] = _now()          # this pass (first run or a re-run)
    _save(rec)
    logger.info("📋 Plan run %s — %s (%d test cases)", rec["id"], rec["plan_name"], len(rec["items"]))
    stop_rest = False
    lanes = max(1, min(int(ex.get("parallel") or 1), MAX_PARALLEL))
    if lanes > 1:
        _run_parallel(rec, ex, lanes)
    for item in rec["items"] if lanes == 1 else []:
        if item["status"] != "pending":
            continue
        if rec["id"] in _abort:
            item.update(status="not_run", reason="plan stopped by user")
            continue
        if stop_rest:
            item.update(status="not_run", reason="an earlier test case failed (stop on first failure)")
            continue
        _run_item(rec, item, ex)
        if item["status"] == "failed" and ex.get("stop_on_first_failure"):
            stop_rest = True
    t = rec["totals"]
    t["passed"] = sum(1 for i in rec["items"] if i["status"] == "passed")
    t["failed"] = sum(1 for i in rec["items"] if i["status"] == "failed")
    t["not_run"] = sum(1 for i in rec["items"] if i["status"] == "not_run" and not i.get("out_of_scope"))
    t["out_of_scope"] = sum(1 for i in rec["items"] if i.get("out_of_scope"))
    # Known issues (data/known_issues.json): a failure that matches an open
    # entry is reported as known, so the report shows what is NEW.
    try:
        from core import known_issues
        t["known"] = known_issues.annotate(rec["items"])
        t["new_failures"] = t["failed"] - t["known"]
    except Exception:  # noqa: BLE001
        logger.exception("known-issues annotation failed")
    if not (t["passed"] or t["failed"] or t["not_run"]):
        rec["reason"] = "No test case in this plan has steps tagged for this run type."
    rec["status"] = ("stopped" if rec["id"] in _abort
                     else "not_run" if not (t["passed"] or t["failed"] or t["not_run"])
                     else "failed" if (t["failed"] or t["not_run"]) else "passed")
    rec["finished_at"] = _now()
    try:
        # Execution time = sum of the passes (first run + re-runs), not the
        # wall-clock gap between them.
        a = datetime.fromisoformat(rec.get("pass_started_at") or rec["started_at"])
        b = datetime.fromisoformat(rec["finished_at"])
        this_pass = int((b - a).total_seconds())
        rec["duration_s"] = (int(rec.get("duration_s") or 0) if rec.get("rerun_history") else 0) + this_pass
    except ValueError:
        pass
    _save(rec)
    logger.info("📋 Plan run %s finished: %s (%s)", rec["id"], rec["status"], t)
