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

from typing import Callable

from nicegui import ui

from ui import api_client as api
from ui.theme import COLORS, TYPOGRAPHY


def new_test_case_dialog(platform: str, on_created: Callable) -> None:
    state: dict = {"source_id": None, "testcases": [], "chosen": set(),
                   "inputs": {}, "unclear": [], "assumptions": []}

    dialog = ui.dialog().props("persistent")
    with dialog, ui.card().style("width:56rem; max-width:95vw"):
        ui.label("New Test Case").style(
            f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
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
                    dialog.close()
                    await on_created(n, [], {})

                ui.button("Create empty", on_click=make_blank).props("unelevated")

            # ── spreadsheet ─────────────────────────────────────────────────
            with ui.tab_panel(t_sheet):
                ui.label("Upload a testcase workbook (.xlsx)").style(
                    f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")
                sheet_area = ui.column().classes("w-full gap-2")

                async def handle_upload(e) -> None:
                    try:
                        data = e.content.read()
                        res = await api.upload_source(e.name, data)
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
            with ui.tab_panel(t_prompt):
                ui.label("Describe what to test. Include any URL, number or code "
                         "you want used — anything you leave out will be asked for, "
                         "never invented.").style(
                    f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")
                prompt = ui.textarea(
                    placeholder="Test that a signed-out user tapping Ask More Photos "
                                "on https://... sees the mobile number popup…") \
                    .props("outlined dense rows=6").classes("w-full")
                with ui.row().classes("items-center gap-3"):
                    count = ui.number("Max testcases", value=5, min=1, max=25) \
                        .props("outlined dense").style("width:9rem")
                    spinner = ui.spinner(size="sm")
                    spinner.set_visibility(False)
                prompt_area = ui.column().classes("w-full gap-2")

                async def draft() -> None:
                    text = (prompt.value or "").strip()
                    spinner.set_visibility(True)
                    try:
                        res = await api.draft_from_prompt(
                            text, platform, int(count.value or 5))
                    except api.ApiError as err:
                        ui.notify(err.detail, type="negative")
                        return
                    finally:
                        spinner.set_visibility(False)
                    state["source_id"] = res["source_id"]
                    state["testcases"] = res.get("testcases", [])
                    state["unclear"] = res.get("unclear", [])
                    state["assumptions"] = res.get("assumptions", [])
                    ui.notify(f"{len(state['testcases'])} testcases drafted by "
                              f"{res['extra'].get('model','the model')}", type="positive")
                    _render_picker(prompt_area, show_notes=True)

                ui.button("Draft testcases", icon="auto_awesome", on_click=draft) \
                    .props("unelevated").style(f"background:{COLORS['accent']}")

        def _render_picker(container, show_notes: bool = False) -> None:
            container.clear()
            with container:
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
                        f"max-height:14rem; overflow-y:auto"):
                    for tc in state["testcases"]:
                        with ui.row().classes("w-full items-center gap-2") \
                                .style(f"padding:4px 8px;"
                                       f"border-bottom:1px solid {COLORS['border']}"):
                            boxes[tc["id"]] = ui.checkbox().props("dense")
                            ui.label(tc["id"]).style(
                                f"font-family:{TYPOGRAPHY['mono']};"
                                f"font-size:{TYPOGRAPHY['size_xs']}; width:9rem")
                            ui.label(tc.get("title", "")[:80]).style(
                                f"font-size:{TYPOGRAPHY['size_xs']};"
                                f"color:{COLORS['text_muted']}")
                            ui.label(f"{tc.get('steps', 0)} steps").classes("ml-auto") \
                                .style(f"font-size:{TYPOGRAPHY['size_xs']};"
                                       f"color:{COLORS['text_muted']}")

                flow_name = ui.input("Save as", placeholder="ask_more_photos") \
                    .props("outlined dense").classes("w-full")

                async def do_generate() -> None:
                    chosen = [k for k, b in boxes.items() if b.value]
                    if not chosen:
                        ui.notify("Pick at least one testcase", type="warning")
                        return
                    name = (flow_name.value or "").strip() or chosen[0].lower()
                    try:
                        g = await api.generate(state["source_id"], chosen, platform,
                                               flow_name=name, persist=False)
                    except api.ApiError as err:
                        ui.notify(f"Generation failed: {err.detail}", type="negative")
                        return
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
                    summary = ", ".join(f"{k}={v}" for k, v in g["summary"].items())
                    ui.notify(f"{len(steps)} steps generated — {summary}",
                              type="positive")
                    dialog.close()
                    await on_created(name, steps, meta)

                ui.button("Generate steps", icon="bolt", on_click=do_generate) \
                    .props("unelevated").style(f"background:{COLORS['primary']}")

        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
    dialog.open()
