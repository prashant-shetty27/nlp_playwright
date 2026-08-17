"""
ai_flow_builder/sources — pluggable testcase sources.

The ONLY thing a source adapter does is return raw tab data:

    read_tabs() -> {tab_name: [[cell, ...], ...]}

Everything downstream — testcase assembly, variable joining, mapping, emission —
is shared and lives in ai_flow_builder/testcase.py. Adding a source must never
mean duplicating parsing or mapping logic.

Adapters
--------
  xlsx_source.XlsxSource        — a local .xlsx export (openpyxl; no auth)
  gsheets_source.GoogleSheetsSource — the live Google Sheet (read-only; needs auth)
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SourceAdapter(Protocol):
    """Every testcase source implements exactly this."""

    #: Short identifier recorded in each Testcase.source for provenance.
    kind: str

    def describe(self) -> dict:
        """Provenance: title, location, last-modified, and anything auth-relevant."""
        ...

    def read_tabs(self) -> dict[str, list[list]]:
        """Return {tab_name: rows}, where each row is a list of raw cell values."""
        ...


__all__ = ["SourceAdapter"]
