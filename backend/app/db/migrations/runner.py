# backend/app/db/migrations/runner.py

from pathlib import Path
from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log

MIGRATIONS_DIR = Path(__file__).parent


def run_migrations(conn):
    """
    Runs SQL migrations in filename order.
    Safe to run multiple times.
    DOES NOT drop existing tables unless SQL explicitly does so.
    """
    cur = conn.cursor()

    # Ensure migrations table exists
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

    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if sql_file.name in applied:
            continue

        write_audit_log(f"[DB][MIGRATE] Applying {sql_file.name}")

        sql = sql_file.read_text()
        cur.executescript(sql)

        cur.execute(
            """
            INSERT INTO schema_migrations (filename, applied_at)
            VALUES (?, strftime('%s','now'))
            """,
            (sql_file.name,),
        )

        conn.commit()

    write_audit_log("[DB][MIGRATE] All migrations applied")
