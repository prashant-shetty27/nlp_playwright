"""
ui/components/log_viewer.py — Real-time Log Viewer

A scrollable, auto-updating log panel for streaming runner output.

Features:
    - Monospace font, dark background
    - Color-coded log levels:
        INFO    → white
        WARNING → amber
        ERROR   → red
        PASS    → green
        FAIL    → red bold
        DEBUG   → grey
    - Autoscroll toggle (follows tail by default)
    - [Clear] button
    - [Download .txt] button
    - Max lines buffer (default 500, older lines dropped)

Props:
    log_source: AsyncIterator[str] | list[str]
        Pass an async generator for live streaming, or a list for replay
    autoscroll: bool (default True)
    max_lines:  int (default 500)
    height:     str (default "400px")

Implementation note:
    For live runs → bind to subprocess stdout via asyncio.create_subprocess_exec
    For history  → read from data/*/logs/<run_id>.log

Used in: pages/executions/live.py, pages/reports/detail.py
"""
