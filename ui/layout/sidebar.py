"""
ui/layout/sidebar.py — Left navigation sidebar

Persistent navigation panel rendered on every page.

Nav sections:
    Dashboard       → /
    ─────────────────────────────
    Platforms
      Web           → /platform/web
      Android       → /platform/android
      iOS           → /platform/ios
    ─────────────────────────────
    Test Plans      → /plans
    Run Center      → /run
    History         → /history
    ─────────────────────────────
    Test Data       → /data
    Reports         → /reports
    ─────────────────────────────
    Settings        → /settings

Active route is highlighted. Platform items show colored dot (theme.PLATFORM_COLOR).
Collapsed state (icon-only) supported for narrow screens.
"""
