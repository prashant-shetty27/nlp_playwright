#!/usr/bin/env python3
"""
ai_flow_builder/cli.py — the tool's testcase → flow generator.

  # inventory only
  python -m ai_flow_builder.cli inventory --xlsx <workbook.xlsx>

  # generate exactly one flow spanning several source testcases
  python -m ai_flow_builder.cli generate \
      --xlsx <workbook.xlsx> \
      --testcase TC_AFP_C01 --testcase TC_AFP_C02 --testcase TC_AFP_C03 \
      --out data/drafts/ask_more_photos_mobile.flow \
      --platform web --max-flows-per-batch 1

Generation never executes anything.
"""
from __future__ import annotations

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from ai_flow_builder import pipeline                      # noqa: E402
from ai_flow_builder.bundle import parse_bundle           # noqa: E402
from ai_flow_builder.mapper import (SUBSTITUTE, SUPPORTED,  # noqa: E402
                                    StepMapping, summarise)
from ai_flow_builder.sources.xlsx_source import XlsxSource   # noqa: E402
from ai_flow_builder import scenario as scenario_mod          # noqa: E402

# Approved bindings: source variable → secure runtime placeholder.
DEFAULT_BINDINGS = {
    "TEST_MOBILE": "${jd_test_mobile}",
    # ${otp} is automatic: static OTP for test numbers (per platform), OTP portal for others
    # (both in Test Data). Drafts default to the touch one; switch for web cases.
    "VALID_OTP": "${otp}",
    "NEW_VALID_OTP": "${otp}",
    "PDP_URL_AI_3IMG": "${product_url}",
    "PDP_URL_AI_1IMG": "${product_url}",
    "PDP_URL_AI_2IMG": "${product_url}",
}

DEFAULT_PLACEHOLDERS = {
    "${product_url}": "suite parameter — DesignTest JDMart product page under test",
    "${jd_test_mobile}": "secure runtime config (JD_TEST_MOBILE) — never stored in this file",
    "${otp}": "Automatic — static OTP for test numbers (Website / Mobile Site), OTP portal for any other number",
}


def _build_source(args):
    if args.xlsx:
        return XlsxSource(args.xlsx)
    if args.spreadsheet:
        from ai_flow_builder.sources.gsheets_source import GoogleSheetsSource

        return GoogleSheetsSource(args.spreadsheet)
    raise SystemExit("supply --xlsx <path> or --spreadsheet <url|id>")


def cmd_inventory(args) -> int:
    src = _build_source(args)
    b = pipeline.load_bundle(src)
    print()
    print(f"spreadsheet          : {b.source.get('title')}")
    print(f"tabs read            : {b.tabs_read}")
    print(f"tabs skipped         : {b.tabs_skipped or 'none'}")
    print(f"testcases detected   : {len(b.testcases)}")
    print(f"interpretable        : {len(b.interpreted)}")
    print(f"duplicate ids        : {b.duplicate_ids or 'none'}")
    print(f"rejected rows        : {len(b.rejections)}")
    print(f"by module            : {b.by_module()}")
    print(f"by classification    : {b.by_classification()}")
    print(f"automatable          : {b.by_automatable()}")
    print(f"unresolved variables : {b.unresolved_variables()}")
    return 0


def cmd_generate(args) -> int:
    sc = scenario_mod.load(args.scenario) if args.scenario else None

    if sc and not args.xlsx and sc.source_xlsx:
        args.xlsx = sc.source_xlsx
    src = _build_source(args)
    b = pipeline.load_bundle(src)

    testcases = args.testcase or (sc.testcases if sc else None)
    if not testcases:
        raise SystemExit("supply --testcase (repeatable) or a --scenario that lists them")
    out = args.out or (sc.output if sc else None)
    if not out:
        raise SystemExit("supply --out or a --scenario with an 'output' field")
    platform = args.platform or (sc.platform if sc else "web")
    name = args.name or (sc.name if sc else "Generated flow")

    cli = ("python -m ai_flow_builder.cli generate"
           + (f" --scenario {args.scenario}" if args.scenario else "")
           + f" --xlsx {args.xlsx}"
           + ("" if args.scenario else "".join(f" --testcase {t}" for t in testcases))
           + f" --out {out} --platform {platform}"
             f" --max-flows-per-batch {args.max_flows_per_batch}"
           + (" --overwrite" if args.overwrite else ""))

    res = pipeline.generate(
        bundle=b,
        testcase_ids=testcases,
        flow_name=name,
        out_path=os.path.join(BASE_DIR, out),
        platform=platform,
        variable_bindings=sc.variable_bindings if sc else {},
        placeholders=sc.placeholders if sc else {},
        pre_steps=scenario_mod.to_step_mappings(sc.inject_before, platform) if sc else None,
        extra_steps=scenario_mod.to_step_mappings(sc.inject_end, platform) if sc else None,
        inject_after={r: scenario_mod.to_step_mappings(v, platform)
                      for r, v in sc.inject_after_row.items()} if sc else None,
        include_rows=sc.resolved_rows if sc else None,
        excluded_rows=sc.exclude_rows if sc else None,
        locator_overrides=sc.locator_overrides if sc else None,
        calibration_notes=sc.notes if sc else None,
        max_flows_per_batch=args.max_flows_per_batch,
        overwrite=args.overwrite,
        cli_command=cli,
    )

    print()
    print("=" * 78)
    print(f"scenario        : {sc.path if sc else '<none — all rows>'}")
    print(f"flow            : {res.flow_path}")
    print(f"source testcases: {', '.join(res.source_ids)}")
    print(f"mapping result  : {summarise(res.mappings)}")
    print(f"locators reused : {sorted(res.reused_locators) or 'none'}")
    print(f"locators missing: {res.missing_locators or 'none'}")
    print(f"parser          : {'OK' if res.parser_ok else res.parser_errors}")
    print(f"flow_lint exit  : {res.lint_exit}")
    print("=" * 78)
    return 0


def cmd_capture(args) -> int:
    from ai_flow_builder import capture_runner

    sc = scenario_mod.load(args.scenario)
    if not sc.capture:
        raise SystemExit(f"{sc.path}: no 'capture' section in this scenario")

    pending = sc.capture.state_changing_stages
    if pending and not args.allow_state_change:
        print("state-changing stages present (skipped unless --allow-state-change):")
        for st in pending:
            print(f"  {st.name}: {st.side_effect}")
        print()

    res = capture_runner.run(sc, allow_state_change=args.allow_state_change,
                             dry_run=args.dry_run,
                             only=set(args.stage) if args.stage else None)
    print()
    print(capture_runner.report(sc, res))
    return 0 if not (res.failed or res.needs_input) else 1


def cmd_locator_add(args) -> int:
    from ai_flow_builder import capture_runner

    sc = scenario_mod.load(args.scenario)
    res = capture_runner.accept_user_locator(
        sc, args.name, args.selector, stage=args.stage, dry_run=args.dry_run,
        absence_guard=args.absence_guard)
    print()
    for k in ("locator", "selector", "mode", "matches", "visible", "enabled",
              "dna_fields", "saved"):
        if k in res:
            print(f"  {k:<12}: {res[k]}")
    for a in res.get("alternates", []):
        print(f"  alternate   : w={a['weight']:<4} {a['type']:<10} {a['value']}")
    if res.get("error"):
        print(f"  ERROR       : {res['error']}")
        return 1
    return 0


def cmd_scenarios(args) -> int:
    for n in scenario_mod.list_scenarios():
        sc = scenario_mod.load(n)
        rows = "all rows" if sc.resolved_rows is None else f"{len(sc.resolved_rows)} rows"
        print(f"  {n:<24} {', '.join(sc.testcases):<40} {rows:<10} -> {sc.output}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ai_flow_builder", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scenarios").set_defaults(func=cmd_scenarios)

    cap = sub.add_parser("capture")
    cap.add_argument("--scenario", required=True)
    cap.add_argument("--stage", action="append", help="capture only these stage(s)")
    cap.add_argument("--dry-run", action="store_true", help="report without saving")
    cap.add_argument("--allow-state-change", action="store_true",
                     help="include stages that perform real, side-effecting actions")
    cap.set_defaults(func=cmd_capture)

    la = sub.add_parser("locator-add", help="save an operator-supplied locator after verifying it live")
    la.add_argument("--scenario", required=True)
    la.add_argument("--name", required=True, help="framework locator name")
    la.add_argument("--selector", required=True, help="CSS or XPath you want to use")
    la.add_argument("--stage", help="capture stage whose state exposes this element")
    la.add_argument("--dry-run", action="store_true")
    la.add_argument("--absence-guard", action="store_true",
                    help="this locator asserts an element is ABSENT; it must match 0 elements")
    la.set_defaults(func=cmd_locator_add)

    for name, fn in (("inventory", cmd_inventory), ("generate", cmd_generate)):
        p = sub.add_parser(name)
        p.add_argument("--xlsx")
        p.add_argument("--spreadsheet")
        p.set_defaults(func=fn)
        if name == "generate":
            p.add_argument("--scenario", help="scenarios/<name>.json (or just <name>)")
            p.add_argument("--testcase", action="append")
            p.add_argument("--out")
            p.add_argument("--name")
            p.add_argument("--platform")
            p.add_argument("--max-flows-per-batch", type=int, default=1)
            p.add_argument("--overwrite", action="store_true")

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
