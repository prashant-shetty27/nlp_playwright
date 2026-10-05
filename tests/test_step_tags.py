"""core/step_tags.py — dependency-safe per-step run-type tags."""
import os

from core import step_tags as T

GROUPS = {"SG_Open_Chat": ["open https://example.com", "click chat_fab", "store text of reply as bot_reply"],
          "SG_Send": ["type ${message} into chat_input", "press key Enter", "store text of reply as bot_reply"]}

FLOW = """# Platform: mobilesite
store "Pune" as city
call SG_Open_Chat
verify ${bot_reply} contains "Hello"

store "pipes" as message
call SG_Send
verify ${bot_reply} contains "campaign"
verify ${bot_reply} does not contain "error"

if element offer_popup is visible
verify element offer_title is visible
else
verify element no_offer_text is visible
end if
"""


def _graph(text=FLOW):
    return T.FlowGraph(text.splitlines(True), GROUPS)


def test_auto_levels_smoke_sanity_rest_full():
    g = _graph()
    lv = dict(zip((s.text for s in g.steps), g.auto_levels()))
    assert lv['store "Pune" as city'] == "S"
    assert lv["call SG_Open_Chat"] == "S"
    assert lv['verify ${bot_reply} contains "Hello"'] == "S"
    assert lv["call SG_Send"] == "Sy"                      # needed by the 2nd paragraph's check
    assert lv['verify ${bot_reply} contains "campaign"'] == "Sy"
    assert lv['verify ${bot_reply} does not contain "error"'] == "F"
    # an if-branch check pulls its sibling branch and the block lines
    assert lv["verify element offer_title is visible"] == lv["verify element no_offer_text is visible"]
    assert lv["else"] == lv["end if"] == lv["if element offer_popup is visible"]


def test_invariant_closure_inside_every_type():
    g = _graph()
    lv = g.auto_levels()
    for t in ("S", "Sy", "R"):
        members = {i for i, v in enumerate(lv) if T._IDX[v] <= T._IDX[t]}
        assert g.closure(members) <= members


def test_toggle_on_lifts_dependencies_and_off_cascades(tmp_path, monkeypatch):
    p = tmp_path / "Demo.flow"
    p.write_text(FLOW)
    monkeypatch.setattr(T, "_load_groups", lambda base_dir=None: GROUPS)
    d = T.build(str(p))
    texts = [e["text"] for e in d["steps"]]
    chk = texts.index('verify ${bot_reply} does not contain "error"')
    send = texts.index("call SG_Send")
    r = T.toggle(str(p), chk, "S")
    lv = [e["level"] for e in r["steps"]]
    assert lv[chk] == "S" and lv[send] == "S"
    r = T.toggle(str(p), send, "S")
    lv = [e["level"] for e in r["steps"]]
    assert lv[send] != "S" and lv[chk] != "S"            # needs the send → off too
    assert T.select_lines(str(p), "smoke") is not None
    assert T.select_lines(str(p), "full") is None


def test_manual_tags_survive_an_inserted_line(tmp_path, monkeypatch):
    p = tmp_path / "Demo.flow"
    p.write_text(FLOW)
    monkeypatch.setattr(T, "_load_groups", lambda base_dir=None: GROUPS)
    d = T.build(str(p))
    texts = [e["text"] for e in d["steps"]]
    T.toggle(str(p), texts.index('verify ${bot_reply} does not contain "error"'), "S")
    p.write_text(FLOW.replace('store "pipes"', 'wait 2 seconds\nstore "pipes"'))
    os.utime(p, None)
    d = T.ensure(str(p))
    by = {e["text"]: e for e in d["steps"]}
    assert by['verify ${bot_reply} does not contain "error"']["level"] == "S"
    assert by['verify ${bot_reply} does not contain "error"']["manual"] is True
