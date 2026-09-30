"""
ui/pages/data/variables.py — the values tests use, in one place.

Why this screen matters
-----------------------
Without it the store was invisible: the API worked and the step editor offered
values, but nothing showed WHAT was stored, so the only way to see a value was
to guess its name while typing a step.

Two scopes, deliberately distinct:

  Global      one value everywhere — a test mobile, a static OTP, a username.
  Environment the same name resolving differently per environment: base_url is
              staging2 on staging and www on live, while every flow just says
              ${base_url}.

Values are shown IN FULL here. This is the screen you come to in order to check
what a test will actually use, and a masked `93---210` cannot answer that — it
was being read off the file instead, which defeats the point of the screen.
Everywhere a value could leak by accident — the step editor, the log, the report,
the run response — it stays masked; this one place shows it.

A credential — anything named like a password, token or api_key — is declared
here but never stores a value: it comes from .env at run time. Writing one into
a repository file is the thing the store exists to prevent, so those show as
"from .env" and nothing else, here included.
"""
from __future__ import annotations

from nicegui import ui

from ui import api_client as api
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.theme import COLORS, TYPOGRAPHY


class TestDataPage:
    #: Every environment at once, which is the useful default: the question
    #: this screen answers is "what is stored", and an environment filter
    #: applied before you have asked anything hides most of the answer.
    ALL = "*"

    def __init__(self) -> None:
        self.environment = self.ALL
        self.environments: list[str] = []
        self.values: dict = {}
        self.rows: list[dict] = []
        self.platforms: list[dict] = []

    async def load(self) -> None:
        try:
            self.platforms = await api.platforms()
        except api.ApiError:
            self.platforms = []
        try:
            data = await api.testdata(self.environment)
            self.environments = data.get("environments", [])
            self.values = data.get("values", {})
            self.rows = data.get("rows", [])
        except api.ApiError as e:
            ui.notify(f"Could not read test data: {e.detail}", type="negative")
            self.values = {}
            self.rows = []

    def render(self) -> None:
        sidebar(active="/data/variables", platforms=self.platforms)
        topbar(["Manage", "Test Data"], platforms=self.platforms)
        with ui.column().classes("w-full gap-3 p-4"):
            self.body = ui.column().classes("w-full gap-3")
        self._draw()

    def _draw(self) -> None:
        self.body.clear()
        with self.body:
            with ui.row().classes("w-full items-center gap-3"):
                ui.select({self.ALL: "All environments", "": "Global only"}
                          | {e: e for e in self.environments},
                          value=self.environment, label="Environment",
                          on_change=lambda e: self._switch(e.value)) \
                    .props("outlined dense").style("width:16rem")
                ui.space()
                # Bound through a lambda, NOT passed directly: NiceGUI hands a
                # click event to any handler that takes an argument, so
                # `on_click=self._add_dialog` arrived as name=<ClickEvent>. That
                # made `editing` true — so Add value opened the EDIT form, with
                # the name locked and an event object sitting in the field.
                ui.button("Add value", icon="add",
                          on_click=lambda: self._add_dialog()) \
                    .props("unelevated")

            ui.label("A value saved here is offered while writing a step — type "
                     "part of the name or of the value itself. Picking it writes "
                     "${name} into the step, never the value, so the number stays "
                     "out of the test file, the report and the screenshot.").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']};"
                f"max-width:56rem")
            ui.label("OTP is automatic — write ${otp} in the step. For the "
                     "common test numbers (TEST_MOBILES) it uses the static OTP "
                     "for the platform (Website / Mobile Site); for any other "
                     "number it fetches the OTP from the OTP portal. Nothing to "
                     "store here.").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']};"
                f"max-width:56rem")

            if not self.rows:
                ui.label("Nothing stored yet.").style(
                    f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_sm']}")
                return

            with ui.column().classes("w-full gap-0").style(
                    f"border:1px solid {COLORS['border']}; border-radius:6px"):
                self._header()
                # Grouped by name so the same name in two environments reads as
                # one value with two settings, which is what it is.
                for entry in sorted(self.rows,
                                    key=lambda e: (e["name"],
                                                   e.get("environment", ""))):
                    self._row(entry["name"], entry)

    #: One grid, used by the header and every row, so the columns cannot drift
    #: apart. A flex row with fixed-width labels let a long name spill over the
    #: Scope column (prod_Hotel_Equipment_Manufacturers ran into "global");
    #: a grid cell clips its own content instead.
    GRID = ("display:grid; grid-template-columns:"
            "minmax(10rem,17rem) 5.5rem 7rem minmax(0,1fr) auto;"
            "column-gap:12px; align-items:center; width:100%")

    #: Clip to one line with an ellipsis; the full text is in the tooltip.
    CLIP = "overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0"

    def _header(self) -> None:
        with ui.element("div").style(
                f"{self.GRID}; padding:6px 10px;"
                f"border-bottom:1px solid {COLORS['border']};"
                f"background:{COLORS['surface_alt']}"):
            for text in ("Name", "Scope", "Environment", "Value", ""):
                ui.label(text).style(
                    f"font-size:{TYPOGRAPHY['size_xs']};"
                    f"color:{COLORS['text_muted']}; font-weight:"
                    f"{TYPOGRAPHY['weight_bold']}; text-transform:uppercase;"
                    f"letter-spacing:.04em")

    def _row(self, name: str, entry: dict) -> None:
        mono = f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_sm']}"
        with ui.element("div").style(
                f"{self.GRID}; padding:7px 10px;"
                f"border-bottom:1px solid {COLORS['border']}"):
            # The name IS the reference: steps write ${name}. Shown once, with
            # the ${...} form in the tooltip, instead of a second column that
            # repeated it and squeezed the value.
            ui.label(name).style(f"{mono}; {self.CLIP}") \
                .tooltip("In a step: ${" + name + "}")
            scope = entry.get("scope", "global")
            colour = COLORS["primary"] if scope == "global" else COLORS["accent"]
            with ui.element("div"):
                ui.label(scope).style(
                    f"background:{colour}1A; color:{colour}; border-radius:4px;"
                    f"padding:1px 7px; font-size:{TYPOGRAPHY['size_xs']};"
                    f"display:inline-block")
            env = entry.get("environment", "")
            ui.label(env or "—").style(
                f"{mono}; {self.CLIP};"
                f"color:{COLORS['text'] if env else COLORS['text_muted']}")
            if entry.get("is_secret"):
                ui.label(f"{entry.get('display', '')}  · credential, from .env") \
                    .style(f"{mono}; {self.CLIP}; color:{COLORS['warning']}")
            else:
                # In full (tooltip) — see the module docstring. One line in the
                # table so a long URL does not turn a row into a paragraph.
                value = entry.get("value", "") or "—"
                ui.label(value).style(f"{mono}; {self.CLIP}").tooltip(value)
            with ui.row().classes("gap-0 no-wrap"):
                if not entry.get("is_secret"):
                    ui.button(icon="edit").props("flat dense size=xs") \
                        .on("click", lambda n=name, e=entry: self._add_dialog(n, e)) \
                        .tooltip("Change this value")
                ui.button(icon="drive_file_rename_outline").props("flat dense size=xs") \
                    .on("click", lambda n=name, e=entry: self._rename_dialog(n, e)) \
                    .tooltip("Rename it — every ${reference} is rewritten too")
                ui.button(icon="delete_outline").props("flat dense size=xs color=negative") \
                    .on("click", lambda n=name, e=entry: self._delete(n, e)) \
                    .tooltip("Remove it")

    def _rename_dialog(self, name: str, entry: dict) -> None:
        """
        Rename a value, after showing every file it will rewrite.

        The name is referenced from flows as ${name} and from their Params
        headers, so renaming it in the store alone would leave every test
        pointing at something that no longer exists. The preview is fetched
        first and listed; nothing moves until it is confirmed.
        """
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:34rem"):
            ui.label(f"Rename {name}").style(
                f"font-size:{TYPOGRAPHY['size_lg']};"
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            box = ui.input("New name", value=name,
                           placeholder="staging2_base_url") \
                .props("outlined dense").classes("w-full")
            impact = ui.column().classes("w-full gap-1")
            note = ui.label().style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['danger']}")

            async def preview() -> None:
                impact.clear()
                note.set_text("")
                new = (box.value or "").strip()
                if not new or new == name:
                    return
                try:
                    res = await api.rename_testdata(
                        name, new, entry.get("scope", "global"),
                        entry.get("environment", ""), apply=False)
                except api.ApiError as e:
                    note.set_text(str(e.detail)[:200])
                    return
                with impact:
                    ui.label("This will change:").style(
                        f"font-size:{TYPOGRAPHY['size_sm']};"
                        f"font-weight:{TYPOGRAPHY['weight_medium']}")
                    for ch in res.get("changes", []):
                        ui.label(f"• {ch.get('kind','')} — {ch.get('file','')} "
                                 f"{ch.get('detail','')}").style(
                            f"font-size:{TYPOGRAPHY['size_xs']};"
                            f"font-family:{TYPOGRAPHY['mono']};"
                            f"color:{COLORS['text_muted']}")

            async def go() -> None:
                new = (box.value or "").strip()
                if not new or new == name:
                    dialog.close()
                    return
                try:
                    res = await api.rename_testdata(
                        name, new, entry.get("scope", "global"),
                        entry.get("environment", ""), apply=True)
                except api.ApiError as e:
                    note.set_text(str(e.detail)[:200])
                    return
                dialog.close()
                ui.notify(f"Renamed to {res.get('new_name', new)}", type="positive")
                await self._reload()

            box.on_value_change(preview)
            ui.timer(0.05, preview, once=True)
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Rename", on_click=go).props("unelevated")
        dialog.open()

    def _switch(self, env: str) -> None:
        self.environment = env or ""
        ui.timer(0.01, self._reload, once=True)

    async def _reload(self) -> None:
        await self.load()
        self._draw()

    def _add_dialog(self, name: str = "", entry: dict | None = None) -> None:
        editing = bool(name)
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:32rem"):
            ui.label("Change value" if editing else "Add a value").style(
                f"font-size:{TYPOGRAPHY['size_lg']};"
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            n = ui.input("Name", value=name, placeholder="test_mobile") \
                .props("outlined dense").classes("w-full")
            if editing:
                # Locked here on purpose: changing the NAME has to rewrite every
                # ${reference} in every flow, which is a different operation
                # with a different preview. The rename button on the row does
                # that; this dialog only changes the value.
                n.props("readonly")
                ui.label("To change the name, use the rename button on the row "
                         "— it rewrites every step that references it.").style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            v = ui.input("Value", value=(entry or {}).get("value", "")) \
                .props("outlined dense").classes("w-full")
            scope = ui.select({"global": "Global — every platform and environment",
                               "environment": "This environment only"},
                              value=(entry or {}).get("scope", "global"),
                              label="Scope").props("outlined dense").classes("w-full")
            # "All environments" is a way of LOOKING at the store, not a place
            # to write to, so it can never be this select's value — it was
            # passed straight through and Quasar rejected it, which killed the
            # dialog before it drew and made Add value do nothing at all.
            options = {e: e for e in self.environments} or {"local": "local"}
            current = (entry or {}).get("environment", "")
            chosen = next((e for e in (current, self.environment) if e in options),
                          next(iter(options)))
            envsel = ui.select(options, value=chosen, label="Environment") \
                .props("outlined dense").classes("w-full")
            note = ui.label().style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['warning']}")
            #: Where a name clash is explained, with the way out beside it.
            conflict = ui.column().classes("w-full gap-1")

            async def save(force: bool = False) -> None:
                try:
                    res = await api.set_testdata(
                        (n.value or "").strip(), v.value or "",
                        scope=scope.value or "global",
                        environment=envsel.value or "", force=force,
                        updating=editing)
                except api.ApiError as e:
                    detail = e.detail
                    # A name clash comes back as 409 with a structured body. It
                    # was being sliced like a string, which raises on a dict —
                    # so the one check that stops you overwriting somebody
                    # else's value threw silently and the dialog just sat there.
                    if e.status == 409 and isinstance(detail, dict) and not force:
                        conflict.clear()
                        with conflict:
                            ui.label(detail.get("message", "Already stored.")).style(
                                f"font-size:{TYPOGRAPHY['size_sm']};"
                                f"color:{COLORS['danger']};"
                                f"font-weight:{TYPOGRAPHY['weight_medium']}")
                            if detail.get("why"):
                                ui.label(detail["why"]).style(
                                    f"font-size:{TYPOGRAPHY['size_xs']};"
                                    f"color:{COLORS['text_muted']}")
                            for c in detail.get("conflicts", []):
                                ui.label(f"• {c.get('message','')}").style(
                                    f"font-size:{TYPOGRAPHY['size_xs']};"
                                    f"color:{COLORS['text_muted']}")
                            kinds = {c.get("kind") for c in detail.get("conflicts", [])}
                            with ui.row().classes("gap-2"):
                                if "duplicate_value" in kinds and "duplicate_name" not in kinds:
                                    # Same value, different name: the fix is
                                    # usually to reference the existing name.
                                    ui.button("Save anyway — two names, one value",
                                              on_click=lambda: save(True)) \
                                        .props("flat dense color=negative")
                                    ui.button("Cancel", on_click=dialog.close) \
                                        .props("flat dense")
                                else:
                                    ui.button("Replace it",
                                              on_click=lambda: save(True)) \
                                        .props("flat dense color=negative")
                                    ui.button("Keep both — rename this one",
                                              on_click=lambda: n.run_method("focus")) \
                                        .props("flat dense")
                        return
                    note.set_text(str(detail)[:200])
                    return
                dialog.close()
                if res.get("note"):
                    ui.notify(res["note"], type="warning", timeout=7000)
                else:
                    ui.notify(f"Saved {res['name']} — shows as {res.get('display','')}",
                              type="positive")
                await self._reload()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Save", on_click=save).props("unelevated")
        dialog.open()

    def _delete(self, name: str, entry: dict) -> None:
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:28rem"):
            ui.label(f"Remove {name}?").style(
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            ui.label("Any step that references ${" + name + "} will stop resolving "
                     "until the value is supplied another way.").style(
                f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")

            async def go() -> None:
                try:
                    await api.delete_testdata(name, entry.get("scope", "global"),
                                              entry.get("environment", ""))
                except api.ApiError as e:
                    ui.notify(e.detail, type="negative")
                    return
                dialog.close()
                ui.notify(f"Removed {name}", type="positive")
                await self._reload()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Remove", on_click=go).props("unelevated color=negative")
        dialog.open()


async def render() -> None:
    page = TestDataPage()
    await page.load()
    page.render()
