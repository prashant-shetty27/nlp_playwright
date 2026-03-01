"""
reporting/slack_notifier.py
Slack Incoming Webhook notifier using Block Kit for rich formatting.

Message format:
  📋 TEST PLAN: <name>
  🌍 Environment | Parallel | Retry | Rerun
  ─────────────────────────
  📦 Suite: <suite_name>
     ✅ script.flow ......... PASSED  (4.2s)
     ❌ script2.flow ........ FAILED  (1.1s)  ↩ Retried 2x
  ─────────────────────────
  📊 TOTAL: N  |  ✅ P PASSED  |  ❌ F FAILED  |  ⏭ S SKIPPED
  ⏱ Duration: Xs  |  Run by: <owner>

Activate: set NOTIFY_ON_SLACK=true and SLACK_WEBHOOK_URL in .env
"""
import json
import logging
import os
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

class ScriptResult:
    """Result for a single .flow script execution."""
    def __init__(
        self,
        script: str,
        status: str,           # "passed" | "failed" | "skipped"
        duration_s: float = 0.0,
        retries: int = 0,
        failure_reason: str = "",
    ):
        self.script = script
        self.status = status.lower()
        self.duration_s = duration_s
        self.retries = retries
        self.failure_reason = failure_reason

    @property
    def icon(self) -> str:
        return {"passed": "✅", "failed": "❌", "skipped": "⏭"}.get(self.status, "❓")


class SuiteResult:
    """Aggregated results for one test suite."""
    def __init__(self, suite_name: str):
        self.suite_name = suite_name
        self.scripts: list[ScriptResult] = []

    def add(self, result: ScriptResult):
        self.scripts.append(result)

    @property
    def passed(self) -> int:
        return sum(1 for s in self.scripts if s.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for s in self.scripts if s.status == "failed")

    @property
    def skipped(self) -> int:
        return sum(1 for s in self.scripts if s.status == "skipped")

    @property
    def total(self) -> int:
        return len(self.scripts)

    @property
    def overall_status(self) -> str:
        if self.failed > 0:
            return "❌ FAILED"
        if self.skipped == self.total:
            return "⏭ SKIPPED"
        return "✅ PASSED"


class PlanResult:
    """Full plan execution result — aggregates all suite results."""
    def __init__(
        self,
        plan_name: str,
        environment: str = "local",
        platform: str = "web",
        parallel: bool = False,
        retry_on_failure: bool = False,
        max_retries: int = 0,
        rerun_on_failure: bool = False,
        owner: str = "",
        started_at: Optional[datetime] = None,
    ):
        self.plan_name = plan_name
        self.environment = environment
        self.platform = platform.lower()
        self.parallel = parallel
        self.retry_on_failure = retry_on_failure
        self.max_retries = max_retries
        self.rerun_on_failure = rerun_on_failure
        self.owner = owner
        self.started_at = started_at or datetime.now()
        self.finished_at: Optional[datetime] = None
        self.suites: list[SuiteResult] = []
        self.report_json_path: str = ""
        self.report_txt_path: str = ""

    def add_suite(self, suite: SuiteResult):
        self.suites.append(suite)

    def finish(self):
        self.finished_at = datetime.now()

    @property
    def duration_s(self) -> float:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return 0.0

    @property
    def total_scripts(self) -> int:
        return sum(s.total for s in self.suites)

    @property
    def total_passed(self) -> int:
        return sum(s.passed for s in self.suites)

    @property
    def total_failed(self) -> int:
        return sum(s.failed for s in self.suites)

    @property
    def total_skipped(self) -> int:
        return sum(s.skipped for s in self.suites)

    @property
    def overall_icon(self) -> str:
        return "🟢" if self.total_failed == 0 else "🔴"


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK KIT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _divider() -> dict:
    return {"type": "divider"}


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _header(text: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


_PLATFORM_LABELS: dict[str, str] = {
    "web":     "🌐  Website",
    "mobile":  "📱  Mobile Web",
    "android": "🤖  Android App",
    "ios":     "🍎  iOS App",
    "hybrid":  "🔀  Hybrid App",
}


def _fmt_duration(seconds: float) -> str:
    """Smart duration: seconds below 60s, minutes (2dp) at 60s+."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.2f} min"


def _build_blocks(result: PlanResult) -> list[dict]:
    blocks: list[dict] = []

    # ── 1. Header ─────────────────────────────────────────────────────────────
    overall = "ALL PASSED" if result.total_failed == 0 else "FAILURES DETECTED"
    blocks.append(_header(f"{result.overall_icon}  {result.plan_name}  —  {overall}"))

    # ── 2. Top meta: Platform  •  Owner  •  Started ───────────────────────────
    platform_label = _PLATFORM_LABELS.get(result.platform, f"🌐  {result.platform.title()}")
    started_str    = result.started_at.strftime("%d %b %Y  %H:%M:%S")
    owner_part     = f"   •   👤  *{result.owner}*" if result.owner else ""
    blocks.append(_section(f"{platform_label}{owner_part}   •   🕐  *{started_str}*"))
    blocks.append(_divider())

    # ── 3. Per-suite breakdown ─────────────────────────────────────────────────
    for suite in result.suites:
        if suite.skipped == suite.total and suite.total > 0:
            badge = f"⏭ SKIPPED  (0/{suite.total})"
        elif suite.failed == 0:
            badge = f"✅ PASSED  ({suite.passed}/{suite.total})"
        else:
            badge = f"❌ FAILED  ({suite.passed}/{suite.total} passed,  {suite.failed} failed)"

        blocks.append(_section(f"📦  *Suite: {suite.suite_name}*   ›   {badge}"))

        # ── Per-script rows — one block each so mobile wraps cleanly ──────────
        for sc in suite.scripts:
            name     = sc.script.split("/")[-1]
            duration = _fmt_duration(sc.duration_s)
            retry_tag = f"   ↩ retried {sc.retries}×" if sc.retries > 0 else ""
            blocks.append(_section(
                f"{sc.icon}  *{name}*\n"
                f">  Status: *{sc.status.upper()}*   │   ⏱ {duration}{retry_tag}"
            ))

        # Failure detail block — only shown when there are failures
        failed_scripts = [sc for sc in suite.scripts if sc.status == "failed"]
        if failed_scripts:
            lines = ["*🔍  Failure Details*"]
            for sc in failed_scripts:
                reason = sc.failure_reason or "No reason captured"
                lines.append(f">  *{sc.script.split('/')[-1]}*")
                lines.append(f">  _{reason}_")
            blocks.append(_section("\n".join(lines)))

        blocks.append(_divider())

    # ── 4. Test Execution Summary ─────────────────────────────────────────────
    started_disp  = result.started_at.strftime("%d %b %Y  %H:%M:%S")
    finished_disp = (
        result.finished_at.strftime("%d %b %Y  %H:%M:%S")
        if result.finished_at else "—"
    )
    blocks.append(_section(
        "*📊  Test Execution Summary*\n"
        f"📋 *Total Scripts:* {result.total_scripts}   │   "
        f"✅ *Passed:* {result.total_passed}   │   "
        f"❌ *Failed:* {result.total_failed}   │   "
        f"⏭ *Skipped:* {result.total_skipped}\n"
        f"🕐 *Start:* {started_disp}   │   "
        f"🏁 *End:* {finished_disp}\n"
        f"⏱ *Duration:* {_fmt_duration(result.duration_s)}   │   "
        f"🌍 *Environment:* {result.environment.upper()}"
    ))
    blocks.append(_divider())

    # ── 5. Execution Configuration ────────────────────────────────────────────
    retry_val = f"ON (max {result.max_retries}×)" if result.retry_on_failure else "OFF"
    platform_label = _PLATFORM_LABELS.get(result.platform, result.platform.title())
    blocks.append(_section(
        "*⚙️  Execution Configuration*\n"
        f"⚡ *Parallel:* {'ON' if result.parallel else 'OFF'}   │   "
        f"🔁 *Retry on Failure:* {retry_val}   │   "
        f"♻️ *Rerun Suite on Fail:* {'ON' if result.rerun_on_failure else 'OFF'}   │   "
        f"📌 *Platform:* {platform_label}"
    ))

    # ── 6. Report location ────────────────────────────────────────────────────
    if result.report_json_path:
        report_name = os.path.basename(result.report_json_path)
        blocks.append(_divider())
        blocks.append(_section(
            f"📁  *Report File:*  `{report_name}`\n"
            f"📂  *Full Path:*    `{result.report_json_path}`"
        ))

    # ── 7. Footer ─────────────────────────────────────────────────────────────
    blocks.append(_context(
        f"_NLP-Playwright Automation Framework_   •   "
        f"_Plan: {result.plan_name}_   •   "
        f"_{result.started_at.strftime('%d %b %Y')}_"
    ))

    return blocks


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def send_report(
    result: PlanResult,
    webhook_url: str = "",
    channel: str = "",
    username: str = "",
    icon_emoji: str = "",
    force: bool = False,
) -> bool:
    """
    Send a Block Kit Slack notification for the completed plan run.

    Args:
        result      : PlanResult instance (populated by plan_runner)
        webhook_url : Override; falls back to settings.SLACK_WEBHOOK_URL
        channel     : Override; falls back to settings.SLACK_CHANNEL
        username    : Override; falls back to settings.SLACK_USERNAME
        icon_emoji  : Override; falls back to settings.SLACK_ICON_EMOJI
        force       : Send even if NOTIFY_ON_SLACK=false in settings

    Returns:
        True if message was sent successfully, False otherwise.
    """
    if not force and not settings.NOTIFY_ON_SLACK:
        logger.info("📵 Slack notifications disabled (NOTIFY_ON_SLACK=false). Skipping.")
        return False

    url    = webhook_url or settings.SLACK_WEBHOOK_URL
    ch     = channel     or settings.SLACK_CHANNEL
    user   = username    or settings.SLACK_USERNAME
    emoji  = icon_emoji  or settings.SLACK_ICON_EMOJI

    if not url:
        logger.warning("⚠️  SLACK_WEBHOOK_URL is not set — cannot send Slack notification.")
        return False

    payload = {
        "channel":    ch,
        "username":   user,
        "icon_emoji": emoji,
        "blocks":     _build_blocks(result),
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            if body.strip() == "ok":
                logger.info("✅ Slack notification sent to %s", ch)
                return True
            logger.warning("⚠️  Slack returned unexpected response: %s", body)
            return False
    except urllib.error.HTTPError as e:
        logger.error("❌ Slack HTTP error %s: %s", e.code, e.read().decode())
        return False
    except Exception as e:
        logger.error("❌ Failed to send Slack notification: %s", e)
        return False


def preview_blocks(result: PlanResult) -> str:
    """
    Return the Block Kit JSON as a formatted string (for debugging / dry-run).
    Does NOT send anything to Slack.
    """
    return json.dumps(_build_blocks(result), indent=2)
