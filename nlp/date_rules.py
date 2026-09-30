"""
nlp/date_rules.py — reading the calendar / date steps.

    select date "next working day" in checkin_date
    select next weekend in travel_date                     (the word "date" is optional
    pick tomorrow from dob_field                            when the date is obvious)
    click date "next saturday" in calendar                  (calendar already open)
    select date ${checkin} + 2 days in checkout_date as "DD MMM YYYY"

    store date "next month" as d                            (DD/MM/YYYY)
    store date next working day as d in "DD MMM YYYY"
    store date from checkin_date as d                       (reads the field)
    add 3 working days to ${d} as d2 · subtract 1 week from ${d} as d0
    calculate days between ${d} and ${d2} as n              (days / working days / weeks / months)

    verify date in checkin_date is next working day
    verify date in checkin_date is a weekend
    verify date ${d} is after today · verify ${d} is a working day · verify ${d} is saturday
"""
from __future__ import annotations

import re

from nlp.command import Command

_EL = r"(?:the\s+)?(?:element\s+|field\s+)?([A-Za-z_][\w.\-]*(?:\[[^\]]+\])?)"
_FMT = r'(?:\s+(?:as|in|with|using)\s+(?:the\s+)?(?:format\s+)?"([^"]+)")?'
_VAR = r"([A-Za-z_]\w*)"
_UNITS = r"(working\s+days?|business\s+days?|days?|weeks?|months?|years?)"
_DAY_WORDS = (r"(?:working\s+day|weekday|business\s+day|weekend(?:\s+day)?|"
              r"mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|"
              r"sat(?:urday)?|sun(?:day)?)")
_CALENDAR = {"calendar", "the calendar", "date picker", "datepicker", "picker"}


def _is_date_phrase(p: str, relative_only: bool = False) -> bool:
    """True for 'tomorrow', 'next weekend', 'in 3 days' … (and written dates unless relative_only)."""
    from execution import date_ops
    t = p.strip().strip("\"'")
    if not t or re.fullmatch(r"\d{1,3}", t) or "${" in t:
        return False
    if relative_only and date_ops.parse_date(t):
        return False
    try:
        date_ops.resolve(t)
        return True
    except Exception:  # noqa: BLE001
        return False


def _unq(p: str) -> str:
    p = p.strip()
    return p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p


def match(s: str) -> Command | None:
    # ── read a date from a field ────────────────────────────────────────────
    m = re.match(r"^(?:store|save|get|fetch|read|capture)\s+(?:the\s+)?(?:selected\s+|chosen\s+)?date\s+"
                 r"(?:from|of|in|shown\s+in|on)\s+" + _EL + r"\s+(?:as|into|in)\s+" + _VAR + _FMT + r"$", s, re.I)
    if m:
        return Command(type="store_date_from", target=m.group(1), variable_name=m.group(2),
                       values=[m.group(3) or ""])

    # ── pick a date on a calendar / date field ─────────────────────────────
    m = re.match(r"^(select|pick|choose|click|tap|set|enter)\s+(?:on\s+)?(?:the\s+)?(date\s+)?(.+?)\s+"
                 r"(?:in|on|from|into|for)\s+" + r"(the\s+calendar|calendar|date\s*picker|picker|" + _EL[:-1] + r"))"
                 + _FMT + r"$", s, re.I)
    if m:
        verb, has_word, phrase = m.group(1).lower(), bool(m.group(2)), m.group(3)
        target = m.group(4)
        if has_word or (verb not in ("set", "enter") and _is_date_phrase(phrase)):
            t = "calendar" if target.lower() in _CALENDAR else (m.group(5) or target)
            return Command(type="select_date", target=t, text=_unq(phrase), values=[m.group(6) or ""])

    # ── store a date ────────────────────────────────────────────────────────
    m = re.match(r'^(?:store|save|get|set)\s+(?:the\s+)?date\s+(.+?)\s+(?:in|with|using)\s+(?:the\s+)?'
                 r'(?:format\s+)?"([^"]+)"\s+as\s+' + _VAR + r"$", s, re.I)
    if m:
        return Command(type="store_date", text=_unq(m.group(1)), variable_name=m.group(3), values=[m.group(2)])
    m = re.match(r"^(?:store|save|get|set)\s+(?:the\s+)?(date\s+)?(.+?)\s+as\s+" + _VAR + _FMT + r"$", s, re.I)
    if m and (m.group(1) or _is_date_phrase(m.group(2), relative_only=True)) and not re.match(r"(?i)^(text|value|length|attribute)\b", m.group(2)):
        return Command(type="store_date", text=_unq(m.group(2)), variable_name=m.group(3),
                       values=[m.group(4)] if m.group(4) else [])

    # ── move a date ─────────────────────────────────────────────────────────
    m = re.match(r"^(add|subtract|minus)\s+(\d+)\s+" + _UNITS + r"\s+(?:to|from)\s+(.+?)\s+as\s+" + _VAR + _FMT + r"$",
                 s, re.I)
    if m:
        n = int(m.group(2)) * (-1 if m.group(1).lower() != "add" else 1)
        unit = re.sub(r"\s+", " ", m.group(3).lower())
        return Command(type="date_add", target=_unq(m.group(4)), count=n, variable_name=m.group(5),
                       values=[unit] + ([m.group(6)] if m.group(6) else []))

    m = re.match(r"^(?:calculate|count|store|get|find)\s+(?:the\s+)?(?:number\s+of\s+)?(days|working\s+days|"
                 r"business\s+days|weeks|months)\s+between\s+(.+?)\s+and\s+(.+?)\s+as\s+" + _VAR + r"$", s, re.I)
    if m:
        return Command(type="date_diff", target=_unq(m.group(2)), values=[_unq(m.group(3)),
                       re.sub(r"\s+", " ", m.group(1).lower())], variable_name=m.group(4))

    # ── date checks ─────────────────────────────────────────────────────────
    m = re.match(r"^verify\s+(?:that\s+)?(?:the\s+)?(?:selected\s+|chosen\s+)?date\s+(?:in|of|shown\s+in|on)\s+"
                 + _EL + r"\s+((?:is|equals)\b.*)$", s, re.I)
    if m:
        return Command(type="verify_date_in", target=m.group(1), values=[m.group(2).strip()])
    m = re.match(r'^verify\s+(?:that\s+)?(?:the\s+)?date\s+(\$\{[^}]+\}|"[^"]+"|[A-Za-z_]\w*|\d[\d/\-.]+\d)\s+'
                 r"((?:is|equals)\b.*)$", s, re.I)
    if m:
        return Command(type="verify_date_value", target=_unq(m.group(1)), values=[m.group(2).strip()])
    m = re.match(r"^verify\s+(?:that\s+)?(\$\{[^}]+\}|[A-Za-z_]\w*)\s+(is\s+(?:not\s+)?(?:a\s+|an\s+|on\s+a\s+)?"
                 + _DAY_WORDS + r")$", s, re.I)
    if m:
        return Command(type="verify_date_value", target=m.group(1), values=[m.group(2).strip()])
    return None
