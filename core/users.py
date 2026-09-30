"""
core/users.py — the people who use the portal.

Stored in data/users.json. Passwords are never kept: only a salted PBKDF2
hash. Three roles, the same split Testsigma uses in practice:

    admin   — everything, plus managing users
    editor  — write test cases / elements / suites / plans, run them
    viewer  — read everything, run nothing, change nothing

The file is small and local; a lock keeps two saves from interleaving.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone

from config.settings import DATA_DIR

USERS_FILE = os.path.join(DATA_DIR, "users.json")
ROLES = ("admin", "editor", "viewer")
_NAME_RE = re.compile(r"^[a-z][a-z0-9._-]{2,31}$")
_ITER = 200_000
_lock = threading.Lock()


class UserError(ValueError):
    """A request the store refuses, with a message fit to show a person."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict:
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, USERS_FILE)
    try:
        os.chmod(USERS_FILE, 0o600)
    except OSError:
        pass


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITER).hex()


def _public(username: str, u: dict) -> dict:
    return {"username": username, "name": u.get("name", username), "email": u.get("email", ""),
            "role": u.get("role", "viewer"), "active": u.get("active", True),
            "created_at": u.get("created_at", ""), "created_by": u.get("created_by", ""),
            "last_login": u.get("last_login", "")}


def has_users() -> bool:
    return bool(_load())


def list_users() -> list[dict]:
    return [_public(k, v) for k, v in sorted(_load().items())]


def get(username: str) -> dict | None:
    u = _load().get((username or "").strip().lower())
    return _public(username.strip().lower(), u) if u else None


def role_of(username: str) -> str:
    u = get(username or "")
    return u["role"] if u and u["active"] else ""


def owners() -> set[str]:
    """
    The portal owner(s): PORTAL_OWNERS in .env (comma-separated usernames), or
    — when that is not set — the admin who set the portal up (first created).
    """
    import os
    listed = {n.strip().lower() for n in os.getenv("PORTAL_OWNERS", "").split(",") if n.strip()}
    if listed:
        return listed
    data = _load()
    admins = sorted((v.get("created_at", ""), k) for k, v in data.items()
                    if v.get("role") == "admin")
    return {admins[0][1]} if admins else set()


def can(username: str, action: str) -> bool:
    """action: 'read' | 'write' | 'run' | 'admin' | 'restart'.

    'restart' (restart the server, which stops everyone's runs) is the OWNER's
    alone for now — not every admin. Granting it to others by role/permission
    is planned; until then it is decided by owners() only.
    """
    role = role_of(username)
    if not role:
        return False
    if action == "restart":
        return (username or "").lower() in owners()
    if action == "read":
        return True
    if action in ("write", "run"):
        return role in ("admin", "editor")
    if action == "admin":
        return role == "admin"
    return False


def _check_password(pw: str) -> None:
    if len(pw or "") < 8:
        raise UserError("Password must be at least 8 characters.")


def create(username: str, password: str, *, name: str = "", email: str = "",
           role: str = "editor", created_by: str = "", first_only: bool = False) -> dict:
    """`first_only`: refuse unless no user exists yet (the /setup route), checked
    under the same lock as the write, so two setup calls cannot both succeed."""
    username = (username or "").strip().lower()
    if not _NAME_RE.match(username):
        raise UserError("Username: 3–32 characters, lower-case letters, digits, . _ - "
                        "and it must start with a letter.")
    if role not in ROLES:
        raise UserError(f"Role must be one of {', '.join(ROLES)}.")
    _check_password(password)
    with _lock:
        data = _load()
        if first_only and data:
            raise UserError("Setup is already done — sign in instead.")
        if username in data:
            raise UserError(f"A user called '{username}' already exists.")
        if email and any(v.get("email", "").lower() == email.strip().lower() for v in data.values()):
            raise UserError(f"{email} is already used by another user.")
        if not data:
            role = "admin"                    # the first user is always an admin
        if username == "system":
            raise UserError("'system' is reserved for the portal itself.")
        salt = secrets.token_hex(16)
        data[username] = {"name": (name or username).strip(), "email": (email or "").strip(),
                          "role": role, "active": True, "salt": salt,
                          "hash": _hash(password, salt), "created_at": _now(),
                          "created_by": created_by or username}
        _save(data)
        return _public(username, data[username])


def verify(username: str, password: str) -> dict | None:
    username = (username or "").strip().lower()
    with _lock:
        data = _load()
        u = data.get(username)
        if not u or not u.get("active", True):
            return None
        if not hmac.compare_digest(_hash(password or "", u["salt"]), u["hash"]):
            return None
        u["last_login"] = _now()
        _save(data)
        return _public(username, u)


def update(username: str, *, name: str | None = None, email: str | None = None,
           role: str | None = None, active: bool | None = None,
           password: str | None = None) -> dict:
    username = (username or "").strip().lower()
    with _lock:
        data = _load()
        u = data.get(username)
        if not u:
            raise UserError(f"No user called '{username}'.")
        if role is not None:
            if role not in ROLES:
                raise UserError(f"Role must be one of {', '.join(ROLES)}.")
            if u.get("role") == "admin" and role != "admin" and _admins(data) <= 1:
                raise UserError("This is the last admin — make someone else admin first.")
            u["role"] = role
        if active is not None:
            if not active and u.get("role") == "admin" and _admins(data) <= 1:
                raise UserError("This is the last admin — it cannot be deactivated.")
            u["active"] = bool(active)
        if name is not None:
            u["name"] = name.strip() or username
        if email is not None:
            e = email.strip()
            if e and any(k != username and v.get("email", "").lower() == e.lower()
                         for k, v in data.items()):
                raise UserError(f"{e} is already used by another user.")
            u["email"] = e
        if password:
            _check_password(password)
            u["salt"] = secrets.token_hex(16)
            u["hash"] = _hash(password, u["salt"])
        _save(data)
        return _public(username, u)


def delete(username: str) -> None:
    username = (username or "").strip().lower()
    with _lock:
        data = _load()
        u = data.get(username)
        if not u:
            raise UserError(f"No user called '{username}'.")
        if u.get("role") == "admin" and _admins(data) <= 1:
            raise UserError("This is the last admin — it cannot be deleted.")
        del data[username]
        _save(data)


def _admins(data: dict) -> int:
    return sum(1 for v in data.values() if v.get("role") == "admin" and v.get("active", True))
