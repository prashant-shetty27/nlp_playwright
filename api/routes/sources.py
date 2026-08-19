"""
api/routes/sources.py — uploading testcase sources.

Upload is separate from generation on purpose: one sheet is uploaded once and
then generated from many times, with different testcase selections, without
re-uploading. The response reports what was actually ingested — including rows
that were REJECTED — so the operator can see the tool's reading of their file
before generating anything from it.
"""
from __future__ import annotations

import os
import sys

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from ai_flow_builder.storage import (SourceNotFound,  # noqa: E402
                                     UnsupportedSource, adapter_for,
                                     default_store)

router = APIRouter(prefix="/sources", tags=["sources"])

#: Overridable like every other limit — a team with larger workbooks should
#: not have to edit source to raise it.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))


def _ingest(rec) -> dict:
    """Parse a stored source and summarise what came out of it."""
    from ai_flow_builder.pipeline import load_bundle

    b = load_bundle(adapter_for(rec), progress=lambda *_a, **_k: None)
    return {
        "tabs_read": b.tabs_read,
        "tabs_skipped": b.tabs_skipped,
        "testcases": [
            {"id": t.testcase_id, "title": t.title, "steps": len(t.steps),
             "priority": t.priority, "classification": t.classification,
             "interpretable": t.is_interpretable}
            for t in b.testcases
        ],
        "counts": {
            "testcases": len(b.testcases),
            "interpretable": len(b.interpreted),
            "variables": len(b.variables),
            "conversions": len(b.conversions),
            "rejected_rows": len(b.rejections),
        },
        "duplicate_ids": b.duplicate_ids,
        "unresolved_variables": b.unresolved_variables(),
    }


@router.post("/upload", status_code=201)
async def upload_source(file: UploadFile = File(...), uploaded_by: str = ""):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is {len(data)} bytes; the limit is {MAX_UPLOAD_BYTES}.",
        )
    try:
        rec = default_store.put(data, file.filename or "upload", uploaded_by=uploaded_by)
    except UnsupportedSource as e:
        raise HTTPException(status_code=415, detail=str(e)) from e

    try:
        ingested = _ingest(rec)
    except Exception as e:  # noqa: BLE001
        # The bytes are kept even when parsing fails — the file is still the
        # evidence needed to work out why, and deleting it would destroy that.
        raise HTTPException(
            status_code=422,
            detail=f"Stored as {rec.source_id} but could not be parsed: {e}",
        ) from e

    return {**rec.to_dict(), "stored_in": default_store.name, **ingested}


class PromptRequest(BaseModel):
    prompt: str
    platform: str                    # required — see nlp/platforms.py
    max_testcases: int = 10
    provider: str = ""               # blank = LLM_PROVIDER, else this adapter
    model: str = ""                  # blank = the provider's default
    uploaded_by: str = ""
    #: Ask the model again instead of returning the draft already on file for
    #: this exact request. Off by default: a model does not write the same
    #: testcases twice, and silently re-drafting meant the same prompt produced
    #: "${test_url}" one day and "${page_url}" the next, under the same id.
    redraft: bool = False


@router.post("/prompt", status_code=201)
def draft_from_prompt(body: PromptRequest):
    """
    Draft testcases from a written request and store them as a source.

    The result is an ordinary source: it lists, retrieves and generates exactly
    like an uploaded workbook. Everything downstream — the mapper, locator reuse,
    per-step statuses, linting — is the same code path, so a prompt gets the same
    guarantees a spreadsheet does. In particular, nothing a model writes reaches
    a flow without passing the live parser and the runner's dispatch table.
    """
    from ai_flow_builder.llm import (ProviderError, ProviderNotConfigured,
                                     ProviderRefused, ProviderUnavailable)
    from ai_flow_builder.prompt_source import draft_testcases
    from nlp.platforms import UnknownPlatform, normalise

    text = (body.prompt or "").strip()
    if len(text) < 15:
        raise HTTPException(
            status_code=422,
            detail="Describe what to test in a sentence or more — a few words "
                   "cannot be turned into testcases without inventing the rest.",
        )
    if len(text) > 20000:
        raise HTTPException(status_code=413, detail="Prompt is too long (20,000 char limit).")
    if not 1 <= body.max_testcases <= 25:
        raise HTTPException(status_code=422, detail="max_testcases must be between 1 and 25.")

    try:
        platform = normalise(body.platform)
    except UnknownPlatform as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    # Same request, same answer. Re-asking returns what is already on file, so a
    # prompt is reproducible and a second look does not cost a model call.
    from ai_flow_builder.storage import prompt_source_id

    if not body.redraft:
        cached_id = prompt_source_id(text, platform, body.max_testcases)
        try:
            rec = default_store.get(cached_id)
        except SourceNotFound:
            rec = None
        if rec is not None:
            return {
                **rec.to_dict(),
                "stored_in": default_store.name,
                "platform": platform,
                "cached": True,
                "values_found": rec.extra.get("values_found", {}),
                "inputs_needed": rec.extra.get("inputs_needed", []),
                "assumptions": rec.extra.get("assumptions", []),
                "unclear": rec.extra.get("unclear", []),
                **_ingest(rec),
            }

    try:
        draft = draft_testcases(text, platform=platform,
                                max_testcases=body.max_testcases,
                                provider=body.provider or None,
                                model=body.model or None)
    except ProviderNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ProviderUnavailable as e:
        # Transient, so the caller should retry rather than change the request.
        headers = {"Retry-After": str(e.retry_after)} if e.retry_after else None
        raise HTTPException(status_code=503, detail=str(e), headers=headers) from e
    except ProviderRefused as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ProviderError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    rec = default_store.put_prompt(draft, text, uploaded_by=body.uploaded_by,
                                   platform=platform, max_testcases=body.max_testcases)
    return {
        **rec.to_dict(),
        "stored_in": default_store.name,
        "platform": platform,
        "cached": False,
        # Surfaced so the operator can judge the draft before generating from it.
        "values_found": draft.get("values_found", {}),
        "inputs_needed": draft.get("inputs_needed", []),
        "assumptions": draft.get("assumptions", []),
        "unclear": draft.get("unclear", []),
        **_ingest(rec),
    }


@router.get("")
def list_sources():
    return {"sources": [r.to_dict() for r in default_store.list()],
            "stored_in": default_store.name}


@router.get("/{source_id}")
def get_source(source_id: str):
    try:
        rec = default_store.get(source_id)
    except SourceNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    try:
        return {**rec.to_dict(), **_ingest(rec)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not parse: {e}") from e


@router.delete("/{source_id}")
def delete_source(source_id: str):
    if not default_store.delete(source_id):
        raise HTTPException(status_code=404, detail=f"No source {source_id!r}.")
    return {"deleted": source_id}
