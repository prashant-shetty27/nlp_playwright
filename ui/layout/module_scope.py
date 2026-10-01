"""
ui/layout/module_scope.py — pages that show one module at a time.

Whatever is created in a module stays in it: Test Data, data sets, step groups,
suites, plans, history and reports each list only the chosen module's items.
The module comes from the URL (?module=), set by the sidebar from the module
last picked in the topbar, and the topbar switcher on these pages changes it.
"""
from __future__ import annotations

from nicegui import ui

MODULES = ("website", "mobilesite", "android", "ios", "hybrid")
SHORT = {"website": "Web", "mobilesite": "MSite", "android": "Android", "ios": "iOS",
         "hybrid": "Hybrid"}


def pick(module: str, platforms: list[dict] | None) -> str:
    """The module to show: the one asked for, else the first enabled one."""
    m = (module or "").lower()
    if m in MODULES:
        return m
    enabled = [p["name"] for p in (platforms or []) if p.get("enabled", True)]
    return enabled[0] if enabled else "website"


def switcher(route: str, extra: str = ""):
    """on_platform_change for topbar(): reopen this page on the other module."""
    def go(value: str) -> None:
        ui.navigate.to(f"{route}?module={value}{extra}")
    return go


def belongs(item_platform: str, module: str) -> bool:
    """'mobilesite' / 'website, mobilesite' (a mixed plan) → is it in this module?"""
    parts = {p.strip().lower() for p in str(item_platform or "").split(",") if p.strip()}
    return not parts or module in parts
