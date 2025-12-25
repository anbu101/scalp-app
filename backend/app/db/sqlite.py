# backend/app/db/sqlite.py

import sqlite3
from pathlib import Path

DB_PATH = Path("/data/app.db")
_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn

    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row

    return _conn


def init_db() -> sqlite3.Connection:
    """
    Lowest-level DB bootstrap.
    ONLY ensures DB file + connection.
    MUST NOT import migrations.
    """
    return get_conn()
