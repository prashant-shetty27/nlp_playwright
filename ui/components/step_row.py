"""
ui/components/step_row.py — NLP Step Row

A single row in a flow's step list. Displays one NLP command with
action type badge, target, and inline edit capability.

Visual layout (horizontal):
    [drag handle] [step #] [action badge] [NLP text] [element name] [edit] [delete]

Props:
    index:      int — step number (1-based)
    nlp_text:   str — raw NLP command string (e.g. "click login button")
    action:     str — parsed action type (e.g. "click", "type", "verify")
    target:     str — element name or literal value
    on_edit:    callable — called when user clicks edit
    on_delete:  callable — called when user clicks delete

Action badge colors:
    click    → blue
    type     → teal
    verify   → green
    wait     → grey
    tap      → orange (mobile)
    swipe    → orange (mobile)
    screenshot → purple

Drag-to-reorder: uses NiceGUI drag/drop events to swap step order.

Used in: pages/platform/web/test_cases.py, android/test_cases.py, ios/test_cases.py
"""
