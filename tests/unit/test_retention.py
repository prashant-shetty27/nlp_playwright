"""Retention is off by default and keeps the newest runs of every plan."""
import json
import os
import time

from execution import retention


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("RETENTION_DAYS", raising=False)
    assert retention.prune() == {"enabled": False}


def test_prunes_old_but_keeps_newest_plan_runs(tmp_path, monkeypatch):
    data, logs = tmp_path / "data", tmp_path / "data" / "logs"
    (data / "plan_runs").mkdir(parents=True)
    logs.mkdir()
    old = time.time() - 90 * 86400
    for i in range(3):
        rec = {"plan_id": "p", "status": "passed",
               "items": [{"report_file": f"report_x_{i}.json", "run_ids": [f"r{i}"]}]}
        p = data / "plan_runs" / f"PR_2026010{i}_p.json"
        p.write_text(json.dumps(rec))
        os.utime(p, (old, old))
        r = logs / f"report_x_{i}.json"
        r.write_text("{}")
        os.utime(r, (old, old))
    stray = logs / "report_stray.json"
    stray.write_text("{}")
    os.utime(stray, (old, old))
    monkeypatch.setattr(retention, "DATA_DIR", str(data))
    monkeypatch.setattr(retention, "LOGS_DIR", str(logs))
    monkeypatch.setenv("RETENTION_DAYS", "30")
    monkeypatch.setenv("RETENTION_KEEP_RUNS", "2")
    out = retention.prune()
    assert out["removed"]["plan_runs"] == 1                  # oldest of 3
    assert not stray.exists()                                  # belongs to no kept run
    assert (logs / "report_x_2.json").exists() and (logs / "report_x_1.json").exists()
