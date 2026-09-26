"""ui/pages/login.py — sign in, or create the first admin on a fresh install."""
from __future__ import annotations

from nicegui import ui

from ui import api_client as api
from ui.auth import sign_in
from ui.theme import COLORS, TYPOGRAPHY


async def render(next_path: str = "/") -> None:
    try:
        first = await api.setup_needed()
    except api.ApiError:
        first = False
    target = next_path if (next_path or "").startswith("/") and not next_path.startswith("/login") else "/"

    with ui.column().classes("absolute-center items-center gap-3").style("width:24rem"):
        ui.label("Codeless Automation").style(
            f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
        with ui.card().classes("w-full").style("padding:1.4rem"):
            if first:
                ui.label("Create the first admin").style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
                ui.label("No users exist yet. This account manages everyone else.").style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
                full = ui.input("Full name").props("outlined dense").classes("w-full")
                email = ui.input("Email").props("outlined dense").classes("w-full")
            uname = ui.input("Username").props("outlined dense autofocus").classes("w-full")
            pw = ui.input("Password", password=True, password_toggle_button=True) \
                .props("outlined dense").classes("w-full")
            msg = ui.label().style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['danger']}")

            async def go() -> None:
                try:
                    if first:
                        u = await api.setup_admin((uname.value or "").strip().lower(), pw.value or "",
                                                  full.value or "", email.value or "")
                    else:
                        u = await api.login((uname.value or "").strip().lower(), pw.value or "")
                except api.ApiError as e:
                    msg.set_text(e.detail)
                    return
                sign_in(u)
                ui.navigate.to(target)

            pw.on("keydown.enter", go)
            ui.button("Create admin & sign in" if first else "Sign in", on_click=go) \
                .props("unelevated").classes("w-full").style(f"background:{COLORS['primary']}")
        ui.label("Forgot your password? Ask an admin to reset it under Manage → Users.").style(
            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
