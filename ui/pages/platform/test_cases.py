"""
ui/pages/platform/test_cases.py — Test Cases  (route: /platform/{platform})

List and editor for NLP flow test cases.

Layout (as specified):
    Left panel  — test case list, search, [+ New Test Case]
    Right panel — NLP step editor: ordered steps, inline edit, drag-to-reorder,
                  [+ Add Step] with autocomplete, [Run], [Save]

Two deliberate divergences from the spec:

  ONE PAGE, NOT THREE. The spec carried byte-identical web/android/ios copies of
  this module. Platform is a parameter of the same page instead — three copies of
  one editor means a fix applied to one and forgotten in the other two, and the
  platform vocabulary is now five, so the copy count would have grown.

  [+ NEW TEST CASE] OFFERS THREE ORIGINS. The spec assumed hand-authoring only.
  A test case can now also be generated from an uploaded spreadsheet or from a
  written prompt; both routes produce the same reviewable step list, with each
  step carrying the status the mapper assigned it. Hand-authoring is unchanged.
"""
from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

from nicegui import ui

from ui import api_client as api
from ui.components.nlp_input import NlpInput
from ui.components.step_row import step_row
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.theme import COLORS, TYPOGRAPHY


class TestCasesPage:
    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.platforms: list[dict] = []
        self.projects: list[str] = []
        self.selected: str | None = None
        self.steps: list[str] = []
        #: Test cases ticked in the list for a bulk delete. Empty = normal list.
        self.picked: set[str] = set()
        self.pick_mode = False
        self.step_meta: dict[int, dict] = {}     # 1-based -> generation metadata
        self.filter = ""
        #: Elements this platform's runner can resolve — what decides whether a
        #: step will run, and therefore how its element token is coloured.
        self._locator_labels: dict[str, str] = {}
        #: Enough locator detail for the step editor to fork one into a new copy.
        self._locator_details: dict[str, dict[str, str]] = {}
        #: Elements that exist, but on some OTHER platform. Kept apart so the
        #: token can say "recorded for Android, not for Website" instead of the
        #: misleading "not in your element list", which invites a duplicate.
        self._locators_elsewhere: dict[str, str] = {}
        #: Where each ${variable} in this test gets its value — see
        #: POST /nlp/variables. Refreshed per render, because inserting a
        #: `store … as x` step changes the answer for every step below it.
        self._vars: dict = {"defined": {}, "stored": [], "unresolved": []}
        #: The list collapses while a test case is open so the editor gets the
        #: width, and comes back from the chevron. It used to sit there at full
        #: size competing with the thing you were actually editing.
        self.list_open = True
        #: 1-based indices ticked in the editor. Selection drives the bulk
        #: actions — save as a step group, switch off, delete — so it is held
        #: here rather than inside the rows, which are rebuilt constantly.
        self.selection: set[int] = set()
        #: Steps changed since the last save. Generated steps start dirty — they
        #: exist only in this editor until Save writes a file, and a reload or a
        #: dropped connection takes them with it. A whole drafted testcase was
        #: lost that way, with nothing on screen suggesting it was at risk.
        self.dirty = False
        #: Bands the reader has folded away. Not saved to the file — how you are
        #: reading a test is not part of what it does.
        self.collapsed: set[int] = set()
        self.select_mode = False
        #: Where an inline "new step" composer is open, as a 0-based position in
        #: self.steps. None means closed. A step could only ever be added at the
        #: very end, so putting one into the middle of a written test meant
        #: appending it and walking it up the list one press at a time.
        self.compose_at: int | None = None
        #: Folders (Testsigma-style tree): {"folders": [paths], "assign": {test: path}}.
        self.folder_data: dict = {"folders": [], "assign": {}}
        #: Folders shown open. New test cases go into `current_folder`.
        self.open_folders: set[str] = set()
        self.current_folder: str = ""

    # ── data ────────────────────────────────────────────────────────────────
    async def load(self) -> None:
        try:
            self.platforms = await api.platforms()
            # Scoped to the selected platform: a Website author has no use for
            # ios_e2e in their list, and cannot run it from there anyway.
            raw = await api.list_projects(self.platform)
            self.projects = sorted(
                p if isinstance(p, str) else p.get("name", "") for p in raw)
            try:
                self.folder_data = await api.folders()
            except api.ApiError:
                self.folder_data = {"folders": [], "assign": {}}
        except api.ApiError as e:
            ui.notify(f"Could not load: {e.detail}", type="negative")

    def open_project_guarded(self, name: str) -> None:
        """Opening another testcase discards unsaved steps — so it asks first."""
        if self.dirty and name == self.selected:
            # Clicking the test case that is already open used to re-read the
            # file and throw the unsaved steps away with no question asked.
            ui.notify("This test case is already open — press Save to keep your changes",
                      type="info")
            return
        if not self.dirty:
            ui.timer(0.01, lambda: self.open_project(name), once=True)
            return
        self._ask_unsaved(lambda: self.open_project(name))

    def _ask_unsaved(self, then) -> None:
        """Save / discard / cancel before `then()` replaces the editor's steps."""
        import inspect
        current = self.selected

        async def go() -> None:
            r = then()
            if inspect.isawaitable(r):
                await r
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:30rem"):
            ui.label(f"{current} has unsaved steps").style(
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            ui.label("Going on discards them. They are not written to a file "
                     "until you save.").style(
                f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")

            async def save_then() -> None:
                dialog.close()
                # Only move on if the save really happened — a refused save
                # (viewer, bad name, conflict) used to lose the edits here.
                if await self.save():
                    await go()

            async def discard() -> None:
                dialog.close()
                self.dirty = False
                await go()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Discard them", on_click=discard).props("flat color=negative")
                ui.button("Save first", on_click=save_then).props("unelevated")
        dialog.open()

    async def open_project(self, name: str) -> None:
        try:
            data = await api.get_project(name)
        except api.ApiError as e:
            ui.notify(f"Could not open {name}: {e.detail}", type="negative")
            return
        self.selected = name
        self.meta = data.get("meta") or {}
        # Remembered so a save can tell whether someone else saved in between.
        self.file_mtime = data.get("mtime")
        self._is_new = False
        try:
            from ui.layout.topbar import set_current_flow
            set_current_flow(name)
        except Exception:  # noqa: BLE001 - the button is a convenience, never a blocker
            pass
        # Header comments are dropped, but the two markers that ARE content are
        # kept: a purpose band, and a step switched off. Stripping every "#" line
        # meant both vanished the moment a test case was reopened — the file
        # still held them and the editor did not.
        raw_steps = data.get("steps", [])
        raw_lines = list(data.get("lines") or [])
        keep = [i for i, s in enumerate(raw_steps)
                if s.strip() and (not s.strip().startswith("#")
                                  or s.strip().startswith((self.DISABLED, self.PURPOSE)))]
        self.steps = [raw_steps[i] for i in keep]
        # Parallel to self.steps, when the API gave file lines.
        self.file_lines = [raw_lines[i] for i in keep] if len(raw_lines) == len(raw_steps) else []
        self.step_meta = {}
        self.dirty = False
        self.compose_at = None
        self.selection.clear()
        self.collapsed.clear()
        # replaceState, not navigate: the address bar has to match what is open so
        # a refresh restores it, but re-running the page would throw away the very
        # editor being opened. The name is remembered at the same time, so that
        # arriving with no ?flow at all — from the sidebar, or from a link that
        # dropped the query string — still lands on what you were editing.
        ui.run_javascript(
            f"history.replaceState({{}},'',"
            f"'/platform/{self.platform}?flow={quote(name, safe='')}');"
            f"localStorage.setItem({json.dumps(self._remember_key)},"
            f" {json.dumps(name)});")
        await self.render_editor()

    #: Where the last-opened test case is remembered, one key per platform.
    #: Browser storage rather than the server: "the one I was last editing" is
    #: a fact about the person at the keyboard, and it has to survive the
    #: process being restarted, which server memory does not.
    @property
    def _remember_key(self) -> str:
        return f"lastFlow:{self.platform}"

    async def restore_last(self) -> None:
        """
        Reopen whatever was last open on this platform.

        Only when nothing is open already — a ?flow in the URL is an explicit
        instruction and must not be overridden by a remembered one.
        """
        if self.selected:
            return
        try:
            name = await ui.run_javascript(
                f"localStorage.getItem({json.dumps(self._remember_key)})",
                timeout=2.0)
        except Exception:  # noqa: BLE001 — a page still connecting has no storage
            return
        if isinstance(name, str) and name in self.projects:
            await self.open_project(name)

    # ── rendering ───────────────────────────────────────────────────────────
    def render(self) -> None:
        sidebar(active=f"/platform/{self.platform}", platforms=self.platforms)
        topbar(["Author", "Test Cases"], platforms=self.platforms,
               platform=self.platform,
               on_platform_change=lambda p: ui.navigate.to(f"/platform/{p}"),
               current_flow=self.selected or "")
        # Dialogs are parented here, outside the panes that get cleared and
        # redrawn; a dialog created inside the review panel vanished the
        # moment the editor re-rendered under it.
        self.dialog_host = ui.element("div")
        with ui.row().classes("w-full no-wrap gap-4 p-4"):
            self.left = ui.column().classes("gap-2").style("width:20rem; flex:none")
            # min-width:0 — a flex child otherwise refuses to be narrower than
            # its widest line, so one long step widened the whole editor past
            # the viewport and hid the right-hand toolbar.
            self.right = ui.column().classes("flex-grow gap-2").style("min-width:0")
        self.render_list()
        # Installed once per page. Without it the flag below is set on a window
        # that has no handler reading it, which is how this shipped un-armed.
        ui.timer(0.2, self._guard_unload, once=True)
        with self.right:
            ui.label("Select a test case, or create one.").style(
                f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_sm']}")

    def delete_dialog(self) -> None:
        """
        Delete a test case, after saying exactly what that does and does not do.

        The worry with a delete button next to Save is that it takes the
        elements and the test data with it. It does not, and the dialog says so
        — an unqualified "Are you sure?" leaves the author to guess.
        """
        if not self.selected:
            return
        victim = self.selected
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:30rem"):
            ui.label(f"Delete {victim}?").style(
                f"font-size:{TYPOGRAPHY['size_lg']};"
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            ui.label(f"This removes the test case and its {len(self.steps)} step(s).").style(
                f"font-size:{TYPOGRAPHY['size_sm']}")
            ui.label("Your saved elements, test data and step groups are NOT "
                     "affected — other test cases keep working.").style(
                f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")

            async def do_delete() -> None:
                try:
                    await api.delete_project(victim)
                except api.ApiError as e:
                    ui.notify(f"Could not delete: {e.detail}", type="negative")
                    return
                dialog.close()
                ui.notify(f"Deleted {victim}", type="positive")
                # Forget it, or the next visit tries to reopen a test case that
                # no longer exists and lands on the empty pane anyway.
                ui.run_javascript(
                    f"localStorage.removeItem({json.dumps(self._remember_key)})")
                self.selected = None
                self.steps = []
                self.step_meta = {}
                await self.load()
                self.render_list()
                self.right.clear()
                with self.right:
                    ui.label("Select a test case, or create one.").style(
                        f"color:{COLORS['text_muted']};"
                        f"font-size:{TYPOGRAPHY['size_sm']}")

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Delete", on_click=do_delete) \
                    .props("unelevated color=negative")
        dialog.open()

    def rename_dialog(self) -> None:
        """
        Rename, after showing what else it will change.

        A flow is named by suites and plans, so renaming one can rewrite files
        the author is not looking at. The preview is fetched first and listed;
        nothing moves until they confirm it.
        """
        if not self.selected:
            return
        if self.dirty:
            # Renaming reloads the test case from disk under its new name, which
            # threw unsaved steps away.
            ui.notify("Save (or discard) your unsaved steps before renaming.", type="warning")
            return
        current = self.selected
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:34rem"):
            ui.label(f"Rename {current}").style(
                f"font-size:{TYPOGRAPHY['size_lg']};"
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            box = ui.input("New name", value=current).props("outlined dense").classes("w-full")
            impact = ui.column().classes("w-full gap-1")
            note = ui.label().style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['warning']}")

            async def preview() -> None:
                impact.clear()
                note.set_text("")
                new = (box.value or "").strip()
                if not new or new == current:
                    return
                # Show the convention rather than enforcing it silently: the
                # name is normalised on save, and the author should see the
                # result before committing to it, not discover it afterwards.
                try:
                    nm = await api.check_flow_name(new)
                    if not nm.get("ok"):
                        note.set_text(f"{nm.get('reason','')} {nm.get('hint','')}")
                        return
                    if nm.get("changed"):
                        note.set_text(f"Will be saved as “{nm['saved_as']}” — "
                                      f"{nm.get('hint','')}")
                except api.ApiError:
                    pass
                try:
                    res = await api.rename_project(current, new, apply=False)
                except api.ApiError as e:
                    note.set_text(e.detail[:160])
                    return
                with impact:
                    ui.label("This will change:").style(
                        f"font-size:{TYPOGRAPHY['size_sm']};"
                        f"font-weight:{TYPOGRAPHY['weight_medium']}")
                    for ch in res.get("changes", []):
                        ui.label(f"• {ch['kind']} — {ch['file']} {ch.get('detail','')}").style(
                            f"font-size:{TYPOGRAPHY['size_xs']};"
                            f"font-family:{TYPOGRAPHY['mono']};"
                            f"color:{COLORS['text_muted']}")

            async def do_rename() -> None:
                new = (box.value or "").strip()
                if not new or new == current:
                    dialog.close()
                    return
                try:
                    res = await api.rename_project(current, new, apply=True)
                except api.ApiError as e:
                    note.set_text(e.detail[:160])
                    return
                dialog.close()
                ui.notify(f"Renamed to {res['new_name']}", type="positive")
                self.selected = res["new_name"]
                await self.load()
                self.render_list()
                await self.open_project(res["new_name"])

            box.on_value_change(preview)
            ui.timer(0.05, preview, once=True)
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Rename", on_click=do_rename).props("unelevated")
        dialog.open()

    def _new_and_collapse(self) -> None:
        """Open the create dialog and fold the list away behind its chevron."""
        if self.dirty:
            # Creating a new test case replaces the editor's steps just as
            # opening another one does — same question first.
            self._ask_unsaved(self._new_and_collapse)
            return
        self.list_open = False
        self.render_list()
        self.new_dialog()

    def _toggle_list(self) -> None:
        self.list_open = not self.list_open
        self.render_list()

    def render_list(self) -> None:
        """
        Draw the whole left column: search box, list, and the new-testcase button.

        The rows live in their own container (`self.list_box`) so that filtering
        can redraw JUST them. Rebuilding this column on every keystroke destroyed
        the search input mid-word — the text you had typed went with it, and the
        filter ended up applied to whatever single character survived.
        """
        self.left.clear()
        if not self.list_open:
            # Collapsed: one chevron, so the list is one click away and the
            # editor has the room.
            self.left.style("width:2.5rem; flex:none")
            with self.left:
                ui.button(icon="chevron_right", on_click=self._toggle_list) \
                    .props("flat dense").tooltip("Show test cases")
                ui.label(str(len(self.projects))).style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']};"
                    f"text-align:center; width:100%")
            return
        self.left.style("width:20rem; flex:none")
        with self.left:
            with ui.row().classes("w-full items-center no-wrap gap-1"):
                self.search = ui.input(placeholder="Search test cases",
                                       value=self.filter,
                                       on_change=lambda e: self._filter(e.value)) \
                    .props("outlined dense clearable").classes("flex-grow")
                ui.button(icon="create_new_folder", on_click=lambda: self._folder_dialog("new")) \
                    .props("flat dense").tooltip("New folder")
                ui.button(icon="checklist", on_click=self._toggle_pick) \
                    .props("flat dense" + (" color=primary" if self.pick_mode else "")) \
                    .tooltip("Select several test cases to move or delete")
                ui.button(icon="chevron_left", on_click=self._toggle_list) \
                    .props("flat dense").tooltip("Hide the list")
            self.pick_bar = ui.row().classes("w-full items-center gap-2 no-wrap")
            self.list_box = ui.column().classes("w-full gap-0").style(
                f"border:1px solid {COLORS['border']}; border-radius:6px;"
                f"max-height:32rem; overflow-y:auto")
            ui.button("+ New Test Case", icon="add", on_click=self._new_and_collapse) \
                .props("unelevated").classes("w-full") \
                .style(f"background:{COLORS['primary']}")
        self.render_rows()

    def render_rows(self) -> None:
        """Redraw only the rows (folder tree). Called on every keystroke; the input is untouched."""
        if not getattr(self, "list_box", None):
            return
        self.list_box.clear()
        assign = self.folder_data.get("assign", {})
        shown = [p for p in self.projects if self.filter in p.lower()]
        with self.list_box:
            if not shown and not self.folder_data.get("folders"):
                ui.label("Nothing matches that search" if self.filter
                         else "No test cases yet").style(
                    f"padding:10px; color:{COLORS['text_muted']};"
                    f"font-size:{TYPOGRAPHY['size_sm']}")
            elif self.filter:
                # Searching: a flat list, each row saying which folder it is in.
                for name in shown:
                    self._test_row(name, 0, folder=assign.get(name, "") or "Unfiled")
                if not shown:
                    ui.label("Nothing matches that search").style(
                        f"padding:10px; color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_sm']}")
            else:
                folders = sorted(self.folder_data.get("folders", []), key=str.lower)
                for top in [f for f in folders if "/" not in f]:
                    self._folder_node(top, folders, assign, 0)
                unfiled = [n for n in shown if not assign.get(n) or assign.get(n) not in folders]
                if unfiled:
                    self._folder_header("", "Unfiled", len(unfiled), 0, unfiled=True)
                    if "" in self.open_folders or not folders:
                        for n in unfiled:
                            self._test_row(n, 1)
        self._render_pick_bar(shown)

    def _count_in(self, path: str, assign: dict) -> int:
        """Test cases in this folder and everything under it."""
        return sum(1 for t, f in assign.items()
                   if t in self.projects and (f == path or f.startswith(path + "/")))

    def _folder_node(self, path: str, folders: list[str], assign: dict, depth: int) -> None:
        self._folder_header(path, path.split("/")[-1], self._count_in(path, assign), depth)
        if path not in self.open_folders:
            return
        for sub in [f for f in folders if f.startswith(path + "/") and "/" not in f[len(path) + 1:]]:
            self._folder_node(sub, folders, assign, depth + 1)
        for t in sorted(t for t, f in assign.items() if f == path and t in self.projects):
            self._test_row(t, depth + 1)

    def _folder_header(self, path: str, label: str, count: int, depth: int,
                       unfiled: bool = False) -> None:
        is_open = path in self.open_folders or (unfiled and not self.folder_data.get("folders"))
        current = (path == self.current_folder) and not unfiled and bool(path)
        row = ui.row().classes("w-full items-center no-wrap cursor-pointer gap-1").style(
            f"padding:6px 8px 6px {8 + depth * 14}px; border-bottom:1px solid {COLORS['border']};"
            f"background:{COLORS['primary'] + '0d' if current else COLORS['surface'] if 'surface' in COLORS else 'transparent'}")
        with row:
            ui.icon("expand_more" if is_open else "chevron_right", size="18px").style(
                f"color:{COLORS['text_muted']}")
            ui.icon("folder_open" if is_open else ("inventory_2" if unfiled else "folder"),
                    size="18px").style(f"color:{COLORS['primary'] if not unfiled else COLORS['text_muted']}")
            ui.label(label).classes("flex-grow").style(
                f"font-size:{TYPOGRAPHY['size_sm']}; font-weight:{TYPOGRAPHY['weight_bold']}")
            ui.label(str(count)).style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            if not unfiled:
                with ui.button(icon="more_vert").props("flat dense round size=sm") \
                        .on("click.stop", lambda: None):
                    with ui.menu():
                        ui.menu_item("New sub-folder", on_click=lambda p=path: self._folder_dialog("new", p))
                        ui.menu_item("Rename", on_click=lambda p=path: self._folder_dialog("rename", p))
                        ui.menu_item("Delete folder", on_click=lambda p=path: self._folder_delete_dialog(p))
        row.on("click", lambda p=path: self._toggle_folder(p))
        # Drop target: drag a test case onto a folder (or onto Unfiled) to move it.
        row.on("dragover.prevent", lambda: None)
        row.on("dragenter", js_handler="(e) => e.currentTarget.style.outline = "
               f"'2px dashed {COLORS['primary']}'")
        row.on("dragleave", js_handler="(e) => e.currentTarget.style.outline = ''")
        row.on("drop.prevent", lambda p=path: self._drop_on(p))

    async def _drop_on(self, folder: str) -> None:
        name = getattr(self, "_dragging", None)
        self._dragging = None
        if not name:
            return
        await self._move_to([name], folder)

    async def _move_to(self, tests: list[str], folder: str) -> None:
        if all(self.folder_data.get("assign", {}).get(t, "") == folder for t in tests):
            self.render_rows()          # dropped where it already was
            return
        try:
            res = await api.assign_folder(tests, folder)
        except api.ApiError as e:
            ui.notify(e.detail, type="warning")
            return
        dest = res.get("folder", "") or "Unfiled"
        if folder:
            parts = folder.split("/")
            self.open_folders.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        ui.notify(f"Moved {', '.join(tests) if len(tests) < 3 else f'{len(tests)} test cases'} to {dest}",
                  type="positive")
        await self.load()
        self.render_rows()

    def _toggle_folder(self, path: str) -> None:
        if path in self.open_folders:
            self.open_folders.discard(path)
        else:
            self.open_folders.add(path)
        # The folder last opened is where "+ New Test Case" puts the new one.
        self.current_folder = path
        self.render_rows()

    def _test_row(self, name: str, depth: int, folder: str = "") -> None:
        on = name == self.selected
        row = ui.row().classes("w-full items-center no-wrap cursor-pointer gap-1") \
            .style(f"padding:6px 10px 6px {10 + depth * 14}px;"
                   f"border-bottom:1px solid {COLORS['border']};"
                   f"background:"
                   f"{COLORS['primary'] + '12' if on else 'transparent'}")
        with row:
            if self.pick_mode:
                ui.checkbox(value=name in self.picked,
                            on_change=lambda e, n=name: self._pick(n, e.value)) \
                    .props("dense")
            else:
                ui.icon("description", size="16px").style(f"color:{COLORS['text_muted']}")
            with ui.column().classes("gap-0").style("min-width:0"):
                ui.label(name).style(
                    f"font-size:{TYPOGRAPHY['size_sm']};"
                    f"font-family:{TYPOGRAPHY['mono']}; word-break:break-all")
                if folder:
                    ui.label(folder).style(
                        f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            if not self.pick_mode:
                ui.space()
                with ui.button(icon="more_vert").props("flat dense round size=xs") \
                        .on("click.stop", lambda: None):
                    with ui.menu():
                        ui.menu_item("Move to folder…",
                                     on_click=lambda n=name: self._move_dialog([n]))
        if self.pick_mode:
            row.on("click", lambda n=name: self._pick(n, n not in self.picked))
        else:
            row.on("click", lambda n=name: self.open_project_guarded(n))
            # Drag a test case onto a folder to move it there.
            row.props('draggable="true"').tooltip("Drag onto a folder to move")
            row.on("dragstart", lambda n=name: setattr(self, "_dragging", n))

    # ── folders ─────────────────────────────────────────────────────────────
    def _folder_dialog(self, mode: str, path: str = "") -> None:
        """mode 'new' (a folder, or a sub-folder of `path`) or 'rename' (`path`)."""
        with self.dialog_host:
            dialog = ui.dialog()
        with dialog, ui.card().style("width:28rem"):
            if mode == "rename":
                ui.label(f"Rename folder {path}").style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
                name = ui.input("New name", value=path.split("/")[-1]).props("outlined dense autofocus") \
                    .classes("w-full")
            else:
                ui.label("New sub-folder of " + path if path else "New folder").style(
                    f"font-weight:{TYPOGRAPHY['weight_bold']}")
                name = ui.input("Name", placeholder="e.g. PDP   —   or a path: Prashant/B2B/PDP") \
                    .props("outlined dense autofocus").classes("w-full")
                ui.label("A path with / creates the parents too.").style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

            async def save() -> None:
                val = (name.value or "").strip()
                if not val:
                    return
                try:
                    if mode == "rename":
                        res = await api.rename_folder(path, val)
                        new = res.get("path", "")
                        self.open_folders = {new + o[len(path):] if o == path or o.startswith(path + "/")
                                             else o for o in self.open_folders}
                        if self.current_folder == path or self.current_folder.startswith(path + "/"):
                            self.current_folder = new + self.current_folder[len(path):]
                    else:
                        res = await api.create_folder(f"{path}/{val}" if path else val)
                        new = res.get("path", "")
                        parts = new.split("/")
                        self.open_folders.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
                        self.current_folder = new
                except api.ApiError as e:
                    ui.notify(e.detail, type="warning")
                    return
                dialog.close()
                await self.load()
                self.render_rows()
            name.on("keydown.enter", save)
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Rename" if mode == "rename" else "Create", on_click=save).props("unelevated")
        dialog.open()

    def _folder_delete_dialog(self, path: str) -> None:
        n = self._count_in(path, self.folder_data.get("assign", {}))
        parent = "/".join(path.split("/")[:-1]) or "Unfiled"

        async def go() -> None:
            try:
                await api.delete_folder(path)
            except api.ApiError as e:
                ui.notify(e.detail, type="warning")
                return
            self.open_folders = {o for o in self.open_folders if not (o == path or o.startswith(path + "/"))}
            if self.current_folder == path or self.current_folder.startswith(path + "/"):
                self.current_folder = ""
            await self.load()
            self.render_rows()
        self._confirm(f"Delete folder {path}?",
                      [f"Its sub-folders go too. The {n} test case(s) in it are NOT deleted — "
                       f"they move to {parent}."], go, button="Delete folder", note="")

    def _move_dialog(self, only: list[str] | None = None) -> None:
        tests = sorted(only) if only else sorted(self.picked)
        if not tests:
            return
        with self.dialog_host:
            dialog = ui.dialog()
        with dialog, ui.card().style("width:30rem"):
            ui.label(f"Move {len(tests)} test case(s) to").style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
            options = {"": "Unfiled"} | {f: f for f in sorted(self.folder_data.get("folders", []), key=str.lower)}
            pick = ui.select(options, value=self.current_folder if self.current_folder in options else "",
                             with_input=True).props("outlined dense").classes("w-full")
            ui.label("Or type a new folder path (created for you):").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            new = ui.input(placeholder="Prashant/B2B/PDP").props("outlined dense").classes("w-full")

            async def go() -> None:
                target = (new.value or "").strip() or (pick.value or "")
                dialog.close()
                if not only:
                    self.picked.clear()
                    self.pick_mode = False
                    self.render_list()
                await self._move_to(tests, target)
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Move", icon="drive_file_move", on_click=go).props("unelevated")
        dialog.open()

    # ── bulk delete ─────────────────────────────────────────────────────────
    def _toggle_pick(self) -> None:
        self.pick_mode = not self.pick_mode
        if not self.pick_mode:
            self.picked.clear()
        self.render_list()

    def _pick(self, name: str, on: bool) -> None:
        (self.picked.add if on else self.picked.discard)(name)
        self.render_rows()

    def _render_pick_bar(self, shown: list[str]) -> None:
        bar = getattr(self, "pick_bar", None)
        if bar is None:
            return
        bar.clear()
        if not self.pick_mode:
            return
        with bar:
            ui.checkbox("All shown", value=bool(shown) and all(n in self.picked for n in shown),
                        on_change=lambda e: (self.picked.update(shown) if e.value
                                             else self.picked.difference_update(shown),
                                             self.render_rows())).props("dense")
            ui.space()
            ui.button("Move", icon="drive_file_move", on_click=lambda: self._move_dialog()) \
                .props("flat dense").set_enabled(bool(self.picked))
            ui.button(f"Delete {len(self.picked)}", icon="delete_outline",
                      on_click=self._bulk_delete_dialog) \
                .props("unelevated dense color=negative").set_enabled(bool(self.picked))

    def _bulk_delete_dialog(self) -> None:
        victims = sorted(self.picked)
        if not victims:
            return
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:34rem; max-height:80vh; overflow-y:auto"):
            ui.label(f"Delete {len(victims)} test case(s)?").style(
                f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
            for v in victims:
                ui.label(v).style(f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_sm']}")
            ui.label("Elements, test data and step groups are NOT affected. "
                     "This cannot be undone.").style(
                f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")

            async def do_delete() -> None:
                gone, failed = [], []
                for v in victims:
                    try:
                        await api.delete_project(v)
                        gone.append(v)
                    except api.ApiError as e:
                        failed.append(f"{v}: {e.detail}")
                dialog.close()
                if gone:
                    ui.notify(f"Deleted {len(gone)} test case(s)", type="positive")
                for f in failed:
                    ui.notify(f, type="warning")
                if self.selected in gone:
                    ui.run_javascript(
                        f"localStorage.removeItem({json.dumps(self._remember_key)})")
                    self.selected = None
                    self.steps = []
                    self.step_meta = {}
                    self.right.clear()
                self.picked.clear()
                self.pick_mode = False
                await self.load()
                self.render_list()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button(f"Delete {len(victims)}", on_click=do_delete) \
                    .props("unelevated color=negative")
        dialog.open()

    def _filter(self, value: str) -> None:
        self.filter = (value or "").lower()
        self.render_rows()

    async def _guard_unload(self) -> None:
        """
        Ask the browser to warn before a reload discards unsaved steps.

        The handler looks for the "unsaved" badge in the DOM rather than a flag
        we have to keep updated. The badge is drawn from self.dirty on every
        render, so it cannot fall out of step with the editor — an earlier
        version kept a separate window variable and silently never set it.
        """
        try:
            await ui.run_javascript(
                "window.onbeforeunload = function (e) {"
                "  if (!document.querySelector('[data-unsaved=\"1\"]')) return undefined;"
                "  e.preventDefault(); e.returnValue = ''; return ''; };")
        except Exception:  # noqa: BLE001 — a page still connecting has no window
            pass

    def _confirm(self, title: str, lines: list[str], on_yes, *, button: str = "Delete",
                 note: str = "Nothing is written until you press Save.") -> None:
        """
        One confirmation for every destructive click in the editor.

        Steps, bands and bulk removals used to vanish on a single click; a
        mis-click at step 100 was only recoverable by not saving. The dialog
        lists exactly what goes so the person can read it before agreeing.
        """
        host = getattr(self, "dialog_host", None)
        with (host if host is not None else ui.element("div")):
            dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:36rem; max-height:80vh; overflow-y:auto"):
            ui.label(title).style(
                f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
            with ui.column().classes("w-full gap-0").style(
                    f"border:1px solid {COLORS['border']}; border-radius:6px;"
                    f"max-height:14rem; overflow-y:auto"):
                for ln in lines[:60]:
                    ui.label(ln).style(f"font-family:{TYPOGRAPHY['mono']};"
                                       f"font-size:{TYPOGRAPHY['size_xs']}; padding:3px 8px")
                if len(lines) > 60:
                    ui.label(f"… and {len(lines) - 60} more").style(
                        f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}; padding:3px 8px")
            ui.label(note).style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

            async def go() -> None:
                dialog.close()
                res = on_yes()
                if asyncio.iscoroutine(res):
                    await res

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button(button, icon="delete_outline", on_click=go) \
                    .props("unelevated color=negative")
        dialog.open()

    def _restore_scroll(self, top: int) -> None:
        """Put the step list back where it was once the client has rebuilt it."""
        try:
            ui.run_javascript(
                "(function(){var want=%d, old=document.getElementById('steps-scroll'),"
                "n=0, hits=0; var t=setInterval(function(){"
                "var el=document.getElementById('steps-scroll');"
                "if(el && el!==old){el.scrollTop=want;"
                "if(Math.abs(el.scrollTop-want)<4 && ++hits>3){clearInterval(t);return;}}"
                "if(++n>60){clearInterval(t);}},100);})();" % top)
        except Exception:  # noqa: BLE001 — cosmetic
            pass

    async def render_editor(self) -> None:
        """Redraw the editor — one redraw at a time.

        Every change schedules a redraw, and a redraw awaits several API calls
        and draws in batches; two quick clicks used to start a second redraw
        while the first was still drawing into rows that no longer existed
        (duplicate "Add a step" boxes, a move applied to the wrong step). A
        request that arrives mid-redraw now just asks for one more pass.
        """
        if getattr(self, "_rendering", False):
            self._rerender = True
            return
        self._rendering = True
        try:
            while True:
                self._rerender = False
                await self._render_editor_once()
                if not self._rerender:
                    break
        finally:
            self._rendering = False

    async def _render_editor_once(self) -> None:
        # Every edit redraws the list; without this the list jumped back to
        # the top each time, so working at step 100 meant scrolling down again
        # after every click.
        try:
            top = await ui.run_javascript(
                "(document.getElementById('steps-scroll')||{scrollTop:0}).scrollTop", timeout=1.0)
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning("scroll read failed: %r", e)
            top = 0
        if top:
            self._restore_scroll(int(top))
        try:
            await self._render_editor()
        except Exception as e:  # noqa: BLE001
            # An empty pane reads as "your work is gone". Whatever went wrong,
            # say it, and leave a way back to the test case.
            self.right.clear()
            with self.right:
                ui.label("Could not draw this test case").style(
                    f"font-weight:{TYPOGRAPHY['weight_bold']}; color:{COLORS['danger']}")
                ui.label(f"{type(e).__name__}: {str(e)[:200]}").style(
                    f"font-size:{TYPOGRAPHY['size_xs']};"
                    f"font-family:{TYPOGRAPHY['mono']}; color:{COLORS['text_muted']}")
                ui.button("Reload it",
                          on_click=lambda: ui.navigate.to(
                              f"/platform/{self.platform}?flow={self.selected or ''}")) \
                    .props("flat")
            raise

    async def _render_editor(self) -> None:
        # Fetched here rather than inside each row: a forty-step flow would
        # otherwise make forty identical calls just to draw itself. Once per
        # RENDER rather than once per page, though — an element saved from a
        # step token used to stay amber until the page was reloaded, because
        # the map that decided its colour had been read before it existed. Two
        # in-process reads cost a few milliseconds against the per-row work.
        try:
            self._locator_labels = await api.locator_labels(self.platform)
            self._locator_details = _locator_detail_index(
                await api.locators_for(self.platform))
            everywhere = await api.locator_labels()
            self._locators_elsewhere = {
                n: label for n, label in everywhere.items()
                if n not in self._locator_labels}
        except api.ApiError:
            self._locator_labels = {}
            self._locator_details = {}
            self._locators_elsewhere = {}
        try:
            self._vars = await api.step_variables(self.steps)
        except api.ApiError:
            self._vars = {"defined": {}, "stored": [], "unresolved": []}
        # One request for every row's segmentation instead of one per row.
        await api.prefetch_segments([st for st in self.steps if not st.startswith("#")])
        # Step groups, so a `call <name>` row can show what it expands to.
        try:
            self._groups = {g["name"]: g.get("steps", [])
                            for g in await api.step_groups(self.platform)}
        except api.ApiError:
            self._groups = {}
        self.right.clear()
        with self.right:
            with ui.row().classes("w-full items-center gap-2"):
                ui.label(self.selected or "").style(
                    f"font-size:{TYPOGRAPHY['size_lg']};"
                    f"font-weight:{TYPOGRAPHY['weight_bold']};"
                    f"font-family:{TYPOGRAPHY['mono']}")
                ui.button(icon="drive_file_rename_outline",
                          on_click=self.rename_dialog) \
                    .props("flat dense size=sm").tooltip("Rename this test case")
                # Bands are annotation, not steps — counting them made a
                # four-step test claim six.
                real = sum(1 for st in self.steps
                           if not st.strip().startswith(self.PURPOSE))
                ui.label(f"{real} steps").style(
                    f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_sm']}")
                meta = getattr(self, "meta", {}) or {}
                if meta.get("updated_by"):
                    ui.label(f"· edited by {meta['updated_by']} "
                             f"{(meta.get('updated_at') or '')[:16].replace('T', ' ')} UTC").style(
                        f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_xs']}") \
                        .tooltip(f"created by {meta.get('created_by', '?')}")
                if self.dirty:
                    with ui.row().classes("items-center gap-1") \
                            .props('data-unsaved="1"').style(
                            f"background:{COLORS['warning']}1A; border-radius:4px;"
                            f"padding:1px 8px"):
                        ui.label("●").style(
                            f"color:{COLORS['warning']}; font-size:0.6rem")
                        ui.label("unsaved").style(
                            f"color:{COLORS['warning']};"
                            f"font-size:{TYPOGRAPHY['size_xs']}")
                ui.space()
                ui.button("Review", icon="auto_fix_high", on_click=self.review) \
                    .props("flat dense").tooltip(
                        "Check this test for hardcoded values, fixed waits, "
                        "missing checks and repeated blocks")
                ui.button("Save", icon="save", on_click=self.save).props("unelevated dense")
                ui.button("Extend with AI", icon="auto_awesome",
                          on_click=lambda: ui.navigate.to(
                              f"/platform/{self.platform}/draft?extend={quote(self.selected or '', safe='')}")) \
                    .props("flat dense").tooltip(
                        "Describe what to add (a ticket link or a sentence) — the drafted "
                        "steps are appended after the last step of this test case")
                ui.button(icon="delete_outline", on_click=self.delete_dialog) \
                    .props("flat dense color=negative").tooltip("Delete this test case")
                ui.button("Run", icon="play_arrow", on_click=self.run) \
                    .props("unelevated dense").style(f"background:{COLORS['success']}")

            # Any step that cannot run is surfaced before the operator presses Run,
            # not after — that is the whole point of carrying per-step status.
            blocked = [i for i, m in self.step_meta.items()
                       if m.get("status") not in ("", "SUPPORTED", "SUPPORTED_VIA_SUBSTITUTE")]
            if blocked:
                with ui.row().classes("w-full items-center gap-2").style(
                        f"background:{COLORS['warning']}14; border:1px solid {COLORS['warning']}55;"
                        f"border-radius:6px; padding:6px 10px"):
                    ui.icon("info").style(f"color:{COLORS['warning']}")
                    ui.label(f"{len(blocked)} step(s) need attention before this will run") \
                        .style(f"font-size:{TYPOGRAPHY['size_sm']}")

            # ── selection toolbar ────────────────────────────────────────
            # Drawn by its own method so a single tick on a row can refresh
            # just this bar (count + action buttons) without redrawing rows.
            self.sel_bar = ui.row().classes("w-full items-center gap-2").style("padding:4px 2px")
            self._render_selection_toolbar()

            # Review findings sit ABOVE the step list: below it they were under
            # the scroll region and looked like nothing had happened.
            self.review_panel = ui.column().classes("w-full gap-1").style(
                "max-height:16rem; overflow-y:auto")

            # Only the step list scrolls. The name, Save / Run buttons and the
            # Select row stay put, so at step 100 "Select" is still one click
            # away instead of a scroll to the top of the page.
            with ui.column().classes("w-full gap-0").props('id="steps-scroll"').style(
                    f"border:1px solid {COLORS['border']}; border-radius:6px;"
                    f"max-height:calc(100vh - 17rem); overflow-y:auto"):
                # A purpose band applies to every step below it until the
                # next band. A hidden step is skipped, never renumbered, so what
                # a reader sees always matches the file.
                hidden = False
                # A variable is only usable from the step AFTER the one that
                # sets it. Carrying the running set down the list is what lets
                # `${otp}` read as unset at step 9 and settled at step 11.
                stored = set(self._vars.get("stored", []))
                made: dict = self._vars.get("defined", {}) or {}
                token_queue: list = []
                for i, text in enumerate(self.steps, 1):
                    known_here = stored | {n for n, at in made.items() if at < i}
                    if self.compose_at == i - 1:
                        await self._compose_row()
                    stripped = text.strip()
                    if stripped.startswith(self.PURPOSE):
                        hidden = i in self.collapsed
                        self._purpose_band(i, stripped[len(self.PURPOSE):].strip(),
                                           collapsed=hidden)
                        continue
                    if hidden:
                        continue
                    meta = self.step_meta.get(i, {})
                    off = stripped.startswith(self.DISABLED)
                    shown = stripped[len(self.DISABLED):] if off else text
                    step_row(i, shown, action=meta.get("action", ""),
                             platform=self.platform,
                             target=meta.get("locator", ""), status=meta.get("status", ""),
                             note=meta.get("note", ""), selector=meta.get("selector", ""),
                             total=len(self.steps),
                             locators=self._locator_labels,
                             locator_details=self._locator_details,
                             locators_elsewhere=self._locators_elsewhere,
                             variables=self._variables(),
                             known_values=known_here,
                             selectable=self.select_mode,
                             selected=i in self.selection,
                             on_select=self._toggle_selected,
                             disabled=off,
                             on_toggle_enabled=self._toggle_enabled,
                             on_drop=self.move_step_to,
                             on_add=self._open_composer,
                             on_edit=self.edit_step, on_delete=self.delete_step,
                             on_move=self.move_step,
                             token_queue=token_queue,
                             group_steps=getattr(self, "_groups", {}),
                             on_edit_group=self._edit_group)
                if self.compose_at == len(self.steps):
                    await self._compose_row()

            # The rows are on screen now; fill in their tokens in batches so
            # the first screenful is readable while the rest is still drawing.
            for k in range(0, len(token_queue), 25):
                for tok in token_queue[k:k + 25]:
                    try:
                        await tok.render()
                    except Exception:  # noqa: BLE001 — one bad row must not blank the rest
                        pass
                await asyncio.sleep(0.02)


            ui.label("Add a step").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']};"
                f"margin-top:8px")
            # No autofocus here: this box sits at the foot of the editor, and
            # focusing it on open would scroll a long test case to the bottom.
            box = NlpInput(self.platform, self.add_step,
                           known_variables=self._variables(), autofocus=False)
            await box.load()
            self._to_top_button()

    def _to_top_button(self) -> None:
        """
        A small floating "back to top" in the bottom-right corner.

        Two things scroll in this editor — the page (which takes the header with
        it) and the step list — so at step 100 the Save/Run row is two scrolls
        away. This one button resets both. It sits in the empty strip under the
        "Add a step" box, clear of the row icons, and only shows once either
        scroll has moved.
        """
        ui.button(icon="arrow_upward", on_click=lambda: ui.run_javascript(
            "window.scrollTo({top:0,behavior:'smooth'});"
            "var el=document.getElementById('steps-scroll');"
            "if(el){el.scrollTo({top:0,behavior:'smooth'});}")) \
            .props('round dense unelevated id="to-top-btn"') \
            .style(f"position:fixed; right:14px; bottom:10px; z-index:60;"
                   f"width:32px; height:32px; min-height:0; font-size:0.8rem;"
                   f"background:{COLORS['primary']}; color:white; opacity:0;"
                   f"pointer-events:none; transition:opacity .2s;"
                   f"box-shadow:0 2px 6px rgba(0,0,0,.25)") \
            .tooltip("Back to top")
        ui.run_javascript(
            "(function(){"
            "var b=document.getElementById('to-top-btn');"
            "var el=document.getElementById('steps-scroll'); if(!b)return;"
            "function upd(){var on=(window.scrollY>60)||(el&&el.scrollTop>60);"
            "b.style.opacity=on?'0.9':'0'; b.style.pointerEvents=on?'auto':'none';}"
            "if(!window.__toTopBound){window.addEventListener('scroll',function(){"
            "var x=document.getElementById('to-top-btn');"
            "var e=document.getElementById('steps-scroll');"
            "if(!x)return; var on=(window.scrollY>60)||(e&&e.scrollTop>60);"
            "x.style.opacity=on?'0.9':'0'; x.style.pointerEvents=on?'auto':'none';},"
            "{passive:true}); window.__toTopBound=true;}"
            "if(el&&!el.__toTop){el.addEventListener('scroll',upd,{passive:true});"
            "el.__toTop=true;} upd();})();")

    async def _compose_row(self) -> None:
        """
        A step being written, in the place it will land.

        The same autocomplete as the box at the bottom of the editor, so a step
        inserted mid-test is written with the same help as one appended to the
        end. It stays open at the next position after each insert: adding three
        steps in a row is one continuous action rather than three round trips
        through the row buttons.
        """
        where = self.compose_at or 0
        with ui.column().classes("w-full gap-1").style(
                f"background:{COLORS['primary']}0D;"
                f"border-top:1px solid {COLORS['primary']}44;"
                f"border-bottom:1px solid {COLORS['primary']}44; padding:6px 10px"):
            with ui.row().classes("w-full items-center gap-2"):
                ui.label(f"New step — goes in at position {where + 1}").style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['primary']};"
                    f"font-weight:{TYPOGRAPHY['weight_medium']}")
                ui.space()
                ui.button("Done", icon="close", on_click=self._close_composer) \
                    .props("flat dense").tooltip("Stop adding steps here")
            box = NlpInput(self.platform,
                           lambda text, at=where: self.insert_step(at, text),
                           placeholder="Type the step to insert here",
                           known_variables=self._variables())
            # Esc closes the composer, the same as Done. First press clears the
            # suggestion list (the box's own handler); on an empty box it closes.
            box.input.on("keydown.escape",
                         lambda _: self._close_composer()
                         if not (box.input.value or "").strip() else None)
            await box.load()

    def _open_composer(self, index: int, where: str) -> None:
        """Open the composer above or below the 1-based step `index`."""
        self.compose_at = index - 1 if where == "above" else index
        ui.timer(0.01, self.render_editor, once=True)

    def _close_composer(self) -> None:
        self.compose_at = None
        ui.timer(0.01, self.render_editor, once=True)

    def _variables(self) -> list[str]:
        """
        Every variable name worth offering as a ${completion}.

        References already written in the steps, PLUS the ones steps CREATE —
        `store text of … as otp_code` puts `otp_code` in scope without ever
        writing `${otp_code}`, so a list built only from references never
        offered the variable the previous step had just made, and it had to be
        typed from memory. Plus Test Data, which every run resolves.
        """
        import re
        found: set[str] = set()
        for s in self.steps:
            found |= set(re.findall(r"\$\{([A-Za-z0-9_]+)\}", s))
        found |= set(self._vars.get("defined", {}) or {})
        found |= set(self._vars.get("stored", []) or [])
        return sorted(found)

    # ── review ──────────────────────────────────────────────────────────────
    async def review(self) -> None:
        """
        Read the test and offer improvements, each with its reasoning.

        Deterministic: the same test gives the same findings every time, in
        milliseconds, with no model call. A finding that can be corrected
        automatically carries the corrected step, so it is one click rather than
        a description to act on by hand.
        """
        try:
            res = await api.review_steps(self.steps, self.platform,
                                         flow_name=self.selected or "")
        except api.ApiError as e:
            ui.notify(f"Could not review: {e.detail}", type="negative")
            return
        findings = res.get("findings", [])
        self.review_panel.clear()
        with self.review_panel:
            if not findings:
                with ui.row().classes("w-full items-center gap-2").style(
                        f"background:{COLORS['success']}14; border-radius:6px;"
                        f"padding:8px 10px"):
                    ui.icon("check_circle").style(f"color:{COLORS['success']}")
                    ui.label("Nothing to improve — no hardcoded values, fixed "
                             "waits, missing checks or unknown elements.").style(
                        f"font-size:{TYPOGRAPHY['size_sm']}")
                return
            c = res.get("counts", {})
            labels = res.get("labels", {})
            ui.label(f"{res['total']} suggestion(s) — {c.get('must', 0)} must do, "
                     f"{c.get('can', 0)} could, {c.get('optional', 0)} optional").style(
                f"font-weight:{TYPOGRAPHY['weight_bold']};"
                f"font-size:{TYPOGRAPHY['size_sm']}; margin-top:6px")
            # Grouped by what you are being asked to DO. Presenting everything as
            # one flat list of problems is what makes a review screen get skipped.
            for bucket in res.get("buckets", ["must", "can", "optional"]):
                rows = [f for f in findings if f.get("bucket") == bucket]
                if not rows:
                    continue
                tint = {"must": COLORS["danger"], "can": COLORS["warning"]}.get(
                    bucket, COLORS["text_muted"])
                with ui.row().classes("w-full items-center gap-2").style(
                        "margin-top:8px"):
                    ui.label(labels.get(bucket, bucket)).style(
                        f"color:{tint}; font-size:{TYPOGRAPHY['size_xs']};"
                        f"font-weight:{TYPOGRAPHY['weight_bold']};"
                        f"letter-spacing:.04em; text-transform:uppercase")
                    ui.label(f"({len(rows)})").style(
                        f"color:{COLORS['text_muted']};"
                        f"font-size:{TYPOGRAPHY['size_xs']}")
                for f in rows:
                    self._finding_row(f)

    def _change_block(self, caption: str, nlp: str, label: str, icon: str,
                      handler, colour: str) -> None:
        """
        The proposed step, written out, with the button that applies it.

        Upfront and verbatim, next to its own button. A finding that describes a
        change in prose leaves the author to reconstruct the step from the
        description and type it themselves — which is most of the work, and the
        reason a suggestion gets read and then skipped.
        """
        with ui.row().classes("w-full items-center gap-2 no-wrap").style(
                f"background:{COLORS['surface']}; border:1px solid {colour}55;"
                f"border-radius:4px; padding:4px 8px; margin-top:4px"):
            ui.label(caption).style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']};"
                f"white-space:nowrap")
            ui.label(nlp).classes("flex-grow").style(
                f"font-family:{TYPOGRAPHY['mono']};"
                f"font-size:{TYPOGRAPHY['size_sm']}; color:{colour};"
                f"word-break:break-all")
            ui.button(label, icon=icon, on_click=handler).props("unelevated dense") \
                .style(f"background:{colour}; white-space:nowrap")

    def _finding_row(self, f: dict) -> None:
        colour = {"high": COLORS["danger"], "medium": COLORS["warning"]}.get(
            f["severity"], COLORS["text_muted"])
        with ui.column().classes("w-full gap-0").style(
                f"border-left:3px solid {colour}; background:{colour}0D;"
                f"border-radius:4px; padding:6px 10px"):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.label(f["severity"].upper()).style(
                    f"color:{colour}; font-size:{TYPOGRAPHY['size_xs']};"
                    f"font-family:{TYPOGRAPHY['mono']}; width:4.2rem")
                where = f"step {f['step_index']}" if f["step_index"] else "this test"
                ui.label(where).style(
                    f"font-size:{TYPOGRAPHY['size_xs']};"
                    f"color:{COLORS['text_muted']}; width:4rem")
                ui.label(f["message"]).style(f"font-size:{TYPOGRAPHY['size_sm']}")
                ui.space()
                # Every finding gets the action its KIND allows. A description
                # with no way to act on it is why review screens get ignored.
                # The step-rewriting actions are NOT here — they sit with the
                # step they propose, further down, so the button and the text it
                # will write are read together.
                if f.get("extra"):
                    ui.button("Make a step group", icon="bookmark_add",
                              on_click=lambda ff=f: self._group_from_finding(ff)) \
                        .props("flat dense").style(f"color:{COLORS['primary']}")
                if f.get("kind") == "unknown_element":
                    # Needs a selector, which nothing can invent — so open the
                    # form with the name already filled in.
                    ui.button("Add this element", icon="add_location_alt",
                              on_click=lambda ff=f: self._add_element_for(ff)) \
                        .props("flat dense").style(f"color:{COLORS['primary']}") \
                        .tooltip("Record it now so the step can run")
                if f.get("kind") == "unstored_value":
                    ui.button("Save as test data", icon="dataset",
                              on_click=lambda ff=f: ui.navigate.to("/data/variables")) \
                        .props("flat dense").style(f"color:{COLORS['primary']}")
                if f.get("kind") == "no_assertion":
                    # A judgement, not a rule — this is where a model earns a turn.
                    ui.button("Suggest checks", icon="auto_awesome",
                              on_click=lambda ff=f: self._propose_for(ff)) \
                        .props("flat dense").style(f"color:{COLORS['primary']}") \
                        .tooltip("Read the steps and propose what this test should verify")
            # The reasoning is shown, not hidden behind a tooltip: a suggestion
            # you cannot evaluate is one you either follow blindly or ignore.
            ui.label(f["why"]).style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            if f.get("bucket_note"):
                ui.label(f"({f['bucket_note']})").style(
                    f"font-size:{TYPOGRAPHY['size_xs']};"
                    f"color:{COLORS['text_muted']}; font-style:italic")
            # The approaches, best first — so the choice is informed rather than
            # a single instruction to obey.
            for opt in f.get("options", []):
                mark = "★" if opt.get("best") else "○"
                colour = COLORS["success"] if opt.get("best") else COLORS["text_muted"]
                with ui.row().classes("items-start gap-2").style("padding-left:2px"):
                    ui.label(mark).style(f"color:{colour}; width:1rem")
                    with ui.column().classes("gap-0"):
                        ui.label(opt.get("approach", "")).style(
                            f"font-family:{TYPOGRAPHY['mono']};"
                            f"font-size:{TYPOGRAPHY['size_xs']}; color:{colour}")
                        ui.label(opt.get("note", "")).style(
                            f"font-size:{TYPOGRAPHY['size_xs']};"
                            f"color:{COLORS['text_muted']}")
            # ── the change itself, verbatim, each with its own button ────
            idx = int(f.get("step_index") or 0)
            if f.get("fix"):
                self._change_block(f"step {idx} becomes", f["fix"], "Apply", "done",
                                   lambda ff=f: self._apply_fix(ff),
                                   COLORS["primary"])
            elif f.get("fix_template") and idx:
                # The fix is known but its element is not — ask for the one
                # blank instead of showing advice with no button.
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    ui.label(f"step {idx} becomes").style(
                        f"font-size:{TYPOGRAPHY['size_xs']};"
                        f"color:{COLORS['text_muted']}; white-space:nowrap")
                    ui.label(f["fix_template"].replace("{}", "<element>")) \
                        .classes("flex-grow").style(
                            f"font-family:{TYPOGRAPHY['mono']};"
                            f"font-size:{TYPOGRAPHY['size_sm']};"
                            f"color:{COLORS['primary']}")
                    ui.button("Choose element & apply", icon="ads_click",
                              on_click=lambda ff=f: self._fill_template(ff)) \
                        .props("unelevated dense") \
                        .style(f"background:{COLORS['primary']}; white-space:nowrap")
            if f.get("add_step"):
                self._change_block(f"add as step {f.get('add_at') or idx}",
                                   f["add_step"], "Add", "add",
                                   lambda ff=f: self._add_from_finding(ff),
                                   COLORS["primary"])
            if f.get("remove") and 0 < idx <= len(self.steps):
                self._change_block(f"remove step {idx}", self.steps[idx - 1],
                                   "Remove", "delete_outline",
                                   lambda ff=f: self._remove_from_finding(ff),
                                   COLORS["danger"])
            # Where model-written proposals land, so they appear under the
            # finding that asked for them rather than in a dialog over it.
            if f.get("kind") == "no_assertion":
                f["_box"] = ui.column().classes("w-full gap-1")

    async def _add_from_finding(self, f: dict) -> None:
        """Put the finding's proposed step into the test, where it says, and save."""
        at = int(f.get("add_at") or 0)
        pos = self._insert_at(at - 1 if at else len(self.steps),
                              f.get("add_step", ""))
        if pos < 0:
            return
        await self._save_after_review(f"Added as step {pos + 1}")

    async def _remove_from_finding(self, f: dict) -> None:
        """Take out the step this finding says should not be there, and save."""
        idx = int(f.get("step_index") or 0)
        if not 0 < idx <= len(self.steps):
            return
        self._confirm(f"Remove step {idx} and save?", [f"{idx}  {self.steps[idx - 1]}"],
                      lambda: self._remove_from_finding_now(idx),
                      note="Review changes are saved to the test case immediately.")

    async def _remove_from_finding_now(self, idx: int) -> None:
        if not 0 < idx <= len(self.steps):
            return
        gone = self.steps.pop(idx - 1)
        self.step_meta = {}
        self.selection.clear()
        await self._save_after_review(f"Removed step {idx}: {gone.strip()[:60]}")

    async def _save_after_review(self, what: str) -> None:
        """Review edits are saved as they are applied; the review then re-runs."""
        try:
            await self._persist()
            self.dirty = False
            ui.notify(f"{what} — saved", type="positive")
        except api.ApiError as e:
            self.dirty = True
            ui.notify(f"{what}, but saving failed: {e.detail}", type="negative")
        await self._rerun_review()

    def _add_element_for(self, f: dict) -> None:
        """Open the save-element form for the name this step could not resolve."""
        import re as _re

        m = _re.search(r"'([^']+)'", f.get("message", ""))
        name = m.group(1) if m else ""
        from ui.components.token_step import _unknown_locator_dialog

        idx = f.get("step_index", 0)
        _unknown_locator_dialog(
            name, self.platform,
            on_saved=lambda saved: self._element_saved(f, saved),
            on_use_anyway=lambda: None,
            step=self.steps[idx - 1] if 0 < idx <= len(self.steps) else "")

    def _element_saved(self, f: dict, saved: str) -> None:
        idx = f.get("step_index", 0)
        if idx and saved:
            import re as _re

            m = _re.search(r"'([^']+)'", f.get("message", ""))
            if m and m.group(1) != saved:
                self.steps[idx - 1] = self.steps[idx - 1].replace(m.group(1), saved)
                self.dirty = True          # the step text changed; Save keeps it
        ui.timer(0.01, self._rerun_review, once=True)

    async def _propose_for(self, f: dict) -> None:
        """
        Ask for candidate assertions and show them under the finding.

        Never applied automatically, and never all at once. An assertion
        inserted without being read is how a suite fills up with checks that
        pass whatever the page does — which is the very problem this finding is
        about. Each candidate is shown as the step it would become, with its own
        Add button, so taking one and ignoring the rest is a single click.
        """
        box = f.get("_box")
        if box is None:
            return
        box.clear()
        with box:
            ui.label("Reading the steps…").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
        try:
            res = await api.review_assist(f.get("kind", ""), self.steps,
                                          self.platform, f.get("step_index", 0),
                                          self.selected or "")
        except api.ApiError as e:
            box.clear()
            with box:
                ui.label(e.detail[:220]).style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['danger']}")
            return

        try:
            self._render_proposals(box, res)
        except Exception as e:  # noqa: BLE001
            # Drawing the proposals used to fail silently: the model answered,
            # the request succeeded, and nothing appeared on screen.
            ui.notify(f"Could not show the proposals: {type(e).__name__}: {e}",
                      type="negative", timeout=9000)

    def _render_proposals(self, box, res: dict) -> None:
        """Draw each candidate assertion as a step with its own Add button."""
        proposals = res.get("proposals", [])
        box.clear()
        with box:
            if not proposals:
                ui.label("No assertion could be proposed from these steps — "
                         + ("; ".join(res.get("unclear", []))
                            or "the intent is unclear.")).style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['warning']}")
                return
            ui.label(f"Proposed by {res.get('model', 'the model')} — each one "
                     f"parses and uses only elements already in your steps. Add "
                     f"the ones that match what the test is for; ignore the "
                     f"rest.").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']};"
                f"margin-top:4px")
            for prop in proposals:
                pos = int(prop.get("insert_after", 0))
                with ui.row().classes("w-full items-center gap-2 no-wrap").style(
                        f"background:{COLORS['surface']};"
                        f"border:1px solid {COLORS['primary']}55;"
                        f"border-radius:4px; padding:4px 8px"):
                    with ui.column().classes("gap-0 flex-grow"):
                        ui.label(prop["step"]).style(
                            f"font-family:{TYPOGRAPHY['mono']};"
                            f"font-size:{TYPOGRAPHY['size_sm']};"
                            f"color:{COLORS['primary']}")
                        # Where it lands, in the test's own numbering — an
                        # assertion in the wrong place checks the wrong moment.
                        where = (f"goes in at step {pos + 1}, right after "
                                 f"“{prop.get('after_step_text', '')}”" if pos
                                 else "goes in as the first step")
                        ui.label(where).style(
                            f"font-size:{TYPOGRAPHY['size_xs']};"
                            f"color:{COLORS['text_muted']}")
                        ui.label(prop.get("reason", "")).style(
                            f"font-size:{TYPOGRAPHY['size_xs']};"
                            f"color:{COLORS['text_muted']}")
                    ui.button("Add", icon="add",
                              on_click=lambda p=prop: self._add_proposal(p)) \
                        .props("unelevated dense") \
                        .style(f"background:{COLORS['primary']}; white-space:nowrap")
            for bad in res.get("rejected", []):
                ui.label(f"Discarded: {bad['step']} — {bad['why']}").style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['danger']}")
            if res.get("unclear"):
                ui.label("Unclear from the steps: " + "; ".join(res["unclear"])).style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['warning']}")

    def _add_proposal(self, prop: dict) -> None:
        """Insert one proposed assertion where the proposal said it belongs."""
        pos = self._insert_at(int(prop.get("insert_after", len(self.steps))),
                              prop.get("step", ""))
        if pos < 0:
            return
        ui.notify(f"Added as step {pos + 1} — Save to keep it", type="positive")
        ui.timer(0.01, self._rerun_review, once=True)

    async def _apply_fix(self, f: dict) -> None:
        """
        Apply a review fix AND save it. "Updated — Save to keep it" was read as
        done; the next navigation then threw the change away.
        """
        idx = f.get("step_index", 0)
        if not idx or not f.get("fix") or idx > len(self.steps):
            return
        before = self.steps[idx - 1]
        self.steps[idx - 1] = f["fix"]
        try:
            await self._persist()
            self.dirty = False
            ui.notify(f"Step {idx} updated and saved", type="positive")
        except api.ApiError as e:
            if e.status == 409:
                # The conflict dialog is open with Reload / Overwrite: the
                # fix must stay applied so "Overwrite with mine" keeps it.
                self.dirty = True
                return
            self.steps[idx - 1] = before
            ui.notify(f"Could not save the change: {e.detail}", type="negative")
            return
        await self._rerun_review()

    async def _fill_template(self, f: dict) -> None:
        """Pick the element a template fix needs, then apply it like any fix."""
        try:
            names = sorted(await api.locator_names())
        except api.ApiError as e:
            ui.notify(f"Could not load elements: {e.detail}", type="negative")
            return
        with ui.dialog() as dlg, ui.card().style("min-width:28rem"):
            ui.label(f"Step {f.get('step_index')}: wait for which element?") \
                .style(f"font-size:{TYPOGRAPHY['size_sm']}; font-weight:600")
            pick = ui.select(names, with_input=True, label="Element") \
                .props("dense outlined use-input input-debounce=0").classes("w-full")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dlg.close).props("flat")

                async def go() -> None:
                    if not pick.value:
                        ui.notify("Choose an element first", type="warning")
                        return
                    dlg.close()
                    await self._apply_fix(
                        {**f, "fix": f["fix_template"].replace("{}", pick.value)})
                ui.button("Apply", icon="done", on_click=go).props("unelevated")
        dlg.open()

    async def _rerun_review(self) -> None:
        await self.render_editor()
        await self.review()

    async def _group_from_finding(self, f: dict) -> None:
        block = f.get("extra") or []
        start = f.get("step_index", 1)
        self.select_mode = True
        self.selection = set(range(start, start + len(block)))
        await self.render_editor()
        self.save_group_dialog(replace_in_flow=True)

    # ── purpose bands ───────────────────────────────────────────────────────
    def _purpose_band(self, index: int, purpose: str, collapsed: bool) -> None:
        """
        A one-line explanation above the steps it covers.

        Expanded by default: the steps ARE the test, and a reader forced to
        unfold everything is worse off than one reading a flat list. Folding is
        for sections you already understand.
        """
        n = self._band_size(index)
        with ui.row().classes("w-full items-center gap-2 no-wrap").style(
                f"background:{COLORS['primary']}0D;"
                f"border-top:1px solid {COLORS['primary']}33;"
                f"padding:5px 10px"):
            ui.button(icon="expand_more" if not collapsed else "chevron_right",
                      on_click=lambda i=index: self._toggle_band(i)) \
                .props("flat dense size=xs") \
                .tooltip("Fold these away" if not collapsed else "Show them again")
            ui.label("Purpose").style(
                f"background:{COLORS['primary']}1A; color:{COLORS['primary']};"
                f"border-radius:4px; padding:0 6px;"
                f"font-size:{TYPOGRAPHY['size_xs']};"
                f"font-family:{TYPOGRAPHY['mono']}")
            ui.label(purpose).style(
                f"font-size:{TYPOGRAPHY['size_sm']};"
                f"font-weight:{TYPOGRAPHY['weight_medium']}")
            ui.label(f"{n} step(s)").style(
                f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_xs']}")
            ui.space()
            ui.button(icon="edit",
                      on_click=lambda i=index, t=purpose: self._edit_band(i, t)) \
                .props("flat dense size=xs").tooltip("Reword it")
            ui.button(icon="close", on_click=lambda i=index: self._remove_band(i)) \
                .props("flat dense size=xs") \
                .tooltip("Remove the band — the steps stay exactly as they are")

    def _band_size(self, index: int) -> int:
        n = 0
        for st in self.steps[index:]:
            if st.strip().startswith(self.PURPOSE):
                break
            n += 1
        return n

    def _toggle_band(self, index: int) -> None:
        if index in self.collapsed:
            self.collapsed.discard(index)
        else:
            self.collapsed.add(index)
        ui.timer(0.01, self.render_editor, once=True)

    def _remove_band(self, index: int) -> None:
        """Drop the annotation, keep every step it covered."""
        self._confirm("Remove this purpose band?",
                      [self.steps[index - 1].strip()[len(self.PURPOSE):].strip(),
                       "(the steps under it stay exactly as they are)"],
                      lambda: self._remove_band_now(index), button="Remove band")

    def _remove_band_now(self, index: int) -> None:
        self.steps.pop(index - 1)
        self.collapsed.discard(index)
        self.step_meta = {}
        self.dirty = True
        ui.notify("Band removed — the steps are untouched", type="positive")
        ui.timer(0.01, self.render_editor, once=True)

    def _edit_band(self, index: int, current: str) -> None:
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:32rem"):
            ui.label("What are these steps for?").style(
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            box = ui.input(value=current,
                           placeholder="sign in and reach the dashboard") \
                .props("outlined dense").classes("w-full")

            def go() -> None:
                text = (box.value or "").strip()
                if not text:
                    return
                self.steps[index - 1] = self.PURPOSE + text
                self.dirty = True
                dialog.close()
                ui.timer(0.01, self.render_editor, once=True)

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Save", on_click=go).props("unelevated")
        dialog.open()

    def club_dialog(self) -> None:
        """
        Put a purpose band above the selected steps.

        Deliberately NOT a step group: nothing becomes reusable or callable. It
        is a sentence explaining what a run of steps achieves, so a reader can
        skip five lines they already understand.
        """
        if not self.selection:
            return
        first = min(self.selection)
        chosen = [self.steps[i - 1] for i in sorted(self.selection)]
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:34rem"):
            ui.label("Club these steps under a purpose").style(
                f"font-size:{TYPOGRAPHY['size_lg']};"
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            ui.label("One line saying what they achieve together. Annotation "
                     "only — the steps run exactly as they do now, and removing "
                     "the band leaves them alone.").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            box = ui.input("Purpose",
                           placeholder="verify the referral banner and free trial") \
                .props("outlined dense").classes("w-full")
            with ui.column().classes("w-full gap-0").style(
                    f"border:1px solid {COLORS['border']}; border-radius:6px;"
                    f"max-height:11rem; overflow-y:auto"):
                for st in chosen:
                    ui.label(st).style(
                        f"font-family:{TYPOGRAPHY['mono']};"
                        f"font-size:{TYPOGRAPHY['size_xs']}; padding:3px 8px")

            def go() -> None:
                text = (box.value or "").strip()
                if not text:
                    ui.notify("Say what they are for", type="warning")
                    return
                self.steps.insert(first - 1, self.PURPOSE + text)
                self.selection.clear()
                self.step_meta = {}
                self.dirty = True
                dialog.close()
                ui.notify("Clubbed — fold it away with the chevron", type="positive")
                ui.timer(0.01, self.render_editor, once=True)

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Club them", on_click=go).props("unelevated")
        dialog.open()

    # ── selection ───────────────────────────────────────────────────────────
    DISABLED = "# OFF: "
    #: A one-line explanation of what the steps BELOW it are collectively doing.
    #: A comment in the file, so the runner and the linter ignore it. Not a step
    #: group: nothing becomes reusable, nothing is called by name, and removing
    #: the band leaves every step exactly where it was.
    PURPOSE = "# --- Purpose: "

    def _set_select_mode(self, on: bool) -> None:
        self.select_mode = on
        if not on:
            self.selection.clear()
        ui.timer(0.01, self.render_editor, once=True)

    def _edit_group(self, name: str) -> None:
        """Open the group on the Step Groups page; Save there comes back here."""
        back = f"/platform/{self.platform}?flow={quote(self.selected or '', safe='')}"
        ui.navigate.to(f"/step-groups?platform={self.platform}&edit={quote(name, safe='')}"
                       f"&back={quote(back, safe='')}")

    def _render_selection_toolbar(self) -> None:
        bar = getattr(self, "sel_bar", None)
        if bar is None:
            return
        bar.clear()
        with bar:
            ui.checkbox("Select", value=self.select_mode,
                        on_change=lambda e: self._set_select_mode(bool(e.value))) \
                .props("dense")
            if not self.select_mode:
                return
            ui.checkbox("All", value=len(self.selection) == len(self.steps)
                        and bool(self.steps),
                        on_change=lambda e: self._select_all(bool(e.value))) \
                .props("dense")
            rng = ui.input(placeholder="20-30").props("outlined dense") \
                .style("width:7rem")
            ui.button("Select range", on_click=lambda: self._select_range(rng.value)) \
                .props("flat dense")
            ui.label(f"{len(self.selection)} selected").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            if self.selection:
                ui.button("Club with a purpose", icon="segment",
                          on_click=self.club_dialog) \
                    .props("flat dense").style(f"color:{COLORS['primary']}") \
                    .tooltip("Explain what these steps do — annotation, "
                             "not a reusable group")
                ui.button("Save as step group", icon="bookmark_add",
                          on_click=self.save_group_dialog) \
                    .props("flat dense").style(f"color:{COLORS['primary']}")
                ui.button("Switch off", icon="toggle_off",
                          on_click=lambda: self._bulk_enabled(False)) \
                    .props("flat dense")
                ui.button("Switch on", icon="toggle_on",
                          on_click=lambda: self._bulk_enabled(True)) \
                    .props("flat dense")
                ui.button("Delete", icon="delete_outline",
                          on_click=self._bulk_delete) \
                    .props("flat dense color=negative")

    def _toggle_selected(self, index: int, on: bool) -> None:
        self.selection.add(index) if on else self.selection.discard(index)
        self._render_selection_toolbar()

    def _select_all(self, on: bool) -> None:
        self.selection = set(range(1, len(self.steps) + 1)) if on else set()
        ui.timer(0.01, self.render_editor, once=True)

    def _select_range(self, text: str) -> None:
        """Accepts "20-30", "20 to 30" or a single number."""
        import re

        nums = [int(n) for n in re.findall(r"\d+", text or "")]
        if not nums:
            ui.notify("Give a range like 20-30", type="warning")
            return
        lo, hi = (nums[0], nums[-1]) if len(nums) > 1 else (nums[0], nums[0])
        lo, hi = max(1, min(lo, hi)), min(len(self.steps), max(lo, hi))
        self.selection |= set(range(lo, hi + 1))
        ui.notify(f"Selected steps {lo}–{hi}", type="positive")
        ui.timer(0.01, self.render_editor, once=True)

    def _toggle_enabled(self, index: int) -> None:
        step = self.steps[index - 1]
        self.dirty = True
        if step.strip().startswith(self.DISABLED):
            self.steps[index - 1] = step.strip()[len(self.DISABLED):]
        else:
            self.steps[index - 1] = self.DISABLED + step.strip()
        ui.timer(0.01, self.render_editor, once=True)

    def _bulk_enabled(self, on: bool) -> None:
        self.dirty = True
        for i in sorted(self.selection):
            step = self.steps[i - 1]
            is_off = step.strip().startswith(self.DISABLED)
            if on and is_off:
                self.steps[i - 1] = step.strip()[len(self.DISABLED):]
            elif not on and not is_off:
                self.steps[i - 1] = self.DISABLED + step.strip()
        ui.notify(f"{len(self.selection)} step(s) switched "
                  f"{'on' if on else 'off'}", type="positive")
        ui.timer(0.01, self.render_editor, once=True)

    def _bulk_delete(self) -> None:
        idxs = sorted(self.selection)
        if not idxs:
            return
        self._confirm(f"Remove {len(idxs)} selected step(s)?",
                      [f"{i}  {self.steps[i - 1]}" for i in idxs if 0 < i <= len(self.steps)],
                      self._bulk_delete_now, button=f"Remove {len(idxs)}")

    def _bulk_delete_now(self) -> None:
        self.dirty = True
        for i in sorted(self.selection, reverse=True):
            self.steps.pop(i - 1)
        n = len(self.selection)
        self.selection.clear()
        self.step_meta = {}
        ui.notify(f"Removed {n} step(s)", type="positive")
        ui.timer(0.01, self.render_editor, once=True)

    def save_group_dialog(self, replace_in_flow: bool = False) -> None:
        """
        Save the ticked steps as a reusable group.

        The steps are stored under one name and offered while typing as
        `call <name>` with an (sg) tag, so a sequence you repeat — open the site,
        wait, dismiss the login popup — is written once. With
        ``replace_in_flow`` the ticked steps are swapped for `call <name>` in
        THIS test case and it is saved — what the review's "make a step group"
        promises.
        """
        # "# OFF:" is kept: a switched-off step (a lead submission, say) must
        # stay off inside the group too — the runner skips "#" lines in a
        # group exactly as it does in a flow. Purpose bands are not steps.
        chosen = [self.steps[i - 1] for i in sorted(self.selection)
                  if not self.steps[i - 1].strip().startswith(self.PURPOSE)]
        if not chosen:
            ui.notify("Tick the steps first", type="warning")
            return
        host = getattr(self, "dialog_host", None)
        with (host if host is not None else ui.element("div")):
            dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:34rem"):
            ui.label("Save as step group").style(
                f"font-size:{TYPOGRAPHY['size_lg']};"
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            name = ui.input("Group name", placeholder="jd_open_and_dismiss_login") \
                .props("outlined dense").classes("w-full")
            rule = ui.label("Starts with a letter, at least 3 characters. "
                            "Spaces and capitals are fine.").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

            def check_name() -> bool:
                """
                Say whether the name will be accepted, as it is typed.

                The rule used to be enforced only on Save, by the server. A name
                that broke it left the dialog open with a line of small print
                below the fold — which reads as the button doing nothing at all.
                """
                typed = (name.value or "").strip()
                if not typed:
                    rule.set_text("Starts with a letter, at least 3 characters. "
                                  "Spaces and capitals are fine.")
                    rule.style(f"font-size:{TYPOGRAPHY['size_xs']};"
                               f"color:{COLORS['text_muted']}")
                    return False
                if not typed[0].isalpha() or len(typed) < 3:
                    rule.set_text(f"'{typed}' will not be accepted — it must "
                                  f"start with a letter and be at least 3 "
                                  f"characters.")
                    rule.style(f"font-size:{TYPOGRAPHY['size_xs']};"
                               f"color:{COLORS['danger']}")
                    return False
                # Surrounding spaces are trimmed on save, so show the result
                # rather than complaining about something invisible.
                extra = (" — saved without the surrounding spaces"
                         if typed != (name.value or "") else "")
                rule.set_text(f"Saved as “{typed}”{extra}. "
                              f"Reuse it with: call {typed}")
                rule.style(f"font-size:{TYPOGRAPHY['size_xs']};"
                           f"color:{COLORS['success']}")
                return True

            name.on_value_change(check_name)
            with ui.column().classes("w-full gap-0").style(
                    f"border:1px solid {COLORS['border']}; border-radius:6px;"
                    f"max-height:12rem; overflow-y:auto"):
                for st in chosen:
                    ui.label(st).style(
                        f"font-family:{TYPOGRAPHY['mono']};"
                        f"font-size:{TYPOGRAPHY['size_xs']}; padding:3px 8px")
            note = ui.label().style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['warning']}")
            swap = ui.checkbox(f"Replace these {len(chosen)} step(s) in this test case "
                               f"with 'call <name>' and save", value=replace_in_flow) \
                .props("dense")

            async def do_save(overwrite: bool = False) -> None:
                if not check_name():
                    ui.notify("That name cannot be used — see the note under "
                              "the box", type="warning")
                    return
                try:
                    await api.save_step_group((name.value or "").strip(), chosen,
                                              self.platform, overwrite=overwrite)
                except api.ApiError as e:
                    if e.status == 409 and not overwrite:
                        note.set_text(f"{e.detail} ")
                        ui.button("Replace it", on_click=lambda: do_save(True)) \
                            .props("flat dense color=negative")
                        return
                    note.set_text(str(e.detail)[:200])
                    return
                dialog.close()
                gname = (name.value or "").strip()
                if swap.value and self.selection:
                    idxs = sorted(self.selection)
                    first = idxs[0]
                    for i in reversed(idxs):
                        del self.steps[i - 1]
                    self.steps.insert(first - 1, f"call {gname}")
                    self.step_meta = {}
                    try:
                        await self._persist()
                        self.dirty = False
                        ui.notify(f"Saved step group and replaced {len(idxs)} step(s) "
                                  f"with 'call {gname}'", type="positive")
                    except api.ApiError as e:
                        self.dirty = True
                        ui.notify(f"Group saved, but this test case could not be "
                                  f"saved: {e.detail}", type="warning")
                else:
                    ui.notify(f"Saved step group — type 'call {gname}' to reuse it",
                              type="positive")
                self.selection.clear()
                self.select_mode = False
                await self.render_editor()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Save group", on_click=lambda: do_save(False)) \
                    .props("unelevated")
        dialog.open()

    def move_step_to(self, src: int, dst: int) -> None:
        """Drag-and-drop reorder."""
        if src == dst:
            return
        self.dirty = True
        self.steps.insert(dst - 1, self.steps.pop(src - 1))
        self.step_meta = {}
        self.selection.clear()
        ui.timer(0.01, self.render_editor, once=True)

    # ── step mutation ───────────────────────────────────────────────────────
    def add_step(self, text: str) -> None:
        self.steps.append(text)
        self.dirty = True
        ui.timer(0.01, self.render_editor, once=True)

    def _insert_at(self, at: int, text: str) -> int:
        """
        Put a new step in at a 0-based position and say where it landed, or -1.

        Selection and per-step metadata are dropped because both are keyed by
        position, and every position from here on has just moved — stale entries
        would tick the wrong rows and label the wrong steps.

        Shared by the row composer and the review panel so that a step added
        from a suggestion and a step typed by hand are the same operation.
        """
        text = (text or "").strip()
        if not text:
            return -1
        at = max(0, min(at, len(self.steps)))
        self.steps.insert(at, text)
        self.step_meta = {}
        self.selection.clear()
        self.dirty = True
        return at

    def insert_step(self, at: int, text: str) -> None:
        """Insert from the composer, and leave it open ready for the next one."""
        pos = self._insert_at(at, text)
        if pos < 0:
            return
        # Left open one place further down, so a run of steps can be typed
        # straight through without reaching for the row buttons again.
        self.compose_at = pos + 1
        ui.notify(f"Inserted at step {pos + 1} — Save to keep it", type="positive")
        ui.timer(0.01, self.render_editor, once=True)

    def edit_step(self, index: int, text: str) -> None:
        if not text:
            ui.timer(0.01, self.render_editor, once=True)
            return
        old_call = self._call_target(self.steps[index - 1])
        new_call = self._call_target(text)
        if old_call and new_call and old_call != new_call:
            # A `call` line is never retyped here — the group is renamed on the
            # Step Groups page, which rewrites every test case that uses it.
            ui.notify("Step groups are renamed on the Step Groups page (use the edit "
                      "icon on this row) — every test case is updated there",
                      type="warning", timeout=6000)
        elif text != self.steps[index - 1]:
            self.steps[index - 1] = text
            self.dirty = True
        ui.timer(0.01, self.render_editor, once=True)

    @staticmethod
    def _call_target(step: str) -> str:
        import re
        m = re.match(r"^call\s+(.+?)\s*$", (step or "").strip(), re.I)
        return m.group(1) if m else ""

    def delete_step(self, index: int) -> None:
        if not 0 < index <= len(self.steps):
            return
        self._confirm(f"Remove step {index}?", [f"{index}  {self.steps[index - 1]}"],
                      lambda: self._delete_step_now(index))

    def _delete_step_now(self, index: int) -> None:
        self.steps.pop(index - 1)
        self.dirty = True
        self.step_meta = {}          # positions shifted; stale metadata would mislead
        ui.timer(0.01, self.render_editor, once=True)

    def move_step(self, src: int, dst: int) -> None:
        self.steps.insert(dst - 1, self.steps.pop(src - 1))
        self.dirty = True
        self.step_meta = {}
        ui.timer(0.01, self.render_editor, once=True)

    # ── actions ─────────────────────────────────────────────────────────────
    async def _persist(self, overwrite: bool = False) -> None:
        """Write the steps. Raises ApiError; a concurrent edit (409) also offers
        Reload / Overwrite instead of the second save silently erasing the first."""
        try:
            res = await api.save_project(
                self.selected, self.steps, self.platform,
                expected_mtime=None if overwrite else getattr(self, "file_mtime", None),
                create_if_missing=getattr(self, "_is_new", False))
        except api.ApiError as e:
            if e.status == 409:
                self._conflict_dialog(str(e.detail))
            elif e.status == 404:
                e.detail = (f"'{self.selected}' no longer exists (deleted or renamed by someone "
                            f"else). Copy your steps, then create it again.")
            raise
        self.file_mtime = (res or {}).get("mtime", getattr(self, "file_mtime", None))
        self._is_new = False

    def _conflict_dialog(self, detail: str) -> None:
        with self.dialog_host if getattr(self, "dialog_host", None) else ui.element("div"):
            dialog = ui.dialog().props("persistent")
            with dialog, ui.card().style("width:32rem"):
                ui.label("Someone else saved this test case").style(
                    f"font-weight:{TYPOGRAPHY['weight_bold']}")
                ui.label(detail).style(f"font-size:{TYPOGRAPHY['size_sm']}")

                async def reload() -> None:
                    dialog.close()
                    self.dirty = False
                    await self.open_project(self.selected)

                async def overwrite() -> None:
                    dialog.close()
                    try:
                        await self._persist(overwrite=True)
                        self.dirty = False
                        ui.notify(f"Saved {self.selected} (their changes were replaced)", type="warning")
                        await self.render_editor()
                    except api.ApiError as e:
                        ui.notify(f"Save failed: {e.detail}", type="negative")

                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Cancel", on_click=dialog.close).props("flat")
                    ui.button("Reload theirs (lose mine)", on_click=reload).props("flat color=negative")
                    ui.button("Overwrite with mine", on_click=overwrite).props("unelevated")
        dialog.open()

    async def save(self) -> bool:
        """Save; True when it worked (callers must not carry on after a failed save)."""
        if not self.selected:
            return False
        try:
            await self._persist()
        except api.ApiError as e:
            if e.status != 409:
                ui.notify(f"Save failed: {e.detail}", type="negative")
            return False
        self.dirty = False
        ui.notify(f"Saved {self.selected}", type="positive")
        await self.render_editor()      # drop the "unsaved" badge
        await self.load()
        self.render_list()
        return True

    def run(self) -> None:
        if self.selected:
            ui.navigate.to(f"/run?flow={self.selected}&platform={self.platform}")

    def new_dialog(self) -> None:
        from ui.pages.platform.new_test_case import new_test_case_dialog
        new_test_case_dialog(self.platform, on_created=self._after_create)

    async def _after_create(self, name: str, steps: list[str],
                            meta: dict[int, dict] | None = None) -> None:
        self.selected = name
        self.steps = steps
        self.step_meta = meta or {}
        self._is_new = True            # the first save may create the file
        self.file_mtime = None
        self.compose_at = None
        self.selection.clear()
        self.collapsed.clear()
        # Generated steps exist only in this editor until Save writes a file.
        # A whole drafted testcase was lost that way, with nothing on screen
        # saying it was at risk.
        self.dirty = bool(steps)
        if self.current_folder:
            try:
                await api.assign_folder([name], self.current_folder)
            except api.ApiError:
                pass
        await self.load()
        self.render_list()
        await self.render_editor()


def _focus_line(line: int) -> None:
    """Scroll the step list to one step and flash it (opened from a report)."""
    ui.run_javascript(
        "(function(){var n=0;var t=setInterval(function(){"
        "var el=document.querySelector('#steps-scroll [data-step=\"%d\"]');"
        "if(el){clearInterval(t);el.scrollIntoView({block:'center'});"
        "el.style.transition='box-shadow .3s';el.style.boxShadow='inset 0 0 0 2px #ef4444';"
        "setTimeout(function(){el.style.boxShadow='';},4000);}"
        "if(++n>80){clearInterval(t);}},150);})();" % int(line))


async def render(platform: str, flow: str = "", line: int = 0) -> None:
    page = TestCasesPage(platform)
    await page.load()
    # Known before the first paint so the top bar's Run button names THIS test
    # case rather than whatever ran last.
    if flow and flow in page.projects:
        page.selected = flow
    page.render()
    # Reopen whatever the URL names. The editor's state lived only in server
    # memory, so a reload — or a websocket reconnect after the server restarted —
    # dropped back to "Select a test case" with the author's place lost.
    if flow and flow in page.projects:
        await page.open_project(flow)
        if line:
            # Reports name the FILE line; the editor numbers steps. Map it,
            # else "line 26" flashed step 26 — a different step, or nothing.
            lines = getattr(page, "file_lines", []) or []
            idx = lines.index(int(line)) + 1 if int(line) in lines else int(line)
            _focus_line(idx)
    else:
        # Nothing named in the URL: reopen what was last open rather than
        # showing an empty pane. Deferred by a tick because reading browser
        # storage needs a connected client.
        ui.timer(0.3, page.restore_last, once=True)


def _selector_from_record(rec: dict | str | None) -> str:
    """Turn a saved locator record into the selector string shown in editors."""
    if isinstance(rec, str):
        return rec
    if not isinstance(rec, dict):
        return ""
    if rec.get("custom_xpath"):
        return rec["custom_xpath"]
    if rec.get("xpath"):
        return rec["xpath"]
    sels = rec.get("selectors")
    if isinstance(sels, list) and sels and isinstance(sels[0], dict):
        return sels[0].get("value", "")
    return ""


def _locator_detail_index(groups: dict) -> dict[str, dict[str, str]]:
    """Flatten grouped locators into {name: {group, selector}} for step menus."""
    out: dict[str, dict[str, str]] = {}
    if not isinstance(groups, dict):
        return out
    for group, locators in groups.items():
        if not isinstance(locators, dict):
            continue
        for name, rec in locators.items():
            if not name:
                continue
            out[name] = {
                "group": str(group),
                "selector": _selector_from_record(rec),
            }
    return out
