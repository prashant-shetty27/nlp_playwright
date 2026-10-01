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
    #: Modules this value belongs to; a new value defaults to the module it is created in.
    modules: list[str] = []
    #: True when `modules` is a deliberate re-tag of an existing value.
    retag: bool = False
    force: bool = False
    #: True when changing the value of a name the caller knows exists: the name
    #: is then not reported as a clash, only a value some OTHER name already has.
    updating: bool = False


@router.get("")
def list_values(environment: str = Query(""), module: str = Query("")):
    """
    Everything stored. `environment="*"` spans every environment.

    `values` stays the name-keyed resolved view every other caller uses — what
    a run would actually see. `rows` is the flat one, where the same name in
    two environments is two entries rather than one overwriting the other.
    """
    resolved_for = "" if environment == "*" else environment
    return {"environments": test_data.environments(),
            "environment": environment,
            "values": test_data.get_all(resolved_for, module or None),
            "rows": test_data.rows(environment, module or None),
            "modules": list(test_data.MODULES)}


@router.get("/suggest")
def suggest_values(partial: str = Query(""), environment: str = Query(""),
                   limit: int = Query(12, ge=1, le=50), module: str = Query("")):
    """
    Values matching `partial`, by name OR by the value itself.

    Matching the value is what lets an author type "93" and pick the test mobile
    without remembering its name. Only the masked form comes back.
    """
    return {"suggestions": test_data.suggestions(partial, environment, limit, module or None)}


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
                                   scope=body.scope, environment=body.environment,
                                   modules=body.modules, retag=body.retag)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


class ModulesBody(BaseModel):
    modules: list[str]


@router.put("/{name}/modules")
def put_modules(name: str, body: ModulesBody):
    """Which modules a value belongs to (shown, suggested and supplied only there)."""
    try:
        return {"name": name, "modules": test_data.set_modules(name, body.modules)}
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


# ── Data sets: uploaded tables that `for each row in <name>` loops over ───────
from fastapi import Depends, File, UploadFile  # noqa: E402

from api.auth import acting_user  # noqa: E402
from core import datasets  # noqa: E402


@router.get("/datasets")
def list_datasets(module: str = Query("")):
    """Uploaded tables (only this module's when given): name, columns, rows, creator / editor."""
    return {"datasets": datasets.all_info(module)}


@router.put("/datasets/{name}/modules")
def put_dataset_modules(name: str, body: ModulesBody):
    try:
        return {"name": name, "modules": datasets.set_modules(name, body.modules)}
    except datasets.DatasetError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/datasets/{name}")
def get_dataset(name: str, limit: int = Query(200, ge=1, le=datasets.MAX_ROWS)):
    try:
        headings, body = datasets.table(name)
    except datasets.DatasetError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {**datasets.info(name), "rows": body[:limit]}


@router.post("/datasets/upload")
async def upload_dataset(file: UploadFile = File(...), replace: bool = Query(False),
                         module: str = Query(""), user: str = Depends(acting_user)):
    """
    A .xlsx / .csv file → one data set per sheet. An existing name is only
    overwritten with replace=true, so two people cannot silently replace each
    other's table.
    """
    content = await file.read()
    try:
        tables = datasets.parse_upload(file.filename or "", content)
    except datasets.DatasetError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 — a corrupt workbook
        raise HTTPException(status_code=422, detail=f"Could not read the file: {e}") from e
    clashes = [n for n in tables if datasets.exists(n)]
    if clashes and not replace:
        raise HTTPException(status_code=409, detail={
            "message": f"A data set called {', '.join(clashes)} already exists.",
            "why": "Uploading again replaces its rows for every test that uses it.",
            "names": clashes})
    saved = [datasets.save(n, h, r, user=user, module=module) for n, (h, r) in tables.items()]
    return {"saved": saved}


@router.delete("/datasets/{name}")
def delete_dataset(name: str):
    if not datasets.exists(name):
        raise HTTPException(status_code=404, detail=f"No data set called '{name}'.")
    datasets.delete(name)
    return {"deleted": name}
