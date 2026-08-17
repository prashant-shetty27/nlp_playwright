"""
ai_flow_builder/scenario.py

Scenario configuration — which testcase rows become a flow, and what else goes in.

Replaces the hardcoded SCENARIO_ROWS / SCENARIO_EXCLUDED / inject_after constants
that previously lived in cli.py, so a new testcase needs a JSON file rather than a
code edit.

A scenario is validated on load: injected statements must parse with the real
parser and be dispatchable by the target runner, and every excluded row must carry
a reason. An invalid scenario fails at load time, not mid-generation.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIOS_DIR = os.path.join(BASE_DIR, "scenarios")


class ScenarioError(ValueError):
    """Raised when a scenario file is malformed or references something unusable."""


@dataclass
class InjectedStep:
    statement: str
    why: str = ""
    source: str = "requirement"
    note: str = ""


@dataclass
class CaptureElement:
    locator: str                 # framework locator name to save as
    element: str                 # semantic source name, e.g. "MobileNumberInput"
    text_hint: str = ""          # exact visible text, when the name alone is ambiguous
    scope: str = ""              # CSS scope to search within


@dataclass
class CaptureStage:
    name: str
    elements: list[CaptureElement] = field(default_factory=list)
    reach: list[str] = field(default_factory=list)     # .flow statements to get here
    state_changing: bool = False
    side_effect: str = ""


@dataclass
class CapturePlan:
    locator_group: str
    start_url: str = ""
    device: str = ""
    http_auth_domain: str = ""
    stages: list[CaptureStage] = field(default_factory=list)

    @property
    def state_changing_stages(self) -> list[CaptureStage]:
        return [s for s in self.stages if s.state_changing]


@dataclass
class Scenario:
    name: str
    testcases: list[str]
    output: str
    platform: str = "web"
    description: str = ""
    source_xlsx: str = ""
    include_rows: set[int] | None = None          # None = every row of the testcases
    exclude_rows: dict[int, str] = field(default_factory=dict)
    locator_overrides: dict[int, str] = field(default_factory=dict)
    variable_bindings: dict[str, str] = field(default_factory=dict)
    placeholders: dict[str, str] = field(default_factory=dict)
    inject_before: list[InjectedStep] = field(default_factory=list)
    inject_after_row: dict[int, list[InjectedStep]] = field(default_factory=dict)
    inject_end: list[InjectedStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    capture: CapturePlan | None = None
    path: str = ""

    @property
    def resolved_rows(self) -> set[int] | None:
        """Rows to emit: the include list minus anything explicitly excluded."""
        if self.include_rows is None:
            return None
        return {r for r in self.include_rows if r not in self.exclude_rows}


def _as_int_keys(d: dict, field_name: str) -> dict:
    out = {}
    for k, v in (d or {}).items():
        try:
            out[int(k)] = v
        except (TypeError, ValueError):
            raise ScenarioError(f"{field_name}: key {k!r} is not a row number") from None
    return out


def _steps(raw: list, where: str) -> list[InjectedStep]:
    out = []
    for i, item in enumerate(raw or []):
        if isinstance(item, str):
            out.append(InjectedStep(statement=item))
            continue
        if not isinstance(item, dict) or "statement" not in item:
            raise ScenarioError(f"{where}[{i}]: expected a string or an object with 'statement'")
        out.append(InjectedStep(
            statement=item["statement"],
            why=item.get("why", ""),
            source=item.get("source", "requirement"),
            note=item.get("note", ""),
        ))
    return out


def load(path: str) -> Scenario:
    """Load and validate a scenario file."""
    full = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
    if not os.path.exists(full) and not path.endswith(".json"):
        alt = os.path.join(SCENARIOS_DIR, f"{path}.json")
        if os.path.exists(alt):
            full = alt
    if not os.path.exists(full):
        raise ScenarioError(f"scenario not found: {path}")

    with open(full, encoding="utf-8") as f:
        raw = json.load(f)

    for required in ("name", "testcases", "output"):
        if not raw.get(required):
            raise ScenarioError(f"{path}: missing required field {required!r}")
    if not isinstance(raw["testcases"], list) or not raw["testcases"]:
        raise ScenarioError(f"{path}: 'testcases' must be a non-empty list of testcase IDs")

    steps_cfg = raw.get("steps", {}) or {}
    include = steps_cfg.get("include_rows")
    exclude = _as_int_keys(steps_cfg.get("exclude_rows", {}), "steps.exclude_rows")
    for row, reason in exclude.items():
        if not str(reason).strip():
            raise ScenarioError(
                f"{path}: steps.exclude_rows[{row}] has no reason. Every exclusion must say "
                f"why, so a reader can tell a deliberate omission from an oversight."
            )

    inject = raw.get("inject", {}) or {}
    sc = Scenario(
        name=raw["name"],
        testcases=list(raw["testcases"]),
        output=raw["output"],
        platform=raw.get("platform", "web"),
        description=raw.get("description", ""),
        source_xlsx=(raw.get("source", {}) or {}).get("xlsx", ""),
        include_rows={int(r) for r in include} if include else None,
        exclude_rows=exclude,
        locator_overrides={k: str(v) for k, v in
                           _as_int_keys(raw.get("locator_overrides", {}),
                                        "locator_overrides").items()},
        variable_bindings=dict(raw.get("variable_bindings", {})),
        placeholders=dict(raw.get("placeholders", {})),
        inject_before=_steps(inject.get("before"), "inject.before"),
        inject_after_row={k: _steps(v, f"inject.after_row[{k}]") for k, v in
                          _as_int_keys(inject.get("after_row", {}), "inject.after_row").items()},
        inject_end=_steps(inject.get("end"), "inject.end"),
        notes=list(raw.get("notes", [])),
        capture=_capture_plan(raw.get("capture"), path),
        path=full,
    )
    _validate_statements(sc)
    return sc


def _capture_plan(raw: dict | None, path: str) -> CapturePlan | None:
    if not raw:
        return None
    if not raw.get("locator_group"):
        raise ScenarioError(f"{path}: capture.locator_group is required — "
                            f"captured locators must be filed under a named group")
    stages = []
    for i, st in enumerate(raw.get("stages", [])):
        if not st.get("name"):
            raise ScenarioError(f"{path}: capture.stages[{i}] has no name")
        elements = []
        for loc, spec in (st.get("elements", {}) or {}).items():
            if isinstance(spec, str):
                elements.append(CaptureElement(locator=loc, element=spec))
            elif isinstance(spec, dict):
                if not spec.get("element"):
                    raise ScenarioError(
                        f"{path}: capture.stages[{i}].elements[{loc}] needs an 'element' name")
                elements.append(CaptureElement(locator=loc, element=spec["element"],
                                               text_hint=spec.get("text_hint", ""),
                                               scope=spec.get("scope", "")))
            else:
                raise ScenarioError(
                    f"{path}: capture.stages[{i}].elements[{loc}] must be a name or an object")
        if not elements:
            raise ScenarioError(f"{path}: capture stage {st['name']!r} captures no elements")
        if st.get("state_changing") and not str(st.get("side_effect", "")).strip():
            raise ScenarioError(
                f"{path}: capture stage {st['name']!r} is state_changing but does not describe "
                f"its side_effect. Say what it does to the system so the operator can consent.")
        stages.append(CaptureStage(
            name=st["name"], elements=elements, reach=list(st.get("reach", [])),
            state_changing=bool(st.get("state_changing")),
            side_effect=st.get("side_effect", ""),
        ))
    return CapturePlan(
        locator_group=raw["locator_group"], start_url=raw.get("start_url", ""),
        device=raw.get("device", ""), http_auth_domain=raw.get("http_auth_domain", ""),
        stages=stages,
    )


def _validate_statements(sc: Scenario) -> None:
    """Injected statements must parse and be dispatchable on the target platform."""
    from ai_flow_builder.catalogue import load as load_catalogue
    from nlp.parser import parse_step

    cat = load_catalogue(sc.platform)
    problems = []
    groups = ([("inject.before", sc.inject_before), ("inject.end", sc.inject_end)]
              + [(f"inject.after_row[{r}]", v) for r, v in sc.inject_after_row.items()])
    if sc.capture:
        for stage in sc.capture.stages:
            groups.append((f"capture.stages[{stage.name}].reach",
                           [InjectedStep(statement=x) for x in stage.reach]))

    for where, steps in groups:
        for st in steps:
            probe = st.statement
            for var in _vars(probe):
                probe = probe.replace("${" + var + "}", "VAR")
            try:
                cmd = parse_step(probe)
            except ValueError as e:
                problems.append(f"{where}: {st.statement!r} does not parse ({e})")
                continue
            if not cat.supports(cmd.type):
                problems.append(
                    f"{where}: {st.statement!r} -> '{cmd.type}' is not dispatchable on "
                    f"'{sc.platform}' ({cat.evidence_for(cmd.type)})"
                )
    if problems:
        raise ScenarioError(f"{sc.path}:\n  - " + "\n  - ".join(problems))


def _vars(text: str) -> list[str]:
    import re

    return re.findall(r"\$\{([A-Za-z0-9_.\-]+)\}", text or "")


def to_step_mappings(steps: list[InjectedStep], platform: str) -> list:
    """Turn injected statements into StepMappings the pipeline can emit."""
    from ai_flow_builder.catalogue import load as load_catalogue
    from ai_flow_builder.mapper import NEEDS_LOCATOR, SUPPORTED, StepMapping
    from nlp.parser import parse_step

    cat = load_catalogue(platform)
    out = []
    for st in steps:
        probe = st.statement
        for var in _vars(probe):
            probe = probe.replace("${" + var + "}", "VAR")
        cmd = parse_step(probe)

        locator = ""
        if cmd.type not in ("verify_var_contains", "verify_var_not_equals",
                            "create_variable", "extract_json", "open", "screenshot",
                            "wait", "refresh"):
            locator = cmd.target or ""

        reused = cat.has_locator(locator) if locator else False
        out.append(StepMapping(
            source_ref=st.source,
            manual_step=st.why or st.statement,
            intent=st.why,
            command_type=cmd.type,
            statement=st.statement,
            status=SUPPORTED if (not locator or reused) else NEEDS_LOCATOR,
            locator=locator,
            locator_reused=reused,
            evidence=cat.evidence_for(cmd.type),
            note=st.note,
        ))
    return out


def list_scenarios() -> list[str]:
    if not os.path.isdir(SCENARIOS_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(SCENARIOS_DIR) if f.endswith(".json"))
