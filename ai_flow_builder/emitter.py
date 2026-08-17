"""
ai_flow_builder/emitter.py

Renders approved mappings into a .flow file. Deterministic: the same mappings
always produce the same bytes. Emission happens only after structured validation
— a mapping that did not survive the mapper never reaches this module as a
statement, only as a provenance comment recording why.
"""
from __future__ import annotations

import datetime
import os
import textwrap

from ai_flow_builder.mapper import (NEEDS_LOCATOR, SUBSTITUTE, SUPPORTED,
                                    StepMapping, summarise)

RULE = "# " + "=" * 76
THIN = "# " + "-" * 76


def _wrap(text: str, prefix: str = "#   ", width: int = 78) -> list[str]:
    return textwrap.wrap(text, width=width,
                         initial_indent=prefix, subsequent_indent=prefix) or [prefix.rstrip()]


def render_clean(
    *,
    flow_name: str,
    source_ids: list[str],
    source_desc: dict,
    mappings: list[StepMapping],
    placeholders: dict[str, str],
    map_path: str,
) -> str:
    """
    Dashboard-friendly rendering: one statement per line, minimal header.

    Every line the dashboard shows is an executable step, so a failure maps
    directly to a visible row. Full traceability (source row, locator, resolved
    selector, expected value) lives in the sidecar map file instead of crowding
    the flow.
    """
    lines = [
        f"# {flow_name}",
        f"# Source : {source_desc.get('title', '')} | {', '.join(source_ids)}",
        f"# Params : {' '.join(placeholders) if placeholders else 'none'}",
        f"# Map    : {map_path}  (step -> source row, locator, selector, expected)",
        "",
    ]
    lines += [m.statement for m in mappings if m.emits]
    return "\n".join(lines) + "\n"


def build_map(
    *,
    flow_name: str,
    flow_path: str,
    source_ids: list[str],
    source_desc: dict,
    mappings: list[StepMapping],
    placeholders: dict[str, str],
    header_lines: int,
) -> dict:
    """
    Sidecar traceability map — one entry per executable step.

    Carries everything needed to debug a failure without opening the generator:
    which source row it came from, which locator it uses, that locator's CURRENT
    selector and where it is stored, and what the step expects.
    """
    import datetime

    from locators.manager import get_locator_and_dna

    def _locator_detail(name: str) -> dict:
        if not name:
            return {}
        try:
            selector, _dna = get_locator_and_dna(name)
        except Exception:
            selector = None
        return {
            "locator": name,
            "selector": selector,
            "stored_in": "data/locators_manual.json",
            "edit_hint": f"change the 'custom_xpath'/'selectors' of '{name}' if the DOM moved",
        }

    steps, line_no, step_no = [], header_lines, 0
    for m in mappings:
        if not m.emits:
            continue
        step_no += 1
        line_no += 1
        expected = ""
        if m.command_type in ("verify_element_exact", "verify_var_contains",
                              "verify_var_not_equals", "wait_until_text_not"):
            expected = m.statement.split('"')[1] if '"' in m.statement else ""
        steps.append({
            "step": step_no,
            "line": line_no,
            "statement": m.statement,
            "action": m.command_type,
            "source": m.source_ref,
            "manual_instruction": m.manual_step,
            "expected": expected,
            "note": m.note,
            **_locator_detail(m.locator),
        })

    return {
        "flow_name": flow_name,
        "flow_path": flow_path,
        "generated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_testcases": source_ids,
        "source": source_desc,
        "placeholders": placeholders,
        "steps": steps,
    }


def render(
    *,
    flow_name: str,
    source_ids: list[str],
    source_desc: dict,
    testcase_titles: dict[str, str],
    mappings: list[StepMapping],
    placeholders: dict[str, str],
    unresolved_variables: list[str],
    required_locators: dict[str, str],
    reused_locators: set[str],
    platform: str,
    generated_by: str,
    calibration_notes: list[str] | None = None,
) -> str:
    """Build the complete .flow text."""
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    lines: list[str] = []
    a = lines.append

    # ── Header / provenance ──────────────────────────────────────────────────
    a(RULE)
    a(f"# {flow_name}")
    a(THIN)
    a("# GENERATED — do not hand-edit; regenerate through the pipeline instead.")
    a(f"#   generated_by : {generated_by}")
    a(f"#   generated_at : {now}")
    a(f"#   platform     : {platform}")
    a("#")
    a("# SOURCE TESTCASES (one flow, several source IDs)")
    for tid in source_ids:
        for ln in _wrap(f"{tid} — {testcase_titles.get(tid, '')}", "#   "):
            a(ln)
    a("#")
    a(f"#   source_kind  : {source_desc.get('kind', '')}")
    a(f"#   spreadsheet  : {source_desc.get('title', '')}")
    a(f"#   location     : {source_desc.get('location', '')}")
    a(f"#   modified_utc : {source_desc.get('modified_utc', 'n/a')}")
    a(f"#   access       : {source_desc.get('access', '')}")
    a("#")

    # ── Placeholders ─────────────────────────────────────────────────────────
    a("# RUNTIME PLACEHOLDERS — resolved at execution, never stored in this file")
    for name, origin in placeholders.items():
        a(f"#   {name:<26} ← {origin}")
    if unresolved_variables:
        a("#")
        a("# UNRESOLVED SOURCE VARIABLES — must be supplied; never invented")
        for v in unresolved_variables:
            a(f"#   {v}")
    a("#")

    # ── Locators ─────────────────────────────────────────────────────────────
    a("# LOCATORS")
    for loc, element in sorted(required_locators.items()):
        state = "REUSED  " if loc in reused_locators else "REQUIRED"
        a(f"#   [{state}] {loc:<26} ← source element {element}")
    a("#")

    # ── Statuses ─────────────────────────────────────────────────────────────
    a("# MAPPING RESULT")
    for status, n in summarise(mappings).items():
        a(f"#   {status:<26} {n}")
    if calibration_notes:
        a("#")
        a("# NEEDS_FIRST_RUN_CALIBRATION")
        for note in calibration_notes:
            for ln in _wrap(note, "#   "):
                a(ln)
    a(RULE)
    a("")

    # ── Body, grouped by source testcase ─────────────────────────────────────
    current_tc = None
    for m in mappings:
        tc = m.source_ref.split()[0] if m.source_ref else ""
        if tc != current_tc:
            current_tc = tc
            a("")
            a(THIN)
            for ln in _wrap(f"{tc} — {testcase_titles.get(tc, '')}", "# "):
                a(ln)
            a(THIN)

        for ln in _wrap(f"[{m.source_ref}] {m.manual_step}", "# "):
            a(ln)
        if m.note:
            for ln in _wrap(f"note: {m.note}", "#     "):
                a(ln)

        if m.emits:
            a(m.statement)
        else:
            a(f"#     -> {m.status}: no statement emitted")
        a("")

    return "\n".join(lines).rstrip() + "\n"


def write(path: str, text: str, *, overwrite: bool = False) -> str:
    """Write the flow, refusing to clobber an existing file unless told to."""
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.exists(path) and not overwrite:
        raise FileExistsError(
            f"{path} already exists — pass --overwrite to replace it."
        )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path
