"""
core/audit.py — who created / last changed a test case.

Flows are plain files, so their authorship is kept beside them in
data/meta/flows.json rather than inside the .flow text.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from config.settings import DATA_DIR

META_FILE = os.path.join(DATA_DIR, "meta", "flows.json")
_lock = threading.Lock()


def _load() -> dict:
    try:
        with open(META_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (FileNotFoundError, ValueError):
        return {}


def touch(flow: str, user: str, *, created: bool = False) -> None:
    if not flow:
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _lock:
        data = _load()
        rec = data.setdefault(flow, {})
        if created or "created_at" not in rec:
            rec.setdefault("created_at", now)
            rec.setdefault("created_by", user or "system")
        rec["updated_at"] = now
        rec["updated_by"] = user or "system"
        os.makedirs(os.path.dirname(META_FILE), exist_ok=True)
        tmp = META_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, META_FILE)


def rename(old: str, new: str) -> None:
    with _lock:
        data = _load()
        if old in data:
            data[new] = data.pop(old)
            with open(META_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)


def get(flow: str) -> dict:
    return _load().get(flow, {})
