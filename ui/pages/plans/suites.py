"""
ui/pages/plans/suites.py — Test Suites.

/suites              list
/suites/edit?id=…    create (no id) or edit — ordered test cases on one platform
"""
from __future__ import annotations

from urllib.parse import quote

from nicegui import ui

from ui import api_client as api
from ui.auth import can
from ui.layout import module_scope
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.pages.plans.common import confirm, heading, ist, muted
from ui.theme import COLORS, TYPOGRAPHY


async def _shell(crumbs: list[str], module: str = "") -> list[dict]:
    try:
        platforms = await api.platforms()
    except api.ApiError:
        platforms = []
    sidebar(active="/suites", platforms=platforms)
    if module:
        topbar(crumbs, platforms=platforms, platform=module,
               on_platform_change=module_scope.switcher("/suites"))
    else:
        topbar(crumbs, platforms=platforms)
    return platforms


async def render_list(module: str = "") -> None:
    try:
        _pl = await api.platforms()
    except api.ApiError:
        _pl = []
    module = module_scope.pick(module, _pl)
    await _shell(["Execute", "Test Suites"], module)
    with ui.column().classes("w-full gap-3 p-4").style("max-width:76rem"):
        with ui.row().classes("w-full items-center"):
            heading("Test Suites")
            ui.space()
            if can("write"):
                ui.button("New suite", icon="add", on_click=lambda: ui.navigate.to("/suites/edit")) \
                    .props("unelevated").style(f"background:{COLORS['primary']}")
        muted("A suite is an ordered set of test cases on one platform. Plans run suites, "
              "now or on a schedule.")
        try:
            items = await api.suites()
        except api.ApiError as e:
            ui.label(e.detail).style(f"color:{COLORS['danger']}")
            return
        # Only this module's suites — whatever is created in a module stays in it.
        items = [s for s in items if module_scope.belongs(s.get("platform"), module)]
        if not items:
            muted("No suites in this module yet.")
            return
        cols = [{"name": k, "label": l, "field": k, "align": "left", "sortable": True} for k, l in (
            ("name", "Suite"), ("platform", "Platform"), ("count", "Test cases"),
            ("cases", "Contains"), ("updated", "Last changed"))]
        # Every test case in the suite is shown — the cell wraps instead of
        # cutting the list off with "…".
        cols[3].update({"style": "max-width:34rem; white-space:normal; word-break:break-word",
                        "headerStyle": "max-width:34rem"})
        rows = [{"id": s["id"], "name": s["name"], "platform": s["platform"], "count": s["count"],
                 "cases": ",  ".join(c["name"] for c in s["test_cases"]),
                 "updated": f"{ist(s.get('updated_at'))} by {s.get('updated_by') or '—'}"
                            if s.get("updated_at") else "—"} for s in items]
        # Search: matches suite name, platform, and the test cases inside it.
        search = ui.input(placeholder="Search suites, platform or test case…") \
            .props("outlined dense clearable").style("width:28rem")
        with search.add_slot("prepend"):
            ui.icon("search")
        t = ui.table(columns=cols, rows=rows, row_key="id",
                     selection="multiple" if can("write") else None).classes("w-full")
        t.bind_filter_from(search, "value")
        t.add_slot("body-cell-name", r'''
            <q-td :props="props"><a class="cursor-pointer text-primary"
              @click="$parent.$emit('open', props.row)">{{ props.row.name }}</a></q-td>''')
        t.on("open", lambda e: ui.navigate.to(f"/suites/edit?id={quote(e.args['id'])}"))
        if can("write"):
            bar = ui.row().classes("w-full items-center gap-2")

            async def delete_picked() -> None:
                done, refused = [], []
                for r in list(t.selected):
                    try:
                        await api.delete_suite(r["id"])
                        done.append(r["name"])
                    except api.ApiError as e:
                        refused.append(f"{r['name']}: {e.detail}")
                if done:
                    ui.notify(f"Deleted {len(done)} suite(s)", type="positive")
                for msg in refused:
                    ui.notify(msg, type="warning", timeout=9000)
                ui.navigate.to("/suites")

            def draw_bar() -> None:
                bar.clear()
                if not t.selected:
                    return
                with bar:
                    muted(f"{len(t.selected)} selected")
                    ui.button(f"Delete {len(t.selected)} suite(s)", icon="delete_outline",
                              on_click=lambda: confirm(
                                  f"Delete {len(t.selected)} suite(s)?",
                                  "The test cases themselves are not touched. A suite used by a "
                                  "plan is not deleted — remove it from the plan first.",
                                  delete_picked)).props("flat dense color=negative")

            t.on_select(lambda _: draw_bar())


async def render_edit(suite_id: str = "") -> None:
    platforms = await _shell(["Execute", "Test Suites", "Edit" if suite_id else "New"])
    enabled = {p["name"]: p.get("label", p["name"]) for p in platforms if p.get("enabled", True)}
    data = {"name": "", "platform": next(iter(enabled), "website"), "description": "",
            "test_cases": [], "used_by": []}
    if suite_id:
        try:
            data = await api.suite(suite_id)
        except api.ApiError as e:
            ui.label(e.detail).style(f"color:{COLORS['danger']}")
            return
    writable = can("write")
    state = {"cases": [c["name"] for c in data.get("test_cases", [])], "picked": set()}

    with ui.column().classes("w-full gap-3 p-4").style("max-width:60rem"):
        with ui.row().classes("w-full items-center"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/suites")).props("flat dense")
            heading(data["name"] or "New suite")
        if suite_id:
            muted(f"Created by {data.get('created_by') or '—'} {ist(data.get('created_at'))} · "
                  f"last changed by {data.get('updated_by') or '—'} {ist(data.get('updated_at'))}"
                  + (f" · used by plan(s): {', '.join(data['used_by'])}" if data.get("used_by") else ""))
        name = ui.input("Suite name", value=data["name"]).props("outlined dense").classes("w-full")
        desc = ui.textarea("Description", value=data.get("description", "")) \
            .props("outlined dense autogrow").classes("w-full")
        plat = ui.select(enabled, value=data.get("platform") or next(iter(enabled), None),
                         label="Platform").props("outlined dense").style("min-width:18rem")

        with ui.row().classes("w-full items-center gap-2"):
            ui.label("Test cases (run in this order)").style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
            ui.space()
            bulk = ui.row().classes("items-center gap-2")
        box = ui.column().classes("w-full gap-0").style(
            f"border:1px solid {COLORS['border']}; border-radius:6px")
        with ui.row().classes("w-full items-center gap-2"):
            picker = ui.select([], label="Add a test case", with_input=True) \
                .props("outlined dense").style("min-width:30rem")
            add_btn = ui.button("Add", icon="add").props("flat")

        async def load_choices() -> None:
            try:
                names = await api.list_projects(plat.value or "")
            except api.ApiError:
                names = []
            names = [n for n in names if not n.startswith("_") and n not in state["cases"]]
            picker.set_options(names)

        def draw_bulk() -> None:
            bulk.clear()
            if not writable or not state["cases"]:
                return
            with bulk:
                every = bool(state["cases"]) and len(state["picked"]) == len(state["cases"])
                ui.checkbox("Select all", value=every,
                            on_change=lambda e: (state["picked"].clear() if not e.value
                                                 else state["picked"].update(state["cases"]),
                                                 draw())).props("dense")
                ui.button(f"Remove {len(state['picked'])} selected", icon="playlist_remove",
                          on_click=remove_picked) \
                    .props("flat dense color=negative").set_enabled(bool(state["picked"]))

        async def remove_picked() -> None:
            state["cases"] = [c for c in state["cases"] if c not in state["picked"]]
            n = len(state["picked"])
            state["picked"].clear()
            draw()
            await load_choices()
            ui.notify(f"Removed {n} — press Save suite to keep the change", type="info")

        def draw() -> None:
            draw_bulk()
            box.clear()
            with box:
                if not state["cases"]:
                    muted("No test cases yet — add them below.").style("padding:10px")
                for i, c in enumerate(state["cases"]):
                    with ui.row().classes("w-full items-center gap-2 no-wrap").style(
                            f"padding:4px 10px; border-bottom:1px solid {COLORS['border']}"):
                        if writable:
                            ui.checkbox(value=c in state["picked"],
                                        on_change=lambda e, c=c: (state["picked"].add(c) if e.value
                                                                  else state["picked"].discard(c),
                                                                  draw_bulk())).props("dense")
                        ui.label(str(i + 1)).style(f"width:1.6rem; color:{COLORS['text_muted']}")
                        ui.label(c).style(f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_sm']}") \
                            .classes("flex-grow")
                        ui.button(icon="open_in_new", on_click=lambda c=c: ui.navigate.to(
                            f"/platform/{plat.value}?flow={quote(c)}")).props("flat dense size=sm") \
                            .tooltip("Open the test case")
                        if writable:
                            ui.button(icon="keyboard_arrow_up", on_click=lambda i=i: move(i, -1)) \
                                .props("flat dense size=sm").set_enabled(i > 0)
                            ui.button(icon="keyboard_arrow_down", on_click=lambda i=i: move(i, 1)) \
                                .props("flat dense size=sm").set_enabled(i < len(state["cases"]) - 1)
                            ui.button(icon="close", on_click=lambda i=i: remove(i)) \
                                .props("flat dense size=sm color=negative").tooltip("Remove from suite")

        def move(i: int, d: int) -> None:
            c = state["cases"]
            c[i], c[i + d] = c[i + d], c[i]
            draw()

        async def remove(i: int) -> None:
            state["picked"].discard(state["cases"].pop(i))
            draw()
            await load_choices()

        async def add() -> None:
            v = picker.value
            if not v:
                return
            if v in state["cases"]:
                ui.notify(f"{v} is already in this suite", type="warning")
                return
            state["cases"].append(v)
            picker.set_value(None)
            draw()
            await load_choices()

        add_btn.on_click(add)
        plat.on_value_change(lambda _: load_choices())
        draw()
        await load_choices()
        if not writable:
            picker.set_visibility(False)
            add_btn.set_visibility(False)

        msg = ui.label().style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['danger']}")

        async def save() -> None:
            try:
                res = await api.save_suite((name.value or "").strip(), plat.value, state["cases"],
                                           desc.value or "", suite_id)
            except api.ApiError as e:
                msg.set_text(e.detail)
                return
            ui.notify(f"Saved suite '{res['name']}'", type="positive")
            ui.navigate.to(f"/suites/edit?id={quote(res['id'])}")

        async def do_delete() -> None:
            try:
                await api.delete_suite(suite_id)
            except api.ApiError as e:
                ui.notify(e.detail, type="negative", timeout=8000)
                return
            ui.notify("Suite deleted", type="positive")
            ui.navigate.to("/suites")

        with ui.row().classes("w-full items-center gap-2"):
            if writable and suite_id:
                ui.button("Delete suite", icon="delete_outline",
                          on_click=lambda: confirm(f"Delete suite '{data['name']}'?",
                                                   "The test cases themselves are not touched.",
                                                   do_delete)).props("flat color=negative")
            ui.space()
            if writable:
                ui.button("Save suite", icon="save", on_click=save).props("unelevated") \
                    .style(f"background:{COLORS['primary']}")
            else:
                muted("Read-only — your role is viewer.")
