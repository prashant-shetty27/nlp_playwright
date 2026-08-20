
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
import threading

# Trigger @codeless_snippet registration (must import before ACTION_REGISTRY is used)
import execution.action_service  # noqa: F401

from nlp.parser import parse_step
from locators.cleaner import sanitize_database
from reporting.snippet_sync import sync_locators_to_snippets
from execution.browser_manager import open_browser, close_browser
from execution.session import TestSession
from nlp.variable_manager import (
    RUNTIME_VARIABLES,
    VariableManager,
    bind_runtime_variables,
    resolve_variables,
)
from registry import ACTION_REGISTRY
from config import settings
from config import execution_preferences as _prefs

logger = logging.getLogger(__name__)

# ── Per-thread execution state (industry-standard thread-local isolation) ─────
# Each concurrent run gets its own stop flag and call stack — no cross-run bleed.
_thread_local = threading.local()


def _get_stop_on_failure() -> bool:
    return getattr(_thread_local, "stop_on_failure", False)


def _set_stop_on_failure(val: bool) -> None:
    _thread_local.stop_on_failure = val


def _get_call_stack() -> set:
    if not hasattr(_thread_local, "call_stack"):
        _thread_local.call_stack = set()
    return _thread_local.call_stack


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
# Command types whose `target` is a variable name to look up or create, not a value.
_VARIABLE_NAME_TARGETS = {"verify_var_contains", "verify_var_not_equals",
                          "create_variable", "extract_json"}


def _execute_step_from_command(cmd, page):
    """Routes a parsed Command to the appropriate action function."""
    import execution.action_service as svc

    # Resolve active tab: if user ran "switch to tab N", actions run on that tab
    ep = svc.get_active_page(page)
    target = resolve_variables(cmd.target) if isinstance(getattr(cmd, "target", None), str) else getattr(cmd, "target", None)

    # A few commands take a variable NAME as their target rather than a value.
    # resolve_variables() substitutes a bare name for its own contents, so pre-resolving
    # these hands the action the stored value where it expects the key — and the lookup
    # that follows can then never succeed.
    if cmd.type in _VARIABLE_NAME_TARGETS:
        target = cmd.target
    text = resolve_variables(cmd.text) if isinstance(getattr(cmd, "text", None), str) else getattr(cmd, "text", None)
    attribute = resolve_variables(cmd.attribute) if isinstance(getattr(cmd, "attribute", None), str) else getattr(cmd, "attribute", None)
    first_value = (cmd.values or [None])[0] if hasattr(cmd, "values") else None
    first_value = resolve_variables(first_value) if isinstance(first_value, str) else first_value

    dispatch = {
        # ── Navigation ───────────────────────────────────────────────────────
        "open":                      lambda: svc.open_site(ep, target),
        "refresh":                   lambda: svc.refresh_page(ep),
        "browser_permission":        lambda: svc.set_browser_permission(ep, target, text),
        # ── Text matching: page and element ──────────────────────────────────
        "verify_page_contains":      lambda: svc.verify_page_contains(ep, text),
        "verify_page_not_contains":  lambda: svc.verify_page_not_contains(ep, text),
        "verify_page_starts":        lambda: svc.verify_page_starts(ep, text),
        "verify_page_ends":          lambda: svc.verify_page_ends(ep, text),
        "verify_page_matches":       lambda: svc.verify_page_matches(ep, text),
        "verify_title_contains":     lambda: svc.verify_title_contains(ep, text),
        "verify_title_exact":        lambda: svc.verify_title_exact(ep, text),
        "verify_element_starts":     lambda: svc.verify_element_starts(ep, target, text),
        "verify_element_ends":       lambda: svc.verify_element_ends(ep, target, text),
        "verify_element_matches":    lambda: svc.verify_element_matches(ep, target, text),
        "verify_element_not_contains": lambda: svc.verify_element_not_contains(ep, target, text),
        # ── Conditional actions, now on web as well as Appium ────────────────
        "click_if_visible":          lambda: svc.click_if_visible(ep, target, float(cmd.wait or 0)),
        "tap_if_visible":            lambda: svc.click_if_visible(ep, target, float(cmd.wait or 0)),
        "fill_if_visible":           lambda: svc.fill_if_visible(ep, target, text, float(cmd.wait or 0)),
        "verify_if_visible":         lambda: svc.verify_if_visible(ep, target, float(cmd.wait or 0)),
        "click_if_exists":           lambda: svc.click_if_exists(ep, target),
        "tap_if_exists":             lambda: svc.click_if_exists(ep, target),
        # ── Browser alerts, cookies, upload, windows, frames (Testsigma parity)
        "accept_alert":              lambda: svc.accept_alert(ep),
        "dismiss_alert":             lambda: svc.dismiss_alert(ep),
        "type_into_alert":           lambda: svc.type_into_alert(ep, text),
        "verify_alert_present":      lambda: svc.verify_alert_present(ep),
        "verify_alert_text":         lambda: svc.verify_alert_text(ep, text),
        "delete_all_cookies":        lambda: svc.delete_all_cookies(ep),
        "delete_cookie":             lambda: svc.delete_cookie(ep, text),
        "verify_cookie":             lambda: svc.verify_cookie(ep, text),
        "upload_file":               lambda: svc.upload_file(ep, target, text),
        "switch_window_title":       lambda: svc.switch_window_title(ep, text),
        "parent_frame":              lambda: svc.parent_frame(ep),
        "press_back":                lambda: ep.go_back(),
        "go_forward":                lambda: ep.go_forward(),
        # ── Interaction ──────────────────────────────────────────────────────
        "search":                    lambda: svc.search(ep, text),
        "click":                     lambda: svc.click_element(ep, target),
        "fill":                      lambda: svc.fill_element(ep, text, target),
        # ── Wait / Timing ─────────────────────────────────────────────────────
        "wait":                      lambda: svc.wait_seconds(ep, cmd.wait),
        "wait_for_result_page_load": lambda: svc.wait_for_result_page_load(ep),
        # ── Scroll ────────────────────────────────────────────────────────────
        "scroll_to":                 lambda: svc.scroll_to_element(ep, target),
        "scroll":                    lambda: svc.vertical_scroll(ep, cmd.count or 500),
        "scroll_until_text_visible": lambda: svc.scroll_until_text_visible(ep, text, cmd.count, cmd.wait),
        # ── Screenshot ───────────────────────────────────────────────────────
        "screenshot":                lambda: svc.take_screenshot(ep, target or "capture"),
        # ── Verification — Global ────────────────────────────────────────────
        "verify_text":               lambda: svc.verify_global_exact_text(ep, text, exact_match=False),
        "verify_exact_text":         lambda: svc.verify_global_exact_text(ep, text, exact_match=True),
        "verify_multiple_texts":     lambda: svc.verify_multiple_global_texts(ep, text),
        # ── Verification — Element ───────────────────────────────────────────
        "verify_element_exact":      lambda: svc.verify_element_exact_text(ep, target, text),
        "verify_element_contains":   lambda: svc.verify_element_contains_text(ep, target, text),
        # ── Verification — Variables ─────────────────────────────────────────
        "verify_var_contains":       lambda: svc.verify_stored_variable_contains(target, text),
        "verify_var_not_equals":     lambda: svc.verify_stored_variable_not_equals(target, text),
        # ── Visibility / condition-based wait / multi-input OTP ───────────────
        "verify_element_visible":    lambda: svc.verify_element_visible(ep, target),
        "verify_element_not_exists": lambda: svc.verify_element_not_exists(ep, target),
        "verify_element_not_visible": lambda: svc.verify_element_not_visible(ep, target),
        "wait_until_visible":        lambda: svc.wait_until_element_visible(ep, target),
        "wait_until_text_not":       lambda: svc.wait_until_element_text_not(ep, target, text),
        "enter_otp":                 lambda: svc.enter_otp(ep, text, target),
        "fetch_otp":                 lambda: svc.fetch_otp_from_portal(
                                         ep, text, cmd.variable_name, after=target),
        # ── Extract — Page info ──────────────────────────────────────────────
        "extract_url":               lambda: svc.extract_page_url(ep, cmd.variable_name),
        "extract_title":             lambda: svc.extract_page_title(ep, cmd.variable_name),
        # ── Extract — Element data ───────────────────────────────────────────
        "extract_text":              lambda: svc.extract_element_text(ep, target, cmd.variable_name),
        "store_text":                lambda: svc.extract_element_text(ep, target, cmd.variable_name),
        "extract_attribute":         lambda: svc.extract_element_attribute(ep, target, attribute, cmd.variable_name),
        "extract_input":             lambda: svc.extract_input_value(ep, target, cmd.variable_name),
        "extract_count":             lambda: svc.extract_element_count(ep, target, cmd.variable_name),
        # ── Variables / Data ─────────────────────────────────────────────────
        "create_variable":           lambda: svc.create_custom_variable(text, target),
        "math":                      lambda: svc.execute_math(target, text, first_value, cmd.variable_name),
        # ── Fake data generation (faker) ─────────────────────────────────────
        "generate_fake":             lambda: svc.generate_fake_data(text, cmd.variable_name),
        "random_number":             lambda: svc.generate_random_number(target, text, cmd.variable_name),
        "random_string":             lambda: svc.generate_random_string(cmd.count or 8, cmd.variable_name),
        # ── Date / Time ───────────────────────────────────────────────────────
        "get_date":                  lambda: svc.get_date_value(text, cmd.variable_name),
        "format_date":               lambda: svc.format_date_value(text, first_value, cmd.variable_name),
        # ── HTTP / API ────────────────────────────────────────────────────────
        "api_get":                   lambda: svc.api_get(text, cmd.variable_name),
        "api_post":                  lambda: svc.api_post(text, target, cmd.variable_name),
        "extract_json":              lambda: svc.extract_json_path(target, text, cmd.variable_name),
        # ── Excel / CSV ───────────────────────────────────────────────────────
        "read_excel_cell":           lambda: svc.read_excel_cell(text, int(target), first_value, cmd.variable_name),
        "read_excel_row":            lambda: svc.read_excel_row(text, int(target), cmd.variable_name),
        "read_csv_cell":             lambda: svc.read_csv_cell(text, int(target), first_value, cmd.variable_name),
        # ── JavaScript Actions ──────────────────────────────────────────────────
        "js_click":                  lambda: svc.js_click(ep, target),
        "js_scroll_to":              lambda: svc.js_scroll_to(ep, target),
        "js_scroll":                 lambda: svc.js_scroll(ep, text, cmd.count or 300),
        "js_type":                   lambda: svc.js_type(ep, text, target),
        "js_set_value":              lambda: svc.js_set_value(ep, text, target),
        "js_focus":                  lambda: svc.js_focus(ep, target),
        "js_submit":                 lambda: svc.js_submit(ep, target),
        "js_dispatch":               lambda: svc.js_dispatch_event(ep, text, target),
        # ── Image ─────────────────────────────────────────────────────────────
        "verify_image":              lambda: None,  # handled in _interpret below
        # ── Tabs / Windows ───────────────────────────────────────────────────
        "switch_tab":                lambda: svc.switch_tab(page, cmd.count,
                                                              where=cmd.text or ""),
        "close_tab":                 lambda: svc.close_tab(page, int(cmd.count) if cmd.count is not None else None),
        "close_all_tabs":            lambda: svc.close_all_tabs(page),
        "open_new_tab":              lambda: svc.open_new_tab(page),
        "open_in_new_tab":           lambda: svc.open_in_new_tab(page, target),
        "list_tabs":                 lambda: svc.list_tabs(page),
        # ── Iframes ──────────────────────────────────────────────────────────
        "switch_iframe":             lambda: svc.switch_iframe(page, target),
        "exit_iframe":               lambda: svc.exit_iframe(),
    }

    handler = dispatch.get(cmd.type)
    if not handler:
        raise ValueError(f"❌ Unknown command type: {cmd.type}")
    handler()


def _expand_reusable(name: str, page) -> None:
    """Inline-expand a named reusable step group, with circular-call protection."""
    from core.reusable_steps import get as _rs_get
    name_lower = name.lower()
    call_stack = _get_call_stack()
    if name_lower in call_stack:
        raise RuntimeError(
            f"Circular call detected: '{name}' is already in the active call stack "
            f"({' → '.join(sorted(call_stack))} → {name})"
        )
    try:
        steps = _rs_get(name)
    except KeyError as e:
        raise ValueError(str(e)) from e

    call_stack.add(name_lower)
    try:
        logger.info("▶ Expanding reusable '%s' (%d steps)", name, len(steps))
        for sub_step in steps:
            _interpret(sub_step, page)
    finally:
        call_stack.discard(name_lower)


def _interpret(step: str, page):
    """Pre-processes variables, then parses and executes one NLP step."""
    normalized = step.strip()
    logger.info("👉 Interpreting: %s", normalized)

    # Apply active environment domain replacement to URL steps
    try:
        from config.environment_manager import apply_to_flow_steps
        normalized = apply_to_flow_steps([normalized])[0]
    except Exception:
        pass

    # Variable injection: ${my_var} → value (shared RUNTIME_VARIABLES from action_service)
    try:
        resolved = resolve_variables(normalized)
    except ValueError as e:
        raise ValueError(str(e)) from e

    try:
        cmd = parse_step(resolved)
    except ValueError as e:
        raise ValueError(f"❌ Invalid syntax or unknown command: {resolved}") from e

    if cmd.type == "call_reusable":
        _expand_reusable(cmd.target, page)
        return

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
            if _get_stop_on_failure():
                logger.critical("🛑 STOP_ON_FAILURE enabled. Halting.")
                break

    return stats


def run_nlp_flow(file_path: str):
    """
    Main NLP .flow runner (CLI entry point).
    Reads .flow file line-by-line, interprets each step, prints summary.
    """
    _setup_logging()
    sanitize_database()
    run_cfg = _load_run_config()
    _set_stop_on_failure(bool(run_cfg.get("stop_on_failure", False)))

    session = TestSession()
    preloaded = dict(RUNTIME_VARIABLES.items())
    if preloaded:
        session.runtime_variables.update(preloaded)
    bind_runtime_variables(session.runtime_variables)
    execution.action_service.set_test_session(session)
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
    finally:
        execution.action_service.set_test_session(None)

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
    sanitize_database()
    run_cfg = _load_run_config()
    _set_stop_on_failure(bool(run_cfg.get("stop_on_failure", False)))

    caps = capabilities or {}
    session = TestSession()
    preloaded = dict(RUNTIME_VARIABLES.items())
    if preloaded:
        session.runtime_variables.update(preloaded)
    bind_runtime_variables(session.runtime_variables)
    execution.action_service.set_test_session(session)
    page = None   # guard: open_browser may raise; close_browser handles page=None safely
    stats: dict = {"passed": 0, "failed": 0, "log": []}

    try:
        should_record_video = settings.ENABLE_VIDEO_RECORDING and bool(caps.get("record_video", False))
        # Forward the suite's desired_capabilities so mobile-web emulation (device_name /
        # mobile_web) reaches browser context creation. Desktop suites supply neither and
        # are unaffected.
        page = open_browser(session, record_video=should_record_video, capabilities=caps)
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
        finally:
            execution.action_service.set_test_session(None)
        try:
            sync_locators_to_snippets()
        except Exception as e:
            logger.warning("⚠️ Snippet sync failed: %s", e)

    return stats


# ── JSON / codeless flow ────────────────────────────────────────────────────────
def _accepts_save_target(func) -> bool:
    """True if a registered codeless action declares a `save_to_variable_name` parameter."""
    import inspect
    try:
        return "save_to_variable_name" in inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False


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
    preloaded = dict(RUNTIME_VARIABLES.items())
    if preloaded:
        session.runtime_variables.update(preloaded)
    bind_runtime_variables(session.runtime_variables)
    execution.action_service.set_test_session(session)
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
            save_target = resolved_params.get("save_to_variable_name")

            # Most storing actions take save_to_variable_name themselves and write the
            # variable internally; the rest return a value for us to save. Only strip the
            # key when the action cannot accept it, otherwise the call loses a required
            # argument and raises TypeError.
            if save_target is not None and not _accepts_save_target(target_function):
                resolved_params.pop("save_to_variable_name", None)

            step_result = target_function(page=page, **resolved_params)

            if save_target and step_result is not None:
                runtime_memory.save(save_target, step_result)

        logger.info("✅ Test Flow Executed Successfully!")

    except Exception as e:
        logger.error("❌ Test Failed at Step %d [%s]: %s", index + 1, action_name, e)

    finally:
        close_browser(page, test_name="codeless_run", session=session)
        execution.action_service.set_test_session(None)


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
