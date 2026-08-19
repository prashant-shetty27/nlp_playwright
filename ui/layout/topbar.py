"""
ui/layout/topbar.py — Top application bar

Contents (left → right): breadcrumb trail, platform switcher, environment badge,
Quick Run, settings.

The platform switcher offers only ENABLED platforms — offering a masked one would
produce a 422 from the API, and an option that always errors is worse than an
absent one. The sidebar still lists the masked platforms so they remain visible.
"""
from __future__ import annotations

from typing import Callable

from nicegui import ui

from ui.theme import COLORS, TYPOGRAPHY


def topbar(breadcrumb: list[str], *, platforms: list[dict] | None = None,
           platform: str = "", on_platform_change: Callable[[str], None] | None = None,
           quick_run_route: str = "/run") -> None:
    selectable = [p for p in (platforms or []) if p.get("enabled", True)]
    with ui.header(fixed=True).props("bordered").style(
            f"background:{COLORS['surface']}; color:{COLORS['text']}"):
        with ui.row().classes("w-full items-center gap-3 px-3 py-1"):
            for i, crumb in enumerate(breadcrumb):
                if i:
                    ui.label("/").style(f"color:{COLORS['text_muted']}")
                ui.label(crumb).style(
                    f"font-size:{TYPOGRAPHY['size_sm']};"
                    f"color:{COLORS['text'] if i == len(breadcrumb) - 1 else COLORS['text_muted']}")
            ui.space()
            if selectable and on_platform_change:
                ui.select(
                    {p["name"]: p.get("label", p["name"]) for p in selectable},
                    value=platform or selectable[0]["name"],
                    on_change=lambda e: on_platform_change(e.value),
                ).props("outlined dense options-dense").style("min-width:11rem")
            ui.button("Run", icon="play_arrow",
                      on_click=lambda: ui.navigate.to(quick_run_route)) \
                .props("dense unelevated").style(f"background:{COLORS['primary']}")
            ui.button(icon="settings", on_click=lambda: ui.navigate.to("/settings")) \
                .props("flat dense").tooltip("Settings")
