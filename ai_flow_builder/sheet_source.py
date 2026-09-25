"""
ai_flow_builder/sheet_source.py — spreadsheets as drafting context.

A tester's manual cases usually live in a Google Sheet or an .xlsx: the QA
round for a ticket, a coverage tracker, an attachment on the story. Handed to
the drafter, those become the baseline it must cover and extend — instead of
re-inventing cases the team already reviewed.

Two entry points:
  read_sheet_bytes(name, data)     → text table from an uploaded .xlsx / .csv
  fetch_google_sheet(url)          → the same for a docs.google.com link, via
                                      its CSV export (needs "anyone with the
                                      link can view", or the fetch says so)
"""
from __future__ import annotations

import csv
import io
import re

_GSHEET = re.compile(r"https?://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)\S*", re.I)
MAX_ROWS = 400
MAX_CHARS = 60000


class SheetError(RuntimeError):
    """A sheet could not be read; the message says what to do."""


def find_google_sheets(text: str) -> list[tuple[str, str]]:
    """(sheet id, gid or '') for every Google Sheets link in `text`."""
    out = []
    for m in _GSHEET.finditer(text or ""):
        g = re.search(r"gid=(\d+)", m.group(0))
        key = (m.group(1), g.group(1) if g else "")
        if key not in out:
            out.append(key)
    return out


def _rows_to_text(name: str, rows: list[list]) -> str:
    kept = []
    for r in rows[:MAX_ROWS]:
        cells = ["" if c is None else str(c).replace("\n", " ").strip() for c in r]
        if any(cells):
            kept.append(" | ".join(cells).rstrip(" |"))
    body = "\n".join(kept)
    if len(body) > MAX_CHARS:
        body = body[:MAX_CHARS] + "\n… (sheet trimmed)"
    return f"===== SHEET: {name} ({len(kept)} rows) =====\n{body}"


def read_sheet_bytes(name: str, data: bytes) -> str:
    low = (name or "").lower()
    if low.endswith((".xlsx", ".xlsm", ".xltx")):
        try:
            import openpyxl
        except ImportError as e:  # pragma: no cover
            raise SheetError("openpyxl is not installed; upload a .csv instead.") from e
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts = []
        for ws in wb.worksheets[:6]:
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            if any(any(c not in (None, "") for c in r) for r in rows):
                parts.append(_rows_to_text(f"{name} / {ws.title}", rows))
        return "\n\n".join(parts) or f"===== SHEET: {name} (empty) ====="
    text = data.decode("utf-8", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    return _rows_to_text(name, rows)


def fetch_google_sheet(sheet_id: str, gid: str = "") -> str:
    import requests

    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv" + (f"&gid={gid}" if gid else "")
    try:
        r = requests.get(url, timeout=30, allow_redirects=True)
    except Exception as e:  # noqa: BLE001
        raise SheetError(f"Could not reach Google Sheets: {e}") from e
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or "text/html" in ctype:
        raise SheetError(
            "The Google Sheet is not readable without signing in. Share it as "
            "'Anyone with the link — Viewer' (or download it as .xlsx and attach it here).")
    return read_sheet_bytes(f"google-sheet {sheet_id}" + (f" gid {gid}" if gid else "") + ".csv", r.content)


_GDOC = re.compile(r"https?://docs\.google\.com/document/d/([A-Za-z0-9_-]+)", re.I)


def fetch_google_doc(doc_id: str) -> str:
    """A Google Doc (test notes, a spec) as plain text, via its txt export."""
    import requests

    url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
    try:
        r = requests.get(url, timeout=30, allow_redirects=True)
    except Exception as e:  # noqa: BLE001
        raise SheetError(f"Could not reach Google Docs: {e}") from e
    if r.status_code != 200 or "text/html" in r.headers.get("content-type", ""):
        raise SheetError("The Google Doc is not readable without signing in. Share it as "
                         "'Anyone with the link — Viewer'.")
    text = r.content.decode("utf-8", errors="replace").strip()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n… (document trimmed)"
    return f"===== GOOGLE DOC {doc_id} =====\n{text}"


def sheets_from_prompt(text: str) -> tuple[list[str], list[str]]:
    """
    (texts, problems) for every Google Sheets / Docs link in `text`.

    `text` is the prompt PLUS the ticket's comments: the QA round's sheet is
    usually posted as a comment ("Cases: https://docs.google.com/…"), not in
    the prompt, and it is exactly the baseline the draft should cover.
    """
    texts, problems = [], []
    for sid, gid in find_google_sheets(text):
        try:
            texts.append(fetch_google_sheet(sid, gid))
        except SheetError as e:
            problems.append(f"Sheet {sid[:8]}…: {e}")
    seen = set()
    for m in _GDOC.finditer(text or ""):
        did = m.group(1)
        if did in seen:
            continue
        seen.add(did)
        try:
            texts.append(fetch_google_doc(did))
        except SheetError as e:
            problems.append(f"Doc {did[:8]}…: {e}")
    return texts, problems
