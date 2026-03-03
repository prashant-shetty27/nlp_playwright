"""
ui/pages/reports/index.py — Reports list  (route: /reports)

All generated test reports filterable by platform, status, and date.

Layout:
    Filter bar
        Date range picker
        Platform: All | Web | Android | iOS
        Status: All | Pass | Fail
        Search by flow/plan name

    Reports table
        Columns:
            Report ID
            Flow / Plan name
            Platform badge
            Status (PASS / FAIL)
            Total steps | Passed | Failed | Skipped
            Duration
            Run date
            Actions: View | Download HTML | Download PDF | Delete

    Summary stats strip at top
        Total runs this week | Pass rate % | Avg duration | Most failed flow

Data source:
    Aggregated from data/*/logs/ + data/*/screenshots/
    reporting/report_manager.py provides data model
"""
