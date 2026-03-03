"""
ui/pages/executions/history.py — Execution History  (route: /history)

Browse all past test runs with filtering and drill-down.

Layout:
    Filter bar
        Date range picker
        Platform filter: All | Web | Android | iOS
        Status filter: All | Pass | Fail | Running | Stopped
        Search: by flow name / plan name

    Runs table
        Columns:
            Run ID          unique identifier
            Name            flow or plan name
            Platform        badge (Web / Android / iOS)
            Status          status_chip.py (PASS / FAIL / STOPPED)
            Steps           N passed / N total
            Duration        HH:MM:SS
            Started         relative timestamp ("2 hours ago")
            Actions         View Report | Re-run | Delete

    Pagination controls

On row click → /reports/<run_id>
Re-run → opens run_center.py pre-filled with same flow + profile

Data source:
    data/*/logs/  — execution log files (timestamped)
    Parsed to extract: run_id, flow_name, platform, status, step results, duration
"""
