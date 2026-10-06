"""
core/suites.py — Test Suites: an ordered set of test cases on one platform.

Files live in suites/<id>.json, the same folder and the same core fields the
original plan_runner used (suite_name, description, platform, scripts), so
older suites still list and still run. New fields: created/updated by/at.

Rules enforced here, not in the UI, so every caller gets them:
  * suite names are unique (case-insensitive);
  * a test case appears at most once in a suite;
  * every test case must exist and declare the suite's platform;
  * a suite a plan still uses cannot be deleted.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone

from config.settings import BASE_DIR, FLOWS_DIR, SUITES_DIR, PLANS_DIR

_lock = threading.Lock()


class SuiteError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", (name or "").strip()).strip("_").lower()
    return s[:60] or "suite"


def flow_platform(flow: str) -> str:
    """The `# Platform:` a test case declares ('' when it declares none)."""
    path = os.path.join(FLOWS_DIR, f"{flow}.flow")
    try:
        with open(path, "r", encoding="utf-8") as f:
            for _ in range(6):
                ln = f.readline()
                m = re.match(r"^#\s*Platform\s*:\s*(\S+)", ln, re.I)
                if m:
                    return m.group(1).strip().lower()
    except FileNotFoundError:
        return ""
    return ""


def flow_exists(flow: str) -> bool:
    return os.path.exists(os.path.join(FLOWS_DIR, f"{flow}.flow"))


def _script_to_case(script: str) -> str:
    base = os.path.basename(script or "")
    return base[:-5] if base.endswith(".flow") else base


def script_path(script: str) -> str:
    """Absolute path of a suite entry (bare name or 'flows/x.flow' or legacy path)."""
    if os.path.isabs(script):
        return script
    if "/" in script:
        p = os.path.join(BASE_DIR, script)
        if os.path.exists(p):
            return p
    return os.path.join(FLOWS_DIR, f"{_script_to_case(script)}.flow")


def _path(suite_id: str) -> str:
    return os.path.join(SUITES_DIR, f"{os.path.basename(suite_id)}.json")


def _read(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _view(suite_id: str, d: dict) -> dict:
    scripts = d.get("scripts") or []
    cases = []
    for sc in scripts:
        name = _script_to_case(sc)
        p = script_path(sc)
        cases.append({"name": name, "script": sc, "exists": os.path.exists(p),
                      "platform": flow_platform(name) if p.startswith(FLOWS_DIR) else ""})
    return {"id": suite_id, "name": d.get("suite_name") or suite_id,
            "description": d.get("description", ""), "platform": d.get("platform", ""),
            "test_cases": cases, "count": len(cases),
            "disabled": list(d.get("disabled") or []),
            "execution": d.get("execution", {}), "legacy": d.get("_format") != 2,
            "created_by": d.get("created_by", ""), "created_at": d.get("created_at", ""),
            "updated_by": d.get("updated_by", ""), "updated_at": d.get("updated_at", "")}


def list_suites(platform: str = "") -> list[dict]:
    out = []
    os.makedirs(SUITES_DIR, exist_ok=True)
    for fn in sorted(os.listdir(SUITES_DIR)):
        if not fn.endswith(".json") or fn.startswith("_"):
            continue
        try:
            v = _view(fn[:-5], _read(os.path.join(SUITES_DIR, fn)))
        except (ValueError, OSError):
            continue
        if platform and v["platform"] and v["platform"] not in (platform, "web" if platform == "website" else platform):
            continue
        out.append(v)
    return out


def get(suite_id: str) -> dict:
    p = _path(suite_id)
    if not os.path.exists(p):
        raise SuiteError(f"No suite '{suite_id}'.")
    return _view(suite_id, _read(p))


def _validate(name: str, platform: str, cases: list[str], *, exclude_id: str = "") -> list[str]:
    name = (name or "").strip()
    if len(name) < 3:
        raise SuiteError("Give the suite a name (at least 3 characters).")
    for s in list_suites():
        if s["id"] != exclude_id and s["name"].strip().lower() == name.lower():
            raise SuiteError(f"A suite called '{s['name']}' already exists.")
    clean: list[str] = []
    seen: set[str] = set()
    for c in cases:
        c = _script_to_case((c or "").strip())
        if not c:
            continue
        if c.lower() in seen:
            raise SuiteError(f"'{c}' is in this suite twice — each test case once.")
        seen.add(c.lower())
        if not flow_exists(c):
            raise SuiteError(f"There is no test case called '{c}'.")
        fp = flow_platform(c)
        if platform and fp and fp != platform:
            raise SuiteError(f"'{c}' is a {fp} test case; this suite is {platform}.")
        clean.append(c)
    if not clean:
        raise SuiteError("Add at least one test case.")
    return clean


def save(name: str, platform: str, cases: list[str], *, description: str = "",
         user: str = "", suite_id: str = "", stop_on_failure: bool = False) -> dict:
    """Create (no suite_id) or update (suite_id) a suite."""
    with _lock:
        clean = _validate(name, platform, cases, exclude_id=suite_id)
        if suite_id:
            p = _path(suite_id)
            if not os.path.exists(p):
                raise SuiteError(f"No suite '{suite_id}'.")
            d = _read(p)
        else:
            suite_id = slug(name)
            n = 2
            while os.path.exists(_path(suite_id)):
                suite_id = f"{slug(name)}_{n}"
                n += 1
            d = {"created_by": user or "system", "created_at": _now()}
        d.update({"_format": 2, "suite_name": name.strip(), "description": description.strip(),
                  "platform": platform, "scripts": [f"flows/{c}.flow" for c in clean],
                  "updated_by": user or "system", "updated_at": _now()})
        d.setdefault("execution", {})["stop_on_failure"] = bool(stop_on_failure)
        # a case that is back in the active list is no longer disabled
        d["disabled"] = [x for x in d.get("disabled") or [] if x.get("test_case") not in clean]
        tmp = _path(suite_id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
        os.replace(tmp, _path(suite_id))
        return _view(suite_id, d)


def _write(suite_id: str, d: dict) -> None:
    tmp = _path(suite_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _path(suite_id))


def disable(suite_id: str, cases: list[str], reason: str, *, user: str = "") -> dict:
    """Take test cases out of the suite's execution list but keep them on the
    suite as 'disabled' (with the reason, who and when) so a plan run skips them
    and the report / suite page still show why. The .flow files are untouched."""
    reason = (reason or "").strip()
    if not reason:
        raise SuiteError("Give a reason — it is shown on the suite and in run reports.")
    with _lock:
        p = _path(suite_id)
        if not os.path.exists(p):
            raise SuiteError(f"No suite '{suite_id}'.")
        d = _read(p)
        want = [c.strip() for c in cases if c and c.strip()]
        active = {_script_to_case(sc): sc for sc in d.get("scripts") or []}
        missing = [c for c in want if c not in active]
        if missing:
            raise SuiteError("Not in this suite: " + ", ".join(missing))
        d["scripts"] = [sc for sc in d["scripts"] if _script_to_case(sc) not in want]
        dis = [x for x in d.get("disabled") or [] if x.get("test_case") not in want]
        for c in want:
            dis.append({"test_case": c, "script": active[c], "reason": reason,
                        "since": _now(), "by": user or "system"})
        d["disabled"] = dis
        d["updated_by"] = user or "system"
        d["updated_at"] = _now()
        _write(suite_id, d)
        return _view(suite_id, d)


def enable(suite_id: str, cases: list[str], *, user: str = "") -> dict:
    """Put disabled test cases back on the suite's execution list (at the end)."""
    with _lock:
        p = _path(suite_id)
        if not os.path.exists(p):
            raise SuiteError(f"No suite '{suite_id}'.")
        d = _read(p)
        want = {c.strip() for c in cases if c and c.strip()}
        keep, back = [], []
        for x in d.get("disabled") or []:
            (back if x.get("test_case") in want else keep).append(x)
        if not back:
            raise SuiteError("None of those test cases is disabled on this suite.")
        d["disabled"] = keep
        scripts = list(d.get("scripts") or [])
        for x in back:
            sc = x.get("script") or f"flows/{x['test_case']}.flow"
            if sc not in scripts:
                scripts.append(sc)
        d["scripts"] = scripts
        d["updated_by"] = user or "system"
        d["updated_at"] = _now()
        _write(suite_id, d)
        return _view(suite_id, d)


def plans_using(suite_id: str) -> list[str]:
    out = []
    if not os.path.isdir(PLANS_DIR):
        return out
    for fn in os.listdir(PLANS_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            d = _read(os.path.join(PLANS_DIR, fn))
        except (ValueError, OSError):
            continue
        refs = [os.path.basename(x)[:-5] if x.endswith(".json") else x
                for x in d.get("selected_suites") or []]
        if suite_id in refs:
            out.append(d.get("plan_name") or fn[:-5])
    return out


def delete(suite_id: str) -> None:
    with _lock:
        users = plans_using(suite_id)
        if users:
            raise SuiteError(f"Used by plan(s): {', '.join(users)} — remove it there first.")
        p = _path(suite_id)
        if not os.path.exists(p):
            raise SuiteError(f"No suite '{suite_id}'.")
        os.remove(p)
