"""
ui/layout/sidebar.py — Left navigation sidebar

Persistent navigation panel rendered on every page. Active route is highlighted;
platform items show a coloured dot (theme.PLATFORM_COLOR).

Divergence from the spec: the spec listed three platform entries (Web, Android,
iOS). The platform vocabulary is now five, and two of them — Android and iOS —
are known but not enabled. They are shown greyed rather than hidden, because a
missing entry reads as "unsupported" while a greyed one reads as "not yet", which
is the truth. The list is fetched from GET /nlp/platforms so it cannot drift.
"""
from __future__ import annotations

from nicegui import ui

from ui.theme import COLORS, TYPOGRAPHY, platform_color

NAV = [
    ("Dashboard", "/", "dashboard", None),
    ("__sep__", "", "", None),
    ("__head__", "Author", "", None),
    ("Test Cases", "/platform/website", "list_alt", None),
    ("Elements", "/platform/website/elements", "my_location", None),
    # Step groups sit with Elements because they are the same kind of thing: a
    # named, reusable piece you assemble tests from, stored per platform.
    ("Step Groups", "/step-groups", "playlist_add_check", None),
    ("__sep__", "", "", None),
    ("__head__", "Execute", "", None),
    ("Run Center", "/run", "play_circle", None),
    ("History", "/history", "history", None),
    ("__sep__", "", "", None),
    ("__head__", "Manage", "", None),
    ("Test Data", "/data/variables", "dataset", None),
    ("Reports", "/reports", "assessment", None),
    ("Settings", "/settings", "settings", None),
]


def sidebar(active: str = "", platforms: list[dict] | None = None) -> None:
    with ui.left_drawer(value=True, fixed=True).props("bordered width=232") \
            .style(f"background:{COLORS['surface_alt']}"):
        with ui.column().classes("w-full gap-0 p-2"):
            ui.label("Codeless Automation").style(
                f"font-weight:{TYPOGRAPHY['weight_bold']}; font-size:{TYPOGRAPHY['size_md']};"
                f"color:{COLORS['text']}; padding:6px 8px 10px")

            for label, route, icon, _ in NAV:
                if label == "__sep__":
                    ui.separator().style("margin:6px 0")
                    continue
                if label == "__head__":
                    ui.label(route.upper()).style(
                        f"font-size:0.66rem; letter-spacing:.08em; padding:6px 8px 2px;"
                        f"color:{COLORS['text_muted']}; font-weight:{TYPOGRAPHY['weight_bold']}")
                    continue
                on = active == route or (route != "/" and active.startswith(route))
                with ui.row().classes("w-full items-center gap-2 cursor-pointer") \
                        .style(f"padding:6px 8px; border-radius:6px;"
                               f"background:{COLORS['primary'] + '14' if on else 'transparent'}") \
                        .on("click", lambda r=route: ui.navigate.to(r)):
                    ui.icon(icon).style(
                        f"color:{COLORS['primary'] if on else COLORS['text_muted']}")
                    ui.label(label).style(
                        f"font-size:{TYPOGRAPHY['size_sm']};"
                        f"color:{COLORS['primary'] if on else COLORS['text']};"
                        f"font-weight:{TYPOGRAPHY['weight_medium'] if on else TYPOGRAPHY['weight_normal']}")

            if platforms:
                ui.separator().style("margin:6px 0")
                ui.label("PLATFORMS").style(
                    f"font-size:0.66rem; letter-spacing:.08em; padding:6px 8px 2px;"
                    f"color:{COLORS['text_muted']}; font-weight:{TYPOGRAPHY['weight_bold']}")
                for p in platforms:
                    enabled = p.get("enabled", True)
                    with ui.row().classes("w-full items-center gap-2") \
                            .style("padding:4px 8px"):
                        ui.label("●").style(
                            f"color:{platform_color(p['name']) if enabled else '#CBD5E1'};"
                            f"font-size:0.7rem")
                        lab = ui.label(p.get("label", p["name"])).style(
                            f"font-size:{TYPOGRAPHY['size_xs']};"
                            f"color:{COLORS['text'] if enabled else COLORS['text_muted']}")
                        if not enabled:
                            lab.tooltip("Known platform, not enabled yet")
