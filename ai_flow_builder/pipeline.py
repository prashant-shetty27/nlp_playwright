"""
ai_flow_builder/pipeline.py

Orchestrates: source → bundle → select → map → emit → validate.

Emits live progress events while it works, and enforces max_flows_per_batch.
The pipeline never executes a flow — generation and execution are separate gates.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable

from ai_flow_builder import catalogue as catalogue_mod
from ai_flow_builder import emitter
from ai_flow_builder.bundle import Bundle, parse_bundle, referenced_variables
from ai_flow_builder.mapper import (NEEDS_LOCATOR, SUBSTITUTE, SUPPORTED,
                                    Mapper, StepMapping, summarise)

BASE_DIR = catalogue_mod.BASE_DIR
Progress = Callable[[str], None]


def _stdout(msg: str) -> None:
    print(msg, flush=True)


@dataclass
class GenerationResult:
    flow_name: str = ""
    flow_path: str = ""
    flow_text: str = ""
    source_ids: list[str] = field(default_factory=list)
    mappings: list[StepMapping] = field(default_factory=list)
    required_locators: dict[str, str] = field(default_factory=dict)
    reused_locators: set[str] = field(default_factory=set)
    parser_ok: bool = False
    parser_errors: list[str] = field(default_factory=list)
    lint_exit: int | None = None
    lint_output: str = ""
    cli_command: str = ""
    annotated_text: str = ""
    map_path: str = ""

    @property
    def missing_locators(self) -> list[str]:
        return sorted(set(self.required_locators) - self.reused_locators)


def load_bundle(source, progress: Progress = _stdout) -> Bundle:
    desc = source.describe()
    progress(f"[source] {desc.get('kind')} :: {desc.get('title')}  ({desc.get('access')})")
    tabs = source.read_tabs()
    progress(f"[source] tabs returned: {list(tabs)}")
    b = parse_bundle(tabs, desc)
    progress(f"[ingest] {len(b.testcases)} testcases | {len(b.variables)} variables "
             f"| {len(b.conversions)} variable conversions | {len(b.rejections)} rejected rows")
    return b


def generate(
    *,
    bundle: Bundle,
    testcase_ids: list[str],
    flow_name: str,
    out_path: str,
    platform: str = "web",
    variable_bindings: dict[str, str] | None = None,
    placeholders: dict[str, str] | None = None,
    pre_steps: list[StepMapping] | None = None,
    extra_steps: list[StepMapping] | None = None,
    include_rows: set[int] | None = None,
    excluded_rows: dict[int, str] | None = None,
    locator_overrides: dict[int, str] | None = None,
    inject_after: dict[int, list] | None = None,
    calibration_notes: list[str] | None = None,
    max_flows_per_batch: int = 1,
    overwrite: bool = False,
    cli_command: str = "",
    progress: Progress = _stdout,
) -> GenerationResult:
    """Generate exactly one flow spanning the given source testcases."""
    if max_flows_per_batch < 1:
        raise ValueError("max_flows_per_batch must be >= 1")

    res = GenerationResult(flow_name=flow_name, source_ids=list(testcase_ids),
                           cli_command=cli_command)

    cat = catalogue_mod.load(platform)
    progress(f"[catalogue] {catalogue_mod.summary(cat)}")
    progress(f"[catalogue] source: {cat.source['commands']}")

    by_id = {t.testcase_id: t for t in bundle.testcases}
    selected = []
    for tid in testcase_ids:
        if tid not in by_id:
            raise KeyError(f"testcase {tid!r} not present in the source")
        selected.append(by_id[tid])

    progress(f"[select] 1 flow from {len(selected)} source testcases")

    mapper = Mapper(cat, variable_bindings or {})
    mappings: list[StepMapping] = []
    inject_after = inject_after or {}
    locator_overrides = locator_overrides or {}
    excluded_rows = excluded_rows or {}

    planned = []
    for tc in selected:
        for sr in tc.raw["step_rows"]:
            if include_rows is not None and sr["row"] not in include_rows:
                continue
            planned.append((tc, sr))
    total = len(planned) + len(pre_steps or []) + len(extra_steps or []) \
            + sum(len(v) for v in inject_after.values())

    progress("")
    progress("BUILDING FLOW 1 OF 1")
    n = 0

    def _emit(m: StepMapping, src: str, manual: str):
        nonlocal n
        n += 1
        progress("")
        progress(f"STEP {n}")
        progress(f"Source testcase and row: {src}")
        progress(f"Manual instruction: {manual}")
        progress(f"Selected action: {m.command_type or '<none>'}")
        progress(f"Selected locator: {m.locator or 'not applicable'}"
                 + (f"  [{'REUSED' if m.locator_reused else 'MISSING'}]" if m.locator else ""))
        progress(f"Generated statement: {m.statement or '<none emitted>'}")
        progress(f"Status: {m.status}")

    for pre in pre_steps or []:
        if pre.locator:
            mapper.required_locators.setdefault(pre.locator, pre.locator)
            pre.locator_reused = cat.has_locator(pre.locator)
            if not pre.locator_reused:
                pre.status = NEEDS_LOCATOR
        mappings.append(pre)
        _emit(pre, pre.source_ref, pre.manual_step)

    for tc, sr in planned:
        ref = f"{tc.testcase_id} row {sr['row']}"
        m = mapper.map_step(sr["step"], ref)

        override = locator_overrides.get(sr["row"])
        if override and m.locator:
            m.statement = m.statement.replace(m.locator, override)
            m.locator = override
            m.locator_reused = cat.has_locator(override)
            mapper.required_locators.setdefault(override, sr.get("step", ""))
            m.status = SUPPORTED if m.locator_reused else NEEDS_LOCATOR
            m.note = (m.note + " | " if m.note else "") + \
                     f"locator retargeted to {override} for this scenario"

        mappings.append(m)
        _emit(m, ref, sr["step"])

        for inj in inject_after.get(sr["row"], []):
            if inj.locator:
                mapper.required_locators.setdefault(inj.locator, inj.locator)
                inj.locator_reused = cat.has_locator(inj.locator)
                if not inj.locator_reused:
                    inj.status = NEEDS_LOCATOR
            mappings.append(inj)
            _emit(inj, inj.source_ref, inj.manual_step)

    for extra in extra_steps or []:
        if extra.locator:
            mapper.required_locators.setdefault(extra.locator, extra.locator)
            extra.locator_reused = cat.has_locator(extra.locator)
            if not extra.locator_reused:
                extra.status = NEEDS_LOCATOR
        mappings.append(extra)
        _emit(extra, extra.source_ref, extra.manual_step)

    progress("")
    progress("FLOW 1 OF 1 CREATED")
    progress("")
    if excluded_rows:
        progress("EXCLUDED SOURCE STEPS")
        for row, why in sorted(excluded_rows.items()):
            progress(f"  row {row}: {why}")

    res.mappings = mappings
    res.required_locators = dict(mapper.required_locators)
    res.reused_locators = {l for l in res.required_locators if cat.has_locator(l)}
    progress(f"[map] result: {summarise(mappings)}")
    progress(f"[locators] reused {len(res.reused_locators)} / "
             f"required {len(res.required_locators)}")

    used_vars: set[str] = set()
    for tc in selected:
        for s in tc.steps:
            used_vars |= referenced_variables(s)
    unresolved = [v for v in sorted(used_vars)
                  if not (bundle.variables.get(v) and bundle.variables[v].is_resolved)
                  and v not in (variable_bindings or {})]

    progress("[emit] rendering flow …")
    map_path = out_path.replace(".flow", ".map.json")
    res.flow_text = emitter.render_clean(
        flow_name=flow_name,
        source_ids=list(testcase_ids),
        source_desc=bundle.source,
        mappings=mappings,
        placeholders=placeholders or {},
        map_path=os.path.relpath(map_path, BASE_DIR),
    )
    res.annotated_text = emitter.render(
        flow_name=flow_name,
        source_ids=list(testcase_ids),
        source_desc=bundle.source,
        testcase_titles={t.testcase_id: t.title for t in selected},
        mappings=mappings,
        placeholders=placeholders or {},
        unresolved_variables=unresolved,
        required_locators=res.required_locators,
        reused_locators=res.reused_locators,
        platform=platform,
        generated_by=cli_command or "ai_flow_builder.pipeline",
        calibration_notes=calibration_notes,
    )
    res.flow_path = emitter.write(out_path, res.flow_text, overwrite=overwrite)
    progress(f"[emit] wrote {res.flow_path}")

    trace = emitter.build_map(
        flow_name=flow_name, flow_path=res.flow_path,
        source_ids=list(testcase_ids), source_desc=bundle.source,
        mappings=mappings, placeholders=placeholders or {},
        header_lines=5,
    )
    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(trace, f, indent=2)
    res.map_path = map_path
    progress(f"[emit] wrote {map_path} ({len(trace['steps'])} traceable steps)")

    _validate(res, platform, progress)
    return res


def _validate(res: GenerationResult, platform: str, progress: Progress) -> None:
    from nlp.parser import parse_step

    progress("[validate] parser …")
    bad: list[str] = []
    for i, line in enumerate(res.flow_text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        probe = s
        for var in referenced_variables(s):
            probe = probe.replace("${" + var + "}", "VAR")
        try:
            parse_step(probe)
        except ValueError as e:
            bad.append(f"line {i}: {s}  -> {e}")
    res.parser_errors = bad
    res.parser_ok = not bad
    progress(f"[validate] parser: {'OK' if res.parser_ok else f'{len(bad)} FAILED'}")

    progress("[validate] flow_lint …")
    proc = subprocess.run(
        [sys.executable, "tools/flow_lint.py", res.flow_path, "--platform", platform],
        cwd=BASE_DIR, capture_output=True, text=True,
    )
    res.lint_exit = proc.returncode
    res.lint_output = (proc.stdout + proc.stderr).strip()
    progress(f"[validate] flow_lint exit={res.lint_exit}")
