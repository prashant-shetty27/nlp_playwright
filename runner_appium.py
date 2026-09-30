"""
runner_appium.py  —  NLP .flow runner for Android & iOS via Appium.

Parses the same .flow file format used by the web runner and routes each
NLP command to execution/appium_action_service.py instead of Playwright.

Usage (direct):
    python runner_appium.py flows/android_demo.flow --platform android

Used by plan_runner.py automatically when suite platform = android|ios.
"""

import os
import re
import sys
import json
import logging
import time
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from nlp.parser import parse_step
from nlp.variable_manager import RUNTIME_VARIABLES, bind_runtime_variables, resolve_variables
from execution.session import TestSession
from execution.ios_readiness import enforce_ios_readiness, enrich_ios_session_exception
from config import settings
from config.settings import _as_bool

logger = logging.getLogger(__name__)

# ── Per-thread execution state ────────────────────────────────────────────────
_thread_local = threading.local()


def _get_stop_on_failure() -> bool:
    return getattr(_thread_local, "stop_on_failure", False)


def _set_stop_on_failure(val: bool) -> None:
    _thread_local.stop_on_failure = val


def _get_call_stack() -> set:
    if not hasattr(_thread_local, "call_stack"):
        _thread_local.call_stack = set()
    return _thread_local.call_stack
def _bind_app_runtime_session() -> None:
    """
    Bind this thread's runtime variable proxy to a fresh TestSession store,
    preserving any variables pre-injected by callers (e.g. suite parameters).
    Each thread gets its own isolated TestSession via threading.local().
    """
    preloaded = dict(RUNTIME_VARIABLES.items())
    session = TestSession()
    _thread_local.session = session
    if preloaded:
        session.runtime_variables.update(preloaded)
    bind_runtime_variables(session.runtime_variables)

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
# iOS APP LIFECYCLE POLICY
# ─────────────────────────────────────────────────────────────────────────────

def _is_app_installed_on_simulator(udid: str, bundle_id: str) -> bool:
    """Return True if the app is already installed on the given simulator UDID."""
    if not udid or not bundle_id:
        return False
    try:
        result = subprocess.run(
            ["xcrun", "simctl", "listapps", udid],
            capture_output=True, text=True, timeout=15
        )
        return bundle_id in result.stdout
    except Exception as e:
        logger.debug("simctl listapps check failed: %s", e)
        return False


def _is_simulator_udid(udid: str) -> bool:
    """Best-effort simulator detection by checking whether simctl knows the UDID."""
    if not udid:
        return False
    try:
        result = subprocess.run(
            ["xcrun", "simctl", "list", "devices"],
            capture_output=True, text=True, timeout=15
        )
        return udid in (result.stdout or "")
    except Exception:
        return False


def _is_app_installed_on_real_device(udid: str, bundle_id: str) -> bool:
    """Return True if app is installed on a real iOS device via ios-deploy."""
    if not udid or not bundle_id:
        return False
    try:
        result = subprocess.run(
            ["ios-deploy", "--bundle_id", bundle_id, "--exists", "--id", udid],
            capture_output=True, text=True, timeout=20,
        )
        return result.returncode == 0
    except Exception as e:
        logger.debug("ios-deploy --exists check failed: %s", e)
        return False


def _install_ipa_via_ios_deploy(udid: str, ipa_path: str) -> bool:
    """Install an IPA on a real device using ios-deploy (more reliable than AFC)."""
    try:
        logger.info("📲 Installing IPA via ios-deploy: %s", ipa_path)
        result = subprocess.run(
            ["ios-deploy", "--bundle", ipa_path, "--id", udid],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            logger.info("✅ IPA installed successfully via ios-deploy")
            return True
        logger.warning("⚠️  ios-deploy install failed: %s", result.stderr or result.stdout)
        return False
    except Exception as e:
        logger.warning("⚠️  ios-deploy install exception: %s", e)
        return False


def _detect_ios_version(udid: str) -> str | None:
    """Return the iOS version of the connected device via xcrun xctrace."""
    try:
        result = subprocess.run(
            ["xcrun", "xctrace", "list", "devices"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            if udid in line:
                import re
                m = re.search(r'\((\d+\.\d+(?:\.\d+)?)\)', line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return None


def _prepare_ios_app(caps: dict) -> dict:
    """
    iOS pre-session policy:
    - If the app is already installed on the simulator, remove appium:app
      so Appium launches it directly without reinstalling.
    - On a real device, keep appium:app as-is (always install IPA).
    - If autoAcceptAlerts is explicitly false and autoDismissAlerts is unset,
      enable autoDismissAlerts to reliably close permission popups.
    - Auto-detects platformVersion from connected device so suite files
      never need manual version updates after iOS upgrades.
    """
    prepared = dict(caps)

    udid       = prepared.get("appium:udid") or prepared.get("udid", "")
    bundle_id  = prepared.get("appium:bundleId") or prepared.get("bundleId", "")
    app_path   = prepared.get("appium:app") or prepared.get("app", "")

    # Auto-detect iOS version from connected device — overrides suite file value
    if udid and not _is_simulator_udid(udid):
        detected = _detect_ios_version(udid)
        if detected:
            configured = prepared.get("appium:platformVersion") or prepared.get("platformVersion")
            if configured != detected:
                logger.info("📱 iOS version auto-detected: %s (suite had: %s) — updating", detected, configured)
            prepared["appium:platformVersion"] = detected

    if udid and bundle_id and app_path:
        if _is_simulator_udid(udid):
            if _is_app_installed_on_simulator(udid, bundle_id):
                logger.info(
                    "✅ iOS simulator: '%s' already installed on %s — skipping reinstall",
                    bundle_id, udid,
                )
                prepared.pop("appium:app", None)
                prepared.pop("app", None)
                prepared["appium:noReset"] = True
            else:
                logger.info(
                    "📦 iOS simulator: '%s' NOT found on %s — will install from: %s",
                    bundle_id, udid, app_path,
                )
        else:
            # Real device — use ios-deploy (reliable) instead of Appium AFC transfer
            no_reset = prepared.get("appium:noReset", prepared.get("noReset", False))
            already_installed = _is_app_installed_on_real_device(udid, bundle_id)
            if already_installed and no_reset:
                logger.info("✅ iOS real device: '%s' already installed, noReset=true — skipping reinstall", bundle_id)
                prepared.pop("appium:app", None)
                prepared.pop("app", None)
            else:
                installed = _install_ipa_via_ios_deploy(udid, app_path)
                if installed:
                    # Tell Appium the app is already installed — skip its AFC transfer
                    prepared.pop("appium:app", None)
                    prepared.pop("app", None)
                    prepared["appium:noReset"] = True
                elif already_installed:
                    # ios-deploy reinstall failed (e.g. app running/locked) but app is present
                    # — skip AFC transfer, let Appium launch the existing install
                    logger.warning("⚠️  ios-deploy reinstall failed but app is installed — skipping AFC, launching existing")
                    prepared.pop("appium:app", None)
                    prepared.pop("app", None)
                    prepared["appium:noReset"] = True
                else:
                    logger.warning("⚠️  ios-deploy failed and app not installed — falling back to Appium AFC install")
                    logger.info("📦 iOS real device: will install '%s' from: %s", bundle_id, app_path)
    elif not app_path and bundle_id:
        logger.info("✅ iOS: no appium:app set — launching existing install of '%s'", bundle_id)

    auto_accept = prepared.get("appium:autoAcceptAlerts", prepared.get("autoAcceptAlerts"))
    has_auto_dismiss = (
        "appium:autoDismissAlerts" in prepared
        or "autoDismissAlerts" in prepared
    )
    if auto_accept is False and not has_auto_dismiss:
        prepared["appium:autoDismissAlerts"] = True
        logger.info("🛡️  iOS: autoAcceptAlerts=false → enabling autoDismissAlerts=true")

    return prepared


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

    # ── iOS simulator: check if app is already installed; skip appium:app if so ─
    if platform == "ios":
        caps = _prepare_ios_app(caps)
        caps = enforce_ios_readiness(caps)

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
    try:
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
    except Exception as exc:
        if platform == "ios":
            raise enrich_ios_session_exception(exc, caps) from exc
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

# Command types whose `target` is a variable name to look up or create, not a value.
_VARIABLE_NAME_TARGETS = {"verify_var_contains", "create_variable", "extract_json"}


def _execute_step(cmd, driver, platform: str):
    """Route a parsed Command to the correct appium_action_service function."""
    import execution.appium_action_service as svc

    # Resolve any ${variables} in text/target fields
    target = resolve_variables(cmd.target or "")
    text   = resolve_variables(cmd.text   or "")

    # A few commands take a variable NAME as their target rather than a value.
    # resolve_variables() substitutes a bare name for its own contents, so pre-resolving
    # these hands the action the stored value where it expects the key — and the lookup
    # that follows can then never succeed.
    if cmd.type in _VARIABLE_NAME_TARGETS:
        target = cmd.target or ""

    # Extract index suffix from target (e.g. "login_btn[last]" → el_index="last")
    # _find_element handles this internally, but we also expose it for if-visible helpers.
    _idx_m = re.match(r'^(.+?)\[([^\]]+)\]$', target)
    el_index = _idx_m.group(2) if _idx_m else "any"
    first_value = (cmd.values or [None])[0] if hasattr(cmd, "values") else None
    first_value = resolve_variables(first_value) if isinstance(first_value, str) else first_value

    def _switch_window_cmd(svc, driver, cmd):
        """
        Native windows are a flat list — there is no opener graph, so
        parent/child cannot be resolved. Saying so beats silently switching to
        window 0, which looks like it worked and then asserts against the
        wrong screen.
        """
        if cmd.text:
            if cmd.text in ("first", "current"):
                return svc.switch_window(driver, 0)
            raise AssertionError(
                f"❌ '{cmd.text} window' has no meaning on a native app — there "
                f"is no parent/child relationship between native windows. Use "
                f"'switch to window <number>'.")
        return svc.switch_window(driver, int(cmd.count or 0))

    dispatch = {
        # ── App lifecycle ─────────────────────────────────────────────────
        "open":                      lambda: svc.open_url(driver, text or target),
        "launch_app":                lambda: svc.launch_app(driver),
        # ── Interaction ───────────────────────────────────────────────────
        # target may carry [index] suffix — _find_element handles it internally
        "click":                     lambda: svc.tap_element(driver, target, platform),
        "tap":                       lambda: svc.tap_element(driver, target, platform),
        "tap_text":                  lambda: svc.tap_by_text(driver, text, platform),
        "dismiss_alerts":            lambda: svc.dismiss_alerts(driver, platform),
        "dismiss_play_rating":       lambda: svc.dismiss_play_rating(driver, platform),
        "click_if_exists":           lambda: _tap_if_exists(svc, driver, target, platform),
        "tap_if_exists":             lambda: _tap_if_exists(svc, driver, target, platform),
        # ── If Visible (conditional + optional wait timeout) ──────────────
        "tap_if_visible":            lambda: _tap_if_visible(svc, driver, target, platform, float(cmd.wait or 0)),
        "click_if_visible":          lambda: _tap_if_visible(svc, driver, target, platform, float(cmd.wait or 0)),
        "fill_if_visible":           lambda: _fill_if_visible(svc, driver, target, text, platform, float(cmd.wait or 0)),
        "type_if_visible":           lambda: _fill_if_visible(svc, driver, target, text, platform, float(cmd.wait or 0)),
        "verify_if_visible":         lambda: _verify_if_visible(svc, driver, target, platform, float(cmd.wait or 0), el_index),
        "double_tap_if_visible":     lambda: _double_tap_if_visible(svc, driver, target, platform, float(cmd.wait or 0)),
        "long_press_if_visible":     lambda: _long_press_if_visible(svc, driver, target, platform, float(cmd.wait or 0)),
        "store_text_if_visible":     lambda: _store_text_if_visible(svc, driver, target, platform, cmd.variable_name, float(cmd.wait or 0), el_index),
        # ─────────────────────────────────────────────────────────────────
        "fill":                      lambda: svc.fill_element(driver, target, text, platform),
        "type":                      lambda: svc.fill_element(driver, target, text, platform),
        "type_text":                 lambda: svc.type_into_active(driver, text, platform),
        "fill_if_exists":            lambda: _fill_if_exists(svc, driver, target, text, platform),
        "type_if_exists":            lambda: _fill_if_exists(svc, driver, target, text, platform),
        "clear":                     lambda: svc.clear_element(driver, target, platform),
        "double_tap":                lambda: svc.double_tap(driver, target, platform),
        "long_press":                lambda: svc.long_press(driver, target, platform),
        "wait_for_element":          lambda: svc.wait_for_element(driver, target, platform, timeout_s=float(cmd.wait or 15)),
        "press_back":                lambda: svc.press_back(driver),
        "press_home":                lambda: svc.press_home(driver, platform),
        "press_enter":               lambda: svc.press_enter(driver, platform),
        "hide_keyboard":             lambda: svc.hide_keyboard(driver, platform),
        # ── Scroll / Swipe ────────────────────────────────────────────────
        "scroll":                    lambda: svc.swipe_up(driver),
        "scroll_down":               lambda: svc.swipe_up(driver),
        "scroll_up":                 lambda: svc.swipe_down(driver),
        "swipe_left":                lambda: svc.swipe_left(driver),
        "swipe_right":               lambda: svc.swipe_right(driver),
        "scroll_until_text_visible": lambda: svc.scroll_until_text_visible(
                                         driver, text, int(cmd.count or 8), float(cmd.wait or 0.5), platform
                                     ),
        "scroll_until_element":      lambda: svc.scroll_until_element_visible(
                                         driver, target, platform, int(cmd.count or 8)
                                     ),
        "scroll_to":                 lambda: svc.scroll_to_element(driver, target, platform),
        # ── Wait / Timing ─────────────────────────────────────────────────
        "wait":                      lambda: svc.wait_seconds(driver, float(cmd.wait or 1)),
        # ── Screenshot ────────────────────────────────────────────────────
        "screenshot":                lambda: svc.take_screenshot(driver, target or "capture"),
        # ── Verification ──────────────────────────────────────────────────
        "verify_text":               lambda: svc.verify_text(driver, text),
        "verify_exact_text":         lambda: svc.verify_text(driver, text),
        "verify_multiple_texts":     lambda: svc.verify_texts(driver, cmd.values if hasattr(cmd, "values") and cmd.values else [text]),
        "verify_element_exists":     lambda: svc.verify_element_exists(driver, target, platform, el_index),
        "verify_element_not_exists": lambda: svc.verify_element_not_exists(driver, target, platform, el_index),
        "verify_var_contains":       lambda: _verify_var_contains(target, text),
        # ── Variable extraction ───────────────────────────────────────────
        "extract_text":              lambda: svc.store_element_text(driver, target, platform, cmd.variable_name, el_index),
        "store_text":                lambda: svc.store_element_text(driver, target, platform, cmd.variable_name, el_index),
        "extract_url":               lambda: _store_value(str(driver.current_url), cmd.variable_name),
        "extract_title":             lambda: _store_value(str(driver.title), cmd.variable_name),
        "create_variable":           lambda: svc.store_variable(text, target),
        "math":                      lambda: _execute_math(cmd),
        # ── Fake data generation (faker) ─────────────────────────────────────
        "generate_fake":             lambda: __import__("execution.action_service", fromlist=["generate_fake_data"]).generate_fake_data(text, cmd.variable_name),
        "random_number":             lambda: __import__("execution.action_service", fromlist=["generate_random_number"]).generate_random_number(target, text, cmd.variable_name),
        "random_string":             lambda: __import__("execution.action_service", fromlist=["generate_random_string"]).generate_random_string(cmd.count or 8, cmd.variable_name),
        # ── Date / Time ───────────────────────────────────────────────────────
        "get_date":                  lambda: __import__("execution.action_service", fromlist=["get_date_value"]).get_date_value(text, cmd.variable_name),
        "format_date":               lambda: __import__("execution.action_service", fromlist=["format_date_value"]).format_date_value(text, first_value, cmd.variable_name),
        # ── HTTP / API ───────────────────────────────────────────────────────
        "api_get":                   lambda: __import__("execution.action_service", fromlist=["api_get"]).api_get(text, cmd.variable_name),
        "api_post":                  lambda: __import__("execution.action_service", fromlist=["api_post"]).api_post(text, target, cmd.variable_name),
        "extract_json":              lambda: __import__("execution.action_service", fromlist=["extract_json_path"]).extract_json_path(target, text, cmd.variable_name),
        # ── Excel / CSV ───────────────────────────────────────────────────────
        "read_excel_cell":           lambda: __import__("execution.action_service", fromlist=["read_excel_cell"]).read_excel_cell(text, int(target), first_value, cmd.variable_name),
        "read_excel_row":            lambda: __import__("execution.action_service", fromlist=["read_excel_row"]).read_excel_row(text, int(target), cmd.variable_name),
        "read_csv_cell":             lambda: __import__("execution.action_service", fromlist=["read_csv_cell"]).read_csv_cell(text, int(target), first_value, cmd.variable_name),
        # -- JavaScript Actions (WebView / hybrid context only) ---------------
        # These require driver.execute_script — they do NOT use Playwright page.evaluate().
        # Only valid after switching to a WebView context (driver.switch_to.context("WEBVIEW_*")).
        "js_click":     lambda: driver.execute_script(f"document.querySelector('{target}').click()"),
        "js_scroll_to": lambda: driver.execute_script(f"document.querySelector('{target}').scrollIntoView(true)"),
        "js_scroll":    lambda: driver.execute_script(f"window.scrollBy(0, {cmd.count or 300})"),
        "js_type":      lambda: driver.execute_script(f"document.querySelector('{target}').value = arguments[0]", text),
        "js_set_value": lambda: driver.execute_script(f"document.querySelector('{target}').value = arguments[0]", text),
        "js_focus":     lambda: driver.execute_script(f"document.querySelector('{target}').focus()"),
        "js_submit":    lambda: driver.execute_script(f"document.querySelector('{target}').submit()"),
        "js_dispatch":  lambda: driver.execute_script(f"document.querySelector('{target}').dispatchEvent(new Event('{text}', {{bubbles: true}}))"),
        # ── Windows / Tabs ────────────────────────────────────────────────
        "switch_tab":                lambda: _switch_window_cmd(svc, driver, cmd),
        "close_tab":                 lambda: svc.close_window(driver, int(cmd.count) if cmd.count is not None else None),
        "close_all_tabs":            lambda: svc.close_all_windows(driver),
        "list_tabs":                 lambda: svc.list_windows(driver),
        "open_new_tab":              lambda: logger.warning("⚠️  open_new_tab is not supported in native Appium context"),
        "open_in_new_tab":           lambda: logger.warning("⚠️  open_in_new_tab is not supported in native Appium context"),
        # ── Iframes (WebView / mobile-browser context only) ───────────────
        "switch_iframe":             lambda: svc.switch_iframe(driver, target),
        "exit_iframe":               lambda: svc.exit_iframe(driver),
    }

    handler = dispatch.get(cmd.type)
    if not handler:
        logger.warning("⚠️  Unsupported command for Appium: '%s' — skipping", cmd.type)
        return

    # iOS recovery: if a system permission sheet blocks the step, dismiss alert(s)
    # and retry the same step once. This keeps flows stable when popups appear
    # asynchronously between actions.
    if platform != "ios" or cmd.type in {"dismiss_alerts", "dismiss_play_rating"}:
        handler()
        return

    # Pre-step iOS alert sweep: clears async permission sheets that appeared
    # between commands, before they block the next action.
    try:
        svc.dismiss_alerts(driver, platform, attempts=1)
    except Exception:
        pass

    try:
        handler()
    except Exception:
        recovered = False
        try:
            recovered = bool(svc.dismiss_alerts(driver, platform, attempts=3))
        except Exception:
            recovered = False

        if recovered:
            logger.info("🔁 Retrying step after iOS alert recovery: %s", cmd.type)
            handler()
            return
        raise


def _execute_single_step(step_text: str, driver, platform: str):
    """
    Parse a single flow step string and execute it on the given driver.
    Used by recorder_ui.py 'Run Flow' feature.
    Raises on hard failures so the caller can log them.
    """
    from nlp.parser import parse_step
    cmd = parse_step(step_text)
    if cmd is None:
        raise ValueError(f"Unrecognised step syntax: {step_text!r}")
    if cmd.type == "call_reusable":
        _expand_reusable_appium(cmd.target, driver, platform)
        return
    _execute_step(cmd, driver, platform)


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


# ─── IF VISIBLE helpers (conditional + configurable wait timeout) ─────────────

def _wait_for_visible(svc, driver, target: str, platform: str, timeout: float) -> bool:
    """Returns True if element becomes visible within `timeout` seconds, False otherwise."""
    if timeout and timeout > 0:
        try:
            svc.wait_for_element(driver, target, platform, timeout_s=timeout)
            return True
        except Exception:
            return False
    else:
        # Zero timeout — just check if it's already on screen right now (1s max)
        try:
            svc.wait_for_element(driver, target, platform, timeout_s=1)
            return True
        except Exception:
            return False


def _tap_if_visible(svc, driver, target: str, platform: str, timeout: float = 0):
    """Tap element only if visible; skip silently otherwise."""
    if _wait_for_visible(svc, driver, target, platform, timeout):
        try:
            svc.tap_element(driver, target, platform)
        except Exception as e:
            logger.info("ℹ️  tap_if_visible: element appeared but tap failed (%s): '%s'", e, target)
    else:
        logger.info("ℹ️  tap_if_visible: element not visible, skipping: '%s'", target)


def _fill_if_visible(svc, driver, target: str, text: str, platform: str, timeout: float = 0):
    """Type into element only if visible; skip silently otherwise."""
    if _wait_for_visible(svc, driver, target, platform, timeout):
        try:
            svc.fill_element(driver, target, text, platform)
        except Exception as e:
            logger.info("ℹ️  fill_if_visible: element appeared but fill failed (%s): '%s'", e, target)
    else:
        logger.info("ℹ️  fill_if_visible: element not visible, skipping: '%s'", target)


def _verify_if_visible(svc, driver, target: str, platform: str, timeout: float = 0, el_index: str = "any"):
    """Verify element exists only if visible; skip (pass) silently otherwise."""
    if _wait_for_visible(svc, driver, target, platform, timeout):
        try:
            svc.verify_element_exists(driver, target, platform, el_index)
        except Exception as e:
            logger.info("ℹ️  verify_if_visible: verify failed (%s): '%s'", e, target)
    else:
        logger.info("ℹ️  verify_if_visible: element not visible, skipping: '%s'", target)


def _double_tap_if_visible(svc, driver, target: str, platform: str, timeout: float = 0):
    """Double-tap element only if visible; skip silently otherwise."""
    if _wait_for_visible(svc, driver, target, platform, timeout):
        try:
            svc.double_tap(driver, target, platform)
        except Exception as e:
            logger.info("ℹ️  double_tap_if_visible: failed (%s): '%s'", e, target)
    else:
        logger.info("ℹ️  double_tap_if_visible: element not visible, skipping: '%s'", target)


def _long_press_if_visible(svc, driver, target: str, platform: str, timeout: float = 0):
    """Long-press element only if visible; skip silently otherwise."""
    if _wait_for_visible(svc, driver, target, platform, timeout):
        try:
            svc.long_press(driver, target, platform)
        except Exception as e:
            logger.info("ℹ️  long_press_if_visible: failed (%s): '%s'", e, target)
    else:
        logger.info("ℹ️  long_press_if_visible: element not visible, skipping: '%s'", target)


def _store_text_if_visible(svc, driver, target: str, platform: str, variable_name: str, timeout: float = 0, el_index: str = "any"):
    """Store element text only if visible; skip silently otherwise."""
    if _wait_for_visible(svc, driver, target, platform, timeout):
        try:
            svc.store_element_text(driver, target, platform, variable_name, el_index)
        except Exception as e:
            logger.info("ℹ️  store_text_if_visible: failed (%s): '%s'", e, target)
    else:
        logger.info("ℹ️  store_text_if_visible: element not visible, skipping: '%s'", target)


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
        target = resolve_variables(cmd.target) if isinstance(getattr(cmd, "target", None), str) else cmd.target
        rhs = (cmd.values[0] if hasattr(cmd, "values") and cmd.values else 0)
        rhs = resolve_variables(rhs) if isinstance(rhs, str) else rhs
        op = resolve_variables(cmd.text) if isinstance(getattr(cmd, "text", None), str) else (cmd.text or "+")
        a = float(RUNTIME_VARIABLES.get(target, target))
        b = float(RUNTIME_VARIABLES.get(rhs, rhs))
        op = (op or "+").strip()
        result = {"+": a + b, "-": a - b, "*": a * b, "/": a / b if b != 0 else 0}.get(op, a + b)
        RUNTIME_VARIABLES[cmd.variable_name] = str(int(result) if result == int(result) else result)
        logger.info("🔢 Math: %s %s %s = %s → $%s", a, op, b, RUNTIME_VARIABLES[cmd.variable_name], cmd.variable_name)
    except Exception as e:
        logger.error("❌ Math error: %s", e)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# STEP INTERPRETATION
# ─────────────────────────────────────────────────────────────────────────────

def _expand_reusable_appium(name: str, driver, platform: str) -> None:
    """Inline-expand a named reusable step group on the Appium driver."""
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
        # if / loops work inside a step group on the app too.
        from execution.control_flow import AppProbe, run_lines
        run_lines(steps, None, lambda sub: _interpret_step(sub, driver, platform),
                  logger=logger, probe=AppProbe(driver, platform))
    finally:
        call_stack.discard(name_lower)


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

    if cmd.type == "call_reusable":
        _expand_reusable_appium(cmd.target, driver, platform)
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
    steps  = _load_flow_file(file_path)
    stats  = {"passed": 0, "failed": 0, "log": []}

    # if / else, for each row, repeat — the same blocks as Website / Mobile Site.
    from core import datasets as _datasets
    from execution.control_flow import AppProbe, FlowProgram, make_evaluator
    program = FlowProgram(steps, evaluate=make_evaluator(probe=AppProbe(driver, platform)),
                          variables=RUNTIME_VARIABLES, load_rows=_datasets.rows)

    for item in program.steps():
        step = item.text
        try:
            if item.kind is not None:
                said = program.decide(item)
                logger.info("🔀 %s → %s", step, said)
                stats["log"].append(f"🔀 {step} → {said}")
                continue
            _interpret_step(step, driver, platform)
            stats["passed"] += 1
        except AssertionError as e:
            msg = str(e)
            logger.error("❌ Assertion failed: %s", msg)
            stats["failed"] += 1
            stats["log"].append(f"❌ {msg}")
            if _get_stop_on_failure():
                logger.warning("🛑 stop_on_failure=true — halting flow")
                break
        except Exception as e:
            msg = str(e)
            logger.error("❌ Step error: %s", msg)
            stats["failed"] += 1
            stats["log"].append(f"❌ {msg}")
            if _get_stop_on_failure():
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
    _bind_app_runtime_session()
    _set_stop_on_failure(stop_on_first_failure)

    try:
        import json as _json
        cfg_path = "config/playwright.config.json"
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                run_cfg = _json.load(f).get("run", {})
                _set_stop_on_failure(bool(run_cfg.get("stop_on_failure", False)))
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

        # ── Auto video recording ──────────────────────────────────────────
        import execution.appium_action_service as _svc_vid
        _svc_vid.start_recording(driver, platform)

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
        # ── Save video recording ──────────────────────────────────────────
        try:
            import execution.appium_action_service as _svc_vid
            if driver:
                suite_label = os.path.splitext(os.path.basename(
                    flow_files[0] if flow_files else "suite"
                ))[0]
                _svc_vid.stop_recording(driver, label=suite_label, platform=platform)
        except Exception as _ve:
            logger.warning("⚠️  Video save error: %s", _ve)
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
    _bind_app_runtime_session()

    # Load stop_on_failure from playwright config (shared config)
    try:
        import json as _json
        cfg_path = "config/playwright.config.json"
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                run_cfg = _json.load(f).get("run", {})
                _set_stop_on_failure(bool(run_cfg.get("stop_on_failure", False)))
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
        # ── Auto video recording ──────────────────────────────────────────
        import execution.appium_action_service as _svc_vid
        _svc_vid.start_recording(driver, platform)
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
        # ── Save video recording ──────────────────────────────────────────
        try:
            import execution.appium_action_service as _svc_vid
            if driver:
                _svc_vid.stop_recording(driver, label=label, platform=platform)
        except Exception as _ve:
            logger.warning("⚠️  Video save error: %s", _ve)
        _end_session(driver, label)

    return stats


# ─────────────────────────────────────────────────────────────────────────────
# CLI  (direct usage)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NLP flow runner for Appium (Android/iOS)")
    parser.add_argument("flow",     help="Path to .flow file OR a suite JSON file (suites/*.json)")
    parser.add_argument("--platform", "-p", default="android",
                        choices=["android", "ios"], help="Target platform (default: android)")
    parser.add_argument("--caps", "-c", default=None,
                        help="Path to JSON file with Appium capabilities")
    args = parser.parse_args()

    # ── Suite JSON shortcut: python runner_appium.py suites/android_suite.json ──
    if args.flow.endswith(".json"):
        with open(args.flow) as f:
            suite = json.load(f)

        # Extract fields from the suite JSON
        flow_files   = suite.get("scripts", [])
        caps         = suite.get("desired_capabilities", {})
        platform     = suite.get("platform", args.platform).lower()
        parameters   = suite.get("parameters", [])

        if not flow_files:
            print(f"❌ No 'scripts' found in suite file: {args.flow}")
            sys.exit(1)

        # Inject suite parameters into RUNTIME_VARIABLES
        for param in parameters:
            name  = param.get("name", "").strip()
            value = str(param.get("value", "")).strip()
            if name:
                RUNTIME_VARIABLES[name] = value
                logger.info("  🔑 Param injected: ${%s} = '%s'", name, value)

        results = run_appium_suite_collect(flow_files, caps, platform)

        total_p = sum(r.get("passed", 0) for r in results)
        total_f = sum(r.get("failed", 0) for r in results)
        total   = total_p + total_f
        status  = "✅ PASSED" if total_f == 0 else "❌ FAILED"
        print(f"\n{status}  {total_p}/{total} steps passed")
        for r in results:
            flow_status = "✅" if r.get("failed", 0) == 0 else "❌"
            print(f"  {flow_status} {r['file']}  ({r.get('passed',0)}/{r.get('passed',0)+r.get('failed',0)} steps)")
            for msg in r.get("log", []):
                print(f"      {msg}")
        sys.exit(0 if total_f == 0 else 1)

    # ── Standard .flow file usage ─────────────────────────────────────────────
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
