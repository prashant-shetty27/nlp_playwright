"""
ui/layout/topbar.py — Top application bar

Contents (left → right): breadcrumb trail, platform switcher, environment badge,
Quick Run, settings.

Quick Run repeats the most recent run — same flow, same settings — and lands on
the live view. Until something has been run it stays a plain Run that opens Run
Center; see _quick_run.

The platform switcher offers only ENABLED platforms — offering a masked one would
produce a 422 from the API, and an option that always errors is worse than an
absent one. The sidebar still lists the masked platforms so they remain visible.
"""
from __future__ import annotations

from typing import Callable

from nicegui import ui

from ui.theme import COLORS, TYPOGRAPHY


def _quick_run(route: str) -> None:
    """
    Repeat the last run, in one click, from anywhere.

    The button used to navigate to Run Center and stop there — so "run it again"
    was five controls and a form, every time, to reproduce settings the tool had
    already recorded. It now launches the most recent run's flow with the setup
    that run used, and goes straight to the live view.

    It only claims to be a Quick Run once there IS a last run to repeat: with an
    empty history it stays a plain Run and opens Run Center, because a one-click
    button that launches something unspecified is worse than a form.
    """
    from ui import api_client as api

    state: dict = {}

    async def launch() -> None:
        """One handler, deciding on state — not two handlers racing."""
        flow = state.get("flow")
        if not flow:
            ui.navigate.to(route)       # nothing to repeat yet
            return
        setup = state.get("setup") or {}
        # A secret is recorded by name only and has to be typed again, so a
        # flow needing one goes to the form rather than failing mid-run.
        if setup.get("needs_secrets"):
            ui.notify(f"{flow} needs {', '.join(setup['needs_secrets'])} — "
                      f"enter it in Run Center", type="warning")
            ui.navigate.to(f"{route}?flow={flow}&platform={setup.get('platform','website')}")
            return
        ui.notify(f"Starting {flow}…", type="info")
        try:
            res = await api.run(
                flow, setup.get("platform", "website"),
                headless=bool(setup.get("headless", True)),
                device_name=setup.get("device_name", "") or "",
                browser=setup.get("browser", "") or "",
                parameters=setup.get("parameters", {}) or {},
                browser_permissions=setup.get("browser_permissions", "") or "")
        except api.ApiError as e:
            # Most commonly a value the flow needs and the store cannot supply.
            ui.notify(str(e.detail)[:200], type="negative", timeout=9000)
            ui.navigate.to(f"{route}?flow={flow}&platform={setup.get('platform','website')}")
            return
        ui.navigate.to(f"/run/live?run_id={res['run_id']}&flow={flow}"
                       f"&platform={setup.get('platform', 'website')}")

    async def arm() -> None:
        """
        Turn the button into a Quick Run, but only if there is a run to repeat.

        Runs from a timer after the page is built, so `button` below exists by
        the time this executes.
        """
        try:
            history = await api.run_history(limit=1)
            if not history:
                return
            flow = (history[0].get("flow") or "").strip()
            if not flow:
                return
            setup = await api.last_setup(flow)
        except api.ApiError:
            return                      # leave it as the plain Run button
        state.update(flow=flow, setup=setup or {})
        button.set_text(f"Run {flow}")
        button.tooltip(f"Repeat the last run — {flow}"
                       + (f" on {setup.get('platform')}" if setup.get("platform") else ""))

    button = ui.button("Run", icon="play_arrow", on_click=launch) \
        .props("dense unelevated").style(f"background:{COLORS['primary']}")
    button.tooltip("Open Run Center")
    # Run Center is still one click away — Quick Run replaces the button's
    # meaning, and taking away the only route to the form would be a downgrade.
    ui.button(icon="tune", on_click=lambda: ui.navigate.to(route)) \
        .props("flat dense").tooltip("Run Center — choose flow, device and values")

    ui.timer(0.2, arm, once=True)


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
            _quick_run(quick_run_route)
            ui.button(icon="settings", on_click=lambda: ui.navigate.to("/settings")) \
                .props("flat dense").tooltip("Settings")
