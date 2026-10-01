"""
The pencil's edit dialog — Testsigma-style.

The first control is the ACTION: a dropdown of every step type the portal
knows, so the whole NLP can be changed up front ("Check an element is
visible" → "Click an element") and not only the element inside it. Picking
an action shows its fields — element pickers list this platform's saved
elements, a popup list has "+ Add popup", numbers are number boxes, a step
group picker lists the groups — and the step text is composed server-side
from the fields and checked against the parser before it is written back.

"Raw text" shows the composed line for people who prefer typing, and is
where a step whose type has no form (a rarely used command) is edited.
The dialog always opens: nothing about a step is out of reach from it.
"""
from __future__ import annotations

from typing import Callable

from nicegui import ui

from ui import api_client as api
from ui.theme import COLORS, TYPOGRAPHY

OTHER = "__other__"


async def open_step_form(step: str, platform: str, index: int,
                         on_save: Callable[[str], None]) -> bool:
    """Open the edit dialog for `step`. Always opens; returns True."""
    # "Ignore result" lives in this dialog (a switch + wait), not on every row.
    from execution import step_flags
    ign_on, ign_wait, step = step_flags.split(step)
    try:
        form = (await api.step_form(step) or {}).get("form")
    except api.ApiError:
        form = None
    try:
        forms = await api.step_forms()
    except api.ApiError:
        forms = []
    try:
        names = sorted((await api.locator_labels(platform)).keys())   # this platform only
    except api.ApiError:
        names = []
    try:
        groups = sorted(g.get("name", "") for g in await api.step_groups(platform))
    except api.ApiError:
        groups = []

    by_type = {f["type"]: f for f in forms}
    stype = form["type"] if form else OTHER
    values: dict = dict(form.get("values") or {}) if form else {}
    state = {"type": stype, "raw": step}
    controls: dict[str, ui.element] = {}
    closer_rows: list = []

    action_options = {f["type"]: f["title"] for f in forms}
    action_options[OTHER] = "Other — type the step"

    with ui.dialog() as dlg, ui.card().style("min-width:38rem; max-width:46rem"):
        ui.label(f"Step {index}").style(
            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
        with ui.row().classes("w-full items-center no-wrap gap-2"):
            action = ui.select(action_options, value=stype, label="Action", with_input=True) \
                .props("dense outlined use-input input-debounce=0").classes("flex-grow") \
                .tooltip("What this step does — change it here to rewrite the whole step")
            raw_toggle = ui.switch("Raw text").props("dense")

        form_box = ui.column().classes("w-full gap-2")
        raw_box = ui.column().classes("w-full gap-1")
        preview = ui.label().style(
            f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']};"
            f"color:{COLORS['text_muted']}; white-space:pre-wrap; word-break:break-word;"
            f"background:{COLORS['border']}33; border-radius:4px; padding:4px 8px; width:100%")
        err = ui.label().style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['danger']}")

        with raw_box:
            raw = ui.input("Step text", value=step).props("dense outlined") \
                .classes("w-full").style(f"font-family:{TYPOGRAPHY['mono']}")
            ui.label("Typed text is checked against the step grammar when you save.").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

        def collect() -> dict:
            out = dict(values)
            for key, ctl in controls.items():
                out[key] = ctl.value
            if closer_rows:
                out["closers"] = [r.value for r in closer_rows if r.value]
            return out

        async def recompose() -> str:
            err.set_text("")
            if state["type"] == OTHER:
                preview.set_text(raw.value or "")
                return raw.value or ""
            try:
                res = await api.step_compose(state["type"], collect())
                text = res.get("step", "")
                preview.set_text(text)
                return text
            except api.ApiError as e:
                detail = e.detail.get("message", "") if isinstance(e.detail, dict) else str(e.detail)
                err.set_text(detail)
                return ""

        def _on_change(_=None) -> None:
            ui.timer(0.01, recompose, once=True)

        def pick(label: str, options: list[str], value: str, help_: str = "") -> ui.select:
            opts = options if value in options or not value else [value] + options
            sel = ui.select(opts, value=value or None, label=label, with_input=True,
                            on_change=_on_change) \
                .props("dense outlined use-input input-debounce=0 new-value-mode=add-unique") \
                .classes("w-full")
            if help_:
                sel.tooltip(help_)
            return sel

        def build_fields() -> None:
            """Draw the fields for state['type'], carrying over matching values."""
            form_box.clear()
            controls.clear()
            closer_rows.clear()
            spec = by_type.get(state["type"])
            if not spec:
                return
            with form_box:
                if not spec["fields"]:
                    ui.label("This step takes no values.").style(
                        f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")
                for f in spec["fields"]:
                    key, kind = f["key"], f["kind"]
                    cur = values.get(key)
                    if kind == "locator":
                        controls[key] = pick(f["label"], names, cur or "", f.get("help", ""))
                    elif kind == "group":
                        controls[key] = pick(f["label"], groups, cur or "", f.get("help", ""))
                    elif kind == "locators":
                        ui.label(f["label"]).style(
                            f"font-size:{TYPOGRAPHY['size_sm']}; font-weight:500; margin-top:4px")
                        if f.get("help"):
                            ui.label(f["help"]).style(
                                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
                        list_box = ui.column().classes("w-full gap-1")

                        def add_closer(val: str = "", _box=list_box) -> None:
                            with _box:
                                with ui.row().classes("w-full items-center no-wrap gap-1") as row:
                                    sel = pick("Popup close button / element", names, val)
                                    closer_rows.append(sel)

                                    def remove(_=None, _row=row, _sel=sel) -> None:
                                        closer_rows.remove(_sel)
                                        _row.delete()
                                        _on_change()
                                    ui.button(icon="close", on_click=remove) \
                                        .props("flat dense size=sm").tooltip("Remove this popup")

                        for c in (cur or []):
                            add_closer(c)
                        ui.button("Add popup", icon="add", on_click=lambda: add_closer()) \
                            .props("flat dense no-caps").style(f"color:{COLORS['primary']}")
                    elif kind == "number":
                        controls[key] = ui.number(
                            f["label"], value=cur if cur not in (None, "") else f.get("default"),
                            on_change=_on_change).props("dense outlined").classes("w-full")
                    elif kind == "choice":
                        controls[key] = ui.select(
                            f["choices"], value=cur if cur in f["choices"] else f.get("default"),
                            label=f["label"], on_change=_on_change) \
                            .props("dense outlined").classes("w-full")
                    else:   # text / url / variable
                        controls[key] = ui.input(f["label"], value=cur or "",
                                                 on_change=_on_change) \
                            .props("dense outlined").classes("w-full")
                    if f.get("help") and key in controls and kind != "locator":
                        controls[key].tooltip(f["help"])

        def show_mode() -> None:
            raw_mode = bool(raw_toggle.value) or state["type"] == OTHER
            form_box.set_visibility(not raw_mode)
            raw_box.set_visibility(raw_mode)
            preview.set_visibility(not raw_mode)

        async def action_changed(e) -> None:
            new_type = e.value
            if not new_type or new_type == state["type"]:
                return
            # Values the user already filled carry over where the keys match.
            values.update(collect())
            state["type"] = new_type
            build_fields()
            show_mode()
            if new_type == OTHER:
                raw.set_value(preview.text or step)
            await recompose()
        action.on_value_change(action_changed)

        async def toggled(e) -> None:
            if e.value and state["type"] != OTHER:
                text = await recompose()
                if text:
                    raw.set_value(text)
            show_mode()
        raw_toggle.on_value_change(toggled)

        ui.separator().style("margin-top:4px")
        with ui.row().classes("w-full items-center no-wrap gap-3"):
            ign = ui.switch("Ignore result", value=ign_on).props("dense color=warning")
            ign_sel = ui.select({3.0: "wait 3 s", 5.0: "wait 5 s", 10.0: "wait 10 s",
                                 15.0: "wait 15 s", 30.0: "wait 30 s"},
                                value=ign_wait if ign_on and ign_wait in (3.0, 5.0, 10.0, 15.0, 30.0)
                                else step_flags.DEFAULT_WAIT_S) \
                .props("dense outlined options-dense").style("width:8rem")
            ign_sel.bind_visibility_from(ign, "value")
        ui.label("If this step fails, the test carries on — the step shows amber "
                 "'Ignored' (Testsigma's 'Ignore step result'). For popups that may not "
                 "appear; the wait keeps a missing popup from costing 15 s.").style(
            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

        with ui.row().classes("w-full justify-end gap-2").style("margin-top:6px"):
            ui.button("Cancel", on_click=dlg.close).props("flat")

            async def save() -> None:
                if raw_toggle.value or state["type"] == OTHER:
                    text = (raw.value or "").strip()
                    if not text:
                        err.set_text("The step cannot be empty")
                        return
                    try:
                        await api.parse_step(text)
                    except api.ApiError as e:
                        err.set_text(f"Not a step the runner understands: {e.detail}")
                        return
                else:
                    text = await recompose()
                    if not text:
                        return
                dlg.close()
                on_save(step_flags.join(text, bool(ign.value), float(ign_sel.value or 5)))
            ui.button("Save step", icon="done", on_click=save).props("unelevated")

    build_fields()
    show_mode()
    preview.set_text(step)
    dlg.open()
    return True
