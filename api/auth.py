"""
api/auth.py — who is making this request, and may they.

The portal UI calls the API in-process. Every such call carries an internal
token that exists only inside this process (generated at start-up, never
written anywhere) plus the signed-in person in `X-User`. Only a request with
that token may name a user, so a browser tab or another program on the Mac
can no longer act as an admin by sending `X-User: <admin>`.

Calls from outside the portal:
  * read-only GETs keep working (reports, PDFs from Slack links, health);
  * anything that changes something needs `Authorization: Bearer <API_TOKEN>`
    with API_TOKEN set in .env (scripts / CI) — otherwise 401.
"""
from __future__ import annotations

import hmac
import os
import secrets

from fastapi import Header, HTTPException, Request

from core import users

#: Shared by the UI client and the API because they are the same process.
INTERNAL_TOKEN = secrets.token_hex(32)
SYSTEM = "system"


def _api_token() -> str:
    try:
        import config.settings  # noqa: F401 — loads .env
    except Exception:  # noqa: BLE001
        pass
    return (os.getenv("API_TOKEN") or "").strip()


def acting_user(request: Request,
                x_user: str = Header(default=""),
                x_internal_token: str = Header(default=""),
                authorization: str = Header(default="")) -> str:
    """The person (or 'system') this request acts as; '' when unauthenticated."""
    trusted = bool(x_internal_token) and hmac.compare_digest(x_internal_token, INTERNAL_TOKEN)
    # The old "client host == testclient" shortcut for the in-process tests is
    # gone: uvicorn's proxy-headers middleware trusts X-Forwarded-For from
    # 127.0.0.1, so any local process could claim to be "testclient" and act
    # as an admin. Tests send the internal token instead (tests/conftest.py).
    if trusted:
        return (x_user or "").strip().lower() or SYSTEM
    token = _api_token()
    if token and authorization.lower().startswith("bearer ") and \
            hmac.compare_digest(authorization[7:].strip(), token):
        return SYSTEM
    return ""


def require(user: str, action: str) -> None:
    """Refuse unless the caller may do `action` (read / write / run / admin)."""
    if user == SYSTEM:
        return
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Sign in to the portal to do this (direct API calls need API_TOKEN).")
    if not users.can(user, action):
        role = users.role_of(user) or "no access"
        raise HTTPException(
            status_code=403,
            detail=f"'{user}' ({role}) cannot {action} here — ask an admin for the editor role.")


def need(action: str, *, reads: str = ""):
    """
    Router dependency: every non-GET request on the router needs `action`.

    `reads`: an action GET requests need too — for routers whose reads
    return values that must not be open to any local process (test data
    holds mobile numbers and OTPs). As in Testsigma, there is no anonymous
    read: the portal (internal token) or a script with API_TOKEN.
    """
    from fastapi import Depends

    def _dep(request: Request, user: str = Depends(acting_user)) -> None:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            if reads:
                require(user, reads)
            return
        require(user, action)
    return _dep
