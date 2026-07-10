"""SQLite + Fernet crypto — single-file state.

Schema is created inline on first startup; migrations are a full-drop
event since this is single-user and state is easy to rebuild.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from cryptography.fernet import Fernet, InvalidToken

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "state.db"

_lock = threading.Lock()
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = os.environ.get("ENCRYPTION_KEY", "").encode()
        if not key:
            raise RuntimeError("ENCRYPTION_KEY env var is required")
        _fernet = Fernet(key)
    return _fernet


def encrypt(value: str) -> str:
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("stored token cannot be decrypted with current ENCRYPTION_KEY") from exc


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()


def init_schema() -> None:
    with connect() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS kv (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts TEXT NOT NULL,
              module TEXT NOT NULL,
              level TEXT NOT NULL,
              task_id TEXT,
              action TEXT NOT NULL,
              detail TEXT
            );

            CREATE INDEX IF NOT EXISTS events_ts ON events(ts DESC);
            CREATE INDEX IF NOT EXISTS events_module_ts ON events(module, ts DESC);
            """
        )


def kv_get(key: str, default: Any = None) -> Any:
    with connect() as c:
        row = c.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        return json.loads(row["value"])


def kv_set(key: str, value: Any) -> None:
    payload = json.dumps(value, separators=(",", ":"))
    with connect() as c:
        c.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, payload),
        )


def kv_delete(key: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM kv WHERE key = ?", (key,))


def log_event(
    module: str,
    action: str,
    *,
    level: str = "info",
    task_id: str | None = None,
    detail: dict | str | None = None,
) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    detail_str: str | None
    if detail is None:
        detail_str = None
    elif isinstance(detail, str):
        detail_str = detail
    else:
        detail_str = json.dumps(detail, separators=(",", ":"))
    with connect() as c:
        c.execute(
            "INSERT INTO events(ts, module, level, task_id, action, detail) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (ts, module, level, task_id, action, detail_str),
        )


def recent_events(
    *,
    module: str | None = None,
    limit: int = 200,
) -> list[dict]:
    q = "SELECT id, ts, module, level, task_id, action, detail FROM events"
    args: tuple = ()
    if module:
        q += " WHERE module = ?"
        args = (module,)
    q += " ORDER BY ts DESC, id DESC LIMIT ?"
    args = args + (limit,)
    with connect() as c:
        rows = c.execute(q, args).fetchall()
    return [dict(r) for r in rows]


def prune_events(keep_days: int) -> int:
    cutoff = datetime.now(timezone.utc).timestamp() - keep_days * 86400
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat(timespec="seconds")
    with connect() as c:
        cur = c.execute("DELETE FROM events WHERE ts < ?", (cutoff_iso,))
        return cur.rowcount
