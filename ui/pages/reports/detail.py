"""
ui/pages/reports/detail.py — Report Detail  (route: /reports/<run_id>)

Step-by-step test run report with screenshots and logs.

Layout:
    Header
        Flow name | Platform badge | Overall status chip | Duration | Run date
        [Download HTML] [Download PDF] [Re-run] buttons

    Summary row
        Donut chart: Passed / Failed / Skipped steps
        Key metrics: total steps, pass rate %, first failure step

    Steps accordion (each step = one collapsible row)
        Collapsed: step number | NLP text | status chip | duration
        Expanded:
            Action type + target element + value used
            Locator strategy used + actual locator string
            Healing info (if ML healing kicked in): original vs healed locator
            Error message (if failed): full stack trace
            Screenshot thumbnail (click to enlarge)
            Log lines for this step

    Screenshots strip
        All screenshots from the run in order
        Click to open full-screen viewer with prev/next navigation

    Logs panel (collapsible at bottom)
        Full raw log output (components/log_viewer.py)
        Download as .txt

Data source:
    data/*/logs/<run_id>*.log
    data/*/screenshots/<run_id>_*.png
    reporting/report_manager.py
"""
