"""
api/routes/nlp.py
POST /nlp/parse     — parse one NLP step → Command dict
POST /nlp/suggest   — autocomplete suggestions for a partial step
POST /nlp/segment   — split a step into separately-editable values
POST /nlp/variables — where each ${variable} in a test gets its value
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from nlp.parser import parse_step
from nlp.keywords import KEYWORD_MAP
#: A ${name} reference inside a step. One definition, shared with the editor's
#: variable panel via this route rather than re-written in the UI.
from nlp.variables import REFERENCE_RE as _VAR_REF

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
    # Several phrasings share one template: "click", "click element" and "click on
    # element" all insert `click {locator}`. The caller shows the TEMPLATE, so
    # offering each phrase separately rendered the same line three times over and
    # crowded out genuinely different commands. One row per distinct insertion;
    # the alternative phrasings ride along under "aliases" so nothing is lost.
    seen: dict[str, dict] = {}

    for _key, entry in KEYWORD_MAP.items():
        # Deprecated entries name an action no runner dispatches. Their phrasing
        # is kept in the map for the record, but suggesting them would hand the
        # operator a step that cannot run.
        if entry.get("deprecated") or entry.get("hidden"):
            continue
        action = entry.get("action", "")
        # Never suggest something this platform's runner cannot execute.
        if action not in dispatchable:
            continue
        template = entry.get("template", "")
        for phrase in entry.get("phrases", []):
            if partial not in phrase.lower():
                continue
            # `template` is the complete, parseable statement — the caller can
            # insert it directly instead of reconstructing syntax from prose.
            key = template or phrase
            if key in seen:
                seen[key]["aliases"].append(phrase)
                continue
            row = {"phrase": phrase, "action": action, "platform": platform,
                   "template": template, "aliases": []}
            seen[key] = row
            results.append(row)
            if len(results) >= body.limit:
                return results

    # Saved step groups are offered alongside commands, tagged so they read as
    # what they are: several steps behind one name. Without this the feature was
    # invisible — you had to remember both that a group existed and what it was
    # called before you could type `call <name>`.
    try:
        from core.reusable_steps import describe as _groups

        for g in _groups(platform):
            name = g["name"]
            if partial and partial not in name.lower() and partial not in "call":
                continue
            results.append({
                "phrase": f"call {name}",
                "action": "call_reusable",
                "platform": platform,
                "template": f"call {name}",
                "aliases": [],
                "kind": "stepgroup",
                "tag": "sg",
                "detail": f"{g['step_count']} steps",
                "steps": g["steps"][:6],
            })
            if len(results) >= body.limit:
                break
    except Exception:  # noqa: BLE001 — a broken group file must not break typing
        pass

    return results


class SegmentRequest(BaseModel):
    step: str


@router.post("/segment")
def segment_step(body: SegmentRequest):
    """
    Split a step into fixed words and separately-editable values.

    The editor renders `click maybe_later_link` with the locator as its own
    clickable token, so changing an element does not mean retyping the line and
    does not mean guessing which word is the element. Which field means what is
    decided in nlp/fields.py, shared with the linter.

    An unparseable step (normal for a half-typed one) still returns segments —
    just fewer of them — so the editor always has something to draw.
    """
    import dataclasses

    step = body.step or ""
    try:
        parsed = dataclasses.asdict(parse_step(step))
    except Exception:  # noqa: BLE001 — mid-edit steps legitimately do not parse
        parsed = None

    from nlp.fields import segment

    out = {
        "step": step,
        "parses": parsed is not None,
        "action": (parsed or {}).get("type", ""),
        "segments": [dataclasses.asdict(seg) for seg in segment(step, parsed)],
    }
    if parsed is None and step.strip() and not step.lstrip().startswith("#"):
        out["hint"] = _closest_template(step)
    return out


def _closest_template(step: str) -> dict:
    """
    The step shape this line was probably going for — the longest keyword
    phrase it starts with, and that keyword's template and help. Shown under a
    line that does not parse, so the fix is a comparison rather than a guess.
    """
    low = step.strip().lower()
    best: tuple[int, str, dict] | None = None
    for name, kw in KEYWORD_MAP.items():
        for ph in kw.get("phrases", []):
            ph_l = ph.lower()
            # Start-of-line matches win; a phrase found anywhere ("until
            # element" inside a swipe) breaks the tie for the longer form.
            score = (2 if low.startswith(ph_l) else 1 if ph_l in low else 0) * 100 + len(ph_l)
            if score >= 100 and (best is None or score > best[0]):
                best = (score, name, kw)
    if not best:
        return {}
    _, name, kw = best
    return {"keyword": name, "template": kw.get("template", ""),
            "help": kw.get("help", "")}


class SegmentBatchRequest(BaseModel):
    steps: list[str]


@router.post("/segment-batch")
def segment_steps(body: SegmentBatchRequest):
    """
    /segment for a whole test case in one round trip.

    The editor drew a 170-step case with 170 sequential requests, one per
    row, and the page sat blank for seconds while they queued. Parsing is
    pure and cheap; the network hops were the cost.
    """
    seen: dict[str, dict] = {}
    for st in body.steps:
        if st not in seen:
            seen[st] = segment_step(SegmentRequest(step=st))
    return {"segments": seen}


class VariablesRequest(BaseModel):
    steps: list[str]
    environment: str = ""


@router.post("/variables")
def variables(body: VariablesRequest):
    """
    Where every ${variable} in a test gets its value — or that it has none.

    Three sources, and the difference between them is what the editor colours:

      defined   a step earlier in this test produces it — `store text of x as
                otp_code`, `fetch otp for … as code`. Carried with the step
                number, because a variable used BEFORE the step that sets it is
                empty at that moment, however well defined it is later.
      stored    it is in Test Data, so every run has it without being asked.
      unresolved  neither. The run will stop and ask for it in Run Center.

    Here rather than in the editor because knowing which field of which command
    DEFINES a variable is grammar, and the UI is deliberately kept out of the
    grammar — nlp/fields.py already owns the same question for the linter.
    """
    import dataclasses

    defined: dict[str, int] = {}
    referenced: dict[str, int] = {}
    for i, step in enumerate(body.steps, 1):
        text = (step or "").strip()
        if not text or text.startswith("#"):
            continue
        for name in _VAR_REF.findall(text):
            referenced.setdefault(name, i)
        try:
            cmd = dataclasses.asdict(parse_step(text))
        except Exception:  # noqa: BLE001 — a half-written step defines nothing
            continue
        made = cmd.get("variable_name")
        if isinstance(made, str) and made:
            defined.setdefault(made, i)

    try:
        from execution.test_data import get_all

        stored = sorted(get_all(body.environment))
    except Exception:  # noqa: BLE001
        stored = []

    known = set(defined) | set(stored)
    return {"defined": defined, "stored": stored,
            "unresolved": sorted(n for n in referenced if n not in known)}


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
