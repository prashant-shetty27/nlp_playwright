"""
core/folders.py — folders for test cases (Testsigma-style tree).

Test cases stay where they are (flows/<name>.flow) — every runner, suite and
plan finds them by name, so moving files into sub-directories would break all
of them. A folder is only a label kept beside them in
data/test_case_folders.json:

    {"folders": ["Prashant", "Prashant/B2B", "Prashant/B2B/PDP", ...],
     "assign":  {"PDP_Change_City_Implementation": "Prashant/B2B/PDP", ...}}

A path is "Parent/Child/Grandchild". A test case sits in at most one folder;
one with no folder is shown under "Unfiled".
"""
from __future__ import annotations

import json
import os
import re
import threading

from config.settings import DATA_DIR

FOLDERS_FILE = os.path.join(DATA_DIR, "test_case_folders.json")
_lock = threading.Lock()
_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.&()-]{0,39}$")


class FolderError(ValueError):
    pass


def _load() -> dict:
    try:
        with open(FOLDERS_FILE, encoding="utf-8") as f:
            d = json.load(f) or {}
    except (FileNotFoundError, ValueError):
        d = {}
    d.setdefault("folders", [])
    d.setdefault("assign", {})
    return d


def _save(d: dict) -> None:
    d["folders"] = sorted(set(d["folders"]), key=str.lower)
    d["assign"] = dict(sorted(d["assign"].items()))
    os.makedirs(os.path.dirname(FOLDERS_FILE), exist_ok=True)
    tmp = FOLDERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, FOLDERS_FILE)


def clean_path(path: str) -> str:
    """'  prashant / B2B/ pdp ' -> 'prashant/B2B/pdp'; each part checked."""
    parts = [p.strip() for p in str(path or "").replace("\\", "/").split("/") if p.strip()]
    if not parts:
        raise FolderError("Give the folder a name.")
    if len(parts) > 5:
        raise FolderError("Folders go at most 5 levels deep.")
    for p in parts:
        if not _PART.match(p):
            raise FolderError(f"'{p}': use letters, digits, spaces, - _ . & ( ) — up to 40 characters, "
                              f"starting with a letter or digit.")
    return "/".join(parts)


def _with_parents(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]


def _find(folders: list[str], path: str) -> str | None:
    """The existing spelling of a path, matched without regard to case."""
    low = path.lower()
    return next((f for f in folders if f.lower() == low), None)


def get() -> dict:
    return _load()


def visible(module: str) -> dict:
    """
    The tree as one module sees it. Whatever is created in a module stays in it:
    a folder shows in a module when it was created there, holds that module's
    test cases (here or below), or is an empty, untagged folder (shared).
    Parents of a shown folder are shown so the tree stays whole.
    """
    d = _load()
    if not module:
        return d
    try:
        from api.routes.projects import _platform_of
    except Exception:  # noqa: BLE001
        return d
    mods = d.get("modules") or {}
    plat = {t: _platform_of(t) for t in d["assign"]}

    def under(f: str, t_folder: str) -> bool:
        return t_folder == f or t_folder.startswith(f + "/")
    keep: set[str] = set()
    for f in d["folders"]:
        mine = any(under(f, tf) and plat.get(t) == module for t, tf in d["assign"].items())
        anyone = any(under(f, tf) for tf in d["assign"].values())
        tags = mods.get(f) or []
        if mine or module in tags or (not tags and not anyone):
            keep.update(_with_parents(f))
    folders_ = [f for f in d["folders"] if f in keep]
    return {**d, "folders": folders_,
            "assign": {t: f for t, f in d["assign"].items() if plat.get(t) == module}}


def create(path: str, module: str = "") -> str:
    path = clean_path(path)
    with _lock:
        d = _load()
        out = []
        for p in _with_parents(path):
            same = _find(d["folders"], p)
            # Reuse the existing spelling of a parent ("prashant" -> "Prashant").
            if same:
                out.append(same.split("/")[-1])
            else:
                out.append(p.split("/")[-1])
            full = "/".join(out)
            if not _find(d["folders"], full):
                d["folders"].append(full)
                if module:                       # belongs to the module it was created in
                    d.setdefault("modules", {})[full] = [module]
            elif module:
                tags = d.setdefault("modules", {}).setdefault(full, [])
                if tags and module not in tags:
                    tags.append(module)
        _save(d)
        return "/".join(out)


def rename(old: str, new_name: str) -> str:
    """Rename the LAST part of a folder; its sub-folders and test cases follow."""
    with _lock:
        d = _load()
        old = _find(d["folders"], clean_path(old)) or ""
        if not old:
            raise FolderError("That folder does not exist.")
        part = clean_path(new_name)
        if "/" in part:
            raise FolderError("A new name is one part — use Move for another parent.")
        new = "/".join(old.split("/")[:-1] + [part])
        if new.lower() != old.lower() and _find(d["folders"], new):
            raise FolderError(f"'{new}' already exists.")

        def swap(p: str) -> str:
            return new + p[len(old):] if p == old or p.startswith(old + "/") else p
        d["folders"] = [swap(p) for p in d["folders"]]
        d["assign"] = {t: swap(p) for t, p in d["assign"].items()}
        d["modules"] = {swap(p): m for p, m in (d.get("modules") or {}).items()}
        _save(d)
        return new


def move(path: str, new_parent: str) -> str:
    """Move a folder (with its sub-folders and test cases) under another folder
    ('' = top level). Returns the new path."""
    with _lock:
        d = _load()
        old = _find(d["folders"], clean_path(path)) or ""
        if not old:
            raise FolderError("That folder does not exist.")
        parent = ""
        if str(new_parent or "").strip():
            parent = _find(d["folders"], clean_path(new_parent)) or ""
            if not parent:
                raise FolderError(f"'{new_parent}' does not exist.")
        if parent == old or parent.startswith(old + "/"):
            raise FolderError("A folder cannot be moved inside itself.")
        name = old.split("/")[-1]
        new = f"{parent}/{name}" if parent else name
        if new == old:
            return old
        if _find(d["folders"], new):
            raise FolderError(f"'{new}' already exists — rename one of them first.")
        deepest = max(p.count("/") - old.count("/") for p in d["folders"]
                      if p == old or p.startswith(old + "/"))
        if new.count("/") + deepest + 1 > 5:
            raise FolderError("That would make folders more than 5 levels deep.")

        def swap(p: str) -> str:
            return new + p[len(old):] if p == old or p.startswith(old + "/") else p
        d["folders"] = [swap(p) for p in d["folders"]]
        d["assign"] = {t: swap(p) for t, p in d["assign"].items()}
        d["modules"] = {swap(p): m for p, m in (d.get("modules") or {}).items()}
        _save(d)
        return new


def delete(path: str) -> dict:
    """Delete a folder and its sub-folders. Their test cases are NOT deleted —
    they move to the parent folder (or Unfiled)."""
    with _lock:
        d = _load()
        path = _find(d["folders"], clean_path(path)) or ""
        if not path:
            raise FolderError("That folder does not exist.")
        parent = "/".join(path.split("/")[:-1])
        gone = [p for p in d["folders"] if p == path or p.startswith(path + "/")]
        d["folders"] = [p for p in d["folders"] if p not in gone]
        d["modules"] = {p: m for p, m in (d.get("modules") or {}).items() if p not in gone}
        moved = 0
        for t, p in list(d["assign"].items()):
            if p in gone:
                moved += 1
                if parent:
                    d["assign"][t] = parent
                else:
                    del d["assign"][t]
        _save(d)
        return {"deleted": gone, "moved_tests": moved, "to": parent or "Unfiled"}


def assign(tests: list[str], folder: str, module: str = "") -> dict:
    """Put test cases into a folder ('' = Unfiled). The folder is created if needed."""
    folder = create(folder, module) if str(folder or "").strip() else ""
    with _lock:
        d = _load()
        for t in tests:
            if not t:
                continue
            if folder:
                d["assign"][t] = folder
            else:
                d["assign"].pop(t, None)
        _save(d)
    return {"folder": folder or "Unfiled", "tests": len(tests)}


def folder_of(test: str) -> str:
    return _load()["assign"].get(test, "")


def on_rename(old: str, new: str) -> None:
    """A test case was renamed: keep it in its folder."""
    with _lock:
        d = _load()
        if old in d["assign"]:
            d["assign"][new] = d["assign"].pop(old)
            _save(d)


def on_delete(test: str) -> None:
    with _lock:
        d = _load()
        if d["assign"].pop(test, None) is not None:
            _save(d)
