"""
api/routes/testsigma.py — reading test cases out of Testsigma (admin only).

GET /testsigma/probe?path=/api/v1/…   raw read-only GET (for mapping the API)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.auth import acting_user, require

router = APIRouter(prefix="/testsigma", tags=["testsigma"])


@router.get("/probe")
def probe(path: str, user: str = Depends(acting_user)):
    require(user, "admin")
    from tools.testsigma_api import TestsigmaError, get
    import json
    import os
    import re
    import time
    from config.settings import DATA_DIR
    try:
        data = get(path)
    except TestsigmaError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    # Every probe is also written to disk (data/testsigma_exports/_probe/), so a
    # large response can be analysed without shipping it through the page.
    out_dir = os.path.join(DATA_DIR, "testsigma_exports", "_probe")
    os.makedirs(out_dir, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9]+", "_", path)[:120].strip("_") + ".json"
    if isinstance(data, str):              # XML reports are kept as XML
        name = name[:-5] + ".xml"
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(data)
        return {"path": path, "saved": name, "data": data[:5000]}
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
        json.dump({"path": path, "at": time.time(), "data": data}, f, indent=1, ensure_ascii=False)
    return {"path": path, "saved": name, "data": data}


# ── pull + import ────────────────────────────────────────────────────────────
from pydantic import BaseModel  # noqa: E402


class PullBody(BaseModel):
    run_id: int


class ImportBody(BaseModel):
    test_case_ids: list[int]
    overwrite: bool = False
    platform: str = "mobilesite"


def module_folder(name: str) -> str:
    """Folder an imported test case goes into: <root>/Core/NCT or <root>/B2B/<module>.
    TESTSIGMA_FOLDER_ROOT in .env changes the root (default Prashant)."""
    import os
    root = os.environ.get("TESTSIGMA_FOLDER_ROOT", "Prashant").strip() or "Prashant"
    mod = _module(name)
    return f"{root}/Core/{mod}" if mod == "NCT" else f"{root}/B2B/{mod}"


def _module(name: str) -> str:
    n = (name or "").strip().lower()
    for key, label in (("nct", "NCT"), ("prp", "PRP"), ("pdp", "PDP"), ("catalogue", "Catalogue"),
                       ("rfq", "RFQ")):
        if n.startswith(key):
            return label
    return "Other"


@router.post("/pull")
def pull(body: PullBody, user: str = Depends(acting_user)):
    require(user, "admin")
    from tools import testsigma_pull as P
    return P.start_pull(str(body.run_id))


@router.get("/pull/{run_id}")
def pull_status(run_id: int, user: str = Depends(acting_user)):
    require(user, "admin")
    import os
    from tools import testsigma_pull as P
    rows = []
    for c in P.cases(str(run_id)):
        flow = os.path.join(P.BASE_DIR, "flows", P.slug(c["name"]) + ".flow")
        rows.append({**c, "module": _module(c["name"]), "imported": os.path.exists(flow),
                     "flow": P.slug(c["name"])})
    return {"status": P.status(str(run_id)), "cases": rows}


@router.get("/runs/{run_id}/cases/{test_case_id}/preview")
def preview(run_id: int, test_case_id: int, platform: str = "mobilesite", user: str = Depends(acting_user)):
    require(user, "admin")
    from tools import testsigma_pull as P
    try:
        res = P.convert(P.load_bundle(str(run_id), test_case_id), platform=platform)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {**res, "elements": {k: v for k, v in res["elements"].items()}}


@router.post("/runs/{run_id}/import")
def import_cases(run_id: int, body: ImportBody, user: str = Depends(acting_user)):
    require(user, "admin")
    from tools import testsigma_pull as P
    out = []
    from core import folders
    names = {int(c["test_case_id"]): c["name"] for c in P.cases(str(run_id))}
    for tc in body.test_case_ids:
        try:
            res = P.import_case(str(run_id), tc, platform=body.platform, overwrite=body.overwrite)
            # File it by module (Prashant/Core/NCT, Prashant/B2B/PDP …) unless it
            # already sits in a folder someone chose.
            if res.get("status") == "imported" and not folders.folder_of(res["name"]):
                folders.assign([res["name"]], module_folder(names.get(int(tc), res["name"])))
            out.append({"test_case_id": tc, **res})
        except Exception as e:  # noqa: BLE001 — one bad case must not stop the batch
            out.append({"test_case_id": tc, "status": "error", "detail": f"{type(e).__name__}: {e}"[:300]})
    return {"results": out}
