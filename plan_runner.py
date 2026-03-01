"""
plan_runner.py  —  Test Plan Orchestrator
=========================================
Usage:
  python plan_runner.py plans/example_plan.json
  python plan_runner.py plans/example_plan.json --dry-run
  python plan_runner.py plans/example_plan.json --dry-run --suites suites/search_suite.json
  python plan_runner.py plans/example_plan.json --suites suites/search_suite.json suites/navigation_suite.json

What it does:
  1. Reads a Plan JSON  (plans/*.json)
  2. Resolves which suites to execute (plan.selected_suites or --suites flag)
  3. For each suite  (suites/*.json):
       a. Injects suite parameters into RUNTIME_VARIABLES
       b. Applies desired_capabilities overrides to env
       c. Runs each .flow script via the NLP runner
       d. Respects retry_on_failure / max_retries per step
       e. Optionally reruns the whole suite on failure (rerun_on_failure)
  4. Builds a PlanResult object
  5. Sends a Slack Block Kit notification (if NOTIFY_ON_SLACK=true)
  6. Sends an email report           (if NOTIFY_ON_EMAIL=true  — future)
  7. Writes JSON + text report to    data/logs/
  8. Exits with code 0 (all passed) or 1 (any failure)
"""
import argparse
import json
import logging
import os
import sys
import time

# ── Ensure project root is on the path when run directly ──────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Trigger @codeless_snippet registration
import execution.action_service  # noqa: F401

from nlp.variable_manager import RUNTIME_VARIABLES
from reporting.slack_notifier import PlanResult, SuiteResult, ScriptResult, send_report, preview_blocks
from reporting import email_notifier
from reporting.report_manager import TestReportManager
from runner import run_nlp_flow_collect as _run_flow_file
from config import settings as _cfg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# JSON LOADERS
# ─────────────────────────────────────────────────────────────────────────────

def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in '{path}': {exc}") from exc


def _load_plan(plan_path: str) -> dict:
    plan = _load_json(plan_path)
    # Defaults
    plan.setdefault("plan_name", os.path.basename(plan_path))
    plan.setdefault("environment", "local")
    plan.setdefault("owner", "")
    exec_cfg = plan.setdefault("execution", {})
    exec_cfg.setdefault("parallel",             False)
    exec_cfg.setdefault("max_wait_seconds",     300)
    exec_cfg.setdefault("retry_on_failure",     False)
    exec_cfg.setdefault("max_retries",          1)
    exec_cfg.setdefault("rerun_on_failure",     False)
    exec_cfg.setdefault("stop_on_first_failure",False)
    plan.setdefault("notifications", {})
    plan["notifications"].setdefault("slack", None)   # None = use .env
    plan["notifications"].setdefault("email", None)
    plan.setdefault("suites", [])
    plan.setdefault("selected_suites", [])
    return plan


def _load_suite(suite_path: str) -> dict:
    suite = _load_json(suite_path)
    suite.setdefault("suite_name", os.path.basename(suite_path))
    suite.setdefault("scripts", [])
    suite.setdefault("desired_capabilities", {})
    suite.setdefault("parameters", [])
    return suite


# ─────────────────────────────────────────────────────────────────────────────
# PARAMETER INJECTION
# ─────────────────────────────────────────────────────────────────────────────

def _inject_parameters(parameters: list[dict]) -> None:
    """Load suite parameters into RUNTIME_VARIABLES so .flow scripts can use ${param_name}."""
    for param in parameters:
        name  = param.get("name", "").strip()
        value = str(param.get("value", "")).strip()
        ptype = param.get("type", "string").lower()

        if not name:
            continue

        # Basic type coercion for integer / boolean
        if ptype == "integer":
            try:
                value = str(int(float(value)))
            except ValueError:
                pass
        elif ptype == "boolean":
            value = "true" if value.lower() in ("true", "1", "yes", "y") else "false"

        RUNTIME_VARIABLES[name] = value
        logger.info("  🔑 Param injected: ${%s} = '%s'  [%s]", name, value, ptype)


# ─────────────────────────────────────────────────────────────────────────────
# SINGLE SCRIPT RUNNER  (with retry)
# ─────────────────────────────────────────────────────────────────────────────

def _run_script(
    script_path: str,
    retry_on_failure: bool,
    max_retries: int,
    dry_run: bool,
) -> ScriptResult:
    """Run one .flow script; retry up to max_retries on failure."""
    attempts = 0
    last_error = ""

    while True:
        attempts += 1
        t0 = time.time()

        if dry_run:
            logger.info("  🔍 [DRY-RUN] Would execute: %s", script_path)
            return ScriptResult(script_path, "passed", duration_s=0.0, retries=attempts - 1)

        if not os.path.exists(script_path):
            return ScriptResult(
                script_path, "skipped",
                failure_reason=f"File not found: {script_path}",
            )

        try:
            logger.info("  ▶  Running: %s  (attempt %d)", script_path, attempts)
            stats    = _run_flow_file(script_path)
            duration = time.time() - t0
            retries  = attempts - 1

            if stats["failed"] == 0:
                logger.info("  ✅ PASSED: %s  (%.1fs)", script_path, duration)
                return ScriptResult(script_path, "passed", duration_s=duration, retries=retries)

            # Flow ran but had step-level failures
            last_error = next(
                (ln for ln in reversed(stats["log"]) if "❌" in ln),
                f"{stats['failed']} step(s) failed",
            )
            logger.error("  ❌ FAILED: %s  (%.1fs) — %s steps failed", script_path, duration, stats["failed"])
        except Exception as e:
            duration   = time.time() - t0
            last_error = str(e).strip()
            logger.error("  ❌ ERROR: %s  (%.1fs) — %s", script_path, duration, last_error)

        # Reached here = failure path
        if retry_on_failure and attempts <= max_retries:
            logger.info("  🔁 Retrying (%d/%d)…", attempts, max_retries)
            time.sleep(1.0)
            continue

        retries = attempts - 1
        return ScriptResult(
            script_path, "failed",
            duration_s=duration,
            retries=retries,
            failure_reason=last_error,
        )


# ─────────────────────────────────────────────────────────────────────────────
# SUITE RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def _run_suite(
    suite: dict,
    exec_cfg: dict,
    dry_run: bool,
) -> SuiteResult:
    logger.info("")
    logger.info("📦  Suite: %s", suite["suite_name"])
    logger.info("    Scripts : %s", suite["scripts"])
    logger.info("    Params  : %d configured", len(suite["parameters"]))

    _inject_parameters(suite["parameters"])

    retry_on = exec_cfg["retry_on_failure"]
    max_ret  = exec_cfg["max_retries"]
    stop     = exec_cfg["stop_on_first_failure"]

    def _run_all() -> SuiteResult:
        sr = SuiteResult(suite["suite_name"])
        for script in suite["scripts"]:
            res = _run_script(script, retry_on, max_ret, dry_run)
            sr.add(res)
            if stop and res.status == "failed":
                logger.warning("  🛑 stop_on_first_failure=true — halting suite.")
                break
        return sr

    # First pass
    suite_result = _run_all()

    # Rerun whole suite on failure if configured
    if exec_cfg["rerun_on_failure"] and suite_result.failed > 0:
        logger.info("  ♻️  rerun_on_failure=true — re-running suite: %s", suite["suite_name"])
        suite_result = _run_all()

    return suite_result


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def run_plan(plan_path: str, suite_overrides: list[str] = None, dry_run: bool = False) -> int:
    """
    Execute a test plan.

    Returns:
        0  — all scripts passed
        1  — one or more failures / plan not found
    """
    logger.info("=" * 70)
    logger.info("🚀  PLAN RUNNER  —  %s", plan_path)
    logger.info("=" * 70)

    try:
        plan = _load_plan(plan_path)
    except (FileNotFoundError, ValueError) as e:
        logger.error("❌ %s", e)
        return 1

    exec_cfg = plan["execution"]

    # Resolve which suite files to run
    candidates = suite_overrides or plan["selected_suites"] or plan["suites"]
    if not candidates:
        logger.error("❌ No suites configured in plan and no --suites flag provided.")
        return 1

    logger.info("📋  Plan      : %s", plan["plan_name"])
    logger.info("🌍  Env       : %s", plan["environment"])
    logger.info("⚡  Parallel  : %s", exec_cfg["parallel"])
    logger.info("🔁  Retry     : %s (max %sx)", exec_cfg["retry_on_failure"], exec_cfg["max_retries"])
    logger.info("♻️   Rerun     : %s", exec_cfg["rerun_on_failure"])
    logger.info("🛑  StopFirst : %s", exec_cfg["stop_on_first_failure"])
    logger.info("📦  Suites    : %s", candidates)

    plan_result = PlanResult(
        plan_name        = plan["plan_name"],
        environment      = plan["environment"],
        platform         = plan.get("platform", _cfg.PLATFORM),
        parallel         = exec_cfg["parallel"],
        retry_on_failure = exec_cfg["retry_on_failure"],
        max_retries      = exec_cfg["max_retries"],
        rerun_on_failure = exec_cfg["rerun_on_failure"],
        owner            = plan.get("owner", ""),
    )

    # ── Run suites ────────────────────────────────────────────────────────────
    # TODO: if parallel=true, use concurrent.futures.ThreadPoolExecutor here
    for suite_path in candidates:
        try:
            suite = _load_suite(suite_path)
        except (FileNotFoundError, ValueError) as e:
            logger.error("❌ Suite load error: %s", e)
            skipped = SuiteResult(os.path.basename(suite_path))
            skipped.add(ScriptResult(suite_path, "skipped", failure_reason=str(e)))
            plan_result.add_suite(skipped)
            continue

        suite_result = _run_suite(suite, exec_cfg, dry_run)
        plan_result.add_suite(suite_result)

        if exec_cfg["stop_on_first_failure"] and suite_result.failed > 0:
            logger.warning("🛑  stop_on_first_failure — halting plan after suite: %s", suite["suite_name"])
            break

    plan_result.finish()

    # ── Print summary ─────────────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 70)
    logger.info("📊  PLAN SUMMARY: %s", plan["plan_name"])
    logger.info("=" * 70)
    for suite in plan_result.suites:
        logger.info("  %s  %s  (%d/%d passed)", suite.overall_status, suite.suite_name,
                    suite.passed, suite.total)
        for sc in suite.scripts:
            logger.info("      %s  %s  (%.1fs)", sc.icon, sc.script.split("/")[-1], sc.duration_s)
    logger.info("-" * 70)
    logger.info(
        "  TOTAL: %d  |  ✅ %d PASSED  |  ❌ %d FAILED  |  ⏭ %d SKIPPED  |  ⏱ %.1fs",
        plan_result.total_scripts, plan_result.total_passed,
        plan_result.total_failed,  plan_result.total_skipped,
        plan_result.duration_s,
    )
    logger.info("=" * 70)

    # ── Write report files ────────────────────────────────────────────────────
    report_mgr = TestReportManager(
        testplan_name=plan["plan_name"],
        executer_name=plan.get("owner", "plan_runner"),
    )
    for suite in plan_result.suites:
        for sc in suite.scripts:
            report_mgr.add_result(
                test_name=f"[{suite.suite_name}] {sc.script.split('/')[-1]}",
                status=sc.status,
                reason=sc.failure_reason or None,
            )
    json_path, txt_path = report_mgr.generate_report()
    plan_result.report_json_path = json_path
    plan_result.report_txt_path  = txt_path
    logger.info("📁  Report saved: %s", json_path)
    logger.info("📁  Report saved: %s", txt_path)

    # ── Notifications ─────────────────────────────────────────────────────────
    notify_cfg   = plan.get("notifications", {})
    slack_enable = notify_cfg.get("slack")    # None = honour .env
    email_enable = notify_cfg.get("email")

    if dry_run:
        logger.info("\n📋  [DRY-RUN] Slack Block Kit payload preview:\n%s", preview_blocks(plan_result))
    else:
        # Slack
        if slack_enable is not False:          # None or True = try
            force = slack_enable is True       # plan JSON True → override .env
            if force or _cfg.NOTIFY_ON_SLACK:
                send_report(plan_result, force=force)

        # Email (future)
        if email_enable is not False:
            force = email_enable is True
            if force or _cfg.NOTIFY_ON_EMAIL:
                email_notifier.send_report(plan_result, force=force)

    return 0 if plan_result.total_failed == 0 else 1


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="NLP-Playwright Test Plan Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python plan_runner.py plans/example_plan.json
  python plan_runner.py plans/example_plan.json --dry-run
  python plan_runner.py plans/example_plan.json --suites suites/search_suite.json suites/navigation_suite.json
        """,
    )
    parser.add_argument("plan", help="Path to plan JSON file  (e.g. plans/example_plan.json)")
    parser.add_argument(
        "--suites", nargs="+", metavar="SUITE",
        help="Override suite selection — one or more suite JSON paths",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would run + Slack Block Kit preview without executing anything",
    )

    args = parser.parse_args()
    exit_code = run_plan(args.plan, suite_overrides=args.suites, dry_run=args.dry_run)
    sys.exit(exit_code)
