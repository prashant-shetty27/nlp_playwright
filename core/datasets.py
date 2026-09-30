"""
core/datasets.py — uploaded tables (Excel / CSV) that tests loop over.

A data set is a table: the first row holds the column headings, every other
row is one set of inputs. In a step a column is used by its heading:

    for each row in city_list rows 2 to 5
        enter ${city} in search_city_box
    end for

Storage
-------
    data/common/datasets/<name>.csv      the table, always saved as CSV
    data/common/datasets/<name>.meta.json who created it, who changed it last

An Excel workbook with several sheets becomes one data set per sheet
(<file>_<sheet>); a single-sheet workbook keeps the file's name. Everything is
stored as text, exactly as a tester would read it in the sheet — an Excel
number cell 9000000001 is "9000000001", never "9000000001.0".

Column headings are turned into step names the same way everywhere
(lower case, spaces and punctuation become "_"), so "Mobile Number" is
${mobile_number}. The preview on the Data Sets screen shows the name to use.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import os
import re
import threading

from config import settings

DIR = os.path.join(settings.BASE_DIR, "data", "common", "datasets")
_LOCK = threading.Lock()

#: A loop over more rows than this is almost certainly the wrong file.
MAX_ROWS = 5000


class DatasetError(ValueError):
    """A data set that cannot be read, or a name/range that does not exist."""


# ── names ─────────────────────────────────────────────────────────────────────
def clean_name(raw: str) -> str:
    """'Login Data (Mumbai).xlsx' → 'login_data_mumbai'."""
    base = os.path.splitext(os.path.basename(str(raw or "")))[0]
    name = re.sub(r"[^0-9a-zA-Z]+", "_", base).strip("_").lower()
    if name and name[0].isdigit():
        name = "d_" + name
    return name


def column_name(raw: str) -> str:
    """'Mobile Number' → 'mobile_number' (the ${…} name used in steps)."""
    name = re.sub(r"[^0-9a-zA-Z]+", "_", str(raw or "")).strip("_").lower()
    if name and name[0].isdigit():
        name = "c_" + name
    return name


def _path(name: str) -> str:
    return os.path.join(DIR, f"{name}.csv")


def _meta_path(name: str) -> str:
    return os.path.join(DIR, f"{name}.meta.json")


# ── reading uploads ───────────────────────────────────────────────────────────
def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, _dt.datetime):
        return v.date().isoformat() if v.time() == _dt.time() else v.isoformat(sep=" ")
    if isinstance(v, _dt.date):
        return v.isoformat()
    return str(v).strip()


def _tidy(rows: list[list]) -> tuple[list[str], list[list[str]]]:
    """Headings + body, with blank trailing rows/columns dropped."""
    rows = [[_cell(c) for c in r] for r in rows]
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        raise DatasetError("The sheet is empty — the first row must hold the column headings.")
    headings = rows[0]
    while headings and not headings[-1]:
        headings.pop()
    if not headings or any(not h for h in headings):
        raise DatasetError("Every column needs a heading in the first row "
                           "(an empty heading was found).")
    names = [column_name(h) for h in headings]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise DatasetError(f"Two columns have the same name: {', '.join(dupes)}. "
                           "Rename one of them in the sheet.")
    width = len(headings)
    body = [(r + [""] * width)[:width] for r in rows[1:]]
    if len(body) > MAX_ROWS:
        raise DatasetError(f"{len(body)} rows — the limit is {MAX_ROWS}.")
    return headings, body


def parse_upload(filename: str, content: bytes) -> dict[str, tuple[list[str], list[list[str]]]]:
    """{dataset name: (headings, rows)} from an uploaded .csv / .xlsx file."""
    ext = os.path.splitext(filename.lower())[1]
    stem = clean_name(filename)
    if not stem:
        raise DatasetError("The file name has no letters or digits to name the data set by.")
    if ext == ".csv":
        text = content.decode("utf-8-sig", errors="replace")
        return {stem: _tidy(list(csv.reader(io.StringIO(text))))}
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheets = [ws for ws in wb.worksheets if ws.sheet_state == "visible"]
        out = {}
        for ws in sheets:
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            if not any(any(c not in (None, "") for c in r) for r in rows):
                continue
            name = stem if len(sheets) == 1 else f"{stem}_{clean_name(ws.title)}"
            out[name] = _tidy(rows)
        if not out:
            raise DatasetError("Every sheet in the workbook is empty.")
        return out
    raise DatasetError("Upload a .xlsx or .csv file (an old .xls can be saved as .xlsx from Excel).")


# ── store ─────────────────────────────────────────────────────────────────────
def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def save(name: str, headings: list[str], rows: list[list[str]], user: str = "") -> dict:
    name = clean_name(name)
    if not name:
        raise DatasetError("A data set needs a name.")
    os.makedirs(DIR, exist_ok=True)
    with _LOCK:
        meta = _read_meta(name)
        with open(_path(name), "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(headings)
            w.writerows(rows)
        meta.setdefault("created_by", user or "")
        meta.setdefault("created_at", _now())
        meta["updated_by"] = user or ""
        meta["updated_at"] = _now()
        with open(_meta_path(name), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    return info(name)


def _read_meta(name: str) -> dict:
    try:
        with open(_meta_path(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def exists(name: str) -> bool:
    return os.path.isfile(_path(clean_name(name)))


def names() -> list[str]:
    if not os.path.isdir(DIR):
        return []
    return sorted(f[:-4] for f in os.listdir(DIR) if f.endswith(".csv"))


def table(name: str) -> tuple[list[str], list[list[str]]]:
    key = clean_name(name)
    try:
        with open(_path(key), encoding="utf-8", newline="") as f:
            data = list(csv.reader(f))
    except FileNotFoundError:
        known = ", ".join(names()) or "none uploaded yet"
        raise DatasetError(f"No data set called '{name}'. Uploaded: {known}.") from None
    if not data:
        return [], []
    return data[0], data[1:]


def rows(name: str) -> list[dict]:
    """Every row as {column step-name: value}, plus row_number (1 = first data row)."""
    headings, body = table(name)
    keys = [column_name(h) for h in headings]
    return [dict(zip(keys, r), row_number=str(i)) for i, r in enumerate(body, 1)]


def info(name: str) -> dict:
    headings, body = table(name)
    meta = _read_meta(clean_name(name))
    return {"name": clean_name(name), "headings": headings,
            "columns": [column_name(h) for h in headings], "row_count": len(body),
            **{k: meta.get(k, "") for k in ("created_by", "created_at",
                                             "updated_by", "updated_at")}}


def all_info() -> list[dict]:
    out = []
    for n in names():
        try:
            out.append(info(n))
        except (OSError, DatasetError):
            continue
    return out


def delete(name: str) -> None:
    key = clean_name(name)
    with _LOCK:
        for p in (_path(key), _meta_path(key)):
            if os.path.exists(p):
                os.remove(p)


def to_csv_bytes(name: str) -> bytes:
    with open(_path(clean_name(name)), "rb") as f:
        return f.read()
