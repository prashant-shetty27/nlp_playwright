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


def _same_steps_as(steps: list[str], except_name: str = "") -> str:
    """Name of an existing group with exactly these steps (ignoring case/space), else ''."""
    norm = [" ".join((st or "").split()).lower() for st in steps if (st or "").strip()]
    for g in rs.describe():
        if g["name"] == except_name:
            continue
        theirs = [" ".join(st.split()).lower() for st in g.get("steps", []) if st.strip()]
        if theirs == norm:
            return g["name"]
    return ""


@router.post("", status_code=201)
def save_group(body: GroupBody):
    """
    Save steps as a named group.

    A duplicate name is reported as 409 rather than silently overwriting — the
    steps in an existing group may be used by flows the author cannot see from
    here. Repeat with overwrite=true to replace it deliberately.
    """
    twin = _same_steps_as(body.steps, except_name=body.name)
    if twin:
        raise HTTPException(
            status_code=409,
            detail=(f"These exact steps are already saved as the step group '{twin}'. "
                    f"Use `call {twin}` instead of saving a second copy."))
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
    twin = _same_steps_as(unique, except_name=name)
    if twin:
        raise HTTPException(
            status_code=409,
            detail=(f"After this edit '{name}' would have exactly the same steps as "
                    f"'{twin}'. Keep one of them, or make them differ."))
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
    try:
        rs.get(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    callers = _callers_of(name)
    if callers:
        where = ", ".join(callers[:8]) + (" …" if len(callers) > 8 else "")
        raise HTTPException(
            status_code=409,
            detail=f"'{name}' is still called by: {where}. Replace those "
                   f"'call {name}' steps first, then delete it.")
    rs.delete(name)
    try:
        from tools.testsigma_pull import forget
        forget("groups", name)
    except Exception:  # noqa: BLE001
        pass
    return {"deleted": name}


def _loose(n: str) -> str:
    import re
    n = re.sub(r"[\s\-\u2013\u2014_]+", "_", (n or "").strip().lower()).strip("_")
    return re.sub(r"^sg_", "", n)


def _callers_of(name: str) -> list[str]:
    """Test cases and step groups with a live `call <name>` step."""
    import os
    import re

    from config import settings

    want = _loose(name)
    pat = re.compile(r"^\s*call\s+(.+?)\s*$", re.I)
    out: list[str] = []
    for fn in sorted(os.listdir(settings.FLOWS_DIR)):
        if not fn.endswith(".flow"):
            continue
        try:
            with open(os.path.join(settings.FLOWS_DIR, fn), encoding="utf-8") as f:
                if any((m := pat.match(ln)) and _loose(m.group(1)) == want for ln in f):
                    out.append(f"test case {fn[:-5]}")
        except OSError:
            continue
    for g in rs.describe():
        if g["name"] == name:
            continue
        if any((m := pat.match(st)) and _loose(m.group(1)) == want for st in g.get("steps", [])):
            out.append(f"step group {g['name']}")
    return out


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
    # Every test case that calls the old name is rewritten, or the rename
    # would silently break them at run time ("Reusable steps … not found").
    updated = _rewrite_calls(name, body.new_name)
    # …and every OTHER group that calls it (groups can call groups).
    groups_updated = _rewrite_group_calls(name, body.new_name)
    try:
        from tools.testsigma_pull import forget
        forget("groups", name, body.new_name)
    except Exception:  # noqa: BLE001
        pass
    return {"renamed": name, "to": body.new_name, "flows_updated": updated,
            "groups_updated": groups_updated}


def _rewrite_group_calls(old: str, new: str) -> list[str]:
    import re
    want = _loose(old)
    pat = re.compile(r"^(\s*(?:#\s*OFF:\s*)?call\s+)(.+?)\s*$", re.I)
    touched: list[str] = []
    for g in rs.describe():
        steps = list(g.get("steps", []))
        changed = False
        for i, st in enumerate(steps):
            m = pat.match(st)
            if m and _loose(m.group(2)) == want:
                steps[i] = f"{m.group(1)}{new}"
                changed = True
        if changed:
            rs.save(g["name"], steps, overwrite=True, platform=g.get("platform", ""))
            touched.append(g["name"])
    return touched


def _rewrite_calls(old: str, new: str) -> list[str]:
    """Replace `call <old>` with `call <new>` in all flows; returns the flows touched."""
    import os
    import re

    from config import settings

    def loose(n: str) -> str:
        n = re.sub(r"[\s\-\u2013\u2014_]+", "_", (n or "").strip().lower()).strip("_")
        return re.sub(r"^sg_", "", n)

    touched: list[str] = []
    want = loose(old)
    pat = re.compile(r"^(\s*(?:#\s*OFF:\s*)?call\s+)(.+?)\s*$", re.I)
    for fn in sorted(os.listdir(settings.FLOWS_DIR)):
        if not fn.endswith(".flow"):
            continue
        path = os.path.join(settings.FLOWS_DIR, fn)
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines(keepends=True)
        changed = False
        for i, ln in enumerate(lines):
            m = pat.match(ln.rstrip("\n"))
            if m and loose(m.group(2)) == want:
                lines[i] = f"{m.group(1)}{new}\n"
                changed = True
        if changed:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            touched.append(fn[:-5])
    return touched
