"""
ai_flow_builder/sources/xlsx_source.py

Reads a local .xlsx workbook. No authentication, no network. Used both for
standalone workbooks and as a faithful stand-in for a Google Sheet export while
Google authentication is being configured.

Returns raw tab data only — all parsing is shared (see ai_flow_builder/testcase.py).
"""
from __future__ import annotations

import datetime
import os


class XlsxSource:
    kind = "xlsx"

    def __init__(self, path: str):
        self.path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"Workbook not found: {self.path}")

    def describe(self) -> dict:
        mtime = datetime.datetime.utcfromtimestamp(os.path.getmtime(self.path))
        return {
            "kind": self.kind,
            "title": os.path.splitext(os.path.basename(self.path))[0],
            "location": self.path,
            "size_bytes": os.path.getsize(self.path),
            "modified_utc": mtime.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "auth": "none required (local file)",
            "access": "read-only (opened read_only=True)",
        }

    def read_tabs(self) -> dict[str, list[list]]:
        import openpyxl

        wb = openpyxl.load_workbook(self.path, data_only=True, read_only=True)
        try:
            return {
                ws.title: [list(r) for r in ws.iter_rows(values_only=True)]
                for ws in wb.worksheets
            }
        finally:
            wb.close()
