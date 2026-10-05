"""
ui/pages/plans/index.py — Test Plans.

/plans               plans + recent plan runs
/plans/edit?id=…     create / edit: suites, execution, schedule, Slack
"""
from __future__ import annotations

from urllib.parse import quote

from nicegui import ui

from ui import api_client as api
from ui.auth import can
from ui.layout import module_scope
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.pages.plans.common import chip, confirm, heading, ist, muted
from ui.theme import COLORS, TYPOGRAPHY

from core.run_types import DEFAULTS as RUN_TYPE_DEFAULTS

DAYS = [("mon", "Mon"), ("tue", "Tue"), ("wed", "Wed"), ("thu", "Thu"), ("fri", "Fri"),
        ("sat", "Sat"), ("sun", "Sun")]
FREQ = {"daily": "Every day", "weekdays": "Weekdays (Mon–Fri)", "weekly": "Selected days",
        "hourly": "Every N hours", "once": "Once"}


async def _shell(crumbs: list[str], module: str | None = "") -> str:
    try:
        platforms = await api.platforms()
    except api.ApiError:
        platforms = []
    sidebar(active="/plans", platforms=platforms)
    if module is None:
        topbar(crumbs, platforms=platforms)
        return ""
    module = module_scope.pick(module, platforms)
    topbar(crumbs, platforms=platforms, platform=module,
           on_platform_change=module_scope.switcher("/plans"))
    return module


RUN_TYPE_HELP = {
    "smoke": ("🔥 Smoke", "Only [smoke] bands — is the build alive? Stops at the first failure, "
                         "no retry, screenshots of failures only, one-page report. Minutes."),
    "sanity": ("🎯 Sanity", "[smoke] + [sanity] bands — does the changed area / fixed defect work? "
                           "Runs every tagged check, retries flaky errors, quick report."),
    "regression": ("🔁 Regression", "Everything tagged smoke, sanity or regression (untagged = regression) — "
                                   "did anything else break? Full screenshots, detailed report."),
    "full": ("🧪 Full", "Every step that is not # OFF, including [full]-only bands. Longest, most "
                       "complete. Detailed report."),
}


def _run_menu(plan_id: str, dense: bool = True) -> None:
    """'Run now' with a type chooser — Smoke before a release, Regression nightly, etc."""
    with ui.button("Run now", icon="play_arrow").props("unelevated" + (" dense" if dense else "")) \
            .style(f"background:{COLORS['success']}"):
        with ui.menu():
            ui.menu_item("Run with the plan's type", on_click=lambda: _run_now(plan_id))
            ui.separator()
            for key, (label, help_) in RUN_TYPE_HELP.items():
                with ui.menu_item(on_click=lambda k=key: _run_now(plan_id, k)):
                    with ui.column().classes("gap-0"):
                        ui.label(f"Run as {label}")
                        muted(help_).style("max-width:26rem; white-space:normal")


_STARTING: set[str] = set()


async def _run_now(plan_id: str, run_type: str = "") -> None:
    if plan_id in _STARTING:
        return                      # double-click: one plan run, not two
    _STARTING.add(plan_id)
    try:
        await _start_plan(plan_id, run_type)
    finally:
        _STARTING.discard(plan_id)


async def _start_plan(plan_id: str, run_type: str = "") -> None:
    try:
        r = await api.run_plan(plan_id, run_type)
    except api.ApiError as e:
        ui.notify(e.detail, type="negative")
        return
    if r.get("queued_behind"):
        ui.notify("Another plan is running — this one is queued and starts right after it.",
                  type="info", timeout=8000)
    ui.navigate.to(f"/plans/run/{quote(r['run_id'])}")


async def render_list(module: str = "") -> None:
    module = await _shell(["Execute", "Test Plans"], module)
    with ui.column().classes("w-full gap-3 p-4").style("max-width:80rem"):
        with ui.row().classes("w-full items-center"):
            heading("Test Plans")
            ui.space()
            if can("write"):
                ui.button("New plan", icon="add", on_click=lambda: ui.navigate.to("/plans/edit")) \
                    .props("unelevated").style(f"background:{COLORS['primary']}")
        try:
            sched = await api.scheduler_status()
            muted(("⏰ Scheduler running" if sched.get("running") else "⚠️ Scheduler NOT running")
                  + f" · last check {ist(sched.get('last_tick'), '%H:%M:%S')}"
                  + f" · a run more than {sched.get('grace_minutes')} min late is recorded as missed, not run late")
        except api.ApiError:
            pass
        try:
            items = await api.plans()
        except api.ApiError as e:
            ui.label(e.detail).style(f"color:{COLORS['danger']}")
            return
        # Only plans with this module's suites (a mixed plan shows in each of its modules).
        items = [p for p in items if module_scope.belongs(p.get("platform"), module)]
        if not items:
            muted("No plans yet. A plan runs one or more suites, now or on a schedule, "
                  "and posts the result to Slack.")
        for p in items:
            with ui.card().classes("w-full").style("padding:10px 14px"):
                with ui.row().classes("w-full items-center gap-3 no-wrap"):
                    with ui.column().classes("gap-0 flex-grow"):
                        with ui.row().classes("items-center gap-2"):
                            ui.link(p["name"], f"/plans/edit?id={quote(p['id'])}").style(
                                f"font-weight:{TYPOGRAPHY['weight_bold']}; font-size:{TYPOGRAPHY['size_md']}")
                            rt = (p.get("execution") or {}).get("run_type") or "full"
                            ui.label(RUN_TYPE_HELP.get(rt, ("", ""))[0]).style(
                                f"font-size:{TYPOGRAPHY['size_xs']}; border:1px solid {COLORS['border']};"
                                "border-radius:10px; padding:0 8px")
                            if p["last_run"].get("status"):
                                chip(p["last_run"]["status"])
                        muted(f"{len(p['suites'])} suite(s) · {p['test_case_count']} test case(s) · "
                              f"{', '.join(s['name'] for s in p['suites'])}")
                        muted(f"🗓 {p['schedule_text']}"
                              + (f" · next {ist(p['next_run'], '%a %d %b %H:%M')}" if p.get("next_run") else "")
                              + (f" · last run {ist(p['last_run'].get('at'))}" if p["last_run"].get("at") else "")
                              + (" · Slack ✓" if p["notify"].get("slack") else ""))
                    if can("run"):
                        _run_menu(p["id"])
                    ui.button(icon="edit", on_click=lambda pid=p["id"]: ui.navigate.to(
                        f"/plans/edit?id={quote(pid)}")).props("flat dense")
        ui.separator()
        heading("Recent plan runs")
        await _runs_table("")


async def _runs_table(plan_id: str) -> None:
    try:
        runs = await api.plan_runs(plan_id, 30)
    except api.ApiError:
        runs = []
    if not runs:
        muted("No plan runs yet.")
        return
    cols = [{"name": k, "label": l, "field": k, "align": "left"} for k, l in (
        ("plan", "Plan"), ("status", "Result"), ("result", "Test cases"), ("when", "Started"),
        ("duration", "Duration"), ("by", "Triggered by"))]
    rows = []
    for r in runs:
        t = r.get("totals") or {}
        rows.append({"id": r["id"], "plan": r.get("plan_name"), "status": r.get("status"),
                     "result": f"{t.get('passed', 0)}✓ {t.get('failed', 0)}✗ {t.get('not_run', 0)}⊘ / {t.get('test_cases', 0)}",
                     "when": ist(r.get("started_at") or r.get("queued_at")),
                     "duration": (f"{r['duration_s'] / 60:.1f} min" if r.get("duration_s") is not None
                                  else ("running…" if r.get("status") == "running" else "—")),
                     "by": r.get("triggered_by") or ""})
    t = ui.table(columns=cols, rows=rows, row_key="id").classes("w-full")
    t.add_slot("body-cell-plan", r'''
        <q-td :props="props"><a class="cursor-pointer text-primary"
          @click="$parent.$emit('open', props.row)">{{ props.row.plan }}</a></q-td>''')
    t.add_slot("body-cell-status", r'''
        <q-td :props="props"><q-badge :color="{passed:'positive',failed:'negative',error:'negative',
          running:'primary',queued:'warning',missed:'warning',stopped:'warning'}[props.value]||'grey'"
          :label="props.value" /></q-td>''')
    t.on("open", lambda e: ui.navigate.to(f"/plans/run/{quote(e.args['id'])}"))
    t.on("rowClick", lambda e: ui.navigate.to(f"/plans/run/{quote(e.args[1]['id'])}"))
    t.classes("cursor-pointer")


async def render_edit(plan_id: str = "") -> None:
    await _shell(["Execute", "Test Plans", "Edit" if plan_id else "New"], None)
    data = {"name": "", "description": "", "suites": [],
            "execution": {"headless": False, "stop_on_failure": False,
                          "stop_on_first_failure": False, "retry_failed": True},
            "schedule": {"enabled": False, "frequency": "weekdays", "time": "09:00",
                         "days": ["mon", "tue", "wed", "thu", "fri"], "every_hours": 4, "date": ""},
            "notify": {"slack": True, "channel": "C0AAP4882H4", "when": "always",
                       "email": False, "email_to": ""}}
    if plan_id:
        try:
            data = await api.plan(plan_id)
        except api.ApiError as e:
            ui.label(e.detail).style(f"color:{COLORS['danger']}")
            return
    try:
        all_suites = await api.suites()
    except api.ApiError:
        all_suites = []
    suite_opts = {s["id"]: f"{s['name']}  ({s['platform']}, {s['count']} test cases)" for s in all_suites}
    writable = can("write")
    ex, sc, nt = data["execution"], dict(data.get("schedule") or {}), dict(data.get("notify") or {})

    with ui.column().classes("w-full gap-3 p-4").style("max-width:62rem"):
        with ui.row().classes("w-full items-center"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/plans")).props("flat dense")
            heading(data["name"] or "New plan")
            ui.space()
            if plan_id and can("run"):
                _run_menu(plan_id, dense=False)
        if plan_id:
            muted(f"Created by {data.get('created_by') or '—'} {ist(data.get('created_at'))} · "
                  f"last changed by {data.get('updated_by') or '—'} {ist(data.get('updated_at'))}")

        with ui.card().classes("w-full"):
            ui.label("Plan").style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
            name = ui.input("Plan name", value=data["name"]).props("outlined dense").classes("w-full")
            desc = ui.textarea("Description", value=data.get("description", "")) \
                .props("outlined dense autogrow").classes("w-full")
            chosen = ui.select(suite_opts, value=[s["id"] for s in data.get("suites", []) if s["id"] in suite_opts],
                               multiple=True, label="Suites (run in this order)") \
                .props("outlined dense use-chips").classes("w-full")
            if not suite_opts:
                muted("No suites yet — create one under Test Suites first.")

        with ui.card().classes("w-full"):
            ui.label("Execution").style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
            run_type = ui.select({k: v[0] for k, v in RUN_TYPE_HELP.items()},
                                 value=ex.get("run_type") or "full", label="Execution type") \
                .props("outlined dense").style("min-width:16rem")
            type_help = muted(RUN_TYPE_HELP.get(ex.get("run_type") or "full")[1])
            muted("Tag bands in a test case with [smoke] / [sanity] / [regression] / [full] on the "
                  "'Purpose' line (untagged = regression; a flow-wide default goes in a '# Tags:' line).")
            preview_box = ui.column().classes("w-full gap-0")
            headless = ui.switch("Headless (no visible browser)", value=bool(ex.get("headless")))
            muted("Leave off for runs that need the VPN-authenticated visible browser.")
            retry = ui.switch("Re-run a failed test case once before marking it failed",
                              value=bool(ex.get("retry_failed")))
            retry_mode = ui.select({"flaky": "Only when it failed on a timeout / network / browser error "
                                             "(recommended — saves time)",
                                    "always": "Always, whatever the failure"},
                                   value=ex.get("retry_mode") or "flaky", label="Re-run when") \
                .props("outlined dense").classes("w-full")
            retry_mode.bind_visibility_from(retry, "value")
            stop_step = ui.switch("Inside a test case, stop at its first failed step",
                                  value=bool(ex.get("stop_on_failure")))
            stop_plan = ui.switch("Stop the whole plan after the first failed test case",
                                  value=bool(ex.get("stop_on_first_failure")))
            shots = ui.select({"all": "Every step", "key": "Checks and new pages only",
                               "failure": "Only the failing step (and the few before it)"},
                              value=ex.get("screenshot_mode") or "all", label="Screenshots") \
                .props("outlined dense").style("min-width:22rem")
            muted("Steps switched off in a test case (# OFF, e.g. lead submission) never run in a plan.")
            parallel = ui.select({1: "1 — one test case at a time", 2: "2 at a time", 3: "3 at a time",
                                  4: "4 at a time (recommended on this Mac)", 5: "5 at a time",
                                  6: "6 at a time"},
                                 value=int(ex.get("parallel") or 1), label="Run test cases in parallel") \
                .props("outlined dense").style("min-width:22rem")
            muted("Each parallel test case is its own browser (~1 CPU core, ~0.7 GB). Slowest test "
                  "cases start first. Live step view is per test case report while running in parallel.")

            # Device / browser matrix — a design change has to be seen on the
            # browsers people actually use, not only on the platform default.
            # Each ticked profile runs every test case once more.
            DEVICE_PROFILES = {
                "android_chrome":   {"label": "Chrome on Android (Pixel 7) — full suite", "device_name": "Pixel 7",
                                     "browser": "chromium", "browser_identity": "android_chrome", "coverage": "full"},
                "ios_safari":       {"label": "Safari on iPhone (iPhone 15, WebKit) — full suite", "device_name": "iPhone 15",
                                     "browser": "webkit", "browser_identity": "ios_safari", "coverage": "full"},
                "samsung_internet": {"label": "Samsung Internet (Galaxy S9+) — positive cases only",
                                     "device_name": "Galaxy S9+", "browser": "chromium",
                                     "browser_identity": "samsung_internet", "coverage": "positive"},
                "ios_chrome":       {"label": "Chrome on iPhone (iPhone 15) — positive cases only",
                                     "device_name": "iPhone 15", "browser": "webkit",
                                     "browser_identity": "ios_chrome", "coverage": "positive"},
            }
            # The saved choice is an option from the start: the full list arrives a
            # moment later, and a value missing from the options broke the page.
            _saved_env = ex.get("site_env") or ""
            _first = {"": "Default — each test case's own URL (prot3, devx …)"}
            if _saved_env:
                _first[_saved_env] = ("live — www.justdial.com" if _saved_env == "live" else _saved_env)
            site_env_sel = ui.select(_first, value=_saved_env,
                                     label="Environment (where the test cases run)") \
                .props("outlined dense").style("min-width:22rem")
            muted("Only the servers of this plan's modules are listed (Website: staging2, stg · "
                  "Mobile Site: prot, prot3, prot4, devx, designtest, seo · live: both). Every "
                  "www.justdial.com URL in its test cases moves to the chosen server and its saved "
                  "login is attached.")

            _suite_platform = {s["id"]: (s.get("platform") or "").lower() for s in all_suites}

            async def _fill_envs(_=None) -> None:
                # Only the servers of the modules in this plan: a Mobile Site plan
                # lists Mobile Site servers, a Website plan Website servers.
                mods = {_suite_platform.get(sid, "") for sid in (chosen.value or [])}
                mods = {m for m in mods if m in ("website", "mobilesite")}
                try:
                    envs = await api.site_environments(next(iter(mods)) if len(mods) == 1 else "")
                except api.ApiError:
                    envs = []
                if len(mods) > 1:
                    pass                                  # mixed plan: show all, labelled
                elif not mods:
                    envs = [e for e in envs if e["name"] == "live"]
                opts = {"": "Default — each test case's own URL (prot3, devx …)"}
                for e in envs:
                    only = e.get("platforms") or []
                    tag = "" if len(mods) < 2 else \
                          "  (Website only)" if only == ["website"] else \
                          "  (Mobile Site only)" if only == ["mobilesite"] else ""
                    opts[e["name"]] = f"{e['name']} — {e['host']}{tag}"
                keep = site_env_sel.value if site_env_sel.value in opts else ""
                site_env_sel.set_options(opts, value=keep)
            ui.timer(0.1, _fill_envs, once=True)
            chosen.on_value_change(_fill_envs)

            devices = ui.select({k: v["label"] for k, v in DEVICE_PROFILES.items()}, multiple=True,
                                value=[d.get("browser_identity") for d in (ex.get("devices") or [])
                                       if isinstance(d, dict) and d.get("browser_identity") in DEVICE_PROFILES],
                                label="Browsers / devices (Mobile Site test cases)") \
                .props("outlined dense use-chips").classes("w-full")
            muted("Nothing ticked = the platform's default device (Pixel 7 / Chrome). Chrome Android and "
                  "Safari iPhone run the whole suite; 'positive cases only' browsers run just the test "
                  "cases tagged smoke / sanity (their '# Tags:' line). Website test cases ignore this.")

            # Fill these settings from a saved run configuration (Run Center 💾).
            cfg_sel = ui.select({"": "—"}, value="", label="Fill from a saved run configuration") \
                .props("outlined dense").style("min-width:22rem")
            _cfgs: dict = {}

            async def _fill_cfgs(_=None) -> None:
                mods = {_suite_platform.get(sid, "") for sid in (chosen.value or [])} - {""} \
                    or {"mobilesite", "website"}
                _cfgs.clear()
                for m in sorted(mods):
                    try:
                        for c in await api.run_configs(m):
                            _cfgs[c["id"]] = c
                    except api.ApiError:
                        pass
                cfg_sel.set_options({"": "—", **{k: f"{c['name']}  ({c['module']}) — {c.get('summary', '')}"
                                                 for k, c in _cfgs.items()}}, value="")

            def _apply_cfg(e) -> None:
                c = _cfgs.get(e.value or "")
                if not c:
                    return
                st = c.get("settings") or {}
                headless.value = bool(st.get("headless", headless.value))
                stop_step.value = bool(st.get("stop_on_failure", stop_step.value))
                if st.get("screenshot_mode") in (shots.options or {}):
                    shots.value = st["screenshot_mode"]
                if (st.get("site_env") or "") in (site_env_sel.options or {}):
                    site_env_sel.value = st.get("site_env") or ""
                if st.get("browser_identity") in DEVICE_PROFILES:
                    devices.value = [st["browser_identity"]]
                ui.notify(f"Settings filled from '{c['name']}' — save the plan to keep them",
                          type="positive")
            cfg_sel.on_value_change(_apply_cfg)
            ui.timer(0.2, _fill_cfgs, once=True)
            chosen.on_value_change(_fill_cfgs)

            def on_type(e) -> None:
                d = RUN_TYPE_DEFAULTS.get(e.value) or {}
                type_help.set_text(RUN_TYPE_HELP[e.value][1])
                stop_step.value = d.get("stop_on_failure", stop_step.value)
                stop_plan.value = d.get("stop_on_first_failure", stop_plan.value)
                retry.value = d.get("retry_failed", retry.value)
                shots.value = d.get("screenshot_mode", shots.value)
                ui.notify(f"{RUN_TYPE_HELP[e.value][0]}: stop / retry / screenshot settings set to the "
                          "usual ones for this type — change them if you need to.", type="info")
            run_type.on_value_change(on_type)

            async def load_preview() -> None:
                if not plan_id:
                    return
                try:
                    pv = await api.preview_plan(plan_id)
                except api.ApiError:
                    return
                preview_box.clear()
                with preview_box:
                    with ui.row().classes("gap-4 items-center").style(
                            f"border:1px dashed {COLORS['border']}; border-radius:6px; padding:6px 10px"):
                        muted("What each type would run:")
                        for k, (label, _h) in RUN_TYPE_HELP.items():
                            v = pv.get(k) or {}
                            ui.label(f"{label}: {v.get('steps', 0)} steps · {v.get('test_cases', 0)} test case(s)") \
                                .style(f"font-size:{TYPOGRAPHY['size_xs']}")
                    warns = sorted({w for v in pv.values() for it in v.get("items", []) for w in it.get("warnings", [])})
                    for w in warns[:5]:
                        muted(f"⚠️ {w}").style(f"color:{COLORS['warning']}")
            ui.timer(0.1, load_preview, once=True)

        with ui.card().classes("w-full"):
            ui.label("Schedule (IST)").style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
            enabled = ui.switch("Run on a schedule", value=bool(sc.get("enabled")))
            with ui.row().classes("w-full items-center gap-3") as sched_row:
                freq = ui.select(FREQ, value=sc.get("frequency") or "weekdays", label="Repeat") \
                    .props("outlined dense").style("min-width:14rem")
                time_in = ui.input("Time (24 h)", value=sc.get("time") or "09:00") \
                    .props("outlined dense mask='##:##'").style("width:8rem")
                every = ui.number("Every N hours", value=sc.get("every_hours") or 4, min=1, max=24) \
                    .props("outlined dense").style("width:9rem")
                date_in = ui.input("Date", value=sc.get("date") or "").props("outlined dense type=date") \
                    .style("width:11rem")
            days = ui.select(dict(DAYS), value=sc.get("days") or ["mon", "tue", "wed", "thu", "fri"],
                             multiple=True, label="Days").props("outlined dense use-chips").classes("w-full")
            if data.get("next_run"):
                muted(f"Next run: {ist(data['next_run'], '%A %d %b %Y, %H:%M')}")
            muted("The server on this Mac runs the plan, on VPN. If the Mac is asleep or the "
                  "portal is closed at that time, the run is recorded as missed (not run late).")

            def refresh_sched(*_) -> None:
                on = bool(enabled.value)
                sched_row.set_visibility(on)
                f = freq.value
                every.set_visibility(on and f == "hourly")
                date_in.set_visibility(on and f == "once")
                days.set_visibility(on and f == "weekly")
            for w in (enabled, freq):
                w.on_value_change(refresh_sched)
            refresh_sched()

        with ui.card().classes("w-full"):
            ui.label("Notifications").style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
            slack = ui.switch("Post the result to Slack", value=bool(nt.get("slack", True)))
            with ui.row().classes("w-full items-center gap-3"):
                channel = ui.input("Slack channel id", value=nt.get("channel") or "C0AAP4882H4") \
                    .props("outlined dense").style("width:14rem")
                when = ui.select({"always": "Every run", "failure": "Only when something fails"},
                                 value=nt.get("when") or "always", label="When").props("outlined dense") \
                    .style("min-width:16rem")
            muted("C0AAP4882H4 = #b2b-selenium-reports. Needs SLACK_BOT_TOKEN in .env with the bot "
                  "invited to the channel. The PDF report is posted in the message thread (needs the "
                  "files:write scope).")
            ui.separator()
            email_on = ui.switch("Email the report", value=bool(nt.get("email", False)))
            email_to = ui.input("Recipients (comma separated)", value=nt.get("email_to") or "") \
                .props("outlined dense").classes("w-full")
            muted("Formatted summary in the body + full PDF attached. Uses SMTP_HOST / SMTP_PORT / "
                  "SMTP_USER / SMTP_PASSWORD from .env. Same 'When' setting as Slack.")

        msg = ui.label().style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['danger']}")

        async def save() -> None:
            schedule = {"enabled": bool(enabled.value), "frequency": freq.value,
                        "time": (time_in.value or "").strip(), "days": days.value or [],
                        "every_hours": int(every.value or 1), "date": date_in.value or "",
                        "timezone": "Asia/Kolkata"}
            execution = {"headless": bool(headless.value), "retry_failed": bool(retry.value),
                         "retry_mode": retry_mode.value or "flaky",
                         "run_type": run_type.value or "full",
                         "screenshot_mode": shots.value or "all",
                         "stop_on_failure": bool(stop_step.value),
                         "stop_on_first_failure": bool(stop_plan.value),
                         "devices": [DEVICE_PROFILES[k] for k in (devices.value or []) if k in DEVICE_PROFILES],
                         "site_env": site_env_sel.value or "",
                         "parallel": int(parallel.value or 1)}
            notify = {"slack": bool(slack.value), "channel": (channel.value or "").strip(),
                      "when": when.value, "email": bool(email_on.value),
                      "email_to": (email_to.value or "").strip()}
            try:
                res = await api.save_plan((name.value or "").strip(), list(chosen.value or []),
                                          description=desc.value or "", execution=execution,
                                          schedule=schedule, notify=notify, plan_id=plan_id)
            except api.ApiError as e:
                msg.set_text(e.detail)
                return
            ui.notify(f"Saved plan '{res['name']}' — {res['schedule_text']}", type="positive")
            ui.navigate.to(f"/plans/edit?id={quote(res['id'])}")

        async def do_delete() -> None:
            try:
                await api.delete_plan(plan_id)
            except api.ApiError as e:
                ui.notify(e.detail, type="negative")
                return
            ui.navigate.to("/plans")

        with ui.row().classes("w-full items-center gap-2"):
            if writable and plan_id:
                ui.button("Delete plan", icon="delete_outline",
                          on_click=lambda: confirm(f"Delete plan '{data['name']}'?",
                                                   "Its schedule stops. Suites, test cases and past "
                                                   "run reports are kept.", do_delete)) \
                    .props("flat color=negative")
            ui.space()
            if writable:
                ui.button("Save plan", icon="save", on_click=save).props("unelevated") \
                    .style(f"background:{COLORS['primary']}")
            else:
                muted("Read-only — your role is viewer.")
        if plan_id:
            ui.separator()
            heading("Runs of this plan")
            await _runs_table(plan_id)
