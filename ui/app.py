"""
ui/app.py — Main application entry point

Initialises NiceGUI, registers all page routes, and mounts the FastAPI backend.

Usage:
    python -m ui.app          # start the full UI server (default port 8080)
    python -m ui.app --port 9000

Routes registered here (each delegates to a pages/ module):
    /                         → pages/dashboard.py
    /platform/web             → pages/platform/web/test_cases.py
    /platform/web/elements    → pages/platform/web/elements.py
    /platform/web/recorder    → pages/platform/web/recorder.py
    /platform/android         → pages/platform/android/test_cases.py
    /platform/android/elements→ pages/platform/android/elements.py
    /platform/android/recorder→ pages/platform/android/recorder.py
    /platform/ios             → pages/platform/ios/test_cases.py
    /platform/ios/elements    → pages/platform/ios/elements.py
    /platform/ios/recorder    → pages/platform/ios/recorder.py
    /plans                    → pages/plans/index.py
    /plans/new                → pages/plans/editor.py
    /run                      → pages/executions/run_center.py
    /run/live                 → pages/executions/live.py
    /history                  → pages/executions/history.py
    /data                     → pages/data/datasets.py
    /data/media               → pages/data/media_library.py
    /data/variables           → pages/data/variables.py
    /reports                  → pages/reports/index.py
    /settings                 → pages/settings/environments.py
"""
