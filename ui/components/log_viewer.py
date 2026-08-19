"""
ui/components/log_viewer.py — Live log panel

Real-time scrollable log with an autoscroll toggle.

Props:
    max_lines: int — ring buffer size. A run can emit thousands of lines; keeping
               them all would grow the page without bound, which on a long
               execution is the difference between a responsive tab and a stalled
               one. Oldest lines are dropped.

Usage:
    log = log_viewer()
    log.push("step 1 passed")
    log.clear()

Used in: pages/executions/live.py
"""
from __future__ import annotations

from nicegui import ui

from ui.theme import COLORS, TYPOGRAPHY


class LogViewer:
    def __init__(self, max_lines: int = 800) -> None:
        self.max_lines = max_lines
        self._lines: list[str] = []
        with ui.column().classes("w-full gap-1"):
            with ui.row().classes("w-full items-center gap-2"):
                self.autoscroll = ui.switch("Autoscroll", value=True).props("dense")
                ui.space()
                ui.button("Clear", icon="clear_all",
                          on_click=self.clear).props("flat dense size=sm")
            self.area = ui.log(max_lines=max_lines).classes("w-full").style(
                f"height:22rem; font-family:{TYPOGRAPHY['mono']};"
                f"font-size:{TYPOGRAPHY['size_xs']}; background:{COLORS['surface_alt']};"
                f"border:1px solid {COLORS['border']}; border-radius:6px")

    def push(self, line: str) -> None:
        self._lines.append(line)
        if len(self._lines) > self.max_lines:
            self._lines = self._lines[-self.max_lines:]
        if self.autoscroll.value:
            self.area.push(line)
        else:
            self.area.push(line)          # ui.log keeps its own scroll position

    def clear(self) -> None:
        self._lines.clear()
        self.area.clear()


def log_viewer(max_lines: int = 800) -> LogViewer:
    return LogViewer(max_lines)
