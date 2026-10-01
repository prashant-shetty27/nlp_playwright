"""
ui/components/folder_picker.py — "Save in folder", used wherever a test case is created.

A new test case used to land silently in whichever folder happened to be open
(or Unfiled), so it had to be hunted down afterwards. Every create path — Blank,
From spreadsheet, the AI drafter — now shows this picker: pre-filled with the
folder you are working in, required, with "+ New folder" beside it.
"""
from __future__ import annotations

from nicegui import ui

from ui import api_client as api
from ui.theme import COLORS, TYPOGRAPHY


class FolderPicker:
    def __init__(self, value: str = "", module: str = "") -> None:
        self.module = module            # only this module's folders; new ones belong to it
        self.folders: list[str] = []
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            self.select = ui.select({value: value} if value else {}, value=value or None,
                                    label="Save in folder *", with_input=True) \
                .props("outlined dense").classes("flex-grow")
            ui.button("New folder", icon="create_new_folder", on_click=self._new_folder) \
                .props("flat dense no-caps")
        self.note = ui.label("").style(
            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['danger']}; min-height:0")

    async def load(self) -> None:
        try:
            self.folders = sorted((await api.folders(self.module)).get("folders", []))
        except api.ApiError:
            self.folders = []
        current = self.select.value
        self.select.set_options({f: f for f in self.folders}, value=current
                                if current in self.folders else None)

    @property
    def value(self) -> str:
        return (self.select.value or "").strip()

    def require(self) -> str | None:
        """The chosen folder, or None after saying why nothing was created."""
        if not self.value:
            self.note.set_text("Pick the folder this test case goes in (or create one).")
            ui.notify("Choose a folder first", type="warning")
            return None
        self.note.set_text("")
        return self.value

    def _new_folder(self) -> None:
        dialog = ui.dialog()
        with dialog, ui.card().style("width:26rem"):
            ui.label("New folder").style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
            parent = ui.select({"": "— top level —", **{f: f for f in self.folders}},
                               value=self.value if self.value in self.folders else "",
                               label="Inside").props("outlined dense").classes("w-full")
            name = ui.input("Folder name", placeholder="PDP").props("outlined dense autofocus") \
                .classes("w-full")

            async def create() -> None:
                n = (name.value or "").strip().strip("/")
                if not n:
                    ui.notify("Give the folder a name", type="warning")
                    return
                path = f"{parent.value}/{n}" if parent.value else n
                try:
                    path = (await api.create_folder(path, self.module)).get("path", path)
                except api.ApiError as e:
                    ui.notify(str(e.detail), type="negative")
                    return
                dialog.close()
                await self.load()
                self.select.set_value(path)
                self.note.set_text("")

            name.on("keydown.enter", create)
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Create", on_click=create).props("unelevated")
        dialog.open()
