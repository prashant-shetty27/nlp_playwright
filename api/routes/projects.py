"""
api/routes/projects.py
Manage .flow test projects stored in the flows/ directory.

GET  /projects                      — list all flow files
POST /projects                      — create a new flow file
GET  /projects/{name}               — read steps of a flow
PUT  /projects/{name}               — overwrite all steps
DELETE /projects/{name}             — delete a flow file
"""
import os
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from config.settings import FLOWS_DIR
from api.auth import acting_user, require
from core import audit

router = APIRouter(prefix="/projects", tags=["projects"])

os.makedirs(FLOWS_DIR, exist_ok=True)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _flow_path(name: str) -> str:
    """
    Resolve a flow file path.

    basename() alone blocked traversal but still accepted names that produce
    junk on disk: "" wrote a hidden ".flow", ".." wrote "...flow", and a name
    with spaces produced a file that is awkward to pass on a command line. A
    flow name is an identifier, so it is held to one.
    """
    safe = os.path.basename((name or "").strip())
    if safe.endswith(".flow"):
        safe = safe[:-len(".flow")]
    safe = re.sub(r"\s+", "_", safe)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "", safe).strip(".-")
    if not safe or not any(ch.isalnum() for ch in safe):
        raise ValueError(
            f"{name!r} is not a usable flow name. Use letters, digits, "
            f"underscores or hyphens."
        )
    return os.path.join(FLOWS_DIR, safe + ".flow")


def _flow_path_or_422(name: str) -> str:
    """_flow_path, with an unusable name reported as a 422 rather than a 500."""
    try:
        return _flow_path(name)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


#: A step switched off in the editor. It stays in the file as a comment — the
#: runner and the linter skip comments — so turning it back on is not retyping.
DISABLED_PREFIX = "# OFF: "

#: A one-line explanation of what the steps BELOW it are collectively doing.
#: "Purpose: sign in and reach the dashboard" over four steps saves reading the
#: four. It is a comment, so the runner and the linter ignore it entirely — this
#: is annotation, not structure, and deliberately not a step group: nothing is
#: reusable, nothing is called by name, and deleting the band leaves the steps.
PURPOSE_PREFIX = "# --- Purpose: "


def _read_steps(path: str) -> list[str]:
    out: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith((DISABLED_PREFIX, PURPOSE_PREFIX)):
                out.append(stripped)          # kept and marked, never executed
            elif not stripped.startswith("#"):
                out.append(line)
    return out


_PARAMS_LINE = re.compile(r"^#\s*Params\s*:", re.I)
_PLATFORM_LINE = re.compile(r"^#\s*Platform\s*:", re.I)


def _read_header(path: str) -> list[str]:
    """
    The leading comment block, up to the first statement.

    The editor reads a flow as bare statements (_read_steps drops comments) and
    saving rewrites the whole file. Without preserving this, editing one step of
    a generated flow silently destroyed its "# Source" and "# Map" lines — the
    only record of which testcase it came from and where its trace file is.
    """
    out: list[str] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.rstrip("\n")
                if not line.strip():
                    continue
                if not line.strip().startswith("#"):
                    break
                # A purpose band or a disabled step is CONTENT that happens to
                # be written as a comment. Absorbing one into the header made it
                # be written twice — once as header, once as a step.
                if line.strip().startswith((DISABLED_PREFIX, PURPOSE_PREFIX)):
                    break
                out.append(line)
    except OSError:
        return []
    return out


def _compose(steps: list[str], header: list[str] | None = None,
             platform: str = "") -> str:
    """
    Render a flow file: preserved header, a truthful "# Params" line, then steps.

    The Params line is DERIVED from the steps on every write, never carried over.
    A flow that gains a ${variable} gains the declaration; one that loses its
    last variable loses the line, so the header cannot drift into a claim the
    body does not support. tools/flow_lint.py reads this line, so a flow saved
    from the editor now passes the same gate a generated flow does.
    """
    from ai_flow_builder.emitter import run_parameters

    # Neither a disabled step nor a purpose band contributes a run parameter.
    params = run_parameters([st for st in steps
                             if not st.strip().startswith((DISABLED_PREFIX,
                                                           PURPOSE_PREFIX))])
    kept = [l for l in (header or []) if not _PARAMS_LINE.match(l.strip())]
    if platform:
        kept = [l for l in kept if not _PLATFORM_LINE.match(l.strip())]
        kept.insert(0, f"# Platform: {platform}")
    if params:
        line = f"# Params : {' '.join(params)}"
        # Put it back where it was if the file already declared one, so a
        # generated flow keeps its header order (Source, Verifies, Params, Map).
        at = next((i for i, l in enumerate(header or [])
                   if _PARAMS_LINE.match(l.strip())), None)
        kept.insert(at if at is not None else len(kept), line)
    body = "\n".join(steps)
    return ("\n".join(kept) + "\n\n" + body + "\n") if kept else (body + "\n")


# ── Request models ─────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    steps: list[str] = []
    #: Recorded in the file as "# Platform:". Guessing it from the filename works
    #: only for flows named android_*/ios_*; one authored for the mobile site and
    #: called ui_driven_ask_more_photos was filed under Website.
    platform: str = ""


class ProjectUpdate(BaseModel):
    steps: list[str]
    platform: str = ""
    #: The file's modified-time when the editor opened it. When sent and the
    #: file has changed since (someone else saved), the save is refused with
    #: 409 instead of silently overwriting their work.
    expected_mtime: float | None = None


# ── Routes ─────────────────────────────────────────────────────────────────────

def _platform_of(name: str) -> str:
    """
    Which platform a flow belongs to.

    A declared `# Platform:` header wins. Otherwise it is inferred from the name
    the same way tools/flow_lint.py infers it, so the list, the linter and the
    runner agree about what an android_ flow is.
    """
    path = os.path.join(FLOWS_DIR, name + ".flow")
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                if not line.startswith("#"):
                    break
                low = line.lower()
                if "platform" in low and ":" in low:
                    declared = low.split(":", 1)[1].strip()
                    try:
                        from nlp.platforms import normalise
                        return normalise(declared)
                    except Exception:  # noqa: BLE001
                        pass
    except OSError:
        pass

    low = name.lower()
    for prefix, platform in (("ios", "ios"), ("android", "android"),
                             ("hybrid", "hybrid"), ("mobile", "mobilesite"),
                             ("wap", "mobilesite"), ("web", "website")):
        if low.startswith(prefix + "_") or f"_{prefix}_" in low:
            return platform
    return "website"


@router.get("")
def list_projects(platform: str = ""):
    """
    List .flow projects, optionally only those for one platform.

    Unfiltered, the Website list showed android_demo and ios_e2e alongside web
    flows — every platform's tests in one list, none of them runnable from where
    they were shown.
    """
    files = [f[:-5] for f in os.listdir(FLOWS_DIR) if f.endswith(".flow")]
    if platform:
        try:
            from nlp.platforms import normalise
            want = normalise(platform)
        except Exception:  # noqa: BLE001
            want = platform
        files = [f for f in files if _platform_of(f) == want]
    return {"projects": sorted(files),
            "platforms": {f: _platform_of(f) for f in sorted(files)}}


@router.post("", status_code=201)
def create_project(body: ProjectCreate, user: str = Depends(acting_user)):
    """Create a new .flow project file."""
    require(user, "write")
    path = _flow_path_or_422(body.name)
    if os.path.exists(path):
        raise HTTPException(status_code=409, detail=f"Project '{body.name}' already exists.")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_compose(body.steps, platform=body.platform))
    audit.touch(os.path.basename(path)[:-5], user, created=True)
    return {"message": f"Project '{body.name}' created.", "path": path}


@router.get("/{name}")
def get_project(name: str):
    """Return the steps of a .flow project."""
    path = _flow_path_or_422(name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found.")
    return {"name": name, "steps": _read_steps(path), "meta": audit.get(name),
            "mtime": os.path.getmtime(path)}


@router.put("/{name}")
def update_project(name: str, body: ProjectUpdate, user: str = Depends(acting_user)):
    """Overwrite all steps of a .flow project."""
    require(user, "write")
    path = _flow_path_or_422(name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found.")
    if body.expected_mtime is not None and abs(os.path.getmtime(path) - body.expected_mtime) > 0.001:
        who = (audit.get(name) or {}).get("updated_by") or "someone"
        raise HTTPException(status_code=409, detail=(
            f"'{name}' was changed by {who} after you opened it. Reload to see their version, "
            f"or overwrite it with yours."))
    header = _read_header(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(_compose(body.steps, header, platform=body.platform))
    audit.touch(name, user)
    return {"message": f"Project '{name}' updated.", "steps": body.steps,
            "mtime": os.path.getmtime(path)}


class RenameBody(BaseModel):
    new_name: str
    #: False returns what WOULD change and touches nothing. The UI shows that
    #: list first — a rename can rewrite suites and plans, and that should never
    #: be a surprise.
    apply: bool = False


@router.get("/check-name/{name}")
def check_name(name: str):
    """
    What a typed name will actually be saved as.

    Names are normalised, not rejected — "JD Search 01" becomes
    "JD_Search_01" — but that happened silently, so the file ended up with a
    name the author never saw. The editor asks this first and shows the result,
    so the convention is explained rather than imposed.
    """
    import os as _os

    try:
        resolved = _os.path.basename(_flow_path(name))[:-len(".flow")]
    except ValueError as e:
        return {"ok": False, "saved_as": "", "reason": str(e),
                "hint": "Letters, digits, underscores or hyphens."}
    return {"ok": True, "saved_as": resolved,
            "changed": resolved != (name or "").strip(),
            "exists": _os.path.exists(_flow_path(name)),
            "hint": ("Spaces become underscores and punctuation is dropped, so "
                     "the name works on a command line and in a suite file.")}


@router.post("/{name}/rename")
def rename_project(name: str, body: RenameBody, user: str = Depends(acting_user)):
    """Rename a test case, its sidecar map, and every suite/plan naming it."""
    from execution.refactor import RenameError, rename_flow

    if body.apply:
        require(user, "write")
    try:
        res = rename_flow(name, body.new_name, apply=body.apply)
        if body.apply:
            audit.rename(name, body.new_name)
            audit.touch(body.new_name, user)
        return res
    except RenameError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.delete("/{name}", status_code=200)
def delete_project(name: str, user: str = Depends(acting_user)):
    """Delete a .flow project file."""
    require(user, "write")
    path = _flow_path_or_422(name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found.")
    os.remove(path)
    return {"message": f"Project '{name}' deleted."}
