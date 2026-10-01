"""Run failures → Jira drafts, type rule, fields, raising (Jira mocked — nothing is sent)."""
import json

import pytest

from core import jira_issues as ji


def test_type_rule():
    assert ji.type_rule(True, True) == ("Bug", ["Bug"])
    assert ji.type_rule(True, False) == ("Concern", ["Bug", "Concern"])
    assert ji.type_rule(False, True) == ("Defect", ["Defect", "Concern"])
    assert ji.type_rule(None, False) == ("Concern", ["Defect", "Concern"])


def test_classify_and_plain_reason():
    assert ji.classify("'x' is not in your element list", "click x") == "automation"
    assert ji.classify("Variable '${a}' is not stored in memory!", "enter ${a}") == "automation"
    assert ji.classify("the site did not load: net::ERR_NAME_NOT_RESOLVED", "open u") == "setup"
    assert ji.classify("Visibility assertion failed for 'icon'", "verify element icon is visible") == "product"
    r = "Visibility assertion failed for 'gallery_360_icon' (//div[contains(@class,'x')]): Locator expected\nCall log: …"
    assert ji.plain_reason(r) == "gallery 360 icon is not visible"


@pytest.fixture
def plan(tmp_path, monkeypatch):
    logs, runs, shots = tmp_path / "logs", tmp_path / "plan_runs", tmp_path / "shots"
    for d in (logs, runs, shots):
        d.mkdir()
    monkeypatch.setattr(ji, "LOGS_DIR", str(logs))
    monkeypatch.setattr(ji, "PLAN_RUNS_DIR", str(runs))
    monkeypatch.setattr(ji, "SCREENSHOTS_DIR", str(shots))
    monkeypatch.setattr(ji, "RAISED_FILE", str(tmp_path / "raised.json"))
    monkeypatch.setattr(ji, "LOGINS_FILE", str(tmp_path / "logins.json"))
    monkeypatch.setattr(ji, "story_of", lambda tc, *a: "GJDT-1")

    def report(name, dev, fail_reason):
        (shots / f"{name}_1.jpg").write_bytes(b"x")
        (shots / f"{name}_2.jpg").write_bytes(b"x")
        rep = {"testplan": "TC_A", "started_at": "2026-10-01T10:00:00",
               "device": {"device_name": dev, "browser_identity": "android_chrome"},
               "site_env": {"name": "prot3", "host": "prot3.justdial.com"},
               "results": [
                   {"test_name": "open https://www.justdial.com/x", "status": "passed", "screenshot": f"{name}_1.jpg"},
                   {"test_name": "verify element icon is visible", "status": "failed",
                    "reason": fail_reason, "screenshot": f"{name}_2.jpg"},
                   {"test_name": "click y", "status": "failed", "reason": "'y' is not in your element list"}]}
        (logs / f"{name}.json").write_text(json.dumps(rep))
        return f"{name}.json"

    items = [
        {"test_case": "TC_A", "status": "failed", "device_label": "Chrome on Android (Pixel 7) — full suite",
         "report_file": report("r1", "Pixel 7", "Visibility assertion failed for 'icon' (//a): x"), "run_id": "1"},
        {"test_case": "TC_A", "status": "failed", "device_label": "Safari on iPhone (iPhone 15)",
         "report_file": report("r2", "iPhone 15", "Visibility assertion failed for 'icon' (//a): x"), "run_id": "2"},
    ]
    (runs / "PR_1.json").write_text(json.dumps({"id": "PR_1", "plan_name": "p", "items": items,
                                                "execution": {"site_env": "prot3"}}))
    return "PR_1"


def test_draft_groups_devices_and_separates_test_problems(plan):
    d = ji.draft(plan_run=plan)
    assert len(d["issues"]) == 1 and len(d["automation"]) == 2
    i = d["issues"][0]
    assert i["devices"] == ["Chrome on Android (Pixel 7)", "Safari on iPhone (iPhone 15)"]
    assert i["confirmed"] and i["type"] == "Defect" and i["live"] is False
    assert "https://prot3.justdial.com/x" in i["description"]        # moved to the run's site
    assert i["summary"].endswith("Icon not shown on page load")
    assert "# *Check the icon is shown*" in i["description"] and "Expected result" in i["description"]
    assert len(i["screenshots"]) == 4


class FakeResp:
    def __init__(self, code=200, data=None):
        self.status_code, self._d = code, data or {}

    def json(self):
        return self._d


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, files=None, headers=None, timeout=None):
        self.calls.append((url, json, bool(files)))
        if url.endswith("/rest/api/2/issue"):
            return FakeResp(201, {"key": "GJDT-99"})
        return FakeResp(201, {})


def test_raise_creates_links_attaches_and_never_twice(plan, monkeypatch):
    d = ji.draft(plan_run=plan)
    ji._write_json(ji.LOGINS_FILE, {"tester": {"token": "t"}})
    fake = FakeSession()
    monkeypatch.setattr(ji, "_session", lambda token: fake)
    monkeypatch.setattr(ji, "story_info", lambda k: {"key": k, "project": {"id": "10755", "key": "GJDT"}})
    item = {**d["issues"][0], "owner": "sohini", "priority": "High"}
    res = ji.raise_issues("tester", [item], "GJDT-1")
    assert res[0]["ok"] and res[0]["key"] == "GJDT-99"
    fields = fake.calls[0][1]["fields"]
    assert fields["issuetype"] == {"name": "Defect"} and fields["assignee"] == {"name": "sohini"}
    assert fields["priority"] == {"name": "High"} and fields["labels"] == ["GJDT-1", "ps_codeless_automation"]
    assert "environment" in fields
    assert any(c[0].endswith("/issueLink") for c in fake.calls)
    assert sum(1 for c in fake.calls if c[2]) == 4                     # 4 screenshots attached
    again = ji.raise_issues("tester", [item], "GJDT-1")
    assert again[0].get("already") and len([c for c in fake.calls if c[0].endswith("/issue")]) == 1


def test_raise_needs_own_token(plan):
    with pytest.raises(ji.IssueError, match="Connect your Jira"):
        ji.raise_issues("nobody", [{"id": "x"}], "GJDT-1")


def test_prefill_and_csv():
    item = {"id": "a", "type": "Concern", "summary": "S", "description": "D", "priority": "Low",
            "owner": "sohini"}
    url = ji.prefill_url(item, "10755", "GJDT-1")
    assert "pid=10755" in url and "issuetype=11300" in url and "priority=4" in url and "labels=GJDT-1" in url
    data = ji.csv_bytes([item], "GJDT", "GJDT-1").decode("utf-8-sig")
    assert data.splitlines()[0].startswith("Project Key,Issue Type,Summary")
    assert "Concern" in data and "sohini" in data
