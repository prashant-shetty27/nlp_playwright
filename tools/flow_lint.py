#!/usr/bin/env python3
"""
tools/flow_lint.py
Offline validator for .flow scripts, suite JSON, plan JSON and codeless action JSON.

No browser, no device, no Appium — pure static analysis. This is the gate that makes
AI-authored (or hand-authored) test assets safe to commit: every step is parsed with the
real nlp/parser.py, every locator name is resolved against the real locator databases,
and every command is checked against the dispatch table of the runner that will execute it.

Usage
-----
  python tools/flow_lint.py flows/web/jd_web_02_search_happy_path.flow --platform web
  python tools/flow_lint.py suites/web_regression_suite.json
  python tools/flow_lint.py plans/web_regression_plan.json
  python tools/flow_lint.py --all
  python tools/flow_lint.py --catalog              # emit authoring catalog (for AI authors)
  python tools/flow_lint.py flows/web/*.flow --json

Exit codes
----------
  0  no errors (warnings may be present)
  1  one or more errors
  2  bad invocation / nothing to check
"""
from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import re
import sys
from dataclasses import dataclass, asdict, field

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from nlp.parser import parse_step  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════════
# Findings
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Finding:
    level: str          # "error" | "warning"
    code: str           # E001, W002, …
    file: str
    line: int
    step: str
    message: str
    hint: str = ""


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    files_checked: list[str] = field(default_factory=list)

    def add(self, *a, **kw) -> None:
        self.findings.append(Finding(*a, **kw))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]


# ═══════════════════════════════════════════════════════════════════════════════
# Runner capability matrix — extracted from the real dispatch tables via AST,
# so it can never drift out of sync with runner.py / runner_appium.py.
# ═══════════════════════════════════════════════════════════════════════════════

def _dispatch_keys(module_path: str) -> set[str]:
    """Parse a runner module and return every string key of its `dispatch = {...}` dict."""
    try:
        with open(module_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=module_path)
    except (OSError, SyntaxError):
        return set()

    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "dispatch" for t in node.targets):
            continue
        if isinstance(node.value, ast.Dict):
            for k in node.value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


# Command types handled before dispatch (special-cased inside _interpret).
_PRE_DISPATCH = {"call_reusable"}

_WEB_SUPPORTED = _dispatch_keys(os.path.join(BASE_DIR, "runner.py")) | _PRE_DISPATCH
_APPIUM_SUPPORTED = _dispatch_keys(os.path.join(BASE_DIR, "runner_appium.py")) | _PRE_DISPATCH

# Commands that parse fine but do nothing at runtime on a given platform.
_NOOP = {
    "web": {"verify_image": "verify_image dispatches to `lambda: None` in runner.py — the step "
                            "always passes without comparing anything."},
}

# Kept for backwards compatibility with anything reading it directly; the table in
# nlp/platforms.py is now the authority and covers aliases this dict never did.
_PLATFORM_RUNNER = {
    "web": "web", "chromium": "web", "firefox": "web", "webkit": "web", "mobile": "web",
    "ios": "appium", "android": "appium", "hybrid": "appium",
}


def _supported_for(platform: str) -> set[str]:
    """Commands dispatchable on `platform`.

    Resolves through nlp/platforms.py so an unrecognised value raises instead of
    silently returning the web set — which is how `appium` (a runner name, not a
    platform) previously reported `enter_otp` as supported on mobile.
    """
    from nlp.platforms import runner_for

    return _APPIUM_SUPPORTED if runner_for(platform) == "appium" else _WEB_SUPPORTED


# ═══════════════════════════════════════════════════════════════════════════════
# Locator databases
# ═══════════════════════════════════════════════════════════════════════════════

_APPIUM_PLATFORM_KEYS = {"ios", "android", "hybrid"}


def _read_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def load_locator_names(platform: str, extra_dbs: list[str] | None = None) -> set[str]:
    """
    Every locator name resolvable at run time for `platform`.

    Delegates to locators/sources.py, which is the single declaration of which
    databases exist and in what order the runners consult them. This function
    used to keep its own copy of that list; when a database was added or the
    precedence changed, the linter and the API drifted apart and the linter
    would pass a flow the runner could not resolve (or vice versa).
    """
    from locators.sources import names as _names

    found = set(_names(platform))
    for path in extra_dbs or []:
        data = _read_json(path)
        for group, elements in data.items():
            if isinstance(elements, dict) and not group.startswith("_"):
                found.update(k for k in elements if not k.startswith("_"))
    return found


# ═══════════════════════════════════════════════════════════════════════════════
# Which Command fields carry a locator name (vs. free text / a variable / a URL)
# ═══════════════════════════════════════════════════════════════════════════════

# Which field of a step means what. Shared with the step editor via
# nlp/fields.py — a second copy here would let the editor offer a locator
# picker for a field this linter checks as a variable.
from nlp.fields import (TARGET_IS_LOCATOR as _TARGET_IS_LOCATOR,  # noqa: E402
                        TARGET_IS_VARIABLE as _TARGET_IS_VARIABLE,
                        TEXT_IS_FILE as _TEXT_IS_FILE)

# The RUNTIME grammar, not a stricter one. nlp/variable_manager resolves
# ${anything-up-to-a-brace}, so a linter using a narrower pattern simply
# could not see ${product.id} or ${user-name} — and reported nothing about
# them, defined or not.
from nlp.variables import REFERENCE_RE as _VAR_REF_RE  # noqa: E402

#: A trailing "[last]" / "[2]" index on a locator reference.
_INDEX_SUFFIX_RE = re.compile(r"\[[^\]]*\]$")
_PARAMS_HEADER_RE = re.compile(r"^#\s*Params\s*:\s*(.*)$", re.I)


def _declared_params(lines: list[str]) -> set[str]:
    """Names listed on a leading `# Params :` comment (see nlp/variables)."""
    from nlp.variables import declared_params

    return declared_params(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Known grammar traps — steps that parse successfully into the WRONG command
# because an earlier regex in nlp/parser.py shadows the intended one.
# ═══════════════════════════════════════════════════════════════════════════════

def _grammar_traps(step: str, cmd) -> tuple[str, str] | None:
    low = step.lower().strip()

    if low == "open new tab" and cmd.type == "open":
        return ("W001",
                "`open new tab` is shadowed by the generic `open <target>` rule and parses as "
                "open(target='new tab') — it will try to navigate to a site alias called 'new tab'.")

    if re.match(r'^click\s+text\s+"', low) and cmd.type == "click":
        return ("W001",
                "`click text \"…\"` is shadowed by the generic `click <target>` rule and parses as "
                "click(target='text \"…\"'). Use `tap text \"…\"` for text-based tapping.")

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Flow validation
# ═══════════════════════════════════════════════════════════════════════════════

def _infer_platform(path: str, explicit: str | None) -> str:
    if explicit:
        return explicit.lower()
    norm = path.replace(os.sep, "/").lower()

    # flows/contexts/<platform-ish>/… uses recorder context names, not bare platform keys.
    for marker, plat in (("/ios_app/", "ios"), ("/android_app/", "android"),
                         ("/website/", "web"), ("/hybrid_app/", "hybrid")):
        if marker in norm:
            return plat

    for p in ("ios", "android", "hybrid", "web"):
        if f"/{p}/" in norm or f"_{p}_" in norm or os.path.basename(norm).startswith(p + "_"):
            return p
    return "web"


def validate_flow(
    path: str,
    report: Report,
    platform: str | None = None,
    known_vars: set[str] | None = None,
    locator_names: set[str] | None = None,
) -> None:
    plat = _infer_platform(path, platform)
    supported = _supported_for(plat)
    locators = locator_names if locator_names is not None else load_locator_names(plat)
    defined: set[str] = set(known_vars or set())

    # Globals declared in data/common/variables.json are always in scope.
    common = _read_json(os.path.join(BASE_DIR, "data", "common", "variables.json"))
    defined.update((common.get("global") or {}).keys())
    #: Names Test Data supplies — a step that stores into one of them shadows
    #: the saved value for the rest of the run, which is rarely what was meant.
    stored_names: set[str] = set((common.get("global") or {}).keys())
    for env_vals in (common.get("env") or {}).values():
        if isinstance(env_vals, dict):
            stored_names.update(env_vals.keys())
    #: Names an earlier step of THIS flow produced, with the line that did it.
    produced_here: dict[str, int] = {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        report.add("error", "E005", path, 0, "", f"Cannot read flow file: {e}")
        return

    report.files_checked.append(path)
    screenshot_labels: dict[str, int] = {}

    # A "# Params : a b c" header declares values the flow expects its caller to
    # supply (POST /tests/run parameters, or a suite parameter). Without reading
    # it, every parameterised flow failed E004 on its own first line — the flow
    # was correct and runnable, the linter simply had no notion of run inputs.
    # A flow that declares nothing is unaffected, so genuinely undefined
    # variables are still reported.
    defined.update(_declared_params(lines))
    from execution.test_data import AUTOMATIC
    defined.update(AUTOMATIC)          # ${otp}: filled in by the runner

    # if / loops: every block closed, else inside an if, stop loop inside a loop.
    try:
        from execution.control_flow import FlowProgram, FlowStructureError
        FlowProgram(lines)
    except FlowStructureError as e:
        m = re.match(r"Line (\d+):", str(e))
        report.add("error", "E006", path, int(m.group(1)) if m else 0, "",
                   str(e), "Close each block with 'end if' / 'end for' / 'end repeat'.")

    for line_no, raw in enumerate(lines, 1):
        step = raw.strip()
        if not step or step.startswith("#"):
            continue

        # ── Variable references must be defined before use ────────────────────
        for var in _VAR_REF_RE.findall(step):
            if var not in defined:
                report.add("error", "E004", path, line_no, step,
                           f"Variable '${{{var}}}' is used before it is defined.",
                           "Define it with a suite parameter, or an earlier `store … as "
                           f"{var}` / `create variable {var} …` step.")

        # ── Parse with the real parser ────────────────────────────────────────
        probe = _VAR_REF_RE.sub("VAR", step)   # neutralise vars so regexes still match
        try:
            cmd = parse_step(probe)
        except ValueError as e:
            report.add("error", "E001", path, line_no, step,
                       f"Step does not parse: {e}",
                       "Run `python tools/flow_lint.py --catalog` for the supported grammar.")
            continue

        # ── Block lines: loops define their values for the lines below ───────
        if cmd.type == "block":
            if cmd.text == "for":
                from execution.control_flow import classify as _cls
                ds_name = _cls(step)[1][0]
                defined.add("row_number")
                try:
                    from core import datasets as _ds
                    defined.update(_ds.info(ds_name)["columns"])
                except Exception:  # noqa: BLE001
                    report.add("error", "E007", path, line_no, step,
                               f"No data set called '{ds_name}'.",
                               "Upload it on Test Data → Data Sets.")
            elif cmd.text in ("times", "until", "while"):
                defined.add("round")
            from execution.control_flow import element_names
            for name in element_names(step):
                if name not in locators:
                    report.add("error", "E003", path, line_no, step,
                               f"Locator '{name}' is not defined in the locator database.",
                               "A condition on a missing element is never true.")
            continue

        # ── Runner support for this platform ──────────────────────────────────
        if cmd.type not in supported:
            other = "appium (ios/android/hybrid)" if _PLATFORM_RUNNER.get(plat, "web") == "web" else "web"
            other_set = _APPIUM_SUPPORTED if _PLATFORM_RUNNER.get(plat, "web") == "web" else _WEB_SUPPORTED
            extra = f" It IS supported on {other}." if cmd.type in other_set else ""
            report.add("error", "E002", path, line_no, step,
                       f"Command '{cmd.type}' has no handler in the {plat} runner.{extra}",
                       "Remove the step or move this flow to a platform whose runner supports it.")

        # ── Silent no-ops ─────────────────────────────────────────────────────
        noop_reason = _NOOP.get(_PLATFORM_RUNNER.get(plat, "web") == "web" and "web" or "", {}).get(cmd.type)
        if noop_reason:
            report.add("warning", "W004", path, line_no, step,
                       f"'{cmd.type}' is a no-op on {plat}: {noop_reason}")

        # ── Grammar traps ─────────────────────────────────────────────────────
        trap = _grammar_traps(step, cmd)
        if trap:
            report.add("warning", trap[0], path, line_no, step, trap[1])

        # ── Locator names must resolve ────────────────────────────────────────
        target = getattr(cmd, "target", None)
        if cmd.type in _TARGET_IS_LOCATOR and isinstance(target, str) and target:
            base = _INDEX_SUFFIX_RE.sub("", target).strip()
            looks_like_selector = base.startswith(("//", "(", "#", ".", "role=", "css=", "xpath=", "~"))
            open_calendar = cmd.type == "select_date" and base.lower() == "calendar"
            if not looks_like_selector and not open_calendar and "VAR" not in base and base not in locators:
                scope = (f"the '{plat}' section of" if _PLATFORM_RUNNER.get(plat, "web") == "appium"
                         else "")
                report.add("error", "E003", path, line_no, step,
                           f"Locator '{base}' is not defined in {scope} the locator database.",
                           "Record it with the recorder/spy, or add it to "
                           "data/locators_manual.json before running this flow.")

        # ── Site aliases ──────────────────────────────────────────────────────
        if cmd.type == "open" and isinstance(target, str) and target:
            from config import settings
            if not target.lower().startswith(("http://", "https://")) and "VAR" not in target:
                if target not in settings.SITES:
                    report.add("warning", "W002", path, line_no, step,
                               f"'{target}' is neither a URL nor a key in config/sites.json "
                               f"(known: {', '.join(sorted(settings.SITES)) or 'none'}).")

        # ── Reusable step groups ──────────────────────────────────────────────
        if cmd.type == "call_reusable" and isinstance(target, str):
            try:
                from core.reusable_steps import list_names
                if target not in list_names():
                    report.add("warning", "W003", path, line_no, step,
                               f"Reusable step group '{target}' is not defined in "
                               f"data/reusable_steps.json.")
            except Exception:
                pass

        # ── Data files referenced by excel/csv steps ──────────────────────────
        if cmd.type in _TEXT_IS_FILE and isinstance(cmd.text, str):
            fpath = cmd.text if os.path.isabs(cmd.text) else os.path.join(BASE_DIR, cmd.text)
            if "VAR" not in cmd.text and not os.path.exists(fpath):
                report.add("error", "E005", path, line_no, step,
                           f"Data file '{cmd.text}' does not exist (looked in {fpath}).")

        # ── Duplicate screenshot labels overwrite each other ──────────────────
        if cmd.type == "screenshot" and isinstance(target, str):
            if target in screenshot_labels and target != "capture":
                report.add("warning", "W005", path, line_no, step,
                           f"Screenshot label '{target}' already used on line "
                           f"{screenshot_labels[target]} — the earlier image will be overwritten.")
            screenshot_labels.setdefault(target, line_no)

        # ── Record variables this step defines ────────────────────────────────
        vname = getattr(cmd, "variable_name", None)
        if cmd.type == "create_variable" and isinstance(target, str):
            vname = target
        if isinstance(vname, str) and vname:
            # Same name, second value. Runtime memory simply overwrites, so a
            # later `verify stored x` silently checks the newer value — and a
            # store into a Test Data name replaces the saved value for the
            # rest of the run. Both are legal; neither should be a surprise.
            if vname in produced_here:
                report.add("warning", "W009", path, line_no, step,
                           f"'{vname}' is stored again — line {produced_here[vname]} "
                           f"already stores into it; that value is overwritten from here on.",
                           f"Use a different name (e.g. {vname}2) if both values are "
                           f"needed later.")
            elif vname in stored_names:
                report.add("warning", "W010", path, line_no, step,
                           f"'{vname}' is a saved Test Data value — storing into it "
                           f"replaces that value for the rest of this run.",
                           "Pick a name that is not in Test Data unless overriding "
                           "it here is intended.")
            produced_here.setdefault(vname, line_no)
            defined.add(vname)


# ═══════════════════════════════════════════════════════════════════════════════
# Suite / plan / codeless-JSON validation
# ═══════════════════════════════════════════════════════════════════════════════

def find_owning_suite(flow_path: str) -> tuple[set[str], str | None]:
    """
    Find a suite that lists this flow and return (its parameter names, its platform).

    A .flow validated on its own has no way to know that `${search_term}` is supplied by
    `suites/ios_suite.json`. Resolving the owning suite makes standalone validation agree
    with what actually happens at run time.
    """
    rel = os.path.relpath(flow_path, BASE_DIR)
    for suite_path in sorted(glob.glob(os.path.join(BASE_DIR, "suites", "*.json"))):
        data = _read_json(suite_path)
        scripts = data.get("scripts", [])
        if not any(os.path.normpath(s) == os.path.normpath(rel) for s in scripts):
            continue
        params = {p.get("name") for p in data.get("parameters", [])
                  if isinstance(p, dict) and p.get("name")}
        caps = data.get("desired_capabilities", {}) or {}
        platform = caps.get("platform") or data.get("platform") or caps.get("browser")
        platform = str(platform).lower() if platform else None
        if platform in ("chromium", "firefox", "webkit"):
            platform = "web"
        return params, platform
    return set(), None


def _classify_json(data) -> str:
    if isinstance(data, list):
        return "codeless"
    if isinstance(data, dict):
        if "selected_suites" in data or "plan_name" in data:
            return "plan"
        if "scripts" in data or "suite_name" in data:
            return "suite"
    return "unknown"


def validate_suite(path: str, report: Report) -> None:
    data = _read_json(path)
    if not data:
        report.add("error", "E005", path, 0, "", "Suite file is missing or not valid JSON.")
        return
    report.files_checked.append(path)

    caps = data.get("desired_capabilities", {}) or {}
    platform = str(caps.get("platform") or data.get("platform") or caps.get("browser") or "web").lower()
    if platform in ("chromium", "firefox", "webkit"):
        platform = "web"

    params = {p.get("name") for p in data.get("parameters", []) if isinstance(p, dict) and p.get("name")}

    for p in data.get("parameters", []):
        if not isinstance(p, dict):
            report.add("error", "E006", path, 0, str(p), "Each entry of `parameters` must be an object.")
            continue
        if not p.get("name"):
            report.add("error", "E006", path, 0, json.dumps(p), "Parameter is missing a `name`.")
        if "value" not in p:
            report.add("warning", "W006", path, 0, json.dumps(p),
                       f"Parameter '{p.get('name')}' has no `value` — it resolves to empty at runtime.")

    scripts = data.get("scripts", [])
    if not scripts:
        report.add("warning", "W007", path, 0, "", "Suite lists no scripts — nothing will run.")

    locators = load_locator_names(platform)
    for script in scripts:
        spath = script if os.path.isabs(script) else os.path.join(BASE_DIR, script)
        if not os.path.exists(spath):
            report.add("error", "E005", path, 0, script,
                       f"Suite references '{script}' which does not exist.")
            continue
        validate_flow(spath, report, platform=platform, known_vars=params, locator_names=locators)


def validate_plan(path: str, report: Report) -> None:
    data = _read_json(path)
    if not data:
        report.add("error", "E005", path, 0, "", "Plan file is missing or not valid JSON.")
        return
    report.files_checked.append(path)

    suites = data.get("selected_suites") or data.get("suites") or []
    if not suites:
        report.add("warning", "W007", path, 0, "", "Plan selects no suites — nothing will run.")

    for suite in suites:
        spath = suite if os.path.isabs(suite) else os.path.join(BASE_DIR, suite)
        if not os.path.exists(spath):
            report.add("error", "E005", path, 0, suite,
                       f"Plan references suite '{suite}' which does not exist.")
            continue
        validate_suite(spath, report)


def validate_codeless(path: str, report: Report) -> None:
    """Validate a JSON action list (the `.flow_files/*.json` / ACTION_REGISTRY format)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            steps = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        report.add("error", "E005", path, 0, "", f"Cannot read codeless flow: {e}")
        return
    report.files_checked.append(path)

    import inspect

    import execution.action_service  # noqa: F401 — registers @codeless_snippet actions
    from core.registry import ACTION_REGISTRY

    if not isinstance(steps, list):
        report.add("error", "E006", path, 0, "", "A codeless flow must be a JSON array of steps.")
        return

    defined_vars: set[str] = set()

    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            report.add("error", "E006", path, i, str(step), "Each step must be an object.")
            continue

        action = step.get("action", "")
        fn = ACTION_REGISTRY.get(action)
        if fn is None:
            report.add("error", "E006", path, i, action,
                       f"Action '{action}' is not registered in ACTION_REGISTRY.",
                       "Run `python tools/flow_lint.py --catalog` for the registered action names.")
            continue

        params = step.get("parameters", {})
        if not isinstance(params, dict):
            report.add("error", "E006", path, i, action, "`parameters` must be an object.")
            continue

        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            continue

        accepted = {n for n in sig.parameters if n != "page"}
        required = {n for n, p in sig.parameters.items()
                    if n != "page" and p.default is inspect.Parameter.empty}

        # `save_to_variable_name` is always permitted: actions that declare it write the
        # variable themselves, and for the rest the runner saves the return value.
        allowed = accepted | {"save_to_variable_name"}

        for missing in sorted(required - set(params)):
            report.add("error", "E007", path, i, action,
                       f"Action '{action}' requires parameter '{missing}'.",
                       f"Expected parameters: {', '.join(sorted(accepted)) or 'none'}")

        for unknown in sorted(set(params) - allowed):
            report.add("error", "E007", path, i, action,
                       f"Action '{action}' does not accept parameter '{unknown}'.",
                       f"Expected parameters: {', '.join(sorted(accepted)) or 'none'}")

        # Variable references must be produced by an earlier step.
        for key, val in params.items():
            if key == "save_to_variable_name" or not isinstance(val, str):
                continue
            for var in _VAR_REF_RE.findall(val):
                if var not in defined_vars:
                    report.add("error", "E004", path, i, f"{action} → {key}",
                               f"Variable '${{{var}}}' is used before it is defined.")

        saved = params.get("save_to_variable_name")
        if isinstance(saved, str) and saved:
            defined_vars.add(saved)


# ═══════════════════════════════════════════════════════════════════════════════
# Authoring catalog — the machine-readable contract handed to an AI author
# ═══════════════════════════════════════════════════════════════════════════════

def build_catalog() -> dict:
    from config import settings

    import execution.action_service  # noqa: F401
    from core.registry import ACTION_REGISTRY

    locators_by_platform = {}
    for plat in ("web", "ios", "android"):
        locators_by_platform[plat] = sorted(load_locator_names(plat))

    # Full locator detail for web, grouped by page, so an author can pick sensibly.
    web_groups: dict[str, list[str]] = {}
    for db in (settings.RECORDED_ELEMENTS_FILE, settings.MANUAL_LOCATORS_FILE):
        for group, elements in _read_json(db).items():
            if group in _APPIUM_PLATFORM_KEYS or not isinstance(elements, dict):
                continue
            web_groups.setdefault(group, [])
            web_groups[group].extend(k for k in elements if k not in web_groups[group])

    appium_groups: dict[str, dict[str, list[str]]] = {}
    for db in (settings.MANUAL_LOCATORS_FILE,):
        data = _read_json(db)
        for plat in _APPIUM_PLATFORM_KEYS:
            section = data.get(plat)
            if not isinstance(section, dict):
                continue
            appium_groups.setdefault(plat, {})
            for screen, els in section.items():
                if isinstance(els, dict):
                    appium_groups[plat][screen] = sorted(els.keys())

    try:
        from core.reusable_steps import list_names
        reusables = list_names()
    except Exception:
        reusables = []

    return {
        "generated_by": "tools/flow_lint.py --catalog",
        "command_support": {
            "web_runner": sorted(_WEB_SUPPORTED),
            "appium_runner": sorted(_APPIUM_SUPPORTED),
            "web_only": sorted(_WEB_SUPPORTED - _APPIUM_SUPPORTED),
            "appium_only": sorted(_APPIUM_SUPPORTED - _WEB_SUPPORTED),
            "both": sorted(_WEB_SUPPORTED & _APPIUM_SUPPORTED),
        },
        "locators": {
            "web_by_page": {k: sorted(v) for k, v in sorted(web_groups.items())},
            "appium_by_screen": appium_groups,
            "all_names_by_platform": locators_by_platform,
        },
        "sites": settings.SITES,
        "codeless_actions": sorted(ACTION_REGISTRY.keys()),
        "reusable_step_groups": reusables,
        "known_grammar_traps": [
            "`open new tab` parses as open(target='new tab') — the open_new_tab rule is unreachable.",
            "`click text \"X\"` parses as click(target='text \"X\"') — use `tap text \"X\"` instead.",
            "`verify image \"…\"` is a no-op on web and unsupported on Appium.",
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

_C = {"error": "\033[31m", "warning": "\033[33m", "ok": "\033[32m",
      "dim": "\033[2m", "bold": "\033[1m", "off": "\033[0m"}


def _print_human(report: Report) -> None:
    use_color = sys.stdout.isatty()

    def c(key: str, s: str) -> str:
        return f"{_C[key]}{s}{_C['off']}" if use_color else s

    by_file: dict[str, list[Finding]] = {}
    for f in report.findings:
        by_file.setdefault(f.file, []).append(f)

    for fpath in sorted(by_file):
        rel = os.path.relpath(fpath, BASE_DIR)
        print(f"\n{c('bold', rel)}")
        for f in sorted(by_file[fpath], key=lambda x: (x.line, x.code)):
            badge = c(f.level, f"{f.level.upper():7s} {f.code}")
            loc = c("dim", f"line {f.line}")
            print(f"  {badge} {loc}  {f.message}")
            if f.step:
                print(f"          {c('dim', '│ ' + f.step)}")
            if f.hint:
                print(f"          {c('dim', '└ ' + f.hint)}")

    n_files = len(set(report.files_checked))
    ne, nw = len(report.errors), len(report.warnings)
    print()
    if ne == 0 and nw == 0:
        print(c("ok", f"✓ {n_files} file(s) checked — no issues."))
    else:
        parts = []
        if ne:
            parts.append(c("error", f"{ne} error(s)"))
        if nw:
            parts.append(c("warning", f"{nw} warning(s)"))
        print(f"{n_files} file(s) checked — " + ", ".join(parts))


def _expand_targets(patterns: list[str]) -> list[str]:
    out: list[str] = []
    for pat in patterns:
        hits = glob.glob(pat, recursive=True)
        if hits:
            out.extend(sorted(hits))
        else:
            out.append(pat)
    return out


def _default_targets() -> list[str]:
    targets: list[str] = []
    targets += sorted(glob.glob(os.path.join(BASE_DIR, "plans", "*.json")))
    targets += sorted(glob.glob(os.path.join(BASE_DIR, "suites", "*.json")))
    targets += sorted(glob.glob(os.path.join(BASE_DIR, "flows", "**", "*.flow"), recursive=True))
    return targets


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Offline validator for .flow / suite / plan / codeless-JSON test assets.")
    ap.add_argument("targets", nargs="*", help="Files or globs to validate.")
    ap.add_argument("--all", action="store_true", help="Validate every plan, suite and flow in the repo.")
    ap.add_argument("--platform", help="Force platform for .flow files (web|ios|android|hybrid).")
    ap.add_argument("--json", action="store_true", dest="as_json", help="Emit findings as JSON.")
    ap.add_argument("--catalog", action="store_true",
                    help="Emit the authoring catalog (locators, commands, actions) as JSON.")
    ap.add_argument("--strict", action="store_true", help="Treat warnings as errors.")
    args = ap.parse_args(argv)

    if args.catalog:
        print(json.dumps(build_catalog(), indent=2))
        return 0

    targets = _default_targets() if args.all else _expand_targets(args.targets)
    if not targets:
        ap.print_usage()
        print("\nNothing to validate. Pass files/globs, or use --all.", file=sys.stderr)
        return 2

    report = Report()
    for path in targets:
        abspath = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
        if not os.path.exists(abspath):
            report.add("error", "E005", abspath, 0, "", "File does not exist.")
            continue

        if abspath.endswith(".flow"):
            # Already covered as part of a suite in this run — don't re-check without params.
            if abspath in report.files_checked:
                continue
            suite_params, suite_platform = find_owning_suite(abspath)
            validate_flow(abspath, report,
                          platform=args.platform or suite_platform,
                          known_vars=suite_params)
        elif abspath.endswith(".json"):
            kind = _classify_json(json.load(open(abspath, encoding="utf-8"))
                                  if os.path.getsize(abspath) else None)
            if kind == "plan":
                validate_plan(abspath, report)
            elif kind == "suite":
                validate_suite(abspath, report)
            elif kind == "codeless":
                validate_codeless(abspath, report)
            else:
                report.add("warning", "W008", abspath, 0, "",
                           "Unrecognised JSON shape — not a plan, suite or codeless flow. Skipped.")
        else:
            report.add("warning", "W008", abspath, 0, "", "Unsupported file type. Skipped.")

    if args.as_json:
        print(json.dumps({
            "files_checked": sorted(set(os.path.relpath(f, BASE_DIR) for f in report.files_checked)),
            "errors": len(report.errors),
            "warnings": len(report.warnings),
            "findings": [asdict(f) | {"file": os.path.relpath(f.file, BASE_DIR)} for f in report.findings],
        }, indent=2))
    else:
        _print_human(report)

    if report.errors:
        return 1
    if args.strict and report.warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
