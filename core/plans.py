"""
core/plans.py — Test Plans: which suites to run, how, when, and who to tell.

plans/<id>.json keeps the original plan_runner fields (plan_name,
description, platform, selected_suites, execution) and adds:

    schedule: {enabled, frequency, time, days, every_hours, date, timezone}
        frequency: once | daily | weekdays | weekly | hourly
    notify:   {slack: bool, channel: "C0AAP4882H4", when: always|failure}
    created_by/at, updated_by/at, last_run {id, status, at}, next_run (ISO)
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone

from config.settings import PLANS_DIR, SUITES_DIR
from core import suites as suite_store

_lock = threading.Lock()
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
FREQUENCIES = ("once", "daily", "weekdays", "weekly", "hourly")
DEFAULT_TZ = "Asia/Kolkata"
DEFAULT_CHANNEL = os.getenv("SLACK_REPORT_CHANNEL", "C0AAP4882H4")


class PlanError(ValueError):
    pass


def _tz(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name or DEFAULT_TZ)
    except Exception:  # noqa: BLE001 — no tz database: IST is a fixed offset
        return timezone(timedelta(hours=5, minutes=30))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(plan_id: str) -> str:
    return os.path.join(PLANS_DIR, f"{os.path.basename(plan_id)}.json")


def _read(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write(plan_id: str, d: dict) -> None:
    os.makedirs(PLANS_DIR, exist_ok=True)
    tmp = _path(plan_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _path(plan_id))


# ── schedule maths ──────────────────────────────────────────────────────────
def next_run(schedule: dict, after: datetime | None = None) -> datetime | None:
    """The next time (UTC) this schedule fires strictly after `after`."""
    if not schedule or not schedule.get("enabled"):
        return None
    tz = _tz(schedule.get("timezone", DEFAULT_TZ))
    now_local = (after or datetime.now(timezone.utc)).astimezone(tz)
    m = re.match(r"^(\d{1,2}):(\d{2})$", schedule.get("time") or "09:00")
    hh, mm = (int(m.group(1)), int(m.group(2))) if m else (9, 0)
    freq = schedule.get("frequency", "daily")

    def at(day: datetime) -> datetime:
        return day.replace(hour=hh, minute=mm, second=0, microsecond=0)

    if freq == "once":
        try:
            d = datetime.strptime(schedule.get("date") or "", "%Y-%m-%d").replace(tzinfo=tz)
        except ValueError:
            return None
        t = at(d)
        return t.astimezone(timezone.utc) if t > now_local else None
    if freq == "hourly":
        every = max(1, int(schedule.get("every_hours") or 1))
        t = at(now_local)
        while t > now_local:
            t -= timedelta(hours=every)
        while t <= now_local:
            t += timedelta(hours=every)
        return t.astimezone(timezone.utc)
    allowed = {"daily": DAYS, "weekdays": DAYS[:5]}.get(freq) or \
        [d for d in (schedule.get("days") or []) if d in DAYS] or DAYS
    for i in range(0, 8):
        day = now_local + timedelta(days=i)
        t = at(day)
        if t > now_local and DAYS[t.weekday()] in allowed:
            return t.astimezone(timezone.utc)
    return None


def describe_schedule(s: dict) -> str:
    if not s or not s.get("enabled"):
        return "Not scheduled"
    t = s.get("time") or "09:00"
    f = s.get("frequency", "daily")
    # The stored timezone is honoured by next_run(); say the same one here.
    tzname = s.get("timezone") or DEFAULT_TZ
    z = {"Asia/Kolkata": "IST", "UTC": "UTC"}.get(tzname, tzname)
    if f == "once":
        return f"Once on {s.get('date')} at {t} {z}"
    if f == "daily":
        return f"Every day at {t} {z}"
    if f == "weekdays":
        return f"Mon–Fri at {t} {z}"
    if f == "weekly":
        return f"{', '.join(d.title() for d in s.get('days') or [])} at {t} {z}"
    return f"Every {s.get('every_hours') or 1} h from {t} {z}"


# ── views ───────────────────────────────────────────────────────────────────
def _suite_ids(d: dict) -> list[str]:
    out = []
    for x in d.get("selected_suites") or []:
        b = os.path.basename(x)
        out.append(b[:-5] if b.endswith(".json") else b)
    return out


def _view(plan_id: str, d: dict) -> dict:
    sched = d.get("schedule") or {}
    suites = []
    for sid in _suite_ids(d):
        try:
            s = suite_store.get(sid)
            suites.append({"id": sid, "name": s["name"], "count": s["count"], "exists": True})
        except suite_store.SuiteError:
            suites.append({"id": sid, "name": sid, "count": 0, "exists": False})
    ex = d.get("execution") or {}
    return {"id": plan_id, "name": d.get("plan_name") or plan_id,
            "description": d.get("description", ""), "platform": d.get("platform", ""),
            "suites": suites, "test_case_count": sum(s["count"] for s in suites),
            "execution": {"headless": bool(ex.get("headless", False)),
                          "stop_on_failure": bool(ex.get("stop_on_failure", False)),
                          "stop_on_first_failure": bool(ex.get("stop_on_first_failure", False)),
                          "retry_failed": bool(ex.get("retry_failed", ex.get("retry_on_failure", False))),
                          "retry_mode": ex.get("retry_mode") or "flaky",
                          "run_type": ex.get("run_type") or "full",
                          "screenshot_mode": ex.get("screenshot_mode") or "all",
                          # Device / browser matrix: every test case runs once per
                          # entry. [] = the platform's default device only.
                          "devices": [d for d in (ex.get("devices") or []) if isinstance(d, dict)]},
            "schedule": sched, "schedule_text": describe_schedule(sched),
            "next_run": d.get("next_run", ""),
            "notify": d.get("notify") or {"slack": False, "channel": DEFAULT_CHANNEL, "when": "always"},
            "last_run": d.get("last_run") or {}, "legacy": d.get("_format") != 2,
            "created_by": d.get("created_by", ""), "created_at": d.get("created_at", ""),
            "updated_by": d.get("updated_by", ""), "updated_at": d.get("updated_at", "")}


def list_plans() -> list[dict]:
    os.makedirs(PLANS_DIR, exist_ok=True)
    out = []
    for fn in sorted(os.listdir(PLANS_DIR)):
        if not fn.endswith(".json") or fn.startswith("_"):
            continue
        try:
            out.append(_view(fn[:-5], _read(os.path.join(PLANS_DIR, fn))))
        except (ValueError, OSError):
            continue
    return out


def get(plan_id: str) -> dict:
    p = _path(plan_id)
    if not os.path.exists(p):
        raise PlanError(f"No plan '{plan_id}'.")
    return _view(plan_id, _read(p))


def raw(plan_id: str) -> dict:
    return _read(_path(plan_id))


def save(name: str, suite_ids: list[str], *, description: str = "", user: str = "",
         plan_id: str = "", execution: dict | None = None, schedule: dict | None = None,
         notify: dict | None = None) -> dict:
    name = (name or "").strip()
    if len(name) < 3:
        raise PlanError("Give the plan a name (at least 3 characters).")
    ids = [s for s in (suite_ids or []) if s]
    if not ids:
        raise PlanError("Pick at least one suite.")
    if len(set(ids)) != len(ids):
        raise PlanError("A suite is listed twice.")
    platforms = set()
    for sid in ids:
        try:
            platforms.add(suite_store.get(sid)["platform"])
        except suite_store.SuiteError as e:
            raise PlanError(str(e)) from e
    sched = dict(schedule or {})
    if sched.get("enabled"):
        if sched.get("frequency") not in FREQUENCIES:
            raise PlanError(f"Schedule frequency must be one of {', '.join(FREQUENCIES)}.")
        bad = [d for d in (sched.get("days") or []) if d not in DAYS]
        if bad:
            # next_run() silently fell back to EVERY day for unknown names.
            raise PlanError(f"Unknown day name(s) {', '.join(map(str, bad))}; use {', '.join(DAYS)}.")
        m = re.match(r"^(\d{1,2}):(\d{2})$", sched.get("time") or "")
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise PlanError("Schedule time must be a real 24-hour time like 09:00 (IST).")
        if sched["frequency"] == "hourly":
            try:
                every = int(sched.get("every_hours") or 0)
            except (TypeError, ValueError):
                every = 0
            if not 1 <= every <= 24:
                raise PlanError("'Every N hours' must be a whole number from 1 to 24.")
            sched["every_hours"] = every
        tzname = sched.get("timezone") or DEFAULT_TZ
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(tzname)
        except Exception as e:  # noqa: BLE001
            raise PlanError(f"Unknown time zone '{tzname}'.") from e
        if sched["frequency"] == "weekly" and not sched.get("days"):
            raise PlanError("Pick at least one day for a weekly schedule.")
        if sched["frequency"] == "once" and not sched.get("date"):
            raise PlanError("Pick the date for a one-time run.")
        sched.setdefault("timezone", DEFAULT_TZ)
    with _lock:
        for other in list_plans():
            if other["id"] != plan_id and other["name"].lower() == name.lower():
                raise PlanError(f"A plan called '{other['name']}' already exists.")
        if plan_id:
            if not os.path.exists(_path(plan_id)):
                raise PlanError(f"No plan '{plan_id}'.")
            d = _read(_path(plan_id))
        else:
            plan_id = suite_store.slug(name)
            n = 2
            while os.path.exists(_path(plan_id)):
                plan_id = f"{suite_store.slug(name)}_{n}"
                n += 1
            d = {"created_by": user or "system", "created_at": _now()}
        nxt = next_run(sched)
        d.update({"_format": 2, "plan_name": name, "description": (description or "").strip(),
                  "platform": ", ".join(sorted(p for p in platforms if p)),
                  "selected_suites": [f"suites/{s}.json" for s in ids],
                  "execution": execution or d.get("execution") or {},
                  "schedule": sched,
                  "notify": notify or d.get("notify") or {"slack": True, "channel": DEFAULT_CHANNEL,
                                                           "when": "always"},
                  "next_run": nxt.isoformat(timespec="seconds") if nxt else "",
                  "updated_by": user or "system", "updated_at": _now()})
        _write(plan_id, d)
        return _view(plan_id, d)


def record_run(plan_id: str, run: dict) -> None:
    """After a run: remember it, and roll the schedule forward."""
    with _lock:
        if not os.path.exists(_path(plan_id)):
            return
        d = _read(_path(plan_id))
        d["last_run"] = {"id": run.get("id"), "status": run.get("status"),
                         "at": run.get("started_at"), "trigger": run.get("trigger")}
        nxt = next_run(d.get("schedule") or {})
        d["next_run"] = nxt.isoformat(timespec="seconds") if nxt else ""
        if (d.get("schedule") or {}).get("frequency") == "once" and not nxt:
            d["schedule"]["enabled"] = False
        _write(plan_id, d)


def roll_forward(plan_id: str) -> None:
    with _lock:
        d = _read(_path(plan_id))
        nxt = next_run(d.get("schedule") or {})
        d["next_run"] = nxt.isoformat(timespec="seconds") if nxt else ""
        # A 'once' plan whose slot has passed is over — missed or fired. It
        # used to stay "scheduled, enabled" forever after a missed slot.
        if (d.get("schedule") or {}).get("frequency") == "once" and not nxt:
            d["schedule"]["enabled"] = False
        _write(plan_id, d)


def delete(plan_id: str) -> None:
    with _lock:
        p = _path(plan_id)
        if not os.path.exists(p):
            raise PlanError(f"No plan '{plan_id}'.")
        os.remove(p)
