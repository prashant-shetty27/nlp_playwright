"""
api/auth.py — who is making this request, and may they.

The UI calls the API in-process and names the signed-in person in the
`X-User` header. Scripts and CI that call the API directly send no header
and are treated as the system (they run on this machine already).
"""
from __future__ import annotations

from fastapi import Header, HTTPException

from core import users


def acting_user(x_user: str = Header(default="")) -> str:
    return (x_user or "").strip().lower()


def require(user: str, action: str) -> None:
    """Refuse when a signed-in user lacks the permission. No header = system."""
    if not user:
        return
    if not users.can(user, action):
        role = users.role_of(user) or "no access"
        raise HTTPException(
            status_code=403,
            detail=f"'{user}' ({role}) cannot {action} here — ask an admin for the editor role.")
