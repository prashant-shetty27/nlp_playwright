"""
locators/io_utils.py
Shared JSON I/O helpers for locator files:
- advisory file locking (best-effort, cross-platform fallback)
- atomic JSON writes (temp file + os.replace)
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager

try:
    import fcntl  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - non-posix fallback
    fcntl = None


@contextmanager
def file_lock(target_path: str, exclusive: bool = True):
    """
    Advisory file lock on <target_path>.lock.
    Uses shared lock for reads and exclusive lock for writes on POSIX.
    Falls back to no-op lock on platforms without fcntl.
    """
    lock_path = f"{target_path}.lock"
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    lock_fh = open(lock_path, "a+", encoding="utf-8")
    try:
        if fcntl is not None:
            mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(lock_fh.fileno(), mode)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        finally:
            lock_fh.close()


def atomic_write_json(path: str, data: dict, indent: int = 2) -> None:
    """
    Atomically write JSON: write temp file in same dir, fsync, then os.replace.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    base = os.path.basename(path)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{base}.", suffix=".tmp", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=indent)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_json(path: str, retries: int = 2, delay_s: float = 0.05):
    """
    Read JSON with small retry window to handle concurrent replace/write races.
    Returns decoded object, or raises the last exception.
    """
    last_exc = None
    for _ in range(max(1, retries + 1)):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as e:
            last_exc = e
            time.sleep(delay_s)
    raise last_exc  # type: ignore[misc]
