"""
execution/control_flow.py — if / else, loops and data-driven rounds, in plain steps.

    if element login_popup is visible
        tap login_popup_close
    else if ${user_type} is seller
        tap seller_dashboard_tab
    else
        tap buyer_home_tab
    end if

    for each row in city_list rows 2 to 5 where platform is mobilesite
        enter ${city} in search_city_box
        stop loop if element no_results_text is visible
    end for

    repeat 3 times                     repeat until element load_more is not visible (max 20 times)
        swipe left                         tap load_more
    end repeat                         end repeat

Block lines are not actions: they decide which of the lines under them run,
and how often. Everything else in a flow is unchanged — a flow with no blocks
runs exactly as before, line by line.

How it runs
-----------
FlowProgram checks the structure once, up front (every `if` has its `end if`,
`else` is inside an `if`, `stop loop` is inside a loop …) and reports the line
of the first mistake, so a half-written block fails before the browser opens
rather than half-way through a run.

The runner then walks the program with `steps()`, which yields one line at a
time. Action lines are executed by the caller exactly as before; block lines
are handed back to `decide()`, which evaluates the condition, moves the
position and returns a short sentence for the log ("yes — element
login_popup is visible"). The caller keeps its own reporting, screenshots and
stop-on-failure logic untouched.

Loop values
-----------
Inside `for each row`, every column of the current row is a ${value} named by
its heading (see core/datasets.column_name), plus ${row_number}. They shadow a
Test Data value with the same name only inside the loop; the previous values
are put back when the loop ends. `repeat` exposes ${round}.

Every loop is capped (50 rounds unless `(max N times)` says otherwise) so a
condition that never comes true fails the test instead of running forever.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

DEFAULT_MAX_ROUNDS = 50
HARD_MAX_ROUNDS = 1000


class FlowStructureError(ValueError):
    """A block that is not closed, or a line that is outside the block it needs."""


# ─────────────────────────────────────────────────────────────────────────────
# Recognising block lines
# ─────────────────────────────────────────────────────────────────────────────
_MAX = r"(?:\s*\(?\s*max(?:imum)?\s+(\d+)\s*(?:times?|rounds?)?\s*\)?)?"

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("elif",    re.compile(r"^(?:else\s*if|elseif|otherwise\s+if)\s+(.+?)(?:\s+then)?$", re.I)),
    ("else",    re.compile(r"^(?:else|otherwise)$", re.I)),
    ("endif",   re.compile(r"^end\s*if$", re.I)),
    ("if",      re.compile(r"^if\s+(.+?)(?:\s+then)?$", re.I)),
    ("for",     re.compile(r"^for\s+each\s+row\s+(?:in|of|from)\s+([A-Za-z_][\w.]*)(.*)$", re.I)),
    ("times",   re.compile(r"^repeat\s+(\d+)\s+times?$", re.I)),
    ("until",   re.compile(r"^repeat\s+until\s+(.+?)" + _MAX + r"$", re.I)),
    ("while",   re.compile(r"^repeat\s+while\s+(.+?)" + _MAX + r"$", re.I)),
    ("endloop", re.compile(r"^end\s*(?:for|repeat|loop)$", re.I)),
    ("break",   re.compile(r"^(?:stop|exit|break)(?:\s+the)?\s+loop(?:\s+if\s+(.+))?$", re.I)),
    ("continue", re.compile(r"^(?:skip\s+to\s+(?:the\s+)?next\s+(?:row|round)|continue\s+loop)"
                            r"(?:\s+if\s+(.+))?$", re.I)),
]

#: What a block line is called in messages and the editor.
LABELS = {"if": "if", "elif": "else if", "else": "else", "endif": "end if",
          "for": "for each row", "times": "repeat", "until": "repeat until",
          "while": "repeat while", "endloop": "end", "break": "stop loop",
          "continue": "skip to next"}

LOOP_KINDS = ("for", "times", "until", "while")


def classify(step: str) -> tuple[str, tuple] | None:
    """('if', (condition,)) for a block line, None for an ordinary step."""
    s = re.sub(r"\s+", " ", (step or "").strip())
    for kind, rx in _PATTERNS:
        m = rx.match(s)
        if m:
            return kind, m.groups()
    return None


def is_block_line(step: str) -> bool:
    return classify(step) is not None


# ── for-each options: rows 2 to 5 · from row 3 · first 10 rows · where … ──────
_RANGE_RX = [
    (re.compile(r"\brows?\s+(\d+)\s*(?:to|-|till|until|through)\s*(\d+)\b", re.I), "range"),
    (re.compile(r"\bfrom\s+row\s+(\d+)(?:\s+to\s+(\d+))?\b", re.I), "range"),
    (re.compile(r"\bfirst\s+(\d+)\s+rows?\b", re.I), "first"),
    (re.compile(r"\blast\s+(\d+)\s+rows?\b", re.I), "last"),
    (re.compile(r"\bonly\s+row\s+(\d+)\b|\brow\s+(\d+)\s+only\b", re.I), "one"),
]


@dataclass
class RowSelection:
    start: int = 1
    end: int | None = None
    first: int | None = None
    last: int | None = None
    where: str = ""

    def describe(self) -> str:
        bits = []
        if self.first:
            bits.append(f"first {self.first} rows")
        elif self.last:
            bits.append(f"last {self.last} rows")
        elif self.start != 1 or self.end:
            bits.append(f"rows {self.start} to {self.end or 'end'}")
        if self.where:
            bits.append(f"where {self.where}")
        return ", ".join(bits) or "all rows"


def parse_row_options(rest: str) -> RowSelection:
    sel = RowSelection()
    text = (rest or "").strip()
    where = re.search(r"\bwhere\s+(.+)$", text, re.I)
    if where:
        sel.where = where.group(1).strip()
        text = text[:where.start()].strip()
    for rx, kind in _RANGE_RX:
        m = rx.search(text)
        if not m:
            continue
        if kind == "range":
            sel.start = int(m.group(1))
            sel.end = int(m.group(2)) if m.group(2) else None
        elif kind == "first":
            sel.first = int(m.group(1))
        elif kind == "last":
            sel.last = int(m.group(1))
        elif kind == "one":
            n = int(m.group(1) or m.group(2))
            sel.start = sel.end = n
        text = (text[:m.start()] + text[m.end():]).strip()
    text = re.sub(r"^(?:,|and)\s*", "", text).strip()
    if text:
        raise FlowStructureError(
            f"Did not understand '{text}' after the data set name. Use e.g. "
            "'rows 2 to 5', 'from row 3', 'first 10 rows' or 'where city is Mumbai'.")
    if sel.start < 1 or (sel.end is not None and sel.end < sel.start):
        raise FlowStructureError("Row numbers start at 1, and 'rows A to B' needs A ≤ B.")
    return sel


def select_rows(all_rows: list[dict], sel: RowSelection, evaluate_where=None) -> list[dict]:
    if sel.first:
        chosen = all_rows[:sel.first]
    elif sel.last:
        chosen = all_rows[-sel.last:]
    else:
        chosen = all_rows[sel.start - 1: sel.end]
    if sel.where and evaluate_where is not None:
        chosen = [r for r in chosen if evaluate_where(sel.where, r)]
    return chosen


# ─────────────────────────────────────────────────────────────────────────────
# Conditions
# ─────────────────────────────────────────────────────────────────────────────
_NUM_OPS = {
    "is more than": lambda a, b: a > b, "is greater than": lambda a, b: a > b,
    "is above": lambda a, b: a > b, "is less than": lambda a, b: a < b,
    "is below": lambda a, b: a < b, "is at least": lambda a, b: a >= b,
    "is at most": lambda a, b: a <= b,
}
_TEXT_OPS = {
    "does not contain": lambda a, b: b.lower() not in a.lower(),
    "doesn't contain": lambda a, b: b.lower() not in a.lower(),
    "contains": lambda a, b: b.lower() in a.lower(),
    "starts with": lambda a, b: a.lower().startswith(b.lower()),
    "ends with": lambda a, b: a.lower().endswith(b.lower()),
    "is not": lambda a, b: a.strip().lower() != b.strip().lower(),
    "equals": lambda a, b: a.strip().lower() == b.strip().lower(),
    "is": lambda a, b: a.strip().lower() == b.strip().lower(),
}
_OP_ORDER = sorted(list(_NUM_OPS) + list(_TEXT_OPS), key=len, reverse=True)


def _unquote(v: str) -> str:
    v = (v or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def split_logic(cond: str) -> list[tuple[str, str]]:
    """'a and b or c' → [('', a), ('and', b), ('or', c)] — quotes respected."""
    parts, buf, joiner, quote = [], "", "", ""
    tokens = re.split(r"(\s+(?:and|or)\s+|[\"'])", cond, flags=re.I)
    for tok in tokens:
        if tok in ('"', "'"):
            quote = "" if quote == tok else (quote or tok)
            buf += tok
        elif re.fullmatch(r"\s+(?:and|or)\s+", tok or "", re.I) and not quote:
            parts.append((joiner, buf.strip()))
            joiner, buf = tok.strip().lower(), ""
        else:
            buf += tok or ""
    parts.append((joiner, buf.strip()))
    return [p for p in parts if p[1]]


def combine(results: list[tuple[str, bool]]) -> bool:
    """Left to right, 'and' binding tighter than 'or' (as people read it)."""
    groups: list[bool] = []
    for joiner, value in results:
        if joiner == "or" or not groups:
            groups.append(value)
        else:
            groups[-1] = groups[-1] and value
    return any(groups)


def compare(left: str, op: str, right: str) -> bool:
    left, right = str(left), _unquote(right)
    if op in _NUM_OPS:
        try:
            a = float(re.sub(r"[^\d.\-]", "", left) or "nan")
            b = float(re.sub(r"[^\d.\-]", "", right) or "nan")
        except ValueError:
            raise ValueError(f"'{left}' {op} '{right}': both sides must be numbers") from None
        if a != a or b != b:
            raise ValueError(f"'{left}' {op} '{right}': both sides must be numbers")
        return _NUM_OPS[op](a, b)
    return _TEXT_OPS[op](left, right)


def split_comparison(text: str) -> tuple[str, str, str] | None:
    """'${city} is not Mumbai' → ('${city}', 'is not', 'Mumbai')."""
    m = re.match(r"^(.+?)\s+is\s+(not\s+)?empty$", text, re.I)
    if m:
        return m.group(1).strip(), "is not empty" if m.group(2) else "is empty", ""
    low = text.lower()
    for op in _OP_ORDER:
        idx = low.find(f" {op} ")
        if idx > 0:
            return text[:idx].strip(), op, text[idx + len(op) + 2:].strip()
    return None


# ─────────────────────────────────────────────────────────────────────────────
# The program
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Line:
    index: int                 # position in the program
    line_no: int               # line in the file (1-based)
    text: str
    kind: str | None = None    # None = ordinary step
    args: tuple = ()
    depth: int = 0
    # filled by the structure check
    end: int | None = None           # if / loop: index of the closing line
    branches: list = field(default_factory=list)   # if: [if, elif…, else]
    loop: int | None = None          # break / continue / endloop: loop header


@dataclass
class _Frame:
    header: int
    kind: str
    rows: list = field(default_factory=list)
    position: int = 0          # rows done / rounds done
    limit: int = 0
    saved: dict = field(default_factory=dict)
    name: str = ""


class FlowProgram:
    """
    Parameters
    ----------
    lines         the flow's lines (raw file lines are fine; blanks and '#' are skipped)
    evaluate      callable(condition_text) -> (bool, explanation) — page-aware,
                  supplied by the runner (see make_evaluator)
    variables     the runtime variable store (loop values are written here)
    load_rows     callable(dataset_name) -> list[dict] (core.datasets.rows)
    """

    def __init__(self, lines, evaluate=None, variables=None, load_rows=None,
                 numbered: list[tuple[int, str]] | None = None):
        if numbered is None:
            numbered = [(n, raw.strip()) for n, raw in enumerate(lines, 1)]
        self.items: list[Line] = []
        for n, text in numbered:
            text = (text or "").strip()
            if not text or text.startswith("#"):
                continue
            c = classify(text)
            self.items.append(Line(len(self.items), n, text,
                                   kind=c[0] if c else None, args=c[1] if c else ()))
        self.evaluate = evaluate
        self.variables = variables if variables is not None else {}
        self.load_rows = load_rows
        self._frames: list[_Frame] = []
        self._next: int | None = None
        self._check()

    # ── structure ────────────────────────────────────────────────────────────
    @property
    def has_blocks(self) -> bool:
        return any(i.kind for i in self.items)

    def _check(self) -> None:
        stack: list[Line] = []
        for it in self.items:
            it.depth = len(stack)
            k = it.kind
            if k in ("if",) + LOOP_KINDS:
                if k == "for":
                    parse_row_options(it.args[1])          # fail early on bad options
                if k in ("until", "while") and it.args[1]:
                    if int(it.args[1]) > HARD_MAX_ROUNDS:
                        raise FlowStructureError(
                            f"Line {it.line_no}: max {HARD_MAX_ROUNDS} rounds.")
                if k == "if":
                    it.branches = [it.index]
                stack.append(it)
            elif k in ("elif", "else"):
                top = stack[-1] if stack else None
                if top is None or top.kind != "if":
                    raise FlowStructureError(
                        f"Line {it.line_no}: '{LABELS[k]}' must be inside an 'if' block.")
                if self.items[top.branches[-1]].kind == "else":
                    raise FlowStructureError(
                        f"Line {it.line_no}: nothing can follow 'else' in the same 'if' "
                        "block except 'end if'.")
                top.branches.append(it.index)
                it.depth = len(stack) - 1
            elif k == "endif":
                if not stack or stack[-1].kind != "if":
                    raise FlowStructureError(self._mismatch(it, stack, "if"))
                top = stack.pop()
                top.end = it.index
                it.depth = len(stack)
                for b in top.branches:
                    self.items[b].end = it.index
            elif k == "endloop":
                if not stack or stack[-1].kind not in LOOP_KINDS:
                    raise FlowStructureError(self._mismatch(it, stack, "loop"))
                top = stack.pop()
                top.end = it.index
                it.loop = top.index
                it.depth = len(stack)
            elif k in ("break", "continue"):
                loop = next((s for s in reversed(stack) if s.kind in LOOP_KINDS), None)
                if loop is None:
                    raise FlowStructureError(
                        f"Line {it.line_no}: '{it.text}' only works inside a loop "
                        "('for each row' or 'repeat').")
                it.loop = loop.index
        if stack:
            top = stack[-1]
            closer = "end if" if top.kind == "if" else (
                "end for" if top.kind == "for" else "end repeat")
            raise FlowStructureError(
                f"Line {top.line_no}: '{top.text}' is never closed — add '{closer}' "
                "after the steps that belong to it.")

    @staticmethod
    def _mismatch(it: Line, stack: list[Line], wanted: str) -> str:
        if stack:
            open_ = stack[-1]
            closer = "end if" if open_.kind == "if" else "end for / end repeat"
            return (f"Line {it.line_no}: '{it.text}' does not match the open block "
                    f"'{open_.text}' (line {open_.line_no}) — close it with '{closer}' first.")
        return f"Line {it.line_no}: '{it.text}' has no matching {wanted} above it."

    # ── walking ──────────────────────────────────────────────────────────────
    def steps(self):
        """Yield Line items to run. Block lines must be passed to decide()."""
        pc = 0
        while 0 <= pc < len(self.items):
            it = self.items[pc]
            if it.kind in ("endif", "else", "elif"):
                # Reached by falling out of a branch that ran: the block is done.
                # Only an 'else if' that a false condition jumped to is checked.
                if it.kind != "elif" or self._arrived_by_jump != pc:
                    pc = (it.end if it.kind != "endif" else pc) + 1
                    self._arrived_by_jump = None
                    continue
            if it.kind == "endloop":
                pc = self._loop_end(it)
                continue
            self._next = None
            yield it
            if it.kind is None:
                pc += 1
            else:
                if self._next is None:
                    # The condition could not be checked (its step failed and the
                    # runner reported it). Skip the whole block rather than
                    # abandoning every step after it.
                    end = getattr(it, "end", None)
                    pc = (end + 1) if isinstance(end, int) and end > pc else pc + 1
                    self._arrived_by_jump = None
                    continue
                pc = self._next

    _arrived_by_jump: int | None = None

    def _jump(self, target: int) -> None:
        self._next = target
        self._arrived_by_jump = target

    def _cond(self, text: str) -> tuple[bool, str]:
        if self.evaluate is None:
            raise RuntimeError("No condition evaluator configured")
        return self.evaluate(text)

    def decide(self, it: Line) -> str:
        """Evaluate a block line, set where to go next, return a log sentence."""
        k = it.kind
        self._arrived_by_jump = None
        if k in LOOP_KINDS and self._pending_round == it.index:
            return self._next_round(it)
        if k in ("if", "elif"):
            ok, why = self._cond(it.args[0])
            if ok:
                self._next = it.index + 1
                return f"yes — {why} → running the steps under it"
            owner = self.items[self._if_of(it)]
            later = owner.branches[owner.branches.index(it.index) + 1:]
            if later:
                nxt = self.items[later[0]]
                if nxt.kind == "else":
                    self._next = nxt.index + 1
                    return f"no — {why} → running the 'else' steps"
                self._jump(nxt.index)
                return f"no — {why} → checking '{nxt.text}'"
            self._next = owner.end + 1
            return f"no — {why} → skipping the block"
        if k == "else":
            self._next = it.index + 1
            return "running the 'else' steps"
        if k in LOOP_KINDS:
            return self._loop_head(it)
        if k in ("break", "continue"):
            cond = it.args[0]
            if cond:
                ok, why = self._cond(cond)
                if not ok:
                    self._next = it.index + 1
                    return f"no — {why} → carrying on"
            loop = self.items[it.loop]
            if k == "break":
                self._unwind_to(loop.index)
                self._next = loop.end + 1
                return (f"{'yes — ' + why + ' → ' if cond else ''}stopping the loop "
                        f"'{loop.text}'")
            self._unwind_to(loop.index, keep=True)
            self._next = loop.end        # the end line moves to the next round
            return f"{'yes — ' + why + ' → ' if cond else ''}skipping to the next round"
        raise RuntimeError(f"Unknown block line {it.text!r}")

    def _if_of(self, it: Line) -> int:
        for cand in self.items[:it.index + 1][::-1]:
            if cand.kind == "if" and it.index in cand.branches:
                return cand.index
        return it.index

    # ── loops ────────────────────────────────────────────────────────────────
    def _frame(self, header: int) -> _Frame | None:
        return self._frames[-1] if self._frames and self._frames[-1].header == header else None

    def _set_vars(self, frame: _Frame, values: dict) -> None:
        for k, v in values.items():
            if k not in frame.saved:
                frame.saved[k] = self.variables.get(k, _MISSING)
            self.variables[k] = v

    def _restore(self, frame: _Frame) -> None:
        for k, v in frame.saved.items():
            if v is _MISSING:
                self.variables.pop(k, None)
            else:
                self.variables[k] = v

    def _unwind_to(self, header: int, keep: bool = False) -> None:
        while self._frames and self._frames[-1].header != header:
            self._restore(self._frames.pop())
        if not keep and self._frames and self._frames[-1].header == header:
            self._restore(self._frames.pop())

    def _loop_head(self, it: Line) -> str:
        k = it.kind
        frame = self._frame(it.index)
        if k == "for":
            name, rest = it.args
            sel = parse_row_options(rest)
            if self.load_rows is None:
                raise RuntimeError("Data sets are not available here")
            all_rows = self.load_rows(name)
            rows = select_rows(all_rows, sel, self._where)
            if not rows:
                self._next = it.end + 1
                return (f"data set '{name}' has no rows for {sel.describe()} "
                        f"({len(all_rows)} rows in total) → skipping the loop")
            frame = _Frame(it.index, k, rows=rows, name=name)
            self._frames.append(frame)
            return self._start_row(it, frame)
        if k == "times":
            n = int(it.args[0])
            if n <= 0:
                self._next = it.end + 1
                return "repeat 0 times → skipping"
            if n > HARD_MAX_ROUNDS:
                raise ValueError(f"repeat is limited to {HARD_MAX_ROUNDS} times")
            frame = _Frame(it.index, k, limit=n)
            self._frames.append(frame)
            self._set_vars(frame, {"round": "1"})
            self._next = it.index + 1
            return f"round 1 of {n}"
        # until / while: the header is re-checked before every round
        cond, cap = it.args
        limit = int(cap) if cap else DEFAULT_MAX_ROUNDS
        if frame is None:
            frame = _Frame(it.index, k, limit=limit)
            self._frames.append(frame)
        ok, why = self._cond(cond)
        done = ok if k == "until" else not ok
        if done:
            rounds = frame.position
            self._unwind_to(it.index)
            self._next = it.end + 1
            return f"{why} → loop finished after {rounds} round(s)"
        if frame.position >= frame.limit:
            self._unwind_to(it.index)
            raise AssertionError(
                f"'{it.text}': still not finished after {frame.limit} rounds "
                f"(last check: {why}). Raise the limit with '(max N times)' if "
                "more rounds are expected.")
        frame.position += 1
        self._set_vars(frame, {"round": str(frame.position)})
        self._next = it.index + 1
        return f"round {frame.position} — {why}"

    def _start_row(self, it: Line, frame: _Frame) -> str:
        row = frame.rows[frame.position]
        self._set_vars(frame, row)
        self._next = it.index + 1
        return (f"row {row.get('row_number')} ({frame.position + 1} of {len(frame.rows)}) "
                f"— {describe_row(row)}")

    def _loop_end(self, end: Line) -> int:
        header = self.items[end.loop]
        frame = self._frame(header.index)
        if frame is None:            # loop was skipped / already unwound
            return end.index + 1
        if header.kind in ("for", "times"):
            frame.position += 1
            more = len(frame.rows) if header.kind == "for" else frame.limit
            if frame.position < more:
                # The header is yielded again so the log and the report show
                # "row 3 …" before that round's steps.
                self._pending_round = header.index
                return header.index
        else:
            return header.index      # until / while re-check their condition
        self._unwind_to(header.index)
        return end.index + 1

    _pending_round: int | None = None

    def _next_round(self, it: Line) -> str:
        self._pending_round = None
        frame = self._frame(it.index)
        if it.kind == "for":
            return self._start_row(it, frame)
        self._set_vars(frame, {"round": str(frame.position + 1)})
        self._next = it.index + 1
        return f"round {frame.position + 1} of {frame.limit}"

    def _where(self, cond: str, row: dict) -> bool:
        results = []
        for joiner, part in split_logic(cond):
            parsed = split_comparison(part)
            if parsed is None:
                raise FlowStructureError(
                    f"'where {cond}': write it as '<column> is <value>', e.g. "
                    "'where city is Mumbai' or 'where price is more than 500'.")
            col, op, value = parsed
            key = re.sub(r"^\$\{|\}$", "", col).strip()
            from core.datasets import column_name
            key = column_name(key)
            if key not in row:
                raise FlowStructureError(
                    f"'where {cond}': no column '{col}'. Columns: "
                    f"{', '.join(k for k in row if k != 'row_number')}.")
            cell = row.get(key, "")
            if op == "is empty":
                results.append((joiner, not cell.strip()))
            elif op == "is not empty":
                results.append((joiner, bool(cell.strip())))
            else:
                results.append((joiner, compare(cell, op, value)))
        return combine(results)


class _Missing:
    def __repr__(self):
        return "<missing>"


_MISSING = _Missing()

_MOBILE = re.compile(r"^[6-9]\d{9}$")


def mask(value: str) -> str:
    v = str(value)
    if _MOBILE.match(v):
        return v[:2] + "---" + v[-3:]
    return v if len(v) <= 40 else v[:37] + "…"


def describe_row(row: dict, limit: int = 3) -> str:
    shown = [(k, v) for k, v in row.items() if k != "row_number"][:limit]
    return ", ".join(f"{k} = {mask(v)}" for k, v in shown) or "empty row"


# ─────────────────────────────────────────────────────────────────────────────
# Page-aware condition evaluator
# ─────────────────────────────────────────────────────────────────────────────
_ELEM_VIS = re.compile(
    r"^(?:the\s+)?(?:element\s+)?([A-Za-z_][\w.\-]*)\s+(is|are)\s+(not\s+)?"
    r"(visible|displayed|shown|present|on\s+screen)(?:\s+within\s+(\d+(?:\.\d+)?)\s*s(?:ec(?:onds?)?)?)?$",
    re.I)
_ELEM_EXISTS = re.compile(r"^(?:the\s+)?element\s+([A-Za-z_][\w.\-]*)\s+"
                          r"(exists|does\s+not\s+exist|doesn't\s+exist)$", re.I)
_ELEM_TEXT = re.compile(r"^(?:the\s+)?(?:text\s+of\s+)?element\s+([A-Za-z_][\w.\-]*)"
                        r"(?:\s+text)?\s+(.+)$", re.I)
_PAGE = re.compile(r"^(?:the\s+)?page\s+(title|url|address|text)?\s*(.+)$", re.I)
_PLATFORM = re.compile(r"^(?:the\s+)?platform\s+is\s+(not\s+)?(website|web|mobile\s*site|mobilesite|touch|wap|android|ios|iphone)$", re.I)
_ENV = re.compile(r"^(?:the\s+)?environment\s+is\s+(not\s+)?(\S+)$", re.I)


class WebProbe:
    """What a condition can ask about a browser page (Website / Mobile Site)."""

    def __init__(self, page):
        self.page = page

    def _p(self):
        import execution.action_service as svc
        return svc, svc.get_active_page(self.page)

    def visible(self, name: str, secs: float) -> bool:
        svc, page = self._p()
        return svc._visible_within(page, name, secs) is not None

    def exists(self, name: str) -> bool:
        svc, page = self._p()
        try:
            xpath, _ = svc._resolve_live(page, name)
            return svc._get_locator_root(page).locator(xpath).count() > 0
        except Exception:  # noqa: BLE001
            return False

    def text(self, name: str) -> str:
        svc, page = self._p()
        return svc._element_text(page, name)

    def page_value(self, what: str) -> str:
        _svc, page = self._p()
        if what == "title":
            return page.title()
        if what in ("url", "address"):
            return page.url
        return page.inner_text("body")

    def platform(self) -> str:
        import execution.action_service as svc
        return svc.RUN_PLATFORM or "website"

    def environment(self) -> str:
        import execution.action_service as svc
        return str(svc.SITE_ENV or "live")


class AppProbe:
    """The same questions asked of an Android / iOS app (Appium driver)."""

    def __init__(self, driver, platform: str):
        self.driver, self.plat = driver, platform

    def visible(self, name: str, secs: float) -> bool:
        import execution.appium_action_service as svc
        try:
            svc.wait_for_element(self.driver, name, self.plat, timeout_s=max(secs, 1))
            return True
        except Exception:  # noqa: BLE001 — not appearing is an answer, not an error
            return False

    def exists(self, name: str) -> bool:
        import execution.appium_action_service as svc
        try:
            svc._find_element(self.driver, name, self.plat)
            return True
        except Exception:  # noqa: BLE001
            return False

    def text(self, name: str) -> str:
        import execution.appium_action_service as svc
        el = svc._find_element(self.driver, name, self.plat)
        if self.plat == "ios":
            return el.get_attribute("label") or el.text or el.get_attribute("value") or ""
        return el.text or el.get_attribute("content-desc") or ""

    def page_value(self, what: str) -> str:
        if what == "text":
            return self.driver.page_source or ""
        raise ValueError(f"'page {what}' does not exist in an app — check an element instead.")

    def platform(self) -> str:
        return self.plat

    def environment(self) -> str:
        return "app"


def make_evaluator(page=None, variables=None, probe=None):
    """callable(condition) -> (bool, explanation) for FlowProgram."""
    from nlp.variable_manager import RUNTIME_VARIABLES
    store = variables if variables is not None else RUNTIME_VARIABLES
    probe = probe or WebProbe(page)

    def value_of(token: str) -> str:
        token = token.strip()
        out = token
        for n in re.findall(r"\$\{([^}]+)\}", token):
            if n not in store:
                raise ValueError(f"${{{n}}} is not set at this point of the test")
            out = out.replace("${" + n + "}", str(store[n]))
        return _unquote(out)

    def atom(text: str) -> tuple[bool, str]:
        t = text.strip()
        neg = False
        m = re.match(r"^not\s+(.+)$", t, re.I)
        if m:
            neg, t = True, m.group(1)

        def out(ok: bool, why: str):
            return (not ok, f"not ({why})") if neg else (ok, why)

        m = _ELEM_VIS.match(t)
        if m:
            name, _are, notv, _w, secs = m.groups()
            visible = probe.visible(name, float(secs) if secs else 1.0)
            ok = (not visible) if notv else visible
            return out(ok, f"element {name} is {'visible' if visible else 'not visible'}")
        m = _ELEM_EXISTS.match(t)
        if m:
            name, verb = m.groups()
            present = probe.exists(name)
            want = verb.lower() == "exists"
            return out(present == want,
                       f"element {name} {'exists' if present else 'does not exist'}")
        m = _PLATFORM.match(t)
        if m:
            notp, plat = m.groups()
            low = plat.lower().replace(" ", "")
            plat = ("website" if low in ("web", "website") else
                    "ios" if low in ("ios", "iphone") else
                    "android" if low == "android" else "mobilesite")
            cur = probe.platform()
            ok = (cur == plat) != bool(notp)
            return out(ok, f"platform is {cur}")
        m = _ENV.match(t)
        if m:
            note, env = m.groups()
            cur = probe.environment()
            ok = (env.lower() in cur.lower()) != bool(note)
            return out(ok, f"environment is {cur}")
        m = _ELEM_TEXT.match(t)
        if m and split_comparison("x " + m.group(2)):
            name, rest = m.groups()
            _, op, value = split_comparison("x " + rest)
            actual = probe.text(name)
            return out(_cmp(actual, op, value_of(value)),
                       f"element {name} text is {mask(actual)!r}")
        m = _PAGE.match(t)
        if m and split_comparison("x " + m.group(2)):
            what = (m.group(1) or "text").lower()
            _, op, value = split_comparison("x " + m.group(2))
            actual = probe.page_value(what)
            shown = actual if what != "text" else "…"
            return out(_cmp(actual, op, value_of(value)), f"page {what} is {mask(shown)!r}")
        parsed = split_comparison(t)
        if parsed:
            left, op, right = parsed
            try:
                lv = value_of(left)
            except ValueError:
                if op in ("is empty", "is not empty"):
                    lv = ""
                else:
                    raise
            return out(_cmp(lv, op, value_of(right) if right else ""),
                       f"{left} is {mask(lv)!r}")
        raise ValueError(
            f"Did not understand the condition '{text}'. Examples: "
            "'element login_popup is visible', '${city} is Mumbai', "
            "'page title contains \"Justdial\"', 'platform is mobilesite'.")

    def evaluate(cond: str) -> tuple[bool, str]:
        results = []
        whys = []
        for joiner, part in split_logic(cond):
            ok, why = atom(part)
            results.append((joiner, ok))
            whys.append((f"{joiner} " if joiner else "") + why)
        return combine(results), " ".join(whys)

    return evaluate


def _cmp(actual: str, op: str, expected: str) -> bool:
    if op == "is empty":
        return not str(actual).strip()
    if op == "is not empty":
        return bool(str(actual).strip())
    return compare(actual, op, expected)


# ─────────────────────────────────────────────────────────────────────────────
# Simple driver for places without per-step reporting (step groups, CLI)
# ─────────────────────────────────────────────────────────────────────────────
def run_lines(lines, page, execute, logger=None, numbered=None, probe=None) -> None:
    """Run lines with blocks; `execute(step_text)` runs one ordinary step."""
    from core import datasets
    from nlp.variable_manager import RUNTIME_VARIABLES
    prog = FlowProgram(lines, evaluate=make_evaluator(page, probe=probe),
                       variables=RUNTIME_VARIABLES,
                       load_rows=datasets.rows, numbered=numbered)
    for it in prog.steps():
        if it.kind is None:
            execute(it.text)
        else:
            said = prog.decide(it)
            if logger:
                logger.info("🔀 %s → %s", it.text, said)


# ─────────────────────────────────────────────────────────────────────────────
# Editor support: nesting, what is open at a position, what to suggest there
# ─────────────────────────────────────────────────────────────────────────────
OPENERS = ("if",) + LOOP_KINDS
CLOSER = {"if": "end if", "for": "end for", "times": "end repeat",
          "until": "end repeat", "while": "end repeat"}


def layout(lines: list[str]) -> list[dict]:
    """
    Per line: depth (how far to indent), kind, and the index of the line that
    closes it (for folding). Tolerant — a half-written block never raises; the
    editor shows structure_error() separately.
    """
    out: list[dict] = []
    stack: list[int] = []
    for i, raw in enumerate(lines):
        text = (raw or "").strip()
        c = classify(text) if text and not text.startswith("#") else None
        kind = c[0] if c else None
        info = {"depth": len(stack), "kind": kind, "end": None}
        if kind in ("elif", "else"):
            info["depth"] = max(len(stack) - 1, 0)
        elif kind in ("endif", "endloop"):
            if stack:
                out[stack.pop()]["end"] = i
            info["depth"] = len(stack)
        out.append(info)
        if kind in OPENERS:
            stack.append(i)
    return out


def structure_error(lines: list[str]) -> str | None:
    try:
        FlowProgram(lines)
    except FlowStructureError as e:
        return str(e)
    return None


def open_blocks(lines: list[str], position: int) -> list[tuple[str, tuple]]:
    """Blocks still open just before 0-based `position`: [(kind, args), …] outer first."""
    stack: list[tuple[str, tuple]] = []
    for raw in lines[:position]:
        text = (raw or "").strip()
        if not text or text.startswith("#"):
            continue
        c = classify(text)
        if not c:
            continue
        kind, args = c
        if kind in OPENERS:
            stack.append((kind, args))
        elif kind in ("endif", "endloop") and stack:
            stack.pop()
    return stack


#: (template, what it does). Slots: {locator} element · {text} · {number}
#: {value} a ${…} value · {dataset} an uploaded data set · {column} a column.
STARTERS = [
    ("if element {locator} is visible", "Run the steps below only if the element is on screen"),
    ("if element {locator} is not visible", "Run the steps below only if the element is NOT on screen"),
    ("if element {locator} text contains {text}", "Check the element's text"),
    ("if {value} is {text}", "Compare a Test Data / data set value"),
    ("if {value} contains {text}", "Value contains some text"),
    ("if {value} is more than {number}", "Number comparison (also: is less than, is at least)"),
    ("if page title contains {text}", "Check the page title"),
    ("if page url contains {text}", "Check the page address"),
    ("if platform is mobilesite", "Only on Mobile Site (or: platform is website)"),
    ("for each row in {dataset}", "Repeat the steps below for every row of an uploaded table"),
    ("for each row in {dataset} rows {number} to {number}", "Only a range of rows"),
    ("for each row in {dataset} first {number} rows", "Only the first N rows"),
    ("for each row in {dataset} where {column} is {text}", "Only rows matching a value"),
    ("repeat {number} times", "Repeat the steps below a fixed number of times"),
    ("repeat until element {locator} is not visible", "Keep repeating until the element goes away"),
    ("repeat until element {locator} is visible", "Keep repeating until the element appears"),
    ("repeat while element {locator} is visible", "Repeat as long as the element is on screen"),
]

INSIDE_IF = [
    ("else if element {locator} is visible", "Another condition, checked when the one above is false"),
    ("else if {value} is {text}", "Another condition on a value"),
    ("else", "Steps to run when no condition above is true"),
]

INSIDE_LOOP = [
    ("stop loop if element {locator} is visible", "Leave the loop early when the element shows"),
    ("stop loop if {value} is {text}", "Leave the loop early on a value"),
    ("stop loop", "Leave the loop now"),
    ("skip to next row if {value} is {text}", "Skip the rest of this round"),
    ("skip to next row", "Skip the rest of this round"),
]


def suggestions(partial: str, context: list[str] | None = None) -> list[dict]:
    """Block templates matching what is typed; context = open block kinds, outer first."""
    context = context or []
    p = (partial or "").strip().lower()
    rows: list[tuple[str, str, str]] = []
    if context and context[-1] == "if":
        rows += [(t, d, "inside if") for t, d in INSIDE_IF]
    loops = [k for k in context if k in LOOP_KINDS]
    if loops:
        unit = "row" if loops[-1] == "for" else "round"
        rows += [(t.replace("next row", f"next {unit}"), d, "inside loop")
                 for t, d in INSIDE_LOOP]
    if p or not context:
        rows += [(t, d, "block") for t, d in STARTERS]
    if context:
        rows.append((CLOSER[context[-1]], "Close the block above", "inside " +
                     ("if" if context[-1] == "if" else "loop")))
    out = []
    first = p.split()[0] if p else ""
    for t, d, tag in rows:
        low = t.lower()
        if p and not (p in low and any(w.startswith(first) for w in low.split())):
            continue
        out.append({"template": t, "phrase": t, "action": "block", "detail": d, "tag": tag})
    return out


def element_names(step: str) -> list[str]:
    """Element names used in a block line's condition ('if element X is visible')."""
    c = classify(step)
    if not c:
        return []
    return re.findall(r"\belement\s+([A-Za-z_][\w.\-]*)", step, re.I)


def names_defined_by(step: str) -> set[str]:
    """${…} names a block line makes available to the lines under it."""
    c = classify(step or "")
    if not c:
        return set()
    kind, args = c
    if kind == "for":
        try:
            from core import datasets
            cols = set(datasets.info(args[0])["columns"])
        except Exception:  # noqa: BLE001 — unknown data set: reported elsewhere
            cols = set()
        return cols | {"row_number"}
    if kind in ("times", "until", "while"):
        return {"round"}
    return set()
