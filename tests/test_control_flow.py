"""Control flow: if/else, for each row (range, where, columns), repeat, stop/skip."""
import pytest

from execution.control_flow import (FlowProgram, FlowStructureError, classify,
                                    parse_row_options, split_comparison, compare,
                                    split_logic, combine)

ROWS = [
    {"city": "Mumbai", "mobile": "9000000001", "platform": "website", "price": "100", "row_number": "1"},
    {"city": "Pune", "mobile": "9000000002", "platform": "mobilesite", "price": "600", "row_number": "2"},
    {"city": "Delhi", "mobile": "9000000003", "platform": "mobilesite", "price": "300", "row_number": "3"},
    {"city": "Chennai", "mobile": "9000000004", "platform": "website", "price": "900", "row_number": "4"},
    {"city": "Surat", "mobile": "9000000005", "platform": "mobilesite", "price": "50", "row_number": "5"},
]


def run(text, truths=None, variables=None, rows=None):
    """Run a flow; returns the ordinary steps executed (with ${} filled in)."""
    truths = dict(truths or {})
    store = variables if variables is not None else {}

    def evaluate(cond):
        v = truths.get(cond)
        if callable(v):
            v = v()
        if v is None:
            raise AssertionError(f"unexpected condition {cond!r}")
        return bool(v), cond

    prog = FlowProgram(text.strip("\n").splitlines(), evaluate=evaluate,
                       variables=store, load_rows=lambda n: list(rows or ROWS))
    done = []
    for it in prog.steps():
        if it.kind is None:
            out = it.text
            for k, v in store.items():
                out = out.replace("${" + k + "}", str(v))
            done.append(out)
        else:
            prog.decide(it)
    return done


def test_plain_flow_unchanged():
    assert run("open a\n# comment\n\ntap b") == ["open a", "tap b"]


def test_if_true_false_else():
    flow = """
if A
    tap a
else if B
    tap b
else
    tap c
end if
tap after
"""
    assert run(flow, {"A": True}) == ["tap a", "tap after"]
    assert run(flow, {"A": False, "B": True}) == ["tap b", "tap after"]
    assert run(flow, {"A": False, "B": False}) == ["tap c", "tap after"]


def test_if_without_else_skips():
    assert run("if A\n tap a\nend if\ntap z", {"A": False}) == ["tap z"]


def test_for_each_row_all_columns():
    flow = """
for each row in cities
    enter ${city} in box
    enter ${mobile} in mobile
end for
done
"""
    got = run(flow)
    assert got[:2] == ["enter Mumbai in box", "enter 9000000001 in mobile"]
    assert len(got) == 11 and got[-1] == "done"


def test_for_each_row_range_first_last_from():
    assert run("for each row in c rows 2 to 3\n v ${city}\nend for") == ["v Pune", "v Delhi"]
    assert run("for each row in c first 2 rows\n v ${city}\nend for") == ["v Mumbai", "v Pune"]
    assert run("for each row in c last 1 rows\n v ${city}\nend for") == ["v Surat"]
    assert run("for each row in c from row 4\n v ${city}\nend for") == ["v Chennai", "v Surat"]
    assert run("for each row in c only row 3\n v ${row_number}\nend for") == ["v 3"]


def test_for_each_where_and_range():
    flow = "for each row in c rows 1 to 4 where platform is mobilesite\n v ${city}\nend for"
    assert run(flow) == ["v Pune", "v Delhi"]
    flow = "for each row in c where price is more than 500 and platform is website\n v ${city}\nend for"
    assert run(flow) == ["v Chennai"]


def test_loop_values_restored_after_loop():
    store = {"city": "FromTestData"}
    got = run("for each row in c first 1 rows\n v ${city}\nend for\n v ${city}", variables=store)
    assert got == ["v Mumbai", "v FromTestData"]
    assert "mobile" not in store


def test_stop_loop_if_and_skip():
    seen = []
    flow = """
for each row in c
    skip to next row if ${city} is Pune
    v ${city}
    stop loop if delhi
end for
end
"""
    store = {}

    def evaluate(cond):
        if cond == "${city} is Pune":
            return store.get("city") == "Pune", cond
        return store.get("city") == "Delhi", cond

    prog = FlowProgram(flow.strip().splitlines(), evaluate=evaluate, variables=store,
                       load_rows=lambda n: ROWS)
    for it in prog.steps():
        if it.kind is None:
            seen.append(it.text.replace("${city}", store.get("city", "")))
        else:
            prog.decide(it)
    assert seen == ["v Mumbai", "v Delhi", "end"]


def test_repeat_times_and_round():
    assert run("repeat 3 times\n swipe ${round}\nend repeat") == ["swipe 1", "swipe 2", "swipe 3"]


def test_repeat_until_and_cap():
    n = {"i": 0}

    def cond():
        return n["i"] >= 2

    store = {}
    prog = FlowProgram(["repeat until more gone", "tap more", "end repeat", "after"],
                       evaluate=lambda c: (cond(), c), variables=store, load_rows=None)
    done = []
    for it in prog.steps():
        if it.kind is None:
            done.append(it.text)
            if it.text == "tap more":
                n["i"] += 1
        else:
            prog.decide(it)
    assert done == ["tap more", "tap more", "after"]

    prog = FlowProgram(["repeat until never (max 3 times)", "tap", "end repeat"],
                       evaluate=lambda c: (False, c), variables={}, load_rows=None)
    with pytest.raises(AssertionError, match="3 rounds"):
        for it in prog.steps():
            if it.kind:
                prog.decide(it)


def test_nested_if_inside_loop_and_nested_loops():
    flow = """
for each row in c first 3 rows
    if ${city} is Pune
        tap pune
    else
        repeat 2 times
            tap ${city}
        end repeat
    end if
end for
"""
    store = {}

    def evaluate(cond):
        return store.get("city") == "Pune", cond

    prog = FlowProgram(flow.strip().splitlines(), evaluate=evaluate, variables=store,
                       load_rows=lambda n: ROWS)
    done = []
    for it in prog.steps():
        if it.kind is None:
            done.append(it.text.replace("${city}", store.get("city", "")))
        else:
            prog.decide(it)
    assert done == ["tap Mumbai", "tap Mumbai", "tap pune", "tap Delhi", "tap Delhi"]


def test_loop_header_reported_each_round():
    prog = FlowProgram(["for each row in c first 2 rows", "v", "end for"],
                       evaluate=None, variables={}, load_rows=lambda n: ROWS)
    labels = []
    for it in prog.steps():
        if it.kind:
            labels.append(prog.decide(it))
    assert len(labels) == 2 and labels[1].startswith("row 2 (2 of 2)")
    assert "90---002" in labels[1]          # mobiles masked in the log


@pytest.mark.parametrize("flow,msg", [
    ("if A\n tap", "never closed"),
    ("tap\nend if", "no matching if"),
    ("else\n tap", "inside an 'if'"),
    ("stop loop", "only works inside a loop"),
    ("if A\nfor each row in c\nend if\nend for", "does not match"),
    ("if A\nelse\nelse if B\nend if", "nothing can follow 'else'"),
    ("for each row in c rows 5 to 2\nend for", "A ≤ B"),
    ("for each row in c banana\nend for", "Did not understand 'banana'"),
])
def test_structure_errors(flow, msg):
    with pytest.raises(FlowStructureError, match=msg):
        FlowProgram(flow.splitlines())


def test_classify_does_not_catch_ordinary_steps():
    for s in ("tap if visible close_btn", "verify element x is visible",
              "enter ${city} in box", "swipe left", "repeat_button tap"):
        assert classify(s) is None
    assert classify("If element x is visible then")[0] == "if"
    assert classify("end   for")[0] == "endloop"
    assert classify("Stop loop if ${x} is 3") == ("break", ("${x} is 3",))
    assert classify("skip to next round")[0] == "continue"


def test_comparisons():
    assert split_comparison("${city} is not Mumbai") == ("${city}", "is not", "Mumbai")
    assert compare("Pune", "is", "pune")
    assert compare("₹ 1,200", "is more than", "999")
    assert compare("1200", "is at least", "1200")
    assert compare("Hello World", "contains", '"world"')
    assert combine([("", False), ("or", True)]) and not combine([("", True), ("and", False)])
    assert split_logic('${a} is "x and y" and ${b} is 2') == [
        ("", '${a} is "x and y"'), ("and", "${b} is 2")]


def test_row_options():
    s = parse_row_options(" rows 2 to 5 where city is Mumbai")
    assert (s.start, s.end, s.where) == (2, 5, "city is Mumbai")


class FakeProbe:
    """Stands in for a page / app: element visibility and text, platform."""

    def __init__(self, plat="mobilesite", shown=(), texts=None):
        self.plat, self.shown, self.texts = plat, set(shown), texts or {}

    def visible(self, name, secs):
        return name in self.shown

    def exists(self, name):
        return name in self.shown

    def text(self, name):
        return self.texts.get(name, "")

    def page_value(self, what):
        return {"title": "Justdial - Local Search", "url": "https://www.justdial.com/x"}.get(what, "")

    def platform(self):
        return self.plat

    def environment(self):
        return "prot3.justdial.com"


def test_evaluator_conditions():
    from execution.control_flow import make_evaluator
    ev = make_evaluator(variables={"city": "Pune", "price": "₹ 1,200"},
                        probe=FakeProbe(shown={"login_popup"}, texts={"price_tag": "₹ 999"}))
    assert ev("element login_popup is visible")[0]
    assert ev("element banner is not visible")[0]
    assert not ev("login_popup is not visible")[0]
    assert ev("${city} is pune and ${price} is more than 1000")[0]
    assert ev("${city} is Mumbai or platform is mobilesite")[0]
    assert ev('page title contains "justdial"')[0]
    assert ev("element price_tag text contains 999")[0]
    assert ev("environment is prot3")[0]
    assert ev("${missing} is empty")[0]
    assert ev("not element banner is visible")[0]
    with pytest.raises(ValueError, match="Did not understand"):
        ev("banana split")


def test_evaluator_on_app_platforms():
    from execution.control_flow import make_evaluator
    ev = make_evaluator(variables={}, probe=FakeProbe(plat="android"))
    assert ev("platform is android")[0] and not ev("platform is ios")[0]
