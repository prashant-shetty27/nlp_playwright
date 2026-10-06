"""
ui/components/group_children.py — open a step group in a run / report.

Under a `call <group>` step: a fold showing every inner step with its result
(✓ passed · ✗ failed + reason · ⚠ ignored · ⊘ not run), nested for groups that
call groups. Used by the live view and the report page, pass or fail.
"""
from __future__ import annotations

from nicegui import ui

from ui.theme import COLORS, TYPOGRAPHY

_MARK = {"passed": ("check_circle", "success"), "failed": ("cancel", "danger"),
         "ignored": ("warning_amber", "warning"), "skipped": ("remove_circle_outline", "text_muted")}


def _counts(children: list[dict]) -> str:
    n = {k: sum(1 for c in children if c.get("status") == k) for k in _MARK}
    bits = [f"{n['passed']} passed"]
    if n["failed"]:
        bits.append(f"{n['failed']} failed")
    if n["ignored"]:
        bits.append(f"{n['ignored']} ignored")
    if n["skipped"]:
        bits.append(f"{n['skipped']} not run")
    return " · ".join(bits)


def _open_frame(rel_path: str) -> None:
    ui.navigate.to(f"/screenshots/{rel_path.lstrip('/')}", new_tab=True)


def render_children(children: list[dict], *, indent: str = "3.2rem", open_: bool = False,
                    on_frame=None) -> None:
    """`on_frame(rel_path, child)` is called when a row that has a screenshot is
    clicked (the live view shows it in its viewer); without it the frame opens
    in a new tab."""
    if not children:
        return
    show = on_frame or (lambda path, _c: _open_frame(path))
    failed = any(c.get("status") == "failed" for c in children)
    with ui.expansion(f"Step group — {len(children)} steps · {_counts(children)}",
                      icon="account_tree", value=open_ or failed) \
            .classes("w-full").props("dense header-class=text-xs") \
            .style(f"margin-left:{indent}; width:calc(100% - {indent});"
                   f"border-left:3px solid {COLORS['primary']}55; background:{COLORS['primary']}08"):
        for i, c in enumerate(children, 1):
            st = c.get("status", "")
            icon, tone = _MARK.get(st, ("radio_button_unchecked", "text_muted"))
            colour = COLORS.get(tone, COLORS["text_muted"])
            has_frame = bool(c.get("screenshot"))
            row_el = ui.row().classes("w-full items-start no-wrap gap-2" + (" cursor-pointer" if has_frame else "")) \
                .style("padding:3px 8px")
            if has_frame:
                row_el.tooltip("Click to see this step's screenshot")
                row_el.on("click", lambda _, p=c["screenshot"], c=c: show(p, c))
            with row_el:
                ui.label(str(i)).style(
                    f"width:1.4rem; text-align:right; color:{COLORS['text_muted']};"
                    f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']}")
                ui.icon(icon).style(f"color:{colour}; font-size:0.95rem; margin-top:1px")
                with ui.column().classes("gap-0").style("min-width:0; flex:1"):
                    ui.label(c.get("step", "") + (f"  →  {c['note']}" if c.get("note") else "")).style(
                        f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']};"
                        f"word-break:break-all; color:{COLORS['text'] if st != 'skipped' else COLORS['text_muted']};"
                        + ("opacity:.65;" if st == "skipped" else ""))
                    if c.get("error"):
                        ui.label(c["error"]).style(
                            f"font-size:{TYPOGRAPHY['size_xs']}; white-space:pre-wrap;"
                            f"color:{colour if st != 'skipped' else COLORS['text_muted']};"
                            + ("font-style:italic;" if st == "skipped" else ""))
                if c.get("duration_ms") is not None:
                    ui.label(f"{c['duration_ms'] / 1000:.1f}s").style(
                        f"color:{COLORS['text_muted']}; font-size:{TYPOGRAPHY['size_xs']};"
                        f"font-family:{TYPOGRAPHY['mono']}; white-space:nowrap")
            if c.get("children"):
                render_children(c["children"], indent="1.6rem", on_frame=on_frame)
