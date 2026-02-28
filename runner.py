"""
runner.py  —  Unified CLI entry point
Usage:
  python runner.py steps.flow          # NLP .flow file (natural language)
  python runner.py custom_test.json    # Codeless JSON flow

Backward compatible: `python runner.py steps.flow` still works exactly as before.
"""
import os
import sys
import re
import json
import logging

# Trigger @codeless_snippet registration (must import before ACTION_REGISTRY is used)
import execution.action_service  # noqa: F401

from nlp.parser import parse_step
from locators.cleaner import sanitize_database
from reporting.snippet_sync import sync_locators_to_snippets
from execution.browser_manager import open_browser, close_browser
from execution.session import TestSession
from nlp.variable_manager import VariableManager
from registry import ACTION_REGISTRY

logger = logging.getLogger(__name__)

# ── NLP-flow per-session variables (store/use syntax) ─────────────────────────
_VARIABLES: dict = {}
_STOP_ON_FAILURE: bool = False


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


def _load_run_config() -> dict:
    for path in ("config/playwright.config.json", "config.json"):
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f).get("run", {})
            except Exception as e:
                logger.warning("⚠️ Failed to load %s: %s", path, e)
    return {}


# ── NLP flow ──────────────────────────────────────────────────────────────────
def _execute_step_from_command(cmd, page):
    """Routes a parsed Command to the appropriate action function."""
    import execution.action_service as svc

    dispatch = {
        "open":                    lambda: svc.open_site(page, cmd.target),
        "search":                  lambda: svc.search(page, cmd.text),
        "wait":                    lambda: svc.wait_seconds(page, cmd.wait),
        "wait_for_result_page_load": lambda: svc.wait_for_result_page_load(page),
        "refresh":                 lambda: svc.refresh_page(page),
        "scroll_until_text_visible": lambda: svc.scroll_until_text_visible(page, cmd.text, cmd.count, cmd.wait),
        "verify_text":             lambda: svc.verify_global_exact_text(page, cmd.text),
        "click":                   lambda: svc.click_element(page, cmd.target),
        "fill":                    lambda: svc.fill_element(page, cmd.text, cmd.target),
    }

    handler = dispatch.get(cmd.type)
    if not handler:
        raise ValueError(f"❌ Unknown command type: {cmd.type}")
    handler()


def _interpret(step: str, page):
    """Pre-processes variables, then parses and executes one NLP step."""
    normalized = step.strip()
    logger.info("👉 Interpreting: %s", normalized)

    # Variable storage: store "value" as my_var
    if "store " in normalized.lower() and " as " in normalized.lower():
        match = re.search(r'store\s+"(.*?)"\s+as\s+(.*)', step, re.IGNORECASE)
        if match:
            val, var_name = match.groups()
            _VARIABLES[var_name.strip()] = val.strip()
            logger.info("📦 Variable Stored: %s = %s", var_name.strip(), val.strip())
            return

    # Variable injection: {my_var} → value
    resolved = step
    for var_name, val in _VARIABLES.items():
        resolved = resolved.replace(f"{{{var_name}}}", val)

    try:
        cmd = parse_step(resolved)
    except ValueError as e:
        raise ValueError(f"❌ Invalid syntax or unknown command: {resolved}") from e

    _execute_step_from_command(cmd, page)


def run_nlp_flow(file_path: str):
    """
    Main NLP .flow runner.
    Reads .flow file line-by-line, interprets each step, reports results.
    """
    _setup_logging()
    global _STOP_ON_FAILURE

    sanitize_database()
    run_cfg = _load_run_config()
    _STOP_ON_FAILURE = bool(run_cfg.get("stop_on_failure", False))

    session = TestSession()
    page = open_browser(session)
    stats: dict = {"passed": 0, "failed": 0, "log": []}

    try:
        logger.info("🚀 Starting session: %s", file_path)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Cannot find flow file: {file_path}")

        with open(file_path, "r") as f:
            lines = f.readlines()

        for line_num, line in enumerate(lines, 1):
            step = line.strip()
            if not step or step.startswith("#"):
                continue
            try:
                _interpret(step, page)
                stats["passed"] += 1
                stats["log"].append(f"Line {line_num}: ✅ {step}")
            except Exception as e:
                stats["failed"] += 1
                error_msg = str(e).strip()
                stats["log"].append(f"Line {line_num}: ❌ {step} -> {error_msg}")
                logger.error("❌ Failure at Line %s: %s", line_num, error_msg)
                if _STOP_ON_FAILURE:
                    logger.critical("🛑 STOP_ON_FAILURE enabled. Halting.")
                    break

    except Exception as e:
        logger.error("❌ Critical Engine Error: %s", e)

    test_label = os.path.basename(file_path).split(".")[0]

    try:
        close_browser(page, test_label, session)
    except Exception as e:
        logger.error("Browser close failed: %s", e)

    print("\n" + "=" * 80)
    print(f"📊 TEST SUMMARY: {test_label.upper()}")
    print("=" * 80)
    for entry in stats["log"]:
        print(entry)
    print("=" * 80)
    print(
        f"TOTAL: {stats['passed'] + stats['failed']} | "
        f"PASSED: {stats['passed']} | FAILED: {stats['failed']}"
    )
    print("=" * 80 + "\n")

    try:
        sync_locators_to_snippets()
        logger.info("🔄 VS Code Snippets synchronized.")
    except Exception as e:
        logger.warning("⚠️ Snippet sync failed: %s", e)


# ── JSON / codeless flow ────────────────────────────────────────────────────────
def run_json_flow(json_path: str):
    """Codeless JSON flow runner using VariableManager and ACTION_REGISTRY."""
    _setup_logging()

    if not os.path.exists(json_path):
        logger.error("❌ File '%s' not found.", json_path)
        return

    logger.info("📂 Loading Test Flow: %s", json_path)
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            test_steps = json.load(f)
    except Exception as e:
        logger.error("❌ Failed to parse JSON: %s", e)
        return

    runtime_memory = VariableManager(strict_mode=True)
    session = TestSession()
    page = open_browser(session)
    index = 0
    action_name = ""

    try:
        for index, step in enumerate(test_steps):
            action_name = step.get("action", "")
            raw_params = step.get("parameters", {})

            logger.info("▶️ Step %d: [%s]", index + 1, action_name)
            target_function = ACTION_REGISTRY.get(action_name)
            if not target_function:
                raise ValueError(f"Architecture Error: '{action_name}' is not registered.")

            resolved_params = runtime_memory.resolve_parameters(raw_params)
            save_target = resolved_params.pop("save_to_variable_name", None)
            step_result = target_function(page=page, **resolved_params)

            if save_target and step_result is not None:
                runtime_memory.save(save_target, step_result)

        logger.info("✅ Test Flow Executed Successfully!")

    except Exception as e:
        logger.error("❌ Test Failed at Step %d [%s]: %s", index + 1, action_name, e)

    finally:
        close_browser(page, test_name="codeless_run", session=session)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python runner.py <file.flow | file.json>")
        sys.exit(1)

    target = sys.argv[1]
    if target.endswith(".json"):
        run_json_flow(target)
    else:
        run_nlp_flow(target)
