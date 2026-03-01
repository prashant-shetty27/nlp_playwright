"""
config/execution_preferences.py
Runtime execution preference profiles for local/cloud + feature toggles.

Profiles are stored in config/execution_preferences.json and can be:
- created/updated (save)
- loaded and applied
- listed
- deleted

This module applies preferences directly to config.settings at runtime so all
modules that import `config.settings` use the same active values.
"""

from __future__ import annotations

import json
import os
from typing import Any

from config import settings

PREFERENCES_FILE = os.path.join(settings.CONFIG_DIR, "execution_preferences.json")

_ALLOWED_EXEC_TARGETS = {"local", "cloud"}


def _to_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    return str(val).strip().lower() in {"1", "true", "yes", "y", "on"}


def current_preferences() -> dict:
    return {
        "execution_target": settings.EXECUTION_TARGET,
        "rerun_on_failure": _to_bool(getattr(settings, "RERUN_ON_FAILURE", False), False),
        "report_enabled": _to_bool(getattr(settings, "ENABLE_REPORTING", False), False),
        "screenshots_enabled": settings.ENABLE_SCREENSHOTS,
        "video_enabled": settings.ENABLE_VIDEO_RECORDING,
        "slack_enabled": settings.NOTIFY_ON_SLACK,
        "email_enabled": settings.NOTIFY_ON_EMAIL,
        "headless": settings.HEADLESS,
    }


def _normalize(prefs: dict | None) -> dict:
    p = dict(prefs or {})

    execution_target = str(p.get("execution_target", settings.EXECUTION_TARGET)).strip().lower() or "local"
    if execution_target not in _ALLOWED_EXEC_TARGETS:
        execution_target = "local"

    return {
        "execution_target": execution_target,
        "rerun_on_failure": _to_bool(p.get("rerun_on_failure", False), False),
        "report_enabled": _to_bool(p.get("report_enabled", False), False),
        "screenshots_enabled": _to_bool(p.get("screenshots_enabled", False), False),
        "video_enabled": _to_bool(p.get("video_enabled", False), False),
        "slack_enabled": _to_bool(p.get("slack_enabled", False), False),
        "email_enabled": _to_bool(p.get("email_enabled", False), False),
        "headless": _to_bool(p.get("headless", False), False),
    }


def _default_store() -> dict:
    return {
        "last_used_profile": "",
        "profiles": {},
    }


def load_store() -> dict:
    if not os.path.exists(PREFERENCES_FILE):
        return _default_store()

    try:
        with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return _default_store()
            data.setdefault("last_used_profile", "")
            data.setdefault("profiles", {})
            if not isinstance(data["profiles"], dict):
                data["profiles"] = {}
            return data
    except Exception:
        return _default_store()


def save_store(data: dict) -> None:
    os.makedirs(os.path.dirname(PREFERENCES_FILE), exist_ok=True)
    with open(PREFERENCES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def list_profiles() -> list[str]:
    store = load_store()
    return sorted(store.get("profiles", {}).keys())


def get_profile(name: str) -> dict | None:
    if not name:
        return None
    store = load_store()
    raw = store.get("profiles", {}).get(name)
    if not isinstance(raw, dict):
        return None
    return _normalize(raw)


def get_last_used_profile_name() -> str:
    store = load_store()
    return str(store.get("last_used_profile", "") or "").strip()


def save_profile(name: str, prefs: dict, set_as_last_used: bool = True) -> None:
    if not name or not name.strip():
        raise ValueError("Profile name is required")

    safe_name = name.strip()
    store = load_store()
    store["profiles"][safe_name] = _normalize(prefs)
    if set_as_last_used:
        store["last_used_profile"] = safe_name
    save_store(store)


def delete_profile(name: str) -> bool:
    store = load_store()
    profiles = store.get("profiles", {})
    if name not in profiles:
        return False
    profiles.pop(name, None)
    if store.get("last_used_profile") == name:
        store["last_used_profile"] = ""
    save_store(store)
    return True


def apply_preferences(prefs: dict) -> dict:
    p = _normalize(prefs)

    # Update settings module globals so all imports see latest values.
    settings.EXECUTION_TARGET = p["execution_target"]
    settings.RUN_ON_CLOUD = settings.EXECUTION_TARGET == "cloud"

    settings.RERUN_ON_FAILURE = p["rerun_on_failure"]
    settings.ENABLE_REPORTING = p["report_enabled"]
    settings.ENABLE_SCREENSHOTS = p["screenshots_enabled"]
    settings.ENABLE_VIDEO_RECORDING = p["video_enabled"]
    settings.NOTIFY_ON_SLACK = p["slack_enabled"]
    settings.NOTIFY_ON_EMAIL = p["email_enabled"]
    settings.HEADLESS = p["headless"]

    # Keep env in sync for any subprocess/tooling paths.
    os.environ["EXECUTION_TARGET"] = settings.EXECUTION_TARGET
    os.environ["RERUN_ON_FAILURE"] = "true" if settings.RERUN_ON_FAILURE else "false"
    os.environ["ENABLE_REPORTING"] = "true" if settings.ENABLE_REPORTING else "false"
    os.environ["ENABLE_SCREENSHOTS"] = "true" if settings.ENABLE_SCREENSHOTS else "false"
    os.environ["ENABLE_VIDEO_RECORDING"] = "true" if settings.ENABLE_VIDEO_RECORDING else "false"
    os.environ["NOTIFY_ON_SLACK"] = "true" if settings.NOTIFY_ON_SLACK else "false"
    os.environ["NOTIFY_ON_EMAIL"] = "true" if settings.NOTIFY_ON_EMAIL else "false"
    os.environ["HEADLESS"] = "true" if settings.HEADLESS else "false"

    return p


def _prompt_bool(label: str, default: bool) -> bool:
    current = "Y" if default else "N"
    raw = input(f"{label} [y/n] (default {current}): ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes", "1", "true", "on"}


def prompt_preferences(defaults: dict | None = None) -> dict:
    base = _normalize(defaults or current_preferences())

    raw_target = input(
        f"Execution target [local/cloud] (default {base['execution_target']}): "
    ).strip().lower()
    if raw_target not in _ALLOWED_EXEC_TARGETS:
        raw_target = base["execution_target"]

    return {
        "execution_target": raw_target,
        "rerun_on_failure": _prompt_bool("Rerun suite on failure", base["rerun_on_failure"]),
        "report_enabled": _prompt_bool("Enable report generation", base["report_enabled"]),
        "screenshots_enabled": _prompt_bool("Enable screenshots", base["screenshots_enabled"]),
        "video_enabled": _prompt_bool("Enable video recording", base["video_enabled"]),
        "slack_enabled": _prompt_bool("Enable Slack notifications", base["slack_enabled"]),
        "email_enabled": _prompt_bool("Enable Email notifications", base["email_enabled"]),
        "headless": _prompt_bool("Run browser headless", base["headless"]),
    }
