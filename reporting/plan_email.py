"""
reporting/plan_email.py — a plan run's report by email (HTML body + PDF attached).

SMTP settings come from .env (added by the owner; never logged):

    SMTP_HOST=smtp.gmail.com        # or the company relay
    SMTP_PORT=587
    SMTP_SECURITY=starttls          # starttls | ssl | none
    SMTP_USER=qa-reports@company.com
    SMTP_PASSWORD=...               # app password for Gmail / Google Workspace
    SMTP_FROM=QA Automation <qa-reports@company.com>   (optional, defaults to SMTP_USER)

Recipients are per plan: notify.email (on/off), notify.email_to ("a@x.com, b@y.com").
"""
from __future__ import annotations

import logging
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

import config.settings  # noqa: F401 — loads .env

logger = logging.getLogger(__name__)
_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def recipients(raw) -> list[str]:
    if isinstance(raw, (list, tuple)):
        raw = ",".join(raw)
    return [a.strip() for a in re.split(r"[,;\s]+", raw or "") if _EMAIL.match(a.strip())]


def configured() -> str:
    """'' when SMTP is set up, else what is missing."""
    missing = [k for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD") if not os.getenv(k, "").strip()]
    return ("Add " + ", ".join(missing) + " to .env") if missing else ""


def subject(rec: dict) -> str:
    from reporting.plan_report import collect, ist
    d = collect(rec)
    st = {"passed": "PASSED", "failed": "FAILED", "error": "ERROR", "stopped": "STOPPED",
          "missed": "MISSED"}.get(d["status"], d["status"].upper())
    if d.get("run_label"):
        st = f"{d['run_label'].upper()}][{st}"
    return (f"[{st}] {rec.get('plan_name')} — {d['t']['passed']}/{d['t']['total']} test cases · "
            f"{d['pass_pct']}% pass · {ist(rec.get('started_at') or rec.get('queued_at'), '%d %b %H:%M IST')}")


def send(rec: dict, to: list[str] | None = None, *, force: bool = False) -> dict:
    """Email the report. Stores {sent, to, error} on the run record as rec['email']."""
    from reporting.plan_slack import _notify_cfg
    cfg = _notify_cfg(rec)
    to = to or recipients(cfg.get("email_to"))
    result: dict
    if not force and not cfg.get("email"):
        return {"sent": False, "error": "Email is off for this plan"}
    if not force and cfg.get("when") == "failure" and rec.get("status") == "passed":
        return {"sent": False, "error": "only failures are reported"}
    if not to:
        result = {"sent": False, "error": "No recipients — add email addresses to the plan"}
    elif configured():
        result = {"sent": False, "error": configured()}
    else:
        try:
            from reporting.plan_report import email_html, ensure
            files = ensure(rec) if rec.get("items") else {}
            msg = EmailMessage()
            user = os.getenv("SMTP_USER", "").strip()
            sender = os.getenv("SMTP_FROM", "").strip() or formataddr(("QA Automation Reports", user))
            msg["From"] = sender
            msg["To"] = ", ".join(to)
            msg["Subject"] = subject(rec)
            msg.set_content(f"{subject(rec)}\n\nOpen the attached PDF for the full report.")
            msg.add_alternative(email_html(rec), subtype="html")
            for key, mime in (("pdf", ("application", "pdf")),):
                path = files.get(key)
                if path and os.path.exists(path):
                    with open(path, "rb") as f:
                        msg.add_attachment(f.read(), maintype=mime[0], subtype=mime[1],
                                           filename=os.path.basename(path))
            host = os.getenv("SMTP_HOST", "").strip()
            port = int(os.getenv("SMTP_PORT", "587") or 587)
            sec = (os.getenv("SMTP_SECURITY", "starttls") or "starttls").lower()
            ctx = ssl.create_default_context()
            if sec == "ssl":
                server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
            else:
                server = smtplib.SMTP(host, port, timeout=30)
                if sec == "starttls":
                    server.starttls(context=ctx)
            with server:
                server.login(user, os.getenv("SMTP_PASSWORD", "").strip().strip("'\""))
                server.send_message(msg, from_addr=parseaddr(sender)[1] or user, to_addrs=to)
            result = {"sent": True, "to": to, "error": "",
                      "attached": bool(files.get("pdf"))}
        except smtplib.SMTPAuthenticationError:
            result = {"sent": False, "to": to, "error": "SMTP login refused — check SMTP_USER / "
                      "SMTP_PASSWORD (Gmail needs an App Password)"}
        except Exception as e:  # noqa: BLE001
            result = {"sent": False, "to": to, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    (logger.info if result.get("sent") else logger.warning)(
        "✉️ Plan run %s email: %s", rec.get("id"), "sent" if result.get("sent") else result.get("error"))
    try:
        from execution import plan_engine
        rec["email"] = result
        if rec.get("id"):
            plan_engine._save(rec)
    except Exception:  # noqa: BLE001
        pass
    return result
