"""
api/routes/issues.py — failures from a run → Jira tickets (see core/jira_issues.py).

GET    /issues/draft?run_id=…|plan_run=…   proposed issues (+ duplicates, story, login)
GET    /issues/login                       this tester's Jira connection (never the token)
PUT    /issues/login                       save / check their Personal Access Token
DELETE /issues/login                       forget it
PUT    /issues/defaults                    remember common inputs (owners, priority)
GET    /issues/assignees?project=&q=       people who can own a ticket
POST   /issues/raise                       create tickets (tester's own token)
POST   /issues/prefill                     Jira Create-form links (no token needed)
POST   /issues/csv                         bulk-upload CSV
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from api.auth import acting_user
from core import jira_issues as ji

router = APIRouter(prefix="/issues", tags=["issues"])


def _err(e: Exception):
    raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/draft")
def get_draft(run_id: str = Query(""), plan_run: str = Query(""),
              user: str = Depends(acting_user)):
    if not run_id and not plan_run:
        raise HTTPException(status_code=422, detail="Give run_id or plan_run.")
    try:
        d = ji.draft(run_id=run_id, plan_run=plan_run)
    except ji.IssueError as e:
        _err(e)
    d["duplicates"], d["story_info"], d["jira_error"] = {}, None, ""
    if d["story"]:
        try:
            d["story_info"] = ji.story_info(d["story"])
            d["duplicates"] = ji.duplicates(d["story"], d["issues"])
        except Exception as e:  # noqa: BLE001 — drafting still works without Jira
            d["jira_error"] = f"Could not read {d['story']} from Jira: {e}"
    d["login"] = ji.login_status(user)
    return d


class LoginBody(BaseModel):
    email: str = ""
    token: str


@router.get("/login")
def get_login(user: str = Depends(acting_user)):
    return ji.login_status(user)


@router.put("/login")
def put_login(body: LoginBody, user: str = Depends(acting_user)):
    try:
        return ji.save_login(user, body.email, body.token)
    except ji.IssueError as e:
        _err(e)


@router.delete("/login")
def delete_login(user: str = Depends(acting_user)):
    ji.forget_login(user)
    return {"connected": False}


@router.put("/defaults")
def put_defaults(body: dict, user: str = Depends(acting_user)):
    ji.save_defaults(user, body)
    return {"ok": True}


@router.get("/assignees")
def get_assignees(project: str = Query(...), q: str = Query("")):
    try:
        return {"users": ji.assignable(project, q)}
    except ji.IssueError as e:
        _err(e)


class IssuesBody(BaseModel):
    story: str = ""
    issues: list[dict]


@router.post("/raise")
def post_raise(body: IssuesBody, user: str = Depends(acting_user)):
    try:
        return {"results": ji.raise_issues(user, body.issues, body.story.strip().upper())}
    except ji.IssueError as e:
        _err(e)


@router.post("/prefill")
def post_prefill(body: IssuesBody):
    try:
        info = ji.story_info(body.story.strip().upper())
    except Exception as e:  # noqa: BLE001
        _err(e)
    return {"links": {i.get("id", ""): ji.prefill_url(i, info["project"]["id"], info["key"])
                      for i in body.issues}}


@router.post("/csv")
def post_csv(body: IssuesBody):
    story = body.story.strip().upper()
    project = story.split("-")[0] if "-" in story else ""
    data = ji.csv_bytes(body.issues, project, story)
    return Response(content=data, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="issues_{story or "run"}.csv"'})
