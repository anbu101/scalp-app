# backend/app/db/migrations/runner.py

from pathlib import Path
from app.event_bus.audit_logger import write_audit_log
from app.db.schema_guard import ensure_schema

MIGRATIONS_DIR = Path(__file__).parent


def column_exists(cur, table, column):
    rows = cur.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def table_exists(cur, table):
    row = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def run_migrations(conn):
    cur = conn.cursor()

    # --------------------------------------------------
    # BASE DB SETUP (NO APP TABLES)
    # --------------------------------------------------
    ensure_schema(conn)

    # --------------------------------------------------
    # MIGRATION REGISTRY (ALWAYS SAFE)
    # --------------------------------------------------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename TEXT PRIMARY KEY,
            applied_at INTEGER
        )
        """
    )

    applied = {
        row[0]
        for row in cur.execute(
            "SELECT filename FROM schema_migrations"
        ).fetchall()
    }

    # --------------------------------------------------
    # APPLY SQL MIGRATIONS (CREATE TABLES HERE)
    # --------------------------------------------------
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if sql_file.name in applied:
            continue

        write_audit_log(f"[DB][MIGRATE] Applying {sql_file.name}")
        cur.executescript(sql_file.read_text())

        cur.execute(
            """
            INSERT INTO schema_migrations (filename, applied_at)
            VALUES (?, strftime('%s','now'))
            """,
            (sql_file.name,),
        )
        conn.commit()

    # --------------------------------------------------
    # POST-MIGRATION HOTFIXES (FULLY GUARDED)
    # --------------------------------------------------
    if table_exists(cur, "trades"):
        if not column_exists(cur, "trades", "slot"):
            write_audit_log("[DB][FIX] Adding missing trades.slot column")
            cur.execute(
                """
                ALTER TABLE trades
                ADD COLUMN slot TEXT
                """
            )
            conn.commit()

    write_audit_log("[DB][MIGRATE] All migrations applied")
