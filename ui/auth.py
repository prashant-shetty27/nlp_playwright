"""
ui/auth.py — the signed-in person, and the gate in front of every page.

The session lives in NiceGUI's per-browser user storage (a signed cookie),
so a sign-in survives reloads and server restarts.
"""
from __future__ import annotations

from urllib.parse import quote

from nicegui import app, ui

from core import users


def current() -> dict | None:
    try:
        name = app.storage.user.get("username", "")
    except Exception:  # noqa: BLE001 — no browser context
        return None
    u = users.get(name) if name else None
    return u if u and u.get("active") else None


def role() -> str:
    u = current()
    return u["role"] if u else ""


def can(action: str) -> bool:
    u = current()
    return bool(u) and users.can(u["username"], action)


def ensure_login() -> bool:
    """True when someone is signed in; otherwise send the browser to /login."""
    if current():
        return True
    path = "/"
    try:
        req = ui.context.client.request
        path = req.url.path + (("?" + req.url.query) if req.url.query else "")
    except Exception:  # noqa: BLE001
        pass
    ui.navigate.to(f"/login?next={quote(path, safe='')}")
    return False


def sign_in(u: dict) -> None:
    app.storage.user["username"] = u["username"]


def sign_out() -> None:
    try:
        app.storage.user.pop("username", None)
    except Exception:  # noqa: BLE001
        pass
    ui.navigate.to("/login")
