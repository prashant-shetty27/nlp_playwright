"""Shared bits for the Suites / Plans / Plan Run screens."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nicegui import ui

from ui.theme import COLORS, TYPOGRAPHY

STATUS_COLOR = {
    "passed": COLORS["success"], "failed": COLORS["danger"], "error": COLORS["danger"],
    "running": COLORS["primary"], "queued": COLORS["warning"], "pending": COLORS["text_muted"],
    "not_run": COLORS["text_muted"], "stopped": COLORS["warning"], "missed": COLORS["warning"],
}
STATUS_ICON = {"passed": "✅", "failed": "❌", "error": "💥", "running": "🔄", "queued": "⏳",
               "pending": "⏳", "not_run": "⊘", "stopped": "⏹", "missed": "⏰"}


def _ist():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Kolkata")
    except Exception:  # noqa: BLE001
        return timezone(timedelta(hours=5, minutes=30))


def ist(iso: str, fmt: str = "%d %b %H:%M") -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).astimezone(_ist()).strftime(fmt) + " IST"
    except ValueError:
        return iso


def chip(status: str) -> None:
    c = STATUS_COLOR.get(status, COLORS["text_muted"])
    ui.label(f"{STATUS_ICON.get(status, '')} {status.replace('_', ' ')}").style(
        f"background:{c}1A; color:{c}; border-radius:10px; padding:1px 9px;"
        f"font-size:{TYPOGRAPHY['size_xs']}; font-weight:{TYPOGRAPHY['weight_medium']};"
        f"white-space:nowrap")


def heading(text: str) -> None:
    ui.label(text).style(f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")


def muted(text: str) -> ui.label:
    return ui.label(text).style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")


def confirm(title: str, body: str, on_yes, button: str = "Delete") -> None:
    with ui.dialog() as d, ui.card().style("width:30rem"):
        ui.label(title).style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
        muted(body)

        async def go() -> None:
            d.close()
            r = on_yes()
            if hasattr(r, "__await__"):
                await r

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=d.close).props("flat")
            ui.button(button, on_click=go).props("unelevated color=negative")
    d.open()
