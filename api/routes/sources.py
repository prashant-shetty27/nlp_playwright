"""
api/routes/sources.py — uploading testcase sources.

Upload is separate from generation on purpose: one sheet is uploaded once and
then generated from many times, with different testcase selections, without
re-uploading. The response reports what was actually ingested — including rows
that were REJECTED — so the operator can see the tool's reading of their file
before generating anything from it.
"""
from __future__ import annotations

import json
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


def _draft_cases(draft: dict) -> list[dict]:
    """The drafted testcases with their steps — what the reviewer reads."""
    return [{"id": tc.get("testcase_id", ""), "title": tc.get("title", ""),
             "classification": tc.get("classification", ""),
             "priority": tc.get("priority", ""),
             "covers": tc.get("covers") or [], "expected": tc.get("expected", ""),
             "preconditions": tc.get("preconditions") or [],
             "steps": tc.get("steps") or []}
            for tc in (draft or {}).get("testcases") or []]


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


class PromptAttachment(BaseModel):
    name: str
    text: str


class PromptRequest(BaseModel):
    prompt: str
    #: Spreadsheets the tester attached, already read into text by the UI.
    attachments: list[PromptAttachment] = []
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
    #: Name of a saved test case the drafted steps will be appended to.
    extend_flow: str = ""


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
    if not 1 <= body.max_testcases <= 60:
        raise HTTPException(status_code=422, detail="max_testcases must be between 1 and 60.")

    try:
        platform = normalise(body.platform)
    except UnknownPlatform as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    # Same request, same answer. Re-asking returns what is already on file, so a
    # prompt is reproducible and a second look does not cost a model call.
    from ai_flow_builder.storage import prompt_source_id

    if not body.redraft:
        cache_text = text + "".join(f"\n[att {a.name}:{len(a.text)}]" for a in body.attachments) \
            + (f"\n[extend {body.extend_flow}]" if body.extend_flow else "")
        cached_id = prompt_source_id(cache_text, platform, body.max_testcases)
        try:
            rec = default_store.get(cached_id)
        except SourceNotFound:
            rec = None
        if rec is not None:
            try:
                with open(rec.path, "r", encoding="utf-8") as f:
                    cached_draft = (json.load(f) or {}).get("draft") or {}
            except Exception:  # noqa: BLE001
                cached_draft = {}
            return {
                **rec.to_dict(),
                "draft_testcases": _draft_cases(cached_draft),
                "stored_in": default_store.name,
                "platform": platform,
                "cached": True,
                "values_found": rec.extra.get("values_found", {}),
                "inputs_needed": rec.extra.get("inputs_needed", []),
                "assumptions": rec.extra.get("assumptions", []),
                "unclear": rec.extra.get("unclear", []),
                "jira": rec.extra.get("jira", []),
                "questions": rec.extra.get("questions", []),
                **_ingest(rec),
            }

    # An earlier draft for the SAME ticket is the starting point: the model is
    # asked only for what the new prompt adds or changes, and the reviewed
    # cases are kept by id. Without this a second draft re-produced the whole
    # set (and overran the output budget doing it).
    previous = None
    try:
        from ai_flow_builder.jira_source import find_keys
        keys = set(find_keys(text))
        if keys and not body.extend_flow:      # extending a case: the case is the baseline
            from ai_flow_builder.storage import DRAFTER_VERSION
            for rec in default_store.list():
                if rec.kind != "prompt" or not (set(k.get("key") for k in rec.extra.get("jira", [])) & keys):
                    continue
                # Only a draft made for THIS platform under the CURRENT rules is a
                # usable baseline: an old one would be carried over verbatim
                # (that is how a Website draft kept Mobile-Site URLs and elements).
                if rec.extra.get("platform") != platform or rec.extra.get("drafter_version") != DRAFTER_VERSION:
                    continue
                with open(rec.path, "r", encoding="utf-8") as f:
                    previous = (json.load(f) or {}).get("draft")
                break                                   # list() is newest first
    except Exception:  # noqa: BLE001 — no previous draft is fine
        previous = None

    try:
        draft = draft_testcases(text, platform=platform,
                                max_testcases=body.max_testcases,
                                provider=body.provider or None,
                                model=body.model or None, previous=previous,
                                attachments=[a.model_dump() for a in body.attachments],
                                extend_flow=body.extend_flow or None)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"Test case to extend not found: {e}") from e
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
    except RuntimeError as e:
        # A Jira ticket that could not be read (no token, wrong key…). Said
        # plainly rather than drafting from the one sentence around the link.
        raise HTTPException(status_code=424, detail=str(e)) from e

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
        "jira": draft.get("jira", []),
        "questions": draft.get("questions", []),
        "kept_ids": draft.get("kept_ids", []),
        "draft_testcases": _draft_cases(draft),
        "sheets": draft.get("sheets", []),
        "candidate_urls": draft.get("candidate_urls", []),
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
