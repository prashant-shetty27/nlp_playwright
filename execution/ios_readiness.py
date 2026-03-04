"""
execution/ios_readiness.py
Centralized iOS real-device readiness checks for Appium/XCUITest.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field


@dataclass
class IOSReadinessReport:
    ok: bool
    normalized_caps: dict
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class IOSReadinessError(RuntimeError):
    """Raised when required iOS prerequisites are missing."""


def _run(cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception:
        return subprocess.CompletedProcess(cmd, 1, "", "command failed")


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for p in str(version).strip().split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _extract_team_ids(identity_lines: list[str]) -> set[str]:
    teams: set[str] = set()
    for line in identity_lines:
        # Apple Development: Name (TEAMID)
        m = re.search(r"\(([A-Z0-9]{10})\)", line)
        if m:
            teams.add(m.group(1))
    return teams


def _raw_user_keychain_entries() -> list[str]:
    """Return raw keychain entries from `security list-keychains -d user`."""
    listed = _run(["security", "list-keychains", "-d", "user"], timeout=8)
    blob = (listed.stdout or "") + "\n" + (listed.stderr or "")
    return re.findall(r'"([^"]*)"', blob)


def _user_keychain_paths() -> list[str]:
    """Return valid filesystem keychain paths from user keychain search list."""
    paths: list[str] = []
    for entry in _raw_user_keychain_entries():
        if not entry:
            continue
        expanded = os.path.expanduser(entry)
        if os.path.exists(expanded):
            paths.append(expanded)
    return paths


def _apple_dev_identity_lines() -> tuple[list[str], list[str]]:
    """
    Collect Apple Development identity lines.
    Uses default keychain search first; if empty, falls back to each valid
    user keychain path to avoid false negatives from malformed search lists.
    """
    warnings: list[str] = []

    ids = _run(["security", "find-identity", "-v", "-p", "codesigning"], timeout=12)
    id_blob = (ids.stdout or "") + "\n" + (ids.stderr or "")
    lines = [
        ln.strip()
        for ln in id_blob.splitlines()
        if ("Apple Development" in ln or "iPhone Developer" in ln)
    ]
    if lines:
        return lines, warnings

    raw_entries = _raw_user_keychain_entries()
    if any(entry.strip() == "" for entry in raw_entries):
        warnings.append(
            "User keychain search list contains an empty entry; macOS security CLI can return false negatives. "
            "Run: security list-keychains -d user -s ~/Library/Keychains/login.keychain-db"
        )

    collected: list[str] = []
    for keychain in _user_keychain_paths():
        r = _run(["security", "find-identity", "-v", "-p", "codesigning", keychain], timeout=10)
        blob = (r.stdout or "") + "\n" + (r.stderr or "")
        collected.extend(
            ln.strip()
            for ln in blob.splitlines()
            if ("Apple Development" in ln or "iPhone Developer" in ln)
        )

    # Stable de-dup preserve order
    seen: set[str] = set()
    deduped: list[str] = []
    for line in collected:
        key = _canonical_key(line)
        if key not in seen:
            seen.add(key)
            deduped.append(line)
    return deduped, warnings


def _extract_team_ids_from_apple_dev_certs() -> set[str]:
    """
    Extract team IDs from Apple Development certificate subjects (OU field).
    This is more reliable than CN display text from `find-identity`.
    """
    teams: set[str] = set()
    blobs: list[str] = []

    # Query explicit keychains first to avoid malformed-keychain-list failures.
    keychains = _user_keychain_paths()
    if keychains:
        for keychain in keychains:
            certs = _run(
                ["security", "find-certificate", "-a", "-c", "Apple Development", "-p", keychain],
                timeout=18,
            )
            if certs.stdout:
                blobs.append(certs.stdout)
    else:
        certs = _run(
            ["security", "find-certificate", "-a", "-c", "Apple Development", "-p"],
            timeout=18,
        )
        if certs.stdout:
            blobs.append(certs.stdout)

    blob = "\n".join(blobs)
    pem_blocks = re.findall(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
        blob,
        flags=re.S,
    )
    for pem in pem_blocks:
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
                tf.write(pem)
                cert_path = tf.name
            subj = _run(
                ["openssl", "x509", "-in", cert_path, "-noout", "-subject", "-nameopt", "RFC2253"],
                timeout=8,
            )
            subject = (subj.stdout or "") + " " + (subj.stderr or "")
            for m in re.findall(r"OU=([A-Z0-9]{10})", subject):
                teams.add(m)
        except Exception:
            pass
        finally:
            try:
                os.remove(cert_path)
            except Exception:
                pass
    return teams


def _tail(path: str, max_lines: int = 600) -> list[str]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return lines[-max_lines:]
    except Exception:
        return []


def _xctrace_device_listing() -> tuple[int, str]:
    result = _run(["xcrun", "xctrace", "list", "devices"], timeout=20)
    text = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    return result.returncode, text


def _extract_embedded_plist(text: str) -> str:
    start = text.find("<?xml")
    end = text.find("</plist>")
    if start != -1 and end != -1 and end > start:
        return text[start : end + len("</plist>")]
    return text


def _plist_string_value(plist_text: str, key: str) -> str:
    m = re.search(
        rf"<key>\s*{re.escape(key)}\s*</key>\s*<string>\s*([^<]+)\s*</string>",
        plist_text,
        re.I | re.S,
    )
    return (m.group(1).strip() if m else "")


def _plist_first_array_string(plist_text: str, key: str) -> str:
    m = re.search(
        rf"<key>\s*{re.escape(key)}\s*</key>\s*<array>\s*<string>\s*([^<]+)\s*</string>",
        plist_text,
        re.I | re.S,
    )
    return (m.group(1).strip() if m else "")


def _load_local_profiles() -> list[dict]:
    profiles_dir = os.path.expanduser("~/Library/Developer/Xcode/UserData/Provisioning Profiles")
    out: list[dict] = []
    for path in glob.glob(os.path.join(profiles_dir, "*.mobileprovision")):
        try:
            with open(path, "rb") as f:
                raw = f.read().decode("utf-8", errors="ignore")
        except Exception:
            continue
        plist_text = _extract_embedded_plist(raw)
        app_id = _plist_string_value(plist_text, "application-identifier")
        uuid = _plist_string_value(plist_text, "UUID")
        name = _plist_string_value(plist_text, "Name")
        team = _plist_first_array_string(plist_text, "TeamIdentifier")
        if not app_id:
            continue
        out.append({
            "path": path,
            "app_id": app_id,
            "uuid": uuid,
            "name": name,
            "team": team,
        })
    return out


def _matching_profiles_for_bundle(bundle_id: str) -> list[dict]:
    matches: list[dict] = []
    if not bundle_id:
        return matches
    suffix = f".{bundle_id}"
    for p in _load_local_profiles():
        app_id = str(p.get("app_id") or "")
        if app_id.endswith(suffix):
            matches.append(p)
    return matches


def _canonical_key(line: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", line.lower()).strip()


def _iter_value_strings(obj):
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key == "_value" and isinstance(val, str):
                yield val
            yield from _iter_value_strings(val)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_value_strings(item)


def summarize_latest_xcresult_failures(
    xcresult_root: str = os.path.expanduser(
        "~/.appium/node_modules/appium-xcuitest-driver/node_modules/Logs/Test"
    ),
) -> list[str]:
    """
    Extract root-cause lines from the newest WebDriverAgent .xcresult bundle.
    """
    patterns = [
        "No Account for Team",
        "No signing certificate",
        "No profiles for",
        "Failed to install the app on the device",
        "unable to create bookmark data",
        "No tunnel found for device",
        "xcodebuild exited with code '65'",
    ]
    bundles = sorted(
        glob.glob(os.path.join(xcresult_root, "Test-WebDriverAgentRunner-*.xcresult")),
        key=lambda p: os.path.getmtime(p),
        reverse=True,
    )
    if not bundles:
        return []

    newest = bundles[0]
    result = _run(
        ["xcrun", "xcresulttool", "get", "--legacy", "--path", newest, "--format", "json"],
        timeout=35,
    )
    if result.returncode != 0 or not (result.stdout or "").strip():
        return []

    try:
        payload = json.loads(result.stdout)
    except Exception:
        return []

    findings: list[str] = []
    seen: set[str] = set()
    for text in _iter_value_strings(payload):
        line = re.sub(r"\s+", " ", text).strip()
        for p in patterns:
            if p in line:
                key = _canonical_key(line)
                if key not in seen:
                    seen.add(key)
                    findings.append(line)
                break
    return findings


def summarize_recent_appium_xcode_failures(appium_log_path: str = "data/logs/appium.log") -> list[str]:
    """
    Pulls meaningful root-cause lines from recent Appium/Xcode logs.
    """
    patterns = [
        "No Account for Team",
        "No signing certificate",
        "No profiles for",
        "xcodebuild exited with code '65'",
        "Failed to install the app on the device",
        "No tunnel found for device",
    ]
    seen: set[str] = set()
    findings: list[str] = []
    ansi = re.compile(r"\x1B\[[0-9;]*[A-Za-z]")
    level_prefix = re.compile(r"^(?:info|warn|warning|error|err!)\s+\S+\s+", re.I)
    tag_prefix = re.compile(r"^(?:\[[^\]]+\]\s*)+")

    for raw in _tail(appium_log_path):
        line = ansi.sub("", raw).strip()
        line = tag_prefix.sub("", line).strip()
        line = level_prefix.sub("", line).strip()
        line = re.sub(r"\s+", " ", line).strip()
        for p in patterns:
            if p in line:
                key = _canonical_key(line)
                if key in seen:
                    break
                seen.add(key)
                findings.append(line)
                break
    return findings


def _diagnose_primary_failure(findings: list[str]) -> tuple[str, list[str]]:
    lower = [f.lower() for f in findings]
    remedies: list[str] = []

    if any("no account for team" in f for f in lower):
        remedies.append("Add/login Apple ID in Xcode > Settings > Accounts for the configured team.")
    if any("no signing certificate" in f for f in lower):
        remedies.append("Create Apple Development certificate in Manage Certificates and ensure private key exists.")
    if any("no profiles for" in f for f in lower):
        remedies.append("Enable Automatic Signing and let Xcode create profile for updatedWDABundleId.xctrunner.")

    if remedies:
        return ("Signing/provisioning is not configured for WebDriverAgent on this Mac.", remedies)

    if any("no tunnel found for device" in f for f in lower):
        return (
            "XCUITest tunnel is unavailable for this device (secondary transport issue).",
            ["Run: sudo appium driver run xcuitest tunnel-creation"],
        )

    if any("xcodebuild exited with code '65'" in f for f in lower):
        return (
            "xcodebuild exited with code 65, but no specific signature was extracted.",
            ["Inspect the latest .xcresult bundle under ~/.appium/.../Logs/Test/"],
        )

    return ("No known root-cause signature extracted.", [])


def evaluate_ios_readiness(capabilities: dict) -> IOSReadinessReport:
    caps = dict(capabilities or {})
    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    team_id = str(caps.get("appium:xcodeOrgId") or caps.get("xcodeOrgId") or "").strip()
    udid = str(caps.get("appium:udid") or caps.get("udid") or "").strip()
    updated_wda_bundle = str(
        caps.get("appium:updatedWDABundleId") or caps.get("updatedWDABundleId") or ""
    ).strip()
    requested_version = str(
        caps.get("appium:platformVersion") or caps.get("platformVersion") or ""
    ).strip()
    xcode_config = str(caps.get("appium:xcodeConfigFile") or caps.get("xcodeConfigFile") or "").strip()

    xcode = _run(["xcodebuild", "-version"], timeout=12)
    if xcode.returncode != 0:
        errors.append("xcodebuild is unavailable. Install/activate Xcode command line tools.")
    else:
        first = (xcode.stdout or "").splitlines()
        if first:
            notes.append(first[0].strip())

    if xcode_config and not os.path.exists(xcode_config):
        errors.append(f"xcodeConfigFile not found: {xcode_config}")

    identity_lines, identity_warnings = _apple_dev_identity_lines()
    warnings.extend(identity_warnings)
    cert_subject_teams = _extract_team_ids_from_apple_dev_certs()
    if not identity_lines:
        # security CLI cannot see iCloud Keychain certs. Use provisioning profile as
        # a proxy: if a local profile exists for the configured team, Xcode almost
        # certainly has the cert accessible (via iCloud Keychain or local), so
        # downgrade to warning and let xcodebuild try.
        profile_teams: set[str] = set()
        if updated_wda_bundle:
            runner_bundle_early = f"{updated_wda_bundle}.xctrunner"
            for p in _matching_profiles_for_bundle(runner_bundle_early):
                t = p.get("team", "")
                if t:
                    profile_teams.add(t)

        if cert_subject_teams or (team_id and team_id in profile_teams):
            visible_teams = sorted(cert_subject_teams or profile_teams)
            notes.append(f"Apple Development identities: not visible to security CLI (iCloud Keychain)")
            if visible_teams:
                notes.append(f"Apple Development cert team(s): {', '.join(visible_teams)}")
            warnings.append(
                "Apple Development certs are not visible to the security CLI (likely iCloud Keychain). "
                "xcodebuild uses Xcode's own APIs and should still be able to sign. "
                "If WDA build fails with signing errors, re-create the cert locally via "
                "Xcode > Settings > Accounts > Manage Certificates."
            )
            if team_id and team_id not in (cert_subject_teams | profile_teams):
                errors.append(
                    f"No signing identity found for team '{team_id}'. "
                    f"Available teams: {sorted(cert_subject_teams | profile_teams)}"
                )
        else:
            errors.append(
                "No Apple Development signing identity found. "
                "Add one in Xcode > Settings > Accounts > Manage Certificates."
            )
    else:
        notes.append(f"Apple Development identities: {len(identity_lines)}")
        if team_id:
            teams = _extract_team_ids(identity_lines)
            all_teams = sorted(set(teams) | set(cert_subject_teams))
            if all_teams:
                notes.append(f"Apple Development cert team(s): {', '.join(all_teams)}")
            if all_teams and team_id not in all_teams:
                errors.append(
                    f"No signing identity found for team '{team_id}'. "
                    f"Available teams: {all_teams}"
                )

    if requested_version:
        sdk = _run(["xcrun", "--sdk", "iphoneos", "--show-sdk-version"], timeout=8)
        sdk_version = (sdk.stdout or "").strip()
        if sdk_version and _version_tuple(requested_version) > _version_tuple(sdk_version):
            warnings.append(
                f"Requested platformVersion={requested_version} is above installed iPhoneOS SDK={sdk_version}; using {sdk_version}."
            )
            caps["appium:platformVersion"] = sdk_version

    # Device visibility guard for real-device runs.
    if udid:
        rc, listing = _xctrace_device_listing()
        if rc != 0:
            warnings.append(
                "Unable to list devices via xctrace right now. If Appium reports unknown UDID, reconnect/unlock/trust device and retry."
            )
        elif udid not in listing:
            warnings.append(
                f"Target UDID '{udid}' is not currently visible to Xcode tooling (xctrace). "
                "Appium may fail with unknown UDID until the device is detected."
            )

    # Verify configured team matches local profile owner for WDA runner bundle.
    if updated_wda_bundle:
        runner_bundle = f"{updated_wda_bundle}.xctrunner"
        wda_profiles = _matching_profiles_for_bundle(runner_bundle)
        if not wda_profiles:
            warnings.append(
                f"No local provisioning profile found for '{runner_bundle}'. "
                "Xcode must create one during first successful WDA build."
            )
        else:
            teams = sorted({p.get("team", "") for p in wda_profiles if p.get("team")})
            if teams:
                notes.append(
                    f"Local profile team(s) for {runner_bundle}: {', '.join(teams)}"
                )
                if team_id and team_id not in teams:
                    errors.append(
                        f"Configured xcodeOrgId '{team_id}' does not match local WDA profile team(s) "
                        f"{teams} for '{runner_bundle}'."
                    )

    # Keep these defaults in one place for both recorder and suite runners.
    caps.setdefault("appium:allowProvisioningUpdates", True)
    caps.setdefault("appium:allowProvisioningDeviceRegistration", True)

    return IOSReadinessReport(
        ok=not errors,
        normalized_caps=caps,
        errors=errors,
        warnings=warnings,
        notes=notes,
    )


def enforce_ios_readiness(capabilities: dict) -> dict:
    report = evaluate_ios_readiness(capabilities)
    if report.errors:
        lines = ["iOS readiness check failed:"]
        for e in report.errors:
            lines.append(f"- {e}")
        if report.warnings:
            lines.append("Warnings:")
            for w in report.warnings:
                lines.append(f"- {w}")
        raise IOSReadinessError("\n".join(lines))
    return report.normalized_caps


def enrich_ios_session_exception(exc: Exception, capabilities: dict) -> RuntimeError:
    exc_text = str(exc)
    if "Unknown device or simulator UDID" in exc_text:
        udid = str(capabilities.get("appium:udid") or capabilities.get("udid") or "").strip()
        lines = [
            f"iOS Appium session failed: {exc}",
            "Primary diagnosis: target iPhone UDID is not visible to Appium/Xcode on this host.",
            "Required fixes:",
            "- Reconnect device over cable, unlock it, and keep screen ON.",
            "- Confirm 'Trust This Computer' is accepted on iPhone.",
            "- Verify UDID appears in: xcrun xctrace list devices",
            "- Start tunnel and keep it running: sudo appium driver run xcuitest tunnel-creation",
        ]
        if udid:
            lines.append(f"Configured UDID: {udid}")
        return RuntimeError("\n".join(lines))

    findings = summarize_latest_xcresult_failures()
    if not findings:
        findings = summarize_recent_appium_xcode_failures()
    diagnosis, remedies = _diagnose_primary_failure(findings)
    lines = [f"iOS Appium session failed: {exc}", f"Primary diagnosis: {diagnosis}"]
    if remedies:
        lines.append("Required fixes:")
        for r in remedies:
            lines.append(f"- {r}")

    if findings:
        lines.append("Evidence (latest Xcode/Appium):")
        for item in findings[:8]:
            lines.append(f"- {item}")

    team_id = str(capabilities.get("appium:xcodeOrgId") or capabilities.get("xcodeOrgId") or "").strip()
    bundle = str(capabilities.get("appium:updatedWDABundleId") or "").strip()
    if team_id:
        lines.append(f"Configured team: {team_id}")
    if bundle:
        lines.append(f"Configured updatedWDABundleId: {bundle}")

    return RuntimeError("\n".join(lines))


def _load_caps_from_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict) and "desired_capabilities" in raw:
        dc = raw.get("desired_capabilities")
        return dc if isinstance(dc, dict) else {}
    return raw if isinstance(raw, dict) else {}


def _main() -> int:
    parser = argparse.ArgumentParser(description="iOS Appium readiness check")
    parser.add_argument("--caps", required=True, help="Path to capabilities JSON or suite JSON")
    args = parser.parse_args()

    caps = _load_caps_from_file(args.caps)
    report = evaluate_ios_readiness(caps)

    print("iOS readiness summary:")
    for n in report.notes:
        print(f"- {n}")
    for w in report.warnings:
        print(f"- WARNING: {w}")
    for e in report.errors:
        print(f"- ERROR: {e}")
    print("Status:", "READY" if report.ok else "BLOCKED")
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(_main())
