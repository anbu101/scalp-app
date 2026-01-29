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
    global _conn

    if _conn is None:
        ensure_app_dirs()

        _conn = sqlite3.connect(
            DB_PATH,
            check_same_thread=False,
            timeout=30.0,
            isolation_level=None  # ← Autocommit mode - no manual transactions
        )
        _conn.row_factory = sqlite3.Row
        
        # Enable WAL mode for better concurrency
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        
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
