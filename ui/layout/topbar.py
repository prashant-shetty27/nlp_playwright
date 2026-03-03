"""
ui/layout/topbar.py — Top application bar

Rendered at the top of every page.

Contents (left → right):
    Breadcrumb trail        e.g. "Platforms / Android / Test Cases"
    Platform switcher       Quick-jump dropdown: Web | Android | iOS
    Environment badge       Shows active env (local / staging / cloud)
    Quick Run button        Opens run_center with current context pre-filled
    User/settings icon      → /settings

Reads active platform from app-level state so the switcher stays in sync
with the sidebar selection.
"""
