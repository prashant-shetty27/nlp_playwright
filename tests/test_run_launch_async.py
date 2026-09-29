import os
import threading
import time
import uuid

from fastapi.testclient import TestClient
from api.auth import INTERNAL_TOKEN  # noqa: E402

from api.app import app
from api.routes import tests as tests_route
from config.settings import FLOWS_DIR


def test_run_launch_returns_before_execution_finishes(monkeypatch) -> None:
    flow_name = f"_async_launch_{uuid.uuid4().hex}"
    flow_path = os.path.join(FLOWS_DIR, f"{flow_name}.flow")
    with open(flow_path, "w", encoding="utf-8") as f:
        f.write("# async launch probe\n")

    started = threading.Event()
    release = threading.Event()
    original_runner = tests_route._run_flow_sync
    client = TestClient(app, headers={"X-Internal-Token": INTERNAL_TOKEN})

    def fake_run_flow_sync(run_id: str, flow_path: str, headless: bool, **kwargs) -> dict:
        started.set()
        release.wait(timeout=5)
        result = {
            "run_id": run_id,
            "project": flow_name,
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "stopped_early": False,
            "screenshots": {},
            "log": [],
            "report_file": "",
            "finished_at": "2026-08-21T00:00:00+00:00",
        }
        tests_route._remember(run_id, {"status": "done", "result": result})
        return result

    monkeypatch.setattr(tests_route, "_run_flow_sync", fake_run_flow_sync)
    try:
        launched_at = time.perf_counter()
        response = client.post("/tests/run", json={
            "project": flow_name,
            "platform": "website",
            "headless": True,
        })
        launch_ms = (time.perf_counter() - launched_at) * 1000

        assert response.status_code == 200
        assert started.wait(timeout=1), "worker thread did not start"
        assert launch_ms < 1000, f"launch blocked for {launch_ms:.1f} ms"

        run_id = response.json()["run_id"]
        running = client.get(f"/tests/results/{run_id}")
        assert running.status_code == 200
        assert running.json()["status"] == "running"

        release.set()
        deadline = time.time() + 5
        while time.time() < deadline:
            done = client.get(f"/tests/results/{run_id}")
            if done.status_code == 200 and "passed" in done.json():
                break
            time.sleep(0.05)
        else:
            raise AssertionError("run did not finish after worker release")

        assert done.json()["run_id"] == run_id
        assert done.json()["passed"] == 0
        assert done.json()["failed"] == 0
    finally:
        monkeypatch.setattr(tests_route, "_run_flow_sync", original_runner)
        if os.path.exists(flow_path):
            os.unlink(flow_path)
