"""
ui/theme.py — Global theme constants

Defines colors, typography, spacing, and dark/light mode tokens
used consistently across all NiceGUI pages and components.

Sections:
    COLORS      — Primary, accent, surface, status colors per platform
    TYPOGRAPHY  — Font family, sizes, weights
    PLATFORM    — Per-platform accent colors (web=blue, android=green, ios=purple)
    STATUS      — PASS=green, FAIL=red, RUNNING=amber, SKIPPED=grey

Usage:
    from ui.theme import COLORS, PLATFORM_COLOR
    ui.label("Web").style(f"color: {PLATFORM_COLOR['web']}")
"""

COLORS = {}
TYPOGRAPHY = {}
PLATFORM_COLOR = {
    "web": "#2563EB",       # blue-600
    "android": "#16A34A",   # green-600
    "ios": "#7C3AED",       # violet-600
}
STATUS_COLOR = {
    "pass": "#16A34A",
    "fail": "#DC2626",
    "running": "#D97706",
    "skipped": "#6B7280",
}
