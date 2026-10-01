"""
ui/pages/reports/index.py — Reports  (route: /reports)

Every report in one place.

    Summary strip   runs in the last 7 days · pass rate · avg plan duration ·
                    most failed test case
    Tab "Test plan reports"
                    one row per plan run: plan, result, pass %, test cases,
                    duration (min), started, trigger — and View execution /
                    HTML report / PDF download
    Tab "Test case runs"
                    one row per test case run (Run Center or plan): result,
                    steps, duration, started, by — opens the step report
    Filters         search by name, status
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from nicegui import ui

from ui import api_client as api
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.pages.plans.common import ist, muted
from ui.theme import COLORS, TYPOGRAPHY

_BADGE = {"passed": "positive", "failed": "negative", "error": "negative", "running": "primary",
          "queued": "warning", "missed": "warning", "stopped": "warning", "unreadable": "warning"}


def _mins(sec) -> str:
    try:
        return f"{float(sec) / 60:.1f} min"
    except (TypeError, ValueError):
        return "—"


def _tile(label: str, value: str, sub: str = "", colour: str = "") -> None:
    with ui.column().classes("gap-0").style(
            f"flex:1 1 12rem; border:1px solid {COLORS['border']}; border-radius:8px; padding:10px 14px;"
            f"background:{COLORS['surface']}"):
        ui.label(label.upper()).style(f"font-size:0.68rem; letter-spacing:.06em; color:{COLORS['text_muted']}")
        ui.label(value).style(f"font-size:1.45rem; font-weight:700; color:{colour or COLORS['text']}")
        if sub:
            muted(sub)


async def render(module: str = "") -> None:
    from ui.layout import module_scope
    try:
        platforms = await api.platforms()
    except api.ApiError:
        platforms = []
    module = module_scope.pick(module, platforms)
    sidebar(active="/reports", platforms=platforms)
    topbar(["Manage", "Reports"], platforms=platforms, platform=module,
           on_platform_change=module_scope.switcher("/reports"))
    try:
        plan_runs = await api.plan_runs("", 200)
    except api.ApiError:
        plan_runs = []
    try:
        tc_runs = await api.run_history(200)
    except api.ApiError:
        tc_runs = []
    # Only this module's reports (screenshots and videos live inside them).
    tc_runs = [r for r in tc_runs if module_scope.belongs(r.get("platform"), module)]
    try:
        _plan_mod = {p["id"]: p.get("platform", "") for p in await api.plans()}
    except api.ApiError:
        _plan_mod = {}
    plan_runs = [r for r in plan_runs
                 if module_scope.belongs(r.get("platform") or _plan_mod.get(r.get("plan") or r.get("plan_id"), ""),
                                         module)]

    week_ago = datetime.now(timezone.utc) - timedelta(days=7)

    def recent(iso: str) -> bool:
        try:
            return datetime.fromisoformat(iso).astimezone(timezone.utc) >= week_ago
        except (TypeError, ValueError):
            try:   # test case reports store local naive time
                return datetime.fromisoformat(iso) >= datetime.now() - timedelta(days=7)
            except (TypeError, ValueError):
                return False

    week_plans = [r for r in plan_runs if recent(r.get("started_at") or r.get("queued_at") or "")
                  and r.get("status") in ("passed", "failed", "stopped", "error")]
    week_tc = [r for r in tc_runs if recent(r.get("started_at") or "")]
    tc_pass = sum(1 for r in week_tc if r.get("status") == "passed")
    durs = [r["duration_s"] for r in week_plans if r.get("duration_s")]
    worst = Counter(r.get("flow") for r in week_tc if r.get("status") == "failed").most_common(1)

    with ui.column().classes("w-full gap-3 p-4").style("max-width:90rem"):
        with ui.row().classes("w-full items-center"):
            ui.label("Reports").style(f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
            ui.space()
            muted("Plan reports come as a web page and a PDF (the same PDF goes to Slack / email).")
        with ui.row().classes("w-full gap-3"):
            _tile("Plan runs · 7 days", str(len(week_plans)),
                  f"{sum(1 for r in week_plans if r.get('status') == 'passed')} passed")
            pct = round(100 * tc_pass / len(week_tc)) if week_tc else 0
            _tile("Test case pass rate · 7 days", f"{pct}%" if week_tc else "—",
                  f"{tc_pass} of {len(week_tc)} test case runs",
                  COLORS["success"] if pct >= 90 else COLORS["danger"] if week_tc else "")
            _tile("Avg plan duration", _mins(sum(durs) / len(durs)) if durs else "—",
                  f"over {len(durs)} run(s)" if durs else "")
            _tile("Most failed test case", (worst[0][0] or "—") if worst else "—",
                  f"{worst[0][1]} failure(s) this week" if worst else "no failures this week",
                  COLORS["danger"] if worst else COLORS["success"])

        with ui.row().classes("w-full items-center gap-3"):
            search = ui.input(placeholder="Search plan / test case").props("outlined dense clearable") \
                .style("width:20rem")
            status = ui.select({"": "All results", "passed": "Passed", "failed": "Failed"}, value="") \
                .props("outlined dense").style("width:12rem")

        with ui.tabs().classes("w-full").props("align=left dense no-caps") as tabs:
            t_plans = ui.tab(f"Test plan reports ({len(plan_runs)})")
            t_cases = ui.tab(f"Test case runs ({len(tc_runs)})")
        with ui.tab_panels(tabs, value=t_plans).classes("w-full").style("background:transparent"):
            with ui.tab_panel(t_plans).style("padding:0"):
                prow = []
                for r in plan_runs:
                    t = r.get("totals") or {}
                    n = t.get("test_cases") or 0
                    prow.append({
                        "id": r["id"], "plan": r.get("plan_name") or "", "status": r.get("status") or "",
                        "type": (r.get("run_type") or "full").title(),
                        "pct": f"{round(100 * (t.get('passed') or 0) / n)}%" if n else "—",
                        "cases": f"{t.get('passed', 0)} ✓  {t.get('failed', 0)} ✗  {t.get('not_run', 0)} ⊘  / {n}",
                        "duration": _mins(r["duration_s"]) if r.get("duration_s") is not None else "—",
                        "when": ist(r.get("started_at") or r.get("queued_at"), "%d %b %Y %H:%M"),
                        "by": ("⏰ " if r.get("trigger") == "schedule" else "") + (r.get("triggered_by") or ""),
                        "done": r.get("status") in ("passed", "failed", "stopped", "error"),
                    })
                cols = [{"name": k, "label": l, "field": k, "align": "left", "sortable": k in ("plan", "when", "status")}
                        for k, l in (("plan", "Plan"), ("type", "Type"), ("status", "Result"), ("pct", "Pass %"),
                                     ("cases", "Test cases"), ("duration", "Duration"), ("when", "Started (IST)"),
                                     ("by", "Triggered by"), ("actions", ""))]
                pt = ui.table(columns=cols, rows=prow, row_key="id", pagination=25).classes("w-full")
                pt.add_slot("body-cell-status", r'''<q-td :props="props"><q-badge :color="{passed:'positive',failed:'negative',error:'negative',running:'primary',queued:'warning',missed:'warning',stopped:'warning'}[props.value]||'grey'" :label="props.value"/></q-td>''')
                pt.add_slot("body-cell-plan", r'''<q-td :props="props"><a class="text-primary cursor-pointer" style="font-weight:600" @click="$parent.$emit('view', props.row)">{{ props.value }}</a><div style="font-size:11px;color:#64748B">{{ props.row.id }}</div></q-td>''')
                pt.add_slot("body-cell-actions", r'''<q-td :props="props" style="white-space:nowrap">
                  <q-btn flat dense round size="sm" icon="visibility" @click="$parent.$emit('view', props.row)"><q-tooltip>View execution</q-tooltip></q-btn>
                  <q-btn v-if="props.row.done" flat dense round size="sm" icon="description" @click="$parent.$emit('html', props.row)"><q-tooltip>Report (web page)</q-tooltip></q-btn>
                  <q-btn v-if="props.row.done" flat dense round size="sm" icon="picture_as_pdf" color="negative" @click="$parent.$emit('pdf', props.row)"><q-tooltip>Download PDF</q-tooltip></q-btn>
                </q-td>''')
                pt.on("view", lambda e: ui.navigate.to(f"/plans/run/{quote(e.args['id'])}"))
                pt.on("html", lambda e: ui.navigate.to(f"/testplans/runs/{quote(e.args['id'])}/report?format=html", new_tab=True))
                pt.on("pdf", lambda e: ui.navigate.to(f"/testplans/runs/{quote(e.args['id'])}/report?format=pdf", new_tab=True))
                if not prow:
                    muted("No plan runs yet — run a Test Plan and its report appears here.")

            with ui.tab_panel(t_cases).style("padding:0"):
                crow = []
                for r in tc_runs:
                    s = r.get("summary") or {}
                    crow.append({
                        "id": r["run_id"], "flow": r.get("flow") or "", "status": r.get("status") or "",
                        "steps": f"{s.get('passed', 0)} ✓  {s.get('failed', 0)} ✗  {s.get('skipped', 0)} ⊘  / {s.get('total', 0)}",
                        "duration": _mins(r.get("duration_s")) if r.get("duration_s") else "—",
                        "when": (r.get("started_at") or "").replace("T", " ")[:16],
                        "by": r.get("triggered_by") or r.get("executer") or "",
                        "plan": r.get("plan_run") or "",
                    })
                cols = [{"name": k, "label": l, "field": k, "align": "left", "sortable": k in ("flow", "when", "status")}
                        for k, l in (("flow", "Test case"), ("status", "Result"), ("steps", "Steps"),
                                     ("duration", "Duration"), ("when", "Started"), ("by", "By"),
                                     ("plan", "Plan run"))]
                ct = ui.table(columns=cols, rows=crow, row_key="id", pagination=25).classes("w-full")
                ct.add_slot("body-cell-status", r'''<q-td :props="props"><q-badge :color="{passed:'positive',failed:'negative',unreadable:'warning'}[props.value]||'grey'" :label="props.value"/></q-td>''')
                ct.add_slot("body-cell-flow", r'''<q-td :props="props"><a class="text-primary cursor-pointer" style="font-weight:600;font-family:monospace" @click="$parent.$emit('open', props.row)">{{ props.value }}</a></q-td>''')
                ct.add_slot("body-cell-plan", r'''<q-td :props="props"><a v-if="props.value" class="text-primary cursor-pointer" style="font-size:11px" @click="$parent.$emit('plan', props.row)">{{ props.value }}</a></q-td>''')
                ct.on("open", lambda e: ui.navigate.to(f"/reports/{quote(e.args['id'])}"))
                ct.on("plan", lambda e: ui.navigate.to(f"/plans/run/{quote(e.args['plan'])}"))

        def apply() -> None:
            q = (search.value or "").lower().strip()
            st = status.value or ""
            pt.rows = [r for r in prow if (not q or q in r["plan"].lower()) and (not st or r["status"] == st)]
            ct.rows = [r for r in crow if (not q or q in r["flow"].lower()) and (not st or r["status"] == st)]
            pt.update()
            ct.update()

        search.on_value_change(lambda _: apply())
        status.on_value_change(lambda _: apply())
