"""
ui/components/status_chip.py — Status Badge Chip

A small coloured pill showing test execution status.

Statuses (from ui/theme.STATUS_COLOR): PASS, FAIL, RUNNING, SKIPPED, STOPPED,
PENDING. Case-insensitive, and the API's past-tense spellings ("passed",
"failed", "done") are accepted so callers never have to normalise first.

Usage:
    from ui.components.status_chip import status_chip
    status_chip("pass")
    status_chip("running")            # amber, pulsing

Props:
    status:  str
    animate: bool — RUNNING pulses (default True)
    size:    "sm" | "md" | "lg" (default "md")
    tooltip: str — optional hover explanation

Also exports mapping_chip() for per-step GENERATION status, which is a different
vocabulary (SUPPORTED / NEEDS_LOCATOR / …) and must not be shown in the same
colours as a pass/fail — a step needing a locator has not failed.

Used in: dashboard.py, history.py, reports/index.py, executions/live.py
"""
from __future__ import annotations

from nicegui import ui

from ui.theme import MAPPING_COLOR, MAPPING_HINT, TYPOGRAPHY, status_color

ICON = {
    "pass": "✅", "passed": "✅", "done": "✅",
    "fail": "❌", "failed": "❌",
    "running": "▶", "skipped": "⊘", "ignored": "⚠", "stopped": "⏹", "pending": "⏳",
}
_SIZE = {"sm": ("0.68rem", "1px 7px"), "md": ("0.75rem", "2px 10px"),
         "lg": ("0.875rem", "4px 14px")}


def status_chip(status: str, *, animate: bool = True, size: str = "md",
                tooltip: str = "") -> ui.element:
    key = (status or "pending").strip().lower()
    colour = status_color(key)
    font, pad = _SIZE.get(size, _SIZE["md"])
    label = f"{ICON.get(key, '•')} {key.upper()}"

    chip = ui.label(label).style(
        f"background:{colour}1A; color:{colour}; border:1px solid {colour}55;"
        f"border-radius:999px; padding:{pad}; font-size:{font};"
        f"font-weight:{TYPOGRAPHY['weight_medium']}; white-space:nowrap;"
        f"font-family:{TYPOGRAPHY['family']};"
    )
    if animate and key == "running":
        chip.style("animation: sc-pulse 1.4s ease-in-out infinite")
        ui.add_head_html(
            "<style>@keyframes sc-pulse{0%,100%{opacity:1}50%{opacity:.45}}</style>"
        )
    if tooltip:
        chip.tooltip(tooltip)
    return chip


def mapping_chip(status: str, *, note: str = "", size: str = "sm") -> ui.element:
    """
    Chip for a GENERATION status, with the reason on hover.

    Deliberately separate from status_chip: an operator reads a red FAIL as
    "the test broke", and a step that merely needs a locator has not broken.
    """
    key = (status or "").strip().upper()
    colour = MAPPING_COLOR.get(key, "#64748B")
    font, pad = _SIZE.get(size, _SIZE["sm"])
    chip = ui.label(key.replace("_", " ")).style(
        f"background:{colour}1A; color:{colour}; border:1px solid {colour}55;"
        f"border-radius:4px; padding:{pad}; font-size:{font}; white-space:nowrap;"
        f"font-family:{TYPOGRAPHY['mono']};"
    )
    hint = MAPPING_HINT.get(key, "")
    tip = " — ".join(x for x in (hint, note) if x)
    if tip:
        chip.tooltip(tip)
    return chip
