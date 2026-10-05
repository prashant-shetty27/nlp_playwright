"""
ui/pages/plans/run.py — Test Plan execution report (/plans/run/{id}).

Modelled on Testsigma's run result page:

    Header     plan · status · run id · trigger / by · start–end (IST)
               Re-run · Stop (after this test case / now) · Send to Slack
    Alert      shown only when the run looks stuck, with what to do about it
    Summary    result % with a stacked bar · test cases · steps · duration
               (minutes) · environment
    Results    filter All / Failed / Passed / Not run; one row per test case:
               result, test case name (click → opens it in the editor), suite,
               step counts with a bar, duration (minutes), attempt, actions
               (Edit, Live, Report) and an expandable step list — every step
               with its status, line, time and error; a failed step links to
               that exact line in the editor.

Follows the run live (2 s poll) while it is queued / running.
"""
from __future__ import annotations

from urllib.parse import quote

from nicegui import ui

from ui import api_client as api
from ui.auth import can
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.pages.plans.common import STATUS_COLOR, chip, ist, muted
from ui.theme import COLORS, TYPOGRAPHY

MONO = TYPOGRAPHY["mono"]


def mins(sec) -> str:
    """Execution time in minutes, as asked for (hover shows the exact m:s)."""
    try:
        sec = float(sec)
    except (TypeError, ValueError):
        return "—"
    return f"{sec / 60:.1f} min"


def _secs(a: str, b: str = "") -> float | None:
    from datetime import datetime, timezone
    try:
        start = datetime.fromisoformat(a)
        end = datetime.fromisoformat(b) if b else datetime.now(timezone.utc)
        return (end - start).total_seconds()
    except (TypeError, ValueError):
        return None


def _item_secs(it: dict) -> float | None:
    if it.get("duration_s") is not None:
        return it["duration_s"]
    if it.get("started_at"):
        return _secs(it["started_at"], it.get("finished_at", ""))
    return None


def _editor_url(it: dict, line: int | None = None) -> str:
    url = f"/platform/{quote(it.get('platform') or 'website')}?flow={quote(it.get('test_case', ''))}"
    if line:
        url += f"&line={int(line)}"
    return url


def _bar(parts: list[tuple[float, str]], height: str = "8px") -> None:
    total = sum(v for v, _ in parts) or 1
    with ui.row().classes("w-full no-wrap gap-0").style(
            f"height:{height}; border-radius:4px; overflow:hidden; background:{COLORS['border']}"):
        for v, c in parts:
            if v:
                ui.element("div").style(f"width:{100 * v / total:.2f}%; background:{c}; height:100%")


def _card(title: str):
    col = ui.column().classes("gap-1").style(
        f"flex:1 1 11rem; min-width:11rem; border:1px solid {COLORS['border']}; border-radius:8px;"
        f"padding:10px 14px; background:{COLORS['surface']}")
    with col:
        ui.label(title.upper()).style(
            f"font-size:0.68rem; letter-spacing:.06em; color:{COLORS['text_muted']};"
            f"font-weight:{TYPOGRAPHY['weight_medium']}")
    return col


def _num(value, label: str, colour: str) -> None:
    with ui.column().classes("gap-0 items-start"):
        ui.label(str(value)).style(f"font-size:1.25rem; font-weight:600; color:{colour}; line-height:1.2")
        ui.label(label).style(f"font-size:0.7rem; color:{COLORS['text_muted']}")


_STEP_MARK = {"passed": ("check_circle", COLORS["success"]),
              "failed": ("cancel", COLORS["danger"]),
              "skipped": ("remove_circle_outline", COLORS["text_muted"]),
              "running": ("autorenew", COLORS["primary"]),
              "pending": ("radio_button_unchecked", COLORS["text_muted"])}


def _slowest(log: list[dict], top: int = 3) -> list[tuple[str, int, float]]:
    """Step kinds that took the most time in total — where a run can be sped up."""
    import re
    agg: dict[str, list] = {}
    for e in log:
        ms = e.get("duration_ms") or 0
        text = (e.get("step") or "").strip()
        key = "open <url>" if text.startswith("open ") else re.sub(r'"[^"]*"|\$\{[^}]*\}', "…", text)[:48]
        a = agg.setdefault(key, [0, 0.0])
        a[0] += 1
        a[1] += ms / 1000
    rows = sorted(((k, v[0], v[1]) for k, v in agg.items()), key=lambda r: -r[2])
    return [r for r in rows[:top] if r[2] >= 20]


class PlanRunPage:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.rec: dict = {}
        self.filter = "all"
        self.expanded: set[int] = set()
        self.only_failed: set[int] = set()
        self.steps: dict[str, dict] = {}      # test-case run id -> result (log)
        self.sig = None
        self.done = False
        self.alerted = False

    # ── data ────────────────────────────────────────────────────────────────
    async def refresh(self, force: bool = False) -> None:
        if self.done and not force:
            return
        try:
            r = await api.plan_run(self.run_id)
        except api.ApiError as e:
            self.body.clear()
            with self.body:
                ui.label(e.detail).style(f"color:{COLORS['danger']}")
            self.done = True
            return
        h = r.get("health") or {}
        running = r.get("status") in ("queued", "running")
        # Steps of an expanded, RUNNING test case change every poll.
        for idx in list(self.expanded):
            items = r.get("items") or []
            if idx < len(items) and items[idx].get("status") == "running" and items[idx].get("run_id"):
                await self._load_steps(items[idx]["run_id"], force=True)
        sig = (r.get("status"), str(r.get("slack")),
               tuple((i.get("status"), i.get("run_id"), i.get("passed_steps")) for i in r.get("items", [])),
               h.get("done"), h.get("line"), h.get("stuck"), int((h.get("step_minutes") or 0) * 2),
               tuple(len((self.steps.get(i.get("run_id") or "") or {}).get("log") or [])
                     for i in r.get("items", [])))
        self.rec = r
        if h.get("stuck") and not self.alerted:
            self.alerted = True
            ui.notify("This execution looks stuck — see the alert at the top of the page for what to do.",
                      type="warning", timeout=15000, position="top")
        if sig != self.sig or force:
            self.sig = sig
            self.draw()
        self.done = not running

    async def _load_steps(self, run_id: str, force: bool = False) -> None:
        if run_id in self.steps and not force:
            return
        try:
            self.steps[run_id] = await api.run_result(run_id) or {}
        except api.ApiError as e:
            self.steps[run_id] = {"_error": e.detail}

    async def toggle(self, idx: int) -> None:
        if idx in self.expanded:
            self.expanded.discard(idx)
        else:
            it = self.rec["items"][idx]
            if it.get("run_id"):
                await self._load_steps(it["run_id"], force=it.get("status") == "running")
            self.expanded.add(idx)
        self.draw()

    # ── actions ─────────────────────────────────────────────────────────────
    async def stop(self, now: bool) -> None:
        from ui.pages.plans.common import confirm

        async def go() -> None:
            try:
                await api.stop_plan_run(self.run_id, now=now)
            except api.ApiError as e:
                ui.notify(f"Could not stop: {e.detail}", type="negative")
                return
            ui.notify("Stopping at the next step — the rest is marked not run" if now
                      else "Stopping after the current test case", type="info")
            await self.refresh(force=True)

        confirm("Stop this plan run?",
                ("The current step finishes, then every remaining step and test case is marked "
                 "not run. The report is still saved.") if now else
                ("The test case that is running finishes; the remaining test cases are marked "
                 "not run."), go, button="Stop now" if now else "Stop after this test case")

    async def close_orphan(self) -> None:
        try:
            await api.close_plan_run(self.run_id)
            ui.notify("Marked as interrupted. Use Re-run plan to run it again.", type="info")
        except api.ApiError as e:
            ui.notify(e.detail, type="warning")
        self.done = False
        await self.refresh(force=True)

    async def rerun(self) -> None:
        try:
            r = await api.run_plan(self.rec.get("plan_id", ""))
        except api.ApiError as e:
            ui.notify(e.detail, type="negative")
            return
        ui.navigate.to(f"/plans/run/{quote(r['run_id'])}")

    async def resend(self) -> None:
        res = await api.renotify_plan_run(self.run_id)
        ui.notify("Posted to Slack" if res.get("sent") else f"Slack: {res.get('error')}",
                  type="positive" if res.get("sent") else "warning", timeout=8000)
        self.done = False
        await self.refresh(force=True)

    def _email_dialog(self) -> None:
        to0 = (self.rec.get("notify") or {}).get("email_to") or ""
        with ui.dialog() as dlg, ui.card().style("width:32rem"):
            ui.label("Email this report").style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
            muted("A formatted summary in the email body, with the full PDF report attached.")
            to = ui.input("To (comma separated)", value=to0).props("outlined dense").classes("w-full")
            status = muted("")

            async def go() -> None:
                if not (to.value or "").strip():
                    status.set_text("Enter at least one email address.")
                    return
                status.set_text("Sending…")
                try:
                    res = await api.email_plan_run(self.run_id, to.value)
                except api.ApiError as e:
                    status.set_text(e.detail)
                    return
                if res.get("sent"):
                    dlg.close()
                    ui.notify(f"Report emailed to {', '.join(res.get('to') or [])}", type="positive")
                    self.done = False
                    await self.refresh(force=True)
                else:
                    status.set_text(f"Not sent: {res.get('error')}")

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dlg.close).props("flat")
                ui.button("Send", icon="send", on_click=go).props("unelevated")
        dlg.open()

    # ── drawing ─────────────────────────────────────────────────────────────
    def draw(self) -> None:
        self.body.clear()
        with self.body:
            self._header()
            self._stuck_alert()
            self._summary()
            self._results()

    def _header(self) -> None:
        r = self.rec
        running = r.get("status") in ("queued", "running")
        with ui.row().classes("w-full items-center gap-3 no-wrap"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/plans")) \
                .props("flat dense").tooltip("All plans")
            with ui.column().classes("gap-0"):
                with ui.row().classes("items-center gap-2"):
                    ui.label(r.get("plan_name", "")).style(
                        f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
                    chip(r.get("status", ""))
                    rt = r.get("run_type") or (r.get("execution") or {}).get("run_type")
                    if rt:
                        from core.run_types import ICON as _RI, LABEL as _RL
                        ui.label(f"{_RI.get(rt, '')} {_RL.get(rt, rt)} run").style(
                            f"font-size:{TYPOGRAPHY['size_xs']}; border:1px solid {COLORS['border']};"
                            "border-radius:10px; padding:1px 9px")
                trig = "Scheduled" if r.get("trigger") == "schedule" else "Manual"
                muted(f"Run {r.get('id')} · {trig} · by {r.get('triggered_by') or '—'} · "
                      f"started {ist(r.get('started_at') or r.get('queued_at'), '%d %b %Y %H:%M:%S')}"
                      + (f" · finished {ist(r.get('finished_at'), '%H:%M:%S')}" if r.get("finished_at") else ""))
            ui.space()
            ui.button("Edit plan", icon="tune", on_click=lambda: ui.navigate.to(
                f"/plans/edit?id={quote(r.get('plan_id', ''))}")).props("flat dense")
            if running and can("run"):
                with ui.button("Stop", icon="stop").props("unelevated dense color=negative"):
                    with ui.menu():
                        ui.menu_item("Stop after the current test case", on_click=lambda: self.stop(False))
                        ui.menu_item("Stop now (at the next step)", on_click=lambda: self.stop(True))
            n_failed = sum(1 for i in (r.get("items") or []) if i.get("status") == "failed")
            n_redo = sum(1 for i in (r.get("items") or []) if i.get("status") in ("failed", "not_run")
                         and not i.get("out_of_scope"))
            if not running and n_redo and can("run"):
                async def _rerun_failed() -> None:
                    try:
                        res = await api.run_plan(r.get("plan_id", ""), only_failed_from=self.run_id)
                    except api.ApiError as e:
                        ui.notify(f"Could not start: {e.detail}", type="negative")
                        return
                    ui.navigate.to(f"/plans/run/{res['run_id']}")
                ui.button(f"Re-run failed only ({n_redo})", icon="replay", on_click=_rerun_failed) \
                    .props("unelevated dense").tooltip(
                        "A new run with just the failed / not-run test cases of this run — passed ones are not repeated")
            if not running and n_failed:
                ui.button(f"Review & raise issues ({n_failed})", icon="bug_report",
                          on_click=lambda: ui.navigate.to(f"/issues?plan_run={quote(self.run_id)}")) \
                    .props("unelevated dense").style(f"background:{COLORS['danger']}")
            if not running and r.get("items"):
                rid = quote(self.run_id)
                with ui.button("Report", icon="description").props("flat dense"):
                    with ui.menu():
                        ui.menu_item("View report", on_click=lambda: ui.navigate.to(
                            f"/testplans/runs/{rid}/report?format=html", new_tab=True))
                        ui.menu_item("Download PDF", on_click=lambda: ui.navigate.to(
                            f"/testplans/runs/{rid}/report?format=pdf", new_tab=True))
                        ui.menu_item("Rebuild report", on_click=lambda: ui.navigate.to(
                            f"/testplans/runs/{rid}/report?format=pdf&regenerate=true", new_tab=True))
                        if can("run"):
                            ui.menu_item("Email report…", on_click=self._email_dialog)
            if not running and can("run"):
                ui.button("Send to Slack", icon="send", on_click=self.resend).props("flat dense")
                ui.button("Re-run plan", icon="replay", on_click=self.rerun) \
                    .props("unelevated dense").style(f"background:{COLORS['primary']}")
        if r.get("status") == "queued":
            muted("Waiting for the plan that is running now to finish (one plan at a time on this machine).")
        for key, colour in (("reason", COLORS["warning"]), ("error", COLORS["danger"])):
            if r.get(key):
                ui.label(r[key]).style(f"color:{colour}; font-size:{TYPOGRAPHY['size_sm']}")

    def _stuck_alert(self) -> None:
        h = self.rec.get("health") or {}
        if not h.get("stuck"):
            return
        kind = h.get("kind")
        live = ""
        if h.get("run_id"):
            live = (f"/run/live?run_id={quote(h['run_id'])}&flow={quote(h.get('test_case') or '')}"
                    f"&platform={quote(h.get('platform') or 'website')}")
        if kind == "orphaned":
            steps = ["The server was restarted (or crashed) while this plan was running, so nothing is "
                     "executing it any more — it will not finish on its own.",
                     "Click “Mark as interrupted” to close it (its finished test cases keep their results).",
                     "Click “Re-run plan” to run it again."]
        elif kind == "queued":
            steps = ["Only one plan runs at a time on this machine; this one is waiting for another.",
                     "Open Test Plans → Recent plan runs and open the one that is running. If it is "
                     "stuck, follow the steps on its page (Stop now).",
                     "This run starts by itself as soon as the other one finishes."]
        else:
            steps = ["Open the live view to see the step it is on and the latest screenshot.",
                     "Look at the browser window on the Mac running the portal: a login / OTP sheet, "
                     "a basic-auth prompt, a captcha or a ‘site can’t be reached’ page will block the step. "
                     "Check the VPN is connected.",
                     "If the step cannot finish, use Stop → Stop now. The remaining steps are marked "
                     "not run and the report is still saved.",
                     "If nothing changes within 2 minutes of Stop now (the browser itself is hung), "
                     "restart the server with ↻ in the top bar — the run is then marked interrupted.",
                     "Fix the cause (or the step, via the test case link below) and click Re-run plan."]
        with ui.column().classes("w-full gap-1").style(
                f"border:1px solid {COLORS['danger']}; background:{COLORS['danger']}0D;"
                f"border-left:5px solid {COLORS['danger']}; border-radius:8px; padding:10px 14px"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("warning").style(f"color:{COLORS['danger']}; font-size:1.3rem")
                ui.label("Execution looks stuck").style(
                    f"font-weight:{TYPOGRAPHY['weight_bold']}; color:{COLORS['danger']}")
            ui.label(h.get("message", "")).style(f"font-size:{TYPOGRAPHY['size_sm']}")
            if h.get("step"):
                ui.label(f"line {h.get('line')}: {h.get('step')}").style(
                    f"font-family:{MONO}; font-size:{TYPOGRAPHY['size_xs']}; word-break:break-all")
            ui.label("What to do").style(f"font-weight:{TYPOGRAPHY['weight_medium']};"
                                         f"font-size:{TYPOGRAPHY['size_sm']}; margin-top:4px")
            for n, s in enumerate(steps, 1):
                ui.label(f"{n}. {s}").style(f"font-size:{TYPOGRAPHY['size_sm']}")
            with ui.row().classes("gap-2").style("margin-top:4px"):
                if live:
                    ui.button("Open live view", icon="visibility",
                              on_click=lambda u=live: ui.navigate.to(u)).props("unelevated dense")
                if kind == "step" and can("run"):
                    ui.button("Stop now", icon="stop", on_click=lambda: self.stop(True)) \
                        .props("unelevated dense color=negative")
                if kind == "orphaned" and can("run"):
                    ui.button("Mark as interrupted", icon="block", on_click=self.close_orphan) \
                        .props("unelevated dense color=negative")
                if kind == "queued":
                    ui.button("Test Plans", icon="list", on_click=lambda: ui.navigate.to("/plans")) \
                        .props("flat dense")

    def _summary(self) -> None:
        r = self.rec
        items = [i for i in (r.get("items") or []) if not i.get("out_of_scope")]
        h = r.get("health") or {}
        n = len(items)
        passed = sum(1 for i in items if i.get("status") == "passed")
        failed = sum(1 for i in items if i.get("status") == "failed")
        not_run = sum(1 for i in items if i.get("status") == "not_run")
        pending = n - passed - failed - not_run
        sp = sum(int(i.get("passed_steps") or 0) for i in items)
        sf = sum(int(i.get("failed_steps") or 0) for i in items)
        ss = sum(int(i.get("skipped_steps") or 0) for i in items)
        if h.get("done") is not None and r.get("status") == "running":
            sp += int(h.get("passed") or 0)
            sf += int(h.get("failed") or 0)
        dur = r.get("duration_s")
        if dur is None and r.get("started_at"):
            dur = _secs(r["started_at"], r.get("finished_at", ""))
        pct = round(100 * passed / n) if n else 0
        with ui.row().classes("w-full gap-3"):
            with _card("Result"):
                colour = COLORS["success"] if pct == 100 else COLORS["danger"] if failed or not_run else COLORS["primary"]
                ui.label(f"{pct}%").style(f"font-size:1.9rem; font-weight:700; color:{colour}; line-height:1.1")
                muted(f"of test cases passed")
                _bar([(passed, COLORS["success"]), (failed, COLORS["danger"]),
                      (not_run, COLORS["text_muted"]), (pending, COLORS["border"])])
            with _card("Test cases"):
                with ui.row().classes("gap-4"):
                    _num(n, "total", COLORS["text"])
                    _num(passed, "passed", COLORS["success"])
                    _num(failed, "failed", COLORS["danger"])
                    known = sum(1 for i in items if i.get("known"))
                    if known:
                        _num(failed - known, "new", COLORS["danger"])
                        _num(known, "known", COLORS["warning"])
                    _num(not_run, "not run", COLORS["text_muted"])
                    if pending:
                        _num(pending, "to run", COLORS["primary"])
            with _card("Steps"):
                with ui.row().classes("gap-4"):
                    _num(sp + sf + ss, "executed", COLORS["text"])
                    _num(sp, "passed", COLORS["success"])
                    _num(sf, "failed", COLORS["danger"])
                    _num(ss, "not run", COLORS["text_muted"])
            with _card("Duration"):
                ui.label(mins(dur)).style("font-size:1.6rem; font-weight:700; line-height:1.15") \
                    .tooltip(f"{int(dur or 0) // 60}m {int(dur or 0) % 60:02d}s")
                done_items = [i for i in items if i.get("status") in ("passed", "failed")]
                if done_items:
                    avg = sum(_item_secs(i) or 0 for i in done_items) / len(done_items)
                    muted(f"avg {mins(avg)} per test case")
                if r.get("status") == "running":
                    muted("still running…")
            with _card("Environment"):
                ex = r.get("execution") or {}
                plats = sorted({i.get("platform") or "website" for i in items})
                muted(f"Platform: {', '.join(plats) or '—'}")
                # Which site the run used — the question "why did it open prot3?"
                # must be answerable from this page.
                se = ex.get("site_env") or ""
                muted("Site: " + ({"live": "live — www.justdial.com"}.get(se, se)
                                  if se else "default — URL as written in the test"))
                muted(f"Browser: {'headless' if ex.get('headless') else 'visible (headed)'}")
                muted(f"Retry failed: {'once' if ex.get('retry_failed') else 'no'} · "
                      f"stop on first failure: {'yes' if ex.get('stop_on_first_failure') else 'no'}")
                sl = r.get("slack")
                if sl:
                    muted(("Slack: posted ✓" + (" · PDF attached" if sl.get("file") == "attached" else ""))
                          if sl.get("sent") else f"Slack: not posted — {sl.get('error')}")
                    if sl.get("warning"):
                        muted(sl["warning"]).style(f"color:{COLORS['warning']}")
                em = r.get("email")
                if em:
                    muted(f"Email: sent to {', '.join(em.get('to') or [])} ✓" if em.get("sent")
                          else f"Email: not sent — {em.get('error')}")

    def _results(self) -> None:
        items = self.rec.get("items") or []
        counts = {"all": len(items),
                  "failed": sum(1 for i in items if i.get("status") == "failed"),
                  "passed": sum(1 for i in items if i.get("status") == "passed"),
                  "not_run": sum(1 for i in items if i.get("status") == "not_run")}
        clusters = [c for c in (self.rec.get("insights") or []) if c.get("count")]
        if clusters and self.rec.get("status") != "running":
            KIND_COL = {"product-defect": COLORS["danger"], "environment": COLORS["text_muted"],
                        "test-problem": COLORS["primary"], "site-change": COLORS["warning"],
                        "data": COLORS["warning"], "flaky": COLORS["warning"]}
            with ui.card().classes("w-full").style(f"border:1px solid {COLORS['border']}; margin-top:6px"):
                ui.label(f"What this run means — {len(clusters)} cause(s) behind "
                         f"{sum(c['count'] for c in clusters)} failed test case(s)").style(
                    f"font-weight:{TYPOGRAPHY['weight_bold']}; font-size:{TYPOGRAPHY['size_md']}")
                for c in clusters:
                    col = KIND_COL.get(c.get("kind", ""), COLORS["text"])
                    with ui.row().classes("w-full items-start gap-2 no-wrap").style(
                            f"border-top:1px solid {COLORS['border']}; padding:6px 0"):
                        ui.label(str(c["count"])).style(f"font-weight:700; color:{col}; min-width:2rem; text-align:right")
                        with ui.column().classes("gap-0").style("flex:1; min-width:0"):
                            with ui.row().classes("items-center gap-2 no-wrap"):
                                ui.label(c.get("kind", "")).style(
                                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{col}; border:1px solid {col};"
                                    "border-radius:10px; padding:0 8px; white-space:nowrap")
                                ui.label(c.get("title", "")).style(f"font-size:{TYPOGRAPHY['size_sm']}; white-space:normal")
                            muted(c.get("action", "")).style("white-space:normal")
                            muted(", ".join(x[:40] for x in c.get("cases", [])[:6])
                                  + (f" … +{c['count'] - 6}" if c["count"] > 6 else "")
                                  + (f"  ·  {', '.join(c.get('browsers') or [])}" if len(c.get("browsers") or []) > 1 else "")) \
                                .style(f"font-family:{MONO}; white-space:normal")
        with ui.row().classes("w-full items-center gap-1").style("margin-top:6px"):
            ui.label("Test case results").style(
                f"font-weight:{TYPOGRAPHY['weight_bold']}; font-size:{TYPOGRAPHY['size_md']}; margin-right:12px")
            for key, label in (("all", "All"), ("failed", "Failed"), ("passed", "Passed"), ("not_run", "Not run")):
                active = self.filter == key
                colour = STATUS_COLOR.get(key, COLORS["primary"])
                ui.button(f"{label} ({counts[key]})", on_click=lambda k=key: self._set_filter(k)) \
                    .props("dense no-caps " + ("unelevated" if active else "flat")) \
                    .style(f"{'background:' + colour + '; color:white' if active else 'color:' + colour}")
        with ui.column().classes("w-full gap-0").style(
                f"border:1px solid {COLORS['border']}; border-radius:8px; overflow:hidden"):
            with ui.row().classes("w-full items-center no-wrap gap-3").style(
                    f"background:{COLORS['surface_alt']}; padding:6px 12px; font-size:0.72rem;"
                    f"color:{COLORS['text_muted']}; font-weight:600; letter-spacing:.04em"):
                ui.label("#").style("width:1.6rem")
                ui.label("RESULT").style("width:6.5rem")
                ui.label("TEST CASE").classes("flex-grow")
                ui.label("STEPS").style("width:12rem")
                ui.label("DURATION").style("width:5.5rem")
                ui.label("").style("width:11rem")
            shown = 0
            for idx, it in enumerate(items):
                if self.filter != "all" and it.get("status") != self.filter:
                    continue
                shown += 1
                self._row(idx, it)
            if not shown:
                muted("No test cases in this view.").style("padding:12px")

    def _set_filter(self, key: str) -> None:
        self.filter = key
        self.draw()

    def _row(self, idx: int, it: dict) -> None:
        st = it.get("status", "pending")
        h = self.rec.get("health") or {}
        live_now = st == "running" and h.get("run_id") == it.get("run_id")
        tint = {"failed": COLORS["danger"] + "08", "running": COLORS["primary"] + "08"}.get(st, "transparent")
        with ui.column().classes("w-full gap-0").style(
                f"border-top:1px solid {COLORS['border']}; background:{tint}"):
            with ui.row().classes("w-full items-center no-wrap gap-3").style("padding:8px 12px"):
                ui.label(str(idx + 1)).style(f"width:1.6rem; color:{COLORS['text_muted']}")
                with ui.element("div").style("width:6.5rem"):
                    chip(st)
                with ui.column().classes("flex-grow gap-0").style("min-width:0"):
                    with ui.row().classes("items-center gap-1 no-wrap"):
                        ui.link(it.get("test_case", ""), _editor_url(it)).style(
                            f"font-family:{MONO}; font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['primary']};"
                            f"font-weight:500; text-decoration:none; word-break:break-all") \
                            .tooltip("Open this test case in the editor")
                        ui.icon("edit").style(f"font-size:0.85rem; color:{COLORS['text_muted']}")
                    att = it.get("attempts_s") or []
                    muted(f"Suite: {it.get('suite', '')} · {it.get('platform') or 'website'}"
                          + (f" · {it['device_label']}" if it.get('device_label') else "")
                          + (f" · attempt {it.get('attempt')}" if (it.get("attempt") or 1) > 1 else "")
                          + (f" · {len(att)} attempts: " + " + ".join(mins(a) for a in att) if len(att) > 1 else "")
                          + (f" · {it['note']}" if it.get("note") else ""))
                with ui.column().classes("gap-1").style("width:12rem"):
                    if live_now and h.get("total"):
                        p, f = int(h.get("passed") or 0), int(h.get("failed") or 0)
                        muted(f"{h.get('done', 0)} / {h.get('total')} steps · {p}✓ {f}✗")
                        _bar([(p, COLORS["success"]), (f, COLORS["danger"]),
                              (max(int(h["total"]) - p - f, 0), COLORS["border"])], "6px")
                    elif it.get("passed_steps") is not None:
                        p, f, s = (int(it.get(k) or 0) for k in ("passed_steps", "failed_steps", "skipped_steps"))
                        muted(f"{p}✓  {f}✗  {s}⊘  of {p + f + s}")
                        _bar([(p, COLORS["success"]), (f, COLORS["danger"]), (s, COLORS["text_muted"])], "6px")
                    else:
                        muted("—")
                secs = _item_secs(it)
                ui.label(mins(secs) if secs is not None else "—").style(
                    f"width:5.5rem; font-family:{MONO}; font-size:{TYPOGRAPHY['size_xs']}") \
                    .tooltip(f"{int(secs) // 60}m {int(secs) % 60:02d}s" if secs is not None else "")
                with ui.row().classes("items-center gap-0 no-wrap justify-end").style("width:11rem"):
                    ui.button(icon="edit_note", on_click=lambda u=_editor_url(it): ui.navigate.to(u)) \
                        .props("flat dense round size=sm").tooltip("Edit test case")
                    if it.get("run_id"):
                        f_, p_ = quote(it.get("test_case", "")), quote(it.get("platform") or "website")
                        ui.button(icon="visibility", on_click=lambda rid=it["run_id"], f=f_, p=p_:
                                  ui.navigate.to(f"/run/live?run_id={rid}&flow={f}&platform={p}")) \
                            .props("flat dense round size=sm").tooltip("Live view")
                        if st in ("passed", "failed", "not_run"):
                            ui.button(icon="assessment", on_click=lambda rid=it["run_id"]:
                                      ui.navigate.to(f"/reports/{rid}")) \
                                .props("flat dense round size=sm").tooltip("Step report with screenshots")
                        ui.button(icon="expand_less" if idx in self.expanded else "expand_more",
                                  on_click=lambda i=idx: self.toggle(i)) \
                            .props("flat dense round size=sm").tooltip("Show steps")
            if live_now and h.get("step"):
                with ui.row().classes("w-full items-center gap-2 no-wrap").style("padding:0 12px 8px 3.2rem"):
                    ui.spinner(size="1em")
                    ui.label(f"now: line {h.get('line')} · {h.get('step')}").style(
                        f"font-family:{MONO}; font-size:{TYPOGRAPHY['size_xs']}; word-break:break-all;"
                        f"color:{COLORS['danger'] if h.get('stuck') else COLORS['primary']}")
                    if h.get("step_minutes") and h["step_minutes"] >= 1:
                        muted(f"({h['step_minutes']:.0f} min on this step)")
            if st == "failed" and it.get("known"):
                k = it["known"]
                with ui.row().classes("items-center gap-2 no-wrap").style("padding:0 12px 4px 3.2rem"):
                    ui.label(f"KNOWN · {k.get('kind', '')}").style(
                        f"font-size:{TYPOGRAPHY['size_xs']}; font-weight:600; color:{COLORS['warning']};"
                        f"border:1px solid {COLORS['warning']}; border-radius:10px; padding:0 8px")
                    ui.label((k.get("jira") + " · " if k.get("jira") else "") + (k.get("title") or "")).style(
                        f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}; white-space:normal") \
                        .tooltip(k.get("note") or "")
            if st == "failed" and it.get("first_failure") and idx not in self.expanded:
                ui.label(it["first_failure"]).style(
                    f"padding:0 12px 8px 3.2rem; color:{COLORS['danger']}; white-space:pre-wrap;"
                    f"font-family:{MONO}; font-size:{TYPOGRAPHY['size_xs']}")
            if st == "not_run" and it.get("reason"):
                muted(("Not in this run — " if it.get("out_of_scope") else "") + it["reason"]) \
                    .style("padding:0 12px 8px 3.2rem")
            if it.get("bands_in") and not it.get("out_of_scope"):
                muted("Runs: " + ", ".join(it["bands_in"])[:400]).style("padding:0 12px 6px 3.2rem")
            for w in (it.get("warnings") or [])[:3]:
                muted(f"⚠️ {w}").style(f"padding:0 12px 6px 3.2rem; color:{COLORS['warning']}")
            if idx in self.expanded:
                self._steps(idx, it)

    def _steps(self, idx: int, it: dict) -> None:
        res = self.steps.get(it.get("run_id") or "") or {}
        with ui.column().classes("w-full gap-0").style(
                f"margin:0 12px 10px 3.2rem; width:calc(100% - 4.2rem); border:1px solid {COLORS['border']};"
                f"border-radius:6px; background:{COLORS['surface']}"):
            if res.get("_error"):
                muted(f"Could not load the steps: {res['_error']}").style("padding:8px")
                return
            log = res.get("log") or []
            if not log and res.get("results"):
                log = [{"step": x.get("test_name"), "status": x.get("status"), "error": x.get("reason"),
                        "duration_ms": x.get("duration_ms"), "line": n}
                       for n, x in enumerate(res["results"], 1)]
            fails = sum(1 for e in log if e.get("status") == "failed")
            only = idx in self.only_failed
            tm = res.get("timing") or {}
            slow = _slowest(log)
            with ui.row().classes("w-full items-center gap-2").style(
                    f"padding:5px 10px; border-bottom:1px solid {COLORS['border']}; background:{COLORS['surface_alt']}"):
                muted(f"{len(log)} step(s)" + (f" · {fails} failed" if fails else ""))
                if tm:
                    muted(f"· time: steps {mins(tm.get('steps_s'))}, screenshots {mins(tm.get('screenshots_s'))}, "
                          f"browser/other {mins(tm.get('other_s'))}")
                ui.space()
            if slow:
                with ui.row().classes("w-full items-center gap-2").style(
                        f"padding:4px 10px; border-bottom:1px solid {COLORS['border']}"):
                    muted("Slowest:")
                    for name, n, secs in slow:
                        ui.label(f"{name} ×{n} = {mins(secs)}").style(
                            f"font-family:{MONO}; font-size:0.7rem; background:{COLORS['warning']}14;"
                            f"color:{COLORS['warning']}; border-radius:4px; padding:1px 6px")
                if fails:
                    ui.switch("Only failed", value=only,
                              on_change=lambda e, i=idx: self._only_failed(i, e.value)).props("dense")
            with ui.column().classes("w-full gap-0").style("max-height:26rem; overflow-y:auto"):
                for n, e in enumerate(log, 1):
                    if only and e.get("status") != "failed":
                        continue
                    self._step(it, n, e)

    def _only_failed(self, idx: int, on: bool) -> None:
        (self.only_failed.add if on else self.only_failed.discard)(idx)
        self.draw()

    def _step(self, it: dict, n: int, e: dict) -> None:
        st = e.get("status", "")
        icon, colour = _STEP_MARK.get(st, ("radio_button_unchecked", COLORS["text_muted"]))
        with ui.column().classes("w-full gap-0").style(
                f"border-bottom:1px solid {COLORS['border']}; padding:4px 10px;"
                + (f"background:{COLORS['danger']}0A;" if st == "failed" else "")):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.label(str(n)).style(f"width:2rem; text-align:right; color:{COLORS['text_muted']};"
                                       f"font-family:{MONO}; font-size:0.7rem")
                ui.icon(icon).style(f"color:{colour}; font-size:1rem")
                ui.label(e.get("step", "")).classes("flex-grow").style(
                    f"font-family:{MONO}; font-size:{TYPOGRAPHY['size_xs']}; word-break:break-all")
                if e.get("line"):
                    ui.link(f"line {e['line']}", _editor_url(it, e["line"])).style(
                        f"font-size:0.7rem; color:{COLORS['text_muted']}; white-space:nowrap") \
                        .tooltip("Open the test case at this step")
                ms = e.get("duration_ms")
                if ms is not None:
                    ui.label(f"{ms / 1000:.1f}s").style(
                        f"font-family:{MONO}; font-size:0.7rem; color:{COLORS['text_muted']}; width:3.2rem;"
                        f"text-align:right")
            if st == "failed" and e.get("error"):
                with ui.row().classes("w-full no-wrap gap-3").style("padding:4px 0 4px 3rem"):
                    ui.label(str(e["error"])[:1500]).classes("flex-grow").style(
                        f"color:{COLORS['danger']}; white-space:pre-wrap; font-size:{TYPOGRAPHY['size_xs']}")
                    if e.get("screenshot"):
                        src = f"/screenshots/{str(e['screenshot']).lstrip('/')}"
                        ui.image(src).style(f"width:11rem; flex:none; border:1px solid {COLORS['border']};"
                                            f"border-radius:4px").classes("cursor-pointer") \
                            .on("click", lambda s=src: ui.navigate.to(s, new_tab=True)).tooltip("Open full size")
                with ui.row().classes("gap-2").style("padding:0 0 4px 3rem"):
                    ui.button("Fix this step", icon="edit",
                              on_click=lambda u=_editor_url(it, e.get("line")): ui.navigate.to(u)) \
                        .props("dense flat no-caps size=sm")
            elif st == "skipped" and e.get("error"):
                muted(e["error"]).style("padding-left:3rem")


async def render(run_id: str) -> None:
    try:
        platforms = await api.platforms()
    except api.ApiError:
        platforms = []
    sidebar(active="/plans", platforms=platforms)
    topbar(["Execute", "Test Plans", "Execution"], platforms=platforms)
    page = PlanRunPage(run_id)
    page.body = ui.column().classes("w-full gap-3 p-4").style("max-width:90rem")
    await page.refresh(force=True)
    ui.timer(2.0, page.refresh)
