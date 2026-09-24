"""
ui/pages/executions/live.py — Live Execution View  (route: /run/live)

Real-time view while a test run is in progress.

Layout (as specified):
    Top    — Run ID | platform badge | flow name | start time
    Left   — step progress list: status icon, step text, duration, expandable
             detail (action, locator, error)
    Right  — tabbed panels: log output, screenshots
    Bottom — progress bar, status, elapsed

Divergences from the spec, deliberate:

  POLLS GET /tests/results INSTEAD OF TAILING A SUBPROCESS. The spec streamed
  stdout from the runner process. Runs now execute inside the API, which records
  a structured per-step result — polling that yields the step's status, error and
  line number rather than text to be scraped.

  NO [Stop Run]. There is no cancel endpoint, so a Stop button would either do
  nothing or kill the server. Better absent than lying: the run's own timeouts
  bound it. Worth adding once the API can cancel.

  NO LIVE DEVICE SCREENSHOT TAB. Nothing streams frames yet; the screenshots the
  run captures are shown as they appear instead. An empty pane labelled "Device"
  would suggest a feature that does not exist.
"""
from __future__ import annotations

import time
from urllib.parse import quote

from nicegui import ui

from ui import api_client as api
from ui.components.log_viewer import log_viewer
from ui.components.platform_badge import platform_badge
from ui.components.status_chip import status_chip
from ui.theme import COLORS, TYPOGRAPHY



def _back_to_test_case(flow: str, platform: str) -> None:
    """
    A link back to the test case being run.

    Running a flow left you on the run screen with no route back to the thing
    you had just been editing — the browser Back button was the only way, and it
    does not survive a redirect to the live view.

    The flow name travels in the query string. Without it the link landed on the
    Test Cases screen with nothing open — a link that says "Back to X" and then
    does not show X reads as a failure, not as navigation.
    """
    if not flow:
        return
    href = f"/platform/{platform}?flow={quote(flow, safe='')}"
    with ui.row().classes("items-center gap-1 cursor-pointer") \
            .on("click", lambda: ui.navigate.to(href)):
        ui.icon("arrow_back").style(f"font-size:1rem; color:{COLORS['primary']}")
        ui.label(f"Back to {flow}").style(
            f"color:{COLORS['primary']}; font-size:{TYPOGRAPHY['size_sm']};"
            f"text-decoration:underline; text-underline-offset:3px")


class LiveView:
    #: Fast enough to feel live, slow enough that a long run does not hammer the
    #: API with hundreds of identical polls.
    POLL_S = 1.0

    def __init__(self, run_id: str, flow: str, platform: str) -> None:
        self.run_id = run_id
        self.flow = flow
        self.platform = platform
        self.started = time.time()
        self.done = False
        self.seen = 0
        self.shots_seen = 0

    def render(self) -> None:
        from ui.layout.sidebar import sidebar
        from ui.layout.topbar import topbar
        sidebar(active="/run")
        topbar(["Execute", "Live"], quick_run_route="/run",
               platform=self.platform if hasattr(self, "platform") else "",
               current_flow=self.flow if hasattr(self, "flow") else "")
        with ui.row().classes("w-full items-center gap-3").style("padding:8px 16px 0"):
            _back_to_test_case(self.flow if hasattr(self, "flow") else "",
                               self.platform if hasattr(self, "platform") else "website")

        with ui.column().classes("w-full gap-3 p-4"):
            with ui.row().classes("w-full items-center gap-3"):
                ui.label(self.flow or "run").style(
                    f"font-size:{TYPOGRAPHY['size_lg']};"
                    f"font-weight:{TYPOGRAPHY['weight_bold']};"
                    f"font-family:{TYPOGRAPHY['mono']}")
                platform_badge(self.platform)
                ui.label(self.run_id).style(
                    f"font-family:{TYPOGRAPHY['mono']};"
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
                ui.space()
                self.chip_holder = ui.row()
                with self.chip_holder:
                    status_chip("running")

            with ui.row().classes("w-full no-wrap gap-4"):
                with ui.column().classes("flex-grow gap-1"):
                    ui.label("Steps").style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
                    self.steps_area = ui.column().classes("w-full gap-0").style(
                        f"border:1px solid {COLORS['border']}; border-radius:6px;"
                        f"max-height:30rem; overflow-y:auto")
                    with self.steps_area:
                        ui.label("Waiting for the first step…").style(
                            f"padding:10px; color:{COLORS['text_muted']};"
                            f"font-size:{TYPOGRAPHY['size_sm']}")
                with ui.column().style("width:30rem; flex:none"):
                    with ui.tabs().classes("w-full") as tabs:
                        t_log = ui.tab("Log", icon="terminal")
                        t_shot = ui.tab("Screenshots", icon="image")
                    with ui.tab_panels(tabs, value=t_log).classes("w-full"):
                        with ui.tab_panel(t_log):
                            self.log = log_viewer()
                        with ui.tab_panel(t_shot):
                            self.shots = ui.column().classes("w-full gap-1")
                            with self.shots:
                                ui.label("Screenshots appear here as they are taken.") \
                                    .style(f"font-size:{TYPOGRAPHY['size_xs']};"
                                           f"color:{COLORS['text_muted']}")

            with ui.row().classes("w-full items-center gap-3"):
                self.bar = ui.linear_progress(value=0, show_value=False) \
                    .classes("flex-grow")
                self.counter = ui.label("0 / 0").style(
                    f"font-family:{TYPOGRAPHY['mono']};"
                    f"font-size:{TYPOGRAPHY['size_sm']}")
                self.elapsed = ui.label("0s").style(
                    f"font-family:{TYPOGRAPHY['mono']};"
                    f"font-size:{TYPOGRAPHY['size_sm']};"
                    f"color:{COLORS['text_muted']}")
            self.footer = ui.row().classes("w-full")

        self.log.push(f"run {self.run_id} started")
        ui.timer(self.POLL_S, self.poll)

    async def poll(self) -> None:
        if not self.done:
            self.elapsed.set_text(f"{int(time.time() - self.started)}s")
        try:
            res = await api.run_result(self.run_id)
        except api.ApiError as e:
            # A 404 right after launch is normal — the background task may not have
            # registered yet. Anything else is worth showing.
            if e.status != 404:
                self.log.push(f"poll failed: {e.detail}")
            return

        log = res.get("log") or []
        if len(log) > self.seen:
            for entry in log[self.seen:]:
                self.log.push(
                    f"[{entry.get('status','?'):>6}] line {entry.get('line','?')}: "
                    f"{entry.get('step','')}"
                    + (f"  -> {entry.get('error','')}" if entry.get("error") else ""))
            self.seen = len(log)
            self._render_steps(log)
        # Screenshots are promoted late in `failure` mode (a frame is only
        # known to be wanted once a later step fails), so the count of entries
        # holding one, not the entry count, decides whether the tab redraws.
        with_shot = [e for e in log if e.get("screenshot")]
        if len(with_shot) != self.shots_seen:
            self.shots_seen = len(with_shot)
            self._render_shots(log)

        total = res.get("total") or len(log)
        passed, failed = res.get("passed", 0), res.get("failed", 0)
        if total:
            self.bar.set_value((passed + failed) / total)
            self.counter.set_text(f"{passed + failed} / {total}")

        if "passed" in res and not self.done:
            self.done = True
            self.chip_holder.clear()
            with self.chip_holder:
                status_chip("failed" if failed else "passed")
            self.log.push(f"finished: {passed} passed, {failed} failed")
            self._render_footer(res)

    def _render_shots(self, log: list[dict]) -> None:
        """
        The Screenshots tab: one frame per step that has one, newest at the
        bottom, each labelled with its step so a picture can be matched to the
        line that produced it. The report page shows the same frames afterwards;
        here they arrive while the run is still going.
        """
        self.shots.clear()
        with self.shots:
            shown = 0
            for i, entry in enumerate(log, 1):
                rel = entry.get("screenshot")
                if not rel:
                    continue
                shown += 1
                st = entry.get("status", "")
                colour = COLORS["danger"] if st == "failed" else COLORS["text"]
                with ui.column().classes("w-full gap-1").style(
                        f"padding:6px 0; border-bottom:1px solid {COLORS['border']}"):
                    ui.label(f"{i}  {entry.get('step', '')}").style(
                        f"font-family:{TYPOGRAPHY['mono']};"
                        f"font-size:{TYPOGRAPHY['size_xs']}; color:{colour};"
                        f"word-break:break-all")
                    # Served by the /screenshots mount (path relative to
                    # data/screenshots), the same way the report page shows it.
                    src = f"/screenshots/{rel.lstrip('/')}"
                    ui.image(src).style(
                        f"width:100%; border:1px solid {COLORS['border']};"
                        f"border-radius:6px").on(
                        "click", lambda s=src: ui.navigate.to(s, new_tab=True)) \
                        .classes("cursor-pointer").tooltip("Open full size")
            if not shown:
                ui.label("No screenshots yet — this run's screenshot mode "
                         "keeps none, or only the failure.").style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

    def _render_steps(self, log: list[dict]) -> None:
        self.steps_area.clear()
        with self.steps_area:
            for i, entry in enumerate(log, 1):
                st = entry.get("status", "pending")
                colour = COLORS["success"] if st == "passed" else (
                    COLORS["danger"] if st == "failed" else COLORS["text_muted"])
                icon = {"passed": "✅", "failed": "❌"}.get(st, "⏳")
                with ui.column().classes("w-full gap-0").style(
                        f"border-bottom:1px solid {COLORS['border']}"):
                    with ui.row().classes("w-full items-center gap-2") \
                            .style("padding:5px 8px"):
                        ui.label(icon)
                        ui.label(str(i)).style(
                            f"width:1.6rem; text-align:right;"
                            f"color:{COLORS['text_muted']};"
                            f"font-family:{TYPOGRAPHY['mono']};"
                            f"font-size:{TYPOGRAPHY['size_xs']}")
                        ui.label(entry.get("step", "")).style(
                            f"font-family:{TYPOGRAPHY['mono']};"
                            f"font-size:{TYPOGRAPHY['size_sm']}; color:{colour};"
                            f"word-break:break-all")
                    if entry.get("error"):
                        # The failure reason is the reason this page exists — shown
                        # inline, not hidden behind a click.
                        ui.label(entry["error"]).style(
                            f"padding:0 8px 6px 3.2rem; color:{COLORS['danger']};"
                            f"font-family:{TYPOGRAPHY['mono']};"
                            f"font-size:{TYPOGRAPHY['size_xs']};"
                            f"white-space:pre-wrap")

    def _render_footer(self, res: dict) -> None:
        self.footer.clear()
        with self.footer:
            failed = res.get("failed", 0)
            ui.label(f"{res.get('passed',0)} passed, {failed} failed").style(
                f"font-weight:{TYPOGRAPHY['weight_medium']};"
                f"color:{COLORS['danger'] if failed else COLORS['success']}")
            ui.space()
            if res.get("report_file"):
                ui.label(res["report_file"]).style(
                    f"font-family:{TYPOGRAPHY['mono']};"
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            ui.button("Run again", icon="replay",
                      on_click=lambda: ui.navigate.to(
                          f"/run?flow={self.flow}&platform={self.platform}")) \
                .props("unelevated dense")


async def render(run_id: str, flow: str = "", platform: str = "website") -> None:
    LiveView(run_id, flow, platform).render()
