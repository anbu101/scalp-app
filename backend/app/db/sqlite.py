# backend/app/db/sqlite.py

import sqlite3
from pathlib import Path
from typing import Optional

# 🔐 SINGLE SOURCE OF TRUTH FOR PATHS
from app.utils.app_paths import DATA_DIR, ensure_app_dirs

# --------------------------------------------------
# DATABASE PATH (CANONICAL)
# --------------------------------------------------

DB_PATH = DATA_DIR / "app.db"

# --------------------------------------------------
# CONNECTION SINGLETON
# --------------------------------------------------

_conn: Optional[sqlite3.Connection] = None


def get_conn() -> sqlite3.Connection:
    """
    Get (or create) a shared SQLite connection.

    Guarantees:
    - Uses ~/.scalp-app/data/app.db
    - Does NOT create a new DB elsewhere
    - Safe for FastAPI + threads
    - Safe to call multiple times
    """
    global _conn

    if _conn is None:
        # Ensure ~/.scalp-app/{data,logs,state,config}
        ensure_app_dirs()

        _conn = sqlite3.connect(
            DB_PATH,
            check_same_thread=False,
        )
        _conn.row_factory = sqlite3.Row

    return _conn


def init_db() -> sqlite3.Connection:
    """
    Lowest-level DB bootstrap ONLY.

    ❌ MUST NOT:
      - run migrations
      - alter schema
      - create tables

    ✅ ONLY:
      - return a valid connection to the canonical DB
    """
    return get_conn()
