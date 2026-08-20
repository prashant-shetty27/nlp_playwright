"""
ui/app.py — Main application entry point

Initialises NiceGUI, registers page routes, and mounts them on the FastAPI backend.

Usage:
    python -m ui.app                 # start the UI (default port 8080)
    python -m ui.app --port 9000

NiceGUI is mounted ON the FastAPI app rather than run beside it, so the REST API
and the UI share one process and one port: the API is reachable at its own paths
for scripts and CI, and the UI calls it in-process over ASGI with no network hop.

Routes registered here (each delegates to a pages/ module):
    /                          → pages/dashboard.py
    /platform/{platform}       → pages/platform/test_cases.py
    /run                       → pages/executions/run_center.py
    /run/live                  → pages/executions/live.py
    /history                   → pages/executions/history.py      (not yet built)
    /reports                   → pages/reports/index.py           (not yet built)
    /settings                  → pages/settings/environments.py   (not yet built)

Divergence from the spec: the spec listed nine per-platform routes
(/platform/web, /platform/android, …) pointing at separate modules. Platform is a
path parameter on one module instead, and it is validated against
nlp/platforms.py — an unknown or not-yet-enabled platform gets an explanation
rather than a blank page. Screens the spec lists but which are not built yet say
so plainly instead of 404-ing.
"""
from __future__ import annotations

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from nicegui import ui  # noqa: E402

from api.app import app as fastapi_app  # noqa: E402
from ui.theme import COLORS, TYPOGRAPHY  # noqa: E402


def _page_shell() -> None:
    ui.add_head_html(
        f"<style>body{{font-family:{TYPOGRAPHY['family']};"
        f"background:{COLORS['surface_alt']};color:{COLORS['text']}}}</style>")


def _not_built(name: str, spec_path: str) -> None:
    """
    An honest placeholder.

    A screen the spec describes but which has no implementation says so, and says
    where its specification lives. A blank page or a 404 would read as a bug.
    """
    from ui.layout.sidebar import sidebar
    from ui.layout.topbar import topbar
    sidebar()
    topbar([name])
    with ui.column().classes("w-full items-center gap-2").style("padding:4rem"):
        ui.icon("construction").style(f"font-size:2.5rem;color:{COLORS['text_muted']}")
        ui.label(f"{name} is not built yet").style(
            f"font-size:{TYPOGRAPHY['size_lg']};font-weight:{TYPOGRAPHY['weight_bold']}")
        ui.label(f"Its specification is in {spec_path}").style(
            f"font-size:{TYPOGRAPHY['size_sm']};color:{COLORS['text_muted']};"
            f"font-family:{TYPOGRAPHY['mono']}")
        ui.button("Back to Dashboard", on_click=lambda: ui.navigate.to("/")) \
            .props("flat")


@ui.page("/")
async def index() -> None:
    _page_shell()
    from ui.pages.dashboard import render
    await render()


@ui.page("/platform/{platform}")
async def platform_page(platform: str, flow: str = "") -> None:
    _page_shell()
    from nlp.platforms import PLATFORMS, UnknownPlatform, normalise
    try:
        canonical = normalise(platform)
    except UnknownPlatform as e:
        _not_built("Unknown platform", str(e)[:120])
        return
    if not PLATFORMS[canonical].enabled:
        from ui.layout.sidebar import sidebar
        from ui.layout.topbar import topbar
        sidebar()
        topbar(["Author", PLATFORMS[canonical].label])
        with ui.column().classes("w-full items-center gap-2").style("padding:4rem"):
            ui.icon("lock").style(f"font-size:2.5rem;color:{COLORS['text_muted']}")
            ui.label(f"{PLATFORMS[canonical].label} is not enabled yet").style(
                f"font-size:{TYPOGRAPHY['size_lg']}")
            ui.label("The runner supports it; the platform is masked in this build.") \
                .style(f"font-size:{TYPOGRAPHY['size_sm']};color:{COLORS['text_muted']}")
        return
    from ui.pages.platform.test_cases import render
    # `flow` comes from the query string so the open test case survives a
    # reload or a websocket reconnect. Held only in server memory, it was
    # lost on either, and the editor came back blank with no explanation.
    await render(canonical, flow)


@ui.page("/run")
async def run_page(flow: str = "", platform: str = "website") -> None:
    _page_shell()
    from ui.pages.executions.run_center import render
    await render(flow, platform)


@ui.page("/run/live")
async def live_page(run_id: str = "", flow: str = "",
                   platform: str = "website") -> None:
    _page_shell()
    if not run_id:
        ui.navigate.to("/run")
        return
    from ui.pages.executions.live import render
    await render(run_id, flow, platform)


@ui.page("/step-groups")
async def step_groups_page(platform: str = "website") -> None:
    _page_shell()
    from ui.pages.platform.step_groups import render
    await render(platform)


@ui.page("/history")
async def history_page() -> None:
    _page_shell()
    from ui.pages.executions.history import render
    await render()


@ui.page("/reports")
async def reports_page() -> None:
    _page_shell()
    _not_built("Reports", "ui/pages/reports/index.py")


@ui.page("/reports/{run_id}")
async def report_detail_page(run_id: str) -> None:
    """
    One run, step by step, with the screenshot each step saw.

    Registered even though /reports itself is still a placeholder: the list is
    a convenience, but this page is how a failure gets diagnosed, and History
    already links straight to it.
    """
    _page_shell()
    from ui.pages.reports.detail import render
    await render(run_id)


@ui.page("/data/variables")
async def variables_page() -> None:
    _page_shell()
    from ui.pages.data.variables import render
    await render()


@ui.page("/platform/{platform}/elements")
async def elements_page(platform: str, edit: str = "") -> None:
    _page_shell()
    from nlp.platforms import UnknownPlatform, normalise
    try:
        canonical = normalise(platform)
    except UnknownPlatform as e:
        _not_built("Unknown platform", str(e)[:120])
        return
    from ui.pages.platform.elements import render
    # ?edit=<name> opens straight into that element — how the step editor sends
    # you here from a locator token.
    await render(canonical, edit)


@ui.page("/settings")
async def settings_page() -> None:
    _page_shell()
    _not_built("Settings", "ui/pages/settings/environments.py")


def main() -> None:
    ap = argparse.ArgumentParser(description="Codeless automation UI")
    ap.add_argument("--port", type=int, default=int(os.getenv("UI_PORT", "8080")))
    ap.add_argument("--host", default=os.getenv("UI_HOST", "127.0.0.1"))
    ap.add_argument("--show", action="store_true", help="open a browser on start")
    args = ap.parse_args()
    ui.run_with(fastapi_app, title="Codeless Automation", favicon="🧪")
    import uvicorn
    uvicorn.run(fastapi_app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
