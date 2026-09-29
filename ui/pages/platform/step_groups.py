"""
ui/pages/platform/step_groups.py — named, reusable sequences of steps.

A group is written once and called by name: `call jd_open_and_dismiss_login`
expands inline at run time, with circular-call protection. The storage, the
validation and the expansion already existed; this screen is what makes them
reachable, alongside the (sg) suggestions in the step editor.

Groups are platform-scoped, because their steps are: a group that taps an
Android element cannot run on the website, and offering it there would suggest
a step that is guaranteed to fail.
"""
from __future__ import annotations

from nicegui import ui

from ui import api_client as api
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.theme import COLORS, TYPOGRAPHY


async def render(platform: str = "website", edit: str = "", back: str = "") -> None:
    """``edit``: open that group's editor at once; ``back``: where Save returns to."""
    try:
        platforms = await api.platforms()
    except api.ApiError:
        platforms = []
    sidebar(active="/step-groups", platforms=platforms)
    topbar(["Manage", "Step Groups"], platforms=platforms, platform=platform,
           on_platform_change=lambda p: ui.navigate.to(f"/step-groups?platform={p}"))

    with ui.column().classes("w-full gap-3 p-4"):
        try:
            groups = await api.step_groups(platform)
        except api.ApiError as e:
            ui.label(f"Could not read step groups: {e.detail[:140]}").style(
                f"color:{COLORS['danger']}")
            return

        ui.label("Select steps in a test case and choose “Save as step group” to "
                 "make one. Type its name while writing a step and it appears "
                 "tagged (sg).").style(
            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']};"
            f"max-width:54rem")

        # Only a path on this portal: ?back=https://elsewhere would send a
        # signed-in user off-site.
        if back and not (back.startswith("/") and not back.startswith("//")):
            back = ""
        if back:
            with ui.row().classes("items-center gap-2"):
                ui.button("Back to the test case", icon="arrow_back",
                          on_click=lambda: ui.navigate.to(back)).props("flat dense")

        if not groups:
            ui.label("No step groups on this platform yet.").style(
                f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_sm']}")
            return

        for g in groups:
            with ui.expansion(value=bool(edit and g["name"] == edit)).classes("w-full").style(
                    f"border:1px solid {COLORS['border']}; border-radius:6px") as exp:
                with exp.add_slot("header"):
                    with ui.row().classes("w-full items-center gap-3 no-wrap"):
                        ui.label("sg").style(
                            f"background:{COLORS['primary']}1A;"
                            f"color:{COLORS['primary']}; border-radius:4px;"
                            f"padding:1px 7px; font-size:{TYPOGRAPHY['size_xs']};"
                            f"font-family:{TYPOGRAPHY['mono']}")
                        ui.label(g["name"]).style(
                            f"font-family:{TYPOGRAPHY['mono']};"
                            f"font-size:{TYPOGRAPHY['size_sm']}")
                        ui.label(f"{g['step_count']} steps").style(
                            f"color:{COLORS['text_muted']};"
                            f"font-size:{TYPOGRAPHY['size_xs']}")
                        ui.space()
                        ui.label("call " + g["name"]).style(
                            f"font-family:{TYPOGRAPHY['mono']};"
                            f"font-size:{TYPOGRAPHY['size_xs']};"
                            f"color:{COLORS['text_muted']}")
                with ui.column().classes("w-full gap-0"):
                    for i, st in enumerate(g["steps"], 1):
                        with ui.row().classes("w-full items-center gap-2").style(
                                f"padding:3px 10px;"
                                f"border-top:1px solid {COLORS['border']}"):
                            ui.label(str(i)).style(
                                f"width:1.4rem; text-align:right;"
                                f"color:{COLORS['text_muted']};"
                                f"font-size:{TYPOGRAPHY['size_xs']}")
                            ui.label(st).style(
                                f"font-family:{TYPOGRAPHY['mono']};"
                                f"font-size:{TYPOGRAPHY['size_sm']}")
                    with ui.row().classes("w-full justify-end gap-1"):
                        ui.button("Edit steps", icon="edit",
                                  on_click=lambda gg=g: _edit_dialog(gg, back)) \
                            .props("flat dense")
                        ui.button("Rename", icon="drive_file_rename_outline",
                                  on_click=lambda n=g["name"]: _rename_dialog(n)) \
                            .props("flat dense")
                        ui.button("Clone", icon="content_copy",
                                  on_click=lambda n=g["name"]: _clone_dialog(n)) \
                            .props("flat dense").tooltip(
                                "Copy under a new name — repeated steps are dropped")
                        ui.button("Delete group", icon="delete_outline",
                                  on_click=lambda n=g["name"]: _confirm_delete(n)) \
                            .props("flat dense color=negative")


        if edit:
            target = next((g for g in groups if g["name"] == edit), None)
            if target is not None:
                ui.timer(0.2, lambda: _edit_dialog(target, back), once=True)
            else:
                ui.notify(f"No step group called '{edit}' on this platform", type="warning")


def _clone_dialog(name: str) -> None:
    """Clone under a name you choose (blank = <name>_copy); duplicates are refused."""
    dialog = ui.dialog().props("persistent")
    with dialog, ui.card().style("width:30rem"):
        ui.label(f"Clone {name}").style(
            f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
        new = ui.input("New name", placeholder=f"{name}_copy").props("outlined dense").classes("w-full")
        note = ui.label().style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['warning']}")

        async def go() -> None:
            try:
                res = await api.clone_step_group(name, (new.value or "").strip())
            except api.ApiError as e:
                note.set_text(e.detail[:200])
                return
            dialog.close()
            extra = (f" — {res['removed_duplicates']} repeated step(s) dropped"
                     if res.get("removed_duplicates") else "")
            ui.notify(f"Cloned as {res['to']}{extra}. It has the same steps as {name} "
                      f"until you edit it.", type="positive", timeout=8000)
            ui.navigate.reload()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Clone", on_click=go).props("unelevated")
    dialog.open()


async def _clone(name: str) -> None:
    """Copy a group. The name is chosen automatically; duplicate steps are dropped."""
    try:
        res = await api.clone_step_group(name)
    except api.ApiError as e:
        ui.notify(e.detail, type="negative")
        return
    extra = (f" — {res['removed_duplicates']} repeated step(s) dropped"
             if res.get("removed_duplicates") else "")
    ui.notify(f"Cloned as {res['to']}{extra}", type="positive", timeout=6000)
    ui.navigate.reload()


def _edit_dialog(group: dict, back: str = "") -> None:
    """
    Edit a group's steps as text — one per line.

    A group is expanded inline wherever it is called, so this is edited as a
    block rather than step-by-step: seeing all of it at once is what tells you
    whether the sequence still makes sense.
    """
    dialog = ui.dialog().props("persistent")
    with dialog, ui.card().style("width:40rem"):
        ui.label(f"Edit {group['name']}").style(
            f"font-size:{TYPOGRAPHY['size_lg']};"
            f"font-weight:{TYPOGRAPHY['weight_bold']}")
        ui.label("One step per line. Repeated lines are removed on save — a "
                 "duplicate here repeats in every test that calls this group.").style(
            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
        box = ui.textarea(value="\n".join(group.get("steps", []))) \
            .props("outlined dense rows=10").classes("w-full") \
            .style(f"font-family:{TYPOGRAPHY['mono']}")
        note = ui.label().style(
            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['warning']}")

        async def save() -> None:
            steps = [l.strip() for l in (box.value or "").splitlines() if l.strip()]
            try:
                res = await api.edit_step_group(group["name"], steps)
            except api.ApiError as e:
                note.set_text(e.detail[:160])
                return
            dialog.close()
            extra = (f" ({res['removed_duplicates']} duplicate removed)"
                     if res.get("removed_duplicates") else "")
            ui.notify(f"Updated {res['name']}{extra} — every test case that calls it "
                      f"uses the new steps", type="positive", timeout=6000)
            if back:
                ui.navigate.to(back)     # straight back to the test case
            else:
                ui.navigate.reload()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save steps", on_click=save).props("unelevated")
    dialog.open()


def _rename_dialog(name: str) -> None:
    dialog = ui.dialog().props("persistent")
    with dialog, ui.card().style("width:28rem"):
        ui.label(f"Rename {name}").style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
        ui.label("Lowercase letters, digits and underscores.").style(
            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
        box = ui.input("New name", value=name).props("outlined dense").classes("w-full")
        note = ui.label().style(
            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['warning']}")
        ui.label(f"Any test case containing “call {name}” keeps the OLD name and "
                 f"will need updating.").style(
            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['warning']}")

        async def go() -> None:
            try:
                res = await api.rename_step_group(name, (box.value or "").strip())
            except api.ApiError as e:
                note.set_text(e.detail[:160])
                return
            dialog.close()
            flows = res.get("flows_updated") or []
            ui.notify(f"Renamed to {box.value} — `call` updated in {len(flows)} test case(s)"
                      + (f": {', '.join(flows[:5])}" if flows else ""),
                      type="positive", timeout=8000)
            ui.navigate.reload()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Rename", on_click=go).props("unelevated")
    dialog.open()


def _confirm_delete(name: str) -> None:
    dialog = ui.dialog().props("persistent")
    with dialog, ui.card().style("width:28rem"):
        ui.label(f"Delete step group {name}?").style(
            f"font-weight:{TYPOGRAPHY['weight_bold']}")
        ui.label(f"Any test case containing “call {name}” will fail until that "
                 f"step is replaced.").style(
            f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")

        async def go() -> None:
            try:
                await api.delete_step_group(name)
            except api.ApiError as e:
                ui.notify(e.detail, type="negative")
                return
            dialog.close()
            ui.notify(f"Deleted {name}", type="positive")
            ui.navigate.reload()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Delete", on_click=go).props("unelevated color=negative")
    dialog.open()
