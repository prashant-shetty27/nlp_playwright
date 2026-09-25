"""
ui/components/step_row.py — NLP Step Row

A single row in a flow's step list. Displays one NLP command with action type
badge, target, and inline edit capability.

Visual layout (horizontal):
    [drag handle] [step #] [action badge] [NLP text] [element name]
    [add above / add below] [edit] [delete]

Props:
    index:      int — step number (1-based)
    nlp_text:   str — raw NLP command string
    action:     str — parsed action type
    target:     str — element name or literal value
    on_edit:    callable(index, new_text) | None
    on_delete:  callable(index) | None
    on_move:    callable(from_index, to_index) | None  — reorder
    on_add:     callable(index, "above"|"below") | None — insert beside this step
    known_values: set[str] — ${variables} that already have a value AT this step;
                anything referenced but not in here is drawn as needing one
    status:     str — generation status, when the row came from /generate
    note:       str — why a non-SUPPORTED status was assigned
    selector:   str — the locator's resolved selector, shown on hover

Action badge colours come from ui/theme.ACTION_COLOR.

Divergence from the spec, deliberate: the spec described HTML5 drag-and-drop.
Reordering uses explicit up/down controls instead — drag targets are unreliable
inside a scrolling list, and a mis-drop silently reorders a test, which is the
kind of error nobody notices until a run fails for an unrelated-looking reason.

Used in: pages/platform/*/test_cases.py
"""
from __future__ import annotations

from typing import Callable

from nicegui import ui

from ui.components.status_chip import mapping_chip


async def _handle_drop(target_index: int, on_drop: Callable[[int, int], None]) -> None:
    """Move the dragged row to where it was released."""
    try:
        src = await ui.run_javascript("window.__dragStep", timeout=2.0)
    except Exception:  # noqa: BLE001 — a drop we cannot read is simply ignored
        return
    if isinstance(src, (int, float)) and int(src) != target_index:
        on_drop(int(src), target_index)
from ui.theme import COLORS, TYPOGRAPHY, action_color


def step_row(index: int, nlp_text: str, *, action: str = "", target: str = "",
             platform: str = "website",
             on_edit: Callable[[int, str], None] | None = None,
             on_delete: Callable[[int], None] | None = None,
             on_move: Callable[[int, int], None] | None = None,
             status: str = "", note: str = "", selector: str = "",
             total: int = 0, locators: dict[str, str] | None = None,
             locator_details: dict[str, dict[str, str]] | None = None,
             locators_elsewhere: dict[str, str] | None = None,
             variables: list[str] | None = None,
             known_values: set[str] | None = None,
             selectable: bool = False, selected: bool = False,
             on_select: Callable[[int, bool], None] | None = None,
             disabled: bool = False,
             on_toggle_enabled: Callable[[int], None] | None = None,
             on_add: Callable[[int, str], None] | None = None,
             on_drop: Callable[[int, int], None] | None = None,
             token_queue: list | None = None) -> ui.element:
    """
    ``token_queue``: when given, the step's token renderer is appended to it
    instead of being scheduled on its own timer, so the caller can draw a long
    test case in a few batches rather than one websocket update per row.
    """
    editing = {"on": False}

    row = ui.row().classes("w-full items-center gap-2 px-2 py-1 no-wrap").style(
        f"border-bottom:1px solid {COLORS['border']};"
        f"background:{COLORS['surface']};"
        + ("opacity:0.5;" if disabled else "")
    )
    # Drag to reorder. The up/down buttons stay: dragging is quicker for a long
    # move, buttons are surer for a single nudge, and a mis-drop that silently
    # reorders a test is exactly the kind of error nobody notices until a run
    # fails for an unrelated-looking reason.
    if on_drop:
        row.props(f'draggable="true" data-idx="{index}"')
        row.on("dragstart", lambda e, i=index: ui.run_javascript(
            f"window.__dragStep={i}"))
        row.on("dragover.prevent", lambda e: None)
        row.on("drop", lambda e, i=index: _handle_drop(i, on_drop))
    with row:
        if selectable:
            cb = ui.checkbox(value=selected).props("dense")
            if on_select:
                cb.on_value_change(lambda e, i=index: on_select(i, bool(e.value)))
        if on_toggle_enabled:
            ui.button(icon="toggle_off" if disabled else "toggle_on") \
                .props("flat dense size=xs") \
                .on("click", lambda i=index: on_toggle_enabled(i)) \
                .tooltip("Skip this step on the next run" if not disabled
                         else "Include this step again")
        # Reorder controls, disabled at the ends so the affordance matches reality.
        with ui.column().classes("gap-0"):
            up = ui.button(icon="keyboard_arrow_up").props("flat dense size=xs")
            down = ui.button(icon="keyboard_arrow_down").props("flat dense size=xs")
            if on_move and index > 1:
                up.on("click", lambda: on_move(index, index - 1))
            else:
                up.props("disable")
            if on_move and total and index < total:
                down.on("click", lambda: on_move(index, index + 1))
            else:
                down.props("disable")

        ui.label(str(index)).style(
            f"width:1.6rem; text-align:right; color:{COLORS['text_muted']};"
            f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_sm']}")

        if action:
            colour = action_color(action)
            ui.label(action).style(
                f"background:{colour}1A; color:{colour}; border-radius:4px;"
                f"padding:1px 7px; font-size:{TYPOGRAPHY['size_xs']};"
                f"font-family:{TYPOGRAPHY['mono']}; white-space:nowrap")

        # min-width:0 lets this cell shrink below its content, which is what
        # allows a long step to wrap INSIDE the row. Without it a flex child
        # refuses to go under its own min-content width, and the row grew until
        # the delete button was pushed onto a line of its own.
        text_holder = ui.element("div").classes("flex-grow").style("min-width:0")
        with text_holder:
            label = ui.element("div")
            with label:
                # Values inside the step are individually clickable — see
                # ui/components/token_step.py. The pencil below still edits the
                # whole line; this is for changing one element or one number
                # without retyping the rest.
                if on_edit:
                    from ui.components.token_step import TokenStep

                    tok = TokenStep(nlp_text, platform=platform,
                                    on_change=lambda new: on_edit(index, new),
                                    locators=locators or {},
                                    locator_details=locator_details or {},
                                    locators_elsewhere=locators_elsewhere or {},
                                    variables=variables or [],
                                    known_values=known_values or set())

                    if token_queue is not None:
                        token_queue.append(tok)
                    else:
                        async def _draw() -> None:
                            await tok.render()

                        ui.timer(0.01, _draw, once=True)
                else:
                    ui.label(nlp_text).style(
                        f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_sm']};"
                        f"color:{COLORS['text']}; word-break:break-all")

        if target:
            tgt = ui.label(target).style(
                f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_xs']};"
                f"font-family:{TYPOGRAPHY['mono']}; white-space:nowrap")
            # The resolved selector is the thing you need when a step fails, so it
            # is one hover away rather than in another screen.
            if selector:
                tgt.tooltip(f"{target} → {selector}")

        if status:
            mapping_chip(status, note=note)

        def start_edit() -> None:
            if editing["on"] or not on_edit:
                return
            editing["on"] = True
            label.set_visibility(False)
            with text_holder:
                # The same autocomplete the "Add a step" field uses. Editing an
                # existing step offered no suggestions at all, so the one moment
                # you most need to be reminded of the syntax — changing a step
                # that already exists — was the one moment you were on your own.
                from ui.components.nlp_input import NlpInput

                def commit(text: str) -> None:
                    on_edit(index, (text or "").strip())

                def cancel(_=None) -> None:
                    """Put the step back exactly as it was. Changes nothing."""
                    if not editing["on"]:
                        return
                    editing["on"] = False
                    text_holder.clear()
                    label.set_visibility(True)

                # No "clear" X on this box: with the step text loaded, that X
                # emptied it in one click and left a blank box with no way
                # back — it read as "my step got deleted".
                with ui.row().classes("w-full items-start no-wrap gap-1"):
                    editor = NlpInput(platform or "website", commit,
                                      initial_value=nlp_text,
                                      placeholder="Edit this step",
                                      clearable=False)
                    with ui.row().classes("no-wrap gap-0").style("margin-top:2px"):
                        ui.button(icon="check", on_click=lambda: editor._submit()) \
                            .props("flat dense size=sm color=positive") \
                            .tooltip("Save this step (Enter)")
                        ui.button(icon="close", on_click=cancel) \
                            .props("flat dense size=sm") \
                            .tooltip("Cancel — keep the step as it was (Esc)")

                async def _load() -> None:
                    await editor.load()

                ui.timer(0.01, _load, once=True)
                editor.input.on("keydown.escape", cancel)

        # Insert either side of this step. Stacked the same way as the reorder
        # arrows, so the upper button reads as "above" and the lower as "below"
        # without either needing a label. Adding a step used to be possible only
        # at the very end, so putting one in the middle of a written test meant
        # appending it and then walking it up the list one press at a time.
        if on_add:
            with ui.column().classes("gap-0"):
                narrow = f"color:{COLORS['primary']}; min-width:1.2rem; padding:0"
                ui.button(icon="add").props("flat dense size=xs").style(narrow) \
                    .on("click", lambda i=index: on_add(i, "above")) \
                    .tooltip("Insert a new step ABOVE this one")
                ui.button(icon="add").props("flat dense size=xs").style(narrow) \
                    .on("click", lambda i=index: on_add(i, "below")) \
                    .tooltip("Insert a new step BELOW this one")

        if on_edit:
            ui.button(icon="edit").props("flat dense size=xs").on("click", start_edit) \
                .tooltip("Edit this step")
        if on_delete:
            ui.button(icon="delete_outline").props("flat dense size=xs color=negative") \
                .on("click", lambda: on_delete(index)).tooltip("Remove this step")
    return row
