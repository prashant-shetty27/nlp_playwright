"""
execution/date_picker_app.py — pick a date on Android / iOS (and web pages inside apps).

Same steps as the website:
    select date "next working day" in checkin_date
    select next weekend in travel_date
    click date "next saturday" in calendar      (a calendar that is already open)

Handles, in this order, whatever opens after tapping the field:
  • a web page inside the app (WebView / Chrome) → same calendar logic as the website
  • iOS wheels (XCUIElementTypePickerWheel) → each wheel is set to the day / month / year
  • Android spinners (NumberPicker) → each column is typed with the value
  • a month calendar (Android DatePicker, iOS inline calendar, app-made calendars)
    → next / previous month arrows until the month shows, then the day
Then OK / Done / Set / Confirm is tapped if the picker has one. A text box that
opens nothing gets the date typed (DD/MM/YYYY unless the step names a format).
"""
from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date

from execution import date_ops

logger = logging.getLogger(__name__)

_MONTH_YEAR = re.compile(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
                         r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b[\s,]*((?:19|20)\d\d)\b",
                         re.I)
_CONFIRM = {"ok", "done", "set", "confirm", "apply", "select", "save", "choose"}
_NEXT = re.compile(r"(next\s*month|^next$|forward|^›$|^>$|chevron.?right|arrow.?right|android:id/next$)", re.I)
_PREV = re.compile(r"(prev(ious)?\s*month|^prev(ious)?$|back|^‹$|^<$|chevron.?left|arrow.?left|android:id/prev$)", re.I)


def _is_web(driver) -> bool:
    try:
        ctx = driver.current_context or ""
    except Exception:  # noqa: BLE001
        return False
    return ctx and ctx != "NATIVE_APP"


def _labels(node) -> list[str]:
    return [node.get(k) for k in ("content-desc", "text", "name", "label", "value", "resource-id")
            if node.get(k)]


def _bounds(node) -> tuple[int, int] | None:
    b = node.get("bounds")
    if b:
        m = re.findall(r"\d+", b)
        if len(m) == 4:
            x1, y1, x2, y2 = map(int, m)
            return (x1 + x2) // 2, (y1 + y2) // 2
    if node.get("x") is not None and node.get("width") is not None:
        x, y, w, h = (int(float(node.get(k, 0))) for k in ("x", "y", "width", "height"))
        if w > 0 and h > 0:
            return x + w // 2, y + h // 2
    return None


def _visible(node) -> bool:
    if node.get("visible") == "false" or node.get("displayed") == "false":
        return False
    return _bounds(node) is not None


def _enabled(node) -> bool:
    return node.get("enabled", "true") != "false"


def _tap_xy(driver, platform: str, xy: tuple[int, int]) -> None:
    x, y = xy
    try:
        if platform == "ios":
            driver.execute_script("mobile: tap", {"x": x, "y": y})
        else:
            driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
        return
    except Exception:  # noqa: BLE001
        pass
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.actions import interaction
    from selenium.webdriver.common.actions.action_builder import ActionBuilder
    from selenium.webdriver.common.actions.pointer_input import PointerInput
    a = ActionChains(driver)
    a.w3c_actions = ActionBuilder(driver, mouse=PointerInput(interaction.POINTER_TOUCH, "touch"))
    a.w3c_actions.pointer_action.move_to_location(x, y).pointer_down().pause(0.05).release()
    a.perform()


def _tree(driver):
    return ET.fromstring(driver.page_source.encode("utf-8"))


def _shown_month(nodes) -> tuple[int, int] | None:
    for n in nodes:
        for lab in _labels(n):
            if len(lab) > 40:
                continue
            m = _MONTH_YEAR.search(lab)
            if m:
                return date_ops.month_no(m.group(1)), int(m.group(2))
    return None


def _full_labels(d: date) -> list[str]:
    mon, mo3 = date_ops.MONTHS[d.month - 1].title(), date_ops.MONTHS[d.month - 1][:3].title()
    wd = date_ops.DAYS[d.weekday()].title()
    return [f"{d.day} {mon} {d.year}", f"{d.day:02d} {mon} {d.year}", f"{mon} {d.day}, {d.year}",
            f"{wd}, {mon} {d.day}, {d.year}", f"{wd}, {d.day} {mon} {d.year}", f"{d.day} {mo3} {d.year}",
            f"{mo3} {d.day}, {d.year}", d.isoformat(), f"{d.day:02d}/{d.month:02d}/{d.year}"]


def _no_year_labels(d: date) -> list[str]:
    """iOS inline calendars label days 'Wednesday, October 15' — the year is in the header."""
    mon = date_ops.MONTHS[d.month - 1].title()
    wd = date_ops.DAYS[d.weekday()].title()
    return [f"{wd}, {mon} {d.day}", f"{wd}, {d.day} {mon}", f"{wd} {d.day} {mon}"]


def _set_ios_wheels(driver, target: date) -> bool:
    from appium.webdriver.common.appiumby import AppiumBy
    wheels = driver.find_elements(AppiumBy.CLASS_NAME, "XCUIElementTypePickerWheel")
    if not wheels:
        return False
    for w in wheels:
        v = (w.get_attribute("value") or "").strip()
        if re.fullmatch(r"(19|20)\d\d", v):
            w.send_keys(str(target.year))
        elif re.match(r"(?i)[a-z]", v) and \
                any(v.lower().startswith(m[:3]) for m in date_ops.MONTHS):
            w.send_keys(date_ops.MONTHS[target.month - 1].title())
        elif re.fullmatch(r"\d{1,2}", v):
            w.send_keys(str(target.day))
        elif re.match(r"(?i)(mon|tue|wed|thu|fri|sat|sun|today)", v):   # "Wed Oct 15" style
            w.send_keys(date_ops.fmt(target, "ddd MMM D"))
    return True


def _set_android_spinners(driver, target: date) -> bool:
    from appium.webdriver.common.appiumby import AppiumBy
    inputs = driver.find_elements(AppiumBy.ID, "android:id/numberpicker_input")
    if not inputs:
        return False
    for el in inputs:
        v = (el.text or "").strip()
        if re.fullmatch(r"(19|20)\d\d", v):
            new = str(target.year)
        elif re.fullmatch(r"(?i)[a-z]{3,}", v):
            new = date_ops.MONTHS[target.month - 1][:3].title() if len(v) <= 4 \
                else date_ops.MONTHS[target.month - 1].title()
        elif re.fullmatch(r"\d{1,2}", v):
            new = f"{target.day:02d}" if len(v) == 2 else str(target.day)
        else:
            continue
        el.clear()
        el.send_keys(new)
    return True


def _pick_calendar(driver, platform: str, target: date, max_moves: int = 60) -> str:
    last, same = None, 0
    for _ in range(max_moves):
        root = _tree(driver)
        nodes = [n for n in root.iter() if _visible(n)]
        shown = _shown_month(nodes)
        # a day labelled with its full date
        want = {x.lower() for x in _full_labels(target)}
        want_no_year = {x.lower() for x in _no_year_labels(target)}
        for n in nodes:
            labs = [x.lower().strip() for x in _labels(n)]
            if any(l in want or any(l.startswith(w) for w in want) for l in labs) or \
                    (shown == (target.month, target.year) and any(l in want_no_year for l in labs)):
                if not _enabled(n):
                    raise AssertionError(f"{date_ops.fmt(target)} is greyed out in the calendar — it can't be selected.")
                _tap_xy(driver, platform, _bounds(n))
                return "label"
        if shown is None:
            raise LookupError("no calendar")
        if shown == (target.month, target.year):
            days = [n for n in nodes if (n.get("text") or n.get("label") or n.get("name") or "").strip()
                    == str(target.day) and len(list(n)) == 0]
            if not days:
                raise AssertionError(f"The calendar shows {date_ops.fmt(target, 'MMMM YYYY')} but day "
                                     f"{target.day} is not on screen.")
            pick = days[0] if target.day < 15 or len(days) == 1 else days[-1]
            if not _enabled(pick):
                raise AssertionError(f"{date_ops.fmt(target)} is greyed out in the calendar — it can't be selected.")
            _tap_xy(driver, platform, _bounds(pick))
            return "grid"
        forward = (target.year, target.month) > (shown[1], shown[0])
        pat = _NEXT if forward else _PREV
        arrow = next((n for n in nodes if _enabled(n) and any(pat.search(l.strip()) for l in _labels(n))
                      and not re.search(r"(?i)year", " ".join(_labels(n)))), None)
        if arrow is None:
            raise AssertionError(f"The calendar shows {date_ops.MONTHS[shown[0] - 1].title()} {shown[1]} and "
                                 f"has no {'next' if forward else 'previous'} month arrow.")
        _tap_xy(driver, platform, _bounds(arrow))
        same = same + 1 if shown == last else 0
        if same >= 3:
            raise AssertionError("The calendar's month arrow did not change the month.")
        last = shown
        time.sleep(0.35)
    raise AssertionError(f"Could not reach {date_ops.fmt(target, 'MMMM YYYY')} in the calendar.")


def _confirm(driver, platform: str) -> None:
    time.sleep(0.3)
    try:
        root = _tree(driver)
    except Exception:  # noqa: BLE001
        return
    for n in root.iter():
        if not _visible(n) or not _enabled(n):
            continue
        labs = [x.strip().lower() for x in _labels(n)]
        if "android:id/button1" in labs or any(l in _CONFIRM for l in labs):
            _tap_xy(driver, platform, _bounds(n))
            logger.info("👆 Confirmed the date picker")
            return


def _web_select(driver, name: str, target: date, pattern: str | None) -> str:
    from execution.date_picker_web import _JS, _MARK, _labels as web_labels
    from appium.webdriver.common.appiumby import AppiumBy
    for _ in range(60):
        res = driver.execute_script("return (" + _JS + ")(null, arguments[0]);",
                                    {"day": target.day, "month": target.month, "year": target.year,
                                     "mark": _MARK, "isoLabel": web_labels(target)})
        st = (res or {}).get("status")
        if st in ("day", "nav"):
            driver.find_element(AppiumBy.CSS_SELECTOR, f"[{_MARK}='{st}']").click()
            if st == "day":
                return "web calendar"
            time.sleep(0.3)
            continue
        if st == "disabled":
            raise AssertionError(f"{date_ops.fmt(target)} is greyed out in the calendar — it can't be selected.")
        raise AssertionError(f"Calendar problem on the web page ({st}).")
    raise AssertionError("Could not reach the month in the web calendar.")


def select_date(driver, name: str, phrase: str, platform: str, pattern: str | None = None) -> None:
    from execution.appium_action_service import _find_element
    target = date_ops.resolve(phrase)
    nice = f"{date_ops.fmt(target, pattern)} ({date_ops.DAYS[target.weekday()][:3].title()})"
    only_calendar = not name or name.lower() in ("calendar", "the calendar", "date picker", "picker")

    if _is_web(driver):
        if not only_calendar:
            from execution.appium_action_service import tap_element
            tap_element(driver, name, platform)
            time.sleep(0.6)
        how = _web_select(driver, name, target, pattern)
        logger.info("📅 Picked %s (%s)", nice, how)
        return

    el = None
    if not only_calendar:
        el = _find_element(driver, name, platform)
        el.click()
        time.sleep(0.8)

    if _set_ios_wheels(driver, target) or (platform != "ios" and _set_android_spinners(driver, target)):
        _confirm(driver, platform)
        logger.info("📅 Set %s on the picker wheels", nice)
        return
    try:
        how = _pick_calendar(driver, platform, target)
        _confirm(driver, platform)
        logger.info("📅 Picked %s in the calendar (%s)", nice, how)
        return
    except LookupError:
        pass
    cls = (el.get_attribute("className") or el.get_attribute("type") or "") if el is not None else ""
    if el is not None and re.search(r"EditText|TextField", cls):
        text = date_ops.fmt(target, pattern or date_ops.DEFAULT_FORMAT)
        el.clear()
        el.send_keys(text)
        logger.info("📅 Typed %s into '%s'", text, name)
        return
    raise AssertionError(f"Tapped '{name}' but no date picker opened. Record the element that opens it.")


def read_date(driver, name: str, platform: str) -> tuple[str, date]:
    from execution.appium_action_service import _find_element
    el = _find_element(driver, name, platform)
    shown = (el.text or el.get_attribute("value") or el.get_attribute("label")
             or el.get_attribute("content-desc") or "").strip()
    d = date_ops.parse_date(shown)
    if not d:
        m = re.search(r"\d{1,2}[/\-. ]\w{2,9}[/\-. ,]+\d{2,4}|\w{3,9} \d{1,2},? \d{4}", shown)
        d = date_ops.parse_date(m.group(0)) if m else None
    if not d:
        raise AssertionError(f"'{name}' shows {shown!r} — that is not a date that can be read.")
    return shown, d


def store_date_from(driver, name: str, platform: str, variable: str, pattern: str | None = None) -> None:
    from nlp.variable_manager import RUNTIME_VARIABLES
    shown, d = read_date(driver, name, platform)
    RUNTIME_VARIABLES[variable] = date_ops.fmt(d, pattern)
    logger.info("📅 '%s' shows %r → ${%s} = %s", name, shown, variable, RUNTIME_VARIABLES[variable])


def verify_date_in(driver, name: str, platform: str, op: str, phrase: str) -> None:
    _shown, d = read_date(driver, name, platform)
    logger.info("✅ %s", date_ops.check(d, op, phrase))
