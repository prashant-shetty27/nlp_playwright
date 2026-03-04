"""
core/recorder_security.py
Employee-auth and audit helpers for recorder sessions.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

try:
    import fcntl  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    fcntl = None

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USERS_FILE = os.path.join(BASE_DIR, "config", "authorized_users.json")
AUDIT_LOG_FILE = os.path.join(BASE_DIR, "data", "logs", "recorder_audit.jsonl")

_SESSIONS: dict[str, dict[str, Any]] = {}
_sessions_lock = threading.Lock()
_audit_lock = threading.Lock()


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_name(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _norm_emp_id(value: str) -> str:
    return "".join(ch for ch in str(value or "").strip() if ch.isdigit())


def load_authorized_users() -> list[dict[str, str]]:
    if not os.path.exists(USERS_FILE):
        return []
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        users = data.get("users", []) if isinstance(data, dict) else []
        out = []
        for row in users:
            name = str(row.get("employee_name", "")).strip()
            emp_id = _norm_emp_id(str(row.get("employee_id", "")))
            if name and emp_id:
                out.append({"employee_name": name, "employee_id": emp_id})
        return out
    except Exception:
        return []


def validate_employee(employee_name: str, employee_id: str) -> dict[str, str] | None:
    target_name = _norm_name(employee_name)
    target_id = _norm_emp_id(employee_id)
    if not target_name or not target_id:
        return None
    for row in load_authorized_users():
        if _norm_name(row["employee_name"]) == target_name and _norm_emp_id(row["employee_id"]) == target_id:
            return {"employee_name": row["employee_name"], "employee_id": row["employee_id"]}
    return None


def start_session(
    *,
    employee_name: str,
    employee_id: str,
    recorder_kind: str,
    client_id: str | None = None,
) -> dict[str, Any]:
    user = validate_employee(employee_name, employee_id)
    if not user:
        raise ValueError("Invalid employee name or employee ID")

    session = {
        "session_id": uuid.uuid4().hex,
        "employee_name": user["employee_name"],
        "employee_id": user["employee_id"],
        "recorder_kind": recorder_kind,
        "client_id": client_id or "",
        "login_at": now_iso_utc(),
        "last_seen_at": now_iso_utc(),
    }
    with _sessions_lock:
        _SESSIONS[session["session_id"]] = session

    log_action(session, "session_login", {"recorder_kind": recorder_kind})
    return session


def end_session(session: dict[str, Any] | None, reason: str = "user_logout") -> None:
    if not session:
        return
    sid = session.get("session_id")
    with _sessions_lock:
        if sid in _SESSIONS:
            _SESSIONS.pop(sid, None)
    log_action(session, "session_logout", {"reason": reason})


def touch_session(session: dict[str, Any] | None) -> None:
    if not session:
        return
    sid = session.get("session_id")
    if not sid:
        return
    with _sessions_lock:
        row = _SESSIONS.get(sid)
        if row:
            row["last_seen_at"] = now_iso_utc()


def active_sessions_snapshot() -> list[dict[str, Any]]:
    with _sessions_lock:
        return [dict(v) for v in _SESSIONS.values()]


def log_action(session: dict[str, Any] | None, action: str, details: dict[str, Any] | None = None) -> None:
    os.makedirs(os.path.dirname(AUDIT_LOG_FILE), exist_ok=True)
    entry = {
        "timestamp": now_iso_utc(),
        "action": action,
        "details": details or {},
        "session": {
            "session_id": (session or {}).get("session_id", ""),
            "employee_name": (session or {}).get("employee_name", ""),
            "employee_id": (session or {}).get("employee_id", ""),
            "recorder_kind": (session or {}).get("recorder_kind", ""),
            "client_id": (session or {}).get("client_id", ""),
        },
    }

    line = json.dumps(entry, ensure_ascii=False)
    with _audit_lock:
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
            finally:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
