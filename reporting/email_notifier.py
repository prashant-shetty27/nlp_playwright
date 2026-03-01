"""
reporting/email_notifier.py
Email report notifier — STUB (infrastructure ready, SMTP not yet wired).

When you're ready to activate:
  1. Set NOTIFY_ON_EMAIL=true in .env
  2. Set EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECIPIENTS
  3. Replace the NotImplementedError in _send_smtp() with the smtplib implementation below

Full SMTP implementation is commented at the bottom of this file.
"""
import logging
from typing import Optional
from config import settings
from reporting.slack_notifier import PlanResult   # reuse the same data model

logger = logging.getLogger(__name__)


def send_report(
    result: PlanResult,
    recipients: Optional[list[str]] = None,
    subject: Optional[str] = None,
    force: bool = False,
) -> bool:
    """
    Send an HTML email report for the completed plan run.

    Args:
        result      : PlanResult instance (same object used by slack_notifier)
        recipients  : Override list of email addresses; falls back to settings.EMAIL_RECIPIENTS
        subject     : Override email subject line
        force       : Send even if NOTIFY_ON_EMAIL=false in settings

    Returns:
        True if email was sent, False otherwise.
    """
    if not force and not settings.NOTIFY_ON_EMAIL:
        logger.info("📵 Email notifications disabled (NOTIFY_ON_EMAIL=false). Skipping.")
        return False

    to_list = recipients or [r.strip() for r in settings.EMAIL_RECIPIENTS.split(",") if r.strip()]
    if not to_list:
        logger.warning("⚠️  No email recipients configured — skipping email report.")
        return False

    if not settings.EMAIL_SMTP_HOST:
        logger.warning("⚠️  EMAIL_SMTP_HOST not set — cannot send email report.")
        return False

    email_subject = subject or _default_subject(result)
    html_body = _build_html(result)

    try:
        _send_smtp(
            to_list=to_list,
            subject=email_subject,
            html_body=html_body,
        )
        logger.info("✅ Email report sent to: %s", ", ".join(to_list))
        return True
    except NotImplementedError:
        logger.warning("⚠️  Email SMTP not yet implemented. Set up smtplib in _send_smtp().")
        return False
    except Exception as e:
        logger.error("❌ Failed to send email report: %s", e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _default_subject(result: PlanResult) -> str:
    icon = "✅" if result.total_failed == 0 else "❌"
    return (
        f"{icon} [{result.environment.upper()}] {result.plan_name} — "
        f"{result.total_passed}/{result.total_scripts} PASSED"
    )


def _build_html(result: PlanResult) -> str:
    """Generates a clean HTML email body from PlanResult."""
    rows = ""
    for suite in result.suites:
        rows += f"""
        <tr style="background:#f0f4ff">
          <td colspan="4" style="padding:8px 12px;font-weight:bold;font-size:14px">
            📦 {suite.suite_name} — {suite.overall_status}
          </td>
        </tr>"""
        for sc in suite.scripts:
            icon  = {"passed": "✅", "failed": "❌", "skipped": "⏭"}.get(sc.status, "❓")
            retry = f" (retried {sc.retries}x)" if sc.retries > 0 else ""
            reason = f"<br><small style='color:#c00'>{sc.failure_reason}</small>" if sc.failure_reason else ""
            rows += f"""
        <tr>
          <td style="padding:6px 12px">{icon}</td>
          <td style="padding:6px 12px;font-family:monospace">{sc.script.split('/')[-1]}</td>
          <td style="padding:6px 12px;font-weight:bold">{sc.status.upper()}{retry}</td>
          <td style="padding:6px 12px;color:#666">{sc.duration_s:.1f}s{reason}</td>
        </tr>"""

    overall_color = "#2e7d32" if result.total_failed == 0 else "#c62828"
    return f"""
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;padding:20px">
  <div style="background:{overall_color};color:#fff;padding:16px 20px;border-radius:8px 8px 0 0">
    <h2 style="margin:0">{'🟢' if result.total_failed == 0 else '🔴'} {result.plan_name}</h2>
    <p style="margin:4px 0 0;opacity:.9">
      {result.environment.upper()} &nbsp;|&nbsp;
      Parallel: {'ON' if result.parallel else 'OFF'} &nbsp;|&nbsp;
      Retry: {'ON (' + str(result.max_retries) + 'x)' if result.retry_on_failure else 'OFF'} &nbsp;|&nbsp;
      Rerun: {'ON' if result.rerun_on_failure else 'OFF'}
      {' &nbsp;|&nbsp; Owner: ' + result.owner if result.owner else ''}
    </p>
  </div>
  <table width="100%" cellpadding="0" cellspacing="0"
         style="border-collapse:collapse;border:1px solid #ddd">
    <thead>
      <tr style="background:#eeeeee">
        <th style="padding:8px 12px;text-align:left">Status</th>
        <th style="padding:8px 12px;text-align:left">Script</th>
        <th style="padding:8px 12px;text-align:left">Result</th>
        <th style="padding:8px 12px;text-align:left">Duration</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <div style="background:#f5f5f5;padding:12px 20px;border:1px solid #ddd;border-top:none;
              border-radius:0 0 8px 8px">
    <strong>📊 TOTAL: {result.total_scripts}</strong> &nbsp;&nbsp;
    ✅ {result.total_passed} PASSED &nbsp;&nbsp;
    ❌ {result.total_failed} FAILED &nbsp;&nbsp;
    ⏭ {result.total_skipped} SKIPPED &nbsp;&nbsp;
    ⏱ {result.duration_s:.1f}s
    <br><small style="color:#888">
      Started: {result.started_at.strftime('%d %b %Y %H:%M:%S')} &nbsp;|&nbsp;
      Sent by NLP-Playwright Automation Framework
    </small>
  </div>
</body>
</html>"""


def _send_smtp(to_list: list[str], subject: str, html_body: str) -> None:
    """
    ── FUTURE SMTP IMPLEMENTATION ────────────────────────────────────────────
    Uncomment and complete when ready to activate email:

    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = settings.EMAIL_SENDER
    msg["To"]      = ", ".join(to_list)
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(settings.EMAIL_SMTP_HOST, settings.EMAIL_SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(settings.EMAIL_SENDER, settings.EMAIL_PASSWORD)
        smtp.sendmail(settings.EMAIL_SENDER, to_list, msg.as_string())
    ─────────────────────────────────────────────────────────────────────────
    """
    raise NotImplementedError("SMTP not yet wired — see _send_smtp() stub above.")
