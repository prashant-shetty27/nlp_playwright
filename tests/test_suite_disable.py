"""core/suites.py — disable / enable test cases on a suite (kept, not run)."""
import json
import os

import pytest

from core import suites as S


@pytest.fixture
def suite(tmp_path, monkeypatch):
    flows = tmp_path / "flows"
    flows.mkdir()
    for n in ("A", "B", "C"):
        (flows / f"{n}.flow").write_text("# Platform: mobilesite\nopen https://x\n")
    monkeypatch.setattr(S, "FLOWS_DIR", str(flows))
    monkeypatch.setattr(S, "SUITES_DIR", str(tmp_path))
    monkeypatch.setattr(S, "_validate", lambda name, platform, cases, exclude_id="": list(cases))
    v = S.save("s", "mobilesite", ["A", "B", "C"], user="u")
    return v["id"]


def test_disable_keeps_case_with_reason_and_enable_puts_it_back(suite):
    v = S.disable(suite, ["B"], "product not live", user="u")
    assert [c["name"] for c in v["test_cases"]] == ["A", "C"]
    assert v["disabled"][0]["test_case"] == "B" and v["disabled"][0]["reason"] == "product not live"
    d = json.load(open(os.path.join(S.SUITES_DIR, f"{suite}.json")))
    assert "flows/B.flow" not in d["scripts"] and d["disabled"][0]["script"] == "flows/B.flow"
    v = S.enable(suite, ["B"], user="u")
    assert [c["name"] for c in v["test_cases"]] == ["A", "C", "B"] and v["disabled"] == []


def test_disable_needs_reason_and_case_on_suite(suite):
    with pytest.raises(S.SuiteError):
        S.disable(suite, ["A"], "   ")
    with pytest.raises(S.SuiteError):
        S.disable(suite, ["Z"], "x")
    with pytest.raises(S.SuiteError):
        S.enable(suite, ["A"])


def test_saving_a_case_back_into_the_list_clears_its_disabled_entry(suite):
    S.disable(suite, ["C"], "later", user="u")
    v = S.save("s", "mobilesite", ["A", "B", "C"], user="u", suite_id=suite)
    assert v["disabled"] == [] and v["count"] == 3
