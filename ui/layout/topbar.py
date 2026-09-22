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


#: Per-client handle on the "Run <flow>" button, so a page can retarget it.
_RUN_BUTTONS: dict = {}


def set_current_flow(flow: str) -> None:
    """Point this client's top-bar Run button at `flow` (no-op if none is shown)."""
    entry = _RUN_BUTTONS.get(ui.context.client.id)
    if not entry or not flow:
        return
    button, holder = entry
    holder["flow"] = flow
    button.set_text(f"Run {flow}")


def _quick_run(route: str, current_flow: str = "", current_platform: str = "") -> None:
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

    # A page with a test case OPEN gets a button that runs that test case on
    # the page's platform, via Run Center so device and values can be checked.
    # Before this, the button on /platform/mobilesite?flow=X read "Run <last
    # run's flow>" and launched a different flow on a different platform — a
    # mobilesite test appeared to "run in website mode".
    if current_flow:
        holder = {"flow": current_flow}
        button = ui.button(f"Run {current_flow}", icon="play_arrow",
                           on_click=lambda: ui.navigate.to(
                               f"{route}?flow={holder['flow']}&platform={current_platform}")) \
            .props("dense unelevated").style(f"background:{COLORS['primary']}")
        button.tooltip(f"Run {current_flow} on {current_platform or 'its platform'} — opens Run Center")
        # Let the page retarget the button when the author opens another test case.
        _RUN_BUTTONS[ui.context.client.id] = (button, holder)
        return

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


def _restart_button() -> None:
    """
    Restart the server from the page it is serving.

    The page that presses this is the one whose connection is about to be cut,
    so it cannot simply wait for a reply — there will not be one. It asks, then
    polls /health from the browser until a NEW process answers, then reloads
    itself. The overlay exists so a dead page does not look like a frozen one.
    """
    from ui import api_client as api

    async def go(force: bool = False) -> None:
        try:
            res = await api.restart_server(force=force)
        except api.ApiError as e:
            # 409 = a test is still executing. Say what it would interrupt
            # rather than restarting and leaving an orphaned browser behind.
            if e.status == 409:
                _confirm_over_running(str(e.detail))
                return
            ui.notify(f"Could not restart: {e.detail}", type="negative")
            return
        port = res.get("port", "")
        interrupted = res.get("interrupted_runs") or []
        if interrupted:
            ui.notify(f"Interrupted {len(interrupted)} running test(s)",
                      type="warning")
        # From here the server is going away, so everything is done in the
        # browser: poll until something answers, then reload.
        ui.run_javascript(f"""
            (function () {{
              const o = document.createElement('div');
              o.style.cssText = 'position:fixed;inset:0;z-index:99999;display:flex;'
                + 'align-items:center;justify-content:center;flex-direction:column;'
                + 'gap:10px;background:rgba(15,23,42,.82);color:#fff;'
                + 'font-family:system-ui,sans-serif';
              o.innerHTML = '<div style="font-size:1.1rem">Restarting the server…</div>'
                + '<div id="rs-msg" style="font-size:.85rem;opacity:.75">'
                + 'waiting for it to come back on port {port}</div>';
              document.body.appendChild(o);
              const msg = o.querySelector('#rs-msg');
              let tries = 0;
              // The old process answers /health for about a second more, so the
              // first successes are ignored — otherwise the page reloads into
              // the process that is on its way out.
              const startedAt = Date.now();
              (function poll() {{
                tries++;
                if (tries > 90) {{
                  msg.textContent = 'It has not come back. Start it from a terminal.';
                  return;
                }}
                fetch('/health', {{cache: 'no-store'}})
                  .then(r => {{
                    if (r.ok && Date.now() - startedAt > 2500) {{
                      msg.textContent = 'back — reloading';
                      setTimeout(() => location.reload(), 400);
                    }} else {{ setTimeout(poll, 700); }}
                  }})
                  .catch(() => setTimeout(poll, 700));
              }})();
            }})();
        """)

    def _confirm_over_running(detail: str) -> None:
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:32rem"):
            ui.label("A test is still running").style(
                f"font-size:{TYPOGRAPHY['size_lg']};"
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            ui.label(detail).style(
                f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Restart anyway",
                          on_click=lambda: (dialog.close(), go(True))) \
                    .props("unelevated color=negative")
        dialog.open()

    def ask() -> None:
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:32rem"):
            ui.label("Restart the server?").style(
                f"font-size:{TYPOGRAPHY['size_lg']};"
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            ui.label("Picks up code changes without a terminal. This page "
                     "reconnects on its own once it is back.").style(
                f"font-size:{TYPOGRAPHY['size_sm']}")
            ui.label("Unsaved steps in an open editor are lost, and any test "
                     "mid-run is stopped.").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['warning']}")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Restart", icon="restart_alt",
                          on_click=lambda: (dialog.close(), go(False))) \
                    .props("unelevated")
        dialog.open()

    ui.button(icon="restart_alt", on_click=ask).props("flat dense") \
        .tooltip("Restart the server — picks up code changes")


def topbar(breadcrumb: list[str], *, platforms: list[dict] | None = None,
           platform: str = "", on_platform_change: Callable[[str], None] | None = None,
           quick_run_route: str = "/run", current_flow: str = "") -> None:
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
            _quick_run(quick_run_route, current_flow=current_flow,
                       current_platform=platform)
            _restart_button()
            ui.button(icon="settings", on_click=lambda: ui.navigate.to("/settings")) \
                .props("flat dense").tooltip("Settings")
