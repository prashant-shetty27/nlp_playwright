"""
ui/pages/data/datasets_panel.py — the "Data sets" tab of Test Data.

Upload an Excel / CSV table once; any test loops over it with

    for each row in <name> [rows 2 to 5] [where <column> is <value>]
        … ${column} …
    end for

Each card shows what a tester needs to write that loop: the name, every
column as the ${…} it becomes, how many rows there are, and who uploaded /
last replaced it.
"""
from __future__ import annotations

from nicegui import ui

from ui import api_client as api
from ui.theme import COLORS, TYPOGRAPHY


class DatasetsPanel:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self.box = None
        self._pending: tuple[str, bytes] | None = None

    async def load(self) -> None:
        try:
            self.items = await api.datasets()
        except api.ApiError as e:
            ui.notify(f"Could not read data sets: {e.detail}", type="negative")
            self.items = []

    def render(self) -> None:
        with ui.column().classes("w-full gap-3"):
            with ui.row().classes("w-full items-center gap-3"):
                ui.label("Upload an Excel or CSV table. First row = column headings, "
                         "every other row = one set of inputs. A workbook with "
                         "several sheets becomes one data set per sheet.").style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']};"
                    "max-width:46rem")
                ui.space()
                ui.upload(label="Upload .xlsx / .csv", on_upload=self._on_upload,
                          auto_upload=True) \
                    .props('accept=".xlsx,.xlsm,.csv" flat dense').style("max-width:20rem")
            with ui.expansion("How to use a data set in a test", icon="help_outline") \
                    .classes("w-full").style(
                        f"border:1px solid {COLORS['border']}; border-radius:6px"):
                ui.code(
                    "for each row in city_list                      ← every row\n"
                    "for each row in city_list rows 2 to 5          ← a range\n"
                    "for each row in city_list from row 3           ← row 3 to the end\n"
                    "for each row in city_list first 10 rows\n"
                    "for each row in city_list where platform is mobilesite\n"
                    "    enter ${city} in search_city_box           ← any column, by its name\n"
                    "    enter ${mobile} in mobile_number_field\n"
                    "    skip to next row if ${city} is Pune\n"
                    "    stop loop if element no_results_text is visible\n"
                    "end for", language="text").classes("w-full")
            self.box = ui.column().classes("w-full gap-2")
        self._draw()

    def _draw(self) -> None:
        self.box.clear()
        with self.box:
            if not self.items:
                ui.label("No data sets yet.").style(
                    f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_sm']}")
                return
            for d in self.items:
                self._card(d)

    def _card(self, d: dict) -> None:
        with ui.column().classes("w-full gap-1").style(
                f"border:1px solid {COLORS['border']}; border-radius:6px; padding:10px 12px"):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.icon("table_view").style(f"color:{COLORS['primary']}")
                ui.label(d["name"]).style(
                    f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_sm']};"
                    f"font-weight:{TYPOGRAPHY['weight_bold']}")
                ui.label(f"{d.get('row_count', 0)} rows · {len(d.get('columns', []))} columns") \
                    .style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
                ui.space()
                ui.button(icon="visibility").props("flat dense size=sm") \
                    .on("click", lambda n=d["name"]: self._preview(n)).tooltip("Preview rows")
                ui.button(icon="content_copy").props("flat dense size=sm") \
                    .on("click", lambda n=d["name"]: self._copy(n)) \
                    .tooltip("Copy 'for each row in …' block")
                ui.button(icon="delete_outline").props("flat dense size=sm color=negative") \
                    .on("click", lambda n=d["name"]: self._delete(n)).tooltip("Remove")
            with ui.row().classes("w-full gap-1").style("flex-wrap:wrap"):
                for h, c in zip(d.get("headings", []), d.get("columns", [])):
                    ui.label("${" + c + "}").style(
                        f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']};"
                        f"background:{COLORS['accent']}1A; color:{COLORS['accent']};"
                        "border-radius:4px; padding:1px 6px").tooltip(f"Column: {h}")
            who = []
            if d.get("created_by") or d.get("created_at"):
                who.append(f"Created by {d.get('created_by') or '—'} · {d.get('created_at', '')[:16]}")
            if d.get("updated_by") or d.get("updated_at"):
                who.append(f"Last edited by {d.get('updated_by') or '—'} · {d.get('updated_at', '')[:16]}")
            if who:
                ui.label("   |   ".join(who).replace("T", " ")).style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

    def _copy(self, name: str) -> None:
        d = next((x for x in self.items if x["name"] == name), {})
        cols = d.get("columns", [])[:2]
        body = "\n".join(f"    enter ${{{c}}} in <element>" for c in cols) or "    <steps>"
        text = f"for each row in {name}\n{body}\nend for"
        ui.clipboard.write(text)
        ui.notify("Copied — paste it into a test case", type="positive")

    async def _preview(self, name: str) -> None:
        try:
            d = await api.dataset(name, limit=200)
        except api.ApiError as e:
            ui.notify(str(e.detail), type="negative")
            return
        cols = [{"name": "row_number", "label": "#", "field": "row_number", "align": "left"}]
        cols += [{"name": c, "label": f"{h}  → ${{{c}}}", "field": c, "align": "left"}
                 for h, c in zip(d["headings"], d["columns"])]
        rows = [dict(zip(d["columns"], r), row_number=i)
                for i, r in enumerate(d.get("rows", []), 1)]
        dialog = ui.dialog()
        with dialog, ui.card().style("width:min(92vw,70rem); max-width:92vw"):
            with ui.row().classes("w-full items-center"):
                ui.label(f"{name} — {d.get('row_count', 0)} rows").style(
                    f"font-weight:{TYPOGRAPHY['weight_bold']}")
                ui.space()
                ui.button(icon="close", on_click=dialog.close).props("flat dense")
            if d.get("row_count", 0) > len(rows):
                ui.label(f"Showing the first {len(rows)} rows.").style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            ui.table(columns=cols, rows=rows, row_key="row_number",
                     pagination=25).props("dense flat").classes("w-full")
        dialog.open()

    async def _on_upload(self, e) -> None:
        name = getattr(e.file, "name", "upload")
        data = await e.file.read()
        await self._send(name, data, replace=False)

    async def _send(self, name: str, data: bytes, replace: bool) -> None:
        try:
            res = await api.upload_dataset(name, data, replace=replace)
        except api.ApiError as err:
            if err.status == 409 and isinstance(err.detail, dict):
                self._confirm_replace(name, data, err.detail)
                return
            ui.notify(str(err.detail), type="negative", timeout=8000)
            return
        saved = res.get("saved", [])
        ui.notify("Saved " + ", ".join(f"{s['name']} ({s['row_count']} rows)" for s in saved),
                  type="positive")
        await self.load()
        self._draw()

    def _confirm_replace(self, name: str, data: bytes, detail: dict) -> None:
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:30rem"):
            ui.label(detail.get("message", "Already exists.")).style(
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            ui.label(detail.get("why", "")).style(
                f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")

            async def go() -> None:
                dialog.close()
                await self._send(name, data, replace=True)

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Replace it", on_click=go).props("unelevated color=negative")
        dialog.open()

    def _delete(self, name: str) -> None:
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:28rem"):
            ui.label(f"Remove data set {name}?").style(
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            ui.label(f"Any 'for each row in {name}' loop will fail until it is "
                     "uploaded again.").style(
                f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")

            async def go() -> None:
                try:
                    await api.delete_dataset(name)
                except api.ApiError as err:
                    ui.notify(str(err.detail), type="negative")
                    return
                dialog.close()
                ui.notify(f"Removed {name}", type="positive")
                await self.load()
                self._draw()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Remove", on_click=go).props("unelevated color=negative")
        dialog.open()
