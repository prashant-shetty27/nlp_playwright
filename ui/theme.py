"""
ui/theme.py — Global theme constants

Defines colors, typography, spacing, and dark/light mode tokens
used consistently across all NiceGUI pages and components.

Sections:
    COLORS       — Primary, accent, surface, status colors
    TYPOGRAPHY   — Font family, sizes, weights
    PLATFORM     — Per-platform accent colours
    STATUS       — PASS=green, FAIL=red, RUNNING=amber, SKIPPED=grey
    MAPPING      — Per-step generation status (SUPPORTED / NEEDS_LOCATOR / …)

Usage:
    from ui.theme import COLORS, PLATFORM_COLOR
    ui.label("Web").style(f"color: {PLATFORM_COLOR['website']}")
"""

COLORS = {
    "primary": "#2563EB",
    "accent": "#7C3AED",
    "surface": "#FFFFFF",
    "surface_alt": "#F8FAFC",
    "border": "#E2E8F0",
    "text": "#0F172A",
    "text_muted": "#64748B",
    "danger": "#DC2626",
    "warning": "#D97706",
    "success": "#16A34A",
}

TYPOGRAPHY = {
    "family": "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    "mono": "'SF Mono', 'JetBrains Mono', Menlo, monospace",
    "size_xs": "0.75rem",
    "size_sm": "0.875rem",
    "size_md": "1rem",
    "size_lg": "1.25rem",
    "size_xl": "1.5rem",
    "weight_normal": "400",
    "weight_medium": "500",
    "weight_bold": "600",
}

#: Keyed by the CANONICAL platform names from nlp/platforms.py, not the spec's
#: original web/android/ios. The two mobile-web platforms share the web runner
#: but are distinct choices, so they get distinct colours; the legacy keys are
#: retained so older callers keep working.
PLATFORM_COLOR = {
    "website": "#2563EB",       # blue-600
    "mobilesite": "#0891B2",    # cyan-600
    "android": "#16A34A",       # green-600
    "ios": "#7C3AED",           # violet-600
    "hybrid": "#DB2777",        # pink-600
    "web": "#2563EB",           # legacy alias
    "mobile": "#0891B2",        # legacy alias
}

STATUS_COLOR = {
    "pass": "#16A34A",
    "passed": "#16A34A",
    "fail": "#DC2626",
    "failed": "#DC2626",
    "running": "#D97706",
    "skipped": "#6B7280",
    "stopped": "#EA580C",
    "pending": "#6B7280",
    "done": "#16A34A",
}

#: Generation statuses from ai_flow_builder/mapper.py. The operator acts on these
#: differently, so they must not all look like "error": NEEDS_LOCATOR is a request
#: for input, UNSUPPORTED_ACTION is often satisfied structurally, and only
#: NEEDS_CLARIFICATION genuinely needs the step rewritten.
MAPPING_COLOR = {
    "SUPPORTED": "#16A34A",
    "SUPPORTED_VIA_SUBSTITUTE": "#0891B2",
    "NEEDS_LOCATOR": "#D97706",
    "UNSUPPORTED_ACTION": "#6B7280",
    "NEEDS_CLARIFICATION": "#DC2626",
}

MAPPING_HINT = {
    "SUPPORTED": "Ready to run",
    "SUPPORTED_VIA_SUBSTITUTE": "Mapped to an equivalent command",
    "NEEDS_LOCATOR": "Needs a locator before this can run",
    "UNSUPPORTED_ACTION": "No command for this — often already satisfied; check the note",
    "NEEDS_CLARIFICATION": "Could not be interpreted — rewrite the step",
}

#: Action badge colours from the step_row spec, keyed by parsed command type.
ACTION_COLOR = {
    "click": "#2563EB", "js_click": "#2563EB",
    "fill": "#0D9488", "type": "#0D9488", "type_text": "#0D9488",
    "enter_otp": "#0D9488",
    "verify_element_visible": "#16A34A", "verify_element_exact": "#16A34A",
    "verify_element_contains": "#16A34A", "verify_text": "#16A34A",
    "verify_var_contains": "#16A34A", "verify_var_not_equals": "#16A34A",
    "verify_element_not_visible": "#16A34A", "verify_element_not_exists": "#16A34A",
    "wait": "#6B7280", "wait_until_visible": "#6B7280",
    "wait_until_text_not": "#6B7280", "wait_for_element": "#6B7280",
    "tap": "#EA580C", "tap_text": "#EA580C",
    "swipe_left": "#EA580C", "swipe_right": "#EA580C",
    "screenshot": "#7C3AED",
    "open": "#4F46E5",
    "fetch_otp": "#DB2777",
}
DEFAULT_ACTION_COLOR = "#64748B"


def action_color(action: str) -> str:
    return ACTION_COLOR.get((action or "").lower(), DEFAULT_ACTION_COLOR)


def platform_color(platform: str) -> str:
    return PLATFORM_COLOR.get((platform or "").lower(), COLORS["text_muted"])


def status_color(status: str) -> str:
    return STATUS_COLOR.get((status or "").lower(), STATUS_COLOR["pending"])
