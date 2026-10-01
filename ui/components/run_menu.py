"""
ui/components/run_menu.py — "▾" beside a Run button: run with a saved configuration.

Lists this module's saved run configurations (mine + shared). Picking one opens
Run Center with that configuration applied and starts the run straight away —
unless the test still needs a value typed there, when the form stays open.
"""
from __future__ import annotations

from typing import Callable
from urllib.parse import quote

from nicegui import ui

from ui import api_client as api
from ui.theme import COLORS, TYPOGRAPHY


def config_menu(get_flow: Callable[[], str], platform: str, *, colour: str = "") -> None:
    btn = ui.button(icon="arrow_drop_down").props("dense unelevated") \
        .style(f"background:{colour or COLORS['primary']}; min-width:0; padding:0 2px") \
        .tooltip("Run with a saved configuration")
    with btn:
        menu = ui.menu().props("auto-close")

    async def fill() -> None:
        menu.clear()
        flow = get_flow() or ""
        try:
            configs = await api.run_configs(platform)
        except api.ApiError:
            configs = []
        with menu:
            ui.label("Run with a saved configuration").style(
                f"padding:6px 14px 2px; font-size:{TYPOGRAPHY['size_xs']};"
                f"color:{COLORS['text_muted']}")
            if not configs:
                ui.label("None saved yet — save one in Run Center (💾 next to "
                         "'Saved configuration').").style(
                    f"padding:4px 14px 8px; font-size:{TYPOGRAPHY['size_xs']}; max-width:20rem;"
                    f"color:{COLORS['text_muted']}")
            for c in configs:
                with ui.item(on_click=lambda c=c: ui.navigate.to(
                        f"/run?flow={quote(flow, safe='')}&platform={platform}"
                        f"&config={c['id']}&autorun=1")):
                    with ui.item_section():
                        ui.item_label(c["name"] + ("" if c.get("mine") else f" · {c.get('owner')}"))
                        ui.item_label(c.get("summary", "")).props("caption")
            ui.separator()
            ui.menu_item("Open Run Center…", on_click=lambda: ui.navigate.to(
                f"/run?flow={quote(flow, safe='')}&platform={platform}"))
    menu.on("before-show", fill)
