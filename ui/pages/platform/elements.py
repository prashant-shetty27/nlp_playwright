"""
ui/pages/platform/elements.py — every saved element, editable in one place.

Until now there was no screen: 146 elements existed, the API could read them, and
the only way to see one was to guess its name while typing a step. Editing meant
hand-editing JSON.

What this screen is for, beyond looking at a list:

  * Editing an element's SELECTOR fixes every test at once — steps reference
    elements by name, so nothing else has to change. That is the whole point of
    naming them.
  * RENAMING is different. The name is written into every step that clicks it, so
    a rename has to rewrite those steps and every step group too. The preview
    shows exactly what will change before anything moves.
  * Duplicates are refused on save — same name, same selector under another name,
    and the underscore twin (search_box vs searchbox). The one place it is cheap
    to stop them is before they enter.

Read-only elements — the ones the spy recorded — are shown but not editable, and
say so. Rewriting the recording from here would leave it and the database
permanently disagreeing.

`?edit=<name>` opens straight into that element, which is how the step editor
sends you here from a locator token.
"""
from __future__ import annotations

import inspect

from nicegui import ui

from ui import api_client as api
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.theme import COLORS, TYPOGRAPHY


class ElementsPage:
    def __init__(self, platform: str, focus: str = "") -> None:
        self.platform = platform
        self.focus = focus
        self.groups: dict = {}
        self.conflicts: dict = {}
        self.platforms: list[dict] = []
        self.filter = ""

    async def load(self) -> None:
        try:
            self.platforms = await api.platforms()
        except api.ApiError:
            self.platforms = []
        try:
            self.groups = await api.locators_for(self.platform)
        except api.ApiError as e:
            ui.notify(f"Could not read elements: {e.detail}", type="negative")
            self.groups = {}
        try:
            self.conflicts = await api.locator_conflicts(self.platform)
        except api.ApiError:
            self.conflicts = {}

    def render(self) -> None:
        sidebar(active=f"/platform/{self.platform}/elements", platforms=self.platforms)
        topbar(["Author", "Elements"], platforms=self.platforms,
               platform=self.platform,
               on_platform_change=lambda p: ui.navigate.to(f"/platform/{p}/elements"))
        with ui.column().classes("w-full gap-2 p-4"):
            self.body = ui.column().classes("w-full gap-2")
        self._draw()
        if self.focus:
            ui.timer(0.3, lambda: self._open_focused(), once=True)

    def _open_focused(self) -> None:
        for group, els in self.groups.items():
            if self.focus in els:
                self._edit_dialog(group, self.focus, els[self.focus])
                return
        ui.notify(f"'{self.focus}' is not in this platform's element list.",
                  type="warning")

    # ── rendering ───────────────────────────────────────────────────────────
    def _draw(self) -> None:
        """
        Draw the header once and the results into their own container.

        Filtering redraws only the results. Rebuilding this whole column on every
        keystroke destroyed the search box mid-word — the same defect the test
        case list had.
        """
        self.body.clear()
        total = sum(len(v) for v in self.groups.values())
        with self.body:
            with ui.row().classes("w-full items-center gap-3"):
                ui.input(placeholder="Search elements or selectors",
                         value=self.filter,
                         on_change=lambda e: self._set_filter(e.value)) \
                    .props("outlined dense clearable").style("width:22rem")
                ui.label(f"{total} element(s) in {len(self.groups)} group(s)").style(
                    f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_sm']}")
                ui.space()
                ui.button("Add element", icon="add",
                          on_click=lambda: open_create_element_dialog(
                              self.platform,
                              on_saved=lambda _saved: self._reload(),
                          )) \
                    .props("unelevated")

            if self.conflicts:
                with ui.row().classes("w-full items-center gap-2").style(
                        f"background:{COLORS['warning']}14;"
                        f"border:1px solid {COLORS['warning']}55;"
                        f"border-radius:6px; padding:6px 10px"):
                    ui.icon("info").style(f"color:{COLORS['warning']}")
                    ui.label(f"{len(self.conflicts)} name(s) are defined more than "
                             f"once. The runner uses the first match, which may not "
                             f"be the one a step meant.").style(
                        f"font-size:{TYPOGRAPHY['size_sm']}")

            self.results = ui.column().classes("w-full gap-2")
        self._draw_results()

    def _draw_results(self) -> None:
        """Redraw only the matching groups; the search box is left alone."""
        if not getattr(self, "results", None):
            return
        self.results.clear()
        with self.results:
            shown_any = False
            for group in sorted(self.groups):
                els = {n: r for n, r in sorted(self.groups[group].items())
                       if self._matches(n, r)}
                if not els:
                    continue
                shown_any = True
                self._group_block(group, els)
            if not shown_any:
                ui.label("Nothing matches that search." if self.filter
                         else "No elements yet.").style(
                    f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_sm']}")

    def _matches(self, name: str, rec: dict) -> bool:
        if not self.filter:
            return True
        return self.filter in name.lower() or self.filter in _selector(rec).lower()

    def _set_filter(self, value: str) -> None:
        self.filter = (value or "").lower()
        self._draw_results()

    def _group_block(self, group: str, els: dict) -> None:
        with ui.expansion(value=bool(self.filter)).classes("w-full").style(
                f"border:1px solid {COLORS['border']}; border-radius:6px") as exp:
            with exp.add_slot("header"):
                with ui.row().classes("w-full items-center gap-3 no-wrap"):
                    ui.label(group).style(
                        f"font-family:{TYPOGRAPHY['mono']};"
                        f"font-weight:{TYPOGRAPHY['weight_bold']};"
                        f"font-size:{TYPOGRAPHY['size_sm']}")
                    ui.label(f"{len(els)} element(s)").style(
                        f"color:{COLORS['text_muted']};"
                        f"font-size:{TYPOGRAPHY['size_xs']}")
            for name, rec in els.items():
                self._row(group, name, rec)

    def _row(self, group: str, name: str, rec: dict) -> None:
        source = rec.get("_source", "manual")
        editable = source == "manual"
        clash = name in self.conflicts
        with ui.row().classes("w-full items-center gap-3 no-wrap").style(
                f"padding:5px 10px; border-top:1px solid {COLORS['border']}"):
            ui.label(name).style(
                f"font-family:{TYPOGRAPHY['mono']};"
                f"font-size:{TYPOGRAPHY['size_sm']}; width:18rem")
            if clash:
                ui.label("duplicate name").style(
                    f"background:{COLORS['warning']}1A; color:{COLORS['warning']};"
                    f"border-radius:4px; padding:0 6px;"
                    f"font-size:{TYPOGRAPHY['size_xs']}")
            if not editable:
                ui.label("recorded — read only").style(
                    f"color:{COLORS['text_muted']};"
                    f"font-size:{TYPOGRAPHY['size_xs']}")
            ui.label(_selector(rec)[:70] or "(no selector)").style(
                f"font-family:{TYPOGRAPHY['mono']};"
                f"font-size:{TYPOGRAPHY['size_xs']};"
                f"color:{COLORS['text_muted']}; flex-grow:1")
            if editable:
                ui.button(icon="edit").props("flat dense size=xs") \
                    .on("click", lambda g=group, n=name, r=rec: self._edit_dialog(g, n, r)) \
                    .tooltip("Change its selector, or rename it")
                ui.button(icon="delete_outline").props("flat dense size=xs color=negative") \
                    .on("click", lambda g=group, n=name: self._delete(g, n)) \
                    .tooltip("Remove it")
            else:
                ui.button(icon="lock").props("flat dense size=xs disable") \
                    .tooltip("Recorded by the spy — re-record it to change it")

    # ── editing ─────────────────────────────────────────────────────────────
    def _edit_dialog(self, group: str, name: str, rec: dict) -> None:
        creating = not name
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:42rem"):
            ui.label("Add an element" if creating else f"Edit {name}").style(
                f"font-size:{TYPOGRAPHY['size_lg']};"
                f"font-weight:{TYPOGRAPHY['weight_bold']}")

            g_in = ui.input("Group / page", value=group,
                            placeholder="home_page").props("outlined dense").classes("w-full")
            n_in = ui.input("Element name", value=name,
                            placeholder="maybe_later_link").props("outlined dense").classes("w-full")
            s_in = ui.input("Selector (CSS or XPath)", value=_selector(rec),
                            placeholder="//button[@id='x']") \
                .props("outlined dense").classes("w-full") \
                .style(f"font-family:{TYPOGRAPHY['mono']}")
            ui.label("All three are required. The name is normalised on save — "
                     "spaces and punctuation become underscores — so the convention "
                     "holds without you having to remember it.").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

            warn = ui.column().classes("w-full gap-0")
            note = ui.label().style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['danger']}")

            async def precheck() -> None:
                warn.clear()
                typed = (n_in.value or "").strip()
                if not typed or typed == name:
                    return
                try:
                    res = await api.check_locator(typed, g_in.value or "",
                                                  s_in.value or "", self.platform)
                except api.ApiError:
                    return
                with warn:
                    if res.get("changed"):
                        ui.label(f"Will be saved as “{res['normalised']}”").style(
                            f"font-size:{TYPOGRAPHY['size_xs']};"
                            f"color:{COLORS['primary']}")
                    for cf in res.get("conflicts", []):
                        colour = (COLORS["danger"] if cf["severity"] == "blocking"
                                  else COLORS["warning"])
                        ui.label(f"{cf['message']}").style(
                            f"font-size:{TYPOGRAPHY['size_xs']}; color:{colour}")
                        ui.label(cf["why"]).style(
                            f"font-size:{TYPOGRAPHY['size_xs']};"
                            f"color:{COLORS['text_muted']}")

            n_in.on_value_change(precheck)
            s_in.on_value_change(precheck)

            async def save(force: bool = False) -> None:
                new_name = (n_in.value or "").strip()
                new_group = (g_in.value or "").strip()
                selector = (s_in.value or "").strip()
                if not (new_name and new_group and selector):
                    note.set_text("Group, name and selector are all required.")
                    return

                # A rename is a different operation from an edit: the name is
                # written into every step that uses it, so those have to be
                # rewritten too. Shown before it happens.
                if name and new_name != name:
                    try:
                        preview = await api.rename_locator(group, name, new_name,
                                                           apply=False)
                    except api.ApiError as e:
                        note.set_text(e.detail if isinstance(e.detail, str)
                                      else str(e.detail)[:200])
                        return
                    dialog.close()
                    self._confirm_rename(group, name, new_name, selector, preview)
                    return

                try:
                    await api.add_locator(new_group, new_name, selector, force=force)
                except api.ApiError as e:
                    detail = e.detail
                    if e.status == 409 and isinstance(detail, dict):
                        note.set_text(detail.get("message", "")[:200])
                        with warn:
                            ui.label(detail.get("why", "")).style(
                                f"font-size:{TYPOGRAPHY['size_xs']};"
                                f"color:{COLORS['text_muted']}")
                            ui.button("Save anyway",
                                      on_click=lambda: save(force=True)) \
                                .props("flat dense color=negative")
                        return
                    note.set_text(str(detail)[:200])
                    return
                if name and group and new_group != group and new_name == name:
                    # A group change is a MOVE: drop the old record, or the
                    # name exists twice and the next load warns "duplicate".
                    try:
                        await api.delete_locator(group, name)
                    except api.ApiError as e:
                        ui.notify(f"Saved under {new_group}, but the old copy in {group} "
                                  f"could not be removed: {e.detail}", type="warning", timeout=8000)
                dialog.close()
                ui.notify(f"Saved {new_name} — every test using it picks up the "
                          f"new selector on its next run", type="positive",
                          timeout=6000)
                await self._reload()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Save", on_click=lambda: save(False)).props("unelevated")
        dialog.open()

    def _confirm_rename(self, group: str, old: str, new: str,
                        selector: str, preview: dict) -> None:
        """A rename rewrites steps, so it is shown in full before it happens."""
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:40rem"):
            ui.label(f"Rename {old} → {preview.get('new_name', new)}").style(
                f"font-size:{TYPOGRAPHY['size_lg']};"
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            changes = preview.get("changes", [])
            ui.label(f"{len(changes)} thing(s) will change:").style(
                f"font-size:{TYPOGRAPHY['size_sm']}")
            with ui.column().classes("w-full gap-0").style(
                    f"border:1px solid {COLORS['border']}; border-radius:6px;"
                    f"max-height:14rem; overflow-y:auto; padding:4px 8px"):
                for ch in changes:
                    ui.label(f"• {ch['kind']} — {ch['file']} "
                             f"{ch.get('detail', '')}").style(
                        f"font-family:{TYPOGRAPHY['mono']};"
                        f"font-size:{TYPOGRAPHY['size_xs']};"
                        f"color:{COLORS['text_muted']}")
            ui.label("Saved test cases and step groups are rewritten now. A test "
                     "case you have open and unsaved will pick the new name up "
                     "when you reload it. Past run history is left alone, so old "
                     "results stay comparable.").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            note = ui.label().style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['danger']}")

            async def go() -> None:
                try:
                    res = await api.rename_locator(group, old, new, apply=True)
                except api.ApiError as e:
                    note.set_text(str(e.detail)[:200])
                    return
                final = res.get("new_name", new)
                if selector:
                    try:
                        await api.add_locator(group, final, selector, force=True)
                    except api.ApiError:
                        pass
                dialog.close()
                ui.notify(f"Renamed to {final} and updated "
                          f"{len(res.get('changes', [])) - 1} reference(s)",
                          type="positive", timeout=6000)
                await self._reload()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Rename and update", on_click=go).props("unelevated")
        dialog.open()

    def _delete(self, group: str, name: str) -> None:
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:30rem"):
            ui.label(f"Remove {name}?").style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
            ui.label("Any step that clicks it will fail at run time until it is "
                     "pointed at something else.").style(
                f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")

            async def go() -> None:
                try:
                    await api.delete_locator(group, name)
                except api.ApiError as e:
                    ui.notify(str(e.detail)[:160], type="negative")
                    return
                dialog.close()
                ui.notify(f"Removed {name}", type="positive")
                await self._reload()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Remove", on_click=go).props("unelevated color=negative")
        dialog.open()

    async def _reload(self) -> None:
        await self.load()
        self._draw()


def _selector(rec) -> str:
    if isinstance(rec, str):
        return rec
    if not isinstance(rec, dict):
        return ""
    if rec.get("custom_xpath"):
        return rec["custom_xpath"]
    if rec.get("xpath"):
        return rec["xpath"]
    # A plain-string element (every Testsigma-imported one) reaches the UI
    # as {"value": "<selector>", "_source": …}. Not reading it made the list
    # say "(no selector)", selector search find nothing, and the edit
    # dialog treat the unchanged selector as a conflicting "different" one.
    if isinstance(rec.get("value"), str) and rec["value"]:
        return rec["value"]
    sels = rec.get("selectors")
    if isinstance(sels, list) and sels and isinstance(sels[0], dict):
        return sels[0].get("value", "")
    return ""


def open_create_element_dialog(platform: str, *,
                               group_hint: str = "",
                               name_hint: str = "",
                               name_placeholder: str = "maybe_later_link",
                               selector_hint: str = "",
                               title: str = "Add an element",
                               intro: str = "",
                               on_saved=None) -> None:
    """
    Open the shared element-creation popup used by the Elements screen.

    The step editor can reuse this directly, so "create locator" behaves like
    the Elements page instead of maintaining a second save form with slightly
    different rules and wording.
    """
    dialog = ui.dialog().props("persistent")
    with dialog, ui.card().style("width:42rem"):
        ui.label(title).style(
            f"font-size:{TYPOGRAPHY['size_lg']};"
            f"font-weight:{TYPOGRAPHY['weight_bold']}")
        if intro:
            ui.label(intro).style(
                f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")

        g_in = ui.input("Group / page", value=group_hint,
                        placeholder="home_page").props("outlined dense").classes("w-full")
        n_in = ui.input("Element name", value=name_hint,
                        placeholder=name_placeholder).props("outlined dense").classes("w-full")
        s_in = ui.input("Selector (CSS or XPath)", value=selector_hint,
                        placeholder="//button[@id='x']") \
            .props("outlined dense").classes("w-full") \
            .style(f"font-family:{TYPOGRAPHY['mono']}")
        ui.label("All three are required. The name is normalised on save — "
                 "spaces and punctuation become underscores — so the convention "
                 "holds without you having to remember it.").style(
            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

        warn = ui.column().classes("w-full gap-0")
        note = ui.label().style(
            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['danger']}")

        async def precheck() -> None:
            warn.clear()
            note.set_text("")
            typed = (n_in.value or "").strip()
            selector = (s_in.value or "").strip()
            if not typed or not selector:
                return
            try:
                res = await api.check_locator(typed, g_in.value or "",
                                              selector, platform)
            except api.ApiError:
                return
            with warn:
                if res.get("changed"):
                    ui.label(f"Will be saved as “{res['normalised']}”").style(
                        f"font-size:{TYPOGRAPHY['size_xs']};"
                        f"color:{COLORS['primary']}")
                for cf in res.get("conflicts", []):
                    colour = (COLORS["danger"] if cf["severity"] == "blocking"
                              else COLORS["warning"])
                    ui.label(f"{cf['message']}").style(
                        f"font-size:{TYPOGRAPHY['size_xs']}; color:{colour}")
                    ui.label(cf["why"]).style(
                        f"font-size:{TYPOGRAPHY['size_xs']};"
                        f"color:{COLORS['text_muted']}")

        g_in.on_value_change(precheck)
        n_in.on_value_change(precheck)
        s_in.on_value_change(precheck)
        if selector_hint:
            ui.timer(0.05, precheck, once=True)

        async def save(force: bool = False) -> None:
            new_name = (n_in.value or "").strip()
            new_group = (g_in.value or "").strip()
            selector = (s_in.value or "").strip()
            if not (new_name and new_group and selector):
                note.set_text("Group, name and selector are all required.")
                return

            try:
                res = await api.add_locator(new_group, new_name, selector, force=force)
            except api.ApiError as e:
                detail = e.detail
                if e.status == 409 and isinstance(detail, dict):
                    note.set_text(detail.get("message", "")[:200])
                    with warn:
                        ui.label(detail.get("why", "")).style(
                            f"font-size:{TYPOGRAPHY['size_xs']};"
                            f"color:{COLORS['text_muted']}")
                        ui.button("Save anyway",
                                  on_click=lambda: save(force=True)) \
                            .props("flat dense color=negative")
                    return
                note.set_text(str(detail)[:200])
                return

            saved = res.get("normalised", {}).get("name") or new_name
            try:
                await api.resync_snippets()
            except api.ApiError:
                pass
            dialog.close()
            ui.notify(f"Saved {saved}", type="positive", timeout=6000)
            if on_saved:
                result = on_saved(saved)
                if inspect.isawaitable(result):
                    await result

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save", on_click=lambda: save(False)).props("unelevated")
    dialog.open()


async def render(platform: str = "website", focus: str = "") -> None:
    page = ElementsPage(platform, focus)
    await page.load()
    page.render()
