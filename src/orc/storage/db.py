"""SQLite connection + schema bootstrap.

Concurrency:
- Connections opened with isolation_level=None for explicit transaction control.
- Writers use BEGIN IMMEDIATE to acquire the write lock immediately.
- WAL mode lets readers proceed while writers hold the lock.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path

SCHEMA_VERSION = 1


def schema_sql() -> str:
    return files("orc.storage").joinpath("schema.sql").read_text(encoding="utf-8")


@contextmanager
def open_connection(db_path: Path, *, create_dir: bool = False) -> Iterator[sqlite3.Connection]:
    if create_dir:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        yield conn
    finally:
        conn.close()


def bootstrap_schema(conn: sqlite3.Connection) -> None:
    """Apply schema.sql to the connection and stamp schema_version. Idempotent."""
    conn.executescript(schema_sql())
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
