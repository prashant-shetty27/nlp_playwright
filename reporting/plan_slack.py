"""
reporting/plan_slack.py — a plan run's summary, posted to Slack.

Needs a bot token (SLACK_BOT_TOKEN, scope chat:write, bot invited to the
channel); posts to the plan's channel id — default C0AAP4882H4
(#b2b-selenium-reports). The old SLACK_WEBHOOK_URL is deliberately not used:
a webhook posts to the channel it was created for, not the plan's channel.

The token is read from the environment (.env) and never logged or stored.
"""
from __future__ import annotations

import logging
import os

import config.settings  # noqa: F401 — loads .env

logger = logging.getLogger(__name__)
BASE_URL = os.getenv("PORTAL_BASE_URL", "http://localhost:8100")
ICON = {"passed": "✅", "failed": "❌", "not_run": "⏭️", "stopped": "⏹️",
        "missed": "⏰", "error": "💥", "running": "🔄", "pending": "⏳"}


def _notify_cfg(rec: dict) -> dict:
    cfg = rec.get("notify")
    if cfg:
        return cfg
    try:
        from core import plans
        return plans.get(rec["plan_id"])["notify"]
    except Exception:  # noqa: BLE001
        return {}


def _fmt_dur(sec) -> str:
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        return ""
    return f"{sec / 60:.1f} min"


def _mins(sec) -> str:
    try:
        return f"{float(sec) / 60:.1f} min"
    except (TypeError, ValueError):
        return "—"


def _pretty(name: str) -> str:
    return (name or "").replace("_", " ").strip()


def _meter(passed: int, failed: int, other: int, width: int = 10) -> str:
    """🟩🟩🟩🟩🟩🟩🟩🟥🟥⬜ — share of passed / failed / not run."""
    total = passed + failed + other
    if not total:
        return "⬜" * width
    g = round(width * passed / total)
    r = round(width * failed / total)
    if failed and not r:
        r = 1
    g = min(g, width - r)
    return "🟩" * g + "🟥" * r + "⬜" * (width - g - r)


def build_blocks(rec: dict) -> tuple[str, list[dict]]:
    """A Slack message that reads like a report card, not a log line."""
    from reporting.plan_report import collect, ist
    d = collect(rec)
    st, t, s = d["status"], d["t"], d["steps"]
    label = {"passed": "PASSED", "failed": "FAILED", "error": "ERROR", "stopped": "STOPPED",
             "missed": "MISSED", "running": "RUNNING"}.get(st, st.upper())
    rt = d.get("run_label") or ""
    if rt:
        label = f"{rt.upper()} · {label}"
    text = f"{ICON.get(st, '')} {rec.get('plan_name')} — {label} ({d['pass_pct']}% pass)"
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "emoji": True,
                                    "text": f"{ICON.get(st, '•')} {rec.get('plan_name')} — {label}"[:150]}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text":
            f"*Test Execution Report* · {ist(rec.get('started_at') or rec.get('queued_at'))} · {d['trigger']}"}]},
    ]
    if st == "missed":
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"⏰ {rec.get('reason', '')}"}})
        return text, blocks
    blocks += [
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Pass rate*\n{_meter(t['passed'], t['failed'], t['not_run'])}  *{d['pass_pct']}%*"},
            {"type": "mrkdwn", "text": f"*Duration*\n⏱️ {_mins(d['dur'])}"},
            {"type": "mrkdwn", "text": f"*Test cases*\n✅ {t['passed']}   ❌ {t['failed']}   ⏭️ {t['not_run']}   of {t['total']}"},
            {"type": "mrkdwn", "text": f"*Steps*\n✅ {s['passed']}   ❌ {s['failed']}   ⏭️ {s['skipped']}   of {s['total']}"},
            {"type": "mrkdwn", "text": f"*Platform*\n{d['platforms']} · {d['browser'].lower()}"},
            {"type": "mrkdwn", "text": f"*Suite(s)*\n{', '.join(d['suites'])[:200]}"},
        ]},
        {"type": "divider"},
    ]
    lines = []
    for it in d["items"]:
        p, f = int(it.get("passed_steps") or 0), int(it.get("failed_steps") or 0)
        k = int(it.get("skipped_steps") or 0)
        steps = f"{p}/{p + f + k} steps" if it.get("passed_steps") is not None else "—"
        dur = _mins(it["dur"]) if it["dur"] is not None else "—"
        link = f"  <{BASE_URL}/reports/{it['run_id']}|details>" if it.get("run_id") else ""
        note = f"  _({it['note']})_" if it.get("note") else ""
        lines.append(f"{ICON.get(it.get('status'), '•')}  *{_pretty(it.get('test_case'))}*   "
                     f"{steps} · {dur}{note}{link}")
    chunk = "*Test case results*\n"
    for ln in lines:                      # Slack section text limit ~3000
        if len(chunk) + len(ln) > 2800:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
            chunk = ""
        chunk += ln + "\n"
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
    fails = [it for it in d["items"] if it.get("status") in ("failed", "not_run")]
    if fails:
        out = "*Why it failed*\n"
        for it in fails[:6]:
            why = it.get("first_failure") or it.get("reason") or ""
            out += f"❌ *{_pretty(it.get('test_case'))}*\n>{why[:280]}\n"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": out[:2900]}})
    blocks.append({"type": "actions", "elements": [
        {"type": "button", "text": {"type": "plain_text", "text": "📊 View execution"},
         "url": d["url"], **({"style": "primary"} if st == "passed" else {"style": "danger"})},
        {"type": "button", "text": {"type": "plain_text", "text": "📄 Download PDF report"},
         "url": f"{BASE_URL}/testplans/runs/{rec.get('id')}/report?format=pdf"},
    ]})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
        f"Run `{rec.get('id')}` · full PDF report in the thread 🧵 · links open on the QA machine"}]})
    return text, blocks


def _api(token: str, method: str, **kw):
    import httpx
    r = httpx.post(f"https://slack.com/api/{method}", timeout=60,
                   headers={"Authorization": f"Bearer {token}"}, **kw)
    return r.json()


def upload_file(token: str, channel: str, path: str, *, title: str = "", thread_ts: str = "",
                comment: str = "") -> dict:
    """Upload a file to a channel/thread (Slack's external-upload flow; needs files:write)."""
    import httpx
    size = os.path.getsize(path)
    name = os.path.basename(path)
    a = _api(token, "files.getUploadURLExternal", data={"filename": name, "length": str(size)})
    if not a.get("ok"):
        return {"ok": False, "error": a.get("error", "getUploadURLExternal failed")}
    with open(path, "rb") as f:
        up = httpx.post(a["upload_url"], content=f.read(), timeout=120)
    if up.status_code >= 300:
        return {"ok": False, "error": f"upload HTTP {up.status_code}"}
    payload = {"files": [{"id": a["file_id"], "title": title or name}], "channel_id": channel}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    if comment:
        payload["initial_comment"] = comment
    c = _api(token, "files.completeUploadExternal", json=payload)
    return {"ok": bool(c.get("ok")), "error": c.get("error", "")}


def notify(rec: dict, *, force: bool = False) -> dict:
    """Post the summary, then the PDF report in its thread. Stores {sent, ...} on the record."""
    cfg = _notify_cfg(rec)
    if not force and not cfg.get("slack"):
        return {"sent": False, "error": "Slack is off for this plan"}
    if not force and cfg.get("when") == "failure" and rec.get("status") == "passed":
        return {"sent": False, "error": "only failures are reported"}
    channel = cfg.get("channel") or os.getenv("SLACK_REPORT_CHANNEL", "C0AAP4882H4")
    token = os.getenv("SLACK_BOT_TOKEN", "").strip().strip("'\"")
    result: dict
    try:
        if not token:
            # No webhook fallback: a webhook posts to the channel it was made
            # for, so results would silently land somewhere else.
            result = {"sent": False, "error": "SLACK_BOT_TOKEN is not set in .env"}
        else:
            text, blocks = build_blocks(rec)
            data = _api(token, "chat.postMessage", json={"channel": channel, "text": text,
                                                          "blocks": blocks, "unfurl_links": False})
            if not data.get("ok") and data.get("error") == "invalid_blocks":
                # Some workspaces refuse non-public button URLs; keep the report, drop the buttons.
                blocks = [b for b in blocks if b.get("type") != "actions"]
                data = _api(token, "chat.postMessage", json={"channel": channel, "text": text,
                                                              "blocks": blocks, "unfurl_links": False})
            result = {"sent": bool(data.get("ok")), "via": "bot", "channel": channel,
                      "error": "" if data.get("ok") else data.get("error", "post failed")}
            if data.get("ok") and rec.get("status") not in ("missed",) and rec.get("items"):
                try:
                    from reporting.plan_report import ensure
                    files = ensure(rec)
                    if files.get("pdf"):
                        up = upload_file(token, data.get("channel") or channel, files["pdf"],
                                         title=f"{rec.get('plan_name')} — Test Execution Report",
                                         thread_ts=data.get("ts", ""),
                                         comment="📄 Full report: summary, failure analysis with "
                                                 "screenshots, and every step.")
                        result["file"] = "attached" if up["ok"] else up["error"]
                        if not up["ok"] and up["error"] == "missing_scope":
                            result["warning"] = ("PDF not attached — add the files:write scope to the "
                                                 "Slack app and reinstall it")
                    elif files.get("error"):
                        result["file"] = files["error"]
                except Exception as ex:  # noqa: BLE001
                    logger.exception("Report attachment failed")
                    result["file"] = f"{type(ex).__name__}: {str(ex)[:150]}"
    except Exception as ex:  # noqa: BLE001
        result = {"sent": False, "error": f"{type(ex).__name__}: {str(ex)[:200]}"}
    if result.get("sent"):
        logger.info("📣 Plan run %s posted to Slack (file: %s)", rec.get("id"), result.get("file"))
    else:
        logger.warning("📣 Slack post for %s not sent: %s", rec.get("id"), result.get("error"))
    try:
        from execution import plan_engine
        rec["slack"] = result
        if rec.get("id"):
            plan_engine._save(rec)
    except Exception:  # noqa: BLE001
        pass
    return result


def alert_stuck(rec: dict, h: dict) -> dict:
    """One Slack message when a plan run stops moving (not repeated per tick)."""
    import httpx

    cfg = _notify_cfg(rec)
    if not cfg.get("slack"):
        return {"sent": False, "error": "Slack is off for this plan"}
    token = os.getenv("SLACK_BOT_TOKEN", "").strip().strip("'\"")
    if not token:
        return {"sent": False, "error": "SLACK_BOT_TOKEN is not set in .env"}
    channel = cfg.get("channel") or os.getenv("SLACK_REPORT_CHANNEL", "C0AAP4882H4")
    text = (f"⚠️ Test Plan *{rec.get('plan_name')}* looks stuck — {h.get('message', '')}\n"
            f"Open <{BASE_URL}/plans/run/{rec.get('id')}|the plan run> to see the live view and "
            f"stop or re-run it.")
    try:
        r = httpx.post("https://slack.com/api/chat.postMessage", timeout=20,
                       headers={"Authorization": f"Bearer {token}"},
                       json={"channel": channel, "text": text, "unfurl_links": False})
        data = r.json()
        return {"sent": bool(data.get("ok")), "error": data.get("error", "")}
    except Exception as e:  # noqa: BLE001
        return {"sent": False, "error": str(e)[:200]}
