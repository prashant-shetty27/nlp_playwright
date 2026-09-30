"""
ui/pages/platform/new_test_case.py — [+ New Test Case] dialog

Three origins for a test case, all landing in the same reviewable step list:

    Blank          hand-author with autocomplete (the spec's original behaviour)
    Spreadsheet    upload .xlsx, pick testcases, generate steps
    Prompt         describe it in prose, Claude drafts testcases, generate steps

The generated routes are NOT a shortcut past review. Every step comes back with
the status the mapper assigned it, and steps that cannot run are shown as such
before anything is saved — a generated flow that looks finished but is missing a
locator is worse than one that says so.

Not in the original spec: it predates generation entirely. Presented under the
spec's own [+ New Test Case] affordance rather than as a separate screen, so the
navigation the spec describes is unchanged.
"""
from __future__ import annotations

import asyncio

from typing import Callable

from nicegui import ui

from ui import api_client as api
from ui.theme import COLORS, TYPOGRAPHY


async def new_test_case_dialog(platform: str, on_created: Callable, folder: str = "") -> None:
    """on_created(name, steps, meta, folder, also_saved=[…]) — folder is always set."""
    from ui.components.folder_picker import FolderPicker

    state: dict = {"source_id": None, "testcases": [], "chosen": set(),
                   "inputs": {}, "unclear": [], "assumptions": []}

    dialog = ui.dialog().props("persistent")
    # The card scrolls: a drafted ticket lists 15–20 cases, and the "Save as"
    # box and the Generate button below the list were pushed off the bottom
    # of a fixed-height dialog — there was no way to finish.
    with dialog, ui.card().style("width:64rem; max-width:95vw; max-height:92vh;"
                                 " overflow-y:auto"):
        ui.label("New Test Case").style(
            f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
        # Where it is saved — asked up front, for every way of creating one.
        picker = FolderPicker(folder)
        with ui.tabs().classes("w-full") as tabs:
            t_blank = ui.tab("Blank", icon="edit")
            t_sheet = ui.tab("From spreadsheet", icon="table_view")
            t_prompt = ui.tab("From prompt", icon="auto_awesome")

        with ui.tab_panels(tabs, value=t_blank).classes("w-full"):
            # ── blank ───────────────────────────────────────────────────────
            with ui.tab_panel(t_blank):
                name = ui.input("Test case name", placeholder="ask_more_photos") \
                    .props("outlined dense").classes("w-full")

                async def make_blank() -> None:
                    n = (name.value or "").strip()
                    if not n:
                        ui.notify("Give it a name", type="warning")
                        return
                    where = picker.require()
                    if where is None:
                        return
                    # The same check Rename uses: an existing name used to
                    # open an empty editor over the old test case, and the
                    # first Save silently replaced it.
                    try:
                        chk = await api.check_flow_name(n)
                    except api.ApiError:
                        chk = {"ok": True, "saved_as": n}
                    if not chk.get("ok"):
                        ui.notify(chk.get("reason") or "That name cannot be used", type="warning")
                        return
                    if chk.get("exists"):
                        ui.notify(f"'{chk.get('saved_as')}' already exists — open it from the "
                                  f"list, or pick another name", type="warning", timeout=6000)
                        return
                    dialog.close()
                    await on_created(chk.get("saved_as") or n, [], {}, where)

                ui.button("Create empty", on_click=make_blank).props("unelevated")

            # ── spreadsheet ─────────────────────────────────────────────────
            with ui.tab_panel(t_sheet):
                ui.label("Upload a testcase workbook (.xlsx)").style(
                    f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")
                sheet_area = ui.column().classes("w-full gap-2")

                async def handle_upload(e) -> None:
                    try:
                        # NiceGUI 3.x: e.file (async read) replaced e.name / e.content.
                        data = await e.file.read()
                        res = await api.upload_source(e.file.name, data)
                    except api.ApiError as err:
                        ui.notify(f"Upload rejected: {err.detail}", type="negative")
                        return
                    state["source_id"] = res["source_id"]
                    state["testcases"] = res.get("testcases", [])
                    ui.notify(f"{res['counts']['testcases']} testcases read from "
                              f"{res['filename']}", type="positive")
                    _render_picker(sheet_area)

                ui.upload(on_upload=handle_upload, auto_upload=True) \
                    .props('accept=".xlsx"').classes("w-full")

            # ── prompt ──────────────────────────────────────────────────────
            # Drafting from a ticket is a page's worth of reading (questions,
            # assumptions, twenty cases with steps), so it lives on its own
            # page; this tab is the door to it.
            with ui.tab_panel(t_prompt):
                ui.label("Paste a Jira link or describe what to test; the ticket is read in "
                         "full and turned into end-to-end positive and negative cases, with "
                         "questions for you where the ticket is silent.").style(
                    f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")
                def open_drafter() -> None:
                    where = picker.require()
                    if where is None:
                        return
                    dialog.close()
                    from urllib.parse import quote as _q
                    ui.navigate.to(f"/platform/{platform}/draft?folder={_q(where, safe='')}")

                ui.button("Open the drafter", icon="auto_awesome", on_click=open_drafter) \
                    .props("unelevated").style(f"background:{COLORS['accent']}")

        def _render_picker(container, show_notes: bool = False) -> None:
            container.clear()
            with container:
                # What was actually read from Jira — so "drafted from the ticket"
                # is a checkable claim, not a hope.
                for j in (state.get("jira") or []) if show_notes else []:
                    with ui.row().classes("items-center gap-2 w-full").style(
                            f"background:{COLORS['success']}14; border-radius:6px; padding:6px 10px"):
                        ui.icon("task_alt").style(f"color:{COLORS['success']}")
                        ui.label(f"Read {j.get('key')} ({j.get('type')}, {j.get('status')}): "
                                 f"{j.get('summary','')[:70]} — {len(j.get('subtasks', []))} "
                                 f"sub-task(s), {len(j.get('linked', []))} linked defect(s)/"
                                 f"concern(s), {j.get('comments', 0)} comment(s)").style(
                            f"font-size:{TYPOGRAPHY['size_xs']}")
                if show_notes and state["assumptions"]:
                    with ui.expansion(f"{len(state['assumptions'])} assumption(s) made",
                                      icon="lightbulb").classes("w-full"):
                        for a in state["assumptions"]:
                            ui.label(f"• {a}").style(f"font-size:{TYPOGRAPHY['size_xs']}")
                if show_notes and state["unclear"]:
                    # Ambiguity is reported, not resolved by guessing.
                    with ui.expansion(f"{len(state['unclear'])} thing(s) unclear",
                                      icon="help_outline").classes("w-full") \
                            .style(f"border:1px solid {COLORS['warning']}55"):
                        for u in state["unclear"]:
                            ui.label(f"• {u}").style(f"font-size:{TYPOGRAPHY['size_xs']}")

                ui.label("Pick the testcases to turn into steps").style(
                    f"font-size:{TYPOGRAPHY['size_sm']};"
                    f"font-weight:{TYPOGRAPHY['weight_medium']}")
                boxes: dict[str, ui.checkbox] = {}
                with ui.column().classes("w-full gap-0").style(
                        f"border:1px solid {COLORS['border']}; border-radius:6px;"
                        f"max-height:22rem; overflow-y:auto"):
                    for tc in state["testcases"]:
                        with ui.row().classes("w-full items-start no-wrap gap-2") \
                                .style(f"padding:5px 8px;"
                                       f"border-bottom:1px solid {COLORS['border']}"):
                            boxes[tc["id"]] = ui.checkbox(value=True).props("dense")
                            kind = (tc.get("classification") or "")[:1].upper()
                            ui.label(kind if kind in ("P", "N") else "·").style(
                                f"font-size:{TYPOGRAPHY['size_xs']}; font-weight:700;"
                                f"color:{COLORS['success'] if kind == 'P' else COLORS['warning']};"
                                f"width:1rem; flex:none; margin-top:2px").tooltip(
                                "Positive" if kind == "P" else "Negative" if kind == "N" else "")
                            with ui.column().classes("gap-0").style("min-width:0; flex:1 1 auto"):
                                ui.label(tc["id"]).style(
                                    f"font-family:{TYPOGRAPHY['mono']};"
                                    f"font-size:{TYPOGRAPHY['size_xs']}; font-weight:600;"
                                    f"overflow:hidden; text-overflow:ellipsis; white-space:nowrap")
                                ui.label(tc.get("title", "")).style(
                                    f"font-size:{TYPOGRAPHY['size_xs']};"
                                    f"color:{COLORS['text_muted']}; white-space:normal")
                            ui.label(f"{tc.get('steps', 0)} steps").style(
                                f"font-size:{TYPOGRAPHY['size_xs']}; flex:none;"
                                f"color:{COLORS['text_muted']}; margin-top:2px")

                # Each drafted testcase becomes its OWN test case in the list
                # (that is what a testcase is); the alternative merges the
                # picked ones into a single flow, for people who want one file.
                one_each = ui.switch("One test case per drafted case (recommended)", value=True)
                flow_name = ui.input("Save as (only when merging into one)",
                                     placeholder="ask_more_photos") \
                    .props("outlined dense").classes("w-full")
                flow_name.bind_visibility_from(one_each, "value", backward=lambda v: not v)

                def _to_steps(g: dict) -> tuple[list[str], dict]:
                    steps, meta, n = [], {}, 0
                    for s in g["steps"]:
                        if not s.get("emits"):
                            continue          # non-emitting rows carry no statement
                        n += 1
                        steps.append(s["statement"])
                        meta[n] = {"action": s.get("action", ""),
                                   "locator": s.get("locator", ""),
                                   "status": s.get("status", ""),
                                   "note": s.get("note", "")}
                    return steps, meta

                async def do_generate() -> None:
                    chosen = [k for k, b in boxes.items() if b.value]
                    if not chosen:
                        ui.notify("Pick at least one testcase", type="warning")
                        return
                    where = picker.require()
                    if where is None:
                        return
                    gen_btn.set_enabled(False)
                    try:
                        if one_each.value:
                            saved, all_steps, first = [], [], None
                            for tc_id in chosen:
                                name = tc_id.lower()
                                try:
                                    g = await api.generate(state["source_id"], [tc_id], platform,
                                                           flow_name=name, persist=False)
                                    steps, meta = _to_steps(g)
                                    if not steps:
                                        continue
                                    await api.save_project(name, steps, platform)
                                    saved.append(name); all_steps += steps
                                    first = first or (name, steps, meta)
                                except api.ApiError as err:
                                    ui.notify(f"{tc_id}: {err.detail}", type="warning")
                            if not saved:
                                ui.notify("Nothing could be generated", type="negative")
                                return
                            ui.notify(f"Saved {len(saved)} test case(s): "
                                      f"{', '.join(saved[:4])}{'…' if len(saved) > 4 else ''}",
                                      type="positive", timeout=8000)
                            dialog.close()
                            await _collect_inputs(all_steps)
                            await on_created(first[0], first[1], first[2], where,
                                             also_saved=saved)
                            return
                        name = (flow_name.value or "").strip() or chosen[0].lower()
                        g = await api.generate(state["source_id"], chosen, platform,
                                               flow_name=name, persist=False)
                        steps, meta = _to_steps(g)
                        summary = ", ".join(f"{k}={v}" for k, v in g["summary"].items())
                        ui.notify(f"{len(steps)} steps generated — {summary}", type="positive")
                        dialog.close()
                        # The values the generated steps need are asked for NOW,
                        # once, with what the prompt/ticket supplied already typed
                        # in — not one by one at run time.
                        await _collect_inputs(steps)
                        await on_created(name, steps, meta, where)
                    except api.ApiError as err:
                        ui.notify(f"Generation failed: {err.detail}", type="negative")
                    finally:
                        gen_btn.set_enabled(True)

                async def _collect_inputs(steps: list[str]) -> None:
                    try:
                        from ai_flow_builder.emitter import run_parameters
                        needed = run_parameters([st for st in steps if not st.startswith("#")])
                    except Exception:  # noqa: BLE001
                        needed = []
                    if not needed:
                        return
                    try:
                        have = (await api.testdata("")).get("values", {}) or {}
                    except api.ApiError:
                        have = {}
                    found = state.get("values_found") or {}
                    why = {i.get("name"): i.get("why", "") for i in (state.get("inputs_needed") or [])
                           if isinstance(i, dict)}
                    done = asyncio.Event()
                    with ui.dialog().props("persistent") as ask, ui.card().style("width:44rem"):
                        ui.label("Values these steps need").style(
                            f"font-size:{TYPOGRAPHY['size_lg']};"
                            f"font-weight:{TYPOGRAPHY['weight_bold']}")
                        ui.label("Taken from the prompt / ticket where it stated them; "
                                 "saved under Test Data so every run and every case "
                                 "picks them up. Leave one blank to be asked at run time.") \
                            .style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
                        fields: dict[str, ui.input] = {}
                        for n in needed:
                            existing = have.get(n, {})
                            prefill = found.get(n, "") or (existing.get("value", "") if existing.get("defined") and not existing.get("is_secret") else "")
                            hint = why.get(n) or ("already in Test Data" if existing.get("defined") else "")
                            fields[n] = ui.input(n, value=prefill, placeholder=hint) \
                                .props("outlined dense").classes("w-full") \
                                .style(f"font-family:{TYPOGRAPHY['mono']}")

                        async def save_all() -> None:
                            saved = 0
                            for n, box in fields.items():
                                v = (box.value or "").strip()
                                if not v or (have.get(n, {}).get("value") == v):
                                    continue
                                try:
                                    await api.set_testdata(n, v, updating=n in have, force=True)
                                    saved += 1
                                except api.ApiError as e:
                                    ui.notify(f"{n}: {e.detail}", type="warning")
                            if saved:
                                ui.notify(f"Saved {saved} value(s) under Test Data", type="positive")
                            ask.close(); done.set()

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Skip for now", on_click=lambda: (ask.close(), done.set())).props("flat")
                            ui.button("Save values", icon="check", on_click=save_all).props("unelevated")
                    ask.open()
                    await done.wait()

                gen_btn = ui.button("Generate & save test cases", icon="bolt", on_click=do_generate) \
                    .props("unelevated").style(f"background:{COLORS['primary']}")

        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
    dialog.open()
    await picker.load()
