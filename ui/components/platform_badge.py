"""
ui/components/platform_badge.py — Platform Badge

Small coloured badge naming the platform a flow or run targets.

Usage:
    from ui.components.platform_badge import platform_badge
    platform_badge("mobilesite")
    platform_badge("android", disabled=True)     # masked platforms read as muted

Props:
    platform: str — canonical name from nlp/platforms.py
    disabled: bool — render muted, for platforms that exist but are not enabled
    device:   str — optional device name appended, e.g. "Mobile Site · iPhone 15"
"""
from __future__ import annotations

from nicegui import ui

from ui.theme import TYPOGRAPHY, platform_color

#: Display names. Sourced from the platform table's own labels at runtime where
#: possible; this map is the offline fallback so a badge never renders a raw slug.
LABEL = {
    "website": "Website", "mobilesite": "Mobile Site", "android": "Android App",
    "ios": "iOS App", "hybrid": "Hybrid", "web": "Website", "mobile": "Mobile Site",
}


def platform_badge(platform: str, *, disabled: bool = False,
                   device: str = "") -> ui.element:
    key = (platform or "").strip().lower()
    colour = "#94A3B8" if disabled else platform_color(key)
    text = LABEL.get(key, key or "unknown")
    if device:
        text = f"{text} · {device}"
    badge = ui.label(text).style(
        f"background:{colour}1A; color:{colour}; border:1px solid {colour}55;"
        f"border-radius:6px; padding:2px 9px; font-size:{TYPOGRAPHY['size_xs']};"
        f"font-weight:{TYPOGRAPHY['weight_medium']}; white-space:nowrap;"
        f"font-family:{TYPOGRAPHY['family']};"
    )
    if disabled:
        badge.tooltip("Known platform, not enabled yet")
    return badge
