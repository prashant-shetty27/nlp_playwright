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
    def __init__(self, platform: str, focus: str = "", back: str = "") -> None:
        self.platform = platform
        self.focus = focus
        # Where the pencil in a test case came from — a portal path only.
        self.back = back if (back.startswith("/") and not back.startswith("//")) else ""
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
            if self.back:
                ui.button("Back to the test case", icon="arrow_back",
                          on_click=lambda: ui.navigate.to(self.back)).props("flat dense no-caps") \
                    .style(f"color:{COLORS['primary']}; align-self:flex-start")
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
                self.search_input = ui.input(placeholder="Search elements or selectors",
                                             value=self.filter,
                                             on_change=lambda e: self._set_filter(e.value)) \
                    .props("outlined dense clearable").style("width:22rem")
                ui.label(f"{total} element(s) in {len(self.groups)} group(s)").style(
                    f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_sm']}")
                ui.space()
                ui.button("Review", icon="auto_awesome", on_click=self._review) \
                    .props("flat").style(f"color:{COLORS['primary']}") \
                    .tooltip("Duplicate names or selectors, blank or position-only "
                             "selectors, auto-generated names, unused elements")
                ui.button("Add element", icon="add",
                          on_click=lambda: open_create_element_dialog(
                              self.platform,
                              on_saved=lambda _saved: self._reload(),
                          )) \
                    .props("unelevated")

            # Review findings go here, above the list, so they are seen.
            self.review_box = ui.column().classes("w-full gap-1")

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
                    if e.status == 422 and isinstance(detail, dict):
                        note.set_text(detail.get("message", "")[:200])
                        with warn:
                            if detail.get("suggested"):
                                sug = detail["suggested"]
                                ui.button(f"Use '{sug}'", icon="auto_fix_high",
                                          on_click=lambda v=sug: n_in.set_value(v)) \
                                    .props("flat dense no-caps").style(f"color:{COLORS['primary']}")
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

    # ── review ───────────────────────────────────────────────────────────────
    _KIND_TITLES = {
        "duplicate_name": "Same name defined more than once",
        "duplicate_selector": "Same selector under different names",
        "blank_selector": "No selector",
        "junk_name": "Auto-generated or meaningless names",
        "bad_name": "Not in the naming convention",
        "fragile_selector": "Position-only selectors",
        "unused": "Not used by any test case or step group",
    }

    async def _review(self) -> None:
        """Run the element review and draw the findings, grouped by kind."""
        box = getattr(self, "review_box", None)
        if box is None:
            return
        box.clear()
        with box:
            ui.spinner(size="sm")
        try:
            findings = await api.review_elements(self.platform)
        except api.ApiError as e:
            box.clear()
            ui.notify(f"Review failed: {e.detail}", type="negative")
            return
        box.clear()
        self._picked: dict[tuple, dict] = {}
        with box:
            with ui.row().classes("w-full items-center gap-2"):
                ui.label(f"Element review — {len(findings)} finding(s)").style(
                    f"font-weight:{TYPOGRAPHY['weight_bold']}")
                ui.space()
                self.bulk_label = ui.label("").style(
                    f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")
                ui.button("Delete selected", icon="delete_sweep",
                          on_click=self._delete_selected) \
                    .props("flat dense no-caps color=negative") \
                    .tooltip("Delete every ticked element (each is refused if a step still uses it)")
                ui.button(icon="close", on_click=box.clear).props("flat dense size=sm") \
                    .tooltip("Hide the review")
            if not findings:
                ui.label("Nothing to fix — every element has a unique name and a selector, "
                         "and all are in use.").style(
                    f"color:{COLORS['success']}; font-size:{TYPOGRAPHY['size_sm']}")
                return
            by_kind: dict[str, list[dict]] = {}
            for f in findings:
                by_kind.setdefault(f["kind"], []).append(f)
            for kind in self._KIND_TITLES:
                rows = by_kind.get(kind)
                if not rows:
                    continue
                sev = rows[0]["severity"]
                colour = {"high": COLORS["danger"], "medium": COLORS["warning"]}.get(
                    sev, COLORS["text_muted"])
                with ui.expansion(f"{self._KIND_TITLES[kind]} ({len(rows)})",
                                  value=sev == "high").classes("w-full").style(
                        f"border-left:3px solid {colour}; border-radius:4px;"
                        f"background:{colour}0D"):
                    with ui.row().classes("w-full items-center gap-2").style("padding:0 8px 6px"):
                        ui.label(rows[0]["why"]).classes("flex-grow").style(
                            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
                        editable = [f for f in rows if self._editable(f)]
                        if editable:
                            ui.button("Select all here", icon="checklist",
                                      on_click=lambda rs=editable: self._select_all(rs)) \
                                .props("flat dense size=sm no-caps")
                    seen: set[tuple] = set()
                    if kind == "duplicate_selector":
                        # One row per selector, with a Merge that keeps the
                        # most-used name and rewrites the steps.
                        by_sel: dict[str, list[dict]] = {}
                        for f in rows:
                            by_sel.setdefault(f.get("selector", ""), []).append(f)
                        for sel, fs in by_sel.items():
                            self._merge_row(sel, fs)
                        continue
                    for f in rows:
                        key = (f["group"], f["name"])
                        if key in seen:
                            continue
                        seen.add(key)
                        self._review_row(f)

    def _editable(self, f: dict) -> bool:
        rec = (self.groups.get(f["group"]) or {}).get(f["name"])
        return rec is not None and rec.get("_source") in (None, "manual")

    def _select_all(self, rows: list[dict]) -> None:
        for f in rows:
            self._picked[(f["group"], f["name"])] = f
        self._refresh_bulk()
        ui.notify(f"{len(rows)} element(s) selected — press 'Delete selected' when ready", type="info")

    def _refresh_bulk(self) -> None:
        n = len(getattr(self, "_picked", {}))
        lbl = getattr(self, "bulk_label", None)
        if lbl is not None:
            lbl.set_text(f"{n} selected" if n else "")

    async def _delete_selected(self) -> None:
        items = list(getattr(self, "_picked", {}).values())
        if not items:
            ui.notify("Tick the elements to delete first", type="warning")
            return
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().style("width:36rem; max-height:80vh; overflow-y:auto"):
            ui.label(f"Delete {len(items)} element(s)?").style(
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            ui.label("Permanent. Any still used by a step or group is refused and kept.").style(
                f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")
            with ui.column().classes("w-full gap-0").style(
                    f"border:1px solid {COLORS['border']}; border-radius:6px;"
                    f"max-height:16rem; overflow-y:auto; padding:4px 8px"):
                for f in items:
                    ui.label(f"{f['group']} / {f['name']}").style(
                        f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']}")

            async def go() -> None:
                try:
                    res = await api.bulk_delete_locators(
                        [{"page": f["group"], "name": f["name"]} for f in items])
                except api.ApiError as e:
                    ui.notify(f"Could not delete: {e.detail}", type="negative")
                    return
                dialog.close()
                d, r = res.get("deleted", []), res.get("refused", [])
                ui.notify(f"Deleted {len(d)}" + (f", kept {len(r)} still in use" if r else ""),
                          type="positive" if d else "warning", timeout=6000)
                await self._reload()
                await self._review()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button(f"Delete {len(items)}", on_click=go).props("unelevated color=negative")
        dialog.open()

    def _merge_row(self, selector: str, fs: list[dict]) -> None:
        """One selector saved under several names: keep one, rewrite the rest."""
        names = sorted({f["name"] for f in fs}, key=lambda n: -max(
            (x.get("used_by", 0) for x in fs if x["name"] == n), default=0))
        used = {n: max((x.get("used_by", 0) for x in fs if x["name"] == n), default=0) for n in names}
        with ui.row().classes("w-full items-center gap-2 no-wrap").style("padding:2px 8px"):
            ui.label(selector[:60]).style(
                f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']};"
                f"color:{COLORS['text_muted']}; min-width:18rem; overflow:hidden;"
                f"text-overflow:ellipsis; white-space:nowrap")
            keep = ui.select({n: f"{n}  (used {used[n]}×)" for n in names}, value=names[0],
                             label="Keep").props("dense outlined").style("min-width:20rem")
            ui.label("→ the other name(s) are rewritten in every step, then deleted").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            ui.space()

            async def merge() -> None:
                k = keep.value
                drop = [n for n in names if n != k]
                try:
                    res = await api.merge_locators(k, drop, apply=True)
                except api.ApiError as e:
                    ui.notify(f"Merge failed: {e.detail}", type="negative")
                    return
                left = res.get("not_deletable") or []
                ui.notify(f"Merged into {k}" + (f" (recorded copies kept: {', '.join(left)})" if left else ""),
                          type="positive", timeout=6000)
                await self._reload()
                await self._review()
            ui.button("Merge", icon="call_merge", on_click=merge).props("flat dense size=sm no-caps") \
                .style(f"color:{COLORS['primary']}")

    def _review_row(self, f: dict) -> None:
        group, name = f["group"], f["name"]
        rec = (self.groups.get(group) or {}).get(name)
        editable = self._editable(f)
        with ui.row().classes("w-full items-center gap-2 no-wrap").style("padding:2px 8px"):
            if editable:
                def toggle(e, ff=f):
                    key = (ff["group"], ff["name"])
                    if e.value:
                        self._picked[key] = ff
                    else:
                        self._picked.pop(key, None)
                    self._refresh_bulk()
                ui.checkbox(value=(group, name) in self._picked, on_change=toggle).props("dense")
            else:
                ui.label("").style("width:1.6rem")
            ui.label(name).style(f"font-family:{TYPOGRAPHY['mono']};"
                                 f"font-size:{TYPOGRAPHY['size_sm']}; min-width:16rem")
            ui.label(group).style(f"font-size:{TYPOGRAPHY['size_xs']};"
                                  f"color:{COLORS['text_muted']}; min-width:12rem")
            # The selector is always shown — it is what the element IS.
            ui.label((f.get("selector") or "(no selector)")[:60]).style(
                f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']};"
                f"color:{COLORS['text']}; min-width:18rem; max-width:24rem; overflow:hidden;"
                f"text-overflow:ellipsis; white-space:nowrap").tooltip(f.get("selector") or "")
            detail = ""
            if f["kind"] == "duplicate_name":
                detail = "also in: " + ", ".join(f"{o['source']}:{o['group']}"
                                                  for o in (f.get("others") or []))
            ui.label(detail[:60]).classes("flex-grow").style(
                f"font-size:{TYPOGRAPHY['size_xs']};"
                f"color:{COLORS['text_muted']}; overflow:hidden; text-overflow:ellipsis;"
                f"white-space:nowrap")
            used = f.get("used_by")
            if used is not None:
                ui.label(f"used {used}×").style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']};"
                    f"white-space:nowrap")
            ui.button(icon="search", on_click=lambda n=name: self._show(n)) \
                .props("flat dense size=xs").tooltip("Show it in the list")
            if editable:
                ui.button("Edit", icon="edit",
                          on_click=lambda g=group, n=name, r=rec: self._edit_dialog(g, n, r)) \
                    .props("flat dense size=sm no-caps") \
                    .tooltip("Rename it or fix the selector — every step is rewritten")
                if f["kind"] in ("unused", "duplicate_selector", "duplicate_name"):
                    ui.button("Delete", icon="delete_outline",
                              on_click=lambda g=group, n=name: self._delete(g, n)) \
                        .props("flat dense size=sm no-caps color=negative")

    def _show(self, name: str) -> None:
        """Filter the list to this element; the review panel stays open."""
        self.filter = name.lower()
        inp = getattr(self, "search_input", None)
        if inp is not None:
            inp.set_value(name)
        self._draw_results()

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


async def render(platform: str = "website", focus: str = "", back: str = "") -> None:
    page = ElementsPage(platform, focus, back)
    await page.load()
    page.render()
