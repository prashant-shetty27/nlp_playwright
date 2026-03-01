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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.settings import FLOWS_DIR

router = APIRouter(prefix="/projects", tags=["projects"])

os.makedirs(FLOWS_DIR, exist_ok=True)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _flow_path(name: str) -> str:
    """Resolve a flow file path; rejects path-traversal attempts."""
    safe = os.path.basename(name)
    if not safe.endswith(".flow"):
        safe += ".flow"
    return os.path.join(FLOWS_DIR, safe)


def _read_steps(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [l.rstrip("\n") for l in f if l.strip() and not l.strip().startswith("#")]


# ── Request models ─────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    steps: list[str] = []


class ProjectUpdate(BaseModel):
    steps: list[str]


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("")
def list_projects():
    """List all .flow files in the flows/ directory."""
    files = [f[:-5] for f in os.listdir(FLOWS_DIR) if f.endswith(".flow")]
    return {"projects": sorted(files)}


@router.post("", status_code=201)
def create_project(body: ProjectCreate):
    """Create a new .flow project file."""
    path = _flow_path(body.name)
    if os.path.exists(path):
        raise HTTPException(status_code=409, detail=f"Project '{body.name}' already exists.")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(body.steps) + "\n")
    return {"message": f"Project '{body.name}' created.", "path": path}


@router.get("/{name}")
def get_project(name: str):
    """Return the steps of a .flow project."""
    path = _flow_path(name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found.")
    return {"name": name, "steps": _read_steps(path)}


@router.put("/{name}")
def update_project(name: str, body: ProjectUpdate):
    """Overwrite all steps of a .flow project."""
    path = _flow_path(name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found.")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(body.steps) + "\n")
    return {"message": f"Project '{name}' updated.", "steps": body.steps}


@router.delete("/{name}", status_code=200)
def delete_project(name: str):
    """Delete a .flow project file."""
    path = _flow_path(name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found.")
    os.remove(path)
    return {"message": f"Project '{name}' deleted."}
