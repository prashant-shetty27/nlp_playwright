"""
api/routes/nlp.py
POST /nlp/parse   — parse one NLP step → Command dict
POST /nlp/suggest — autocomplete suggestions for a partial step
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from nlp.parser import parse_step
from nlp.keywords import KEYWORD_MAP

router = APIRouter(prefix="/nlp", tags=["nlp"])


# ── Request / Response models ──────────────────────────────────────────────────

class ParseRequest(BaseModel):
    step: str


class SuggestRequest(BaseModel):
    partial: str
    # Required: a suggestion is only useful if the runner for this platform can
    # actually dispatch it. Without it the endpoint offered web-only actions such
    # as enter_otp while the operator was authoring a mobile flow.
    platform: str
    limit: int = 10


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/parse")
def parse(body: ParseRequest):
    """
    Parse a single NLP step string into a structured Command.

    Example:
        POST /nlp/parse
        {"step": "search for Restaurants"}
        → {"type": "search", "text": "Restaurants", ...}
    """
    step = body.step.strip()
    if not step:
        raise HTTPException(status_code=422, detail="'step' must not be empty.")
    try:
        cmd = parse_step(step)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # dataclass → dict for JSON serialisation
    import dataclasses
    return dataclasses.asdict(cmd)


@router.post("/suggest")
def suggest(body: SuggestRequest):
    """
    Return NLP phrase suggestions that start with (or contain) the partial input.

    Example:
        POST /nlp/suggest
        {"partial": "scroll"}
        → [{"phrase": "scroll down", "action": "scroll_down"}, ...]
    """
    from ai_flow_builder.catalogue import load as load_catalogue
    from nlp.platforms import UnknownPlatform, normalise

    try:
        platform = normalise(body.platform)
    except UnknownPlatform as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    dispatchable = load_catalogue(platform).supported_commands
    partial = body.partial.strip().lower()
    results: list[dict] = []

    for _key, entry in KEYWORD_MAP.items():
        # Deprecated entries name an action no runner dispatches. Their phrasing
        # is kept in the map for the record, but suggesting them would hand the
        # operator a step that cannot run.
        if entry.get("deprecated"):
            continue
        action = entry.get("action", "")
        # Never suggest something this platform's runner cannot execute.
        if action not in dispatchable:
            continue
        for phrase in entry.get("phrases", []):
            if partial in phrase.lower():
                # `template` is the complete, parseable statement — the caller can
                # insert it directly instead of reconstructing syntax from prose.
                results.append({"phrase": phrase, "action": action,
                                "platform": platform,
                                "template": entry.get("template", "")})
                if len(results) >= body.limit:
                    return results

    return results


@router.get("/platforms")
def platforms(include_disabled: bool = True):
    """
    The platform vocabulary, so the UI never hardcodes it.

    `enabled: false` means known but masked — native app support resolves and
    validates, it is simply not offered in the picker yet. The UI should render
    those greyed out rather than omitting them, so the list stays honest about
    what exists.
    """
    from nlp.platforms import as_dicts

    return {"platforms": as_dicts(include_disabled=include_disabled)}


@router.get("/devices")
def device_catalogue(curated_only: bool = True):
    """
    Device profiles a mobile-web run can emulate.

    `curated_only=false` returns Playwright's full catalogue (~143 profiles,
    including landscape duplicates and older hardware). Each entry names the
    engine the device expects, so the UI can show that picking an iPhone will
    launch WebKit rather than leaving that as a hidden surprise.
    """
    from nlp.platforms import devices

    return {"devices": devices(curated_only=curated_only)}
