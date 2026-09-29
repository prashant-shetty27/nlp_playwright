"""
nlp/platforms.py — the canonical list of platforms a test can target.

One table, one vocabulary. Before this existed, "platform" was a bare string
passed around and looked up in a dict that defaulted to web on a miss — so a
typo, or a value the UI invented, silently produced web behaviour with no error.
That is fine when the caller is a scenario file written by hand and wrong the
moment the value comes from a dropdown.

Each platform declares four things:

  runner   which dispatch table executes it — runner.py or runner_appium.py.
           This is what decides whether `enter_otp` (web) or `tap_text` (Appium)
           is available, so it must be right before suggestions are filtered.
  default_device
           Playwright device profile used when the caller names none. It is a
           DEFAULT, never a limit: any of Playwright's ~143 profiles is valid,
           so mobilesite can run an iPhone just as easily as a Pixel. This is the
           ONLY difference between website and mobilesite: same runner, same 63
           commands, different viewport and touch behaviour.
  browsers Engines this platform can run on. The engine is normally derived from
           the chosen DEVICE rather than picked by hand — Playwright's device
           descriptors declare their own `default_browser_type`, and iPhone
           profiles declare webkit. Emulating an iPhone on Chromium produces a
           Chromium page wearing an iPhone user-agent, which will not reproduce
           WebKit-specific rendering, so the device's own engine wins by default.
  enabled  False means "known, but masked in the UI". Native app support is
           built but not being exposed yet, so the platform is a real value that
           validates and resolves — it simply is not offered in the picker.
  aliases  Older names already written into scenarios, suites and plans. They
           keep working; nothing on disk has to be rewritten.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Flip to False to accept requests that omit a platform, which will then resolve
#: to DEFAULT_PLATFORM. Kept as one switch so the contract can be relaxed later
#: without hunting through request models.
PLATFORM_REQUIRED = True
DEFAULT_PLATFORM = "website"


@dataclass(frozen=True)
class Platform:
    name: str
    label: str
    runner: str                      # "web" | "appium"
    default_device: str | None = None    # a DEFAULT profile, not the only choice
    enabled: bool = True             # False = known but masked in the UI
    aliases: frozenset = field(default_factory=frozenset)
    browsers: tuple = ()             # engines offered; () = not browser-driven

    @property
    def is_appium(self) -> bool:
        return self.runner == "appium"

    @property
    def device(self) -> str | None:
        """Back-compat alias for the old single-device field."""
        return self.default_device


PLATFORMS: dict[str, Platform] = {
    "website": Platform(
        name="website", label="Website", runner="web",
        browsers=("chromium", "firefox", "webkit"),
        aliases=frozenset({"web", "chromium", "firefox", "webkit", "desktop"}),
    ),
    "mobilesite": Platform(
        name="mobilesite", label="Mobile Site (Waptouch)", runner="web",
        default_device="Pixel 7",
        browsers=("chromium", "webkit"),   # Firefox has no mobile-emulation story

        aliases=frozenset({"waptouch", "wap", "mobile", "mobileweb", "mobile_web",
                           "touch", "msite"}),
    ),
    "android": Platform(
        name="android", label="Android App", runner="appium", enabled=False,
        aliases=frozenset({"android app", "android_app", "androidnative"}),
    ),
    "ios": Platform(
        name="ios", label="iOS App", runner="appium", enabled=False,
        aliases=frozenset({"ios app", "ios_app", "iosnative"}),
    ),
    "hybrid": Platform(
        # Native shell rendering responsive web pages in a WebView. Driven through
        # Appium because the shell is a native app, even though the content is web.
        name="hybrid", label="Hybrid (Native + WebView)", runner="appium",
        enabled=False,
        aliases=frozenset({"native_webview", "webview", "native+webview"}),
    ),
}

_ALIAS_INDEX: dict[str, str] = {}
for _p in PLATFORMS.values():
    _ALIAS_INDEX[_p.name] = _p.name
    for _a in _p.aliases:
        _ALIAS_INDEX[_a] = _p.name


class UnknownPlatform(ValueError):
    """Raised instead of quietly falling back to web."""


def normalise(value: str | None) -> str:
    """Canonical platform name for any accepted spelling or alias."""
    if value is None or not str(value).strip():
        if PLATFORM_REQUIRED:
            raise UnknownPlatform(
                "platform is required. Choose one of: "
                + ", ".join(sorted(PLATFORMS))
            )
        return DEFAULT_PLATFORM
    key = str(value).strip().lower().replace("-", "_")
    if key in _ALIAS_INDEX:
        return _ALIAS_INDEX[key]
    if key.replace("_", " ") in _ALIAS_INDEX:
        return _ALIAS_INDEX[key.replace("_", " ")]
    raise UnknownPlatform(
        f"unknown platform {value!r}. Known: {', '.join(sorted(PLATFORMS))}"
        f" (aliases accepted: {', '.join(sorted(a for p in PLATFORMS.values() for a in p.aliases))})"
    )


def resolve(value: str | None) -> Platform:
    return PLATFORMS[normalise(value)]


def runner_for(value: str | None) -> str:
    return resolve(value).runner


def selectable() -> list[Platform]:
    """Platforms the UI should offer — masked ones excluded."""
    return [p for p in PLATFORMS.values() if p.enabled]


def as_dicts(include_disabled: bool = True) -> list[dict]:
    """Serialisable form for the API, so the UI never hardcodes this list."""
    return [
        {"name": p.name, "label": p.label, "runner": p.runner,
         "default_device": p.default_device, "device": p.default_device,
         "browsers": list(p.browsers), "enabled": p.enabled}
        for p in PLATFORMS.values()
        if include_disabled or p.enabled
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Devices
# ─────────────────────────────────────────────────────────────────────────────
# A shortlist for the picker. Playwright ships ~143 profiles, but most are
# landscape duplicates or decade-old hardware; offering all of them makes the
# dropdown unusable. Anything not listed here is still perfectly valid — the
# lookup goes to Playwright, not to this tuple, so a free-text device name works.
CURATED_DEVICES: tuple = (
    "iPhone 15", "iPhone 15 Pro Max", "iPhone 14", "iPhone 13 Pro",
    "iPhone SE", "iPad Pro 11", "iPad Mini",
    "Pixel 7", "Pixel 5", "Galaxy S9+", "Galaxy Tab S4", "Nexus 10",
)


_DEVICE_CATALOGUE: dict | None = None


def _device_catalogue() -> dict:
    """
    Playwright's device registry, read once per process.

    Reading it starts a Playwright driver (a node process) and takes 1-3 s;
    Run Center asked for it on every page load, which blocked the UI's event
    loop long enough for the page to time out and reload itself in a loop.
    The registry is a constant of the installed Playwright, so cache it.
    """
    global _DEVICE_CATALOGUE
    if _DEVICE_CATALOGUE is None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            _DEVICE_CATALOGUE = {}
        else:
            with sync_playwright() as pw:
                _DEVICE_CATALOGUE = {k: dict(v) for k, v in pw.devices.items()}
    return _DEVICE_CATALOGUE


def devices(curated_only: bool = True) -> list[dict]:
    """
    Device profiles a mobile-web run can emulate.

    Each entry carries the engine the device expects, so the caller can see WHY
    an iPhone run launches WebKit rather than having to know the pairing.
    """
    catalogue = _device_catalogue()
    if not catalogue:
        return []
    out = []
    names = [n for n in CURATED_DEVICES if n in catalogue] if curated_only else list(catalogue)
    for n in names:
        d = catalogue[n]
        vp = d.get("viewport") or {}
        out.append({
            "name": n,
            "browser": d.get("default_browser_type", "chromium"),
            "viewport": f"{vp.get('width')}x{vp.get('height')}",
            "mobile": bool(d.get("is_mobile")),
            "curated": n in CURATED_DEVICES,
        })
    return out


def browser_for_device(device_name: str | None, explicit: str | None = None,
                       device_catalogue=None) -> str:
    """
    Which engine to launch.

    Precedence: an explicit choice always wins; otherwise the device's own
    declared engine; otherwise Chromium. This is what makes selecting "iPhone 15"
    produce a real Safari/WebKit page instead of Chromium in disguise.

    `device_catalogue` MUST be passed by any caller that already holds a running
    Playwright instance (i.e. browser_manager). Opening a second sync_playwright()
    inside a live session raises, and swallowing that error would silently return
    "chromium" — reintroducing the exact bug this function exists to fix. It is
    only looked up here when no catalogue is supplied, e.g. from the API.
    """
    if explicit and str(explicit).strip():
        return str(explicit).strip().lower()
    if not device_name:
        return "chromium"

    name = str(device_name).strip()
    if device_catalogue is not None:
        d = device_catalogue.get(name)
        return d.get("default_browser_type", "chromium") if d else "chromium"

    d = _device_catalogue().get(name)      # cached registry (see _device_catalogue)
    return d.get("default_browser_type", "chromium") if d else "chromium"
