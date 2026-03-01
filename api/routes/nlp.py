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
    partial = body.partial.strip().lower()
    results: list[dict] = []

    for _key, entry in KEYWORD_MAP.items():
        action = entry.get("action", "")
        for phrase in entry.get("phrases", []):
            if partial in phrase.lower():
                results.append({"phrase": phrase, "action": action})
                if len(results) >= body.limit:
                    return results

    return results
