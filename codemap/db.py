"""SQLite connection and migrations.

The DB lives at ``.codemap/index.db`` — a single file, no server (spec 5.4).
Migrations are forward-only and keyed by ``SCHEMA_VERSION``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open (creating parent dirs as needed) and return a tuned connection."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if row is None:
        return 0
    r = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(r["value"]) if r else 0


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations. Returns the resulting schema version.

    v0 -> v1 is the full ``schema.sql`` bootstrap. Later versions append
    ``ALTER``/``CREATE`` blocks here, guarded by the from-version.
    """
    version = _current_version(conn)

    if version < 1:
        conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        version = 1

    if version < 2:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(symbol_versions)")}
        if "raw_hash" not in cols:
            conn.execute("ALTER TABLE symbol_versions ADD COLUMN raw_hash TEXT")
        version = 2

    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(version),),
    )
    conn.commit()
    return version


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
