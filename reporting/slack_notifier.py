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
        parallel: bool = False,
        retry_on_failure: bool = False,
        max_retries: int = 0,
        rerun_on_failure: bool = False,
        owner: str = "",
        started_at: Optional[datetime] = None,
    ):
        self.plan_name = plan_name
        self.environment = environment
        self.parallel = parallel
        self.retry_on_failure = retry_on_failure
        self.max_retries = max_retries
        self.rerun_on_failure = rerun_on_failure
        self.owner = owner
        self.started_at = started_at or datetime.now()
        self.finished_at: Optional[datetime] = None
        self.suites: list[SuiteResult] = []

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


def _build_blocks(result: PlanResult) -> list[dict]:
    blocks: list[dict] = []

    # ── Header ────────────────────────────────────────────────────────────────
    overall = "ALL PASSED" if result.total_failed == 0 else "FAILURES DETECTED"
    blocks.append(_header(f"{result.overall_icon}  {result.plan_name}  —  {overall}"))

    # ── Plan metadata row ─────────────────────────────────────────────────────
    meta_parts = [
        f"🌍 *Env:* {result.environment.upper()}",
        f"⚡ *Parallel:* {'ON' if result.parallel else 'OFF'}",
        f"🔁 *Retry:* {'ON (' + str(result.max_retries) + 'x)' if result.retry_on_failure else 'OFF'}",
        f"♻️ *Rerun:* {'ON' if result.rerun_on_failure else 'OFF'}",
    ]
    if result.owner:
        meta_parts.append(f"👤 *Owner:* {result.owner}")
    blocks.append(_section("  |  ".join(meta_parts)))
    blocks.append(_divider())

    # ── Per-suite breakdown ───────────────────────────────────────────────────
    for suite in result.suites:
        blocks.append(_section(f"📦  *Suite: {suite.suite_name}*  —  {suite.overall_status}"))

        script_lines = []
        for sc in suite.scripts:
            name = sc.script.split("/")[-1]          # basename only
            dots = "." * max(2, 40 - len(name))
            dur  = f"{sc.duration_s:.1f}s"
            retry_tag = f"  ↩ retried {sc.retries}x" if sc.retries > 0 else ""
            fail_tag  = f"\n>  _{sc.failure_reason}_" if sc.failure_reason else ""
            script_lines.append(
                f"{sc.icon}  `{name}` {dots} *{sc.status.upper()}*  ({dur}){retry_tag}{fail_tag}"
            )

        blocks.append(_section("\n".join(script_lines)))
        blocks.append(_divider())

    # ── Summary totals ────────────────────────────────────────────────────────
    dur_str = f"{result.duration_s:.1f}s"
    ts_str  = result.started_at.strftime("%d %b %Y  %H:%M:%S")
    summary = (
        f"📊  *TOTAL: {result.total_scripts}*   "
        f"✅ {result.total_passed} PASSED   "
        f"❌ {result.total_failed} FAILED   "
        f"⏭ {result.total_skipped} SKIPPED\n"
        f"⏱  *Duration:* {dur_str}   🕐 *Started:* {ts_str}"
    )
    blocks.append(_section(summary))
    blocks.append(_context("_Sent by NLP-Playwright Automation Framework_"))

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
