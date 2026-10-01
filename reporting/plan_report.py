"""
reporting/plan_report.py — the professional report of one Test Plan run.

Three renderings of the same data:

    build_html(rec)        full HTML report (self-contained: CSS, charts and
                           failure screenshots inline) — opens in any browser,
                           can be attached to an email or uploaded to Slack
    build_pdf(rec)         the same report as an A4 PDF (headless Chromium),
                           page numbers in the footer — the file to forward
    email_html(rec)        a compact, email-client-safe summary (tables +
                           inline styles only; Gmail / Outlook render it)

generate(rec) writes data/plan_reports/<run id>/<Plan>_<date>.{html,pdf} and
returns their paths. Everything is read from the plan-run record and the
per-test-case report JSON files in data/logs — no re-run needed.
"""
from __future__ import annotations

import base64
import html
import io
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from config.settings import DATA_DIR, LOGS_DIR

logger = logging.getLogger(__name__)

REPORTS_DIR = os.path.join(DATA_DIR, "plan_reports")
SHOTS_DIR = os.path.join(DATA_DIR, "screenshots")
BRAND = os.getenv("REPORT_BRAND", "PS Codeless QA Automation")
from config.settings import portal_base_url as _pbu  # noqa: E402
BASE_URL = _pbu()

GREEN, RED, AMBER, GREY, BLUE, INK = "#16A34A", "#DC2626", "#D97706", "#94A3B8", "#2563EB", "#0F172A"
STATUS = {  # label, colour, soft background
    "passed": ("PASSED", GREEN, "#DCFCE7"), "failed": ("FAILED", RED, "#FEE2E2"),
    "error": ("ERROR", RED, "#FEE2E2"), "not_run": ("NOT RUN", "#64748B", "#F1F5F9"),
    "stopped": ("STOPPED", AMBER, "#FEF3C7"), "missed": ("MISSED", AMBER, "#FEF3C7"),
    "running": ("RUNNING", BLUE, "#DBEAFE"), "queued": ("QUEUED", AMBER, "#FEF3C7"),
    "pending": ("PENDING", "#64748B", "#F1F5F9"), "skipped": ("NOT RUN", "#64748B", "#F1F5F9"),
}


# ── helpers ─────────────────────────────────────────────────────────────────
def _ist_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Kolkata")
    except Exception:  # noqa: BLE001
        return timezone(timedelta(hours=5, minutes=30))


def ist(iso: str, fmt: str = "%d %b %Y, %H:%M IST") -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).astimezone(_ist_tz()).strftime(fmt)
    except ValueError:
        return iso


def mins(sec) -> str:
    try:
        return f"{float(sec) / 60:.1f} min"
    except (TypeError, ValueError):
        return "—"


def pretty(name: str) -> str:
    """NCT_Job_related_Questions -> NCT Job related Questions (display only)."""
    return (name or "").replace("_", " ").strip()


def e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _secs(a: str, b: str) -> float | None:
    try:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
    except (TypeError, ValueError):
        return None


def _thumb(rel: str, width: int = 480) -> str:
    """A screenshot as a data URI, downscaled so the report stays small."""
    path = os.path.join(SHOTS_DIR, rel.lstrip("/"))
    if not rel or not os.path.exists(path):
        return ""
    try:
        from PIL import Image
        im = Image.open(path)
        im.thumbnail((width, width * 3))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=72)
        data = buf.getvalue()
    except Exception:  # noqa: BLE001
        with open(path, "rb") as f:
            data = f.read()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


# ── data ────────────────────────────────────────────────────────────────────
def collect(rec: dict) -> dict:
    """Everything the report shows, computed once."""
    items = []
    for n, it in enumerate(rec.get("items") or [], 1):
        steps = []
        rf = it.get("report_file") or ""
        if rf and os.path.exists(os.path.join(LOGS_DIR, rf)):
            try:
                with open(os.path.join(LOGS_DIR, rf), "r", encoding="utf-8") as f:
                    rep = json.load(f)
                lines = rep.get("lines") or []
                if not lines:
                    try:
                        from api.routes.tests import _guess_lines
                        lines = _guess_lines(rep)
                    except Exception:  # noqa: BLE001
                        lines = []
                for i, r in enumerate(rep.get("results") or []):
                    steps.append({"no": i + 1, "line": lines[i] if i < len(lines) else None,
                                  "text": r.get("test_name", ""), "status": r.get("status", ""),
                                  "error": r.get("reason", ""), "ms": r.get("duration_ms"),
                                  "shot": r.get("screenshot", "")})
            except Exception:  # noqa: BLE001
                logger.exception("Could not read %s", rf)
        dur = it.get("duration_s")
        if dur is None and it.get("started_at") and it.get("finished_at"):
            dur = _secs(it["started_at"], it["finished_at"])
        items.append({**it, "n": n, "steps": steps, "dur": dur,
                      "failed_list": [s for s in steps if s["status"] == "failed"]})
    # Test cases with nothing tagged for this run type are "not in this run",
    # not "not run" — they must not drag a green Smoke run down to 50 %.
    scoped = [i for i in items if not i.get("out_of_scope")]
    t = {"total": len(scoped),
         "passed": sum(1 for i in scoped if i.get("status") == "passed"),
         "failed": sum(1 for i in scoped if i.get("status") == "failed"),
         "not_run": sum(1 for i in scoped if i.get("status") in ("not_run", "pending")),
         "out_of_scope": len(items) - len(scoped)}
    sp = sum(int(i.get("passed_steps") or 0) for i in items)
    sf = sum(int(i.get("failed_steps") or 0) for i in items)
    ss = sum(int(i.get("skipped_steps") or 0) for i in items)
    dur = rec.get("duration_s")
    if dur is None and rec.get("started_at") and rec.get("finished_at"):
        dur = _secs(rec["started_at"], rec["finished_at"])
    ex = rec.get("execution") or {}
    return {
        "rec": rec, "items": items, "t": t,
        "steps": {"passed": sp, "failed": sf, "skipped": ss, "total": sp + sf + ss},
        "pass_pct": round(100 * t["passed"] / t["total"]) if t["total"] else 0,
        "step_pct": round(100 * sp / (sp + sf + ss)) if (sp + sf + ss) else 0,
        "dur": dur,
        "platforms": ", ".join(sorted({i.get("platform") or "website" for i in items})) or "—",
        "suites": list(dict.fromkeys(i.get("suite", "") for i in items)),
        "trigger": ("Scheduled" if rec.get("trigger") == "schedule" else "Manual")
                   + f" · {rec.get('triggered_by') or '—'}",
        "browser": "Headless" if ex.get("headless") else "Visible (headed)",
        "retry": "Once" if ex.get("retry_failed") else "No",
        "status": rec.get("status", ""),
        "url": f"{BASE_URL}/plans/run/{rec.get('id', '')}",
        "run_type": rec.get("run_type") or ex.get("run_type") or "",
        "run_label": _type_label(rec.get("run_type") or ex.get("run_type") or ""),
        "quick": (rec.get("run_type") or ex.get("run_type")) in ("smoke", "sanity"),
    }


def _type_label(rt: str) -> str:
    return {"smoke": "Smoke", "sanity": "Sanity", "regression": "Regression", "full": "Full"}.get(rt, "")


# ── charts (inline SVG, print-safe) ─────────────────────────────────────────
def _donut(parts: list[tuple[int, str]], centre: str, sub: str, size: int = 150) -> str:
    total = sum(v for v, _ in parts) or 1
    r, c = 54, 2 * 3.14159265 * 54
    off, arcs = 0.0, []
    for v, colour in parts:
        if not v:
            continue
        ln = c * v / total
        arcs.append(f'<circle r="{r}" cx="75" cy="75" fill="none" stroke="{colour}" stroke-width="18" '
                    f'stroke-dasharray="{ln:.2f} {c - ln:.2f}" stroke-dashoffset="{-off:.2f}" '
                    f'transform="rotate(-90 75 75)"/>')
        off += ln
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 150 150">'
            f'<circle r="{r}" cx="75" cy="75" fill="none" stroke="#E2E8F0" stroke-width="18"/>'
            + "".join(arcs)
            + f'<text x="75" y="74" text-anchor="middle" font-size="26" font-weight="700" fill="{INK}">{e(centre)}</text>'
            f'<text x="75" y="95" text-anchor="middle" font-size="11" fill="#64748B">{e(sub)}</text></svg>')


def _bar(parts: list[tuple[int, str]], h: int = 10) -> str:
    total = sum(v for v, _ in parts) or 1
    segs = "".join(f'<div style="width:{100 * v / total:.2f}%;background:{c}"></div>' for v, c in parts if v)
    return f'<div class="bar" style="height:{h}px">{segs}</div>'


def _pill(status: str) -> str:
    label, fg, bg = STATUS.get(status, (status.upper(), "#64748B", "#F1F5F9"))
    return f'<span class="pill" style="color:{fg};background:{bg}">{e(label)}</span>'


# ── full report ─────────────────────────────────────────────────────────────
CSS = """
*{box-sizing:border-box} body{margin:0;background:#F1F5F9;color:#0F172A;
font-family:Inter,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45}
.page{max-width:1080px;margin:0 auto;background:#fff}
.hero{background:linear-gradient(120deg,#0F172A 0%,#1E3A8A 100%);color:#fff;padding:28px 36px 24px}
.brand{font-size:11px;letter-spacing:.14em;text-transform:uppercase;opacity:.75}
.hero h1{margin:6px 0 4px;font-size:26px;font-weight:700}
.hero .meta{opacity:.85;font-size:12px}
.hero .status{display:inline-block;margin-top:12px;padding:5px 14px;border-radius:999px;font-weight:700;
font-size:12px;letter-spacing:.06em}
.section{padding:22px 36px;border-bottom:1px solid #E2E8F0}
h2{break-after:avoid;font-size:15px;margin:0 0 12px;color:#0F172A;letter-spacing:.01em}
h2 small{font-weight:400;color:#64748B;font-size:12px;margin-left:6px}
.kpis{display:flex;gap:12px;flex-wrap:wrap}
.kpi{flex:1 1 150px;border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px}
.kpi .l{font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;color:#64748B}
.kpi .v{font-size:24px;font-weight:700;margin-top:2px}
.kpi .s{font-size:11.5px;color:#64748B}
.charts{display:flex;gap:28px;align-items:center;flex-wrap:wrap}
.legend div{margin:3px 0;font-size:12px}.dot{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px}
.bar{display:flex;width:100%;border-radius:5px;overflow:hidden;background:#E2E8F0}
.bar div{height:100%}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;color:#64748B;
background:#F8FAFC;padding:8px 10px;border-bottom:1px solid #E2E8F0}
td{padding:8px 10px;border-bottom:1px solid #F1F5F9;vertical-align:top}
.env td{border:none;padding:3px 10px 3px 0}.env td:first-child{color:#64748B;width:170px}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:10.5px;font-weight:700;letter-spacing:.05em;white-space:nowrap}
.mono{font-family:'SF Mono',Menlo,Consolas,monospace;font-size:11.5px;word-break:break-word}
.tc{font-weight:600}.muted{color:#64748B;font-size:11.5px}
.fail{border:1px solid #FECACA;background:#FEF2F2;border-left:4px solid #DC2626;border-radius:8px;padding:12px 14px;margin:10px 0}
.fail{display:flex;gap:14px;align-items:flex-start}.fail .ftxt{flex:1;min-width:0}
.fail img{max-width:230px;max-height:420px;object-fit:contain;object-position:top;border:1px solid #E2E8F0;border-radius:6px;flex:none;background:#fff}
.err{color:#B91C1C;white-space:pre-wrap;font-size:12px;margin-top:4px}
details{border:1px solid #E2E8F0;border-radius:8px;margin:10px 0;overflow:hidden}
summary{cursor:pointer;padding:10px 14px;background:#F8FAFC;font-weight:600;list-style:none}
summary::-webkit-details-marker{display:none}
.st-passed{color:#16A34A}.st-failed{color:#DC2626;font-weight:700}.st-skipped{color:#94A3B8}
tr.row-failed td{background:#FEF2F2}
.foot{padding:16px 36px;color:#64748B;font-size:11px;display:flex;justify-content:space-between}
a{color:#2563EB;text-decoration:none}
.ok{border:1px solid #BBF7D0;background:#F0FDF4;border-radius:8px;padding:12px 14px;color:#166534}
@media print{body{background:#fff}.page{max-width:none}.section{break-inside:auto}
 details{break-inside:auto}.fail{break-inside:avoid}tr{break-inside:avoid}
 summary{background:#F8FAFC !important}.hero{-webkit-print-color-adjust:exact;print-color-adjust:exact}
 *{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""


def build_html(rec: dict, *, for_pdf: bool = False) -> str:
    d = collect(rec)
    t, s, items = d["t"], d["steps"], d["items"]
    label, fg, bg = STATUS.get(d["status"], (d["status"].upper(), "#fff", "#334155"))
    out = [f'<!doctype html><html><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>{e(rec.get("plan_name"))} — Test Execution Report</title><style>{CSS}</style></head>'
           f'<body><div class="page">']
    # hero
    kind = f"{d['run_label']} Test Report" if d["run_label"] and d["run_label"] != "Full" else "Test Execution Report"
    out.append(f'''<div class="hero"><div class="brand">{e(BRAND)} · {e(kind)}</div>
      <h1>{e(rec.get("plan_name"))}</h1>
      <div class="meta">Run {e(rec.get("id"))} &nbsp;·&nbsp; {e(ist(rec.get("started_at") or rec.get("queued_at")))}
      &nbsp;·&nbsp; {e(d["trigger"])}</div>
      <div class="status" style="background:{bg};color:{fg}">{e(label)} &nbsp; {d["pass_pct"]}% PASS</div></div>''')
    # executive summary
    verdict = ("All test cases passed." if d["status"] == "passed" else
               f'{t["failed"]} of {t["total"]} test case(s) failed'
               + (f', {t["not_run"]} not run' if t["not_run"] else "") + ".")
    out.append(f'''<div class="section"><h2>Executive summary <small>{e(verdict)}</small></h2>
      <div class="kpis">
        <div class="kpi"><div class="l">Pass rate</div><div class="v" style="color:{GREEN if d["pass_pct"] == 100 else RED}">{d["pass_pct"]}%</div>
          <div class="s">{t["passed"]} of {t["total"]} test cases</div></div>
        <div class="kpi"><div class="l">Test cases</div><div class="v">{t["total"]}</div>
          <div class="s"><span style="color:{GREEN}">{t["passed"]} passed</span> · <span style="color:{RED}">{t["failed"]} failed</span> · {t["not_run"]} not run</div></div>
        <div class="kpi"><div class="l">Steps executed</div><div class="v">{s["total"]}</div>
          <div class="s"><span style="color:{GREEN}">{s["passed"]} passed</span> · <span style="color:{RED}">{s["failed"]} failed</span> · {s["skipped"]} not run</div></div>
        <div class="kpi"><div class="l">Duration</div><div class="v">{mins(d["dur"])}</div>
          <div class="s">{e(ist(rec.get("started_at"), "%H:%M"))} – {e(ist(rec.get("finished_at"), "%H:%M IST"))}</div></div>
      </div></div>''')
    # charts + environment
    out.append(f'''<div class="section"><div class="charts">
      {_donut([(t["passed"], GREEN), (t["failed"], RED), (t["not_run"], GREY)], f'{d["pass_pct"]}%', "test cases")}
      <div class="legend"><div><span class="dot" style="background:{GREEN}"></span>Passed — {t["passed"]}</div>
        <div><span class="dot" style="background:{RED}"></span>Failed — {t["failed"]}</div>
        <div><span class="dot" style="background:{GREY}"></span>Not run — {t["not_run"]}</div></div>
      <div style="flex:1 1 300px"><table class="env">
        <tr><td>Suites</td><td>{e(", ".join(d["suites"]))}</td></tr>
        <tr><td>Platform</td><td>{e(d["platforms"])}</td></tr>
        <tr><td>Browser</td><td>{e(d["browser"])}</td></tr>
        <tr><td>Retry failed test case</td><td>{e(d["retry"])}</td></tr>
        <tr><td>Triggered</td><td>{e(d["trigger"])}</td></tr>
        <tr><td>Step pass rate</td><td>{d["step_pct"]}% {_bar([(s["passed"], GREEN), (s["failed"], RED), (s["skipped"], GREY)], 8)}</td></tr>
      </table></div></div></div>''')
    # test case summary table
    rows = []
    for it in items:
        p, f, k = (int(it.get(x) or 0) for x in ("passed_steps", "failed_steps", "skipped_steps"))
        note = it.get("note") or (it.get("reason") if it.get("status") == "not_run" else "")
        rows.append(f'''<tr class="{'row-failed' if it.get('status') == 'failed' else ''}">
          <td class="muted">{it["n"]}</td>
          <td><div class="tc">{e(pretty(it.get("test_case")))}</div><div class="muted mono">{e(it.get("test_case"))}</div>
              {f'<div class="muted">{e(note)}</div>' if note else ''}</td>
          <td>{e(it.get("suite"))}<div class="muted">{e(it.get("platform") or "website")}</div></td>
          <td>{_pill(it.get("status", ""))}</td>
          <td style="min-width:120px"><span style="color:{GREEN}">{p}</span> / <span style="color:{RED}">{f}</span> / {k}
              {_bar([(p, GREEN), (f, RED), (k, GREY)], 6)}</td>
          <td class="mono">{mins(it["dur"]) if it["dur"] is not None else "—"}</td>
          <td>{e(it.get("attempt") or 1)}</td></tr>''')
    out.append(f'''<div class="section"><h2>Test case results</h2><table>
      <tr><th>#</th><th>Test case</th><th>Suite</th><th>Result</th><th>Steps ✓ / ✗ / ⏭</th><th>Duration</th><th>Attempt</th></tr>
      {"".join(rows)}</table></div>''')
    # failure analysis
    fails = [it for it in items if it.get("status") in ("failed", "not_run") and (it["failed_list"] or it.get("reason"))]
    out.append('<div class="section"><h2>Failure analysis</h2>')
    if not fails:
        out.append('<div class="ok">✔ No failures in this run.</div>')
    for it in fails:
        if not it["failed_list"]:
            out.append(f'<div class="fail"><div class="tc">{e(pretty(it.get("test_case")))}</div>'
                       f'<div class="err">{e(it.get("reason"))}</div></div>')
        for st in it["failed_list"][:5]:
            img = _thumb(st["shot"]) if st.get("shot") else ""
            out.append(f'''<div class="fail"><div class="ftxt"><div class="tc">{e(pretty(it.get("test_case")))}
              <span class="muted"> · step {st["no"]}{f' · line {st["line"]}' if st.get("line") else ''}</span></div>
              <div class="mono" style="margin-top:4px">{e(st["text"])}</div>
              <div class="err">{e(st["error"][:1500])}</div></div>
              {f'<a href="{img}"><img src="{img}" alt="screenshot at failure"></a>' if img else ''}</div>''')
    out.append('</div>')
    # step details — a Smoke / Sanity report is the quick one: summary + failures
    if d["quick"]:
        out.append(f'''<div class="foot"><div>Generated by {e(BRAND)} · {e(ist(datetime.now(timezone.utc).isoformat()))}
          · quick {e(d["run_label"])} report (step-by-step details are in the portal)</div>
          <div><a href="{e(d["url"])}">Open this run in the portal</a></div></div></div></body></html>''')
        return "".join(out)
    out.append('<div class="section"><h2>Step details</h2>')
    for it in items:
        if not it["steps"]:
            continue
        rows = "".join(
            f'<tr class="{"row-failed" if st["status"] == "failed" else ""}"><td class="muted">{st["no"]}</td>'
            f'<td class="muted">{e(st["line"] or "")}</td><td class="mono">{e(st["text"])}'
            + (f'<div class="err">{e(st["error"][:600])}</div>' if st["status"] in ("failed", "skipped") and st["error"] else "")
            + f'</td><td class="st-{e(st["status"])}">{e(STATUS.get(st["status"], (st["status"],))[0])}</td>'
            f'<td class="mono" style="white-space:nowrap">{(st["ms"] or 0) / 1000:.1f}s</td></tr>' for st in it["steps"])
        open_ = " open" if for_pdf or it.get("status") == "failed" else ""
        out.append(f'''<details{open_}><summary>{_pill(it.get("status", ""))} &nbsp;{e(pretty(it.get("test_case")))}
            <span class="muted"> · {len(it["steps"])} steps · {mins(it["dur"]) if it["dur"] is not None else "—"}</span></summary>
            <table><tr><th>#</th><th>Line</th><th>Step</th><th>Result</th><th>Time</th></tr>{rows}</table></details>''')
    out.append('</div>')
    out.append(f'''<div class="foot"><div>Generated by {e(BRAND)} · {e(ist(datetime.now(timezone.utc).isoformat()))}</div>
      <div><a href="{e(d["url"])}">Open this run in the portal</a></div></div></div></body></html>''')
    return "".join(out)


# ── email body (Gmail / Outlook safe: tables + inline styles) ───────────────
def email_html(rec: dict, *, note: str = "") -> str:
    d = collect(rec)
    t, s = d["t"], d["steps"]
    label, fg, bg = STATUS.get(d["status"], (d["status"].upper(), "#fff", "#334155"))
    td = 'style="padding:8px 10px;border-bottom:1px solid #EEF2F7;font-family:Arial,sans-serif;font-size:13px;color:#0F172A"'
    rows = ""
    for it in d["items"]:
        l2, f2, b2 = STATUS.get(it.get("status", ""), ("", "#64748B", "#F1F5F9"))
        p, f = int(it.get("passed_steps") or 0), int(it.get("failed_steps") or 0)
        ff = ""
        if it.get("status") == "failed" and it.get("first_failure"):
            ff = (f'<div style="color:#B91C1C;font-size:12px;margin-top:3px">'
                  f'{e(it["first_failure"][:300])}</div>')
        rows += (f'<tr><td {td}><b>{e(pretty(it.get("test_case")))}</b>{ff}</td>'
                 f'<td {td}><span style="background:{b2};color:{f2};padding:2px 8px;border-radius:10px;'
                 f'font-size:11px;font-weight:bold">{e(l2)}</span></td>'
                 f'<td {td}>{p} / {f}</td><td {td}>{mins(it["dur"]) if it["dur"] is not None else "—"}</td></tr>')

    def kpi(lbl, val, colour=INK):
        return (f'<td align="center" style="padding:12px 6px;border:1px solid #E2E8F0;border-radius:8px;'
                f'font-family:Arial,sans-serif"><div style="font-size:11px;color:#64748B;text-transform:uppercase;'
                f'letter-spacing:1px">{lbl}</div><div style="font-size:22px;font-weight:bold;color:{colour}">'
                f'{val}</div></td>')
    return f'''<!doctype html><html><body style="margin:0;padding:0;background:#F1F5F9">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F1F5F9"><tr><td align="center" style="padding:20px 10px">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;background:#ffffff;border-radius:10px;overflow:hidden">
<tr><td style="background:#0F172A;padding:22px 26px;font-family:Arial,sans-serif;color:#ffffff">
  <div style="font-size:11px;letter-spacing:2px;text-transform:uppercase;opacity:.7">{e(BRAND)}</div>
  <div style="font-size:21px;font-weight:bold;margin:6px 0 4px">{e(rec.get("plan_name"))}</div>
  <div style="font-size:12px;opacity:.8">{e(ist(rec.get("started_at") or rec.get("queued_at")))} · {e(d["trigger"])}</div>
  <div style="margin-top:12px"><span style="background:{bg};color:{fg};padding:5px 14px;border-radius:14px;font-size:12px;font-weight:bold">{e(label)} · {d["pass_pct"]}% PASS</span></div>
</td></tr>
{f'<tr><td style="padding:14px 26px 0;font-family:Arial,sans-serif;font-size:13px;color:#334155">{e(note)}</td></tr>' if note else ''}
<tr><td style="padding:18px 20px 6px"><table role="presentation" width="100%" cellpadding="0" cellspacing="6"><tr>
  {kpi("Pass rate", f'{d["pass_pct"]}%', GREEN if d["pass_pct"] == 100 else RED)}
  {kpi("Test cases", f'{t["passed"]}/{t["total"]}')}
  {kpi("Steps", f'{s["passed"]}/{s["total"]}')}
  {kpi("Duration", mins(d["dur"]))}
</tr></table></td></tr>
<tr><td style="padding:8px 26px 4px;font-family:Arial,sans-serif;font-size:14px;font-weight:bold;color:#0F172A">Test case results</td></tr>
<tr><td style="padding:0 26px 10px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #E2E8F0;border-radius:8px">
  <tr style="background:#F8FAFC"><td {td}><b style="font-size:11px;color:#64748B">TEST CASE</b></td><td {td}><b style="font-size:11px;color:#64748B">RESULT</b></td>
  <td {td}><b style="font-size:11px;color:#64748B">STEPS ✓/✗</b></td><td {td}><b style="font-size:11px;color:#64748B">DURATION</b></td></tr>
  {rows}</table></td></tr>
<tr><td style="padding:6px 26px 18px;font-family:Arial,sans-serif;font-size:12px;color:#64748B">
  Platform: {e(d["platforms"])} · Browser: {e(d["browser"])} · Run {e(rec.get("id"))}<br>
  The full report with step details and failure screenshots is attached (PDF).
</td></tr>
<tr><td align="center" style="padding:0 26px 24px"><a href="{e(d["url"])}" style="background:#2563EB;color:#ffffff;text-decoration:none;
  padding:10px 20px;border-radius:6px;font-family:Arial,sans-serif;font-size:13px;font-weight:bold;display:inline-block">Open run in portal</a>
  <div style="font-family:Arial,sans-serif;font-size:11px;color:#94A3B8;margin-top:6px">(link works on the QA machine / office network)</div></td></tr>
</table></td></tr></table></body></html>'''


# ── files ───────────────────────────────────────────────────────────────────
def _base(rec: dict) -> str:
    stamp = ist(rec.get("started_at") or rec.get("queued_at"), "%Y-%m-%d_%H%M")
    safe = "".join(ch if ch.isalnum() else "_" for ch in (rec.get("plan_name") or "Plan")).strip("_")
    return os.path.join(REPORTS_DIR, os.path.basename(rec["id"]), f"{safe}_{stamp}")


def build_pdf(html_text: str, pdf_path: str, title: str = "") -> str:
    """Render the HTML with headless Chromium (own thread-safe Playwright)."""
    from playwright.sync_api import sync_playwright
    foot = ('<div style="font-size:8px;color:#94A3B8;width:100%;padding:0 12mm;display:flex;'
            'justify-content:space-between;font-family:Arial"><span>' + e(BRAND) + ' · ' + e(title) +
            '</span><span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>')
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            pg = b.new_page()
            pg.set_content(html_text, wait_until="load")
            pg.emulate_media(media="print")
            pg.pdf(path=pdf_path, format="A4", print_background=True, display_header_footer=True,
                   header_template="<div></div>", footer_template=foot,
                   margin={"top": "10mm", "bottom": "14mm", "left": "8mm", "right": "8mm"})
        finally:
            b.close()
    return pdf_path


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
#: A PDF that failed once is not retried on every page view (each try starts a
#: Chromium); "Rebuild report" (regenerate) tries again.
_PDF_FAILED: dict[str, str] = {}


def _lock_for(run_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(run_id, threading.Lock())


def generate(rec: dict, *, pdf: bool = True) -> dict:
    """Write the HTML (and PDF) report; returns {"html": path, "pdf": path|"", "error": ""}.
    Files are written to a temp name and moved into place, so a download that
    overlaps a rebuild never gets a half-written file."""
    base = _base(rec)
    os.makedirs(os.path.dirname(base), exist_ok=True)
    with _lock_for(rec.get("id", base)):
        out = {"html": base + ".html", "pdf": "", "error": ""}
        tmp = base + ".html.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(build_html(rec))
        os.replace(tmp, out["html"])
        if pdf:
            try:
                build_pdf(build_html(rec, for_pdf=True), base + ".pdf.tmp", rec.get("plan_name", ""))
                os.replace(base + ".pdf.tmp", base + ".pdf")
                out["pdf"] = base + ".pdf"
                _PDF_FAILED.pop(rec.get("id", ""), None)
            except Exception as ex:  # noqa: BLE001
                logger.exception("PDF report failed")
                out["error"] = f"PDF not created: {type(ex).__name__}: {str(ex)[:200]}"
                _PDF_FAILED[rec.get("id", "")] = out["error"]
        return out


def existing(rec: dict) -> dict:
    base = _base(rec)
    return {"html": base + ".html" if os.path.exists(base + ".html") else "",
            "pdf": base + ".pdf" if os.path.exists(base + ".pdf") else ""}


def ensure(rec: dict, *, need_pdf: bool = True) -> dict:
    """The report files for a finished run, generating only what is missing."""
    have = existing(rec)
    if have["html"] and (have["pdf"] or not need_pdf):
        return {**have, "error": ""}
    if have["html"] and _PDF_FAILED.get(rec.get("id", "")):
        return {**have, "error": _PDF_FAILED[rec["id"]]}
    return generate(rec, pdf=need_pdf or not have["pdf"])
