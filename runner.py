
"""
runner.py  —  Unified CLI entry point
Usage:
  python runner.py steps.flow          # NLP .flow file (natural language)
  python runner.py custom_test.json    # Codeless JSON flow

Backward compatible: `python runner.py steps.flow` still works exactly as before.
"""
import os
import sys
import json
import logging
import argparse

# Trigger @codeless_snippet registration (must import before ACTION_REGISTRY is used)
import execution.action_service  # noqa: F401

from nlp.parser import parse_step
from locators.cleaner import sanitize_database
from reporting.snippet_sync import sync_locators_to_snippets
from execution.browser_manager import open_browser, close_browser
from execution.session import TestSession
from nlp.variable_manager import RUNTIME_VARIABLES, VariableManager, resolve_variables
from registry import ACTION_REGISTRY
from config import settings
from config import execution_preferences as _prefs

logger = logging.getLogger(__name__)

# ── NLP-flow per-session stop flag ────────────────────────────────────────────
# Variables now live in nlp.variable_manager.RUNTIME_VARIABLES (shared with action_service)
_STOP_ON_FAILURE: bool = False


def _apply_runtime_config(profile_name: str | None, ask_config: bool, save_profile_name: str | None) -> None:
    selected = _prefs.current_preferences()
    has_saved_profile = False

    if profile_name:
        prof = _prefs.get_profile(profile_name)
        if prof is None:
            raise ValueError(f"Profile not found: {profile_name}")
        selected.update(prof)
        logger.info("🧩 Using profile: %s", profile_name)
    else:
        last_name = _prefs.get_last_used_profile_name()
        if last_name:
            prof = _prefs.get_profile(last_name)
            if prof is not None:
                selected.update(prof)
                has_saved_profile = True
                logger.info("🧩 Using last saved profile: %s", last_name)

    should_prompt = ask_config or (not profile_name and not has_saved_profile)
    if should_prompt and sys.stdin.isatty():
        selected = _prefs.prompt_preferences(selected)

    applied = _prefs.apply_preferences(selected)

    if save_profile_name:
        _prefs.save_profile(save_profile_name, applied, set_as_last_used=True)
        logger.info("💾 Saved profile: %s", save_profile_name)
    elif should_prompt and sys.stdin.isatty():
        suggested = input("Save these settings as profile (blank to skip): ").strip()
        if suggested:
            _prefs.save_profile(suggested, applied, set_as_last_used=True)
            logger.info("💾 Saved profile: %s", suggested)

    logger.info(
        "⚙️  Active runtime config | target=%s | rerun_on_failure=%s | report=%s | screenshots=%s | video=%s | slack=%s | email=%s | headless=%s",
        settings.EXECUTION_TARGET,
        settings.RERUN_ON_FAILURE,
        settings.ENABLE_REPORTING,
        settings.ENABLE_SCREENSHOTS,
        settings.ENABLE_VIDEO_RECORDING,
        settings.NOTIFY_ON_SLACK,
        settings.NOTIFY_ON_EMAIL,
        settings.HEADLESS,
    )


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


def _load_run_config() -> dict:
    path = "config/playwright.config.json"
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
        # ── Navigation ───────────────────────────────────────────────────────
        "open":                      lambda: svc.open_site(page, cmd.target),
        "refresh":                   lambda: svc.refresh_page(page),
        # ── Interaction ──────────────────────────────────────────────────────
        "search":                    lambda: svc.search(page, cmd.text),
        "click":                     lambda: svc.click_element(page, cmd.target),
        "fill":                      lambda: svc.fill_element(page, cmd.text, cmd.target),
        # ── Wait / Timing ─────────────────────────────────────────────────────
        "wait":                      lambda: svc.wait_seconds(page, cmd.wait),
        "wait_for_result_page_load": lambda: svc.wait_for_result_page_load(page),
        # ── Scroll ────────────────────────────────────────────────────────────
        "scroll":                    lambda: svc.vertical_scroll(page, cmd.count or 500),
        "scroll_until_text_visible": lambda: svc.scroll_until_text_visible(page, cmd.text, cmd.count, cmd.wait),
        # ── Screenshot ───────────────────────────────────────────────────────
        "screenshot":                lambda: svc.take_screenshot(page, cmd.target or "capture"),
        # ── Verification — Global ────────────────────────────────────────────
        "verify_text":               lambda: svc.verify_global_exact_text(page, cmd.text, exact_match=False),
        "verify_exact_text":         lambda: svc.verify_global_exact_text(page, cmd.text, exact_match=True),
        "verify_multiple_texts":     lambda: svc.verify_multiple_global_texts(page, cmd.text),
        # ── Verification — Element ───────────────────────────────────────────
        "verify_element_exact":      lambda: svc.verify_element_exact_text(page, cmd.target, cmd.text),
        "verify_element_contains":   lambda: svc.verify_element_contains_text(page, cmd.target, cmd.text),
        # ── Verification — Variables ─────────────────────────────────────────
        "verify_var_contains":       lambda: svc.verify_stored_variable_contains(cmd.target, cmd.text),
        # ── Extract — Page info ──────────────────────────────────────────────
        "extract_url":               lambda: svc.extract_page_url(page, cmd.variable_name),
        "extract_title":             lambda: svc.extract_page_title(page, cmd.variable_name),
        # ── Extract — Element data ───────────────────────────────────────────
        "extract_text":              lambda: svc.extract_element_text(page, cmd.target, cmd.variable_name),
        "extract_attribute":         lambda: svc.extract_element_attribute(page, cmd.target, cmd.attribute, cmd.variable_name),
        "extract_input":             lambda: svc.extract_input_value(page, cmd.target, cmd.variable_name),
        "extract_count":             lambda: svc.extract_element_count(page, cmd.target, cmd.variable_name),
        # ── Variables / Data ─────────────────────────────────────────────────
        "create_variable":           lambda: svc.create_custom_variable(cmd.text, cmd.target),
        "math":                      lambda: svc.execute_math(cmd.target, cmd.text, cmd.values[0], cmd.variable_name),
        # ── Image ─────────────────────────────────────────────────────────────
        "verify_image":              lambda: None,  # handled in _interpret below
    }

    handler = dispatch.get(cmd.type)
    if not handler:
        raise ValueError(f"❌ Unknown command type: {cmd.type}")
    handler()


def _interpret(step: str, page):
    """Pre-processes variables, then parses and executes one NLP step."""
    normalized = step.strip()
    logger.info("👉 Interpreting: %s", normalized)

    # Variable injection: ${my_var} → value (shared RUNTIME_VARIABLES from action_service)
    try:
        resolved = resolve_variables(normalized)
    except ValueError as e:
        raise ValueError(str(e)) from e

    try:
        cmd = parse_step(resolved)
    except ValueError as e:
        raise ValueError(f"❌ Invalid syntax or unknown command: {resolved}") from e

    _execute_step_from_command(cmd, page)


def _execute_nlp_flow_core(file_path: str, page) -> dict:
    """
    Inner execution engine — reads and runs one .flow file.

    Returns:
        {"passed": int, "failed": int, "log": list[str]}

    Raises:
        FileNotFoundError  if the flow file doesn't exist.
    """
    stats: dict = {"passed": 0, "failed": 0, "log": []}

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

    return stats


def run_nlp_flow(file_path: str):
    """
    Main NLP .flow runner (CLI entry point).
    Reads .flow file line-by-line, interprets each step, prints summary.
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
        stats = _execute_nlp_flow_core(file_path, page)
    except Exception as e:
        logger.error("❌ Critical Engine Error: %s", e)
        stats["log"].append(f"❌ {e}")

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


def run_nlp_flow_collect(file_path: str, capabilities: dict | None = None) -> dict:
    """
    Like run_nlp_flow() but *returns* stats instead of printing to stdout.
    Used by plan_runner.py to programmatically collect pass/fail counts.

    Args:
        file_path:    path to .flow script
        capabilities: optional desired_capabilities dict from suite JSON
                      (supports: record_video, headless, slow_mo_ms, …)

    Returns:
        {"passed": int, "failed": int, "log": list[str]}
    """
    _setup_logging()
    global _STOP_ON_FAILURE

    sanitize_database()
    run_cfg = _load_run_config()
    _STOP_ON_FAILURE = bool(run_cfg.get("stop_on_failure", False))

    caps = capabilities or {}
    session = TestSession()
    page = None   # guard: open_browser may raise; close_browser handles page=None safely
    stats: dict = {"passed": 0, "failed": 0, "log": []}

    try:
        should_record_video = settings.ENABLE_VIDEO_RECORDING and bool(caps.get("record_video", False))
        page = open_browser(session, record_video=should_record_video)
        logger.info("🚀 Starting flow (collect mode): %s", file_path)
        stats = _execute_nlp_flow_core(file_path, page)
    except FileNotFoundError as e:
        logger.error("❌ %s", e)
        stats["failed"] += 1
        stats["log"].append(f"❌ {e}")
    except Exception as e:
        logger.error("❌ Critical Engine Error: %s", e)
        stats["failed"] += 1
        stats["log"].append(f"❌ {e}")
    finally:
        test_label = os.path.basename(file_path).split(".")[0]
        try:
            close_browser(page, test_label, session)
        except Exception as e:
            logger.error("Browser close failed: %s", e)
        try:
            sync_locators_to_snippets()
        except Exception as e:
            logger.warning("⚠️ Snippet sync failed: %s", e)

    return stats


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
    parser = argparse.ArgumentParser(description="Unified flow runner (.flow / .json)")
    parser.add_argument("target", nargs="?", help="Path to .flow or .json file")
    parser.add_argument("--profile", help="Use saved execution profile name")
    parser.add_argument("--save-profile", help="Save current runtime config as profile name")
    parser.add_argument("--ask-config", action="store_true", help="Interactively ask runtime execution options")
    parser.add_argument("--list-profiles", action="store_true", help="List saved execution profiles")
    parser.add_argument("--delete-profile", help="Delete a saved execution profile by name")
    args = parser.parse_args()

    if args.list_profiles:
        names = _prefs.list_profiles()
        if not names:
            print("No saved profiles.")
        else:
            print("Saved profiles:")
            for n in names:
                print(f"- {n}")
        sys.exit(0)

    if args.delete_profile:
        deleted = _prefs.delete_profile(args.delete_profile)
        if deleted:
            print(f"Deleted profile: {args.delete_profile}")
            sys.exit(0)
        print(f"Profile not found: {args.delete_profile}")
        sys.exit(1)

    if not args.target:
        print("Usage: python runner.py <file.flow | file.json>")
        sys.exit(1)

    try:
        _apply_runtime_config(args.profile, args.ask_config, args.save_profile)
    except ValueError as e:
        print(str(e))
        sys.exit(1)

    target = args.target
    if target.endswith(".json"):
        run_json_flow(target)
    else:
        run_nlp_flow(target)
