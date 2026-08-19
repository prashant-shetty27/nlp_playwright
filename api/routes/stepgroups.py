"""
api/routes/stepgroups.py — reusable groups of steps.

GET    /stepgroups            — groups available on a platform
POST   /stepgroups            — save a selection of steps as a named group
DELETE /stepgroups/{name}     — remove one
POST   /stepgroups/{name}/rename

The storage, the validation and the runtime expansion already existed in
core/reusable_steps.py — a group runs today via `call <name>`, with circular-call
protection. What was missing was any way to reach it: no endpoint, no screen, and
no suggestion, so the feature was unusable without hand-editing a JSON file.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core import reusable_steps as rs

router = APIRouter(prefix="/stepgroups", tags=["stepgroups"])


class GroupBody(BaseModel):
    name: str
    steps: list[str]
    platform: str = ""
    overwrite: bool = False


class GroupRename(BaseModel):
    new_name: str


@router.get("")
def list_groups(platform: str = Query("")):
    return {"groups": rs.describe(platform), "platform": platform}


@router.post("", status_code=201)
def save_group(body: GroupBody):
    """
    Save steps as a named group.

    A duplicate name is reported as 409 rather than silently overwriting — the
    steps in an existing group may be used by flows the author cannot see from
    here. Repeat with overwrite=true to replace it deliberately.
    """
    try:
        rs.save(body.name, body.steps, overwrite=body.overwrite,
                platform=body.platform)
    except ValueError as e:
        msg = str(e)
        if msg.startswith("DUPLICATE:"):
            raise HTTPException(
                status_code=409,
                detail=(f"A step group named '{msg.split(':', 1)[1]}' already exists. "
                        f"Save again with overwrite to replace it."))
        raise HTTPException(status_code=422, detail=msg) from e
    return {"name": body.name, "steps": body.steps, "platform": body.platform,
            "call": f"call {body.name}"}


class CloneBody(BaseModel):
    new_name: str = ""


@router.post("/{name}/clone", status_code=201)
def clone_group(name: str, body: CloneBody):
    """
    Copy a group under a new name.

    Two duplicate rules, both asked for and both enforced here rather than left
    to the caller:

      * the NAME must be new — cloning onto an existing group would silently
        replace steps other test cases depend on;
      * the STEPS are de-duplicated, keeping first occurrence order. A clone is
        usually the start of an edit, and carrying a repeated line into it just
        propagates a mistake.
    """
    try:
        steps = rs.get(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    existing = {g["name"]: g for g in rs.describe()}
    target = (body.new_name or "").strip() or _next_free_name(name, existing)
    if target in existing:
        raise HTTPException(
            status_code=409,
            detail=f"A step group named '{target}' already exists. Choose another name.")

    seen, unique = set(), []
    for st in steps:
        k = st.strip().lower()
        if k and k not in seen:
            seen.add(k)
            unique.append(st)

    try:
        rs.save(target, unique, overwrite=False,
                platform=existing.get(name, {}).get("platform", ""))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"cloned": name, "to": target, "steps": unique,
            "removed_duplicates": len(steps) - len(unique)}


def _next_free_name(base: str, existing: dict) -> str:
    """base_copy, then base_copy_2 … so a clone never needs a name to be typed."""
    candidate = f"{base}_copy"
    n = 2
    while candidate in existing:
        candidate = f"{base}_copy_{n}"
        n += 1
    return candidate


class EditBody(BaseModel):
    steps: list[str]


@router.put("/{name}")
def edit_group(name: str, body: EditBody):
    """
    Replace the steps of an existing group.

    Duplicated lines are dropped here too, for the same reason: a group is
    expanded inline wherever it is called, so a repeated step repeats in every
    test that uses it.
    """
    try:
        rs.get(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    seen, unique = set(), []
    for st in body.steps:
        k = (st or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            unique.append(st.strip())
    if not unique:
        raise HTTPException(status_code=422, detail="A group needs at least one step.")
    existing = {g["name"]: g for g in rs.describe()}
    try:
        rs.save(name, unique, overwrite=True,
                platform=existing.get(name, {}).get("platform", ""))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"name": name, "steps": unique,
            "removed_duplicates": len(body.steps) - len(unique)}


@router.delete("/{name}")
def delete_group(name: str):
    rs.delete(name)
    return {"deleted": name}


@router.post("/{name}/rename")
def rename_group(name: str, body: GroupRename):
    """Rename a group, carrying its steps and platform across."""
    try:
        steps = rs.get(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    existing = {g["name"]: g for g in rs.describe()}
    try:
        rs.save(body.new_name, steps, overwrite=False,
                platform=existing.get(name, {}).get("platform", ""))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    rs.delete(name)
    return {"renamed": name, "to": body.new_name}
