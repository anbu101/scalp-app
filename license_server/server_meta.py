"""
license_server/server_meta.py

Tiny key-value store for server-wide settings that are NOT per-license.
First use: min_version (the lowest app version allowed to run without a
nag). Kept in its own table so it never entangles the licenses schema.

Same SQLite file as db.py (licenses.db) — shares the connection style but
its own table. Safe to import alongside db.py.
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("LICSRV_DB", Path(__file__).resolve().parent / "licenses.db"))


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_meta():
    """Create the meta table if absent. Idempotent; safe every boot."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS server_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )


def get_meta(key: str, default: str | None = None) -> str | None:
    try:
        with _conn() as c:
            row = c.execute("SELECT value FROM server_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    except Exception:
        return default


def set_meta(key: str, value: str):
    with _conn() as c:
        c.execute(
            """
            INSERT INTO server_meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


# Convenience wrappers for the min_version use-case ------------------

MIN_VERSION_KEY = "min_version"


def get_min_version() -> str | None:
    """Returns the configured minimum app version, or None if never set
    (None = no minimum, every version is allowed)."""
    return get_meta(MIN_VERSION_KEY, default=None)


def set_min_version(version: str):
    set_meta(MIN_VERSION_KEY, version)