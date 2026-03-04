"""
api/routes/locators.py
GET    /locators               — all locators (both DBs merged)
GET    /locators/dropdown/names — flat name map for dropdowns
GET    /locators/{page}        — all locators for one page
POST   /locators               — add / overwrite a locator
DELETE /locators/{page}/{name} — remove a locator
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from locators.manager import get_all_locators, load_locators, save_locators

router = APIRouter(prefix="/locators", tags=["locators"])


# ── Request model ──────────────────────────────────────────────────────────────

class LocatorBody(BaseModel):
    page: str
    name: str
    xpath: str
    dna: dict = {}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _write_manual(data: dict) -> None:
    save_locators(data)


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("")
def list_all_locators():
    """Return every locator from both manual and recorded DBs, keyed by page."""
    return load_locators()


@router.get("/dropdown/names")
def list_locator_names():
    """Return flat element-name → display-string map for UI dropdowns."""
    return get_all_locators()


@router.get("/{page}")
def list_page_locators(page: str):
    """Return all locators for a specific page."""
    data = load_locators()
    if page not in data:
        raise HTTPException(status_code=404, detail=f"Page '{page}' not found.")
    return data[page]


@router.post("", status_code=201)
def add_locator(body: LocatorBody):
    """
    Add or overwrite a manual locator.
    Writes to data/locators_manual.json.
    """
    data = load_locators()
    data.setdefault(body.page, {})[body.name] = {
        "xpath": body.xpath,
        "dna": body.dna,
    }
    _write_manual(data)
    return {"message": f"Locator '{body.page}/{body.name}' saved.", "xpath": body.xpath}


@router.delete("/{page}/{name}", status_code=200)
def delete_locator(page: str, name: str):
    """Remove a manual locator entry."""
    data = load_locators()
    if page not in data or name not in data[page]:
        raise HTTPException(status_code=404, detail=f"Locator '{page}/{name}' not found.")
    del data[page][name]
    if not data[page]:          # prune empty page bucket
        del data[page]
    _write_manual(data)
    return {"message": f"Locator '{page}/{name}' deleted."}
