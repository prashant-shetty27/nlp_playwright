"""
ui/components/status_chip.py — Status Badge Chip

A small colored pill showing test execution status.

Statuses and colors (from ui/theme.STATUS_COLOR):
    PASS     → green  ✅
    FAIL     → red    ❌
    RUNNING  → amber  ▶
    SKIPPED  → grey   ⏭
    STOPPED  → orange ⏹
    PENDING  → grey   ⏳

Usage:
    from ui.components.status_chip import status_chip
    status_chip("pass")     # green PASS chip
    status_chip("running")  # amber animated RUNNING chip (pulse animation)

Props:
    status:  str — one of the statuses above (case-insensitive)
    animate: bool — if True, RUNNING shows a pulse animation (default True)
    size:    "sm" | "md" | "lg" (default "md")

Used in: dashboard.py, history.py, reports/index.py, executions/live.py
"""
