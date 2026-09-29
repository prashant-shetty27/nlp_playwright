"""
locators/manager.py
Dual-database locator management — extracted from locator_manager.py.
LocatorWatcher has been moved to locators/watcher.py.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta

from config import settings
from locators.io_utils import atomic_write_json, file_lock, read_json

logger = logging.getLogger(__name__)

# Platform keys that use the nested platform/screen/element schema
_APPIUM_PLATFORM_KEYS = {"ios", "android", "hybrid"}


def load_locators() -> dict:
    """Robust loading with error handling. Returns {} on missing/corrupt file."""
    path = settings.MANUAL_LOCATORS_FILE
    if not os.path.exists(path):
        logger.info("ℹ️ Manual locators file not found. Starting with empty database.")
        return {}
    try:
        with file_lock(path, exclusive=False):
            data = read_json(path, retries=2)
        return data if data is not None else {}
    except (json.JSONDecodeError, IOError) as e:
        logger.error("❌ CRITICAL: Could not read %s. Data may be corrupted: %s", path, e)
        return {}


def save_locators(data: dict) -> None:
    """Persists locator database to disk."""
    path = settings.MANUAL_LOCATORS_FILE
    try:
        with file_lock(path, exclusive=True):
            atomic_write_json(path, data, indent=2)
    except IOError as e:
        logger.error("❌ Failed to save locators to disk: %s", e)


#: A group or locator name that differs only by case creates a SECOND entry that
#: looks identical to a human — `Web_B2B_HomePage` and `web_b2b_homepage` both
#: existed for exactly this reason. Names are normalised at every write so the
#: database cannot drift into case variants again.
_NAME_OK = re.compile(r"^[a-z][a-z0-9_]*$")


def normalise_name(raw: str, *, kind: str = "name") -> str:
    """
    Lowercase, snake_case form of a group or locator name.

    Digit-led names (the recorder used to emit `0`, `1` … from `<input id="0">`)
    get a prefix, because a bare number tells a reader nothing about what it
    points at and cannot be distinguished from a numeric argument in a step.
    """
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(raw or "").strip().lower()).strip("_")
    if not cleaned:
        raise ValueError(f"{kind} is empty after normalisation: {raw!r}")
    if cleaned[0].isdigit():
        cleaned = f"item_{cleaned}"
    return cleaned


def add_locator(page_name: str, name: str, locator: str) -> bool:
    """Adds a locator with duplicate-XPath prevention and data scrubbing."""
    clean_page = normalise_name(page_name.split(":")[0], kind="group")
    clean_name = normalise_name(name, kind="locator")

    locs = load_locators()

    if clean_page not in locs:
        locs[clean_page] = {}

    # Duplicate XPath check across all pages
    for p_name, elements in locs.items():
        for e_name, e_path in elements.items():
            if e_path == locator:
                logger.warning(
                    "🚫 DATA INTEGRITY: XPath already registered as '%s' on '%s'", e_name, p_name
                )
                return False

    locs[clean_page][clean_name] = locator
    save_locators(locs)
    logger.info("✅ Locator '%s' locked into '%s'", clean_name, clean_page)
    return True


def get_locator_path(page_name: str, locator_name: str) -> tuple[str, str]:
    """Searches for a locator by name. Returns (page_name, xpath)."""
    locs = load_locators()

    if page_name in locs and locator_name in locs[page_name]:
        return page_name, locs[page_name][locator_name]

    for p, elements in locs.items():
        if locator_name in elements:
            return p, elements[locator_name]

    raise ValueError(f"Locator '{locator_name}' not found in any page.")


def _resolve_selector(entry: dict) -> str | None:
    """
    Pick the best locator value from a web/manual entry dict.
    Priority: custom_xpath → custom_xpath_P → xpath → value
              → selectors[] ordered by last_success → innerText fallback.
    """
    xpath = (
        entry.get("custom_xpath")
        or entry.get("custom_xpath_P")
        or entry.get("xpath")
        or entry.get("value")
    )
    if xpath:
        return xpath

    # selectors[] rotation: try last_success type first, then fall back to first item
    selectors = entry.get("selectors", [])
    if selectors:
        last_success = entry.get("last_success", "")
        if last_success:
            for s in selectors:
                if s.get("type") == last_success:
                    return s.get("value")
        # Fall back to first selector
        return selectors[0].get("value")

    # Last resort: innerText-based XPath
    inner = (entry.get("innerText") or "").strip()
    tag   = entry.get("tagName", "*")
    if inner and len(inner) < 80 and "'" not in inner:
        return f"//{tag}[normalize-space(.)='{inner}']"

    return None


def get_alternate_selectors(locator_name: str) -> list[dict]:
    """
    Every recorded selector for this locator EXCEPT the one currently resolved.

    These are the runner-up candidates captured alongside the primary (or found
    when an operator supplied one). They exist so that when the primary stops
    matching there is already a vetted second choice, instead of falling straight
    through to an ML scan that needs DNA the entry may not have.

    Returns [] for every entry that has only one recorded selector, which keeps
    resolution byte-identical for locators captured before alternates existed.
    """
    primary, entry = get_locator_and_dna(locator_name)
    if not isinstance(entry, dict):
        return []
    alts = []
    for sel in entry.get("selectors", []) or []:
        val = sel.get("value") if isinstance(sel, dict) else sel
        if val and val != primary:
            alts.append({"value": val,
                         "type": (sel.get("type") if isinstance(sel, dict) else "") or "css",
                         "weight": (sel.get("weight") if isinstance(sel, dict) else None)})
    return alts


def promote_selector(locator_name: str, winner: str, winner_type: str = "css") -> bool:
    """
    Record that `winner` resolved when the stored primary did not.

    The displaced selector is kept in selectors[] rather than discarded — a DOM
    change can be reverted, and the old value is the fastest way to notice that it
    was. The swap is stamped so a reader can tell an automatic promotion from a
    hand-edit.
    """
    path = settings.MANUAL_LOCATORS_FILE
    if not os.path.exists(path):
        return False
    try:
        with file_lock(path, exclusive=True):
            data = read_json(path, retries=2) or {}
            for _group, elements in data.items():
                if not isinstance(elements, dict) or locator_name not in elements:
                    continue
                entry = elements[locator_name]
                if not isinstance(entry, dict):
                    return False
                displaced = entry.get("custom_xpath")
                sels = entry.get("selectors", []) or []
                if displaced and not any(
                        (x.get("value") if isinstance(x, dict) else x) == displaced for x in sels):
                    sels.append({"type": entry.get("last_success", "css"), "value": displaced})
                entry["selectors"] = sels
                entry["custom_xpath"] = winner
                entry["last_success"] = winner_type
                entry["_promoted_from"] = displaced
                entry["_promoted_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                atomic_write_json(path, data)
                logger.info("📌 Promoted alternate for '%s': %r -> %r",
                            locator_name, displaced, winner)
                return True
    except Exception as e:
        logger.error("❌ Could not promote alternate for '%s': %s", locator_name, e)
    return False


def get_locator_and_dna(locator_name: str) -> tuple:
    """
    Master dispatcher: scans both ML database and manual database.
    Returns: (xpath_string, element_dna_dict | None)
    """
    # Web runner only (Appium has _get_appium_locator): the android / ios /
    # hybrid buckets are never candidates here — a web step used to resolve
    # to a same-named app element and run an //android.widget XPath.
    # A name defined in more than one web page is a warning, not a silent
    # first-wins: the author cannot otherwise tell which one ran.
    seen_in: list[str] = []
    result: tuple = (None, None)

    # 1. ML database first
    ml_path = settings.RECORDED_ELEMENTS_FILE
    if os.path.exists(ml_path):
        try:
            with file_lock(ml_path, exclusive=False):
                ml_data = read_json(ml_path, retries=2)
            for page, elements in ml_data.items():
                if page in _APPIUM_PLATFORM_KEYS or not isinstance(elements, dict):
                    continue
                if locator_name in elements:
                    seen_in.append(f"recorded:{page}")
                    if result[0]:
                        continue
                    dna = elements[locator_name]
                    xpath = (
                        dna.get("custom_xpath")
                        or dna.get("custom_xpath_P")
                        or dna.get("absoluteXPath")
                    )
                    # Fallback: build text-based XPath from innerText if no xpath stored
                    if not xpath:
                        inner = (dna.get("innerText") or "").strip()
                        tag   = dna.get("tagName", "*")
                        if inner and len(inner) < 80 and "'" not in inner:
                            xpath = f"//{tag}[normalize-space(.)='{inner}']"
                    result = (xpath, dna)
        except Exception as e:
            logger.error("❌ Error reading recorded_elements.json: %s", e)

    # 2. Manual database second
    manual_path = settings.MANUAL_LOCATORS_FILE
    if os.path.exists(manual_path):
        try:
            with file_lock(manual_path, exclusive=False):
                manual_data = read_json(manual_path, retries=2)
            for page, elements in manual_data.items():
                if page in _APPIUM_PLATFORM_KEYS or not isinstance(elements, dict):
                    continue
                if locator_name in elements:
                    seen_in.append(f"manual:{page}")
                    if result[0]:
                        continue
                    entry = elements[locator_name]
                    if isinstance(entry, dict):
                        result = (_resolve_selector(entry), entry)
                    else:
                        result = (entry, None)  # plain string
        except Exception as e:
            logger.error("❌ Error reading locators_manual.json: %s", e)

    if len(seen_in) > 1:
        logger.warning("⚠️  Element '%s' is defined in %s — using %s. Rename or "
                       "remove the others so the step is unambiguous.",
                       locator_name, ", ".join(seen_in), seen_in[0])
    return result


def mark_locator_verified(locator_name: str, selector_type: str | None = None) -> bool:
    """
    Stamp last_verified timestamp on a locator after a successful run.
    Optionally updates last_success to the selector type that worked.
    Returns True if the locator was found and updated.
    """
    data = load_locators()
    now  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for page, elements in data.items():
        if not isinstance(elements, dict):
            continue
        # Web/flat entries
        if locator_name in elements:
            entry = elements[locator_name]
            if isinstance(entry, dict):
                entry["last_verified"] = now
                if selector_type:
                    entry["last_success"] = selector_type
                save_locators(data)
                return True
        # Appium nested (platform → screen → element)
        if page in _APPIUM_PLATFORM_KEYS:
            for screen_els in elements.values():
                if isinstance(screen_els, dict) and locator_name in screen_els:
                    entry = screen_els[locator_name]
                    if isinstance(entry, dict):
                        entry["last_verified"] = now
                        if selector_type:
                            entry["last_success"] = selector_type
                        save_locators(data)
                        return True
    return False


def get_stale_locators(max_age_days: int = 30) -> list[dict]:
    """
    Return locators that have no last_verified date, or haven't been verified
    within max_age_days days. Useful for maintenance/health-check reports.
    """
    threshold = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    stale     = []
    data      = load_locators()

    def _check(page: str, screen: str | None, name: str, entry: dict):
        lv = entry.get("last_verified")
        if not lv:
            stale.append({"page": page, "screen": screen, "name": name, "last_verified": None})
            return
        try:
            dt = datetime.fromisoformat(lv.replace("Z", "+00:00"))
            if dt < threshold:
                stale.append({"page": page, "screen": screen, "name": name, "last_verified": lv})
        except Exception:
            stale.append({"page": page, "screen": screen, "name": name, "last_verified": lv})

    for page, elements in data.items():
        if not isinstance(elements, dict):
            continue
        if page in _APPIUM_PLATFORM_KEYS:
            for screen, screen_els in elements.items():
                if isinstance(screen_els, dict):
                    for name, entry in screen_els.items():
                        if isinstance(entry, dict):
                            _check(page, screen, name, entry)
        else:
            for name, entry in elements.items():
                if isinstance(entry, dict):
                    _check(page, None, name, entry)

    return stale


def get_all_locators(platform: str = "") -> dict:
    """
    {locator_name: "PAGE ➔ locator_name"} for UI dropdowns, across every source.

    `platform` narrows it to what that platform's runner can resolve. Left
    empty it spans every platform, which is right for a picker that has not
    been told what the test targets and wrong for anything deciding whether a
    step will run.

    Delegates to locators/sources.py so the dropdown, the Elements screen, the
    linter and the snippet file cannot disagree about what exists. This function
    used to walk the two database files itself, in its own order, with its own
    handling of appium screen groups — a fourth independent copy of a rule that
    only the runner is entitled to define.
    """
    from locators.sources import display_map

    return display_map(platform)
