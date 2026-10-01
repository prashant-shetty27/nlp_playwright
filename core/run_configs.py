"""
core/run_configs.py — saved run configurations ("presets") for one-click runs.

A configuration is HOW to run, never WHAT values to use: headless, device,
browser identity, environment (server), screenshots, stop-at-first-failure,
staging login, browser permissions, video. Test values stay in Test Data.

Each belongs to one module (website, mobilesite …) and to the person who saved
it. It is personal unless shared — a shared one is offered to everyone, but
only its owner (or an admin) can change or delete it.

    data/run_configs.json  {"configs": [{id, name, module, owner, shared,
                                          settings: {...}, created_at, updated_at}]}
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone

from config.settings import DATA_DIR

PATH = os.path.join(DATA_DIR, "run_configs.json")
_LOCK = threading.Lock()

#: The settings a configuration may carry — everything else is dropped.
FIELDS = {
    "headless": bool, "device_name": str, "browser": str, "browser_identity": str,
    "site_env": str, "http_auth_domain": str, "browser_permissions": str,
    "screenshot_mode": str, "screenshot_context": int, "stop_on_failure": bool,
    "record_video": bool,
}


class RunConfigError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict:
    try:
        with open(PATH, encoding="utf-8") as f:
            d = json.load(f) or {}
    except (OSError, ValueError):
        d = {}
    d.setdefault("configs", [])
    return d


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, PATH)


def clean_settings(settings: dict) -> dict:
    out = {}
    for k, typ in FIELDS.items():
        if k in (settings or {}) and settings[k] is not None:
            try:
                out[k] = typ(settings[k])
            except (TypeError, ValueError):
                continue
    return out


def describe(c: dict) -> str:
    """'Live · Pixel 7 · headless · failures only' — what it will do, in a line."""
    s = c.get("settings") or {}
    bits = [s.get("site_env") or "default server"]
    if s.get("browser_identity"):
        bits.append(s["browser_identity"].replace("_", " "))
    elif s.get("device_name"):
        bits.append(s["device_name"])
    bits.append("headless" if s.get("headless") else "visible browser")
    bits.append({"all": "every screenshot", "key": "key screenshots",
                 "failure": "failure screenshots"}.get(s.get("screenshot_mode", "all"), ""))
    if s.get("stop_on_failure"):
        bits.append("stops at first failure")
    if s.get("record_video"):
        bits.append("video")
    return " · ".join(b for b in bits if b)


def visible(module: str, user: str) -> list[dict]:
    """This module's configurations the user may use: their own + shared ones."""
    out = [c for c in _load()["configs"]
           if c.get("module") == module and (c.get("owner") == user or c.get("shared"))]
    out.sort(key=lambda c: (c.get("owner") != user, c.get("name", "").lower()))
    return [{**c, "summary": describe(c), "mine": c.get("owner") == user} for c in out]


def get(cid: str) -> dict:
    c = next((c for c in _load()["configs"] if c.get("id") == cid), None)
    if not c:
        raise RunConfigError("That saved configuration no longer exists.")
    return c


def save(name: str, module: str, user: str, settings: dict, shared: bool = False,
         cid: str = "", is_admin: bool = False) -> dict:
    name = re.sub(r"\s+", " ", (name or "").strip())[:60]
    if not name:
        raise RunConfigError("Give the configuration a name.")
    if not module:
        raise RunConfigError("A configuration belongs to a module.")
    with _LOCK:
        d = _load()
        if cid:
            c = next((c for c in d["configs"] if c.get("id") == cid), None)
            if not c:
                raise RunConfigError("That saved configuration no longer exists.")
            if c.get("owner") != user and not is_admin:
                raise RunConfigError(f"Only {c.get('owner')} can change '{c.get('name')}'. "
                                     f"Save yours under a new name.")
        else:
            clash = next((c for c in d["configs"] if c.get("module") == module
                          and c.get("owner") == user and c.get("name", "").lower() == name.lower()), None)
            c = clash
            if c is None:
                c = {"id": uuid.uuid4().hex[:10], "owner": user, "module": module,
                     "created_at": _now()}
                d["configs"].append(c)
        c.update(name=name, shared=bool(shared), settings=clean_settings(settings),
                 updated_at=_now())
        _save(d)
        return {**c, "summary": describe(c), "mine": c.get("owner") == user}


def delete(cid: str, user: str, is_admin: bool = False) -> None:
    with _LOCK:
        d = _load()
        c = next((c for c in d["configs"] if c.get("id") == cid), None)
        if not c:
            return
        if c.get("owner") != user and not is_admin:
            raise RunConfigError(f"Only {c.get('owner')} can delete '{c.get('name')}'.")
        d["configs"] = [x for x in d["configs"] if x.get("id") != cid]
        _save(d)
