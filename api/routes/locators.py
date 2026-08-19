"""
api/routes/locators.py
GET    /locators                — every locator, from every registered database
GET    /locators/sources        — which databases exist, and their health
GET    /locators/conflicts      — names defined more than once, and which one wins
GET    /locators/dropdown/names — flat name map for dropdowns (?platform= to scope)
GET    /locators/{page}         — all locators for one page/group
POST   /locators                — add / overwrite a locator
DELETE /locators/{page}/{name}  — remove a locator

Every read goes through locators/sources.py, the single declaration of which
databases exist and in what order the runners consult them. This route used to
call load_locators(), which reads ONLY locators_manual.json — so the Elements
screen showed 32 of 142 locators, GET /{page} returned 404 for any recorded
page, and DELETE reported "not found" for locators that resolve perfectly well
at run time. The registry means a database added later shows up here with no
change to this file.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from locators.manager import get_all_locators, load_locators, save_locators
from locators.sources import (conflicts, describe, grouped, owner, problems,
                              sources, writable_source)

router = APIRouter(prefix="/locators", tags=["locators"])


# ── Request model ──────────────────────────────────────────────────────────────

class LocatorBody(BaseModel):
    page: str
    name: str
    xpath: str
    dna: dict = {}
    #: Save despite a blocking conflict. Deliberate, and never the default —
    #: the point of the check is that duplicates are cheap to prevent and
    #: expensive to remove.
    force: bool = False


# ── Helpers ────────────────────────────────────────────────────────────────────

def _write_manual(data: dict) -> None:
    save_locators(data)


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("")
def list_all_locators(platform: str = Query("website")):
    """
    Every locator resolvable on `platform`, keyed by page/group.

    Each record carries "_source" naming the database it came from, so the
    screen can show which entries are editable — a recorded locator belongs to
    the spy and is not hand-editable, and saying so is better than offering an
    edit that fails.
    """
    return grouped(platform)


@router.get("/sources")
def list_sources():
    """
    The registered locator databases, in resolution order, with their health.

    A database that is missing or corrupt reports itself here instead of just
    contributing nothing, which is what made the original under-reporting so
    hard to notice.
    """
    return {"sources": describe(), "problems": problems()}


@router.get("/conflicts")
def list_conflicts(platform: str = Query("website")):
    """
    Names defined in more than one place, and which definition actually wins.

    Not an error: the runner takes the first match. But reusing such a name
    binds to whichever definition wins, which may not be the one intended, so
    it is worth being able to see them.
    """
    return {
        "platform": platform,
        "conflicts": {
            name: {
                "resolves_to": {"source": es[0].source_id, "group": es[0].group},
                "also_defined_in": [
                    {"source": e.source_id, "group": e.group} for e in es[1:]
                ],
            }
            for name, es in conflicts(platform).items()
        },
    }


@router.get("/dropdown/names")
def list_locator_names(platform: str = Query("")):
    """
    Flat element-name → display-string map for UI dropdowns.

    `?platform=` narrows it to the names that platform's runner resolves. The
    default stays every platform so existing callers are unaffected, but the
    step editor passes one: an element it cannot resolve must not be drawn as
    though the step will run.
    """
    return get_all_locators(platform)


@router.get("/{page}")
def list_page_locators(page: str, platform: str = Query("website")):
    """Return all locators for a specific page/group, from whichever database holds it."""
    data = grouped(platform)
    if page not in data:
        raise HTTPException(status_code=404, detail=f"Page '{page}' not found.")
    return data[page]


class CheckBody(BaseModel):
    name: str = ""
    page: str = ""
    xpath: str = ""
    platform: str = "website"
    #: The step being written, when there is one. It tells the namer what the
    #: element is FOR, which the selector alone often does not.
    step: str = ""


def _suggestions(body) -> list[str]:
    """Names derived from the selector itself, minus any already taken."""
    from locators import naming
    from locators.sources import names as _names

    if not body.xpath:
        return []
    return naming.suggest_names(body.xpath, step=getattr(body, "step", ""),
                                group=body.page,
                                taken=_names(body.platform))


@router.post("/check")
def check_before_save(body: CheckBody):
    """
    What is wrong with saving this element, without saving it.

    Called as the form is filled in, so the choice — reuse, rename, ask for the
    existing one to be changed — is offered before the work of typing a selector,
    not after.
    """
    from locators import naming
    from locators.manager import normalise_name
    from locators.validation import as_dicts, check_locator

    try:
        name = normalise_name(body.name, kind="locator")
    except ValueError as e:
        # Still offer names, so an author who typed something unusable is given
        # something to accept rather than only being told no.
        return {"ok": False, "normalised": "", "conflicts": [],
                "reason": str(e), "suggested_names": _suggestions(body),
                "convention": naming.explain()}
    conflicts = check_locator(name, body.page, body.xpath, platform=body.platform)
    return {"ok": not any(c.severity == "blocking" for c in conflicts),
            "normalised": name,
            "changed": name != (body.name or "").strip(),
            "conflicts": as_dicts(conflicts),
            "suggested_names": _suggestions(body),
            "convention": naming.explain()}


@router.post("/resync-snippets")
def resync_snippets():
    """
    Rewrite the editor snippet file from the current element list.

    Called after a save so a newly recorded element is suggestible immediately.
    A failure here is reported but not raised: the element IS saved, and the
    snippet file being stale is a smaller problem than an error implying it was
    not.
    """
    try:
        from reporting.snippet_sync import sync_locators_to_snippets

        sync_locators_to_snippets()
        return {"synced": True}
    except Exception as e:  # noqa: BLE001
        return {"synced": False, "reason": f"{type(e).__name__}: {e}"}


@router.post("", status_code=201)
def add_locator(body: LocatorBody):
    """
    Add or overwrite a manual locator.

    Writes to the registry's writable database. Names are normalised here as
    well as in manager.add_locator(): the HTTP route is a separate write path,
    and a rule enforced in only one of them is not a rule.
    """
    from locators.manager import normalise_name

    try:
        page = normalise_name(body.page, kind="group")
        name = normalise_name(body.name, kind="locator")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    target = writable_source()
    if target is None:
        raise HTTPException(
            status_code=503,
            detail="No writable locator database is registered.",
        )

    # Checked BEFORE anything is written. Afterwards a duplicate is referenced
    # by tests and removing it means editing them.
    from locators.validation import as_dicts, check_locator

    conflicts = check_locator(name, page, body.xpath, platform="website")
    blocking = [c for c in conflicts if c.severity == "blocking"]
    if blocking and not body.force:
        raise HTTPException(
            status_code=409,
            detail={"message": blocking[0].message,
                    "why": blocking[0].why,
                    "conflicts": as_dicts(conflicts),
                    "hint": "Pick one of the options, or resend with force=true to "
                            "save anyway."})

    data = load_locators()
    data.setdefault(page, {})[name] = {
        "xpath": body.xpath,
        "dna": body.dna,
    }
    _write_manual(data)

    # A new name that already exists elsewhere will not win resolution, and the
    # author should hear that now rather than when the step targets the wrong
    # element at run time.
    existing = owner(name, "website")
    shadowed = bool(existing and existing.source_id != target.id)
    return {
        "message": f"Locator '{page}/{name}' saved.",
        "xpath": body.xpath,
        "normalised": {"page": page, "name": name},
        "written_to": target.id,
        "warning": (
            f"'{name}' is also defined in {existing.source_id}:{existing.group}, "
            f"which the runner resolves first — this new entry will not be used."
            if shadowed else ""
        ),
    }


class LocatorRename(BaseModel):
    new_name: str
    apply: bool = False


@router.post("/{page}/{name}/rename")
def rename_locator_route(page: str, name: str, body: LocatorRename):
    """Rename an element and rewrite every step that referred to it."""
    from execution.refactor import RenameError, rename_locator

    try:
        return rename_locator(page, name, body.new_name, apply=body.apply)
    except RenameError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.delete("/{page}/{name}", status_code=200)
def delete_locator(page: str, name: str, platform: str = Query("website")):
    """
    Remove a locator.

    Only the writable database can be edited here. Deleting from a read-only
    source (the spy's recording) is refused with an explanation rather than a
    bare 404 that suggests the locator does not exist.
    """
    data = load_locators()
    if page in data and name in data[page]:
        del data[page][name]
        if not data[page]:          # prune empty page bucket
            del data[page]
        _write_manual(data)
        return {"message": f"Locator '{page}/{name}' deleted.", "deleted_from": "manual"}

    # Not in the writable database — say precisely why, using the registry.
    found = next((e for e in (owner(name, platform),) if e and e.group == page), None)
    if found is None:
        found = next((e for e in [owner(name, platform)] if e), None)
    if found is not None:
        src = next((s for s in sources() if s.id == found.source_id), None)
        if src is not None and not src.writable:
            raise HTTPException(
                status_code=409,
                detail=(f"'{name}' lives in {src.label} ({src.id}), which is not "
                        f"editable here. Re-record it with the spy, or add an "
                        f"overriding entry to the manual database."),
            )
    raise HTTPException(status_code=404, detail=f"Locator '{page}/{name}' not found.")
