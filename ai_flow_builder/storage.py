"""
ai_flow_builder/storage.py — where uploaded testcase sources are kept.

Deliberately an interface with one implementation. Uploads live on local disk
today, but the intended home is a cloud bucket with local kept as a backup copy,
so the seam exists now: adding CloudSourceStore later means writing one class,
not rewriting the upload route.

Two ideas kept separate on purpose:

  the PRIMARY store   where a source is read from and written to.
  the BACKUP copy     an optional second write, never read unless the primary
                      is unavailable. `MirroredSourceStore` composes any two
                      stores into that arrangement, so "cloud primary, local
                      backup" needs no new logic — just different arguments.

A stored source keeps the ORIGINAL uploaded bytes, not just the parsed result.
Parsing improves over time, and re-parsing an old upload is impossible if the
file was thrown away.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOADS_DIR = os.path.join(BASE_DIR, "data", "uploads")

logger = logging.getLogger(__name__)

#: Formats with a working adapter. Anything else is rejected at upload rather
#: than accepted and then failing confusingly at parse time.
SUPPORTED_EXTENSIONS = {".xlsx": "xlsx"}


class UnsupportedSource(ValueError):
    """Raised for a file extension no adapter can read."""


class SourceNotFound(KeyError):
    pass


@dataclass
class StoredSource:
    source_id: str
    filename: str
    kind: str                       # adapter key, e.g. "xlsx"
    size_bytes: int
    sha256: str
    uploaded_at: str
    uploaded_by: str = ""           # free text until there is real auth
    path: str = ""                  # resolved by the store, not persisted
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("path", None)
        return d


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def source_id_for(data: bytes, filename: str) -> str:
    """
    Content-addressed id: the same file uploaded twice is the same source.

    Avoids a pile of duplicates when someone re-uploads the sheet they just
    uploaded, and makes the id reproducible rather than random.
    """
    digest = hashlib.sha256(data).hexdigest()
    return "src_" + digest[:12]


def kind_for(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedSource(
            f"'{ext or filename}' is not a supported testcase source. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    return SUPPORTED_EXTENSIONS[ext]


@runtime_checkable
class SourceStore(Protocol):
    """Everything a store must do. Keep this small so a cloud one stays cheap."""

    name: str

    def put(self, data: bytes, filename: str, uploaded_by: str = "") -> StoredSource: ...
    def get(self, source_id: str) -> StoredSource: ...
    def open_path(self, source_id: str) -> str: ...
    def list(self) -> list[StoredSource]: ...
    def delete(self, source_id: str) -> bool: ...


class LocalSourceStore:
    """Uploads under data/uploads/<source_id>/ with a sidecar meta.json."""

    name = "local"

    def __init__(self, root: str = UPLOADS_DIR) -> None:
        self.root = root
        os.makedirs(self.root, exist_ok=True)

    #: A source id is always of the tool's own making — "src_" plus a hex digest.
    #: Anything else arriving here came from a URL path, and `..` would make
    #: _dir() resolve to the parent of the upload root: delete() would then
    #: rmtree the entire data/ directory. Validate the shape rather than trusting
    #: the caller to have sanitised it.
    _ID = re.compile(r"^src_[0-9a-f]{6,64}$")

    def _dir(self, source_id: str) -> str:
        sid = str(source_id or "")
        if not self._ID.match(sid):
            raise SourceNotFound(f"not a valid source id: {source_id!r}")
        resolved = os.path.realpath(os.path.join(self.root, sid))
        if os.path.commonpath([resolved, os.path.realpath(self.root)]) != os.path.realpath(self.root):
            raise SourceNotFound(f"source id escapes the upload root: {source_id!r}")
        return resolved

    def put(self, data: bytes, filename: str, uploaded_by: str = "") -> StoredSource:
        kind = kind_for(filename)
        sid = source_id_for(data, filename)
        d = self._dir(sid)
        os.makedirs(d, exist_ok=True)
        safe = os.path.basename(filename)
        with open(os.path.join(d, safe), "wb") as f:
            f.write(data)
        rec = StoredSource(
            source_id=sid, filename=safe, kind=kind, size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(), uploaded_at=_now(),
            uploaded_by=uploaded_by, path=os.path.join(d, safe),
        )
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(rec.to_dict(), f, indent=2)
        return rec

    def get(self, source_id: str) -> StoredSource:
        meta = os.path.join(self._dir(source_id), "meta.json")
        if not os.path.exists(meta):
            raise SourceNotFound(f"no uploaded source with id {source_id!r}")
        with open(meta, encoding="utf-8") as f:
            raw = json.load(f)
        rec = StoredSource(**raw)
        rec.path = os.path.join(self._dir(source_id), rec.filename)
        return rec

    def open_path(self, source_id: str) -> str:
        rec = self.get(source_id)
        if not os.path.exists(rec.path):
            raise SourceNotFound(f"{source_id}: metadata exists but the file is gone")
        return rec.path

    def list(self) -> list[StoredSource]:
        if not os.path.isdir(self.root):
            return []
        out = []
        for sid in sorted(os.listdir(self.root)):
            try:
                out.append(self.get(sid))
            except (SourceNotFound, TypeError, json.JSONDecodeError):
                continue
        return sorted(out, key=lambda r: r.uploaded_at, reverse=True)

    def delete(self, source_id: str) -> bool:
        try:
            d = self._dir(source_id)
        except SourceNotFound:
            return False
        if not os.path.isdir(d):
            return False
        shutil.rmtree(d)
        return True


class MirroredSourceStore:
    """
    A primary store with a backup written alongside it.

    Intended shape once cloud lands: MirroredSourceStore(CloudSourceStore(),
    LocalSourceStore()). Writes go to both; reads prefer the primary and fall
    back to the backup, so a cloud outage degrades to the local copy instead of
    failing. A backup write that fails never fails the upload — a missing backup
    is worse than no backup only if it is mistaken for one, so it is reported.
    """

    def __init__(self, primary: SourceStore, backup: SourceStore | None = None) -> None:
        self.primary = primary
        self.backup = backup
        self.name = f"{primary.name}+{backup.name}" if backup else primary.name
        self.backup_errors: list[str] = []

    def put(self, data: bytes, filename: str, uploaded_by: str = "") -> StoredSource:
        rec = self.primary.put(data, filename, uploaded_by)
        if self.backup is not None:
            try:
                self.backup.put(data, filename, uploaded_by)
            except Exception as e:  # noqa: BLE001
                self.backup_errors.append(f"{rec.source_id}: {e}")
        return rec

    def get(self, source_id: str) -> StoredSource:
        try:
            return self.primary.get(source_id)
        except Exception:
            if self.backup is None:
                raise
            return self.backup.get(source_id)

    def open_path(self, source_id: str) -> str:
        try:
            return self.primary.open_path(source_id)
        except Exception:
            if self.backup is None:
                raise
            return self.backup.open_path(source_id)

    def list(self) -> list[StoredSource]:
        return self.primary.list()

    def delete(self, source_id: str) -> bool:
        ok = self.primary.delete(source_id)
        if self.backup is not None:
            try:
                self.backup.delete(source_id)
            except Exception:  # noqa: BLE001
                pass
        return ok


def adapter_for(rec: StoredSource):
    """Build the reader for a stored source. One branch per supported kind."""
    if rec.kind == "xlsx":
        from ai_flow_builder.sources.xlsx_source import XlsxSource

        return XlsxSource(rec.path)
    raise UnsupportedSource(f"no adapter for kind {rec.kind!r}")


#: The store the API uses. Swap this line for MirroredSourceStore(cloud, local)
#: when the cloud bucket exists — nothing else has to change.
default_store: SourceStore = LocalSourceStore()
