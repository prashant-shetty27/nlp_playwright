"""
ui/pages/dashboard.py — Dashboard  (route: /)

Landing view: what the project currently has, and the two ways in.

Deliberately not the spec's chart-heavy overview. There is no historical run data
to chart yet, and a dashboard of empty charts teaches nothing — this shows real
counts (platforms, locators, suggestions, saved flows, recent runs) plus the
entry points, and grows into charts once runs accumulate.
"""
from __future__ import annotations

from nicegui import ui

from ui import api_client as api
from ui.components.status_chip import status_chip
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.theme import COLORS, TYPOGRAPHY


def _tile(value: str, label: str, hint: str = "") -> None:
    with ui.column().classes("gap-0").style(
            f"border:1px solid {COLORS['border']}; border-radius:8px;"
            f"padding:12px 16px; min-width:9rem; background:{COLORS['surface']}"):
        ui.label(value).style(
            f"font-size:{TYPOGRAPHY['size_xl']};"
            f"font-weight:{TYPOGRAPHY['weight_bold']}; color:{COLORS['text']}")
        lab = ui.label(label).style(
            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
        if hint:
            lab.tooltip(hint)


async def render() -> None:
    try:
        platforms = await api.platforms()
        locators = await api.locator_names()
        projects = await api.list_projects()
        history = await api.run_history()
    except api.ApiError as e:
        ui.notify(f"Backend unreachable: {e.detail}", type="negative")
        platforms, locators, projects, history = [], [], [], []

    sidebar(active="/", platforms=platforms)
    topbar(["Dashboard"], platforms=platforms)

    with ui.column().classes("w-full gap-4 p-4"):
        with ui.row().classes("gap-3 flex-wrap"):
            _tile(str(len([p for p in platforms if p.get("enabled")])), "platforms enabled",
                  "Android, iOS and Hybrid are known but not enabled yet")
            _tile(str(len(locators)), "locators on file")
            _tile(str(len(projects)), "saved flows")
            _tile(str(len(history)), "runs recorded",
                  "Bounded store — oldest runs are evicted")

        with ui.row().classes("gap-3 flex-wrap"):
            with ui.column().classes("gap-2").style(
                    f"border:1px solid {COLORS['border']}; border-radius:8px;"
                    f"padding:16px; min-width:22rem"):
                ui.label("Author a test case").style(
                    f"font-weight:{TYPOGRAPHY['weight_bold']}")
                ui.label("From a spreadsheet, from a written prompt, or by hand — "
                         "all three land in the same reviewable step list.").style(
                    f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")
                ui.button("Open Test Cases", icon="list_alt",
                          on_click=lambda: ui.navigate.to("/platform/website")) \
                    .props("unelevated").style(f"background:{COLORS['primary']}")

            with ui.column().classes("gap-2").style(
                    f"border:1px solid {COLORS['border']}; border-radius:8px;"
                    f"padding:16px; min-width:22rem"):
                ui.label("Run something").style(
                    f"font-weight:{TYPOGRAPHY['weight_bold']}")
                ui.label("Pick a flow, choose device and browser, supply the values "
                         "it asks for, and watch it run.").style(
                    f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")
                ui.button("Open Run Center", icon="play_circle",
                          on_click=lambda: ui.navigate.to("/run")) \
                    .props("unelevated").style(f"background:{COLORS['success']}")

        if history:
            ui.label("Recent runs").style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
            with ui.column().classes("w-full gap-0").style(
                    f"border:1px solid {COLORS['border']}; border-radius:6px"):
                for r in history[:10]:
                    with ui.row().classes("w-full items-center gap-3").style(
                            f"padding:6px 10px; border-bottom:1px solid {COLORS['border']}"):
                        # /tests/history rows carry flow + summary{passed,failed,total};
                        # the old keys (project/failed/passed) never existed, so
                        # every run showed "—", PASSED and 0/0.
                        summ = r.get("summary") or {}
                        ui.label(str(r.get("flow") or r.get("project") or "—")).style(
                            f"font-family:{TYPOGRAPHY['mono']};"
                            f"font-size:{TYPOGRAPHY['size_sm']}; min-width:12rem")
                        failed = int(summ.get("failed", r.get("failed", 0)) or 0)
                        status_chip(r.get("status") or ("failed" if failed else "passed"), size="sm")
                        ui.label(f"{summ.get('passed', r.get('passed', 0))}/"
                                 f"{summ.get('total', r.get('total', 0))} steps").style(
                            f"font-size:{TYPOGRAPHY['size_xs']};"
                            f"color:{COLORS['text_muted']}")
                        ui.space()
                        run_id = r.get("run_id", "")
                        ui.button("View", on_click=lambda i=run_id:
                                  ui.navigate.to(f"/run/live?run_id={i}")) \
                            .props("flat dense size=sm")
