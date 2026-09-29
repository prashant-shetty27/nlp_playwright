"""Smoke / Sanity / Regression / Full band selection (core/run_types.py)."""
from core import run_types as R

FLOW = """# Platform: mobilesite
open https://example.com/login
# --- Purpose: TC_A [smoke] — alive
open https://example.com/a
store text of x as va
# --- Purpose: TC_B [sanity] — changed area
open https://example.com/b
verify stored va contains "a"
# --- Purpose: TC_C — untagged = regression
open https://example.com/c
# --- Purpose: TC_D [full] — slow
# OFF: click submit
open https://example.com/d
""".splitlines(keepends=True)


def runs(rt):
    sel = R.select(FLOW, rt)
    return None if sel["run"] is None else sorted(sel["run"]), sel


def test_full_and_no_type_run_everything():
    assert runs("full")[0] is None
    assert runs("")[0] is None
    assert runs("nonsense")[0] is None


def test_smoke_runs_setup_plus_smoke_band_only():
    lines, sel = runs("smoke")
    assert sel["bands_in"] == ["TC_A"]
    assert lines == [2, 4, 5]          # setup open + band A


def test_levels_are_supersets():
    s = set(runs("smoke")[0]); sa = set(runs("sanity")[0]); r = set(runs("regression")[0])
    assert s < sa < r
    assert "TC_D" not in runs("regression")[1]["bands_in"]


def test_off_lines_never_selected():
    for rt in ("smoke", "sanity", "regression"):
        lines = runs(rt)[0]
        assert 12 not in lines          # the '# OFF:' line (line 14 never existed)
    # "full" selects nothing (run everything) — the OFF line is then the
    # runner's job, checked in the runner's own tests.


def test_variable_from_skipped_band_warns():
    # sanity keeps TC_B (reads ${va}) but also TC_A (stores it) -> no warning
    assert not runs("sanity")[1]["warnings"]
    flow = [l.replace("TC_A [smoke]", "TC_A [regression]") for l in FLOW]
    sel = R.select(flow, "sanity")
    assert any("${va}" in w for w in sel["warnings"])


def test_multiple_bracket_groups_and_flow_default():
    assert R.band_tags("# --- Purpose: TC_X [Mumbai] [sanity] — t") == {"sanity"}
    flow = ["# Tags: smoke\n", "open x\n", "verify y\n"]
    assert R.select(flow, "smoke")["run"] is None           # whole flow tagged smoke
    assert R.select(["open x\n"], "smoke")["run"] == set()   # untagged flow: not in smoke


def test_band_not_opening_a_page_after_skipped_band_warns():
    flow = ["# --- Purpose: A [smoke]\n", "open x\n", "# --- Purpose: B\n", "open y\n",
            "# --- Purpose: C [smoke]\n", "click q\n"]
    assert any("does not start by opening" in w for w in R.select(flow, "smoke")["warnings"])
