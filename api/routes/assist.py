"""
api/routes/assist.py — turn a review finding into something you can act on.

A finding falls into one of three kinds, and each needs a different action:

  APPLY     A replacement can be derived with certainty — a hardcoded value that
            is already stored, a fixed wait with an obvious element after it.
            ai_flow_builder/review.py carries the corrected step and the editor
            applies it. No model, no question.

  COLLECT   The fix needs a value only the author has: an element that is not
            recorded needs a selector. Nothing can invent that, so the editor
            opens the right form with what it already knows filled in.

  PROPOSE   The fix is a judgement — "this test asserts nothing" cannot be
            answered by a rule, because what SHOULD be asserted depends on what
            the test is for. Here a model earns its place: it reads the steps and
            proposes candidate assertions, which the author picks from.

This route serves the third kind. It never edits anything: it returns candidates
with the reason for each, and the editor inserts the one that is chosen. A
proposal applied without being read is how a suite fills up with assertions that
pass regardless of what the page does.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/review", tags=["review"])


class AssistRequest(BaseModel):
    kind: str                       # the finding's `kind`
    steps: list[str]
    platform: str = "website"
    step_index: int = 0
    flow_name: str = ""


SYSTEM = """You add assertions to an existing automated test.

You are given the steps of a test that currently checks nothing. Propose at most
three assertions it SHOULD make, using only these exact shapes:

  verify text "<visible text>" on page
  verify element <element_name> is visible
  verify element <element_name> has text "<text>"
  verify element <element_name> is not visible

Rules:
- Only use element names that appear in the steps you were given. Never invent one.
- Prefer asserting the OUTCOME the test exists to prove, not that a button you
  just clicked still exists.
- EVERY proposal must be one of the verify shapes above. A click, a wait or a
  navigation is not an assertion and will be discarded.
- `insert_after` is the 1-based number of the step your assertion should follow.
  Use 0 to put it first. Assert AFTER the step that produces what you are checking.
- Each proposal needs a one-line reason a tester would agree with.
- If the steps do not make the intended outcome clear, say so in `unclear`
  instead of guessing.
"""


def _models():
    from pydantic import BaseModel as BM

    class Proposal(BM):
        step: str
        reason: str
        insert_after: int

    class Proposals(BM):
        proposals: list[Proposal]
        unclear: list[str]

    return Proposals


@router.post("/assist")
def assist(body: AssistRequest):
    """Propose fixes for a finding that cannot be resolved by a rule."""
    if body.kind != "no_assertion":
        raise HTTPException(
            status_code=422,
            detail=(f"'{body.kind}' does not need a proposal — it is either applied "
                    f"directly or needs a value from you."))

    # Position is carried alongside the text. The model reads only the LIVE
    # steps, but the editor holds the raw list — comments, purpose bands and
    # switched-off steps included — so a live position handed straight back put
    # the assertion in the wrong place in any test carrying a band.
    live_pairs = [(i, s) for i, s in enumerate(body.steps, 1)
                  if s.strip() and not s.strip().startswith("#")]
    live = [s for _, s in live_pairs]
    if not live:
        raise HTTPException(status_code=422, detail="There are no steps to read.")

    from ai_flow_builder.llm import (ProviderError, ProviderNotConfigured,
                                     ProviderRefused, ProviderUnavailable,
                                     get_provider)

    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(live, 1))
    try:
        completion = get_provider().complete_structured(
            system=SYSTEM,
            user=(f"Platform: {body.platform}\nTest: {body.flow_name or '(unnamed)'}\n\n"
                  f"Steps:\n{numbered}"),
            schema=_models())
    except ProviderNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ProviderUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ProviderRefused as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ProviderError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    data = completion.data
    # Every proposal is checked against the live parser before it is offered.
    # A suggestion that cannot parse is not a suggestion, it is a retype.
    from nlp.parser import parse_step

    ok, rejected = [], []
    for prop in data.get("proposals", []):
        step = (prop.get("step") or "").strip()
        try:
            cmd = parse_step(step)
        except Exception:  # noqa: BLE001
            rejected.append({"step": step, "why": "does not parse"})
            continue
        # It must actually be an ASSERTION. A proposal that merely parses can be
        # another click — which is what came back the first time this ran, and
        # adding it would have left the test still checking nothing while
        # appearing to have been fixed.
        if not str(cmd.type).startswith("verify"):
            rejected.append({"step": step,
                             "why": f"is a {cmd.type} step, not a check"})
            continue
        # Where it goes. Clamped into the test so a bad index cannot append
        # somewhere meaningless, then translated out of live positions into the
        # ones the editor indexes by.
        after = max(0, min(int(prop.get("insert_after") or len(live)), len(live)))
        prop["after_step_text"] = live[after - 1] if after else "(the start)"
        prop["insert_after"] = live_pairs[after - 1][0] if after else 0
        ok.append(prop)

    return {"proposals": ok, "unclear": data.get("unclear", []),
            "model": completion.model, "provider": completion.provider,
            "rejected": rejected, "steps_read": len(live)}
