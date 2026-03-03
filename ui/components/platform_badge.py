"""
ui/components/platform_badge.py — Platform Badge chip

A small colored chip showing "Web", "Android", or "iOS".
Color is sourced from ui/theme.PLATFORM_COLOR.

Usage:
    from ui.components.platform_badge import platform_badge
    platform_badge("android")   # renders green "Android" chip
    platform_badge("ios")       # renders purple "iOS" chip
    platform_badge("web")       # renders blue "Web" chip

Props:
    platform: Literal["web", "android", "ios"]
    size: "sm" | "md" (default "md")

Used in: plans/index.py, history.py, reports/index.py, dashboard.py
"""
