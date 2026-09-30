"""
execution/date_picker_web.py — pick a date on any calendar (Website + Mobile Site).

    select date "next working day" in checkin_date
    select next weekend in travel_date
    select date ${checkin} + 2 days in checkout_date
    click date "next saturday" in calendar      (a calendar that is already open)

Works out what kind of date field it is, in this order:
  1. a browser date box (<input type="date">) → filled with the date
  2. a pop-up calendar opened by clicking the field → moves month by month with
     the calendar's own next/previous arrows (or its month/year drop-downs) and
     clicks the day. Days greyed out / from the next month are never clicked.
  3. an ordinary text box with no pop-up → typed in the field's own format
     (read from its placeholder, e.g. dd-mm-yyyy), else DD/MM/YYYY
Afterwards the field is read back and the step fails if it shows another date.
"""
from __future__ import annotations

import logging
from datetime import date

from execution import date_ops

logger = logging.getLogger(__name__)

_MARK = "data-nlp-date-pick"

# Finds calendars on the page by what they are — a block showing the days 1–28+ —
# so it works with jQuery UI, react-datepicker, flatpickr, MUI, Angular Material,
# bootstrap-datepicker and hand-made calendars alike, without any recorded element.
_JS = r"""
(_el, args) => {
  const {day, month, year, mark, isoLabel} = args;
  const MONTHS = ['january','february','march','april','may','june','july','august',
                  'september','october','november','december'];
  const monRe = /\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b[\s,]*((?:19|20)\d\d)\b/i;
  const monNo = w => MONTHS.findIndex(m => m.startsWith(w.toLowerCase().slice(0,3))) + 1;
  document.querySelectorAll('['+mark+']').forEach(e => e.removeAttribute(mark));
  const vis = e => { if (!e || !e.getBoundingClientRect) return false;
    const r = e.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false;
    const s = getComputedStyle(e); return s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity !== 0; };
  const txt = e => (e.innerText || e.textContent || '').trim();
  // 1. day cells: deepest visible elements whose text is 1..31
  const cells = [];
  for (const e of document.querySelectorAll('body *')) {
    if (['SCRIPT','STYLE','OPTION','SELECT'].includes(e.tagName)) continue;
    const t = txt(e);
    if (!/^\d{1,2}$/.test(t) || +t < 1 || +t > 31) continue;
    if ([...e.children].some(c => txt(c) === t)) continue;      // a child carries it
    if (!vis(e)) continue;
    cells.push(e);
  }
  // 2. month grids: smallest blocks holding the numbers 1..28
  const grids = new Map();
  for (const c of cells) {
    let a = c.parentElement, depth = 0;
    while (a && a !== document.body && depth++ < 12) {
      if (!grids.has(a)) grids.set(a, new Set());
      grids.get(a).add(+txt(c));
      a = a.parentElement;
    }
  }
  let full = [...grids.entries()].filter(([g, s]) => { for (let i = 1; i <= 28; i++) if (!s.has(i)) return false; return true; })
                                  .map(([g]) => g);
  full = full.filter(g => !full.some(o => o !== g && g.contains(o)));   // keep the innermost
  if (!full.length) return {status: 'none'};
  // 3. which month each grid shows: the nearest "October 2026" above it, or its drop-downs
  const shown = g => {
    let a = g, depth = 0;
    while (a && depth++ < 6) {
      const sels = [...a.querySelectorAll('select')];
      if (sels.length) {
        let m = 0, y = 0;
        for (const s of sels) {
          const o = s.options[s.selectedIndex]; if (!o) continue;
          const ot = o.text.trim();
          if (/^(19|20)\d\d$/.test(ot)) y = +ot;
          else if (monNo(ot) > 0 && /^[a-z]/i.test(ot)) m = monNo(ot);
        }
        const mt = txt(a).match(monRe);
        if (!m && mt) m = monNo(mt[1]);
        if (!y && mt) y = +mt[2];
        const ym = txt(a).match(/\b((?:19|20)\d\d)\b/);
        if (!y && ym) y = +ym[1];
        if (m && y) return {m, y, node: a, sels};
      }
      const t = (a.innerText || '');
      const all = t.match(new RegExp(monRe.source, 'gi')) || [];
      if (all.length === 1 || (all.length > 1 && a === g)) { const mm = all[0].match(monRe); return {m: monNo(mm[1]), y: +mm[2], node: a}; }
      if (all.length > 1) return null;              // this block holds two months — stop
      // month and year in separate elements (e.g. "October" … "2026")
      const mo = t.match(/\b(january|february|march|april|may|june|july|august|september|october|november|december)\b/i);
      const yr = t.match(/\b((?:19|20)\d\d)\b/);
      if (mo && yr && depth > 1) return {m: monNo(mo[1]), y: +yr[1], node: a};
      a = a.parentElement;
    }
    return null;
  };
  const info = full.map(g => ({g, s: shown(g)}));
  const off = e => {
    for (let a = e, i = 0; a && i < 4; a = a.parentElement, i++) {
      const cls = (typeof a.className === 'string' ? a.className : '').toLowerCase();
      if (a.hasAttribute('disabled') || a.getAttribute('aria-disabled') === 'true') return 'disabled';
      if (/\b(\S*disabled\S*|\S*unavailable\S*|\S*blocked\S*|\S*past\S*)\b/.test(cls)) return 'disabled';
      if (/(outside|other-?month|adjacent|prev-?month|next-?month|\bold\b|\bnew\b|not-?current|\boff\b|overflow|nextmonthday|prevmonthday)/.test(cls)) return 'outside';
      if (a.getAttribute('role') === 'gridcell' || a.tagName === 'TD' || a.tagName === 'BUTTON') break;
    }
    return '';
  };
  // 4. fast path — cells labelled with their full date (aria-label / data-date / title)
  for (const {g} of info) {
    for (const e of g.querySelectorAll('[aria-label],[data-date],[data-value],[data-day],[title],[datetime]')) {
      const lab = [e.getAttribute('aria-label'), e.getAttribute('data-date'), e.getAttribute('data-value'),
                   e.getAttribute('title'), e.getAttribute('datetime'), e.getAttribute('data-day')].filter(Boolean).join(' | ');
      if (!lab || !vis(e)) continue;
      const hit = isoLabel.some(v => lab.toLowerCase().includes(v.toLowerCase()));
      if (hit && txt(e).replace(/\D/g,'') .endsWith(String(day)) ) {
        const why = off(e); if (why === 'outside') continue;
        e.setAttribute(mark, 'day'); return {status: why === 'disabled' ? 'disabled' : 'day', how: 'label'};
      }
    }
  }
  // 5. the grid showing the wanted month → click the day
  const want = year * 12 + month;
  const known = info.filter(x => x.s);
  for (const {g, s} of known) {
    if (s.y * 12 + s.m !== want) continue;
    const mine = cells.filter(c => g.contains(c) && +txt(c) === day);
    const inMonth = mine.filter(c => off(c) !== 'outside');
    let pick = inMonth.length === 1 ? inMonth[0] : null;
    if (!pick && inMonth.length > 1) pick = day < 15 ? inMonth[0] : inMonth[inMonth.length - 1];
    if (!pick) return {status: 'missing'};
    pick.setAttribute(mark, 'day');
    return {status: off(pick) === 'disabled' ? 'disabled' : 'day', how: 'grid'};
  }
  if (!known.length) return {status: 'unknown-month'};
  // 6. not showing yet → use drop-downs if any, else the next / previous arrow
  const first = known[0].s, last = known[known.length - 1].s;
  const forward = want > last.y * 12 + last.m;
  if (first.sels && first.sels.length) {
    let changed = false;
    for (const s of first.sels) {
      const opts = [...s.options];
      let idx = opts.findIndex(o => o.text.trim() === String(year));
      if (idx < 0) idx = opts.findIndex(o => monNo(o.text.trim()) === month && /^[a-z]/i.test(o.text.trim()));
      if (idx >= 0 && s.selectedIndex !== idx) {
        s.selectedIndex = idx; s.dispatchEvent(new Event('input', {bubbles: true}));
        s.dispatchEvent(new Event('change', {bubbles: true})); changed = true;
      }
    }
    if (changed) return {status: 'moved', shown: first.y * 100 + first.m};
  }
  const nextRe = /(^|\b|-|_)(next|forward|nxt|right|arrow-?right|chevron-?right|angle-?right)(\b|-|_|$)/i;
  const prevRe = /(^|\b|-|_)(prev|previous|back|left|arrow-?left|chevron-?left|angle-?left)(\b|-|_|$)/i;
  const re = forward ? nextRe : prevRe;
  const glyph = forward ? /^[›»>→❯▶⟩⏵]$/ : /^[‹«<←❮◀⟨⏴]$/;
  let root = known[0].s.node;
  for (let i = 0; i < 5 && root; i++, root = root.parentElement) {
    const cand = [...root.querySelectorAll('button,a,[role=button],span,div,i,svg')].filter(e => {
      if (!vis(e)) return false;
      const lab = [e.getAttribute('aria-label'), e.getAttribute('title'), e.getAttribute('data-action'),
                   e.getAttribute('data-handler'), typeof e.className === 'string' ? e.className : (e.className && e.className.baseVal) || '',
                   e.id].filter(Boolean).join(' ');
      const t = txt(e);
      return (re.test(lab) && !/(year|decade)/i.test(lab) && t.length < 12) || glyph.test(t);
    });
    // prefer real controls over their inner icons
    cand.sort((a, b) => (b.matches('button,a,[role=button]') ? 1 : 0) - (a.matches('button,a,[role=button]') ? 1 : 0));
    const blocked = e => e.hasAttribute('disabled') || e.getAttribute('aria-disabled') === 'true' ||
      /disabled/i.test(typeof e.className === 'string' ? e.className : '');
    const c = cand.find(e => !blocked(e));
    if (c) { c.setAttribute(mark, 'nav'); return {status: 'nav', shown: first.y * 100 + first.m, forward}; }
    if (cand.length) return {status: 'no-arrow', blocked: true, shown: first.y * 100 + first.m, forward};
  }
  return {status: 'no-arrow', shown: first.y * 100 + first.m, forward};
}
"""


def _labels(d: date) -> list[str]:
    """How calendars label a day cell (aria-label / data-date)."""
    mon, mo3 = date_ops.MONTHS[d.month - 1].title(), date_ops.MONTHS[d.month - 1][:3].title()
    return [d.isoformat(), f"{d.day} {mon} {d.year}", f"{mon} {d.day}, {d.year}", f"{mon} {d.day} {d.year}",
            f"{d.day} {mo3} {d.year}", f"{mo3} {d.day}, {d.year}", f"{mo3} {d.day} {d.year}",
            f"{d.day:02d}/{d.month:02d}/{d.year}", f"{d.day:02d}-{d.month:02d}-{d.year}",
            f"{d.day}{'th' if 11 <= d.day <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(d.day % 10, 'th')} {mon} {d.year}",
            f"{mon} {d.day}{'th' if 11 <= d.day <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(d.day % 10, 'th')}, {d.year}"]


def _scope(page):
    from execution.action_service import _get_locator_root, get_active_page
    return get_active_page(page), _get_locator_root(page)


def _eval(root, target: date) -> dict:
    return root.locator("body").evaluate(_JS, {"day": target.day, "month": target.month,
                                                "year": target.year, "mark": _MARK,
                                                "isoLabel": _labels(target)})


def _pick_in_open_calendar(page, target: date, max_moves: int = 60) -> str:
    ep, root = _scope(page)
    last_shown, same = None, 0
    for _ in range(max_moves):
        res = _eval(root, target)
        st = res.get("status")
        if st == "day":
            root.locator(f"[{_MARK}='day']").first.click(timeout=5000)
            return res.get("how", "grid")
        if st == "disabled":
            raise AssertionError(f"{date_ops.fmt(target)} is greyed out in the calendar — it can't be "
                                 f"selected (past date, sold out, or outside the allowed range).")
        if st == "none":
            raise LookupError("no calendar")
        if st in ("unknown-month", "missing"):
            raise AssertionError("The calendar is open but its month and year could not be read, so "
                                 f"{date_ops.fmt(target, 'D MMMM YYYY')} could not be found in it.")
        if st == "no-arrow" and res.get("blocked"):
            raise AssertionError(f"{date_ops.fmt(target)} can't be reached — the calendar shows {_ym(res['shown'])} "
                                 f"and its {'next' if res.get('forward') else 'previous'} arrow is disabled "
                                 f"({'later' if res.get('forward') else 'earlier'} months are not allowed).")
        if st == "no-arrow":
            raise AssertionError(f"The calendar shows {_ym(res['shown'])} and has no "
                                 f"{'next' if res.get('forward') else 'previous'} arrow to reach "
                                 f"{date_ops.fmt(target, 'MMMM YYYY')}.")
        if st == "nav":
            root.locator(f"[{_MARK}='nav']").first.click(timeout=5000)
        shown = res.get("shown")
        same = same + 1 if shown == last_shown else 0
        if same >= 3:
            raise AssertionError(f"The calendar stays on {_ym(shown)} — its arrow did not change the month.")
        last_shown = shown
        ep.wait_for_timeout(250)
    raise AssertionError(f"Could not reach {date_ops.fmt(target, 'MMMM YYYY')} in the calendar.")


def _ym(v) -> str:
    if not v:
        return "an unknown month"
    return f"{date_ops.MONTHS[v % 100 - 1].title()} {v // 100}"


def _calendar_open(root) -> bool:
    try:
        return root.locator("body").evaluate(_JS, {"day": 1, "month": 1, "year": 1900, "mark": _MARK + "-probe",
                                   "isoLabel": []}).get("status") != "none"
    except Exception:  # noqa: BLE001
        return False


def _read_back(loc) -> str:
    try:
        v = loc.input_value(timeout=1500)
        if v:
            return v
    except Exception:  # noqa: BLE001
        pass
    try:
        return (loc.inner_text(timeout=1500) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def select_date(page, name: str, phrase: str, pattern: str | None = None) -> None:
    from config import settings
    from execution.action_service import _el
    target = date_ops.resolve(phrase)
    nice = f"{date_ops.fmt(target, pattern)} ({date_ops.DAYS[target.weekday()][:3].title()})"
    ep, root = _scope(page)

    if not name or name.lower() in ("calendar", "the calendar", "date picker", "datepicker", "picker"):
        how = _pick_in_open_calendar(page, target)
        logger.info("📅 Picked %s in the open calendar (%s)", nice, how)
        return

    loc, _sel = _el(page, name)
    loc.scroll_into_view_if_needed(timeout=settings.ACTION_TIMEOUT_MS)
    kind = loc.evaluate("e => ({tag: e.tagName, type: (e.type||'').toLowerCase(), ro: !!(e.readOnly||e.disabled),"
                        " ph: e.getAttribute('placeholder')||'', val: e.value||''})")

    if kind["tag"] == "INPUT" and kind["type"] in ("date", "datetime-local", "month", "week"):
        v = {"date": target.isoformat(), "month": target.strftime("%Y-%m"),
             "week": f"{target.isocalendar()[0]}-W{target.isocalendar()[1]:02d}",
             "datetime-local": target.isoformat() + "T" + ((kind["val"].split("T") + ["09:00"])[1] or "09:00")
             }[kind["type"]]
        loc.fill(v, timeout=settings.ACTION_TIMEOUT_MS)
        logger.info("📅 Set %s in '%s'", nice, name)
        return

    loc.click(timeout=settings.ACTION_TIMEOUT_MS)
    opened = False
    for _ in range(12):                                 # pop-ups animate in
        if _calendar_open(root):
            opened = True
            break
        ep.wait_for_timeout(250)

    if opened:
        how = _pick_in_open_calendar(page, target)
        ep.wait_for_timeout(300)
        shown = _read_back(loc)
        got = date_ops.parse_date(shown) if shown else None
        if got and got != target:
            raise AssertionError(f"Picked {nice} in the calendar but '{_readable(name)}' shows {shown!r}.")
        logger.info("📅 Picked %s in '%s' (%s)%s", nice, name, how, f" → shows {shown!r}" if shown else "")
        return

    if kind["tag"] in ("INPUT", "TEXTAREA") and not kind["ro"]:
        fmt_used = pattern or date_ops.pattern_from_placeholder(kind["ph"]) or date_ops.DEFAULT_FORMAT
        text = date_ops.fmt(target, fmt_used)
        loc.fill(text, timeout=settings.ACTION_TIMEOUT_MS)
        try:
            loc.press("Tab")
        except Exception:  # noqa: BLE001
            pass
        logger.info("📅 Typed %s into '%s' (%s)", text, name, fmt_used)
        return

    raise AssertionError(f"Clicked '{_readable(name)}' but no calendar opened, and it is not a box "
                         f"that can be typed into. Record the element that opens the calendar.")


def _readable(name: str) -> str:
    return str(name).replace("_", " ")


def read_date(page, name: str) -> tuple[str, date]:
    """(text shown, date) for a date field or label."""
    from execution.action_service import _el
    loc, _ = _el(page, name)
    shown = _read_back(loc)
    d = date_ops.parse_date(shown)
    if not d:
        raise AssertionError(f"'{_readable(name)}' shows {shown!r} — that is not a date that can be read.")
    return shown, d


def store_date_from(page, name: str, variable: str, pattern: str | None = None) -> None:
    from nlp.variable_manager import RUNTIME_VARIABLES
    shown, d = read_date(page, name)
    RUNTIME_VARIABLES[variable] = date_ops.fmt(d, pattern)
    logger.info("📅 '%s' shows %r → ${%s} = %s", name, shown, variable, RUNTIME_VARIABLES[variable])


def verify_date_in(page, name: str, op: str, phrase: str) -> None:
    shown, d = read_date(page, name)
    logger.info("✅ %s", date_ops.check(d, op, phrase))
