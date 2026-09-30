"""
execution/value_ops.py — comparing values and doing sums, the same on every platform.

Values can come from anywhere a test keeps them:
    ${testdata1}       Test Data, a data set column, or a value stored earlier
    testdata1          the same, written without ${ }
    "some text"        typed in the step
    1200 / ₹1,200      a number (currency signs, commas and spaces are ignored)

Equals is number-aware: "₹1,200", "1200" and "1200.00" are the same number. A
value with a leading zero ("044", an STD code) is compared as text, so codes
and ids never match by accident.

Nothing here touches the page, so the web runner and the app runner share it.
"""
from __future__ import annotations

import ast
import math
import operator
import re

from nlp.variable_manager import RUNTIME_VARIABLES, resolve_variables

_NUM_CLEAN = re.compile(r"(?i)^\s*(?:rs\.?|inr|₹|\$|€|£)?\s*|\s*(?:/-|rs\.?|inr)?\s*$")


class ValueError_(AssertionError):  # noqa: N801 — reads as "a value check failed"
    """A check that did not hold — reported like any failed step."""


def to_number(value) -> float | None:
    """'₹1,200' → 1200.0, '12.5%' → 12.5, 'abc' → None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = _NUM_CLEAN.sub("", str(value)).replace(",", "").replace(" ", "").rstrip("%")
    if not s or not re.fullmatch(r"[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?", s):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fmt_number(n: float) -> str:
    if n is None or (isinstance(n, float) and (math.isnan(n) or math.isinf(n))):
        raise ValueError_("The result is not a number (division by zero?).")
    if float(n).is_integer():
        return str(int(n))
    return f"{n:.10f}".rstrip("0").rstrip(".")


def _has_leading_zero(s: str) -> bool:
    t = str(s).strip()
    return len(t) > 1 and t[0] == "0" and t[1].isdigit()


def operand(token: str, side: str = "right") -> tuple[str, str]:
    """(value, how it was read) for one side of a comparison."""
    t = (token or "").strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        return str(resolve_variables(t[1:-1])) if "${" in t else t[1:-1], "text"
    if "${" in t:
        return str(resolve_variables(t)), "value"
    if t in RUNTIME_VARIABLES:
        return str(RUNTIME_VARIABLES[t]), "value"
    if to_number(t) is not None:
        return t, "number"
    if side == "left":
        raise ValueError_(f"'{t}' is not a stored value. Use ${{{t}}} after the step that stores it, "
                          f"save it under Test Data, or put text in quotes.")
    return t, "text"                      # a bare word on the right is plain text


def _label(token: str) -> str:
    t = (token or "").strip()
    if t.startswith("${") or re.fullmatch(r"[A-Za-z_]\w*", t):
        return t if t.startswith("${") else "${" + t + "}"
    return t


def equal(a: str, b: str, ignore_case: bool = False) -> bool:
    na, nb = to_number(a), to_number(b)
    if na is not None and nb is not None and not (_has_leading_zero(a) or _has_leading_zero(b)):
        return abs(na - nb) < 1e-9
    a, b = str(a).strip(), str(b).strip()
    return a.lower() == b.lower() if ignore_case else a == b


OPS = {
    "equals": "equal", "is": "equal", "is equal to": "equal", "is same as": "equal",
    "is the same as": "equal", "=": "equal", "==": "equal",
    "is not": "not equal", "does not equal": "not equal", "doesn't equal": "not equal",
    "not equals": "not equal", "is not equal to": "not equal", "!=": "not equal",
    "contains": "contains", "does not contain": "not contains", "doesn't contain": "not contains",
    "starts with": "starts", "does not start with": "not starts",
    "ends with": "ends", "does not end with": "not ends",
    "matches": "matches", "does not match": "not matches",
    "is greater than": "gt", "greater than": "gt", "is more than": "gt", "more than": "gt",
    "is above": "gt", ">": "gt",
    "is less than": "lt", "less than": "lt", "is below": "lt", "is fewer than": "lt", "<": "lt",
    "is at least": "ge", "at least": "ge", ">=": "ge", "is greater than or equal to": "ge",
    "is at most": "le", "at most": "le", "<=": "le", "is less than or equal to": "le",
    "is empty": "empty", "is not empty": "not empty",
    "is a number": "number", "is not a number": "not number",
}
_NO_RIGHT = {"empty", "not empty", "number", "not number"}


def compare(left: str, op: str, right: str = "", ignore_case: bool = False) -> str:
    """Raise when the comparison does not hold; return a one-line 'ok' message."""
    kind = OPS.get(re.sub(r"\s+", " ", (op or "").strip().lower()))
    if not kind:
        raise ValueError(f"Unknown comparison '{op}'.")
    a, _ = operand(left, "left")
    b = ""
    if kind not in _NO_RIGHT:
        b, _ = operand(right, "right")
    la, lb = _label(left), _label(right)
    A, B = (a.lower(), b.lower()) if ignore_case else (a, b)

    if kind in ("gt", "lt", "ge", "le"):
        na, nb = to_number(a), to_number(b)
        if na is None or nb is None:
            bad = la if na is None else lb
            val = a if na is None else b
            raise ValueError_(f"{bad} is {val!r} — not a number, so it can't be compared as one.")
        ok = {"gt": na > nb, "lt": na < nb, "ge": na >= nb, "le": na <= nb}[kind]
        words = {"gt": "greater than", "lt": "less than", "ge": "at least", "le": "at most"}[kind]
        if not ok:
            raise ValueError_(f"{la} is {fmt_number(na)} — expected {words} {fmt_number(nb)}.")
        return f"{la} ({fmt_number(na)}) is {words} {fmt_number(nb)}"

    checks = {
        "equal": lambda: equal(a, b, ignore_case),
        "not equal": lambda: not equal(a, b, ignore_case),
        "contains": lambda: B in A, "not contains": lambda: B not in A,
        "starts": lambda: A.startswith(B), "not starts": lambda: not A.startswith(B),
        "ends": lambda: A.endswith(B), "not ends": lambda: not A.endswith(B),
        "matches": lambda: re.search(b, a, re.I if ignore_case else 0) is not None,
        "not matches": lambda: re.search(b, a, re.I if ignore_case else 0) is None,
        "empty": lambda: not a.strip(), "not empty": lambda: bool(a.strip()),
        "number": lambda: to_number(a) is not None, "not number": lambda: to_number(a) is None,
    }
    if not checks[kind]():
        want = {"equal": "to equal", "not equal": "not to equal", "contains": "to contain",
                "not contains": "not to contain", "starts": "to start with",
                "not starts": "not to start with", "ends": "to end with",
                "not ends": "not to end with", "matches": "to match",
                "not matches": "not to match", "empty": "to be empty",
                "not empty": "not to be empty", "number": "to be a number",
                "not number": "not to be a number"}[kind]
        rhs = "" if kind in _NO_RIGHT else f" {b!r}" + (f" ({lb})" if lb != b and not lb.startswith('"') else "")
        raise ValueError_(f"{la} is {a!r} — expected it {want}{rhs}"
                          + (" (ignoring case)" if ignore_case else "") + ".")
    return f"{la} {op} {b!r}" if kind not in _NO_RIGHT else f"{la} {op}"


# ── arithmetic ────────────────────────────────────────────────────────────────
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow,
        ast.FloorDiv: operator.floordiv}


def _num_of(name: str) -> float:
    raw = RUNTIME_VARIABLES.get(name)
    if raw is None:
        raise ValueError_(f"${{{name}}} is not stored yet.")
    n = to_number(raw)
    if n is None:
        raise ValueError_(f"${{{name}}} is {raw!r} — not a number.")
    return n


def calculate(expression: str) -> float:
    """'(${price} + 50) * 2 % 7' → number. Only numbers, values and + - * / % ( ) ^."""
    names: list[str] = []

    def _slot(m):
        names.append(m.group(1))
        return f" __v_{len(names) - 1}__ "
    expr = re.sub(r"\$\{([^}]+)\}", _slot, expression)
    expr = expr.replace("^", "**").replace("×", "*").replace("÷", "/")
    expr = re.sub(r"(?<![\w.])(?:₹|rs\.?\s*)(\d)", r"\1", expr, flags=re.I)
    expr = re.sub(r"(?<=\d),(?=\d{3}\b)", "", expr).strip()  # 1,200 → 1200
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise ValueError(f"Can't read the sum '{expression}'. Use numbers, values and + - * / % ( ).") from None

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            l, r = ev(node.left), ev(node.right)
            if isinstance(node.op, (ast.Div, ast.Mod, ast.FloorDiv)) and r == 0:
                raise ValueError_("Division by zero in the sum.")
            return _BIN[type(node.op)](l, r)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.Name):
            m = re.fullmatch(r"__v_(\d+)__", node.id)
            return _num_of(names[int(m.group(1))] if m else node.id)
        raise ValueError(f"Can't read the sum '{expression}'. Use numbers, values and + - * / % ( ).")
    return ev(tree)



def store(variable: str, number: float) -> str:
    text = fmt_number(number)
    RUNTIME_VARIABLES[variable] = text
    return text


def round_number(value: str, decimals: int = 0, mode: str = "") -> float:
    n = to_number(operand(value, "left")[0])
    if n is None:
        raise ValueError_(f"{_label(value)} is not a number, so it can't be rounded.")
    f = 10 ** decimals
    if mode == "up":
        return math.ceil(n * f) / f
    if mode == "down":
        return math.floor(n * f) / f
    return math.floor(n * f + 0.5) / f if n >= 0 else -math.floor(-n * f + 0.5) / f


def round_text(n: float, decimals: int) -> str:
    return f"{n:.{decimals}f}" if decimals else fmt_number(n)


def percent_of(pct: str, value: str) -> float:
    p = to_number(operand(pct, "left")[0])
    v = to_number(operand(value, "left")[0])
    if p is None or v is None:
        raise ValueError_("Both the percentage and the value must be numbers.")
    return v * p / 100.0


def adjust(variable: str, by: str) -> str:
    cur = _num_of(variable)
    d = to_number(operand(by, "right")[0])
    if d is None:
        raise ValueError_(f"'{by}' is not a number.")
    return store(variable, cur + d)


# ── step execution (shared by the web and app runners) ────────────────────────
#: Steps read BEFORE ${…} is replaced in the step text: the engine looks the
#: values up itself, so "New Delhi" or "₹1,200" never breaks the step apart.
RAW_TYPES = {"compare_values", "calc_expr", "round_value", "percent_of", "adjust_var",
             "store_length", "verify_var_compare", "math"}


def execute(cmd) -> str:
    t = cmd.type
    if t == "compare_values":
        return compare(cmd.target, cmd.text, (cmd.values or [""])[0],
                       ignore_case=len(cmd.values or []) > 1 and cmd.values[1] == "ignore_case")
    if t == "verify_var_compare":           # verify price is greater than ${limit}
        op = cmd.text if cmd.text.startswith("is ") else f"is {cmd.text}"
        return compare(cmd.target, op, (cmd.values or [""])[0])
    if t == "math":                         # calculate a + b as c (the original two-number form)
        expr = f"{cmd.target} {cmd.text} {(cmd.values or [''])[0]}"
        res = store(cmd.variable_name, calculate(_as_expr(expr)))
        return f"{expr} = {res} → ${{{cmd.variable_name}}}"
    if t == "calc_expr":
        res = store(cmd.variable_name, calculate(cmd.text))
        return f"{cmd.text} = {res} → ${{{cmd.variable_name}}}"
    if t == "round_value":
        n = round_number(cmd.text, int(cmd.count or 0), (cmd.values or [""])[0])
        RUNTIME_VARIABLES[cmd.variable_name] = round_text(n, int(cmd.count or 0))
        return f"rounded {_label(cmd.text)} → {RUNTIME_VARIABLES[cmd.variable_name]}"
    if t == "percent_of":
        res = store(cmd.variable_name, percent_of(cmd.text, cmd.values[0]))
        return f"{cmd.text}% of {_label(cmd.values[0])} = {res}"
    if t == "adjust_var":
        return f"${{{cmd.target}}} is now {adjust(cmd.target, cmd.text)}"
    if t == "store_length":
        val, _ = operand(cmd.text, "left")
        RUNTIME_VARIABLES[cmd.variable_name] = str(len(val))
        return f"length of {_label(cmd.text)} = {len(val)}"
    raise ValueError(f"Not a value step: {t}")


def _as_expr(expr: str) -> str:
    """Operands of the old form may be ${x}, a bare name, or ₹1,200 — make them readable."""
    def fix(tok):
        if tok.startswith("${") or tok in "+-*/%":
            return tok
        n = to_number(tok)
        return fmt_number(n) if n is not None else tok
    return " ".join(fix(t) for t in expr.split())


def var_equals(name: str, expected: str, ignore_case: bool = False, negate: bool = False) -> None:
    """verify stored <name> equals / is not "<value>" — number-aware (₹1,200 = 1200)."""
    if name not in RUNTIME_VARIABLES:
        raise Exception(f"❌ Variable '{name}' is not stored yet. Did you run the store step first?")
    stored = str(RUNTIME_VARIABLES[name])
    same = equal(stored, str(expected), ignore_case)
    if negate and same:
        raise ValueError_(f"${{{name}}} is {stored!r} — expected it NOT to equal {expected!r}.")
    if not negate and not same:
        raise ValueError_(f"${{{name}}} is {stored!r} — expected {expected!r}"
                          + (" (ignoring case)" if ignore_case else "") + ".")


def var_compare(name: str, op: str, other: str) -> None:
    """verify stored <name> is greater than / less than / at least / at most <other>."""
    if name not in RUNTIME_VARIABLES:
        raise Exception(f"❌ Variable '{name}' is not stored yet.")
    compare(name, op if op.startswith("is ") else f"is {op}", other)
