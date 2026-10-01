"""
ui/pages/executions/history.py — what has been run, and what happened.

Runs were being recorded all along — every execution writes a report to
data/logs/ with a per-step result and, for a failure, the reason. There was
simply no screen, so the record was invisible unless you went looking on disk.

The list answers "did it pass"; opening a row answers "which step, and why",
because that is the question you actually have after a failure.
"""
from __future__ import annotations

from nicegui import ui

from ui import api_client as api
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.theme import COLORS, TYPOGRAPHY

_STATUS_COLOUR = {"passed": COLORS["success"], "failed": COLORS["danger"],
                  "running": COLORS["primary"], "unreadable": COLORS["warning"]}


def _when(iso: str) -> str:
    """'2026-08-19T00:38:04' -> '19 Aug 00:38'. Never raises on odd input."""
    try:
        date, _, time = iso.partition("T")
        y, m, d = date.split("-")
        months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        return f"{int(d)} {months[int(m) - 1]} {time[:5]}"
    except Exception:  # noqa: BLE001
        return iso or "—"


async def render(module: str = "") -> None:
    from ui.layout import module_scope
    platforms = []
    try:
        platforms = await api.platforms()
    except api.ApiError:
        pass
    module = module_scope.pick(module, platforms)
    sidebar(active="/history", platforms=platforms)
    topbar(["Execute", "History"], platforms=platforms, platform=module,
           on_platform_change=module_scope.switcher("/history"))

    with ui.column().classes("w-full gap-2 p-4"):
        try:
            runs = (await api.run_history()) or []
            # This module's runs only (each run carries the platform of its test case).
            runs = [r for r in runs if module_scope.belongs(r.get("platform"), module)]
        except api.ApiError as e:
            ui.label(f"Could not read the run history: {e.detail[:140]}").style(
                f"color:{COLORS['danger']}")
            return

        if not runs:
            ui.label("Nothing has been run yet.").style(
                f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_sm']}")
            return

        # Grouped by test case, because that is the question being asked: not
        # "what ran at 14:07" but "how has THIS test been doing". A flat list of
        # fifty runs across a dozen flows answers neither.
        by_flow: dict[str, list[dict]] = {}
        for run in runs:
            by_flow.setdefault(run.get("flow") or "(unnamed)", []).append(run)

        ui.label(f"{len(runs)} run(s) across {len(by_flow)} test case(s)").style(
            f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_sm']}")

        for flow, flow_runs in sorted(by_flow.items(),
                                      key=lambda kv: kv[1][0].get("started_at", ""),
                                      reverse=True):
            _flow_block(flow, flow_runs)


def _flow_block(flow: str, runs: list[dict]) -> None:
    """One test case: its recent verdicts, a trend, and a way to run it again."""
    passed = sum(1 for r in runs if r.get("status") == "passed")
    latest = runs[0]
    colour = _STATUS_COLOUR.get(latest.get("status", ""), COLORS["text_muted"])

    with ui.column().classes("w-full gap-0").style(
            f"border:1px solid {COLORS['border']}; border-radius:8px; padding:2px"):
        with ui.row().classes("w-full items-center gap-3 no-wrap").style("padding:8px 10px"):
            ui.label(flow).style(
                f"font-family:{TYPOGRAPHY['mono']};"
                f"font-size:{TYPOGRAPHY['size_md']};"
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            ui.label(latest.get("status", "?").upper()).style(
                f"background:{colour}1A; color:{colour}; border-radius:4px;"
                f"padding:1px 8px; font-size:{TYPOGRAPHY['size_xs']};"
                f"font-family:{TYPOGRAPHY['mono']}")
            # Most recent first, so the shape of the last few runs reads left to right.
            ui.label(" ".join("✓" if r.get("status") == "passed" else "✗"
                              for r in runs[:10])).style(
                f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_sm']};"
                f"letter-spacing:1px")
            ui.label(f"{passed}/{len(runs)} passed").style(
                f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_xs']}")
            ui.space()
            _quick_run_button(flow)
            ui.button("Set up a run", icon="tune",
                      on_click=lambda f=flow: ui.navigate.to(f"/run?flow={f}")) \
                .props("flat dense")

        for run in runs:
            _row(run)


def _quick_run_button(flow: str) -> None:
    """
    Repeat the last run of this flow with the same setup.

    The point is to skip the form when nothing has changed. It refuses when the
    last run needed a secret — those are never stored, so "same setup" would be
    a lie — and sends you to the full form instead.
    """
    async def go() -> None:
        try:
            setup = await api.last_setup(flow)
        except api.ApiError as e:
            ui.notify(f"Could not read the last setup: {e.detail}", type="negative")
            return
        if not setup:
            ui.notify("This flow has no recorded setup yet — set one up once.",
                      type="warning")
            ui.navigate.to(f"/run?flow={flow}")
            return
        if setup.get("needs_secrets"):
            ui.notify("Last run used " + ", ".join(setup["needs_secrets"])
                      + " — secrets are never stored, so enter them again.",
                      type="warning", timeout=7000)
            ui.navigate.to(f"/run?flow={flow}")
            return
        try:
            res = await api.run(
                flow, setup.get("platform", "website"),
                headless=bool(setup.get("headless", True)),
                device_name=setup.get("device_name", ""),
                browser=setup.get("browser", ""),
                parameters=setup.get("parameters", {}),
                browser_permissions=setup.get("browser_permissions", ""))
        except api.ApiError as e:
            ui.notify(f"Could not start: {e.detail}", type="negative", timeout=8000)
            return
        ui.navigate.to(f"/run/live?run_id={res['run_id']}&flow={flow}"
                       f"&platform={setup.get('platform','website')}")

    ui.button("Quick Run", icon="bolt", on_click=go).props("flat dense") \
        .style(f"color:{COLORS['success']}") \
        .tooltip("Run again with exactly the same setup as last time")


def _row(run: dict) -> None:
    summary = run.get("summary") or {}
    passed, failed = summary.get("passed", 0), summary.get("failed", 0)
    colour = _STATUS_COLOUR.get(run.get("status", ""), COLORS["text_muted"])

    with ui.expansion().classes("w-full").style(
            f"border:1px solid {COLORS['border']}; border-radius:6px") as exp:
        with exp.add_slot("header"):
            with ui.row().classes("w-full items-center gap-3 no-wrap"):
                ui.label(run.get("status", "?").upper()).style(
                    f"background:{colour}1A; color:{colour}; border-radius:4px;"
                    f"padding:1px 8px; font-size:{TYPOGRAPHY['size_xs']};"
                    f"font-family:{TYPOGRAPHY['mono']}")
                ui.label(run.get("flow") or run.get("run_id", "")).style(
                    f"font-family:{TYPOGRAPHY['mono']};"
                    f"font-size:{TYPOGRAPHY['size_sm']}")
                ui.label(_when(run.get("started_at", ""))).style(
                    f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_xs']}")
                ui.space()
                if failed:
                    ui.label(f"{failed} failed").style(
                        f"color:{COLORS['danger']}; font-size:{TYPOGRAPHY['size_xs']}")
                ui.label(f"{passed}/{summary.get('total', 0)} passed").style(
                    f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_xs']}")
                # The step-by-step view with screenshots. Expanding the row here
                # still lists the steps; this is where you go to SEE them.
                ui.button("Open", icon="open_in_new",
                          on_click=lambda r=run.get("run_id", ""):
                              ui.navigate.to(f"/reports/{r}")) \
                    .props("flat dense").tooltip(
                        "Step-by-step, with the screenshot at each step")

        holder = ui.column().classes("w-full gap-0")

        async def load_steps() -> None:
            if holder.default_slot.children:
                return                      # already fetched; opening again is free
            try:
                detail = await api.run_result(run["run_id"])
            except api.ApiError as e:
                with holder:
                    ui.label(f"Could not open this report: {e.detail[:120]}").style(
                        f"color:{COLORS['danger']}; font-size:{TYPOGRAPHY['size_xs']}")
                return
            with holder:
                for i, step in enumerate(detail.get("results", []), 1):
                    ok = step.get("status") == "passed"
                    c = COLORS["success"] if ok else COLORS["danger"]
                    with ui.row().classes("w-full items-start gap-2").style(
                            f"padding:4px 10px; border-top:1px solid {COLORS['border']}"):
                        ui.label(str(i)).style(
                            f"width:1.6rem; text-align:right;"
                            f"color:{COLORS['text_muted']};"
                            f"font-family:{TYPOGRAPHY['mono']};"
                            f"font-size:{TYPOGRAPHY['size_xs']}")
                        ui.icon("check_circle" if ok else "cancel").style(
                            f"color:{c}; font-size:1rem")
                        with ui.column().classes("gap-0 flex-grow"):
                            ui.label(step.get("test_name", "")).style(
                                f"font-family:{TYPOGRAPHY['mono']};"
                                f"font-size:{TYPOGRAPHY['size_sm']}")
                            # The reason is the reason the screen exists.
                            if step.get("reason"):
                                ui.label(step["reason"]).style(
                                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{c}")

        exp.on("click", load_steps)
