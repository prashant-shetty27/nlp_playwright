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
