"""
api/routes/suites_plans.py — Test Suites, Test Plans, plan runs, scheduler.

GET/POST        /suites                 list / create
GET/PUT/DELETE  /suites/{id}
GET/POST        /plans                  list / create
GET/PUT/DELETE  /plans/{id}
POST            /plans/{id}/run         run now
GET             /plans/runs             recent plan runs (?plan=)
GET             /plans/runs/{run_id}    one plan run, live while it runs
POST            /plans/runs/{run_id}/stop
POST            /plans/runs/{run_id}/notify   re-send the Slack summary
GET             /plans/scheduler        scheduler status
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import acting_user, require
from core import plans, suites

router = APIRouter(tags=["suites & plans"])


class SuiteBody(BaseModel):
    name: str
    platform: str
    test_cases: list[str]
    description: str = ""
    stop_on_failure: bool = False


class PlanBody(BaseModel):
    name: str
    suites: list[str]
    description: str = ""
    execution: dict = {}
    schedule: dict = {}
    notify: dict = {}


# ── suites ──────────────────────────────────────────────────────────────────
@router.get("/testsuites")
def list_suites(platform: str = ""):
    return {"suites": suites.list_suites(platform)}


@router.get("/testsuites/{suite_id}")
def get_suite(suite_id: str):
    try:
        s = suites.get(suite_id)
    except suites.SuiteError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    s["used_by"] = suites.plans_using(suite_id)
    return s


@router.post("/testsuites", status_code=201)
def create_suite(body: SuiteBody, user: str = Depends(acting_user)):
    require(user, "write")
    try:
        return suites.save(body.name, body.platform, body.test_cases, description=body.description,
                           user=user, stop_on_failure=body.stop_on_failure)
    except suites.SuiteError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.put("/testsuites/{suite_id}")
def update_suite(suite_id: str, body: SuiteBody, user: str = Depends(acting_user)):
    require(user, "write")
    try:
        return suites.save(body.name, body.platform, body.test_cases, description=body.description,
                           user=user, suite_id=suite_id, stop_on_failure=body.stop_on_failure)
    except suites.SuiteError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.delete("/testsuites/{suite_id}")
def delete_suite(suite_id: str, user: str = Depends(acting_user)):
    require(user, "write")
    try:
        suites.delete(suite_id)
    except suites.SuiteError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"deleted": suite_id}


# ── plan runs (declared before /plans/{id} so 'runs' is not taken as an id) ──
@router.get("/testplans/runs")
def plan_runs(plan: str = "", limit: int = 50):
    from execution import plan_engine
    return {"runs": plan_engine.list_runs(plan, limit)}


@router.get("/testplans/runs/{run_id}")
def plan_run(run_id: str):
    from execution import plan_engine
    try:
        rec = plan_engine.get_run(run_id)
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=404, detail=f"No plan run '{run_id}'.") from e
    rec["health"] = plan_engine.health(rec)
    return rec


@router.get("/testplans/health")
def plans_health():
    """The running plan (if any) and whether it looks stuck — for the top-bar alert."""
    from execution import plan_engine
    rid = plan_engine.active_run()
    if not rid:
        return {"active": ""}
    rec = plan_engine.get_run(rid)
    return {"active": rid, "plan_name": rec.get("plan_name"), "status": rec.get("status"),
            **plan_engine.health(rec)}


@router.post("/testplans/runs/{run_id}/stop")
def stop_plan_run(run_id: str, now: bool = False, user: str = Depends(acting_user)):
    require(user, "run")
    from execution import plan_engine
    plan_engine.request_stop(run_id, now=now)
    return {"stopping": run_id,
            "note": ("the current test case stops at its next step; the rest are not run" if now
                     else "the current test case finishes, the rest are not run")}


@router.post("/testplans/runs/{run_id}/close")
def close_plan_run(run_id: str, user: str = Depends(acting_user)):
    """Mark a run the server is no longer executing (orphaned) as interrupted."""
    require(user, "run")
    from execution import plan_engine
    try:
        rec = plan_engine.get_run(run_id)
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=404, detail=f"No plan run '{run_id}'.") from e
    if plan_engine.health(rec).get("kind") != "orphaned":
        raise HTTPException(status_code=409, detail="This run is still executing — use Stop instead.")
    plan_engine.recover_orphans()
    return {"closed": run_id}


@router.post("/testplans/runs/{run_id}/notify")
def renotify(run_id: str, user: str = Depends(acting_user)):
    require(user, "run")
    from execution import plan_engine
    from reporting.plan_slack import notify
    try:
        rec = plan_engine.get_run(run_id)
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=404, detail=f"No plan run '{run_id}'.") from e
    return notify(rec, force=True)


@router.get("/testplans/runs/{run_id}/report")
def plan_run_report(run_id: str, format: str = "html", regenerate: bool = False):
    """The professional report of a finished plan run — HTML (view) or PDF (download)."""
    from fastapi.responses import FileResponse
    from execution import plan_engine
    from reporting import plan_report
    try:
        rec = plan_engine.get_run(run_id)
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=404, detail=f"No plan run '{run_id}'.") from e
    if rec.get("status") in ("queued", "running"):
        raise HTTPException(status_code=409, detail="The run is still going — the report is made when it finishes.")
    files = plan_report.generate(rec) if regenerate else plan_report.ensure(rec)
    if format == "pdf":
        if not files.get("pdf"):
            raise HTTPException(status_code=500, detail=files.get("error") or "PDF could not be created.")
        return FileResponse(files["pdf"], media_type="application/pdf",
                            filename=os.path.basename(files["pdf"]))
    return FileResponse(files["html"], media_type="text/html")


class EmailBody(BaseModel):
    to: str = ""


@router.post("/testplans/runs/{run_id}/email")
def email_plan_run(run_id: str, body: EmailBody, user: str = Depends(acting_user)):
    require(user, "run")
    from execution import plan_engine
    from reporting.plan_email import recipients, send
    try:
        rec = plan_engine.get_run(run_id)
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=404, detail=f"No plan run '{run_id}'.") from e
    return send(rec, recipients(body.to) or None, force=True)


@router.get("/testplans/{plan_id}/preview")
def preview_plan(plan_id: str, run_type: str = ""):
    """How many steps each test case runs under each run type (static, no browser)."""
    from core import run_types, suites
    try:
        p = plans.get(plan_id)
    except plans.PlanError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    types = [run_types.normalise(run_type)] if run_types.normalise(run_type) else list(run_types.RUN_TYPES)
    out = {t: {"steps": 0, "test_cases": 0, "items": []} for t in types}
    for s in p["suites"]:
        try:
            suite = suites.get(s["id"])
        except suites.SuiteError:
            continue
        for tc in suite["test_cases"]:
            for t in types:
                try:
                    pv = run_types.preview(suites.script_path(tc["script"]), t)
                except OSError:
                    continue
                out[t]["steps"] += pv["steps"]
                out[t]["test_cases"] += 1 if pv["steps"] else 0
                out[t]["items"].append({"test_case": tc["name"], **pv})
    return out


@router.get("/testplans/scheduler")
def scheduler_status():
    from execution import scheduler
    return scheduler.status()


# ── plans ───────────────────────────────────────────────────────────────────
@router.get("/testplans")
def list_plans():
    return {"plans": plans.list_plans()}


@router.get("/testplans/{plan_id}")
def get_plan(plan_id: str):
    try:
        return plans.get(plan_id)
    except plans.PlanError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/testplans", status_code=201)
def create_plan(body: PlanBody, user: str = Depends(acting_user)):
    require(user, "write")
    try:
        return plans.save(body.name, body.suites, description=body.description, user=user,
                          execution=body.execution, schedule=body.schedule, notify=body.notify)
    except plans.PlanError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.put("/testplans/{plan_id}")
def update_plan(plan_id: str, body: PlanBody, user: str = Depends(acting_user)):
    require(user, "write")
    try:
        return plans.save(body.name, body.suites, description=body.description, user=user,
                          plan_id=plan_id, execution=body.execution, schedule=body.schedule,
                          notify=body.notify)
    except plans.PlanError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.delete("/testplans/{plan_id}")
def delete_plan(plan_id: str, user: str = Depends(acting_user)):
    require(user, "write")
    try:
        plans.delete(plan_id)
    except plans.PlanError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"deleted": plan_id}


@router.post("/testplans/{plan_id}/run", status_code=202)
def run_plan(plan_id: str, run_type: str = "", user: str = Depends(acting_user)):
    require(user, "run")
    from execution import plan_engine
    try:
        plans.get(plan_id)
    except plans.PlanError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    busy = plan_engine.active_run()
    rec = plan_engine.start(plan_id, trigger="manual", user=user, run_type=run_type)
    return {"run_id": rec["id"], "status": rec["status"],
            "queued_behind": busy or None}
