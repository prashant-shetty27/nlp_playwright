"""ui/pages/admin/users.py — Manage → Users (admins only)."""
from __future__ import annotations

from nicegui import ui

from ui import api_client as api
from ui.auth import current
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.theme import COLORS, TYPOGRAPHY

ROLE_HELP = {
    "admin": "everything, including managing users",
    "editor": "write and run test cases, elements, suites and plans",
    "viewer": "read-only: sees everything, changes and runs nothing",
}


async def render() -> None:
    try:
        platforms = await api.platforms()
    except api.ApiError:
        platforms = []
    sidebar(active="/users", platforms=platforms)
    topbar(["Manage", "Users"], platforms=platforms)
    me = current()
    with ui.column().classes("w-full gap-3 p-4").style("max-width:64rem"):
        if not me or me["role"] != "admin":
            ui.label("Only admins can manage users.").style(f"color:{COLORS['text_muted']}")
            return
        with ui.row().classes("w-full items-center"):
            ui.label("Users").style(f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
            ui.space()
            ui.button("Add user", icon="person_add", on_click=lambda: _edit_dialog(None)) \
                .props("unelevated").style(f"background:{COLORS['primary']}")
        with ui.row().classes("gap-4"):
            for r, h in ROLE_HELP.items():
                ui.label(f"{r}: {h}").style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
        try:
            data = await api.list_users()
        except api.ApiError as e:
            ui.label(e.detail).style(f"color:{COLORS['danger']}")
            return
        cols = [{"name": k, "label": l, "field": k, "align": "left"} for k, l in (
            ("username", "Username"), ("name", "Name"), ("email", "Email"), ("role", "Role"),
            ("status", "Status"), ("last_login", "Last sign-in"), ("created_by", "Added by"))]
        rows = [{**u, "status": "active" if u["active"] else "disabled",
                 "last_login": (u.get("last_login") or "")[:16].replace("T", " ")}
                for u in data["users"]]
        table = ui.table(columns=cols, rows=rows, row_key="username").classes("w-full")
        table.add_slot("body-cell-username", r'''
            <q-td :props="props"><a class="cursor-pointer text-primary"
              @click="$parent.$emit('edit', props.row)">{{ props.row.username }}</a></q-td>''')
        table.on("edit", lambda e: _edit_dialog(e.args))


def _edit_dialog(user: dict | None) -> None:
    new = user is None
    me = current() or {}
    dialog = ui.dialog().props("persistent")
    with dialog, ui.card().style("width:30rem"):
        ui.label("Add user" if new else f"Edit {user['username']}").style(
            f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
        uname = ui.input("Username", value="" if new else user["username"]) \
            .props("outlined dense" + ("" if new else " readonly")).classes("w-full")
        name = ui.input("Full name", value="" if new else user.get("name", "")).props("outlined dense").classes("w-full")
        email = ui.input("Email", value="" if new else user.get("email", "")).props("outlined dense").classes("w-full")
        role = ui.select({r: f"{r} — {h}" for r, h in ROLE_HELP.items()},
                         value="editor" if new else user["role"], label="Role") \
            .props("outlined dense").classes("w-full")
        active = None if new else ui.switch("Active (can sign in)", value=user["active"])
        pw = ui.input("Password" if new else "New password (leave blank to keep)", password=True,
                      password_toggle_button=True).props("outlined dense").classes("w-full")
        msg = ui.label().style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['danger']}")

        async def save() -> None:
            try:
                if new:
                    await api.create_user((uname.value or "").strip().lower(), pw.value or "",
                                          name.value or "", email.value or "", role.value)
                else:
                    fields = {"name": name.value or "", "email": email.value or "",
                              "role": role.value, "active": bool(active.value)}
                    if pw.value:
                        fields["password"] = pw.value
                    await api.update_user(user["username"], **fields)
            except api.ApiError as e:
                msg.set_text(e.detail)
                return
            dialog.close()
            ui.notify("Saved", type="positive")
            ui.navigate.reload()

        def confirm_delete() -> None:
            with ui.dialog() as d2, ui.card():
                ui.label(f"Delete user {user['username']}? Their past runs keep their name.")
                async def go() -> None:
                    try:
                        await api.delete_user(user["username"])
                    except api.ApiError as e:
                        ui.notify(e.detail, type="negative")
                        return
                    d2.close(); dialog.close()
                    ui.navigate.reload()
                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=d2.close).props("flat")
                    ui.button("Delete", on_click=go).props("unelevated color=negative")
            d2.open()

        with ui.row().classes("w-full items-center gap-2"):
            if not new and user["username"] != me.get("username"):
                ui.button("Delete", icon="delete_outline", on_click=confirm_delete) \
                    .props("flat color=negative")
            ui.space()
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save", on_click=save).props("unelevated")
    dialog.open()
