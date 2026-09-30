"""
api/routes/folders.py — folders for test cases (see core/folders.py).

GET  /folders                — {"folders": [...paths], "assign": {test: path}}
POST /folders                — {"path": "Prashant/B2B/PDP"}  create (parents too)
POST /folders/rename         — {"path": "...", "new_name": "..."}
POST /folders/delete         — {"path": "..."}  test cases move to the parent
POST /folders/assign         — {"tests": [...], "folder": "..."}  ('' = Unfiled)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core import folders

router = APIRouter(prefix="/folders", tags=["folders"])


class PathBody(BaseModel):
    path: str


class RenameBody(BaseModel):
    path: str
    new_name: str


class AssignBody(BaseModel):
    tests: list[str]
    folder: str = ""


def _run(fn, *a):
    try:
        return fn(*a)
    except folders.FolderError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("")
def list_folders():
    return folders.get()


@router.post("", status_code=201)
def create_folder(body: PathBody):
    return {"path": _run(folders.create, body.path)}


@router.post("/rename")
def rename_folder(body: RenameBody):
    return {"path": _run(folders.rename, body.path, body.new_name)}


class MoveBody(BaseModel):
    path: str
    new_parent: str = ""


@router.post("/move")
def move_folder(body: MoveBody):
    return {"path": _run(folders.move, body.path, body.new_parent)}


@router.post("/delete")
def delete_folder(body: PathBody):
    return _run(folders.delete, body.path)


@router.post("/assign")
def assign_tests(body: AssignBody):
    return _run(folders.assign, body.tests, body.folder)
