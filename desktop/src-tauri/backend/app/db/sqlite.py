# backend/app/db/sqlite.py

import sqlite3
from pathlib import Path
from typing import Optional
import threading

from app.utils.app_paths import DATA_DIR, ensure_app_dirs


# --------------------------------------------------
# DATABASE PATH
# --------------------------------------------------

DB_PATH = DATA_DIR / "app.db"

# --------------------------------------------------
# THREAD-LOCAL CONNECTIONS (SAFE)
# --------------------------------------------------

_local = threading.local()


def _create_connection() -> sqlite3.Connection:
    ensure_app_dirs()

    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=True,  # 🔒 restore safety
        timeout=30.0,
        isolation_level=None,  # autocommit
    )

    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    return conn


def get_conn() -> sqlite3.Connection:
    """
    Returns thread-local SQLite connection.
    Safe for multi-threaded engine.
    """

    if not hasattr(_local, "conn"):
        _local.conn = _create_connection()

    return _local.conn


def init_db() -> sqlite3.Connection:
    return get_conn()
