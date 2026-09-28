"""
ui/pages/testsigma/index.py — Testsigma import (admin).  Route: /testsigma

1. Pull a Testsigma run (its test cases, steps, step groups and real element
   locators) with the API key in .env.  Nothing in the portal changes yet.
2. Review each test case: the converted plain steps, which steps were switched
   OFF (would send a real lead), TODO lines (no equivalent here yet), and
   elements Testsigma had no locator for.
3. Import the selected test cases (module by module) into Test Cases,
   Elements and Step Groups.
"""
from __future__ import annotations

import json

from nicegui import ui

from ui import api_client as api
from ui.auth import can
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.pages.plans.common import chip, heading, muted
from ui.theme import COLORS, TYPOGRAPHY

MONO = TYPOGRAPHY["mono"]
MODULES = ["NCT", "PRP", "PDP", "Catalogue", "RFQ", "Other"]


async def render(run: str = "115613") -> None:
    try:
        platforms = await api.platforms()
    except api.ApiError:
        platforms = []
    sidebar(active="/testsigma", platforms=platforms)
    topbar(["Manage", "Testsigma import"], platforms=platforms)
    body = ui.column().classes("w-full gap-3 p-4").style("max-width:90rem")
    with body:
        heading("Testsigma import")
        if not can("admin"):
            muted("Admins only.")
            return
        muted("Reads Testsigma with the key in .env (TESTSIGMA_KEY) — read-only, nothing changes in Testsigma. "
              "Steps are converted one to one into plain steps; steps that would send a REAL lead come in "
              "switched OFF.")
        with ui.row().classes("items-center gap-2"):
            run_in = ui.input("Testsigma run id", value=run or "115613").props("outlined dense").style("width:12rem")
            pull_btn = ui.button("Pull from Testsigma", icon="cloud_download").props("unelevated")
            prog = muted("")
        table_box = ui.column().classes("w-full gap-2")
        with ui.expansion("API explorer (troubleshooting)", icon="terminal").classes("w-full"):
            paths = ui.textarea("API paths (one per line)", value="/api/v1/test_suites").props(
                "outlined dense autogrow").classes("w-full")
            fetch_btn = ui.button("Fetch", icon="download").props("flat")
            status = ui.column().classes("w-full gap-0")
            out = ui.code("", language="json").classes("w-full").style("max-height:50vh; overflow:auto")

    state = {"rows": [], "sel": set(), "timer": None}

    async def refresh() -> None:
        try:
            res = await api.testsigma_pull_status(int(run_in.value))
        except (api.ApiError, ValueError) as e:
            prog.set_text(str(getattr(e, "detail", e)))
            return
        st = res.get("status") or {}
        if st.get("state") == "running":
            prog.set_text(f"Pulling… {st.get('done', 0)}/{st.get('total') or '?'} — {st.get('message', '')}")
            return
        if st.get("state") == "error":
            prog.set_text(f"Pull failed: {st.get('message')}")
        elif res.get("cases"):
            prog.set_text(f"{len(res['cases'])} test cases pulled.")
        if state["timer"]:
            state["timer"].cancel()
            state["timer"] = None
        state["rows"] = res.get("cases") or []
        draw()

    async def pull() -> None:
        try:
            await api.testsigma_pull(int(run_in.value))
        except (api.ApiError, ValueError) as e:
            ui.notify(str(getattr(e, "detail", e)), type="negative")
            return
        prog.set_text("Pulling…")
        if state["timer"]:
            state["timer"].cancel()
        state["timer"] = ui.timer(2.0, refresh)
    pull_btn.on_click(pull)

    def toggle(tc: int, on: bool) -> None:
        (state["sel"].add if on else state["sel"].discard)(tc)
        if state.get("count") is not None:
            state["count"].set_text(f"{len(state['sel'])} selected")

    def select_module(mod: str) -> None:
        for r in state["rows"]:
            if r["module"] == mod and not r["imported"]:
                state["sel"].add(r["test_case_id"])
        draw()

    async def do_import(overwrite: bool = False) -> None:
        ids = sorted(state["sel"])
        if not ids:
            ui.notify("Select test cases first", type="warning")
            return
        ui.notify(f"Importing {len(ids)} test case(s)…", type="info")
        try:
            res = await api.testsigma_import(int(run_in.value), ids, overwrite=overwrite)
        except api.ApiError as e:
            ui.notify(e.detail, type="negative")
            return
        ok = [r for r in res["results"] if r.get("status") == "imported"]
        bad = [r for r in res["results"] if r.get("status") != "imported"]
        ui.notify(f"Imported {len(ok)}; {len(bad)} not imported" + (
            " (already exist — use Re-import to overwrite)" if any(b.get('status') == 'exists' for b in bad) else ""),
            type="positive" if not bad else "warning", timeout=10000)
        for b in bad:
            if b.get("status") == "error":
                ui.notify(f"{b['test_case_id']}: {b.get('detail')}", type="negative", timeout=15000)
        state["sel"].clear()
        await refresh()

    async def preview(r: dict) -> None:
        try:
            p = await api.testsigma_preview(int(run_in.value), r["test_case_id"])
        except api.ApiError as e:
            ui.notify(e.detail, type="negative")
            return
        with ui.dialog() as dlg, ui.card().style("width:min(1100px,95vw); max-width:95vw"):
            with ui.row().classes("w-full items-center"):
                ui.label(r["name"]).style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
                ui.space()
                ui.button(icon="close", on_click=dlg.close).props("flat dense round")
            muted(f"{p['steps']} runnable steps · {len(p['off'])} switched OFF · {len(p['todo'])} TODO · "
                  f"{len(p['elements'])} elements ({len(p['missing_elements'])} without a Testsigma locator) · "
                  f"{len(p['groups'])} step group(s)")
            with ui.tabs().props("dense align=left no-caps") as tabs:
                t1, t2, t3, t4 = ui.tab("Steps"), ui.tab("OFF / TODO"), ui.tab("Elements"), ui.tab("Step groups")
            with ui.tab_panels(tabs, value=t1).classes("w-full"):
                with ui.tab_panel(t1):
                    ui.code(p["flow"]).classes("w-full").style("max-height:60vh; overflow:auto")
                with ui.tab_panel(t2):
                    ui.label("Switched OFF (would send a real lead, or disabled in Testsigma)").style("font-weight:600")
                    for o in p["off"] or ["—"]:
                        ui.label(o).style(f"font-family:{MONO}; font-size:{TYPOGRAPHY['size_xs']}")
                    ui.label("TODO (no equivalent step here yet)").style("font-weight:600; margin-top:8px")
                    for t in p["todo"] or ["—"]:
                        ui.label(t).style(f"font-family:{MONO}; font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['warning']}")
                with ui.tab_panel(t3):
                    for name, e in p["elements"].items():
                        with ui.row().classes("w-full no-wrap gap-2"):
                            ui.label("✓" if e["found"] else "⚠").style(
                                f"color:{COLORS['success'] if e['found'] else COLORS['warning']}")
                            ui.label(name).style(f"font-family:{MONO}; font-size:{TYPOGRAPHY['size_xs']}; width:18rem")
                            ui.label(e["xpath"]).style(f"font-family:{MONO}; font-size:{TYPOGRAPHY['size_xs']}; "
                                                       "word-break:break-all")
                with ui.tab_panel(t4):
                    for g, lines in p["groups"].items():
                        ui.label(g).style("font-weight:600")
                        ui.code("\n".join(lines)).classes("w-full")
        dlg.open()

    def draw() -> None:
        table_box.clear()
        rows = state["rows"]
        if not rows:
            return
        with table_box:
            with ui.row().classes("w-full items-center gap-2"):
                state["count"] = muted(f"{len(state['sel'])} selected")
                ui.space()
                for m in MODULES:
                    n = sum(1 for r in rows if r["module"] == m)
                    if n:
                        ui.button(f"Select {m} ({n})", on_click=lambda mm=m: select_module(mm)).props("flat dense no-caps")
                ui.button("Import selected", icon="download_done", on_click=lambda: do_import(False)) \
                    .props("unelevated")
                ui.button("Re-import (overwrite)", icon="sync", on_click=lambda: do_import(True)).props("flat")
            for m in MODULES:
                mod_rows = [r for r in rows if r["module"] == m]
                if not mod_rows:
                    continue
                ui.label(f"{m} · {len(mod_rows)}").style(
                    f"font-weight:{TYPOGRAPHY['weight_bold']}; margin-top:6px")
                with ui.column().classes("w-full gap-0").style(
                        f"border:1px solid {COLORS['border']}; border-radius:6px"):
                    for r in mod_rows:
                        with ui.row().classes("w-full items-center no-wrap gap-2").style(
                                f"padding:4px 10px; border-bottom:1px solid {COLORS['border']}"):
                            ui.checkbox(value=r["test_case_id"] in state["sel"],
                                        on_change=lambda e, tc=r["test_case_id"]: toggle(tc, e.value)).props("dense")
                            ui.label(r["name"]).classes("flex-grow").style(f"font-size:{TYPOGRAPHY['size_sm']}")
                            chip("passed" if r.get("result") == "SUCCESS" else "failed")
                            muted(f"{r.get('steps', 0)} steps")
                            if r.get("imported"):
                                ui.link("in portal ↗", f"/platform/mobilesite?flow={r['flow']}").style(
                                    f"font-size:{TYPOGRAPHY['size_xs']}")
                            ui.button("Preview", on_click=lambda rr=r: preview(rr)).props("flat dense no-caps size=sm")

    async def fetch() -> None:
        status.clear()
        last = None
        for line in [p.strip() for p in (paths.value or "").splitlines() if p.strip()]:
            try:
                res = await api.testsigma_probe(line)
                last = res.get("data")
                msg = f"OK   {line}  →  saved {res.get('saved')}"
            except api.ApiError as e:
                msg = f"ERR  {line}  →  {e.status}: {str(e.detail)[:300]}"
            with status:
                ui.label(msg).style(f"font-family:{MONO}; font-size:{TYPOGRAPHY['size_xs']}")
        out.set_content(json.dumps(last, indent=2, ensure_ascii=False)[:20000] if last is not None else "")
    fetch_btn.on_click(fetch)

    await refresh()

