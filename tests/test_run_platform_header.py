"""
A flow's "# Platform:" header decides the platform a run uses.

Regression for: flows/NCT_Job_related_Questions.flow (declared mobilesite) was
launched as website whenever the caller omitted the platform or sent a stale
one, so Playwright opened a desktop context with no device emulation.
"""
import os
import uuid

from fastapi.testclient import TestClient

from api.app import app
from api.routes import tests as tests_route
from config.settings import FLOWS_DIR


def _launch(monkeypatch, header: str | None, requested: str | None) -> tuple[dict, dict]:
    flow_name = f"_platform_probe_{uuid.uuid4().hex}"
    flow_path = os.path.join(FLOWS_DIR, f"{flow_name}.flow")
    with open(flow_path, "w", encoding="utf-8") as f:
        if header:
            f.write(f"# Platform: {header}\n")
        f.write("# probe\n")

    seen: dict = {}

    def fake_run_flow_sync(run_id, flow_path, headless, **kwargs):
        seen.update(kwargs.get("capabilities") or {})
        tests_route._remember(run_id, {"status": "done", "result": {"run_id": run_id}})
        return {"run_id": run_id}

    original = tests_route._run_flow_sync
    monkeypatch.setattr(tests_route, "_run_flow_sync", fake_run_flow_sync)
    try:
        body = {"project": flow_name, "headless": True}
        if requested is not None:
            body["platform"] = requested
        r = TestClient(app).post("/tests/run", json=body)
        assert r.status_code == 200, r.text
        return r.json(), seen
    finally:
        monkeypatch.setattr(tests_route, "_run_flow_sync", original)
        if os.path.exists(flow_path):
            os.unlink(flow_path)


def test_header_wins_over_stale_request(monkeypatch) -> None:
    res, caps = _launch(monkeypatch, header="mobilesite", requested="website")
    assert res["platform"] == "mobilesite"
    assert res["platform_source"] == "flow header"
    assert caps["mobile_web"] is True and caps["device_name"] == "Pixel 7"


def test_header_wins_when_request_omits_platform(monkeypatch) -> None:
    res, caps = _launch(monkeypatch, header="mobilesite", requested=None)
    assert res["platform"] == "mobilesite"
    assert caps["mobile_web"] is True


def test_no_header_keeps_requested_platform(monkeypatch) -> None:
    res, caps = _launch(monkeypatch, header=None, requested="mobilesite")
    assert res["platform"] == "mobilesite"
    assert res["platform_source"] == "request"
    assert caps["mobile_web"] is True


def test_website_header_stays_desktop(monkeypatch) -> None:
    res, caps = _launch(monkeypatch, header="website", requested="website")
    assert res["platform"] == "website"
    assert caps["mobile_web"] is False and caps["device_name"] == ""
