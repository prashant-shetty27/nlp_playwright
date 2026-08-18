"""
api/routes/generate.py — turn selected testcases into a .flow script.

Generation is READ-ONLY by default: it returns the flow text and writes nothing.
A UI regenerates on every change of testcase selection or platform, and writing
a file each time would litter data/drafts/ and clobber earlier work. Saving is a
deliberate act — `persist: true`.

Nothing here decides whether a step is automatable. That judgement belongs to
ai_flow_builder/mapper.py, which checks each step against the LIVE parser and
runner dispatch table. This route only exposes the verdict, per step, so the UI
can ask for a locator where one is missing and let the operator rewrite a step
the mapper could not interpret.
"""
from __future__ import annotations

import os
import sys

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from ai_flow_builder.storage import (SourceNotFound, adapter_for,  # noqa: E402
                                     default_store)

router = APIRouter(tags=["generate"])


class GenerateRequest(BaseModel):
    source_id: str
    testcase_ids: list[str]
    platform: str                      # required — see nlp/platforms.py
    flow_name: str = ""
    persist: bool = False
    out_path: str = ""                 # only consulted when persist is true
    overwrite: bool = False
    # Source variable name -> runtime placeholder, e.g.
    #   {"PDP_URL_AI_3IMG": "${product_url}"}
    # The spreadsheet names a variable; this says which runtime value fills it.
    # Without a binding the generated flow still references the raw source name,
    # which nothing defines — so the flow renders but cannot execute.
    variable_bindings: dict[str, str] = {}


@router.post("/generate")
def generate(body: GenerateRequest):
    from ai_flow_builder import pipeline
    from ai_flow_builder.mapper import summarise
    from nlp.platforms import UnknownPlatform, normalise

    try:
        platform = normalise(body.platform)
    except UnknownPlatform as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    if not body.testcase_ids:
        raise HTTPException(status_code=422, detail="Select at least one testcase.")

    try:
        rec = default_store.get(body.source_id)
    except SourceNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    quiet = lambda *_a, **_k: None  # noqa: E731
    bundle = pipeline.load_bundle(adapter_for(rec), progress=quiet)

    known = {t.testcase_id for t in bundle.testcases}
    missing = [t for t in body.testcase_ids if t not in known]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Testcase id(s) not in this source: {', '.join(missing)}",
        )

    flow_name = body.flow_name or ", ".join(body.testcase_ids)

    # A caller-supplied out_path is a file write, so it is confined to the drafts
    # directory. Without this, `out_path: "../../etc/x"` (or any absolute path)
    # writes wherever the server process can reach — and `overwrite` is caller-
    # controlled too, so it would also clobber.
    drafts = os.path.join(BASE_DIR, "data", "drafts")
    os.makedirs(drafts, exist_ok=True)
    if body.out_path:
        candidate = os.path.realpath(os.path.join(drafts, os.path.basename(body.out_path)))
        if not candidate.endswith(".flow"):
            raise HTTPException(status_code=422, detail="out_path must end in .flow")
        if os.path.commonpath([candidate, os.path.realpath(drafts)]) != os.path.realpath(drafts):
            raise HTTPException(status_code=422,
                                detail="out_path must stay within data/drafts/")
        out_path = candidate
    else:
        out_path = os.path.join(
            drafts, f"{rec.source_id}_{'_'.join(body.testcase_ids)}.flow")

    try:
        res = pipeline.generate(
            bundle=bundle,
            testcase_ids=list(body.testcase_ids),
            flow_name=flow_name,
            out_path=out_path,
            platform=platform,
            variable_bindings=dict(body.variable_bindings or {}),
            max_flows_per_batch=1,
            overwrite=body.overwrite,
            persist=body.persist,
            progress=quiet,
        )
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=f"{e} Pass overwrite=true.") from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    steps, n = [], 0
    for m in res.mappings:
        entry = {
            "statement": m.statement,
            "action": m.command_type,
            "status": m.status,
            "source": m.source_ref,
            "manual_step": m.manual_step,
            "locator": m.locator,
            "locator_reused": m.locator_reused,
            "evidence": m.evidence,
            "note": m.note,
            "emits": m.emits,
        }
        if m.emits:
            n += 1
            entry["step"] = n
        steps.append(entry)

    return {
        "description": _describe(bundle, body.testcase_ids),
        "variables": _variable_report(res, bundle, body.variable_bindings or {}),
        "flow_name": res.flow_name,
        "flow_text": res.flow_text,
        "platform": platform,
        "source_id": rec.source_id,
        "source_testcases": res.source_ids,
        "summary": summarise(res.mappings),
        "steps": steps,
        "required_locators": res.required_locators,
        "missing_locators": res.missing_locators,
        "reused_locators": sorted(res.reused_locators),
        "validation": {
            "parser_ok": res.parser_ok,
            "parser_errors": res.parser_errors,
            "lint_exit": res.lint_exit,
            "lint_output": res.lint_output,
        },
        "persisted": {
            "flow_path": res.flow_path or None,
            "map_path": res.map_path or None,
        },
    }


# How an input is recognised from its variable name. Name-based because the
# spreadsheet names variables meaningfully (PDP_URL_*, TEST_MOBILE, VALID_OTP)
# while its sample column does not carry usable values.
_INPUT_TYPES = (
    ("otp",    ("otp", "one_time", "onetime")),
    ("mobile", ("mobile", "phone", "msisdn", "contact_number")),
    ("url",    ("url", "link", "endpoint", "pdp", "prp")),
    ("email",  ("email", "mail")),
)


def _classify(name: str) -> str:
    low = name.lower()
    for kind, needles in _INPUT_TYPES:
        if any(n in low for n in needles):
            return kind
    return "text"


def _variable_report(res, bundle, bindings: dict) -> dict:
    """
    The inputs this flow needs before it can run.

    Values are NEVER taken from the spreadsheet. Its sample column holds prose —
    "QA test-number pool (must be OTP-reachable)", "GJDT-22166 UAT URL - Rubber Tag
    Applicator" — which describes where to get a value rather than being one. The
    tool previously treated two of those as real values, which would have told the
    operator they were covered when they were not. A sample is therefore passed
    through as a HINT, clearly labelled, and never as a value.

    Testcases describe a flow, not its data, so every input is collected from the
    operator after the steps exist.
    """
    from ai_flow_builder.bundle import referenced_variables

    used: set[str] = set()
    for line in (res.flow_text or "").splitlines():
        st = line.strip()
        if st and not st.startswith("#"):
            used |= referenced_variables(st)

    reverse = {v.strip("${}"): k for k, v in (bindings or {}).items() if v}
    steps_using: dict[str, list[int]] = {}
    n = 0
    for m in res.mappings:
        if not m.emits:
            continue
        n += 1
        for v in referenced_variables(m.statement):
            steps_using.setdefault(v, []).append(n)

    inputs = []
    for name in sorted(used):
        var = bundle.variables.get(name)
        kind = _classify(name)
        entry = {
            "name": name,
            "type": kind,
            "required": True,          # always — nothing is pre-filled from the sheet
            "value": None,
            "bound_from": reverse.get(name, ""),
            "used_by_steps": steps_using.get(name, []),
            "description": (var.description if var else ""),
            # Shown to help the operator recognise the input. Explicitly NOT a value.
            "sheet_hint": (var.sample if var else ""),
        }
        if kind == "otp":
            # Two ways an OTP is obtained. Which one applies depends on the mobile
            # number in use, so the operator picks per run rather than per flow.
            entry["otp_source"] = {
                "options": ["static", "portal"],
                "default": "static",
                "static": "the number has a fixed OTP that you supply",
                "portal": "fetch the live OTP for this number from the OTP portal",
            }
        inputs.append(entry)

    return {
        "required": [i["name"] for i in inputs],
        "inputs": inputs,
        "unbound_source_variables": [i["name"] for i in inputs
                                     if bundle.variables.get(i["name"])
                                     and not i["bound_from"]],
        "note": ("Every input is collected from the operator. Spreadsheet samples "
                 "are hints only — they describe where a value comes from, not the "
                 "value itself, so none are pre-filled."),
    }


def _describe(bundle, testcase_ids: list[str]) -> dict:
    """
    What this flow verifies.

    A flow spanning several testcases loses their individual titles, so a reader
    cannot tell what it covers or which case a failing step belongs to. This keeps
    that visible.
    """
    chosen = [t for t in bundle.testcases if t.testcase_id in set(testcase_ids)]
    cases = [{
        "id": t.testcase_id,
        "title": t.title,
        "verifies": (t.expected or "").strip(),
        "priority": t.priority,
        "type": t.classification,
        "steps": len(t.steps),
    } for t in chosen]
    if len(cases) == 1:
        summary = cases[0]["title"]
    else:
        summary = (f"{len(cases)} testcases: "
                   + "; ".join(f"{c['id']} {c['title'][:56]}" for c in cases))
    return {"summary": summary, "cases": cases}
