"""
ai_flow_builder/testcase.py

Testcase model plus two ingestion modes:

  • XLSX workbook — every sheet is scanned independently. Sheets are NOT assumed to
    share a header row or column naming; each sheet's header is located and its
    columns mapped by synonym. Anything that cannot be interpreted is reported
    rather than silently dropped.
  • Prompt — a single testcase written as free text, parsed into the same model
    by section headings. No LLM required for the structured form.

Ingestion never generates a flow. It produces an inventory the operator selects from.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict

# Column synonyms — lowercased, punctuation-stripped header cell → canonical field.
_COLUMN_SYNONYMS: dict[str, tuple[str, ...]] = {
    "testcase_id": ("testcaseid", "tcid", "id", "testid", "caseid", "tcno", "srno",
                    "slno", "sno", "testcasenumber", "tcnumber"),
    "title": ("title", "testcasetitle", "testcasename", "scenario", "summary",
              "name", "description", "testscenario", "usecase"),
    "preconditions": ("precondition", "preconditions", "prerequisite", "prerequisites",
                      "setup", "precondition(s)", "given"),
    "steps": ("steps", "teststeps", "step", "stepdescription", "actions", "action",
              "procedure", "teststepdescription", "stepstoexecute", "when"),
    "test_data": ("testdata", "data", "inputdata", "input", "parameters", "testinput"),
    "expected": ("expected", "expectedresult", "expectedresults", "expectedoutcome",
                 "expectedbehaviour", "expectedbehavior", "result", "then",
                 "acceptancecriteria"),
    "priority": ("priority", "severity", "importance", "p", "prio"),
    "classification": ("type", "testtype", "positivenegative", "positive/negative",
                       "category", "classification", "scenariotype"),
}

_POSITIVE = re.compile(r"\b(positive|happy|valid|smoke|sanity)\b", re.I)
_NEGATIVE = re.compile(r"\b(negative|invalid|error|failure|edge)\b", re.I)


def _norm(cell) -> str:
    """Normalise a header cell for synonym matching."""
    return re.sub(r"[^a-z0-9]", "", str(cell or "").strip().lower())


@dataclass
class Testcase:
    source: str = ""            # "xlsx:<file>" or "prompt"
    sheet: str = ""
    row: int | None = None
    testcase_id: str = ""
    title: str = ""
    preconditions: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    test_data: str = ""
    expected: str = ""
    priority: str = ""
    classification: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def ref(self) -> str:
        if self.sheet and self.testcase_id:
            return f"{self.sheet}::{self.testcase_id}"
        return self.testcase_id or self.title[:40] or "<untitled>"

    @property
    def is_interpretable(self) -> bool:
        """A testcase is usable only if it has a title and at least one step."""
        return bool(self.title.strip()) and len(self.steps) > 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Rejection:
    sheet: str
    row: int | None
    reason: str
    detail: str = ""


@dataclass
class Inventory:
    testcases: list[Testcase] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    sheets_read: list[str] = field(default_factory=list)
    sheets_skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def interpreted(self) -> list[Testcase]:
        return [t for t in self.testcases if t.is_interpretable]

    def find(self, needle: str) -> list[Testcase]:
        n = needle.lower()
        return [t for t in self.testcases
                if n in t.title.lower() or n in t.testcase_id.lower()]


# ─────────────────────────────────────────────────────────────────────────────
# Step splitting
# ─────────────────────────────────────────────────────────────────────────────

_STEP_SPLIT = re.compile(r"(?:\r?\n)+|(?<=[.;])\s+(?=\d+[\.\)]\s)")
_STEP_PREFIX = re.compile(r"^\s*(?:\d+[\.\)]|[-*•])\s*")


def split_steps(blob) -> list[str]:
    """Split a steps cell into individual manual steps, tolerating many layouts."""
    if blob is None:
        return []
    text = str(blob).strip()
    if not text:
        return []

    parts = [p.strip() for p in _STEP_SPLIT.split(text) if p and p.strip()]
    # A single line holding "1. a 2. b 3. c" still needs splitting.
    if len(parts) == 1 and re.search(r"\d+[\.\)]\s", parts[0]):
        parts = [p.strip() for p in re.split(r"(?=\b\d+[\.\)]\s)", parts[0]) if p.strip()]

    return [_STEP_PREFIX.sub("", p).strip() for p in parts if _STEP_PREFIX.sub("", p).strip()]


def _classify(text: str, explicit: str = "") -> str:
    probe = f"{explicit} {text}"
    if _NEGATIVE.search(probe):
        return "negative"
    if _POSITIVE.search(probe):
        return "positive"
    return explicit.strip().lower() or "unspecified"


# ─────────────────────────────────────────────────────────────────────────────
# XLSX ingestion — every sheet, independently
# ─────────────────────────────────────────────────────────────────────────────

def _map_header(cells) -> dict[str, int]:
    """Map canonical field → column index for one candidate header row."""
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(cells):
        key = _norm(cell)
        if not key:
            continue
        for field_name, synonyms in _COLUMN_SYNONYMS.items():
            if field_name in mapping:
                continue
            if key in synonyms or any(key.startswith(s) for s in synonyms):
                mapping[field_name] = idx
                break
    return mapping


def _find_header(rows, scan_depth: int = 12) -> tuple[int, dict[str, int]]:
    """
    Locate the header row. Sheets often carry a title banner or blank rows first,
    so the header is the best-scoring row within the first `scan_depth` rows —
    not simply row 1.
    """
    best_idx, best_map, best_score = -1, {}, 0
    for i, row in enumerate(rows[:scan_depth]):
        mapping = _map_header(row)
        # Require the two fields without which a row cannot become a testcase.
        score = len(mapping) + (2 if "steps" in mapping else 0) + (1 if "title" in mapping else 0)
        if "steps" in mapping and "title" in mapping and score > best_score:
            best_idx, best_map, best_score = i, mapping, score
    return best_idx, best_map


def ingest_xlsx(path: str) -> Inventory:
    """Read every sheet of a workbook and return the full testcase inventory."""
    import openpyxl

    inv = Inventory()
    if not os.path.exists(path):
        inv.sheets_skipped.append(("<file>", f"not found: {path}"))
        return inv

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)

    for sheet in wb.worksheets:
        name = sheet.title
        rows = [list(r) for r in sheet.iter_rows(values_only=True)]
        if not rows:
            inv.sheets_skipped.append((name, "sheet is empty"))
            continue

        hdr_idx, colmap = _find_header(rows)
        if hdr_idx < 0:
            present = sorted({_norm(c) for c in rows[0] if _norm(c)}) if rows else []
            inv.sheets_skipped.append(
                (name, "no header row with both a title-like and steps-like column "
                       f"(first-row headers seen: {', '.join(present[:8]) or 'none'})")
            )
            continue

        inv.sheets_read.append(name)

        def cell(row, key):
            i = colmap.get(key)
            if i is None or i >= len(row):
                return ""
            return "" if row[i] is None else str(row[i]).strip()

        for r_off, row in enumerate(rows[hdr_idx + 1:], start=hdr_idx + 2):
            if not any(str(c).strip() for c in row if c is not None):
                continue  # blank spacer row — not a rejection

            steps = split_steps(cell(row, "steps"))
            title = cell(row, "title")

            if not title and not steps:
                inv.rejections.append(Rejection(name, r_off, "no title and no steps"))
                continue

            tc = Testcase(
                source=f"xlsx:{os.path.basename(path)}",
                sheet=name,
                row=r_off,
                testcase_id=cell(row, "testcase_id"),
                title=title,
                preconditions=split_steps(cell(row, "preconditions")),
                steps=steps,
                test_data=cell(row, "test_data"),
                expected=cell(row, "expected"),
                priority=cell(row, "priority"),
                classification=_classify(f"{title} {cell(row,'expected')}",
                                         cell(row, "classification")),
                raw={k: cell(row, k) for k in colmap},
            )
            if not tc.is_interpretable:
                why = "missing title" if not title else "no parseable steps"
                inv.rejections.append(Rejection(name, r_off, why, title[:60]))
            inv.testcases.append(tc)

    wb.close()
    return inv


# ─────────────────────────────────────────────────────────────────────────────
# Prompt ingestion — one testcase written as free text
# ─────────────────────────────────────────────────────────────────────────────

_SECTION = re.compile(
    r"^\s*(title|objective|precondition[s]?|logical\s+testcase\s+steps|steps|"
    r"test\s*data|expected[^:\n]*|priority|classification)\s*:?\s*$",
    re.I | re.M,
)


def ingest_prompt(text: str, testcase_id: str = "PROMPT-001") -> Inventory:
    """Parse a testcase written as a prompt into the same Testcase model."""
    inv = Inventory(sheets_read=["<prompt>"])
    sections: dict[str, list[str]] = {}
    current = "_preamble"

    for line in text.splitlines():
        m = _SECTION.match(line)
        if m:
            current = _norm(m.group(1))
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)

    def sect(*keys) -> str:
        for k in keys:
            for have, lines in sections.items():
                if have.startswith(k):
                    return "\n".join(lines).strip()
        return ""

    title = sect("title").strip().splitlines()[0] if sect("title") else ""
    steps = split_steps(sect("logicaltestcasesteps", "steps"))

    tc = Testcase(
        source="prompt",
        sheet="<prompt>",
        testcase_id=testcase_id,
        title=title,
        preconditions=split_steps(sect("precondition")),
        steps=steps,
        test_data=sect("testdata"),
        expected=sect("expected"),
        priority=sect("priority"),
        classification=_classify(f"{title} {sect('objective')}", sect("classification")),
        raw={"objective": sect("objective")},
    )
    if not tc.is_interpretable:
        inv.rejections.append(
            Rejection("<prompt>", None,
                      "missing title" if not title else "no parseable steps")
        )
    inv.testcases.append(tc)
    return inv


def render_inventory(inv: Inventory) -> str:
    """Human-readable testcase inventory."""
    out = [
        f"sheets read      : {len(inv.sheets_read)}  {inv.sheets_read}",
        f"sheets skipped   : {len(inv.sheets_skipped)}",
    ]
    for name, why in inv.sheets_skipped:
        out.append(f"    ✗ {name}: {why}")
    out.append(f"testcases found  : {len(inv.testcases)}")
    out.append(f"interpretable    : {len(inv.interpreted)}")
    out.append("")
    hdr = f"{'#':>3}  {'REF':<28} {'PRI':<6} {'CLASS':<11} {'STEPS':>5}  TITLE"
    out.append(hdr)
    out.append("-" * len(hdr))
    for i, tc in enumerate(inv.testcases, 1):
        flag = " " if tc.is_interpretable else "✗"
        out.append(f"{i:>3}{flag} {tc.ref:<28} {tc.priority[:6]:<6} "
                   f"{tc.classification[:11]:<11} {len(tc.steps):>5}  {tc.title[:60]}")
    if inv.rejections:
        out.append("")
        out.append(f"uninterpretable rows: {len(inv.rejections)}")
        for r in inv.rejections:
            loc = f"{r.sheet} row {r.row}" if r.row else r.sheet
            out.append(f"    ✗ {loc}: {r.reason} {r.detail}")
    return "\n".join(out)
