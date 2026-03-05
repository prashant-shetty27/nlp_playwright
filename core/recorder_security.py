"""
core/recorder_security.py
User auth and audit helpers for recorder sessions.
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
AUDIT_LOG_FILE = os.path.join(BASE_DIR, "data", "logs", "recorder_audit.jsonl")
USER_PREFS_FILE = os.path.join(BASE_DIR, "data", "logs", "recorder_user.json")

_SESSIONS: dict[str, dict[str, Any]] = {}
_sessions_lock = threading.Lock()
_audit_lock = threading.Lock()


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── User name persistence ──────────────────────────────────────────────────────

def load_saved_user_name() -> str:
    """Return the last saved user name, or empty string."""
    try:
        with open(USER_PREFS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("user_name", "")
    except Exception:
        return ""


def save_user_name(user_name: str) -> None:
    """Persist the user name locally."""
    os.makedirs(os.path.dirname(USER_PREFS_FILE), exist_ok=True)
    with open(USER_PREFS_FILE, "w", encoding="utf-8") as f:
        json.dump({"user_name": user_name}, f)


# ── Session management ─────────────────────────────────────────────────────────

def start_session(
    *,
    user_name: str,
    recorder_kind: str,
    client_id: str | None = None,
) -> dict[str, Any]:
    name = (user_name or "").strip() or "guest"
    session = {
        "session_id": uuid.uuid4().hex,
        "user_name": name,
        "recorder_kind": recorder_kind,
        "client_id": client_id or "",
        "login_at": now_iso_utc(),
        "last_seen_at": now_iso_utc(),
    }
    with _sessions_lock:
        _SESSIONS[session["session_id"]] = session
    return session


def end_session(session: dict[str, Any] | None, reason: str = "user_logout") -> None:
    if not session:
        return
    sid = session.get("session_id")
    with _sessions_lock:
        _SESSIONS.pop(sid, None)


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


# ── Audit logging ──────────────────────────────────────────────────────────────

def log_action(
    session: dict[str, Any] | None,
    action: str,
    details: dict[str, Any] | None = None,
) -> None:
    """
    Append one line to recorder_audit.jsonl with the structured log format:
      timestamp, user_name, app_type, total_recorded_steps, status, file_name, action, details
    """
    d = details or {}
    entry = {
        "timestamp":             now_iso_utc(),
        "user_name":             (session or {}).get("user_name", "guest"),
        "app_type":              d.get("app_type", (session or {}).get("recorder_kind", "")),
        "total_recorded_steps":  d.get("total_recorded_steps", None),
        "status":                d.get("status", None),          # "pass" | "fail" | None
        "file_name":             d.get("file_name", ""),
        "action":                action,
        "details":               {k: v for k, v in d.items()
                                  if k not in {"app_type", "total_recorded_steps", "status", "file_name"}},
    }
    _write_audit_line(entry)


def _write_audit_line(entry: dict) -> None:
    os.makedirs(os.path.dirname(AUDIT_LOG_FILE), exist_ok=True)
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
