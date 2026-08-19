"""
ai_flow_builder/capture_runner.py

Drives locators/auto_capture.py from a scenario's capture plan.

Separate from generation on purpose: `generate` stays offline and deterministic —
no browser, no credentials, no network — while `capture` is the one command that
touches the application.

Stage traversal performs real actions. Any stage the scenario marks
`state_changing` is skipped unless the operator passes allow_state_change, and the
declared side effect is printed before it runs.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from ai_flow_builder.scenario import CaptureStage, Scenario  # noqa: E402
from locators import auto_capture  # noqa: E402

Progress = print
#: The one definition, in nlp/variables. A private copy here agreed with
#: it today and had nothing keeping it in step tomorrow.
from nlp.variables import REFERENCE_RE as _VAR  # noqa: E402
@dataclass
class CaptureRun:
    captured: dict[str, str] = field(default_factory=dict)      # locator -> selector
    reused: dict[str, str] = field(default_factory=dict)        # locator -> existing selector
    needs_input: dict[str, str] = field(default_factory=dict)   # locator -> why we won't guess
    failed: dict[str, str] = field(default_factory=dict)        # locator -> why
    skipped_stages: list[tuple[str, str]] = field(default_factory=list)
    results: list = field(default_factory=list)
    saved: dict[str, str] = field(default_factory=dict)


def _bind_params(sc: Scenario) -> dict:
    """
    Resolve ${...} used by capture — product URL from the scenario, secrets from
    the environment. Values are used, never printed.
    """
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE_DIR, ".env"))
    params: dict[str, str] = {}
    for holder in sc.placeholders:
        name = holder.strip("${}")
        env_key = name.upper()
        if os.environ.get(env_key):
            params[name] = os.environ[env_key]
    if sc.capture and sc.capture.start_url and not _VAR.search(sc.capture.start_url):
        params.setdefault("product_url", sc.capture.start_url)
    return params


def _missing_vars(text: str, params: dict) -> list[str]:
    return [v for v in _VAR.findall(text or "") if v not in params]


def run(sc: Scenario, *, allow_state_change: bool = False, dry_run: bool = False,
        only: set[str] | None = None, progress=Progress) -> CaptureRun:
    """Execute the scenario's capture plan and return what was captured."""
    if not sc.capture:
        raise ValueError(f"{sc.path}: scenario has no 'capture' section")

    plan = sc.capture
    run_res = CaptureRun()
    params = _bind_params(sc)

    start = plan.start_url
    for v in _VAR.findall(start):
        if v in params:
            start = start.replace("${" + v + "}", params[v])
    if _VAR.search(start):
        raise ValueError(f"capture.start_url still unresolved: {start} "
                         f"(set the matching environment variable)")

    import execution.action_service as svc
    from execution.browser_manager import close_browser, open_browser
    from execution.session import TestSession
    from nlp.variable_manager import RUNTIME_VARIABLES, bind_runtime_variables

    caps = {"mobile_web": bool(plan.device), "device_name": plan.device,
            "http_auth_domain": plan.http_auth_domain}

    session = TestSession()
    bind_runtime_variables(session.runtime_variables)
    RUNTIME_VARIABLES.update(params)
    svc.set_test_session(session)

    progress(f"[capture] device={plan.device or 'desktop'} "
             f"auth_domain={plan.http_auth_domain or 'none'} group={plan.locator_group}")
    progress(f"[capture] dry_run={dry_run}  allow_state_change={allow_state_change}")

    page = open_browser(session, capabilities=caps)
    import runner as _runner

    try:
        progress(f"[capture] navigating to the start URL")
        page.goto(start, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(2500)

        for stage in plan.stages:
            if only and stage.name not in only:
                run_res.skipped_stages.append((stage.name, "not selected by --stage"))
                continue

            if stage.state_changing and not allow_state_change:
                run_res.skipped_stages.append(
                    (stage.name, f"state-changing ({stage.side_effect}) — "
                                 f"re-run with --allow-state-change to include it"))
                progress(f"\n[stage {stage.name}] SKIPPED — state-changing: {stage.side_effect}")
                progress(f"[stage {stage.name}] elements not captured: "
                         f"{[e.locator for e in stage.elements]}")
                continue

            progress(f"\n[stage {stage.name}] reach: {stage.reach or '(already here)'}")
            if stage.state_changing:
                progress(f"[stage {stage.name}] ⚠️  SIDE EFFECT: {stage.side_effect}")

            ok = True
            for stmt in stage.reach:
                missing = _missing_vars(stmt, params)
                if missing:
                    ok = False
                    run_res.skipped_stages.append(
                        (stage.name, f"unresolved variable(s) {missing} in reach step"))
                    progress(f"[stage {stage.name}] BLOCKED — unresolved {missing}")
                    break
                try:
                    _runner._interpret(stmt, page)
                except Exception as e:
                    ok = False
                    run_res.skipped_stages.append((stage.name, f"reach failed: {e}"))
                    progress(f"[stage {stage.name}] reach FAILED on {stmt!r}: {str(e)[:120]}")
                    break
            if not ok:
                continue
            page.wait_for_timeout(1500)

            for el in stage.elements:
                res = auto_capture.capture(page, el.locator, el.element,
                                           scope=el.scope or None,
                                           text_hint=el.text_hint or None)
                run_res.results.append(res)
                tag = f"[stage {stage.name}] {el.locator:<22}"
                if res.status == "CAPTURED":
                    run_res.captured[el.locator] = res.chosen.selector
                    progress(f"{tag} CAPTURED  w={res.chosen.weight:<4} {res.chosen.selector}")
                    if res.detail:
                        progress(f"{'':<24}   {res.detail}")
                elif res.status == "REUSED":
                    prior = res.existing.get("by_name") or {}
                    run_res.reused[el.locator] = prior.get("selector", "")
                    progress(f"{tag} REUSED    {prior.get('selector','')}")
                    if not prior.get("has_dna"):
                        progress(f"{'':<24}   no DNA stored — ML healing cannot run on this one")
                elif res.status == "NEEDS_USER_INPUT":
                    run_res.needs_input[el.locator] = res.detail
                    best = f"{res.chosen.selector} (w={res.chosen.weight})" if res.chosen else "none"
                    progress(f"{tag} NEEDS YOUR INPUT — {res.detail}")
                    progress(f"{'':<24}   best guess: {best}")
                    progress(f"{'':<24}   supply one with:  python -m ai_flow_builder.cli locator-add \\")
                    progress(f"{'':<24}       --scenario {sc.name} --name {el.locator} \\")
                    progress(f"{'':<24}       --stage {stage.name} --selector '<your css or xpath>'")
                else:
                    run_res.failed[el.locator] = f"{res.status}: {res.detail}"
                    progress(f"{tag} {res.status} — {res.detail[:70]}")
    finally:
        try:
            close_browser(page, "auto_capture", session)
        except Exception:
            pass

    if dry_run:
        progress("\n[capture] dry run — nothing written to the locator database")
    else:
        run_res.saved = auto_capture.save(run_res.results, plan.locator_group)
        progress(f"\n[capture] saved {len(run_res.saved)} locator(s) into "
                 f"'{plan.locator_group}' with DNA")
    return run_res


def accept_user_locator(sc: Scenario, locator_name: str, selector: str, *,
                        stage: str | None = None, dry_run: bool = False,
                        absence_guard: bool = False, progress=Progress) -> dict:
    """
    Save a locator the operator supplied, after proving it on the live page.

    The selector is never taken on trust: it must resolve to exactly one visible
    element. Element DNA is captured from whatever it resolves to, and better
    alternates found on the same element are stored alongside it — so if the
    supplied selector breaks later there is already something to fall back to.
    """
    plan = sc.capture
    if not plan:
        raise ValueError(f"{sc.path}: scenario has no 'capture' section")

    params = _bind_params(sc)
    start = plan.start_url
    for v in _VAR.findall(start):
        if v in params:
            start = start.replace("${" + v + "}", params[v])

    import execution.action_service as svc
    from execution.browser_manager import close_browser, open_browser
    from execution.session import TestSession
    from nlp.variable_manager import RUNTIME_VARIABLES, bind_runtime_variables

    session = TestSession()
    bind_runtime_variables(session.runtime_variables)
    RUNTIME_VARIABLES.update(params)
    svc.set_test_session(session)
    page = open_browser(session, capabilities={
        "mobile_web": bool(plan.device), "device_name": plan.device,
        "http_auth_domain": plan.http_auth_domain})
    import runner as _runner

    out = {"locator": locator_name, "selector": selector}
    try:
        page.goto(start, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(2500)

        # Walk to the stage that contains this element, if one was named.
        if stage:
            for st in plan.stages:
                if st.name != stage:
                    continue
                if st.state_changing:
                    out["error"] = (f"stage {stage!r} is state-changing "
                                    f"({st.side_effect}); run capture with "
                                    f"--allow-state-change instead")
                    return out
                for stmt in st.reach:
                    _runner._interpret(stmt, page)
                page.wait_for_timeout(1500)

        n, vis, en = auto_capture._measure(page, selector)
        out.update(matches=n, visible=vis, enabled=en)
        progress(f"[verify] {selector}  n={n} visible={vis} enabled={en}")

        if absence_guard:
            # A guard for something that must NOT exist. There is no element to
            # describe, so there is no DNA and no alternates — the selector IS the
            # assertion. It is still verified: if it matches now, the thing the
            # test says is gone is actually present, and saving it would bake a
            # guaranteed failure into the suite.
            out["mode"] = "absence_guard"
            if n:
                out["error"] = (f"absence guard matched {n} element(s) right now. "
                                f"The element this guard asserts is gone is present, "
                                f"so the guard would fail immediately.")
                return out
            progress(f"[verify] absence guard confirmed — 0 matches on the live page")
            if dry_run:
                out["saved"] = False
                return out
            import json

            from config import settings

            path = settings.MANUAL_LOCATORS_FILE
            data = json.load(open(path, encoding="utf-8"))
            grp = data.setdefault(plan.locator_group, {})
            grp[locator_name] = {
                "custom_xpath": selector,
                "selectors": [{"type": "user", "value": selector}],
                "last_success": "user",
                "_approved": True,
                "_source": "operator-supplied",
                "_absence_guard": True,
                "_evidence": "0 matches — asserts this element is absent",
                "_note": ("Guard only. Use with 'verify element ... is not present'. "
                          "It has no DNA because there is no element to describe, so "
                          "ML healing does not apply."),
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            out["saved"] = True
            return out

        if n != 1 or not vis:
            out["error"] = (f"supplied selector resolves to {n} element(s), visible={vis}. "
                            f"It must match exactly one visible element to be saved.")
            return out

        dna = page.evaluate("""(sel) => {
            const el = document.querySelector(sel) ||
                       document.evaluate(sel, document, null, 9, null).singleNodeValue;
            if (!el) return null;
            const a = {}; for (const x of el.attributes) a[x.name] = x.value;
            const r = el.getBoundingClientRect();
            return {tagName: el.tagName.toLowerCase(), id: el.id || null,
                    className: (typeof el.className === 'string' ? el.className : ''),
                    innerText: (el.innerText || '').trim().slice(0,120),
                    ownText: [...el.childNodes].filter(n=>n.nodeType===3)
                              .map(n=>n.textContent).join(' ').trim().slice(0,120),
                    attributes: a, rect: {x: Math.round(r.x), y: Math.round(r.y),
                                          width: Math.round(r.width), height: Math.round(r.height)}};
        }""", selector)
        if not dna:
            out["error"] = "selector resolved for counting but no element could be described"
            return out
        out["dna_fields"] = sorted(k for k, v in dna.items() if v)

        # Independently found alternates on the same element, kept for later trouble.
        alts = [c for c in (auto_capture.validate(page, c)
                            for c in auto_capture.build_candidates(dna))
                if c.usable and c.selector != selector]
        alts = sorted(alts, key=lambda c: -c.weight)[:3]
        out["alternates"] = [{"type": c.kind, "value": c.selector, "weight": c.weight}
                             for c in alts]
        for c in alts:
            progress(f"[alternate] w={c.weight:<4} {c.kind:<11} {c.selector}")

        if dry_run:
            out["saved"] = False
            return out

        import json

        from config import settings

        path = settings.MANUAL_LOCATORS_FILE
        data = json.load(open(path, encoding="utf-8"))
        grp = data.setdefault(plan.locator_group, {})
        grp[locator_name] = {
            "custom_xpath": selector,
            "selectors": [{"type": "user", "value": selector}] + out["alternates"],
            "last_success": "user",
            "_approved": True,
            "_source": "operator-supplied",
            "_evidence": f"{n} match, visible={vis}, enabled={en}",
            "tagName": dna.get("tagName"), "id": dna.get("id"),
            "className": dna.get("className"), "innerText": dna.get("innerText"),
            "rect": dna.get("rect"), "attributes": dna.get("attributes", {}),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        out["saved"] = True
    finally:
        try:
            close_browser(page, "locator_add", session)
        except Exception:
            pass
    return out


def report(sc: Scenario, run_res: CaptureRun) -> str:
    lines = [auto_capture.report(run_res.results), ""]
    lines.append(f"captured    : {len(run_res.captured)}   {sorted(run_res.captured)}")
    lines.append(f"reused      : {len(run_res.reused)}   {sorted(run_res.reused)}")
    if run_res.needs_input:
        lines.append(f"needs input : {len(run_res.needs_input)} — the tool will not guess these:")
        for k, v in run_res.needs_input.items():
            lines.append(f"    {k}: {v}")
    if run_res.failed:
        lines.append(f"failed   : {len(run_res.failed)}")
        for k, v in run_res.failed.items():
            lines.append(f"    {k}: {v}")
    if run_res.skipped_stages:
        lines.append("skipped stages:")
        for name, why in run_res.skipped_stages:
            lines.append(f"    {name}: {why}")
    if run_res.saved:
        lines.append(f"saved    : {run_res.saved}")
    return "\n".join(lines)
