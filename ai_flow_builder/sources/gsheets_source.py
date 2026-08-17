"""
ai_flow_builder/sources/gsheets_source.py

Reads the live Google Sheet, READ-ONLY. Returns raw tab data only — parsing is
shared with every other source (see ai_flow_builder/testcase.py).

Read-only is enforced three ways:
  1. the OAuth scope requested is spreadsheets.readonly — the token cannot write
  2. this class exposes no write method of any kind
  3. only values().get / values().batchGet are ever called

Authentication
--------------
Configured entirely through environment variables; no credential material is read
from, or written to, the repository.

    GOOGLE_SHEETS_AUTH=service_account
    GOOGLE_SERVICE_ACCOUNT_FILE=/absolute/path/outside/the/repo/sa.json

  or

    GOOGLE_SHEETS_AUTH=oauth
    GOOGLE_OAUTH_CLIENT_FILE=/absolute/path/outside/the/repo/client_secret.json
    GOOGLE_OAUTH_TOKEN_FILE=/absolute/path/outside/the/repo/token.json

`preflight()` reports what is missing without raising, so the pipeline can degrade
to another source and say so rather than failing opaquely.
"""
from __future__ import annotations

import importlib.util
import os
import re

SCOPE_READONLY = "https://www.googleapis.com/auth/spreadsheets.readonly"

_REQUIRED_PACKAGES = (
    ("googleapiclient", "google-api-python-client"),
    ("google.auth", "google-auth"),
)
_OAUTH_PACKAGE = ("google_auth_oauthlib", "google-auth-oauthlib")


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def extract_spreadsheet_id(url_or_id: str) -> str:
    """Accept a full Sheets URL or a bare spreadsheet ID."""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url_or_id or "")
    return m.group(1) if m else (url_or_id or "").strip()


class GoogleSheetsSource:
    kind = "google_sheets"

    def __init__(self, spreadsheet: str, auth_mode: str | None = None):
        self.spreadsheet_id = extract_spreadsheet_id(spreadsheet)
        self.auth_mode = (auth_mode or os.getenv("GOOGLE_SHEETS_AUTH", "")).strip().lower()
        self._service = None
        self._meta: dict | None = None

    # ── Preflight ────────────────────────────────────────────────────────────
    def preflight(self) -> dict:
        """Report readiness without raising. {'ready': bool, 'missing': [...]}"""
        missing: list[str] = []

        for module, package in _REQUIRED_PACKAGES:
            if not _installed(module):
                missing.append(f"python package '{package}' is not installed")

        if not self.spreadsheet_id:
            missing.append("no spreadsheet id or URL supplied")

        if self.auth_mode == "service_account":
            path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")
            if not path:
                missing.append("GOOGLE_SERVICE_ACCOUNT_FILE is not set")
            elif not os.path.exists(os.path.expanduser(path)):
                missing.append(f"service-account file not found at {path}")
        elif self.auth_mode == "oauth":
            if not _installed(_OAUTH_PACKAGE[0]):
                missing.append(f"python package '{_OAUTH_PACKAGE[1]}' is not installed")
            client = os.getenv("GOOGLE_OAUTH_CLIENT_FILE", "")
            if not client:
                missing.append("GOOGLE_OAUTH_CLIENT_FILE is not set")
            elif not os.path.exists(os.path.expanduser(client)):
                missing.append(f"OAuth client file not found at {client}")
        else:
            missing.append(
                "GOOGLE_SHEETS_AUTH is not set to 'service_account' or 'oauth'"
            )

        return {"ready": not missing, "missing": missing,
                "auth_mode": self.auth_mode or "<unset>",
                "scope": SCOPE_READONLY}

    # ── Credentials ──────────────────────────────────────────────────────────
    def _credentials(self):
        if self.auth_mode == "service_account":
            from google.oauth2 import service_account

            path = os.path.expanduser(os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"])
            return service_account.Credentials.from_service_account_file(
                path, scopes=[SCOPE_READONLY]
            )

        if self.auth_mode == "oauth":
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow

            token_path = os.path.expanduser(
                os.getenv("GOOGLE_OAUTH_TOKEN_FILE", "~/.config/nlp_playwright/gsheets_token.json")
            )
            creds = None
            if os.path.exists(token_path):
                creds = Credentials.from_authorized_user_file(token_path, [SCOPE_READONLY])
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        os.path.expanduser(os.environ["GOOGLE_OAUTH_CLIENT_FILE"]),
                        [SCOPE_READONLY],
                    )
                    creds = flow.run_local_server(port=0)
                os.makedirs(os.path.dirname(token_path), exist_ok=True)
                with open(token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
                os.chmod(token_path, 0o600)
            return creds

        raise RuntimeError(
            "GOOGLE_SHEETS_AUTH must be 'service_account' or 'oauth'. "
            "Run preflight() for the full list of what is missing."
        )

    def _connect(self):
        if self._service is None:
            from googleapiclient.discovery import build

            self._service = build("sheets", "v4", credentials=self._credentials(),
                                  cache_discovery=False)
        return self._service

    # ── SourceAdapter ────────────────────────────────────────────────────────
    def describe(self) -> dict:
        pre = self.preflight()
        base = {
            "kind": self.kind,
            "spreadsheet_id": self.spreadsheet_id,
            "location": f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}",
            "auth": pre["auth_mode"],
            "scope": SCOPE_READONLY,
            "access": "read-only (values().get only; no write method exists)",
        }
        if not pre["ready"]:
            return base | {"title": "<unavailable — auth not configured>",
                           "ready": False, "missing": pre["missing"]}

        meta = self._meta or self._connect().spreadsheets().get(
            spreadsheetId=self.spreadsheet_id, includeGridData=False
        ).execute()
        self._meta = meta
        return base | {
            "title": meta.get("properties", {}).get("title", ""),
            "ready": True,
            "tabs": [s["properties"]["title"] for s in meta.get("sheets", [])],
        }

    def read_tabs(self) -> dict[str, list[list]]:
        pre = self.preflight()
        if not pre["ready"]:
            raise RuntimeError(
                "Google Sheets source is not ready:\n  - " + "\n  - ".join(pre["missing"])
            )

        svc = self._connect()
        meta = self._meta or svc.spreadsheets().get(
            spreadsheetId=self.spreadsheet_id, includeGridData=False
        ).execute()
        self._meta = meta

        titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
        resp = svc.spreadsheets().values().batchGet(
            spreadsheetId=self.spreadsheet_id,
            ranges=[f"'{t}'" for t in titles],
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute()

        tabs: dict[str, list[list]] = {}
        for title, block in zip(titles, resp.get("valueRanges", [])):
            rows = block.get("values", [])
            width = max((len(r) for r in rows), default=0)
            # Sheets truncates trailing empties; pad so column indexes stay stable.
            tabs[title] = [list(r) + [None] * (width - len(r)) for r in rows]
        return tabs
