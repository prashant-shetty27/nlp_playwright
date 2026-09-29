"""
locators/sources.py — the one place that knows which locator databases exist.

Why this module exists
----------------------
Nine modules used to hardcode their own list of locator files, each in its own
order: the API listed one file, the linter read two, the snippet writer read two
in the opposite order, the runtime read two with a precedence rule none of the
others reproduced. The result was that the same question — "does locator X
exist?" — got different answers depending on who asked. The Elements screen
showed 32 of 142 locators, and deleting a recorded locator returned "not found"
for something that resolves perfectly well at run time.

Adding a third database meant finding and editing all nine call sites. Missing
one produced no error, just a quieter disagreement.

So: every source is declared HERE, once, and every reader asks this module. To
add a database — a per-project file, a shared team file, an imported vendor set
— call register() with one more LocatorSource and every screen, the linter, the
snippet writer and the API pick it up with no further edits. That is the whole
point: the Nth database costs one line, not nine.

Resolution order is not invented here. It mirrors what the runners actually do:

  web     locators/manager.get_locator_and_dna scans recorded_elements.json
          first, then locators_manual.json, taking the first match. So a name
          present in both resolves to the RECORDED one.
  appium  execution/appium_action_service._get_appium_locator reads only
          locators_manual.json, only the data[<platform>] section, matching
          flat names there plus one level of screen groups.

tests/test_locator_sources.py asserts this module agrees with every reader on
every platform, so a future divergence fails a test instead of quietly
mis-reporting.

Nothing here raises. A database that is missing, unreadable, corrupt or the
wrong shape is skipped and recorded in problems() — a broken file must never
take down the screen that would let you fix it.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

from config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocatorSource:
    """One locator database."""

    id: str
    #: Attribute on config.settings holding the path. Read at CALL time, never
    #: cached, because tests and the recorder both repoint these at runtime.
    settings_attr: str
    label: str
    #: Whether the API may write to / delete from it. The recorder owns the
    #: recorded file; hand edits go to the manual one.
    writable: bool
    #: Lower wins, mirroring the runtime's first-match-wins scan order.
    precedence: int
    #: Which runners consult it. The appium runner never reads the recorded file.
    runners: tuple[str, ...] = ("web", "appium")


@dataclass
class Entry:
    """One locator, and where it came from."""

    name: str
    group: str
    source_id: str
    record: object = None
    #: Populated for appium screen-group members so callers can show the screen.
    screen: str = ""


#: The registry. Order here is documentation only — precedence decides lookups.
_SOURCES: list[LocatorSource] = [
    # The hand-edited database wins (precedence 0): what is edited on the
    # Elements page is what runs. The spy's recording is a fallback for names
    # the manual store does not have. (Reversed 29 Sep 2026 — with recorded
    # first, editing an element that also existed in a recording did nothing.)
    LocatorSource(
        id="manual",
        settings_attr="MANUAL_LOCATORS_FILE",
        label="Added by hand",
        writable=True,
        precedence=0,
        runners=("web", "appium"),
    ),
    LocatorSource(
        id="recorded",
        settings_attr="RECORDED_ELEMENTS_FILE",
        label="Recorded by the spy",
        writable=False,
        precedence=1,
        runners=("web",),
    ),
]

#: Files that could not be read this session: {source_id: reason}. Surfaced by
#: the API so a corrupt database is visible rather than silently empty.
_PROBLEMS: dict[str, str] = {}


def register(source: LocatorSource, *, replace: bool = False) -> None:
    """
    Add a locator database. This is the entire cost of supporting one more.

    Every reader in the codebase goes through this module, so a source added
    here appears in the Elements screen, the linter, the editor's dropdown and
    the snippet file at once — there is no second place to remember.
    """
    existing = next((s for s in _SOURCES if s.id == source.id), None)
    if existing is not None:
        if not replace:
            raise ValueError(f"A locator source with id {source.id!r} is already registered.")
        _SOURCES.remove(existing)
    _SOURCES.append(source)
    _SOURCES.sort(key=lambda s: s.precedence)


def unregister(source_id: str) -> bool:
    """Remove a source. Returns whether it was present."""
    victim = next((s for s in _SOURCES if s.id == source_id), None)
    if victim is None:
        return False
    _SOURCES.remove(victim)
    _PROBLEMS.pop(source_id, None)
    return True


def sources(runner: str | None = None) -> list[LocatorSource]:
    """Registered sources in resolution order, optionally for one runner only."""
    out = sorted(_SOURCES, key=lambda s: s.precedence)
    if runner:
        out = [s for s in out if runner in s.runners]
    return out


def path_of(source: LocatorSource) -> str:
    """Resolve a source's path NOW — settings are repointed at runtime."""
    return getattr(settings, source.settings_attr, "") or ""


def problems() -> dict[str, str]:
    """Sources that could not be read, and why."""
    return dict(_PROBLEMS)


def _read(source: LocatorSource) -> dict:
    """Load one database. Never raises; an unreadable file is an empty one."""
    path = path_of(source)
    if not path or not os.path.exists(path):
        _PROBLEMS.pop(source.id, None)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _PROBLEMS[source.id] = f"{type(e).__name__}: {e}"
        logger.warning("Locator source %r unreadable (%s): %s", source.id, path, e)
        return {}
    if not isinstance(data, dict):
        _PROBLEMS[source.id] = f"expected a JSON object, found {type(data).__name__}"
        return {}
    _PROBLEMS.pop(source.id, None)
    return data


def _runner_for(platform: str) -> str:
    """Which runner a platform uses, from the one platform table."""
    try:
        from nlp.platforms import PLATFORMS, normalise

        return PLATFORMS[normalise(platform)].runner
    except Exception:  # noqa: BLE001 — an unknown platform is treated as web
        return "web"


def _appium_platform_keys() -> set[str]:
    """Top-level keys that hold appium sections rather than web page groups."""
    try:
        from nlp.platforms import PLATFORMS

        return {name for name, p in PLATFORMS.items() if p.runner == "appium"}
    except Exception:  # noqa: BLE001
        return {"android", "ios", "hybrid"}


def _is_metadata(key: str) -> bool:
    """Underscore-prefixed keys are annotations, not elements."""
    return key.startswith("_")


def entries(platform: str = "website") -> list[Entry]:
    """
    Every locator resolvable on `platform`, in runtime resolution order.

    The first Entry for a given name is the one the runner would actually use,
    so callers can trust index order for conflicts.
    """
    runner = _runner_for(platform)
    out: list[Entry] = []

    for source in sources(runner):
        data = _read(source)
        if runner == "appium":
            section = data.get(platform)
            if not isinstance(section, dict):
                continue
            for key, val in section.items():
                if _is_metadata(key):
                    continue
                # A screen group is a dict whose values are themselves dicts.
                if isinstance(val, dict) and val and all(isinstance(v, dict) for v in val.values()):
                    for name, rec in val.items():
                        if not _is_metadata(name):
                            out.append(Entry(name, f"{platform}/{key}", source.id, rec, screen=key))
                    # The group name is tolerated as a flat reference by the
                    # runtime, so it must resolve here too.
                    out.append(Entry(key, platform, source.id, val, screen=key))
                else:
                    out.append(Entry(key, platform, source.id, val))
        else:
            appium_keys = _appium_platform_keys()
            for group, elements in data.items():
                if group in appium_keys or _is_metadata(group) or not isinstance(elements, dict):
                    continue
                for name, rec in elements.items():
                    if not _is_metadata(name):
                        out.append(Entry(name, group, source.id, rec))
    return out


def names(platform: str = "website") -> set[str]:
    """Every locator name resolvable on `platform`."""
    return {e.name for e in entries(platform)}


def all_platforms() -> list[str]:
    """Every platform name, so a platform-agnostic view can cover all of them."""
    try:
        from nlp.platforms import PLATFORMS

        return list(PLATFORMS)
    except Exception:  # noqa: BLE001
        return ["website"]


def display_map(platform: str = "") -> dict[str, str]:
    """
    {locator_name: "GROUP > name"} for editor dropdowns.

    With no platform this spans every platform, which is what the Elements
    screen and a platform-less picker want.

    With one, it spans only what that platform's runner can actually resolve —
    and a caller that is deciding whether a step will RUN must pass it. The
    editor did not, so an Android-only element rendered in a Website test as a
    working blue link while the review panel, which is platform-scoped,
    correctly called the same name unknown. Two screens disagreeing about
    whether an element exists is worse than either answer alone.
    """
    out: dict[str, str] = {}
    for platform in ([platform] if platform else all_platforms()):
        for e in entries(platform):
            if e.screen:
                label = f"{platform.upper()} / {e.screen} ➔ {e.name}"
            else:
                label = f"{str(e.group).upper()} ➔ {e.name}"
            # First definition wins, matching what the runtime would resolve.
            out.setdefault(e.name, label)
    return out


def owner(name: str, platform: str = "website") -> Entry | None:
    """The entry the runner would actually resolve `name` to, or None."""
    return next((e for e in entries(platform) if e.name == name), None)


def conflicts(platform: str = "website") -> dict[str, list[Entry]]:
    """
    Names defined more than once, with every definition in resolution order.

    A duplicated name is not an error — the runtime takes the first — but it is
    worth showing, because reusing that name binds to whichever definition wins,
    which may not be the one the author had in mind.
    """
    by_name: dict[str, list[Entry]] = {}
    for e in entries(platform):
        by_name.setdefault(e.name, []).append(e)
    return {n: es for n, es in by_name.items() if len(es) > 1}


def grouped(platform: str = "website") -> dict[str, dict]:
    """
    {group: {name: record}} across every source, for display.

    Each record carries "_source" so a screen can show where it came from and
    whether it can be edited. Losers of a name conflict are kept — they are real
    entries in a real file, and hiding them is how the 32-of-142 problem started.
    """
    out: dict[str, dict] = {}
    for e in entries(platform):
        rec = e.record
        shown = dict(rec) if isinstance(rec, dict) else {"value": rec}
        shown["_source"] = e.source_id
        bucket = out.setdefault(e.group, {})
        if e.name in bucket:
            # Same group AND name in two sources: entries() is in precedence
            # order, so the one already here is the one that runs. Keep it
            # editable; note the shadowed copy on it.
            bucket[e.name].setdefault("_also_in", []).append(e.source_id)
            continue
        bucket[e.name] = shown
    return out


def writable_source() -> LocatorSource | None:
    """The source new locators are written to."""
    return next((s for s in sources() if s.writable), None)


def describe() -> list[dict]:
    """One row per source, for a settings screen or a health check."""
    rows = []
    for s in sources():
        path = path_of(s)
        rows.append({
            "id": s.id,
            "label": s.label,
            "path": path,
            "exists": bool(path) and os.path.exists(path),
            "writable": s.writable,
            "runners": list(s.runners),
            "precedence": s.precedence,
            "problem": _PROBLEMS.get(s.id, ""),
        })
    return rows
