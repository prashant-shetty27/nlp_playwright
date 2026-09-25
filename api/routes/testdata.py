"""
api/routes/testdata.py — the values tests use, kept out of the tests.

GET    /testdata                    — every declared value (secrets masked/withheld)
GET    /testdata/suggest            — values matching a partial, for the step editor
PUT    /testdata                    — declare or update one value
DELETE /testdata/{name}             — remove one

The store is execution/test_data.py, which writes data/common/variables.json —
the same file tools/flow_lint.py already reads for its global section, so a value
declared here immediately stops being reported as an undefined variable.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from execution import test_data

router = APIRouter(prefix="/testdata", tags=["testdata"])


class ValueBody(BaseModel):
    name: str
    value: str = ""
    #: "global" — every platform and environment. "environment" — this one only.
    scope: str = "global"
    environment: str = ""
    force: bool = False
    #: True when changing the value of a name the caller knows exists: the name
    #: is then not reported as a clash, only a value some OTHER name already has.
    updating: bool = False


@router.get("")
def list_values(environment: str = Query("")):
    """
    Everything stored. `environment="*"` spans every environment.

    `values` stays the name-keyed resolved view every other caller uses — what
    a run would actually see. `rows` is the flat one, where the same name in
    two environments is two entries rather than one overwriting the other.
    """
    resolved_for = "" if environment == "*" else environment
    return {"environments": test_data.environments(),
            "environment": environment,
            "values": test_data.get_all(resolved_for),
            "rows": test_data.rows(environment)}


@router.get("/suggest")
def suggest_values(partial: str = Query(""), environment: str = Query(""),
                   limit: int = Query(12, ge=1, le=50)):
    """
    Values matching `partial`, by name OR by the value itself.

    Matching the value is what lets an author type "93" and pick the test mobile
    without remembering its name. Only the masked form comes back.
    """
    return {"suggestions": test_data.suggestions(partial, environment, limit)}


@router.put("", status_code=200)
def put_value(body: ValueBody):
    if body.scope not in ("global", "environment"):
        raise HTTPException(status_code=422, detail="scope must be 'global' or 'environment'.")
    from locators.validation import as_dicts, check_variable

    conflicts = check_variable(body.name, environment=body.environment,
                               value=body.value, updating=body.updating)
    blocking = [c for c in conflicts if c.severity == "blocking"]
    if blocking and not body.force:
        raise HTTPException(
            status_code=409,
            detail={"message": blocking[0].message, "why": blocking[0].why,
                    "conflicts": as_dicts(conflicts),
                    "hint": "Resend with force=true to replace it deliberately."})
    try:
        return test_data.set_value(body.name, body.value,
                                   scope=body.scope, environment=body.environment)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


class DataRename(BaseModel):
    new_name: str
    scope: str = "global"
    environment: str = ""
    apply: bool = False


@router.post("/{name}/rename")
def rename_data_value(name: str, body: DataRename):
    """Rename a value and every ${reference} and Params header naming it."""
    from execution.refactor import RenameError, rename_variable

    try:
        return rename_variable(name, body.new_name, scope=body.scope,
                               environment=body.environment, apply=body.apply)
    except RenameError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.delete("/{name}", status_code=200)
def delete_value(name: str, scope: str = Query("global"), environment: str = Query("")):
    if not test_data.delete_value(name, scope=scope, environment=environment):
        raise HTTPException(status_code=404, detail=f"No value named {name!r}.")
    return {"deleted": name, "scope": scope}
