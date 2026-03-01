"""
runner_appium.py  —  NLP .flow runner for Android & iOS via Appium.

Parses the same .flow file format used by the web runner and routes each
NLP command to execution/appium_action_service.py instead of Playwright.

Usage (direct):
    python runner_appium.py flows/android_demo.flow --platform android

Used by plan_runner.py automatically when suite platform = android|ios.
"""

import os
import sys
import json
import logging
import time

from nlp.parser import parse_step
from nlp.variable_manager import RUNTIME_VARIABLES, resolve_variables
from config import settings

logger = logging.getLogger(__name__)

_STOP_ON_FAILURE: bool = False

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# APPIUM SESSION LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────

def _start_session(capabilities: dict, platform: str):
    """Start an Appium WebDriver session. Returns the driver."""
    try:
        from appium import webdriver as appium_webdriver
        from appium.options.common.base import AppiumOptions
    except ImportError:
        raise ImportError(
            "Appium Python client not installed. Run: pip install Appium-Python-Client"
        )

    caps = capabilities or (
        settings.ANDROID_CAPABILITIES if platform == "android"
        else settings.IOS_CAPABILITIES
    )
    if not caps:
        raise ValueError(
            f"No Appium capabilities configured for platform '{platform}'.\n"
            f"  Set ANDROID_CAPABILITIES or IOS_CAPABILITIES in .env"
        )

    server_url = settings.APPIUM_SERVER_URL
    logger.info("🔗 Connecting to Appium at %s [platform=%s]", server_url, platform.upper())

    options = AppiumOptions().load_capabilities(caps)
    driver  = appium_webdriver.Remote(server_url, options=options)
    logger.info("🚀 Appium session started — id: %s", driver.session_id)
    return driver


def _end_session(driver, label: str = "session"):
    """Safely quit the Appium driver."""
    try:
        if driver:
            driver.quit()
            logger.info("🔌 Appium session closed [%s]", label)
    except Exception as e:
        logger.warning("⚠️  Session close error: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND DISPATCH
# ─────────────────────────────────────────────────────────────────────────────

def _execute_step(cmd, driver, platform: str):
    """Route a parsed Command to the correct appium_action_service function."""
    import execution.appium_action_service as svc

    # Resolve any ${variables} in text/target fields
    target = resolve_variables(cmd.target or "")
    text   = resolve_variables(cmd.text   or "")

    dispatch = {
        # ── App lifecycle ─────────────────────────────────────────────────
        "open":                      lambda: svc.open_url(driver, text or target),
        "launch_app":                lambda: svc.launch_app(driver),
        # ── Interaction ───────────────────────────────────────────────────
        "click":                     lambda: svc.tap_element(driver, target, platform),
        "tap":                       lambda: svc.tap_element(driver, target, platform),
        "fill":                      lambda: svc.fill_element(driver, target, text, platform),
        "type":                      lambda: svc.fill_element(driver, target, text, platform),
        "clear":                     lambda: svc.clear_element(driver, target, platform),
        "press_back":                lambda: svc.press_back(driver),
        "press_home":                lambda: svc.press_home(driver),
        "press_enter":               lambda: svc.press_enter(driver),
        "hide_keyboard":             lambda: svc.hide_keyboard(driver),
        # ── Scroll / Swipe ────────────────────────────────────────────────
        "scroll":                    lambda: svc.swipe_up(driver),
        "scroll_down":               lambda: svc.swipe_up(driver),
        "scroll_up":                 lambda: svc.swipe_down(driver),
        "swipe_left":                lambda: svc.swipe_left(driver),
        "swipe_right":               lambda: svc.swipe_right(driver),
        "scroll_until_text_visible": lambda: svc.scroll_until_text_visible(
                                         driver, text, int(cmd.count or 8), float(cmd.wait or 0.5)
                                     ),
        "scroll_until_element":      lambda: svc.scroll_until_element_visible(
                                         driver, target, platform, int(cmd.count or 8)
                                     ),
        # ── Wait / Timing ─────────────────────────────────────────────────
        "wait":                      lambda: svc.wait_seconds(driver, float(cmd.wait or 1)),
        # ── Screenshot ────────────────────────────────────────────────────
        "screenshot":                lambda: svc.take_screenshot(driver, target or "capture"),
        # ── Verification ──────────────────────────────────────────────────
        "verify_text":               lambda: svc.verify_text(driver, text),
        "verify_exact_text":         lambda: svc.verify_text(driver, text),
        "verify_multiple_texts":     lambda: svc.verify_texts(driver, cmd.values if hasattr(cmd, "values") and cmd.values else [text]),
        "verify_element_exists":     lambda: svc.verify_element_exists(driver, target, platform),
        "verify_element_not_exists": lambda: svc.verify_element_not_exists(driver, target, platform),
        "verify_var_contains":       lambda: _verify_var_contains(target, text),
        # ── Variable extraction ───────────────────────────────────────────
        "extract_text":              lambda: svc.store_element_text(driver, target, platform, cmd.variable_name),
        "store_text":                lambda: svc.store_element_text(driver, target, platform, cmd.variable_name),
        "extract_url":               lambda: _store_value(str(driver.current_url), cmd.variable_name),
        "extract_title":             lambda: _store_value(str(driver.title), cmd.variable_name),
        "create_variable":           lambda: svc.store_variable(text, target),
        "math":                      lambda: _execute_math(cmd),
    }

    handler = dispatch.get(cmd.type)
    if not handler:
        logger.warning("⚠️  Unsupported command for Appium: '%s' — skipping", cmd.type)
        return
    handler()


def _store_value(value: str, variable: str):
    RUNTIME_VARIABLES[variable] = value
    logger.info("💾 Stored '%s' → $%s", value, variable)


def _verify_var_contains(var_name: str, expected: str):
    actual = RUNTIME_VARIABLES.get(var_name, "")
    if expected not in actual:
        raise AssertionError(
            f"❌ Variable ${var_name} = '{actual}' does not contain '{expected}'"
        )
    logger.info("✅ Variable $%s contains '%s'", var_name, expected)


def _execute_math(cmd):
    """Simple math: store result of op(a, b)."""
    try:
        a   = float(RUNTIME_VARIABLES.get(cmd.target, cmd.target))
        b   = float(RUNTIME_VARIABLES.get(cmd.values[0], cmd.values[0]) if hasattr(cmd, "values") and cmd.values else 0)
        op  = (cmd.text or "+").strip()
        result = {"+": a + b, "-": a - b, "*": a * b, "/": a / b if b != 0 else 0}.get(op, a + b)
        RUNTIME_VARIABLES[cmd.variable_name] = str(int(result) if result == int(result) else result)
        logger.info("🔢 Math: %s %s %s = %s → $%s", a, op, b, RUNTIME_VARIABLES[cmd.variable_name], cmd.variable_name)
    except Exception as e:
        logger.error("❌ Math error: %s", e)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# STEP INTERPRETATION
# ─────────────────────────────────────────────────────────────────────────────

def _interpret_step(step: str, driver, platform: str) -> None:
    """Resolve variables in a step then parse and execute it."""
    step = step.strip()
    if not step or step.startswith("#"):
        return

    logger.info("👉 Interpreting: %s", step)

    try:
        resolved = resolve_variables(step)
    except ValueError as e:
        raise ValueError(str(e)) from e

    cmd = parse_step(resolved)
    if cmd is None:
        logger.warning("⚠️  Could not parse step: '%s'", step)
        return

    _execute_step(cmd, driver, platform)


def _load_flow_file(file_path: str) -> list[str]:
    """Load and return non-empty, non-comment lines from a .flow file."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Flow file not found: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


# ─────────────────────────────────────────────────────────────────────────────
# CORE EXECUTION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _run_flow_core(file_path: str, driver, platform: str) -> dict:
    """
    Execute all steps in a .flow file against an Appium driver.
    Returns: {"passed": int, "failed": int, "log": list[str]}
    """
    global _STOP_ON_FAILURE
    steps  = _load_flow_file(file_path)
    stats  = {"passed": 0, "failed": 0, "log": []}

    for step in steps:
        try:
            _interpret_step(step, driver, platform)
            stats["passed"] += 1
        except AssertionError as e:
            msg = str(e)
            logger.error("❌ Assertion failed: %s", msg)
            stats["failed"] += 1
            stats["log"].append(f"❌ {msg}")
            if _STOP_ON_FAILURE:
                logger.warning("🛑 stop_on_failure=true — halting flow")
                break
        except Exception as e:
            msg = str(e)
            logger.error("❌ Step error: %s", msg)
            stats["failed"] += 1
            stats["log"].append(f"❌ {msg}")
            if _STOP_ON_FAILURE:
                logger.warning("🛑 stop_on_failure=true — halting flow")
                break

    return stats


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API  (called by plan_runner.py)
# ─────────────────────────────────────────────────────────────────────────────

def run_appium_flow_collect(
    file_path: str,
    capabilities: dict | None = None,
    platform: str = "android",
) -> dict:
    """
    Run a .flow file on Android/iOS via Appium.
    Returns {"passed": int, "failed": int, "log": list[str]}.

    Args:
        file_path:    path to the .flow script
        capabilities: Appium desired capabilities dict (from suite JSON)
        platform:     "android" or "ios"
    """
    _setup_logging()
    global _STOP_ON_FAILURE

    # Load stop_on_failure from playwright config (shared config)
    try:
        import json as _json
        cfg_path = "config/playwright.config.json"
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                run_cfg = _json.load(f).get("run", {})
                _STOP_ON_FAILURE = bool(run_cfg.get("stop_on_failure", False))
    except Exception:
        pass

    driver = None
    stats: dict = {"passed": 0, "failed": 0, "log": []}
    label = os.path.basename(file_path).split(".")[0]

    try:
        driver = _start_session(capabilities, platform)
        logger.info("🚀 Starting flow [%s]: %s", platform.upper(), file_path)
        stats = _run_flow_core(file_path, driver, platform)
    except FileNotFoundError as e:
        logger.error("❌ %s", e)
        stats["failed"] += 1
        stats["log"].append(f"❌ {e}")
    except Exception as e:
        logger.error("❌ Appium flow error: %s", e)
        stats["failed"] += 1
        stats["log"].append(f"❌ {e}")
    finally:
        _end_session(driver, label)

    return stats


# ─────────────────────────────────────────────────────────────────────────────
# CLI  (direct usage)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NLP flow runner for Appium (Android/iOS)")
    parser.add_argument("flow",     help="Path to .flow file")
    parser.add_argument("--platform", "-p", default="android",
                        choices=["android", "ios"], help="Target platform (default: android)")
    parser.add_argument("--caps", "-c", default=None,
                        help="Path to JSON file with Appium capabilities")
    args = parser.parse_args()

    caps = None
    if args.caps:
        with open(args.caps) as f:
            caps = json.load(f)

    result = run_appium_flow_collect(args.flow, caps, args.platform)

    total  = result["passed"] + result["failed"]
    status = "✅ PASSED" if result["failed"] == 0 else "❌ FAILED"
    print(f"\n{status}  {result['passed']}/{total} steps passed")
    if result["log"]:
        print("\nFailures:")
        for msg in result["log"]:
            print(f"  {msg}")

    sys.exit(0 if result["failed"] == 0 else 1)
