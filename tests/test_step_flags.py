import pytest

from execution import step_flags as sf
from nlp.parser import parse_step


def test_split_and_join():
    assert sf.split("[ignore] click X") == (True, 5.0, "click X")
    assert sf.split("[ignore 10s] click X") == (True, 10.0, "click X")
    assert sf.split("click X") == (False, 0.0, "click X")
    assert sf.join("click X", True) == "[ignore] click X"
    assert sf.join("[ignore] click X", True, 10) == "[ignore 10s] click X"
    assert sf.join("[ignore 3s] click X", False) == "click X"


def test_parser_reads_marked_steps():
    assert parse_step("[ignore 3s] click login_btn").type == parse_step("click login_btn").type


def test_failure_becomes_ignored_and_waits_are_short():
    from config import settings
    seen = {}

    def boom(text):
        seen["timeout"] = settings.ACTION_TIMEOUT_MS
        raise AssertionError("not there")
    with pytest.raises(sf.IgnoredFailure, match="not there"):
        sf.run_ignorable("[ignore 2s] click x", boom)
    assert seen["timeout"] == 2000 and settings.ACTION_TIMEOUT_MS != 2000
    with pytest.raises(AssertionError):
        sf.run_ignorable("click x", boom)


def test_report_counts_ignored(tmp_path):
    from reporting.report_manager import TestReportManager
    r = TestReportManager("t", "tester")
    r.add_result("[ignore] click x", "ignored", reason="ignored — not there")
    r.add_result("click y", "passed")
    s = r._summary()
    assert s["ignored"] == 1 and s["failed"] == 0
