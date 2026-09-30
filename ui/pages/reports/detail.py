"""
ui/pages/reports/detail.py — Report Detail  (route: /reports/{run_id})

The steps of one run, and what the page looked like at each of them.

Layout:
    Header      flow name | status | passed/failed/skipped | duration | Re-run
    Left        every step: number, pass/fail/skipped icon, text, duration.
                Click one to select it.
    Right       that step's screenshot, its status, how long it took, and the
                error message when it failed.

Why this screen exists
----------------------
Before it, a failed run gave you a step name and an error string, so working out
what actually happened meant running it again and watching. A step that failed
because the page had not loaded, because an element moved, or because the site
served something entirely different all produced much the same message. The
screenshot answers in one look which of those it was.

Screenshots come from reporting/step_capture.py and are stored in the report as
paths RELATIVE to data/screenshots, served by the /screenshots mount in
api/app.py. A step legitimately has no image when the run used a capture mode
that skipped it — that is said in words rather than left as an empty panel,
because a blank space reads as a broken page.

Deliberately not built here: PDF export and the pass/fail donut from the
original spec. Neither helps diagnose a failure, which is the whole job.
"""
from __future__ import annotations

from nicegui import ui

from ui import api_client as api
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.theme import COLORS, TYPOGRAPHY

#: Marks in the step list. A skipped step did not fail — it never ran — and
#: showing it in the failure colour would exaggerate the damage.
_MARK = {
    "passed": ("check_circle", COLORS["success"]),
    "failed": ("cancel", COLORS["danger"]),
    "skipped": ("remove_circle_outline", COLORS["text_muted"]),
}


def _duration(ms) -> str:
    """Milliseconds as something readable at a glance."""
    if ms is None:
        return ""
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return ""
    if ms < 1000:
        return f"{ms} ms"
    if ms < 60_000:
        return f"{ms / 1000:.2f}s"
    return f"{ms / 60000:.1f} min"


class ReportDetail:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.report: dict = {}
        self.steps: list[dict] = []
        self.selected = 0
        self.platforms: list[dict] = []

    async def load(self) -> None:
        try:
            self.platforms = await api.platforms()
        except api.ApiError:
            self.platforms = []
        try:
            self.report = await api.run_result(self.run_id) or {}
        except api.ApiError as e:
            self.report = {"_error": e.detail}
            return
        # A run still in memory reports its steps as `log`; a saved report calls
        # them `results` and names the step differently. One screen reads both
        # rather than making the caller know which kind of run it is looking at.
        rows = self.report.get("results") or self.report.get("log") or []
        self.steps = [{
            "text": r.get("test_name") or r.get("step", ""),
            "status": r.get("status", ""),
            "reason": r.get("reason") or r.get("error", ""),
            "screenshot": r.get("screenshot", ""),
            "duration_ms": r.get("duration_ms"),
        } for r in rows]
        # Open on the first failure. That is the step you came to look at, and
        # on a forty-step run it saves scrolling to find it.
        self.selected = next((i for i, s in enumerate(self.steps)
                              if s["status"] == "failed"), 0)

    # ── rendering ───────────────────────────────────────────────────────────
    def render(self) -> None:
        sidebar(active="/history", platforms=self.platforms)
        topbar(["Execute", "Report"], platforms=self.platforms)
        if self.report.get("_error"):
            with ui.column().classes("w-full items-center gap-2").style("padding:3rem"):
                ui.icon("error_outline").style(
                    f"font-size:2.5rem; color:{COLORS['text_muted']}")
                ui.label(f"Could not open this run: {self.report['_error']}").style(
                    f"font-size:{TYPOGRAPHY['size_sm']}")
                ui.button("Back to History",
                          on_click=lambda: ui.navigate.to("/history")).props("flat")
            return

        with ui.column().classes("w-full gap-2 p-4"):
            self._header()
            with ui.row().classes("w-full no-wrap gap-4"):
                self.left = ui.column().classes("gap-0").style(
                    f"width:34rem; flex:none; border:1px solid {COLORS['border']};"
                    f"border-radius:6px; max-height:74vh; overflow-y:auto")
                self.right = ui.column().classes("flex-grow gap-2")
        self._draw_steps()
        self._draw_detail()

    def _header(self) -> None:
        summary = self.report.get("summary") or self.report or {}
        total = summary.get("total", len(self.steps))
        passed = summary.get("passed", 0)
        failed = summary.get("failed", 0)
        skipped = summary.get("skipped", 0)
        flow = (self.report.get("testplan") or self.report.get("project")
                or self.run_id)
        ok = not failed
        colour = COLORS["success"] if ok else COLORS["danger"]

        with ui.row().classes("w-full items-center gap-3 no-wrap"):
            ui.label(flow).style(
                f"font-family:{TYPOGRAPHY['mono']};"
                f"font-size:{TYPOGRAPHY['size_lg']};"
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            ui.label("PASSED" if ok else "FAILED").style(
                f"background:{colour}1A; color:{colour}; border-radius:4px;"
                f"padding:2px 10px; font-size:{TYPOGRAPHY['size_xs']};"
                f"font-family:{TYPOGRAPHY['mono']}")
            for label, value, tint in (("passed", passed, COLORS["success"]),
                                       ("failed", failed, COLORS["danger"]),
                                       ("not run", skipped, COLORS["text_muted"])):
                if value:
                    ui.label(f"{value} {label}").style(
                        f"color:{tint}; font-size:{TYPOGRAPHY['size_sm']}")
            ui.label(f"of {total}").style(
                f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_sm']}")
            spent = sum(int(s["duration_ms"] or 0) for s in self.steps)
            if spent:
                ui.label(_duration(spent)).style(
                    f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_sm']};"
                    f"font-family:{TYPOGRAPHY['mono']}")
            ui.space()
            if failed:
                ui.button("Review & raise issues", icon="bug_report",
                          on_click=lambda: ui.navigate.to(f"/issues?run_id={self.run_id}")) \
                    .props("unelevated dense").style(f"background:{COLORS['danger']}")
            # Says WHY images are missing before anyone has to wonder.
            shots = self.report.get("screenshots") or {}
            if shots and shots.get("mode") and shots.get("mode") != "all":
                ui.label(f"screenshots: {shots['mode']}").style(
                    f"color:{COLORS['warning']}; font-size:{TYPOGRAPHY['size_xs']};"
                    f"font-family:{TYPOGRAPHY['mono']}") \
                    .tooltip("This run did not photograph every step, so some "
                             "steps have no image.")
            video = self.report.get("video") or ""
            if video:
                # meta["video"] is relative to data/ (videos/completed/…); the
                # /videos mount serves data/videos.
                href = "/" + video.lstrip("/")
                ui.button("Video", icon="videocam",
                          on_click=lambda h=href: ui.navigate.to(h, new_tab=True)) \
                    .props("flat dense no-caps") \
                    .tooltip("Open the recording of this run (.webm) — save it to attach to a ticket")
            if flow and flow != self.run_id:
                dev = self.report.get("device") or {}
                from urllib.parse import urlencode
                q = urlencode({k: v for k, v in {"flow": flow, "device": dev.get("device_name", ""),
                                                 "browser": dev.get("browser", ""),
                                                 "identity": dev.get("browser_identity", ""),
                                                 "env": (self.report.get("site_env") or {}).get("name", "")}.items() if v})
                ui.button("Re-run", icon="replay",
                          on_click=lambda q=q: ui.navigate.to(f"/run?{q}")) \
                    .props("unelevated dense")

    def _draw_steps(self) -> None:
        self.left.clear()
        with self.left:
            if not self.steps:
                ui.label("This run recorded no steps.").style(
                    f"padding:12px; color:{COLORS['text_muted']};"
                    f"font-size:{TYPOGRAPHY['size_sm']}")
                return
            for i, step in enumerate(self.steps):
                self._step_row(i, step)

    def _step_row(self, i: int, step: dict) -> None:
        icon, colour = _MARK.get(step["status"], ("radio_button_unchecked",
                                                  COLORS["text_muted"]))
        chosen = i == self.selected
        with ui.row().classes("w-full items-center gap-2 no-wrap cursor-pointer") \
                .style(f"padding:6px 10px;"
                       f"border-bottom:1px solid {COLORS['border']};"
                       f"background:{COLORS['primary'] + '14' if chosen else 'transparent'};"
                       f"border-left:3px solid "
                       f"{COLORS['primary'] if chosen else 'transparent'}") \
                .on("click", lambda n=i: self._select(n)):
            ui.label(str(i + 1)).style(
                f"width:1.8rem; text-align:right; color:{COLORS['text_muted']};"
                f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']}")
            ui.icon(icon).style(f"color:{colour}; font-size:1.05rem")
            ui.label(step["text"]).classes("flex-grow").style(
                f"font-family:{TYPOGRAPHY['mono']};"
                f"font-size:{TYPOGRAPHY['size_sm']}; word-break:break-all")
            if step["screenshot"]:
                ui.icon("photo_camera").style(
                    f"color:{COLORS['text_muted']}; font-size:0.85rem")
            ui.label(_duration(step["duration_ms"])).style(
                f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_xs']};"
                f"font-family:{TYPOGRAPHY['mono']}; white-space:nowrap")

    def _select(self, index: int) -> None:
        self.selected = index
        self._draw_steps()
        self._draw_detail()

    def _draw_detail(self) -> None:
        self.right.clear()
        if not self.steps:
            return
        step = self.steps[min(self.selected, len(self.steps) - 1)]
        _icon, colour = _MARK.get(step["status"], ("", COLORS["text_muted"]))
        with self.right:
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.label(f"Step #{self.selected + 1}").style(
                    f"font-weight:{TYPOGRAPHY['weight_bold']};"
                    f"font-size:{TYPOGRAPHY['size_sm']}")
                ui.label(step["status"].upper() or "—").style(
                    f"background:{colour}1A; color:{colour}; border-radius:4px;"
                    f"padding:1px 8px; font-size:{TYPOGRAPHY['size_xs']};"
                    f"font-family:{TYPOGRAPHY['mono']}")
                if step["duration_ms"] is not None:
                    ui.label(_duration(step["duration_ms"])).style(
                        f"color:{COLORS['text_muted']};"
                        f"font-size:{TYPOGRAPHY['size_xs']};"
                        f"font-family:{TYPOGRAPHY['mono']}")
            ui.label(step["text"]).style(
                f"font-family:{TYPOGRAPHY['mono']};"
                f"font-size:{TYPOGRAPHY['size_sm']}; word-break:break-all")

            if step["reason"]:
                with ui.column().classes("w-full gap-0").style(
                        f"background:{colour}0D; border-left:3px solid {colour};"
                        f"border-radius:4px; padding:6px 10px"):
                    ui.label(step["reason"]).style(
                        f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text']};"
                        f"white-space:pre-wrap; word-break:break-word")

            if step["screenshot"]:
                # Served by the /screenshots mount; the stored path is relative
                # to data/screenshots so the report survives being moved.
                src = f"/screenshots/{step['screenshot'].lstrip('/')}"
                ui.image(src).style(
                    f"width:100%; border:1px solid {COLORS['border']};"
                    f"border-radius:6px").on(
                    "click", lambda s=src: ui.navigate.to(s, new_tab=True)) \
                    .classes("cursor-pointer").tooltip("Open full size")
            else:
                with ui.column().classes("w-full items-center gap-1").style(
                        f"border:1px dashed {COLORS['border']}; border-radius:6px;"
                        f"padding:2rem"):
                    ui.icon("image_not_supported").style(
                        f"font-size:1.8rem; color:{COLORS['text_muted']}")
                    ui.label(self._why_no_image(step)).style(
                        f"font-size:{TYPOGRAPHY['size_xs']};"
                        f"color:{COLORS['text_muted']}; text-align:center")

    def _why_no_image(self, step: dict) -> str:
        """Absence with a reason, rather than an empty box."""
        if step["status"] == "skipped":
            return "This step never ran, so there is nothing to show."
        mode = (self.report.get("screenshots") or {}).get("mode", "")
        if mode == "off":
            return "This run was set to take no screenshots."
        if mode == "key":
            return ("This run only photographed checks and new pages, and this "
                    "step is neither.")
        if mode == "failure":
            return ("This run only kept the failing step and the ones just "
                    "before it.")
        return "No screenshot was recorded for this step."


async def render(run_id: str) -> None:
    page = ReportDetail(run_id)
    await page.load()
    page.render()
