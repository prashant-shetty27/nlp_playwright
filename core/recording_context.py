"""
core/recording_context.py
Context model for strict recorder data segregation.

Each recorder session is scoped by:
  platform + project + script

Data is persisted under:
  data/contexts/<platform>/<project>/<script>/
  flows/contexts/<platform>/<project>/<script>/
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

from locators.io_utils import atomic_write_json, file_lock, read_json

PLATFORM_OPTIONS: list[dict[str, str]] = [
    {"label": "Website", "key": "website"},
    {"label": "Touch-mobile", "key": "touch_mobile"},
    {"label": "Android app", "key": "android_app"},
    {"label": "iOS app", "key": "ios_app"},
    {"label": "API", "key": "api"},
    {"label": "Hybrid", "key": "hybrid"},
]

_LABEL_TO_KEY = {row["label"]: row["key"] for row in PLATFORM_OPTIONS}
_KEY_TO_LABEL = {row["key"]: row["label"] for row in PLATFORM_OPTIONS}
_CANONICAL_BY_LOWER = {row["label"].lower(): row["label"] for row in PLATFORM_OPTIONS}


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def list_platform_labels() -> list[str]:
    return [row["label"] for row in PLATFORM_OPTIONS]


def normalize_platform_label(raw_value: str | None) -> str | None:
    if not raw_value:
        return None
    val = str(raw_value).strip()
    if not val:
        return None
    if val in _LABEL_TO_KEY:
        return val
    lowered = val.lower()
    if lowered in _CANONICAL_BY_LOWER:
        return _CANONICAL_BY_LOWER[lowered]
    if lowered in _KEY_TO_LABEL:
        return _KEY_TO_LABEL[lowered]
    return None


def platform_key(platform_label: str) -> str:
    return _LABEL_TO_KEY[platform_label]


def sanitize_name(raw: str, max_len: int = 64) -> str:
    text = (raw or "").strip().lower()
    text = re.sub(r"[\s\-]+", "_", text)
    text = re.sub(r"[^a-z0-9_]", "", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:max_len]


def validate_context(platform_label: str | None, project_name: str, script_name: str) -> list[str]:
    errors: list[str] = []
    label = normalize_platform_label(platform_label)
    if not label:
        errors.append("Select a valid platform")

    project_clean = sanitize_name(project_name)
    if len(project_clean) < 3:
        errors.append("Project name must be at least 3 valid characters")

    script_clean = sanitize_name(script_name)
    if len(script_clean) < 3:
        errors.append("Test script must be at least 3 valid characters")

    reserved = {"default", "all", "none", "tmp", "temp"}
    if project_clean in reserved:
        errors.append("Project name cannot use a reserved keyword")
    if script_clean in reserved:
        errors.append("Test script cannot use a reserved keyword")
    return errors


def build_context_paths(base_dir: str, platform_label: str, project_name: str, script_name: str) -> dict[str, str]:
    p_key = platform_key(platform_label)
    proj = sanitize_name(project_name)
    script = sanitize_name(script_name)

    data_dir = os.path.join(base_dir, "data", "contexts", p_key, proj, script)
    flow_dir = os.path.join(base_dir, "flows", "contexts", p_key, proj, script)
    return {
        "data_dir": data_dir,
        "flow_dir": flow_dir,
        "manifest_file": os.path.join(data_dir, "context_manifest.json"),
        "recorded_elements_file": os.path.join(data_dir, "recorded_elements.json"),
        "manual_locators_file": os.path.join(data_dir, "locators_manual.json"),
        "env_snapshot_file": os.path.join(data_dir, "environment_snapshot.json"),
        "runtime_variables_file": os.path.join(data_dir, "runtime_variables.json"),
        "setup_options_file": os.path.join(data_dir, "setup_options.json"),
    }


def activate_context(
    base_dir: str,
    platform_label: str | None,
    project_name: str,
    script_name: str,
    *,
    recorder_kind: str,
) -> dict[str, Any]:
    errors = validate_context(platform_label, project_name, script_name)
    if errors:
        raise ValueError("; ".join(errors))

    label = normalize_platform_label(platform_label)
    assert label is not None

    project_clean = sanitize_name(project_name)
    script_clean = sanitize_name(script_name)
    paths = build_context_paths(base_dir, label, project_clean, script_clean)

    os.makedirs(paths["data_dir"], exist_ok=True)
    os.makedirs(paths["flow_dir"], exist_ok=True)

    manifest = {
        "schema_version": 1,
        "updated_at": now_iso_utc(),
        "platform": {"label": label, "key": platform_key(label)},
        "project_name": project_clean,
        "script_name": script_clean,
        "recorder_kind": recorder_kind,
        "paths": paths,
    }

    # Preserve original creation timestamp if present.
    mf = paths["manifest_file"]
    with file_lock(mf, exclusive=True):
        existing = {}
        if os.path.exists(mf):
            try:
                existing = read_json(mf, retries=2) or {}
            except Exception:
                existing = {}
        manifest["created_at"] = existing.get("created_at") or now_iso_utc()
        manifest["last_used_at"] = now_iso_utc()
        atomic_write_json(mf, manifest, indent=2)

    return manifest


def snapshot_context_state(
    context: dict[str, Any],
    *,
    env_name: str | None = None,
    env_payload: dict[str, Any] | None = None,
    runtime_variables: dict[str, Any] | None = None,
    setup_options: dict[str, Any] | None = None,
) -> None:
    paths = context.get("paths", {})

    def _write(path: str, payload: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with file_lock(path, exclusive=True):
            atomic_write_json(path, payload, indent=2)

    if env_name is not None or env_payload is not None:
        _write(
            paths["env_snapshot_file"],
            {
                "updated_at": now_iso_utc(),
                "active_env_name": env_name or "",
                "env": env_payload or {},
            },
        )

    if runtime_variables is not None:
        _write(
            paths["runtime_variables_file"],
            {
                "updated_at": now_iso_utc(),
                "variables": runtime_variables,
            },
        )

    if setup_options is not None:
        _write(
            paths["setup_options_file"],
            {
                "updated_at": now_iso_utc(),
                "options": setup_options,
            },
        )


def get_path(context: dict[str, Any] | None, key: str, default: str) -> str:
    if not context:
        return default
    return context.get("paths", {}).get(key, default)


def file_mtime(path: str) -> float | None:
    try:
        return os.path.getmtime(path) if os.path.exists(path) else None
    except OSError:
        return None
