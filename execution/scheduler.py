"""
execution/scheduler.py — fires scheduled Test Plans.

Runs inside the portal server (it needs this machine's VPN and browser).
Every 30 s it looks for enabled plans whose next_run has passed:

  * due within the last 20 minutes → start the plan (trigger "schedule");
  * due longer ago (machine asleep / server down at that time) → record a
    "missed" run so it is visible, and move on to the next slot — a morning
    plan should not suddenly start at lunch.

next_run is rolled forward the moment a plan is fired, so a slow run or a
restart never fires the same slot twice.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
TICK_S = 30
GRACE = timedelta(minutes=int(os.getenv("SCHEDULE_GRACE_MIN", "20")))
_started = False
_state = {"last_tick": "", "fired": 0, "missed": 0}


def status() -> dict:
    return {"running": _started, **_state, "grace_minutes": int(GRACE.total_seconds() // 60)}


def start() -> None:
    global _started
    if _started or os.getenv("DISABLE_SCHEDULER") == "1":
        return
    _started = True
    try:
        from execution import plan_engine
        plan_engine.recover_orphans()
    except Exception:  # noqa: BLE001
        logger.exception("Could not close interrupted plan runs")
    threading.Thread(target=_loop, name="plan-scheduler", daemon=True).start()
    logger.info("⏰ Plan scheduler started (tick %ss, grace %s)", TICK_S, GRACE)


def _loop() -> None:
    time.sleep(5)
    while True:
        try:
            tick()
        except Exception:  # noqa: BLE001 — the loop must survive one bad plan
            logger.exception("Scheduler tick failed")
        try:
            watch_stuck()
        except Exception:  # noqa: BLE001
            logger.exception("Stuck-run check failed")
        try:
            from execution import retention
            retention.daily()            # no-op unless RETENTION_DAYS is set
        except Exception:  # noqa: BLE001
            logger.exception("Retention clean-up failed")
        time.sleep(TICK_S)


def tick(now: datetime | None = None) -> None:
    from core import plans
    from execution import plan_engine

    now = now or datetime.now(timezone.utc)
    _state["last_tick"] = now.isoformat(timespec="seconds")
    for p in plans.list_plans():
        sched = p.get("schedule") or {}
        if not sched.get("enabled") or not p.get("next_run"):
            continue
        try:
            due = datetime.fromisoformat(p["next_run"])
        except ValueError:
            continue
        if due > now:
            continue
        # One bad plan (file removed between list and start, a PlanError)
        # must not abort the tick for the plans after it, and a slot that was
        # claimed but failed to start is recorded as missed, not lost.
        try:
            plans.roll_forward(p["id"])            # claim the slot first
            if now - due > GRACE:
                _state["missed"] += 1
                _record_missed(p, due, now)
                continue
            _state["fired"] += 1
            logger.info("⏰ Firing scheduled plan '%s' (due %s)", p["name"], p["next_run"])
            owner = p.get("updated_by") or p.get("created_by") or "scheduler"
            plan_engine.start(p["id"], trigger="schedule", user=f"scheduler ({owner})")
        except Exception as e:  # noqa: BLE001
            logger.exception("Scheduled plan '%s' could not be started", p.get("name"))
            _state["missed"] += 1
            try:
                _record_missed(p, due, now, reason=f"start failed: {e}")
            except Exception:  # noqa: BLE001
                pass


def _record_missed(p: dict, due: datetime, now: datetime, reason: str = "") -> None:
    from execution import plan_engine

    rid = due.strftime("PR_%Y%m%d_%H%M%S_") + p["id"][:30]
    rec = {"id": rid, "plan_id": p["id"], "plan_name": p["name"], "trigger": "schedule",
           "triggered_by": "scheduler", "status": "missed", "queued_at": due.isoformat(timespec="seconds"),
           "started_at": "", "finished_at": now.isoformat(timespec="seconds"), "items": [],
           "totals": {"test_cases": 0, "passed": 0, "failed": 0, "not_run": 0},
           "reason": reason or (f"Due at {due.isoformat(timespec='minutes')} but the server was not running "
                                f"or the machine was asleep; skipped rather than run late.")}
    plan_engine._save(rec)
    logger.warning("⏰ Plan '%s' missed its %s slot", p["name"], due.isoformat(timespec="minutes"))
    try:
        from reporting.plan_slack import notify
        notify(rec)
    except Exception:  # noqa: BLE001
        logger.exception("Could not report the missed run to Slack")


_alerted: set = set()


def watch_stuck() -> None:
    """Alert Slack once per stuck spot (plan run + test case run + line)."""
    from execution import plan_engine

    rid = plan_engine.active_run()
    if not rid:
        return
    rec = plan_engine.get_run(rid)
    h = plan_engine.health(rec)
    if not h.get("stuck"):
        return
    key = (rid, h.get("kind"), h.get("run_id"), h.get("line"))
    if key in _alerted:
        return
    _alerted.add(key)
    logger.warning("⚠️ Plan run %s looks stuck: %s", rid, h.get("message"))
    from reporting.plan_slack import alert_stuck
    alert_stuck(rec, h)
