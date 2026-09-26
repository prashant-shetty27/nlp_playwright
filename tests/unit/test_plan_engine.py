"""Retry classification and schedule maths — pure functions, no browser."""
from datetime import datetime, timezone

from core import plans
from execution.plan_engine import _looks_flaky


def fl(err):
    return _looks_flaky({"log": [{"status": "failed", "error": err}]})


def test_real_check_failures_are_not_retried():
    assert not fl("❌ Priority order broken in API ${apiResp2}: #8 ...")
    assert not fl("Locator expected to have text 'X' with timeout 5000ms")
    assert not fl("❌ Match Failed: Could not find 'a' in variable 'b'")
    assert not fl("Locator 'x' not found in any page.")


def test_timeouts_and_network_are_retried():
    assert fl("Timeout 30000ms exceeded while navigating")
    assert fl("HTTPConnectionPool(host='192.168.8.27'): Max retries exceeded")
    assert fl("net::ERR_CONNECTION_RESET")
    assert _looks_flaky({"log": []})          # nothing ran at all


def test_next_run_daily_ist():
    after = datetime(2026, 9, 26, 5, 0, tzinfo=timezone.utc)          # 10:30 IST
    nr = plans.next_run({"enabled": True, "frequency": "daily", "time": "11:45"}, after)
    assert nr.astimezone(timezone.utc).strftime("%H:%M") == "06:15"   # 11:45 IST same day


def test_next_run_weekdays_skips_weekend():
    after = datetime(2026, 9, 26, 7, 0, tzinfo=timezone.utc)          # Saturday 12:30 IST
    nr = plans.next_run({"enabled": True, "frequency": "weekdays", "time": "09:00"}, after)
    assert nr.weekday() == 0                                            # Monday


def test_disabled_schedule_has_no_next_run():
    assert plans.next_run({"enabled": False}) is None
