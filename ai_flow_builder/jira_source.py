"""
ai_flow_builder/jira_source.py — the ticket behind a prompt.

A tester pastes a Jira link and expects the drafter to know the story. Until
now it did not: the link was harvested as a value and the model drafted from
the one sentence around it, so a story with a five-level priority hierarchy
became "verify the heading is displayed".

This module fetches, through Jira's REST API, everything a tester would read
before writing cases:

  the story          summary, description (acceptance criteria live here)
  its sub-tasks      testing / design / FE / API / data / UAT — each with its
                     own description, because the API sub-task is where field
                     names and endpoints are written down
  linked issues      defects and concerns raised against it, closed or not —
                     each one is a regression case waiting to be written
  comments           the latest few, minus automation noise — this is where
                     "API is live, FE tomorrow" and test URLs get posted

and renders it as one plain-text brief for the model, plus a structured dict
the UI can show ("drafted from GJDT-22686 + 6 sub-tasks + 5 linked issues").

Credentials come from .env, never from a prompt:

    JIRA_BASE_URL=https://jdjira.justdial.com
    JIRA_PAT=<personal access token>           # preferred (Bearer); JIRA_TOKEN also accepted
    # or
    JIRA_USERNAME=<user>  JIRA_PASSWORD=<pass>  # basic auth

Nothing is cached to disk: a ticket changes daily while it is being tested,
and a stale brief would draft against yesterday's acceptance criteria.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: A Jira issue key as written anywhere in a prompt: bare (GJDT-22686) or in a
#: browse URL. Only keys of the configured Jira are fetched, so a stray
#: "ABC-123" in running text costs nothing.
_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d{1,7})\b")
_BROWSE_RE = re.compile(r"https?://[^\s/]+/browse/([A-Z][A-Z0-9]{1,9}-\d{1,7})", re.I)

#: Comment authors that add nothing a tester needs.
_NOISE_AUTHORS = ("jira automation", "gitlab", "bitbucket", "jenkins")

#: How a sub-task is classified from its summary, for the brief's headings.
_SUBTASK_KINDS = [
    ("api",      r"\bapi\b|backend|endpoint|service changes"),
    ("fe",       r"^fe\b|front[- ]?end|\bui\b|touch|wap|android|ios"),
    ("design",   r"design"),
    ("testing",  r"test|qa|uat|security"),
    ("data",     r"data|db|migration|analytics|impression"),
]


class JiraError(RuntimeError):
    """A ticket could not be read; the message says what to fix."""


@dataclass
class Issue:
    key: str
    summary: str = ""
    type: str = ""
    status: str = ""
    description: str = ""
    labels: list[str] = field(default_factory=list)
    kind: str = ""              # sub-task classification, when it is one
    link_type: str = ""         # "Relates", "Blocks"… when it is a linked issue


@dataclass
class TicketBrief:
    story: Issue
    subtasks: list[Issue] = field(default_factory=list)
    linked: list[Issue] = field(default_factory=list)
    comments: list[tuple[str, str, str]] = field(default_factory=list)  # (when, who, text)
    attachments: list[str] = field(default_factory=list)
    #: Spreadsheet attachments, already rendered to text tables.
    sheets: list[str] = field(default_factory=list)
    url: str = ""

    def summary_line(self) -> str:
        return (f"{self.story.key} ({self.story.type}, {self.story.status}): "
                f"{self.story.summary} — {len(self.subtasks)} sub-task(s), "
                f"{len(self.linked)} linked issue(s), {len(self.comments)} comment(s)")

    def as_text(self, max_chars: int = 24000) -> str:
        """The brief the model reads. Trimmed from the least useful end first."""
        s = self.story
        parts = [f"=== JIRA STORY {s.key} — {s.summary}",
                 f"Type: {s.type} | Status: {s.status} | Labels: {', '.join(s.labels) or '-'}",
                 f"Link: {self.url}", "",
                 "--- Description / acceptance criteria ---",
                 _wiki_to_text(s.description) or "(empty)", ""]
        if self.subtasks:
            parts.append("--- Sub-tasks (what each team changed or tested) ---")
            for t in self.subtasks:
                parts.append(f"* {t.key} [{t.kind or 'other'}] ({t.status}) {t.summary}")
                body = _wiki_to_text(t.description)
                if body:
                    parts.append(_indent(body[:2500]))
            parts.append("")
        if self.linked:
            parts.append("--- Linked defects / concerns (each is a regression case) ---")
            for t in self.linked:
                parts.append(f"* {t.key} [{t.type}, {t.status}, {t.link_type}] {t.summary}")
                body = _wiki_to_text(t.description)
                if body:
                    parts.append(_indent(body[:1500]))
            parts.append("")
        if self.comments:
            parts.append("--- Latest comments (newest first) ---")
            for when, who, text in self.comments:
                parts.append(f"* {when} {who}: {_wiki_to_text(text)[:700]}")
            parts.append("")
        if self.attachments:
            parts.append("--- Attachments ---")
            parts += [f"* {a}" for a in self.attachments]
        for sheet in self.sheets:
            parts += ["", sheet]
        text = "\n".join(parts)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n… (brief trimmed)"
        return text

    def as_dict(self) -> dict:
        return {
            "key": self.story.key, "summary": self.story.summary,
            "type": self.story.type, "status": self.story.status, "url": self.url,
            "subtasks": [{"key": t.key, "kind": t.kind, "status": t.status,
                          "summary": t.summary} for t in self.subtasks],
            "linked": [{"key": t.key, "type": t.type, "status": t.status,
                        "summary": t.summary} for t in self.linked],
            "comments": len(self.comments), "attachments": self.attachments,
        }


# ── configuration ────────────────────────────────────────────────────────────
def base_url() -> str:
    return (os.getenv("JIRA_BASE_URL") or "https://jdjira.justdial.com").rstrip("/")


def configured() -> bool:
    return bool(os.getenv("JIRA_PAT") or os.getenv("JIRA_TOKEN")
                or (os.getenv("JIRA_USERNAME") and os.getenv("JIRA_PASSWORD")))


def _session():
    import requests

    s = requests.Session()
    # JIRA_PAT is the documented name; JIRA_TOKEN is accepted because it is
    # what people naturally write.
    pat = (os.getenv("JIRA_PAT") or os.getenv("JIRA_TOKEN") or "").strip().strip("'\"")
    if pat:
        s.headers["Authorization"] = f"Bearer {pat}"
    elif os.getenv("JIRA_USERNAME") and os.getenv("JIRA_PASSWORD"):
        s.auth = (os.getenv("JIRA_USERNAME"), os.getenv("JIRA_PASSWORD"))
    else:
        raise JiraError(
            "Jira is not configured. Add JIRA_PAT=<personal access token> (or "
            "JIRA_USERNAME / JIRA_PASSWORD) and optionally JIRA_BASE_URL to .env, "
            "then restart the server.")
    s.headers["Accept"] = "application/json"
    return s


# ── discovery ────────────────────────────────────────────────────────────────
def find_keys(text: str) -> list[str]:
    """Issue keys mentioned in `text`, browse-URL ones first, in order, unique."""
    keys: list[str] = []
    for k in _BROWSE_RE.findall(text or ""):
        if k.upper() not in keys:
            keys.append(k.upper())
    host = re.sub(r"^https?://", "", base_url()).lower()
    # Bare keys only count when the prompt also mentions Jira or the host —
    # otherwise "ABC-123" in an address or an order id would trigger a fetch.
    if keys or re.search(r"\bjira\b", text or "", re.I) or host in (text or "").lower():
        for k in _KEY_RE.findall(text or ""):
            if k not in keys:
                keys.append(k)
    return keys


# ── fetching ─────────────────────────────────────────────────────────────────
_FIELDS = "summary,description,issuetype,status,labels,subtasks,issuelinks,comment,attachment"
_LITE = "summary,description,issuetype,status"


def fetch_brief(key: str, *, max_subtasks: int = 12, max_links: int = 12,
                max_comments: int = 8) -> TicketBrief:
    """Everything a tester reads on the ticket, as one brief."""
    s = _session()
    root = _get(s, key, _FIELDS)
    f = root.get("fields") or {}
    story = Issue(key=root.get("key", key), summary=f.get("summary", ""),
                  type=_name(f.get("issuetype")), status=_name(f.get("status")),
                  description=f.get("description") or "",
                  labels=list(f.get("labels") or []))
    brief = TicketBrief(story=story, url=f"{base_url()}/browse/{story.key}")

    for st in (f.get("subtasks") or [])[:max_subtasks]:
        sub = _get(s, st.get("key", ""), _LITE, soft=True)
        sf = (sub or {}).get("fields") or st.get("fields") or {}
        issue = Issue(key=st.get("key", ""), summary=sf.get("summary", ""),
                      type=_name(sf.get("issuetype")), status=_name(sf.get("status")),
                      description=sf.get("description") or "")
        issue.kind = _classify(issue.summary)
        brief.subtasks.append(issue)

    for link in (f.get("issuelinks") or [])[:max_links]:
        other = link.get("outwardIssue") or link.get("inwardIssue") or {}
        if not other.get("key"):
            continue
        full = _get(s, other["key"], _LITE, soft=True)
        of = (full or {}).get("fields") or other.get("fields") or {}
        brief.linked.append(Issue(
            key=other["key"], summary=of.get("summary", ""),
            type=_name(of.get("issuetype")), status=_name(of.get("status")),
            description=of.get("description") or "",
            link_type=_name(link.get("type"))))

    comments = ((f.get("comment") or {}).get("comments") or [])
    kept = []
    for c in reversed(comments):                     # newest first
        who = ((c.get("author") or {}).get("displayName") or "?")
        if any(n in who.lower() for n in _NOISE_AUTHORS):
            continue
        kept.append(((c.get("created") or "")[:16].replace("T", " "), who, c.get("body") or ""))
        if len(kept) >= max_comments:
            break
    brief.comments = kept
    brief.attachments = [a.get("filename", "") for a in (f.get("attachment") or [])]
    # Spreadsheet attachments are read too: a QA round or a data gap analysis
    # attached to the story is exactly the kind of baseline the draft should
    # cover, and it costs one GET with the same credentials.
    for a in (f.get("attachment") or []):
        name = a.get("filename", "") or ""
        if not name.lower().endswith((".xlsx", ".xlsm", ".csv")) or int(a.get("size") or 0) > 3_000_000:
            continue
        try:
            r = s.get(a.get("content", ""), timeout=60)
            if r.status_code == 200:
                from ai_flow_builder.sheet_source import read_sheet_bytes

                brief.sheets.append(read_sheet_bytes(name, r.content))
        except Exception as e:  # noqa: BLE001
            logger.warning("Attachment %s not read: %s", name, e)
    logger.info("📋 Jira brief: %s", brief.summary_line())
    return brief


def fetch_briefs_for(text: str) -> list[TicketBrief]:
    """Briefs for every ticket the prompt names. Empty when it names none."""
    keys = find_keys(text)
    if not keys:
        return []
    return [fetch_brief(k) for k in keys[:3]]


def _get(session, key: str, fields: str, *, soft: bool = False) -> dict | None:
    url = f"{base_url()}/rest/api/2/issue/{key}"
    try:
        r = session.get(url, params={"fields": fields}, timeout=30)
    except Exception as e:  # noqa: BLE001
        if soft:
            logger.warning("Jira %s unreachable: %s", key, e)
            return None
        raise JiraError(f"Could not reach Jira at {base_url()}: {e}") from e
    if r.status_code in (401, 403):
        raise JiraError(f"Jira refused the credentials in .env ({r.status_code}) for {key}. "
                        "Check JIRA_PAT (or JIRA_USERNAME/JIRA_PASSWORD).")
    if r.status_code == 404:
        if soft:
            return None
        raise JiraError(f"{key} does not exist on {base_url()} or is not visible to this account.")
    if r.status_code >= 400:
        if soft:
            return None
        raise JiraError(f"Jira returned {r.status_code} for {key}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError as e:
        raise JiraError(f"Jira returned something that is not JSON for {key}.") from e


# ── helpers ──────────────────────────────────────────────────────────────────
def _name(obj) -> str:
    return (obj or {}).get("name", "") if isinstance(obj, dict) else ""


def _classify(summary: str) -> str:
    low = (summary or "").lower()
    for kind, pat in _SUBTASK_KINDS:
        if re.search(pat, low):
            return kind
    return "other"


def _indent(text: str) -> str:
    return "\n".join("    " + ln for ln in text.splitlines())


def _wiki_to_text(wiki: str) -> str:
    """
    Jira wiki markup → readable plain text. Colour/format macros are dropped,
    headings and lists kept, tables flattened to ' | ' rows, so the model sees
    the acceptance criteria as a tester does rather than as {color:#0747a6}.
    """
    if not wiki:
        return ""
    t = wiki.replace("\r\n", "\n")
    t = re.sub(r"\{color(?::[^}]*)?\}", "", t)
    t = re.sub(r"\{(?:noformat|code(?::[^}]*)?|quote|panel(?::[^}]*)?)\}", "", t)
    t = re.sub(r"\[~([^\]]+)\]", r"@\1", t)                       # mentions
    t = re.sub(r"\[([^|\]]+)\|([^\]]+)\]", r"\1 (\2)", t)          # [text|url]
    t = re.sub(r"!\S+?(?:\|[^!]*)?!", "(image)", t)                # !image.png|…!
    t = re.sub(r"^h([1-6])\.\s*", lambda m: "#" * int(m.group(1)) + " ", t, flags=re.M)
    t = re.sub(r"^\s*\|\|(.*)\|\|\s*$", lambda m: " | ".join(c.strip() for c in m.group(1).split("||")), t, flags=re.M)
    t = re.sub(r"^\s*\|(.*)\|\s*$", lambda m: " | ".join(c.strip() for c in m.group(1).split("|")), t, flags=re.M)
    t = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", t)            # *bold*
    t = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", t)              # _italic_
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()
