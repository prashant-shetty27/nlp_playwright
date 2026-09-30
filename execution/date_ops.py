"""
execution/date_ops.py — dates written the way testers say them.

    today, tomorrow, yesterday, day after tomorrow, next day, previous day
    next week, next month, next year, last week, last month …
    in 3 days, after 2 weeks, 10 days from today, 5 days ago, +7 days, -1 day
    next working day, previous working day, 3 working days from today
    next weekend (coming Saturday), next saturday, next sunday, next monday …
    this saturday, this sunday (today counts when it is that day)
    first day of next month, last day of this month,
    first working day of next month, last working day of this month
    ${checkin} + 2 days, ${checkin} + 1 working day, ${checkin} - 1 week
    15/10/2026, 2026-10-15, 15 Oct 2026, October 15 2026 …

Working days are Monday to Friday. "next month" keeps the day of the month and
moves back to the month's last day when it does not exist (31 Jan → 28/29 Feb).

Dates are written DD/MM/YYYY unless a step names a format:
    DD 05 · D 5 · MM 09 · M 9 · MMM Sep · MMMM September · YY 26 · YYYY 2026
    ddd Wed · dddd Wednesday · Do 5th

Nothing here touches the page, so web and app runners share it.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta

from nlp.variable_manager import RUNTIME_VARIABLES, resolve_variables

DEFAULT_FORMAT = "DD/MM/YYYY"

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]
_MON_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|" \
          r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY_RE = r"(mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"


class DateCheckFailed(AssertionError):
    """A date check did not hold — reported like any failed step."""


def month_no(word: str) -> int:
    w = word.lower()[:3]
    return [m[:3] for m in MONTHS].index(w) + 1


def weekday_no(word: str) -> int:
    w = word.lower()[:3]
    return [d[:3] for d in DAYS].index(w)


def today() -> date:
    return date.today()


# ── moving dates ──────────────────────────────────────────────────────────────
def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def is_working_day(d: date) -> bool:
    return d.weekday() < 5


def add_working_days(d: date, n: int) -> date:
    step = 1 if n >= 0 else -1
    left = abs(n)
    while left:
        d += timedelta(days=step)
        if is_working_day(d):
            left -= 1
    return d


def next_weekday(d: date, wd: int, include_today: bool = False) -> date:
    ahead = (wd - d.weekday()) % 7
    if ahead == 0 and not include_today:
        ahead = 7
    return d + timedelta(days=ahead)


def previous_weekday(d: date, wd: int) -> date:
    back = (d.weekday() - wd) % 7 or 7
    return d - timedelta(days=back)


def shift(d: date, n: int, unit: str) -> date:
    u = unit.lower().rstrip("s")
    if u in ("working day", "business day", "weekday", "workday"):
        return add_working_days(d, n)
    if u == "day":
        return d + timedelta(days=n)
    if u == "week":
        return d + timedelta(weeks=n)
    if u == "month":
        return add_months(d, n)
    if u == "year":
        return add_months(d, 12 * n)
    raise ValueError(f"Unknown unit '{unit}'.")


_UNIT = r"(working\s+days?|business\s+days?|weekdays?|workdays?|days?|weeks?|months?|years?)"
_WORDNUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _num(tok: str) -> int:
    t = tok.lower()
    return _WORDNUM[t] if t in _WORDNUM else int(t)


# ── reading dates ─────────────────────────────────────────────────────────────
_PARSE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%y",
                  "%d-%m-%y", "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%B-%Y", "%b %d %Y",
                  "%B %d %Y", "%a %d %b %Y", "%A %d %B %Y", "%a %b %d %Y", "%A %B %d %Y",
                  "%d %b %y", "%d %b, %Y", "%b %d, %Y", "%B %d, %Y", "%A, %B %d, %Y",
                  "%a, %d %b %Y", "%A, %d %B %Y", "%Y%m%d", "%m/%d/%Y")


def parse_date(text: str) -> date | None:
    """A written date → date, or None. Day-first unless only month-first fits."""
    if text is None:
        return None
    s = str(text).strip()
    s = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", s, flags=re.I)      # 5th Oct → 5 Oct
    s = re.sub(r"T\d{1,2}:\d{2}.*$|\s+\d{1,2}:\d{2}(:\d{2})?(\s*[ap]m)?$", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s.replace(" ,", ",")).strip()
    s = re.sub(r"(?i)\bsept\b", "Sep", s)
    for f in _PARSE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def resolve(phrase: str, base: date | None = None) -> date:
    """'next working day' / '${d} + 2 days' / '15/10/2026' → date."""
    raw = (phrase or "").strip().strip("\"'").strip()
    if not raw:
        raise ValueError("No date given.")
    b = base or today()

    # ${d} + 2 days · checkin + 1 working day · 15/10/2026 - 1 week
    m = re.match(r"^(.+?)\s*([+-])\s*(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
                 + _UNIT + r"$", raw, re.I)
    if m and m.group(1).strip():
        start = resolve(m.group(1), base)
        n = _num(m.group(3)) * (1 if m.group(2) == "+" else -1)
        return shift(start, n, re.sub(r"\s+", " ", m.group(4)))

    value = raw
    if "${" in raw:
        value = str(resolve_variables(raw)).strip()
    elif raw in RUNTIME_VARIABLES:
        value = str(RUNTIME_VARIABLES[raw]).strip()
    if value != raw:
        d = parse_date(value)
        if d:
            return d
        return resolve(value, base)

    d = parse_date(raw)
    if d:
        return d
    p = re.sub(r"\s+", " ", raw.lower().replace("’", "'")).strip()
    p = re.sub(r"^(the|on|date|on the)\s+", "", p)

    simple = {"today": 0, "now": 0, "tomorrow": 1, "next day": 1, "yesterday": -1,
              "previous day": -1, "prev day": -1, "day after tomorrow": 2,
              "day before yesterday": -2}
    if p in simple:
        return b + timedelta(days=simple[p])

    m = re.match(r"^(next|following|coming|last|previous|prev)\s+(week|month|year)$", p)
    if m:
        n = -1 if m.group(1) in ("last", "previous", "prev") else 1
        return shift(b, n, m.group(2))

    m = re.match(r"^(?:in|after|within)\s+(\w+)\s+" + _UNIT + r"$", p) or \
        re.match(r"^(\w+)\s+" + _UNIT + r"\s+(?:from|after)\s+(?:today|now)$", p) or \
        re.match(r"^\+\s*(\w+)\s+" + _UNIT + r"$", p)
    if m:
        return shift(b, _num(m.group(1)), m.group(2))
    m = re.match(r"^(\w+)\s+" + _UNIT + r"\s+(?:ago|before today|back)$", p) or \
        re.match(r"^-\s*(\w+)\s+" + _UNIT + r"$", p)
    if m:
        return shift(b, -_num(m.group(1)), m.group(2))

    if p in ("next working day", "next business day", "next weekday", "next workday"):
        return add_working_days(b, 1)
    if p in ("previous working day", "last working day", "prev working day",
             "previous business day", "previous weekday"):
        return add_working_days(b, -1)
    if p in ("today or next working day", "this working day", "working day"):
        return b if is_working_day(b) else add_working_days(b, 1)

    if p in ("next weekend", "coming weekend", "this weekend", "weekend"):
        # Mon–Fri → the coming Saturday. On a weekend, "this weekend" is today's
        # weekend's Saturday… so "next weekend" goes to next week's Saturday.
        if p in ("this weekend", "weekend") and b.weekday() >= 5:
            return b if b.weekday() == 5 else b - timedelta(days=1)
        return next_weekday(b, 5, include_today=False) if b.weekday() != 6 \
            else b + timedelta(days=6)
    if p in ("last weekend", "previous weekend"):
        return previous_weekday(b, 5) if b.weekday() != 5 else b - timedelta(days=7)

    m = re.match(r"^(next|coming|this|last|previous)\s+" + _DAY_RE + r"$", p) or \
        re.match(r"^" + _DAY_RE + r"$", p)
    if m:
        which = m.group(1) if m.lastindex == 2 else "next"
        wd = weekday_no(m.group(m.lastindex))
        if which in ("last", "previous"):
            return previous_weekday(b, wd)
        return next_weekday(b, wd, include_today=(which == "this"))

    m = re.match(r"^(first|start|1st|last|end)\s+(working\s+|business\s+)?day\s+of\s+"
                 r"(?:the\s+)?(this|next|last|previous)\s+month$", p) or \
        re.match(r"^(start|end)\s+of\s+(?:the\s+)?()(this|next|last|previous)\s+month$", p)
    if m:
        off = {"this": 0, "next": 1, "last": -1, "previous": -1}[m.group(3)]
        first = add_months(date(b.year, b.month, 1), off)
        working = bool(m.group(2))
        if m.group(1) in ("first", "start", "1st"):
            return first if not working or is_working_day(first) else add_working_days(first, 1)
        last = date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])
        return last if not working or is_working_day(last) else add_working_days(last, -1)

    # 15 oct / oct 15 (this year, or next year if already gone)
    m = re.match(r"^(\d{1,2})\s+" + _MON_RE + r"$", p) or re.match(r"^" + _MON_RE + r"\s+(\d{1,2})$", p)
    if m:
        a, c = m.group(1), m.group(2)
        day, mon = (int(a), month_no(c)) if a.isdigit() else (int(c), month_no(a))
        d = date(b.year, mon, day)
        return d if d >= b else date(b.year + 1, mon, day)

    raise ValueError(
        f"Can't read the date '{phrase}'. Try: today, tomorrow, next week, next month, "
        f"next working day, next weekend, next saturday, in 3 days, ${{date}} + 2 days, "
        f"or a date like 15/10/2026.")


# ── writing dates ─────────────────────────────────────────────────────────────
_TOKEN = re.compile(r"YYYY|YY|MMMM|MMM|MM|M|dddd|ddd|DD|Do|D|yyyy|yy|dd|d")


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def fmt(d: date, pattern: str | None = None) -> str:
    """date → text in DD/MM/YYYY-style tokens (also accepts dd/mm/yyyy, %d/%m/%Y)."""
    p = pattern or DEFAULT_FORMAT
    if "%" in p:
        return d.strftime(p)

    def rep(m):
        t = m.group(0)
        return {
            "YYYY": f"{d.year:04d}", "yyyy": f"{d.year:04d}", "YY": f"{d.year % 100:02d}",
            "yy": f"{d.year % 100:02d}", "MMMM": MONTHS[d.month - 1].title(),
            "MMM": MONTHS[d.month - 1][:3].title(), "MM": f"{d.month:02d}", "M": str(d.month),
            "dddd": DAYS[d.weekday()].title(), "ddd": DAYS[d.weekday()][:3].title(),
            "DD": f"{d.day:02d}", "dd": f"{d.day:02d}", "Do": _ordinal(d.day), "D": str(d.day),
            "d": str(d.day),
        }[t]
    # lower-case mm in a date pattern means month (dd/mm/yyyy) — normalise first
    q = re.sub(r"(?<![A-Za-z])mm(?![A-Za-z])", "MM", p)
    return _TOKEN.sub(rep, q)


def pattern_from_placeholder(text: str) -> str | None:
    """'dd/mm/yyyy' / 'DD-MM-YYYY' / 'mm/dd/yyyy' in a placeholder → a format, else None."""
    t = (text or "").strip()
    if re.fullmatch(r"(?i)(dd|mm|yyyy|yy|mmm)([/\-. ])(dd|mm|mmm)\2(yyyy|yy|dd)", t):
        parts = re.split(r"[/\-. ]", t)
        sep = re.search(r"[/\-. ]", t).group(0)
        up = [x.upper() if x.lower() != "mmm" else "MMM" for x in parts]
        return sep.join(up)
    return None


# ── steps (shared by the web and app runners) ─────────────────────────────────
TYPES = {"store_date", "date_add", "date_diff", "verify_date_value"}


def _store(var: str, d: date, pattern: str | None) -> str:
    text = fmt(d, pattern)
    RUNTIME_VARIABLES[var] = text
    return text


def check(actual: date, op: str, expected_phrase: str = "") -> str:
    """is / is not / is after / is before / is on or after … / is a working day / is a weekend."""
    o = re.sub(r"\s+", " ", (op or "is").strip().lower())
    kinds = {"working day": is_working_day, "weekday": is_working_day, "business day": is_working_day,
             "weekend": lambda d: d.weekday() >= 5, "weekend day": lambda d: d.weekday() >= 5}
    m = re.fullmatch(r"is (not )?(?:a |an |on a |on an )?(.+)", o)
    if m and m.group(2) in kinds:
        ok = kinds[m.group(2)](actual) != bool(m.group(1))
        if not ok:
            raise DateCheckFailed(f"{fmt(actual)} is a {DAYS[actual.weekday()].title()} — expected it "
                                  f"{'not ' if m.group(1) else ''}to be a {m.group(2)}.")
        return f"{fmt(actual)} ({DAYS[actual.weekday()].title()}) {o}"
    if m and re.fullmatch(_DAY_RE, m.group(2)):
        ok = (actual.weekday() == weekday_no(m.group(2))) != bool(m.group(1))
        if not ok:
            raise DateCheckFailed(f"{fmt(actual)} is a {DAYS[actual.weekday()].title()} — expected "
                                  f"{'not ' if m.group(1) else ''}{m.group(2).title()}.")
        return f"{fmt(actual)} {o}"
    exp = resolve(expected_phrase)
    rel = {"is": actual == exp, "equals": actual == exp, "is same as": actual == exp,
           "is the same as": actual == exp, "is not": actual != exp,
           "is after": actual > exp, "is later than": actual > exp,
           "is before": actual < exp, "is earlier than": actual < exp,
           "is on or after": actual >= exp, "is on or before": actual <= exp,
           "is not before": actual >= exp, "is not after": actual <= exp}
    if o not in rel:
        raise ValueError(f"Unknown date check '{op}'.")
    if not rel[o]:
        raise DateCheckFailed(f"The date is {fmt(actual)} ({DAYS[actual.weekday()][:3].title()}) — "
                              f"expected it {o[3:] if o.startswith('is ') else o} {fmt(exp)} "
                              f"({expected_phrase.strip()}).")
    return f"{fmt(actual)} {o} {fmt(exp)}"


_DAYWORD = r"(working day|weekday|business day|weekend|weekend day|" + _DAY_RE[1:-1] + r")"
_CHECK_OPS = ("is on or after", "is on or before", "is not before", "is not after", "is the same as",
              "is same as", "is later than", "is earlier than", "is after", "is before", "is not",
              "equals", "is")


def split_check(rest: str) -> tuple[str, str]:
    """'is next working day' → ('is', 'next working day'); 'is a weekend' → ('is a weekend', '')."""
    r = re.sub(r"\s+", " ", (rest or "").strip())
    rl = r.lower()
    if re.fullmatch(r"is (not )?(?:a |an |on a |on an )?" + _DAYWORD, rl):
        return rl, ""
    for op in _CHECK_OPS:
        if rl.startswith(op + " "):
            return op, r[len(op):].strip().strip("\"'")
    raise ValueError(f"Can't read the date check '{rest}'. Try: is next working day, is after today, "
                     f"is a weekend, is not a saturday.")


def execute(cmd) -> str:
    t = cmd.type
    vals = cmd.values or []
    if t == "store_date":                  # store date "next working day" as d [in "DD MMM YYYY"]
        d = resolve(cmd.text)
        return f"{cmd.text} → ${{{cmd.variable_name}}} = {_store(cmd.variable_name, d, vals[0] if vals else None)}"
    if t == "date_add":                    # add 3 working days to ${d} as d2
        n = int(cmd.count or 0)
        d = shift(resolve(cmd.target), n, vals[0])
        return f"→ ${{{cmd.variable_name}}} = {_store(cmd.variable_name, d, vals[1] if len(vals) > 1 else None)}"
    if t == "date_diff":                   # calculate days between ${a} and ${b} as n
        a, b = resolve(cmd.target), resolve(vals[0])
        unit = (vals[1] if len(vals) > 1 else "days").lower()
        if unit.startswith("working"):
            step = 1 if b >= a else -1
            n, d = 0, a
            while d != b:
                d += timedelta(days=step)
                n += step if is_working_day(d) else 0
        elif unit.startswith("week"):
            n = (b - a).days // 7
        elif unit.startswith("month"):
            n = (b.year - a.year) * 12 + b.month - a.month - (1 if b.day < a.day else 0)
        else:
            n = (b - a).days
        RUNTIME_VARIABLES[cmd.variable_name] = str(n)
        return f"{unit} between {fmt(a)} and {fmt(b)} = {n}"
    if t == "verify_date_value":           # verify date ${d} is next working day / is a weekend
        actual = resolve(cmd.target)
        return check(actual, *split_check(vals[0] if vals else ""))
    raise ValueError(f"Not a date step: {t}")
