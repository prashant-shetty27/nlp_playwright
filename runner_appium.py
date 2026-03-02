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
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from nlp.parser import parse_step
from nlp.variable_manager import RUNTIME_VARIABLES, resolve_variables
from config import settings

logger = logging.getLogger(__name__)

_STOP_ON_FAILURE: bool = False

# ── Timeout configuration (seconds) ──────────────────────────────────────────
SESSION_START_TIMEOUT   = 1200  # 20 min — max time for WDA build + IPA install + session start (iOS WDA build 8-15 min, large IPA push up to 15 min)
STEP_TIMEOUT            = 30    # 30 s   — max time for a single step; broken steps should fail fast
IDLE_WATCHDOG_TIMEOUT   = 60    # 60 s   — abort if no step completes for this long
DEVICE_KEEPALIVE_INTERVAL = 25  # 25 s   — send keepalive this often to prevent device screen auto-lock
DEVICE_LOCK_RETRY_TIMEOUT = 60  # 60 s   — retry session start if device is locked (user needs to unlock)

# ── Activity watchdog state ──────────────────────────────────────────────────
_last_activity: float = 0.0
_last_keepalive: float = 0.0
_watchdog_active: bool = False
_watchdog_driver = None            # reference so watchdog can kill session
_watchdog_lock = threading.Lock()


def _touch_activity():
    """Mark that something just happened (step started/completed)."""
    global _last_activity
    _last_activity = time.time()


def _idle_watchdog():
    """
    Background thread: checks every 15 s if the runner has gone idle.
    - Sends a keepalive every DEVICE_KEEPALIVE_INTERVAL seconds to prevent device screen auto-lock.
    - If no activity for IDLE_WATCHDOG_TIMEOUT seconds, force-quits the driver.
    """
    global _watchdog_active, _last_keepalive
    _last_keepalive = time.time()

    while _watchdog_active:
        time.sleep(15)
        if not _watchdog_active:
            break

        now = time.time()

        # ── Device keepalive — prevent screen auto-lock ───────────────────
        if now - _last_keepalive >= DEVICE_KEEPALIVE_INTERVAL:
            with _watchdog_lock:
                drv = _watchdog_driver
            if drv:
                try:
                    drv.get_window_size()
                    _last_keepalive = now
                    logger.debug("💡 Device keepalive sent")
                except Exception:
                    pass  # driver may be mid-command; ignore errors

        # ── Idle timeout check ────────────────────────────────────────────
        elapsed = now - _last_activity
        if elapsed > IDLE_WATCHDOG_TIMEOUT:
            logger.error(
                "⏰ IDLE WATCHDOG: No activity for %.0f s (limit %d s) — aborting session",
                elapsed, IDLE_WATCHDOG_TIMEOUT,
            )
            with _watchdog_lock:
                if _watchdog_driver:
                    try:
                        _watchdog_driver.quit()
                    except Exception:
                        pass
            _watchdog_active = False
            break


def _start_watchdog(driver):
    """Activate the idle watchdog with a reference to the driver."""
    global _watchdog_active, _watchdog_driver
    _touch_activity()
    with _watchdog_lock:
        _watchdog_driver = driver
    _watchdog_active = True
    t = threading.Thread(target=_idle_watchdog, daemon=True, name="idle-watchdog")
    t.start()


def _stop_watchdog():
    """Deactivate the idle watchdog."""
    global _watchdog_active, _watchdog_driver
    _watchdog_active = False
    with _watchdog_lock:
        _watchdog_driver = None


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _adb_cmd(device_id: str | None, args: list[str]) -> list[str]:
    cmd = ["adb"]
    if device_id:
        cmd += ["-s", device_id]
    cmd += args
    return cmd


def _run_adb(
    device_id: str | None,
    args: list[str],
    check: bool = False,
    timeout_seconds: int = 25,
) -> subprocess.CompletedProcess:
    cmd = _adb_cmd(device_id, args)
    try:
        return subprocess.run(
            cmd,
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.warning("⏱️  adb command timed out after %ss: %s", timeout_seconds, " ".join(cmd))
        return subprocess.CompletedProcess(cmd, 124, stdout="", stderr="timeout")


def _prepare_android_app(caps: dict) -> dict:
    """Apply Android pre-session app lifecycle policy.

    Supported control flags (suite/env capabilities):
      - app_install (bool): fresh install path (uninstall old, install from APK)
      - app_update / new_apk_shared (bool): upgrade existing install with APK while preserving data
      - clear_cache (bool): clear app cache only (best effort)
      - clear_storage (bool): clear app data+cache via pm clear
      - reset_device_permission (bool): reset app ops/permissions (best effort)
      - existing_app_present (bool): hints whether app is expected to already exist
    """
    prepared = dict(caps or {})

    # Control flags (support plain and appium-prefixed keys)
    app_install = _as_bool(
        prepared.pop("app_install", prepared.pop("appium:appInstall", settings.ANDROID_APP_INSTALL_DEFAULT)),
        default=settings.ANDROID_APP_INSTALL_DEFAULT,
    )
    app_update = _as_bool(
        prepared.pop("app_update", prepared.pop("appium:appUpdate",
                     prepared.pop("new_apk_shared", prepared.pop("appium:newApkShared", settings.ANDROID_NEW_APK_SHARED_DEFAULT)))),
        default=settings.ANDROID_APP_UPDATE_DEFAULT,
    )
    clear_cache = _as_bool(
        prepared.pop("clear_cache", prepared.pop("appium:clearCache", settings.ANDROID_CLEAR_CACHE_DEFAULT)),
        default=settings.ANDROID_CLEAR_CACHE_DEFAULT,
    )
    clear_storage = _as_bool(
        prepared.pop("clear_storage", prepared.pop("appium:clearStorage", settings.ANDROID_CLEAR_STORAGE_DEFAULT)),
        default=settings.ANDROID_CLEAR_STORAGE_DEFAULT,
    )
    reset_device_permission = _as_bool(
        prepared.pop("reset_device_permission", prepared.pop("appium:resetDevicePermission", settings.ANDROID_RESET_DEVICE_PERMISSION_DEFAULT)),
        default=settings.ANDROID_RESET_DEVICE_PERMISSION_DEFAULT,
    )
    existing_app_present = _as_bool(
        prepared.pop("existing_app_present", prepared.pop("appium:existingAppPresent", settings.ANDROID_EXISTING_APP_PRESENT_DEFAULT)),
        default=settings.ANDROID_EXISTING_APP_PRESENT_DEFAULT,
    )

    device_id = (
        prepared.get("appium:udid")
        or prepared.get("udid")
        or prepared.get("appium:deviceName")
        or prepared.get("deviceName")
    )
    app_package = prepared.get("appium:appPackage") or prepared.get("appPackage")
    app_path = prepared.get("appium:app") or prepared.get("app")

    if app_package:
        # Always force-stop before run (required by user).
        _run_adb(device_id, ["shell", "am", "force-stop", app_package], check=False, timeout_seconds=10)
        logger.info("🛑 Force-stopped app before run: %s", app_package)

        if reset_device_permission:
            # Best effort: reset app ops to default, then global permission reset command.
            _run_adb(device_id, ["shell", "cmd", "appops", "reset", app_package], check=False, timeout_seconds=12)
            _run_adb(device_id, ["shell", "pm", "reset-permissions"], check=False, timeout_seconds=15)
            logger.info("🔐 Reset app/device permission state (best effort): %s", app_package)

        if clear_cache:
            # Best effort: Android command support varies by device/OS.
            cache_res = _run_adb(
                device_id,
                ["shell", "pm", "clear", "--cache-only", app_package],
                check=False,
                timeout_seconds=8,
            )
            out = ((cache_res.stdout or "") + (cache_res.stderr or "")).strip().lower()
            if cache_res.returncode == 0 and ("success" in out or not out):
                logger.info("🧹 Cleared app cache: %s", app_package)
            else:
                logger.warning("⚠️  clear_cache requested but cache-only clear may be unsupported on this device")

        if clear_storage:
            _run_adb(device_id, ["shell", "pm", "clear", app_package], check=False, timeout_seconds=20)
            logger.info("🧽 Cleared app storage/data: %s", app_package)

    if app_install:
        logger.info("♻️  app_install=true → fresh install mode enabled")

        if app_package:
            uninstall = _run_adb(device_id, ["uninstall", app_package], check=False, timeout_seconds=120)
            uninstall_out = (uninstall.stdout or uninstall.stderr or "").strip()
            if uninstall_out:
                logger.info("🗑️  Uninstall result: %s", uninstall_out)

        if not app_path:
            logger.warning(
                "⚠️  app_install=true but no app binary path set ('appium:app'). "
                "Fresh install requires appium:app=/absolute/path/to/app.apk"
            )

        # Fresh install semantics.
        prepared["appium:noReset"] = False
        prepared["appium:fullReset"] = False

    elif app_update:
        logger.info("⬆️  app_update=true → in-place upgrade mode enabled")
        if not existing_app_present:
            logger.warning("⚠️  existing_app_present=false with app_update=true — update expects an installed base app")

        if app_path:
            update_res = _run_adb(device_id, ["install", "-r", app_path], check=False, timeout_seconds=180)
            update_out = ((update_res.stdout or "") + (update_res.stderr or "")).strip()
            if update_out:
                logger.info("📦 Update install result: %s", update_out)

            # Keep user state like Play Store update. Avoid duplicate reinstall in Appium session.
            prepared["appium:noReset"] = True
            prepared.pop("appium:app", None)
            prepared.pop("app", None)
        else:
            logger.warning("⚠️  app_update=true but no APK path set in 'appium:app'")

    else:
        # Existing install mode: keep user state and avoid unnecessary reinstall if app path exists.
        if existing_app_present:
            prepared.setdefault("appium:noReset", True)
            prepared.pop("appium:app", None)
            prepared.pop("app", None)

    return prepared

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

    # Copy to avoid mutating shared config objects.
    caps = dict(caps)

    # ── Auto-set ANDROID_HOME if missing (prefer full SDK with build-tools) ─
    if platform == "android" and not os.environ.get("ANDROID_HOME"):
        import glob
        preferred_roots = [
            "/usr/local/share/android-commandlinetools",  # brew android-commandlinetools cask
            os.path.expanduser("~/Library/Android/sdk"),   # Android Studio default
        ]
        sdk_root = ""
        for root in preferred_roots:
            if os.path.exists(os.path.join(root, "build-tools")):
                sdk_root = root
                break

        if not sdk_root:
            candidates = glob.glob("/usr/local/Caskroom/android-platform-tools/*/platform-tools/adb")
            if candidates:
                sdk_root = os.path.dirname(os.path.dirname(sorted(candidates)[-1]))

        if sdk_root:
            os.environ["ANDROID_HOME"] = sdk_root
            os.environ["ANDROID_SDK_ROOT"] = sdk_root
            logger.info("✅ ANDROID_HOME auto-set: %s", sdk_root)

    # Android app lifecycle policy (app_install + force-stop).
    if platform == "android":
        caps = _prepare_android_app(caps)

    server_url = settings.APPIUM_SERVER_URL
    logger.info("🔗 Connecting to Appium at %s [platform=%s]", server_url, platform.upper())

    # ── Build options using platform-specific Options class (Appium 5.x) ───
    if platform == "android":
        from appium.options.android.uiautomator2.base import UiAutomator2Options
        options = UiAutomator2Options()
    else:
        from appium.options.ios.xcuitest.base import XCUITestOptions
        options = XCUITestOptions()

    for key, val in caps.items():
        if val == "" or val is None or key.startswith("_comment"):
            continue                       # skip empty / comment keys
        clean = key.replace("appium:", "")
        # Try the class property setter (for well-known Options properties).
        # setattr on an unknown attribute silently succeeds but the value is
        # NOT serialised into the W3C payload — so we verify using set_capability
        # as a reliable fallback for any key not defined on the Options class.
        prop = getattr(type(options), clean, None)
        if prop is not None and isinstance(prop, property) and prop.fset is not None:
            try:
                setattr(options, clean, val)
                continue
            except Exception:
                pass
        # Either not a defined property or setter failed — use set_capability
        options.set_capability(key, val)

    # ── Session creation with timeout + retry on device lock ─────────────
    logger.info("⏳ Creating session (timeout %d s)…", SESSION_START_TIMEOUT)
    _touch_activity()

    def _create_driver():
        return appium_webdriver.Remote(server_url, options=options)

    _lock_deadline = time.time() + DEVICE_LOCK_RETRY_TIMEOUT
    driver = None
    while True:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_create_driver)
            try:
                driver = future.result(timeout=SESSION_START_TIMEOUT)
                break  # session created successfully
            except FuturesTimeout:
                future.cancel()
                raise TimeoutError(
                    f"⏰ SESSION TIMEOUT: Appium session did not start within "
                    f"{SESSION_START_TIMEOUT}s — WDA build or device connection may be stuck"
                )
            except Exception as exc:
                err_str = str(exc)
                is_locked = (
                    "could not be, unlocked" in err_str
                    or "device was not" in err_str
                    or "Locked" in err_str
                )
                if is_locked and time.time() < _lock_deadline:
                    remaining = max(0, int(_lock_deadline - time.time()))
                    logger.warning(
                        "🔒 Device locked — please unlock your device. "
                        "Retrying in 10s (up to %ds remaining)…",
                        remaining,
                    )
                    time.sleep(10)
                    # loop continues — rebuild the executor and retry
                else:
                    raise

    logger.info("🚀 Appium session started — id: %s", driver.session_id)
    _touch_activity()

    # Ensure target app is foregrounded immediately.
    try:
        import execution.appium_action_service as svc
        svc.launch_app(driver, fallback_caps=caps)
    except Exception as e:
        logger.warning("⚠️  Auto launch_app after session start failed: %s", e)

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
        "tap_text":                  lambda: svc.tap_by_text(driver, text),
        "dismiss_alerts":            lambda: svc.dismiss_alerts(driver),
        "click_if_exists":           lambda: _tap_if_exists(svc, driver, target, platform),
        "tap_if_exists":             lambda: _tap_if_exists(svc, driver, target, platform),
        "fill":                      lambda: svc.fill_element(driver, target, text, platform),
        "type":                      lambda: svc.fill_element(driver, target, text, platform),
        "type_text":                 lambda: svc.type_into_active(driver, text),
        "fill_if_exists":            lambda: _fill_if_exists(svc, driver, target, text, platform),
        "type_if_exists":            lambda: _fill_if_exists(svc, driver, target, text, platform),
        "clear":                     lambda: svc.clear_element(driver, target, platform),
        "double_tap":                lambda: svc.double_tap(driver, target, platform),
        "long_press":                lambda: svc.long_press(driver, target, platform),
        "wait_for_element":          lambda: svc.wait_for_element(driver, target, platform),
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


def _tap_if_exists(svc, driver, target: str, platform: str):
    """Tap element if present; continue if not found."""
    try:
        svc.tap_element(driver, target, platform)
    except Exception:
        logger.info("ℹ️  Optional element not found, skipping tap: '%s'", target)


def _fill_if_exists(svc, driver, target: str, text: str, platform: str):
    """Fill element if present; continue if not found."""
    try:
        svc.fill_element(driver, target, text, platform)
    except Exception:
        logger.info("ℹ️  Optional element not found, skipping fill: '%s'", target)


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
    """Resolve variables in a step then parse and execute it (with timeout)."""
    step = step.strip()
    if not step or step.startswith("#"):
        return

    logger.info("👉 Interpreting: %s", step)
    _touch_activity()

    try:
        resolved = resolve_variables(step)
    except ValueError as e:
        raise ValueError(str(e)) from e

    cmd = parse_step(resolved)
    if cmd is None:
        logger.warning("⚠️  Could not parse step: '%s'", step)
        return

    # ── Per-step timeout ──────────────────────────────────────────────────
    # "wait" commands get extra time equal to the wait value itself.
    step_limit = STEP_TIMEOUT
    if cmd.type == "wait" and cmd.wait:
        step_limit += float(cmd.wait)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_execute_step, cmd, driver, platform)
        try:
            future.result(timeout=step_limit)
        except FuturesTimeout:
            logger.error(
                "⏰ STEP TIMEOUT: '%s' did not complete within %d s — skipping",
                step, step_limit,
            )
            raise TimeoutError(f"Step timed out after {step_limit}s: {step}")

    _touch_activity()


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

def run_appium_suite_collect(
    flow_files: list,
    capabilities: dict | None = None,
    platform: str = "ios",
    stop_on_first_failure: bool = False,
) -> list:
    """
    Run multiple .flow files in ONE shared Appium session.

    The app is installed exactly once (at session start). All flows run
    consecutively in the same session — no reinstall between flows.

    Returns a list of per-flow dicts:
        [{"file": str, "passed": int, "failed": int, "log": list[str]}, ...]
    """
    _setup_logging()
    global _STOP_ON_FAILURE
    _STOP_ON_FAILURE = stop_on_first_failure

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
    all_results: list = []

    try:
        driver = _start_session(capabilities, platform)
        _start_watchdog(driver)
        logger.info(
            "⏱  Timeouts: session=%ds  step=%ds  idle=%ds",
            SESSION_START_TIMEOUT, STEP_TIMEOUT, IDLE_WATCHDOG_TIMEOUT,
        )

        for file_path in flow_files:
            if not os.path.exists(file_path):
                logger.error("❌ Flow not found: %s", file_path)
                all_results.append({
                    "file": file_path, "passed": 0, "failed": 1,
                    "duration_s": 0.0, "log": [f"❌ File not found: {file_path}"],
                })
                if stop_on_first_failure:
                    break
                continue

            logger.info("🚀 Starting flow [%s]: %s", platform.upper(), file_path)
            t0 = time.time()
            stats = _run_flow_core(file_path, driver, platform)
            duration = time.time() - t0
            all_results.append({"file": file_path, "duration_s": duration, **stats})

            status = "✅ PASSED" if stats["failed"] == 0 else "❌ FAILED"
            logger.info("  %s: %s  (%.1fs)", status, file_path, duration)

            if stop_on_first_failure and stats["failed"] > 0:
                logger.warning("🛑 stop_on_first_failure — aborting suite after: %s", file_path)
                break

    except Exception as e:
        logger.error("❌ Suite session error: %s", e)
        ran = {r["file"] for r in all_results}
        for f in flow_files:
            if f not in ran:
                all_results.append({
                    "file": f, "passed": 0, "failed": 1,
                    "log": [f"❌ Session lost before this flow ran: {e}"],
                })
    finally:
        _stop_watchdog()
        _end_session(driver, "suite")

    return all_results


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
        _start_watchdog(driver)            # ← activate idle watchdog
        logger.info("🚀 Starting flow [%s]: %s", platform.upper(), file_path)
        logger.info(
            "⏱  Timeouts: session=%ds  step=%ds  idle=%ds",
            SESSION_START_TIMEOUT, STEP_TIMEOUT, IDLE_WATCHDOG_TIMEOUT,
        )
        stats = _run_flow_core(file_path, driver, platform)
    except FileNotFoundError as e:
        logger.error("❌ %s", e)
        stats["failed"] += 1
        stats["log"].append(f"❌ {e}")
    except TimeoutError as e:
        logger.error("⏰ %s", e)
        stats["failed"] += 1
        stats["log"].append(f"⏰ {e}")
    except Exception as e:
        logger.error("❌ Appium flow error: %s", e)
        stats["failed"] += 1
        stats["log"].append(f"❌ {e}")
    finally:
        _stop_watchdog()                   # ← deactivate watchdog
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
