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
        #: Steps that have a frame, in order, and which one the viewer shows.
        self.frames: list[tuple[int, dict]] = []
        self.frame_at = -1
        #: True while the viewer follows the newest frame as the run goes;
        #: any manual navigation switches it off so the picture stays put.
        self.follow = True
        self.log_cache: list[dict] = []

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
                self.stop_btn = ui.button("Stop", icon="stop_circle", on_click=self._stop) \
                    .props("outline dense color=negative") \
                    .tooltip("Stop after the current step — done steps keep their "
                             "result, the rest are marked not run, the report is saved")

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
                    self.tabs, self.t_shot = tabs, t_shot
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
        self.missing = 0
        self.timer = ui.timer(self.POLL_S, self.poll)

    async def poll(self) -> None:
        if self.done:
            # Finished: stop polling instead of re-fetching the whole result
            # every second for as long as the tab stays open.
            try:
                self.timer.cancel()
            except Exception:  # noqa: BLE001
                pass
            return
        self.elapsed.set_text(f"{int(time.time() - self.started)}s")
        try:
            res = await api.run_result(self.run_id)
        except api.ApiError as e:
            # A 404 right after launch is normal — the background task may not have
            # registered yet. Anything else is worth showing.
            if e.status != 404:
                self.log.push(f"poll failed: {e.detail}")
                return
            self.missing += 1
            if self.missing == 15:
                # 15 s and the run is still unknown: the server restarted before
                # it saved a report. Say so, rather than a counter climbing forever.
                self.done = True
                self.log.push("This run is no longer known to the server (it was probably "
                              "restarted before the run saved its report).")
                ui.notify("This run is no longer known to the server — it was probably restarted "
                          "mid-run. See History for saved runs.", type="warning", timeout=12000)
            return
        self.missing = 0

        log = res.get("log") or []
        planned = res.get("planned") or getattr(self, "planned", [])
        if planned and not getattr(self, "planned", None):
            self.planned = planned
        finished = [e for e in log if e.get("status") != "running"]
        running = next((e for e in log if e.get("status") == "running"), None)
        state_key = (len(finished), running.get("line") if running else None)
        if len(finished) > self.seen:
            for entry in finished[self.seen:]:
                self.log.push(
                    f"[{entry.get('status','?'):>6}] line {entry.get('line','?')}: "
                    f"{entry.get('step','')}"
                    + (f"  -> {entry.get('error','')}" if entry.get("error") else ""))
            self.seen = len(finished)
        if state_key != getattr(self, "_state_key", None) or (planned and not getattr(self, "_drawn", False)):
            self._state_key = state_key
            self._drawn = True
            self._render_steps(log, planned)
        # Screenshots are promoted late in `failure` mode (a frame is only
        # known to be wanted once a later step fails), so the count of entries
        # holding one, not the entry count, decides whether the tab redraws.
        with_shot = [e for e in finished if e.get("screenshot")]
        if len(with_shot) != self.shots_seen:
            self.shots_seen = len(with_shot)
            self._render_shots(finished)

        total = res.get("total") or len(planned) or len(log)
        passed, failed = res.get("passed", 0), res.get("failed", 0)
        skipped = res.get("skipped", 0)
        if total:
            self.bar.set_value((passed + failed + skipped) / total)
            self.counter.set_text(f"{passed + failed + skipped} / {total}")

        if res.get("status") != "running" and "passed" in res and not self.done:
            self.done = True
            self.chip_holder.clear()
            with self.chip_holder:
                # A stopped run is neither: done steps kept their result,
                # the rest never ran.
                status_chip("stopped" if res.get("stopped_early") and not failed
                            else "failed" if failed else "passed")
            self.stop_btn.set_visibility(False)
            self.log.push(f"finished: {passed} passed, {failed} failed"
                          + (f", {skipped} not run" if skipped else ""))
            self._render_footer(res)

    def _render_shots(self, log: list[dict]) -> None:
        """
        The Screenshots tab as a viewer: ONE frame at a time, with ◀ ▶ to move
        between steps and the step's own text above the picture. Clicking a
        step in the list on the left jumps the viewer to that step's frame. A
        list of every frame stacked end to end had to be scrolled to find the
        one step you cared about, and looked like nothing at all until the
        run had produced a few.
        """
        self.frames = [(e.get("line", i), e) for i, e in enumerate(log, 1) if e.get("screenshot")]
        if not self.frames:
            self.frame_at = -1
        elif self.follow or self.frame_at >= len(self.frames) or self.frame_at < 0:
            self.frame_at = len(self.frames) - 1
        self._draw_frame()

    def _show_frame(self, at: int, *, switch_tab: bool = False) -> None:
        if not self.frames:
            return
        self.frame_at = max(0, min(at, len(self.frames) - 1))
        # Moving by hand means "stay here"; the newest frame no longer steals it.
        self.follow = self.frame_at == len(self.frames) - 1 and not self.done
        self._draw_frame()
        if switch_tab:
            self.tabs.set_value(self.t_shot)

    def _show_step(self, line_index: int) -> None:
        """Jump the viewer to the frame of the step at flow line `line_index`."""
        for at, (i, _e) in enumerate(self.frames):
            if i == line_index:
                self._show_frame(at, switch_tab=True)
                return
        ui.notify("No screenshot for that step (its mode kept none, or it has "
                  "not run yet)", type="info")

    def _draw_frame(self) -> None:
        self.shots.clear()
        with self.shots:
            if not self.frames:
                ui.label("No screenshots yet — frames appear here as steps run; "
                         "click a step on the left to jump to its frame.").style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
                return
            i, entry = self.frames[self.frame_at]
            st = entry.get("status", "")
            colour = COLORS["danger"] if st == "failed" else COLORS["text"]
            with ui.row().classes("w-full items-center no-wrap gap-1"):
                ui.button(icon="chevron_left",
                          on_click=lambda: self._show_frame(self.frame_at - 1)) \
                    .props("flat dense round").tooltip("Previous step") \
                    .set_enabled(self.frame_at > 0)
                ui.label(f"Line {i}  ·  frame {self.frame_at + 1} of {len(self.frames)}") \
                    .style(f"font-size:{TYPOGRAPHY['size_xs']};"
                           f"color:{COLORS['text_muted']}; white-space:nowrap")
                ui.space()
                ui.button(icon="chevron_right",
                          on_click=lambda: self._show_frame(self.frame_at + 1)) \
                    .props("flat dense round").tooltip("Next step") \
                    .set_enabled(self.frame_at < len(self.frames) - 1)
                ui.button(icon="last_page",
                          on_click=lambda: self._show_frame(len(self.frames) - 1)) \
                    .props("flat dense round").tooltip("Latest frame") \
                    .set_enabled(self.frame_at < len(self.frames) - 1)
            ui.label(f"{'✅' if st == 'passed' else '❌' if st == 'failed' else '⏳'} "
                     f"{entry.get('step', '')}").style(
                f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']};"
                f"color:{colour}; word-break:break-all")
            if entry.get("error"):
                ui.label(entry["error"]).style(
                    f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']};"
                    f"color:{COLORS['danger']}; white-space:pre-wrap")
            # Served by the /screenshots mount (path relative to data/screenshots),
            # the same way the report page shows it.
            src = f"/screenshots/{entry['screenshot'].lstrip('/')}"
            ui.image(src).style(
                f"width:100%; border:1px solid {COLORS['border']}; border-radius:6px") \
                .on("click", lambda s=src: ui.navigate.to(s, new_tab=True)) \
                .classes("cursor-pointer").tooltip("Open full size in a new tab")

    def _render_steps(self, log: list[dict], planned: list[dict] | None = None) -> None:
        """
        One row per step the run will execute, ticked as the run goes:
        pending → running → passed / failed / skipped. Before this the list
        only held finished steps, so a 170-step run showed nothing at all
        until its first step completed and gave no sense of where it was.
        """
        by_line = {e.get("line"): e for e in log}
        rows = [dict(p, **by_line.get(p.get("line"), {})) for p in planned] if planned else list(log)
        self.steps_area.clear()
        with self.steps_area:
            for i, entry in enumerate(rows, 1):
                st = entry.get("status", "pending")
                colour = {"passed": COLORS["success"], "failed": COLORS["danger"],
                          "running": COLORS["primary"]}.get(st, COLORS["text_muted"])
                icon = {"passed": "✅", "failed": "❌", "running": "🔄",
                        "skipped": "⏭"}.get(st, "⏳")
                has_frame = bool(entry.get("screenshot"))
                row_el = ui.column().classes(
                    "w-full gap-0" + (" cursor-pointer" if has_frame else "")) \
                    .style(f"border-bottom:1px solid {COLORS['border']};"
                           + (f"background:{COLORS['primary']}12;" if st == "running" else "")) \
                    .on("click", lambda _, n=entry.get("line", i): self._show_step(n))
                if st == "running":
                    row_el.props("id=live-running-row")
                if has_frame:
                    row_el.tooltip("Click to see this step's screenshot")
                with row_el:
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
            if failed:
                ui.button("Review & raise issues", icon="bug_report",
                          on_click=lambda: ui.navigate.to(f"/issues?run_id={self.run_id}")) \
                    .props("unelevated dense").style(f"background:{COLORS['danger']}")
            ui.button("Run again", icon="replay",
                      on_click=lambda: ui.navigate.to(
                          f"/run?flow={self.flow}&platform={self.platform}")) \
                .props("unelevated dense")


    async def _stop(self) -> None:
        try:
            res = await api.stop_run(self.run_id)
        except api.ApiError as e:
            ui.notify(f"Could not stop: {e.detail}", type="negative")
            return
        if res.get("stopped"):
            self.stop_btn.props("disable loading")
            ui.notify("Stopping after the current step…", type="warning")
        else:
            ui.notify(res.get("message") or "Already finished", type="info")


async def render(run_id: str, flow: str = "", platform: str = "website") -> None:
    LiveView(run_id, flow, platform).render()
