"""
ui/pages/settings/pages_kb.py — Settings → Pages (the page knowledge base).

Per page type: how to recognise it (URL patterns), what proves it rendered
(landmark elements), which popups to close on it, and its sections top→bottom.
"""
from __future__ import annotations

from nicegui import ui

from ui import api_client as api
from ui.auth import can
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.theme import COLORS, TYPOGRAPHY


def _muted(t: str):
    return ui.label(t).style(f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_xs']}")


async def render() -> None:
    sidebar(active="/settings")
    topbar(["Settings", "Pages"])
    try:
        pages = (await api.pages_kb()).get("pages", {})
    except api.ApiError as e:
        ui.notify(f"Could not load: {e.detail}", type="negative")
        return
    try:
        locs = await api.locators_for("mobilesite")      # {group: {name: record}}
        names = sorted({n for g in locs.values() if isinstance(g, dict) for n in g})
    except Exception:  # noqa: BLE001
        names = []
    widgets: dict = {}
    with ui.column().classes("w-full gap-3 p-4").style("max-width:72rem"):
        ui.label("Pages — what the runner knows about each page").style(
            f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
        _muted("open <url> waits for the page's landmark; every scroll / swipe closes the page's popups on the way "
               "(skip / close controls only — never a sheet a test may be checking); sections are the page top→bottom. "
               "Elements are names from Elements (Mobile Site). One line per URL pattern (regular expression).")
        box = ui.column().classes("w-full gap-2")

        def draw() -> None:
            box.clear()
            widgets.clear()
            with box:
                for key, p in pages.items():
                    with ui.expansion(f"{p.get('label') or key}  ({key})").classes("w-full").style(
                            f"border:1px solid {COLORS['border']}; border-radius:6px"):
                        w = {}
                        w["label"] = ui.input("Label", value=p.get("label", "")).props("outlined dense").classes("w-full")
                        w["url"] = ui.textarea("URL patterns (one per line)", value="\n".join((p.get("detect") or {}).get("url", []))) \
                            .props("outlined dense autogrow").classes("w-full")
                        for f, lab in (("landmark", "Landmark elements (any one visible = page rendered)"),
                                       ("popups", "Popups to close (skip / close controls)"),
                                       ("sections", "Sections, top to bottom")):
                            opts = {n: n for n in sorted(set(names) | set(p.get(f) or []))}
                            w[f] = ui.select(opts, multiple=True, value=list(p.get(f) or []), label=lab) \
                                .props("outlined dense use-chips use-input input-debounce=0").classes("w-full")
                        if can("write"):
                            ui.button("Remove this page", icon="delete_outline",
                                      on_click=lambda k=key: (pages.pop(k, None), draw())).props("flat dense")
                        widgets[key] = w

        draw()
        if can("write"):
            with ui.row().classes("items-center gap-2"):
                new_key = ui.input("New page key (e.g. srp)").props("outlined dense")

                def add() -> None:
                    k = (new_key.value or "").strip().lower()
                    if k and k not in pages:
                        pages[k] = {"label": k.upper(), "detect": {"url": []}, "landmark": [], "popups": [], "sections": []}
                        draw()
                ui.button("Add page", icon="add", on_click=add).props("flat dense")

            async def save() -> None:
                out = {}
                for k, w in widgets.items():
                    out[k] = {"label": w["label"].value, "detect": {"url": [u.strip() for u in (w["url"].value or "").splitlines() if u.strip()]},
                              "landmark": list(w["landmark"].value or []), "popups": list(w["popups"].value or []),
                              "sections": list(w["sections"].value or [])}
                try:
                    res = await api.save_pages_kb(out)
                    ui.notify(res.get("message", "Saved"), type="positive")
                except api.ApiError as e:
                    ui.notify(f"Could not save: {e.detail}", type="negative")
            ui.button("Save pages", icon="save", on_click=save).props("unelevated")
