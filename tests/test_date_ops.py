from datetime import date

import pytest

from execution import date_ops as d
from execution import value_ops as vo
from nlp.parser import parse_step
from nlp.variable_manager import RUNTIME_VARIABLES as R

FRI = date(2026, 10, 2)
SAT = date(2026, 10, 3)


@pytest.mark.parametrize("phrase,base,want", [
    ("today", FRI, FRI), ("tomorrow", FRI, SAT), ("next day", FRI, SAT),
    ("next week", FRI, date(2026, 10, 9)), ("next month", FRI, date(2026, 11, 2)),
    ("next month", date(2027, 1, 31), date(2027, 2, 28)),
    ("next working day", FRI, date(2026, 10, 5)), ("next working day", SAT, date(2026, 10, 5)),
    ("previous working day", date(2026, 10, 5), FRI),
    ("next weekend", FRI, SAT), ("next weekend", SAT, date(2026, 10, 10)),
    ("next weekend", date(2026, 10, 4), date(2026, 10, 10)),
    ("next saturday", SAT, date(2026, 10, 10)), ("this saturday", SAT, SAT),
    ("next sunday", FRI, date(2026, 10, 4)), ("in 3 days", FRI, date(2026, 10, 5)),
    ("3 working days from today", FRI, date(2026, 10, 7)), ("5 days ago", FRI, date(2026, 9, 27)),
    ("first working day of next month", FRI, date(2026, 11, 2)),
    ("last day of this month", FRI, date(2026, 10, 31)),
    ("last working day of this month", FRI, date(2026, 10, 30)),
    ("15/10/2026 + 2 working days", FRI, date(2026, 10, 19)),
    ("15 Oct 2026", FRI, date(2026, 10, 15)), ("2026-10-15", FRI, date(2026, 10, 15)),
])
def test_resolve(phrase, base, want):
    assert d.resolve(phrase, base) == want


def test_format_tokens():
    assert d.fmt(date(2026, 10, 3)) == "03/10/2026"
    assert d.fmt(date(2026, 10, 3), "dddd, Do MMMM YYYY") == "Saturday, 3rd October 2026"
    assert d.fmt(date(2026, 10, 3), "dd-mm-yyyy") == "03-10-2026"
    assert d.pattern_from_placeholder("dd-mm-yyyy") == "DD-MM-YYYY"


def test_bad_date_is_explained():
    with pytest.raises(ValueError, match="Can't read the date"):
        d.resolve("banana")


@pytest.mark.parametrize("step,typ", [
    ('select date "next working day" in checkin_date', "select_date"),
    ("select next weekend in travel_date", "select_date"),
    ('click date "next saturday" in calendar', "select_date"),
    ("store date from checkin_date as got", "store_date_from"),
    ('store date "next month" as nm', "store_date"),
    ("store next weekend as w", "store_date"),
    ("add 3 working days to ${d} as d2", "date_add"),
    ("calculate days between ${a} and ${b} as n", "date_diff"),
    ("verify date in checkin_date is next working day", "verify_date_in"),
    ("verify ${d} is a working day", "verify_date_value"),
    ('store "New Delhi" as city', "create_variable"),
    ('store "2026-10-15" as raw', "create_variable"),
    ("calculate ${price} * ${qty} as total", "math"),
])
def test_parse(step, typ):
    assert parse_step(step).type == typ


def test_steps_run_and_fail_clearly():
    R.clear()
    vo.execute(parse_step('store date "02/10/2026" + 1 working day as d'))
    assert R["d"] == "05/10/2026"
    vo.execute(parse_step('add 1 month to ${d} as m in "DD MMM YYYY"'))
    assert R["m"] == "05 Nov 2026"
    vo.execute(parse_step("calculate days between ${d} and ${m} as n"))
    assert R["n"] == "31"
    vo.execute(parse_step("verify ${d} is monday"))
    with pytest.raises(AssertionError, match="expected it to be a weekend"):
        vo.execute(parse_step("verify ${d} is a weekend"))
    with pytest.raises(AssertionError, match="expected it after"):
        vo.execute(parse_step("verify date ${d} is after ${m}"))
