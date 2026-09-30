"""Comparing values and sums — number-aware, any source (Test Data, stored, typed)."""
import pytest

from execution import value_ops as v
from nlp.parser import parse_step
from nlp.variable_manager import RUNTIME_VARIABLES as R


@pytest.fixture(autouse=True)
def _vars():
    R.clear()
    R.update({"price": "₹1,200", "expected": "1200.00", "code": "044", "code2": "44",
              "city": "Mumbai", "qty": "3", "blank": "", "testdata1": "New Delhi",
              "variabledata1": "New Delhi", "limit": "1000"})
    yield
    R.clear()


@pytest.mark.parametrize("step", [
    "verify ${testdata1} equals ${variabledata1}", "verify ${testdata1} = ${variabledata1}",
    "verify testdata1 equals variabledata1", "verify ${price} equals ${expected}",
    "verify price equals 1200", "verify ₹1,200 equals ${price}", "verify ${price} >= ${limit}",
    "verify ${price} is greater than ${limit}", 'verify ${city} starts with "mum" ignoring case',
    'verify city does not contain "Delhi"', 'verify city ends with "bai"', 'verify ${city} matches "^M\\w+i$"',
    "verify ${blank} is empty", "verify city is not empty", "verify qty is a number",
    "verify city is not a number", "verify ${code} != ${code2}",
])
def test_compare_passes(step):
    cmd = parse_step(step)
    assert cmd.type in ("compare_values", "verify_var_compare"), step
    v.execute(cmd)


@pytest.mark.parametrize("step,msg", [
    ("verify ${city} equals ${testdata1}", "expected it to equal"),
    ("verify price is less than 100", "expected less than 100"),
    ("verify city is greater than 5", "not a number"),
    ("verify nosuch equals 1", "not a stored value"),
    ("verify ${code} equals ${code2}", "expected it to equal"),
])
def test_compare_fails_clearly(step, msg):
    with pytest.raises(AssertionError, match=msg):
        v.execute(parse_step(step))


def test_existing_checks_number_aware():
    v.var_equals("price", "1200")
    v.var_equals("price", "1,200.00")
    with pytest.raises(AssertionError):
        v.var_equals("code", "44")
    v.var_compare("price", "greater than", "999")
    with pytest.raises(AssertionError):
        v.var_equals("price", "1200", negate=True)


@pytest.mark.parametrize("step,var,val", [
    ("calculate (${price} + 50) * ${qty} as t", "t", "3750"),
    ("calculate ${price} % 7 as r", "r", "3"),
    ("calculate qty ^ 2 as sq", "sq", "9"),
    ("calculate price - 1,000 as d", "d", "200"),
    ("calculate 18 percent of ${price} as gst", "gst", "216"),
    ("calculate 12.5% of price as p", "p", "150"),
    ('round "12.345" to 2 decimals as r2', "r2", "12.35"),
    ('round up "12.341" to 2 decimals as r3', "r3", "12.35"),
    ('round "12.349" down to 2 decimals as r4', "r4", "12.34"),
    ("round price as r5", "r5", "1200"),
    ('store length of "${city}" as n', "n", "6"),
])
def test_arithmetic(step, var, val):
    v.execute(parse_step(step))
    assert R[var] == val


def test_increase_decrease():
    v.execute(parse_step("increase qty by 2"))
    assert R["qty"] == "5"
    v.execute(parse_step("decrement qty"))
    assert R["qty"] == "4"


def test_unsafe_or_bad_sums_rejected():
    with pytest.raises(AssertionError, match="Division by zero"):
        v.execute(parse_step("calculate ${qty} / 0 as x"))
    with pytest.raises(Exception):
        v.calculate("__import__('os').system('x')")
