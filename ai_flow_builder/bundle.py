"""
ai_flow_builder/bundle.py

Shared parsing for a four-tab testcase workbook, whatever produced it.

Input is always raw tab data ({tab: rows}) from a SourceAdapter, so XLSX, Google
Sheets and any future source converge here. Nothing below knows or cares which
source it came from.

Tab roles (each used for its own purpose, never interchangeably):
  Summary               → requirement / scope context. NEVER testcase rows.
  Test Cases            → the testcase rows. One testcase spans MANY rows:
                          a blank Test Case ID with a populated Step No means
                          "continuation of the previous testcase".
  Test Data & Variables → variable dictionary, joined to the steps that use it.
  Traceability Matrix   → Jira ↔ testcase coverage. NEVER the primary source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ai_flow_builder.testcase import Rejection, Testcase, _norm

# Testsigma variable syntax:  $|NAME|  →  framework syntax:  ${NAME}
TESTSIGMA_VAR = re.compile(r"\$\|([A-Za-z0-9_.\-]+)\|")
#: ${...} as the RUNTIME resolves it — see nlp/variables.REFERENCE_RE.
#: A narrower pattern here meant a step could reference something the
#: pipeline never counted as a variable at all.
from nlp.variables import REFERENCE_RE as FRAMEWORK_VAR  # noqa: E402


def convert_variables(text: str) -> tuple[str, list[tuple[str, str]]]:
    """
    Rewrite Testsigma `$|NAME|` into framework `${NAME}`.

    Returns (converted_text, [(before, after), ...]). Values are never substituted —
    only the syntax changes, so an unresolved variable stays visibly unresolved.
    """
    if not text:
        return "", []
    conversions: list[tuple[str, str]] = []

    def _sub(m: re.Match) -> str:
        before, after = m.group(0), "${" + m.group(1) + "}"
        conversions.append((before, after))
        return after

    return TESTSIGMA_VAR.sub(_sub, text), conversions


def referenced_variables(text: str) -> set[str]:
    """Every variable named in a string, in either syntax."""
    return set(TESTSIGMA_VAR.findall(text or "")) | set(FRAMEWORK_VAR.findall(text or ""))


@dataclass
class Variable:
    name: str
    description: str = ""
    sample: str = ""
    owner: str = ""

    #: Prose that marks a "where to source" instruction rather than a usable value.
    #: Matched anywhere in the cell, not just at the start — the column is literally
    #: titled "Sample / Where to source", so both kinds of content share it.
    _SOURCE_PROSE = re.compile(
        r"\b(fetch(ed|ing)?|obtain(ed)?|retriev(e|ed)|generat(e|ed)|provided\s+by|"
        r"ask\b|source[d]?\s+from|from\s+the\b|see\b|refer\b|tbd|to\s+be\s+(decided|provided)|"
        r"n/?a|any\s+valid|per\s+environment|environment[- ]specific)\b",
        re.I,
    )

    @property
    def is_resolved(self) -> bool:
        """
        Resolved only when `sample` holds a usable literal value — never when it
        describes where to get one. Values are never invented from prose, so an
        instruction-shaped sample keeps the variable in the unresolved list where
        the operator must supply it.
        """
        s = (self.sample or "").strip()
        if not s or s in {"-", "—"}:
            return False
        return not self._SOURCE_PROSE.search(s)


@dataclass
class Bundle:
    """Everything parsed out of one workbook, source-agnostic."""

    source: dict = field(default_factory=dict)
    summary_text: str = ""
    testcases: list[Testcase] = field(default_factory=list)
    variables: dict[str, Variable] = field(default_factory=dict)
    traceability: list[dict] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    tabs_read: list[str] = field(default_factory=list)
    tabs_skipped: list[tuple[str, str]] = field(default_factory=list)
    conversions: list[tuple[str, str, str]] = field(default_factory=list)  # (ref, before, after)

    # ── Derived views for the pre-generation display ─────────────────────────
    @property
    def interpreted(self) -> list[Testcase]:
        return [t for t in self.testcases if t.is_interpretable]

    @property
    def duplicate_ids(self) -> dict[str, int]:
        seen: dict[str, int] = {}
        for t in self.testcases:
            if t.testcase_id:
                seen[t.testcase_id] = seen.get(t.testcase_id, 0) + 1
        return {k: v for k, v in seen.items() if v > 1}

    def by_module(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.testcases:
            out[t.raw.get("module", "") or "<none>"] = out.get(t.raw.get("module", "") or "<none>", 0) + 1
        return dict(sorted(out.items()))

    def by_classification(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.testcases:
            out[t.classification] = out.get(t.classification, 0) + 1
        return dict(sorted(out.items()))

    def by_automatable(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.testcases:
            key = (t.raw.get("automatable", "") or "<blank>").strip() or "<blank>"
            out[key] = out.get(key, 0) + 1
        return dict(sorted(out.items()))

    def unresolved_variables(self) -> list[str]:
        """Variables referenced by a testcase that have no usable value."""
        used: set[str] = set()
        for t in self.testcases:
            for s in t.steps:
                used |= referenced_variables(s)
            used |= referenced_variables(t.test_data)
        out = []
        for name in sorted(used):
            v = self.variables.get(name)
            if v is None or not v.is_resolved:
                out.append(name)
        return out

    def find(self, *needles: str) -> list[Testcase]:
        """Case-insensitive search over id, name and module."""
        hits = []
        for t in self.testcases:
            blob = f"{t.testcase_id} {t.title} {t.raw.get('module','')}".lower()
            if all(n.lower() in blob for n in needles):
                hits.append(t)
        return hits


# ─────────────────────────────────────────────────────────────────────────────
# Tab identification — tolerant of naming, strict about role
# ─────────────────────────────────────────────────────────────────────────────

_TAB_ROLES = {
    "testcases": ("testcases", "testcase", "cases", "tests"),
    "variables": ("testdatavariables", "testdata", "variables", "datavariables"),
    "summary": ("summary", "overview", "scope"),
    "traceability": ("traceabilitymatrix", "traceability", "rtm", "coverage"),
}


def _role_of(tab_name: str) -> str:
    key = _norm(tab_name)
    for role, names in _TAB_ROLES.items():
        if key in names or any(key.startswith(n) for n in names):
            return role
    return "unknown"


def _header_index(rows: list[list], wanted: tuple[str, ...], scan: int = 8) -> tuple[int, dict[str, int]]:
    """Find the header row and map canonical name → column index."""
    for i, row in enumerate(rows[:scan]):
        mapping = {}
        for idx, cell in enumerate(row):
            k = _norm(cell)
            if not k:
                continue
            for want in wanted:
                if want not in mapping and (k == want or k.startswith(want)):
                    mapping[want] = idx
                    break
        if len(mapping) >= max(2, len(wanted) // 3):
            return i, mapping
    return -1, {}


_TC_COLUMNS = (
    "testcaseid", "modulesuite", "testcasename", "priority", "testtype",
    "prerequisite", "stepno", "teststeptestsigmanlp", "testdata",
    "expectedresult", "jirareference", "automatable", "status",
    "actualresult", "remarks",
)

_POSITIVE = re.compile(r"\b(positive|happy|valid|smoke|sanity)\b", re.I)
_NEGATIVE = re.compile(r"\b(negative|invalid|boundary|error|failure|edge|not\s+displayed)\b", re.I)


def _classify(test_type: str, title: str) -> str:
    probe = f"{test_type} {title}"
    if _NEGATIVE.search(probe):
        return "negative"
    if _POSITIVE.search(probe):
        return "positive"
    return (test_type or "").strip().lower() or "unspecified"


def _cell(row: list, mapping: dict, key: str) -> str:
    i = mapping.get(key)
    if i is None or i >= len(row) or row[i] is None:
        return ""
    return str(row[i]).strip()


def parse_bundle(tabs: dict[str, list[list]], source_desc: dict | None = None) -> Bundle:
    """Parse raw tab data into a Bundle. Source-agnostic by construction."""
    b = Bundle(source=source_desc or {})

    for tab_name, rows in tabs.items():
        role = _role_of(tab_name)
        non_empty = [r for r in rows if any(str(c).strip() for c in r if c is not None)]
        if not non_empty:
            b.tabs_skipped.append((tab_name, "tab is empty"))
            continue

        if role == "testcases":
            _parse_testcases(b, tab_name, rows)
        elif role == "variables":
            _parse_variables(b, tab_name, rows)
        elif role == "summary":
            b.summary_text = "\n".join(
                " | ".join(str(c).strip() for c in r if c is not None and str(c).strip())
                for r in non_empty[:40]
            )
            b.tabs_read.append(tab_name)
        elif role == "traceability":
            _parse_traceability(b, tab_name, rows)
        else:
            b.tabs_skipped.append((tab_name, "tab role not recognised — not used"))

    return b


def _parse_testcases(b: Bundle, tab: str, rows: list[list]) -> None:
    hdr_i, cols = _header_index(rows, _TC_COLUMNS)
    if hdr_i < 0 or "teststeptestsigmanlp" not in cols:
        b.tabs_skipped.append((tab, "no recognisable Test Cases header row"))
        return
    b.tabs_read.append(tab)

    current: Testcase | None = None

    for r_no, row in enumerate(rows[hdr_i + 1:], start=hdr_i + 2):
        if not any(str(c).strip() for c in row if c is not None):
            continue

        tc_id = _cell(row, cols, "testcaseid")
        step_no = _cell(row, cols, "stepno")
        step_txt_raw = _cell(row, cols, "teststeptestsigmanlp")
        name = _cell(row, cols, "testcasename")

        # ── New testcase, or continuation of the previous one? ───────────────
        if tc_id:
            current = Testcase(
                source=f"{b.source.get('kind', 'workbook')}:{b.source.get('title', tab)}",
                sheet=tab,
                row=r_no,
                testcase_id=tc_id,
                title=name,
                preconditions=[p for p in (_cell(row, cols, "prerequisite"),) if p],
                steps=[],
                test_data="",
                expected="",
                priority=_cell(row, cols, "priority"),
                classification=_classify(_cell(row, cols, "testtype"), name),
                raw={
                    "module": _cell(row, cols, "modulesuite"),
                    "test_type": _cell(row, cols, "testtype"),
                    "jira": _cell(row, cols, "jirareference"),
                    "automatable": _cell(row, cols, "automatable"),
                    "status": _cell(row, cols, "status"),
                    "source_rows": [r_no],
                    "step_rows": [],
                },
            )
            b.testcases.append(current)
        elif current is None:
            b.rejections.append(
                Rejection(tab, r_no, "step row before any Test Case ID", step_txt_raw[:60])
            )
            continue
        else:
            current.raw["source_rows"].append(r_no)

        if not step_txt_raw:
            if tc_id and not name:
                b.rejections.append(Rejection(tab, r_no, "Test Case ID with no name and no step", tc_id))
            continue

        step_txt, conv = convert_variables(step_txt_raw)
        for before, after in conv:
            b.conversions.append((current.testcase_id, before, after))

        data_txt, dconv = convert_variables(_cell(row, cols, "testdata"))
        for before, after in dconv:
            b.conversions.append((current.testcase_id, before, after))

        current.steps.append(step_txt)
        current.raw["step_rows"].append({
            "row": r_no,
            "step_no": step_no,
            "step": step_txt,
            "step_raw": step_txt_raw,
            "test_data": data_txt,
            "expected": _cell(row, cols, "expectedresult"),
        })
        if data_txt:
            current.test_data = (current.test_data + "\n" + data_txt).strip()
        exp = _cell(row, cols, "expectedresult")
        if exp:
            current.expected = (current.expected + "\n" + exp).strip()

    for tc in b.testcases:
        if tc.sheet == tab and not tc.is_interpretable:
            why = "missing name" if not tc.title else "no steps"
            b.rejections.append(Rejection(tab, tc.row, why, tc.testcase_id))


def _parse_variables(b: Bundle, tab: str, rows: list[list]) -> None:
    hdr_i, cols = _header_index(rows, ("variable", "description", "samplewheretosource", "owner"))
    if hdr_i < 0 or "variable" not in cols:
        b.tabs_skipped.append((tab, "no recognisable Variable header row"))
        return
    b.tabs_read.append(tab)

    for r_no, row in enumerate(rows[hdr_i + 1:], start=hdr_i + 2):
        name = _cell(row, cols, "variable")
        if not name:
            continue
        clean = name.strip()
        # Accept the variable named either bare or in Testsigma syntax.
        m = TESTSIGMA_VAR.fullmatch(clean) or FRAMEWORK_VAR.fullmatch(clean)
        if m:
            clean = m.group(1)
        b.variables[clean] = Variable(
            name=clean,
            description=_cell(row, cols, "description"),
            sample=_cell(row, cols, "samplewheretosource"),
            owner=_cell(row, cols, "owner"),
        )


def _parse_traceability(b: Bundle, tab: str, rows: list[list]) -> None:
    hdr_i, cols = _header_index(
        rows, ("jiraref", "type", "requirementdefectsummary", "coveredbytestcaseids", "coverage")
    )
    if hdr_i < 0:
        b.tabs_skipped.append((tab, "no recognisable Traceability header row"))
        return
    b.tabs_read.append(tab)

    for row in rows[hdr_i + 1:]:
        jira = _cell(row, cols, "jiraref")
        if not jira:
            continue
        b.traceability.append({
            "jira": jira,
            "type": _cell(row, cols, "type"),
            "summary": _cell(row, cols, "requirementdefectsummary"),
            "covered_by": _cell(row, cols, "coveredbytestcaseids"),
            "coverage": _cell(row, cols, "coverage"),
        })
