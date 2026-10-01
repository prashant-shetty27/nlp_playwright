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


def _commit_edit(on_edit, index: int, text: str) -> None:
    """The step dialog's save writes the file at once when the callback allows it."""
    try:
        on_edit(index, text, persist=True)
    except TypeError:                       # a caller without the persist flag
        on_edit(index, text)


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
             token_queue: list | None = None,
             group_steps: dict[str, list[str]] | None = None,
             on_edit_group: Callable[[str], None] | None = None) -> ui.element:
    """
    ``token_queue``: when given, the step's token renderer is appended to it
    instead of being scheduled on its own timer, so the caller can draw a long
    test case in a few batches rather than one websocket update per row.
    """
    editing = {"on": False}
    # "Ignore result" (Testsigma's Ignore step result): shown as an amber chip;
    # the step text itself stays clean. Stored as a "[ignore 5s]" prefix.
    from execution import step_flags
    ignore, ignore_wait, body_text = step_flags.split(nlp_text)

    def _set_ignore(on: bool, wait: float | None = None) -> None:
        if on_edit:
            on_edit(index, step_flags.join(body_text, on, wait if wait is not None else ignore_wait or None))

    # A `call <group>` step is a different kind of row: it stands for a saved
    # sequence, so it is drawn with its own colour, a badge, and a fold that
    # shows the steps it expands to (read-only, capped in height so the rows
    # below it stay in view).
    group_name = _called_group(nlp_text)
    group_body = _group_lookup(group_name, group_steps) if group_name else None

    row = ui.row().classes("w-full items-center gap-2 px-2 py-1 no-wrap").style(
        f"border-bottom:1px solid {COLORS['border']};"
        + (f"background:{COLORS['primary']}0F; border-left:4px solid {COLORS['primary']};"
           if group_name else f"background:{COLORS['surface']};")
        + ("opacity:0.5;" if disabled else "")
    )
    # Drag to reorder. The up/down buttons stay: dragging is quicker for a long
    # move, buttons are surer for a single nudge, and a mis-drop that silently
    # reorders a test is exactly the kind of error nobody notices until a run
    # fails for an unrelated-looking reason.
    row.props(f'data-step="{index}"')   # lets a report link scroll to this step
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

        # A badge in the normal UI font, not the step's code font — in the same
        # font and colour it read as the first word of the step.
        ui.label(str(index)).style(
            f"min-width:1.7rem; text-align:center; flex:none; margin-right:6px;"
            f"background:{COLORS['border']}; color:{COLORS['text_muted']};"
            f"border-radius:10px; padding:1px 6px; font-family:{TYPOGRAPHY['family']};"
            f"font-size:{TYPOGRAPHY['size_xs']}; font-weight:{TYPOGRAPHY['weight_medium']};"
            f"line-height:1.4")

        if group_name:
            ui.label("step group").style(
                f"background:{COLORS['primary']}; color:white; border-radius:4px;"
                f"padding:1px 7px; font-size:{TYPOGRAPHY['size_xs']};"
                f"font-family:{TYPOGRAPHY['mono']}; white-space:nowrap")
        elif action:
            colour = action_color(action)
            ui.label(action).style(
                f"background:{colour}1A; color:{colour}; border-radius:4px;"
                f"padding:1px 7px; font-size:{TYPOGRAPHY['size_xs']};"
                f"font-family:{TYPOGRAPHY['mono']}; white-space:nowrap")

        if ignore and not group_name:
            amber = COLORS.get("warning", "#D97706")
            chip = ui.button(f"Ignore result · {ignore_wait:g}s", icon="warning_amber") \
                .props("dense unelevated no-caps size=sm") \
                .style(f"background:{amber}1F; color:{amber}; border-radius:4px; flex:none;"
                       f"font-size:{TYPOGRAPHY['size_xs']}; padding:0 6px") \
                .tooltip("If this step fails, the test carries on and the step shows amber "
                         "'Ignored'. Click to change the wait or turn it off.")
            with chip:
                with ui.menu():
                    ui.label("Wait for this step at most").style(
                        f"padding:6px 14px 2px; font-size:{TYPOGRAPHY['size_xs']};"
                        f"color:{COLORS['text_muted']}")
                    for w in (3, 5, 10, 15, 30):
                        ui.menu_item(f"{w} seconds" + ("  ✓" if abs(w - ignore_wait) < 1e-9 else ""),
                                     on_click=lambda w=w: _set_ignore(True, float(w)))
                    ui.separator()
                    ui.menu_item("Turn off — a failure fails the test",
                                 on_click=lambda: _set_ignore(False))

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
                if group_name:
                    # Read-only here on purpose: the group's name and steps
                    # are edited on the Step Groups page (the edit icon on this
                    # row goes there), so a `call` can never drift from the
                    # group it points at.
                    with ui.row().classes("items-center gap-1 no-wrap"):
                        ui.label("call").style(
                            f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_sm']};"
                            f"color:{COLORS['text']}")
                        ui.label(group_name).style(
                            f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_sm']};"
                            f"font-weight:{TYPOGRAPHY['weight_bold']}; color:{COLORS['primary']}")
                elif on_edit:
                    from ui.components.token_step import TokenStep

                    tok = TokenStep(body_text, platform=platform,
                                    on_change=lambda new: on_edit(
                                        index, step_flags.join(new, ignore, ignore_wait or None)),
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

        if group_name:
            n_steps = len(group_body) if group_body is not None else 0
            ui.label(f"{n_steps} steps" if group_body is not None else "not found").style(
                f"color:{COLORS['danger'] if group_body is None else COLORS['text_muted']};"
                f"font-size:{TYPOGRAPHY['size_xs']}; white-space:nowrap")
            fold = ui.button(icon="expand_more").props("flat dense size=xs") \
                .tooltip("Show the steps this group runs")
            if on_edit_group:
                ui.button(icon="edit_note").props("flat dense size=xs") \
                    .on("click", lambda g=group_name: on_edit_group(g)) \
                    .tooltip("Edit this step group (opens Step Groups, comes back here)")

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

        async def start_edit_form() -> None:
            """Pencil: the form when the step type has one, else the text box."""
            if editing["on"] or not on_edit:
                return
            from ui.components.step_form_dialog import open_step_form
            opened = await open_step_form(
                nlp_text, platform or "website", index,
                on_save=lambda text: _commit_edit(on_edit, index, (text or "").strip()))
            if not opened:
                start_edit()

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
                        def _save_line() -> None:
                            # An emptied box is not a change to keep: the row
                            # was left blank and looked deleted. Say so and
                            # restore the step instead.
                            if not (editor.input.value or "").strip():
                                ui.notify("A step cannot be empty — restored the "
                                          "previous text (use the bin icon to remove a step)",
                                          type="warning")
                                cancel()
                                return
                            editor._submit()

                        ui.button(icon="check", on_click=_save_line) \
                            .props("flat dense size=sm color=positive") \
                            .tooltip("Save this step (Enter) — Esc cancels")

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

        if on_edit and not group_name and not ignore:
            ui.button(icon="warning_amber").props("flat dense size=xs") \
                .style(f"color:{COLORS['text_muted']}") \
                .on("click", lambda: _set_ignore(True, step_flags.DEFAULT_WAIT_S)) \
                .tooltip("Ignore result — if this step fails, carry on (like Testsigma's "
                         "'Ignore step result'). For popups that may not appear.")
        if on_edit and not group_name:
            ui.button(icon="edit").props("flat dense size=xs") \
                .on("click", lambda: start_edit_form()) \
                .tooltip("Edit this step")
        if on_delete:
            ui.button(icon="delete_outline").props("flat dense size=xs color=negative") \
                .on("click", lambda: on_delete(index)).tooltip("Remove this step")
    if group_name:
        # The expansion lives under the row, not inside it, so the row keeps
        # its single-line layout; capped height with its own scrollbar so a
        # 30-step group does not push everything below it off the screen.
        body = ui.column().classes("w-full gap-0").style(
            f"background:{COLORS['primary']}08; border-left:4px solid {COLORS['primary']};"
            f"border-bottom:1px solid {COLORS['border']};"
            f"max-height:11rem; overflow-y:auto; padding:2px 0 4px 3.4rem;"
            # flex:none — inside the scrolling step list (a flex column) an
            # overflow:auto child is allowed to shrink to nothing, and did.
            f"flex:none; min-height:2rem")
        body.set_visibility(False)
        with body:
            if group_body is None:
                ui.label(f"No step group called '{group_name}' — check Step Groups.").style(
                    f"color:{COLORS['danger']}; font-size:{TYPOGRAPHY['size_xs']}; padding:4px 8px")
            for k, st in enumerate(group_body or [], 1):
                with ui.row().classes("items-center gap-2 no-wrap").style("padding:2px 8px"):
                    ui.label(f"{index}.{k}").style(
                        f"width:2.6rem; text-align:right; color:{COLORS['text_muted']};"
                        f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']}")
                    ui.label(st).style(
                        f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']};"
                        f"color:{COLORS['text']}; word-break:break-all")

        def _toggle_fold() -> None:
            body.set_visibility(not body.visible)
            fold.props(f"icon={'expand_less' if body.visible else 'expand_more'}")

        fold.on("click", _toggle_fold)

    return row


def _called_group(text: str) -> str:
    """The group name in a `call <name>` step, else ''."""
    import re

    t = (text or "").strip()
    m = re.match(r"^call\s+(.+?)\s*$", t, re.I)
    return m.group(1) if m else ""


def _group_lookup(name: str, groups: dict[str, list[str]] | None) -> list[str] | None:
    """Exact name first, then the same forgiving match the runner uses."""
    import re

    if not groups:
        return None
    if name in groups:
        return groups[name]

    def loose(n: str) -> str:
        n = re.sub(r"[\s\-\u2013\u2014_]+", "_", (n or "").strip().lower()).strip("_")
        return re.sub(r"^sg_", "", n)

    want = loose(name)
    hits = [k for k in groups if loose(k) == want]
    return groups[hits[0]] if len(hits) == 1 else None
