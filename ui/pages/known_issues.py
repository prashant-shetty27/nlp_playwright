"""
ui/pages/known_issues.py — Reports → Known issues.

The register of failures the team already knows about (a product defect on
Jira, a site/data change, an environment limit, a flaky step). A failing plan
item that matches an open entry is reported as KNOWN and counted apart from
new failures, so a run report shows what is new.
"""
from __future__ import annotations

from nicegui import ui

from ui import api_client as api
from ui.auth import can
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.theme import COLORS, TYPOGRAPHY

KIND_HELP = {
    "product-defect": "The product is wrong (raise / linked on Jira)",
    "site-change": "Live changed (layout, listing, product) — test data or the test needs an update",
    "data": "Test data no longer valid (a listing, a city, a product)",
    "environment": "Network / VPN / API reachability on the run machine",
    "flaky": "Timing — passes on retry",
}


def _muted(t: str):
    return ui.label(t).style(f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_xs']}")


async def render(prefill: dict | None = None) -> None:
    sidebar(active="/reports")
    topbar(["Reports", "Known issues"])
    state = {"issues": [], "kinds": []}
    with ui.column().classes("w-full gap-3 p-4").style("max-width:70rem"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Known issues").style(f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
            if can("write"):
                ui.button("Add known issue", icon="add", on_click=lambda: _edit({})).props("unelevated")
        _muted("A failure that matches an open entry is shown as KNOWN on plan reports and counted apart from new "
               "failures. Match rules are regular expressions (ignoring case); every non-empty rule must match. "
               "An entry with an expiry date stops matching after it; 'Close' stops it at once.")
        # Look a finding up on Jira before registering it (read-only).
        with ui.card().classes("w-full").style(f"border:1px solid {COLORS['border']}; padding:8px 12px"):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                jql_in = ui.input("Search Jira (JQL)", placeholder='text ~ "send enquiry" AND project = GJDT ORDER BY created DESC') \
                    .props("outlined dense").classes("flex-grow")
                jira_btn = ui.button("Search", icon="search").props("unelevated dense")
            jira_out = ui.column().classes("w-full gap-0").props('id="jira-search-out"')

            async def do_search() -> None:
                jira_out.clear()
                try:
                    res = await api.jira_search(jql_in.value or "", 10)
                except api.ApiError as e:
                    with jira_out:
                        ui.label(str(e.detail)).style(f"color:{COLORS['danger']}; font-size:{TYPOGRAPHY['size_xs']}")
                    return
                with jira_out:
                    _muted(f"{res.get('total', 0)} match(es)")
                    for i in res.get("issues", []):
                        with ui.row().classes("items-center gap-2 no-wrap"):
                            ui.link(i["key"], f"https://jdjira.justdial.com/browse/{i['key']}", new_tab=True) \
                                .style(f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']}")
                            ui.label(f"{i['type']} · {i['status']} · {i['created']}").style(
                                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}; white-space:nowrap")
                            ui.label(i["summary"]).style(f"font-size:{TYPOGRAPHY['size_xs']}; white-space:normal")
            jira_btn.on("click", do_search)
            jql_in.on("keydown.enter", do_search)
        box = ui.column().classes("w-full gap-2")

    async def load() -> None:
        try:
            d = await api.known_issues()
        except api.ApiError as e:
            ui.notify(f"Could not load: {e.detail}", type="negative")
            return
        state["issues"], state["kinds"] = d.get("issues", []), d.get("kinds", [])
        box.clear()
        with box:
            if not state["issues"]:
                _muted("Nothing registered yet.")
            for e in sorted(state["issues"], key=lambda x: (bool(x.get("closed")), x.get("id", ""))):
                closed = bool(e.get("closed"))
                with ui.card().classes("w-full").style(
                        f"border:1px solid {COLORS['border']}; padding:8px 12px; opacity:{0.55 if closed else 1}"):
                    with ui.row().classes("w-full items-center gap-2 no-wrap"):
                        ui.label(e.get("id", "")).style(f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']}")
                        ui.label(e.get("kind", "")).style(
                            f"font-size:{TYPOGRAPHY['size_xs']}; border:1px solid {COLORS['warning']}; color:{COLORS['warning']};"
                            "border-radius:10px; padding:0 8px")
                        ui.label(e.get("title", "")).style(f"font-size:{TYPOGRAPHY['size_sm']}; flex:1; min-width:0; white-space:normal")
                        if e.get("jira"):
                            ui.link(e["jira"], f"https://jdjira.justdial.com/browse/{e['jira']}", new_tab=True).style(
                                f"font-size:{TYPOGRAPHY['size_xs']}")
                        if closed:
                            ui.label("closed").style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
                        if can("write"):
                            ui.button(icon="edit", on_click=lambda ee=e: _edit(ee)).props("flat dense size=sm")
                            if not closed:
                                ui.button(icon="check_circle", on_click=lambda ee=e: _close(ee)).props("flat dense size=sm") \
                                    .tooltip("Close — stops matching from now on")
                            ui.button(icon="delete_outline", on_click=lambda ee=e: _delete(ee)).props("flat dense size=sm")
                    m = e.get("match") or {}
                    rules = " · ".join(f"{k}: {v}" for k, v in m.items() if v)
                    _muted((f"opened {e.get('opened', '')}" + (f" · expires {e['expires']}" if e.get("expires") else "")
                            + (f" · owner {e['owner']}" if e.get("owner") else "") + (f" · {rules}" if rules else "")))
                    if e.get("note"):
                        ui.label(e["note"]).style(f"font-size:{TYPOGRAPHY['size_xs']}; white-space:normal")

    async def _close(e: dict) -> None:
        e = dict(e); e["closed"] = True
        try:
            await api.save_known_issue(e)
        except api.ApiError as ex:
            ui.notify(f"Could not save: {ex.detail}", type="negative")
            return
        await load()

    async def _delete(e: dict) -> None:
        try:
            await api.delete_known_issue(e.get("id", ""))
        except api.ApiError as ex:
            ui.notify(f"Could not delete: {ex.detail}", type="negative")
            return
        await load()

    def _edit(e: dict) -> None:
        e = dict(e); m = dict(e.get("match") or {})
        dialog = ui.dialog()
        with dialog, ui.card().style("width:min(44rem, 95vw)"):
            ui.label(("Edit " + e["id"]) if e.get("id") else "New known issue").style(
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            title = ui.input("Title", value=e.get("title", "")).props("outlined dense").classes("w-full")
            kind = ui.select({k: f"{k} — {KIND_HELP.get(k, '')}" for k in (state["kinds"] or list(KIND_HELP))},
                             value=e.get("kind") or "product-defect", label="Kind").props("outlined dense").classes("w-full")
            with ui.row().classes("w-full gap-2"):
                jira = ui.input("Jira key (optional)", value=e.get("jira", "")).props("outlined dense").style("flex:1")
                owner = ui.input("Owner", value=e.get("owner", "")).props("outlined dense").style("flex:1")
                expires = ui.input("Expires (YYYY-MM-DD, optional)", value=e.get("expires", "")).props("outlined dense").style("flex:1")
            note = ui.textarea("Note (what, why, what to do)", value=e.get("note", "")).props("outlined dense").classes("w-full")
            ui.label("Match rules (regex, ignoring case; all non-empty rules must match)").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            mt = ui.input("Test case name", value=m.get("test_case", "")).props("outlined dense").classes("w-full")
            ms = ui.input("Failing step text", value=m.get("step", "")).props("outlined dense").classes("w-full")
            me = ui.input("Error text", value=m.get("error", "")).props("outlined dense").classes("w-full")
            ml = ui.input("Element name", value=m.get("element", "")).props("outlined dense").classes("w-full")

            async def save() -> None:
                body = {**e, "title": title.value, "kind": kind.value, "jira": (jira.value or "").strip(),
                        "owner": owner.value, "expires": (expires.value or "").strip(), "note": note.value,
                        "match": {"test_case": mt.value or "", "step": ms.value or "",
                                  "error": me.value or "", "element": ml.value or ""}}
                try:
                    res = await api.save_known_issue(body)
                except api.ApiError as ex:
                    ui.notify(f"Could not save: {ex.detail}", type="negative")
                    return
                dialog.close()
                ui.notify(res.get("message", "Saved"), type="positive")
                await load()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Save", icon="save", on_click=save).props("unelevated")
        dialog.open()
        if prefill and not e:
            pass

    await load()
    if prefill:
        _edit(prefill)
