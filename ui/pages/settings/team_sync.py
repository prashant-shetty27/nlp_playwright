"""
Settings → Team sync: sharing work with the team without knowing git.

Two buttons, one status line. "Get the latest" brings in what teammates
shared (and restarts the portal when code came with it); "Share my work"
sends this computer's test cases, elements, step groups, suites and plans to
the team. Every outcome is a sentence a tester can act on; the git text sits
behind "details" for whoever needs it.
"""
from __future__ import annotations

from nicegui import ui

from ui import api_client as api
from ui.layout.sidebar import sidebar
from ui.layout.topbar import restart_and_reload, topbar
from ui.theme import COLORS, TYPOGRAPHY


def _muted(text: str):
    return ui.label(text).style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")


async def _exec_defaults_card() -> None:
    """Engine defaults (scroll settle, scroll/swipe limits, timeouts) — editable
    here, stored in config/controllers.json, read live by the runner."""
    try:
        d = await api.exec_defaults()
    except api.ApiError:
        return
    ui.label("Execution defaults").style(f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
    _muted("What the runner uses when a step does not say otherwise. Saved for everyone who runs "
           "from this portal; a value typed into a step always wins.")
    with ui.card().classes("w-full").style(f"border:1px solid {COLORS['border']}"):
        inputs: dict = {}
        for key, f in d.get("fields", {}).items():
            with ui.row().classes("w-full items-center gap-3 no-wrap"):
                inputs[key] = ui.number(f["label"], value=d["values"].get(key), min=f["min"], max=f["max"],
                                        step=1).props("outlined dense").style("min-width:26rem")
                _muted(f["help"]).style("flex:1")

        async def save() -> None:
            try:
                res = await api.save_exec_defaults({k: int(w.value or 0) for k, w in inputs.items()})
                ui.notify(res.get("message", "Saved"), type="positive")
            except api.ApiError as e:
                ui.notify(f"Could not save: {e.detail}", type="negative")

        ui.button("Save execution defaults", icon="save", on_click=save).props("unelevated")


async def render() -> None:
    sidebar(active="/settings")
    topbar(["Settings", "Team sync"])
    with ui.column().classes("w-full gap-3 p-4").style("max-width:52rem"):
        ui.label("Team sync").style(f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
        _muted("Your test cases, elements, step groups, suites and plans live in a shared team "
               "repository. Use these two buttons instead of git commands. Private files — "
               "your .env, Test Data, screenshots, reports — never leave this computer.")

        with ui.row().classes("items-center gap-2"):
            ui.button("Pages — what the runner knows about each page", icon="web",
                      on_click=lambda: ui.navigate.to("/settings/pages")).props("flat dense")
            ui.button("Known issues register", icon="rule",
                      on_click=lambda: ui.navigate.to("/known-issues")).props("flat dense")
        await _exec_defaults_card()

        card = ui.card().classes("w-full").style(f"border:1px solid {COLORS['border']}")
        with card:
            status_line = ui.label("Checking…").style(f"font-size:{TYPOGRAPHY['size_sm']}")
            incoming_box = ui.column().classes("w-full gap-0")
            with ui.row().classes("items-center gap-2"):
                pull_btn = ui.button("Get the latest from the team", icon="cloud_download").props("unelevated")
            ui.separator()
            mine_line = ui.label("").style(f"font-size:{TYPOGRAPHY['size_sm']}")
            msg = ui.input("What did you change? (optional, one line)").props("dense outlined").classes("w-full")
            with ui.row().classes("items-center gap-2"):
                share_btn = ui.button("Share my work with the team", icon="cloud_upload").props("unelevated") \
                    .style(f"background:{COLORS['success']}")
            result = ui.label("").style(f"font-size:{TYPOGRAPHY['size_sm']}")
            details = ui.expansion("details").classes("w-full")
            with details:
                details_text = ui.label("").style(f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']};"
                                                  f"white-space:pre-wrap; color:{COLORS['text_muted']}")
            details.set_visibility(False)

        async def refresh() -> None:
            try:
                st = await api.sync_status()
            except api.ApiError as e:
                status_line.set_text(f"Could not check: {e.detail}")
                return
            if not st.get("repo"):
                status_line.set_text("This folder is not connected to the team repository — ask Prashant.")
                pull_btn.disable(); share_btn.disable()
                return
            behind, ahead = st.get("behind", 0), st.get("ahead", 0)
            if behind:
                status_line.set_text(f"{behind} update{'s' if behind != 1 else ''} from the team waiting.")
                status_line.style(f"color:{COLORS['primary']}; font-weight:500")
            else:
                status_line.set_text("You have the latest from the team.")
                status_line.style(f"color:{COLORS['success']}")
            incoming_box.clear()
            with incoming_box:
                for line in (st.get("incoming") or [])[:6]:
                    _muted("• " + line)
            mine = st.get("my_changes") or {}
            if mine:
                parts = [f"{n} {k}" if k not in ("elements", "step groups", "folders") else k for k, n in mine.items()]
                mine_line.set_text("Not yet shared from this computer: " + ", ".join(parts)
                                   + (f" (plus {ahead} shared-but-not-sent)" if ahead else "") + ".")
                mine_line.style(f"color:{COLORS['warning']}")
            elif ahead:
                mine_line.set_text(f"{ahead} change{'s' if ahead != 1 else ''} saved here but not sent yet — press Share.")
                mine_line.style(f"color:{COLORS['warning']}")
            else:
                mine_line.set_text("Everything you did here is already with the team.")
                mine_line.style(f"color:{COLORS['text_muted']}")

        def show(res: dict) -> None:
            ok = res.get("ok")
            result.set_text(res.get("summary", ""))
            result.style(f"color:{COLORS['success'] if ok else COLORS['danger']}")
            details_text.set_text(res.get("details") or "")
            details.set_visibility(bool(res.get("details")))
            ui.notify(res.get("summary", ""), type="positive" if ok else "negative", timeout=8000)

        async def pull() -> None:
            pull_btn.disable()
            try:
                res = await api.sync_pull()
            except api.ApiError as e:
                res = {"ok": False, "summary": str(e.detail.get("message", e.detail) if isinstance(e.detail, dict) else e.detail)}
            finally:
                pull_btn.enable()
            show(res)
            if res.get("ok") and res.get("restart_needed"):
                await restart_and_reload(force=False)
            else:
                await refresh()

        async def share() -> None:
            share_btn.disable()
            try:
                res = await api.sync_share(msg.value or "")
            except api.ApiError as e:
                res = {"ok": False, "summary": str(e.detail)}
            finally:
                share_btn.enable()
            show(res)
            if res.get("ok"):
                msg.set_value("")
            await refresh()

        pull_btn.on_click(pull)
        share_btn.on_click(share)
        await refresh()
