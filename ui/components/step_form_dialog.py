"""
The pencil's form view: edit a step by its parts, not by its grammar.

Opened for any step whose type has a form (nlp/step_forms.py). Each part is
its own field — element pickers list the platform's saved elements, a
popup list has "+ add popup", numbers are number boxes — and the step text
is composed server-side from the fields and checked against the parser
before it is written back. A "Raw text" toggle shows the composed line and
lets people who prefer typing edit it directly; the two stay in sync.

Steps with no form (a step group call, an unrecognised line) do not come
here; the row keeps its inline text editor.
"""
from __future__ import annotations

from typing import Callable

from nicegui import ui

from ui import api_client as api
from ui.theme import COLORS, TYPOGRAPHY


async def open_step_form(step: str, platform: str, index: int,
                         on_save: Callable[[str], None]) -> bool:
    """Open the form for `step`. Returns False (without opening) when it has none."""
    try:
        form = (await api.step_form(step) or {}).get("form")
    except api.ApiError:
        form = None
    if not form:
        return False
    try:
        names = sorted((await api.locator_labels(platform)).keys())   # this platform only
    except api.ApiError:
        names = []

    values: dict = dict(form.get("values") or {})
    stype = form["type"]
    controls: dict[str, ui.element] = {}
    closer_rows: list = []

    with ui.dialog() as dlg, ui.card().style("min-width:36rem; max-width:44rem"):
        with ui.row().classes("w-full items-center no-wrap"):
            ui.label(f"Step {index}: {form['title']}").style(
                f"font-size:{TYPOGRAPHY['size_md']}; font-weight:600")
            ui.space()
            raw_toggle = ui.switch("Raw text").props("dense")

        form_box = ui.column().classes("w-full gap-2")
        raw_box = ui.column().classes("w-full gap-1")
        raw_box.set_visibility(False)

        preview = ui.label().style(
            f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']};"
            f"color:{COLORS['text_muted']}; white-space:pre-wrap; word-break:break-word;"
            f"background:{COLORS['border']}33; border-radius:4px; padding:4px 8px; width:100%")
        err = ui.label().style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['danger']}")

        def collect() -> dict:
            out = dict(values)
            for key, ctl in controls.items():
                out[key] = ctl.value
            out["closers"] = [r.value for r in closer_rows if r.value]
            return out

        async def recompose() -> str:
            err.set_text("")
            try:
                res = await api.step_compose(stype, collect())
                text = res.get("step", "")
                preview.set_text(text)
                return text
            except api.ApiError as e:
                err.set_text(str(e.detail))
                return ""

        def _on_change(_=None) -> None:
            ui.timer(0.01, recompose, once=True)

        def locator_select(label: str, value: str, help_: str = "") -> ui.select:
            opts = names if value in names or not value else [value] + names
            sel = ui.select(opts, value=value or None, label=label, with_input=True,
                            on_change=_on_change) \
                .props("dense outlined use-input input-debounce=0 new-value-mode=add-unique") \
                .classes("w-full")
            if help_:
                sel.tooltip(help_)
            return sel

        with form_box:
            for f in form["fields"]:
                key, kind = f["key"], f["kind"]
                cur = values.get(key)
                if kind == "locator":
                    controls[key] = locator_select(f["label"], cur or "", f.get("help", ""))
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
                                sel = locator_select("Popup close button / element", val)
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
                    if f.get("help"):
                        controls[key].tooltip(f["help"])
                elif kind == "choice":
                    controls[key] = ui.select(
                        f["choices"], value=cur or f.get("default"), label=f["label"],
                        on_change=_on_change).props("dense outlined").classes("w-full")
                    if f.get("help"):
                        controls[key].tooltip(f["help"])
                else:   # text / url
                    controls[key] = ui.input(f["label"], value=cur or "",
                                             on_change=_on_change) \
                        .props("dense outlined").classes("w-full")
                    if f.get("help"):
                        controls[key].tooltip(f["help"])

        with raw_box:
            raw = ui.input("Step text", value=step).props("dense outlined") \
                .classes("w-full").style(f"font-family:{TYPOGRAPHY['mono']}")
            ui.label("Typing here replaces the form's values when you save.").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

        async def toggled(e) -> None:
            on = bool(e.value)
            if on:
                text = await recompose()
                if text:
                    raw.set_value(text)
            form_box.set_visibility(not on)
            raw_box.set_visibility(on)
            preview.set_visibility(not on)
        raw_toggle.on_value_change(toggled)

        with ui.row().classes("w-full justify-end gap-2").style("margin-top:6px"):
            ui.button("Cancel", on_click=dlg.close).props("flat")

            async def save() -> None:
                if raw_toggle.value:
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
                on_save(text)
            ui.button("Save step", icon="done", on_click=save).props("unelevated")

    preview.set_text(step)
    dlg.open()
    return True
