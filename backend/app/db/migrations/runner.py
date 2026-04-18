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


def _apply_pre_migration_hotfixes(conn):
    """
    Hotfixes that MUST run before SQL migrations.

    Migration 009 does:
        INSERT INTO trades_v2 SELECT strategy_id ... FROM trades

    If trades.strategy_id does not exist yet (fresh install or older DB
    that never ran the post-migration hotfix), that SELECT fails with
    "no such column: strategy_id".

    By applying the column additions here — before the SQL loop — we
    guarantee that trades has both `slot` and `strategy_id` by the time
    migration 009 executes.
    """
    cur = conn.cursor()

    if not table_exists(cur, "trades"):
        return  # table doesn't exist yet; migrations will create it

    if not column_exists(cur, "trades", "slot"):
        write_audit_log("[DB][PRE-MIGRATE] Adding missing trades.slot column")
        cur.execute("ALTER TABLE trades ADD COLUMN slot TEXT")
        conn.commit()

    if not column_exists(cur, "trades", "strategy_id"):
        write_audit_log("[DB][PRE-MIGRATE] Adding trades.strategy_id column")
        cur.execute(
            "ALTER TABLE trades ADD COLUMN strategy_id TEXT DEFAULT 'SCALP_V1'"
        )
        conn.commit()
        cur.execute(
            "UPDATE trades SET strategy_id = 'SCALP_V1' WHERE strategy_id IS NULL"
        )
        conn.commit()
        write_audit_log("[DB][PRE-MIGRATE] strategy_id column added & backfilled")


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

    # --------------------------------------------------
    # PRE-MIGRATION HOTFIXES
    # Must run BEFORE the SQL loop so that migrations
    # like 009 (which SELECT strategy_id from trades)
    # don't fail on older or fresh databases.
    # --------------------------------------------------
    _apply_pre_migration_hotfixes(conn)

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

        cur.executescript(
            sql_file.read_text(encoding="utf-8-sig", errors="replace")
        )

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
    # These are kept for safety on existing installs
    # where the pre-migration pass may not have fired
    # (e.g. trades table created mid-migration-run).
    # --------------------------------------------------

    if table_exists(cur, "trades"):

        # ------------------------------------------
        # 1️⃣ Ensure slot column exists
        # ------------------------------------------
        if not column_exists(cur, "trades", "slot"):
            write_audit_log("[DB][FIX] Adding missing trades.slot column")
            cur.execute(
                """
                ALTER TABLE trades
                ADD COLUMN slot TEXT
                """
            )
            conn.commit()

        # ------------------------------------------
        # 2️⃣ Ensure strategy_id column exists
        # ------------------------------------------
        if not column_exists(cur, "trades", "strategy_id"):
            write_audit_log("[DB][FIX] Adding trades.strategy_id column")

            cur.execute(
                """
                ALTER TABLE trades
                ADD COLUMN strategy_id TEXT DEFAULT 'SCALP_V1'
                """
            )

            conn.commit()

            cur.execute(
                """
                UPDATE trades
                SET strategy_id = 'SCALP_V1'
                WHERE strategy_id IS NULL
                """
            )
            conn.commit()

            write_audit_log("[DB][FIX] strategy_id column added & backfilled")

    # --------------------------------------------------
    # 3️⃣ market_timeline UNIQUE INDEX GUARD
    # --------------------------------------------------
    if table_exists(cur, "market_timeline"):
        _fix_market_timeline_unique_index(cur, conn)

    write_audit_log("[DB][MIGRATE] All migrations applied")


def _fix_market_timeline_unique_index(cur, conn):
    """
    Guarantee that market_timeline has exactly the right unique index:
        UNIQUE (symbol, timeframe, ts)

    Safe to call on every startup — all operations are guarded with
    existence checks.
    """

    indexes = cur.execute(
        """
        SELECT name, sql
        FROM sqlite_master
        WHERE type = 'index'
          AND tbl_name = 'market_timeline'
          AND sql IS NOT NULL
        """
    ).fetchall()

    bad_indexes = []
    correct_index_exists = False

    for idx_name, idx_sql in indexes:
        idx_sql_upper = (idx_sql or "").upper()

        is_unique = "UNIQUE" in idx_sql_upper

        has_symbol    = "SYMBOL"    in idx_sql_upper
        has_timeframe = "TIMEFRAME" in idx_sql_upper
        has_ts        = "TS"        in idx_sql_upper

        if is_unique and has_symbol and has_timeframe and has_ts:
            correct_index_exists = True
            write_audit_log(
                f"[DB][SCHEMA] market_timeline correct unique index: {idx_name}"
            )

        elif is_unique and not has_ts:
            bad_indexes.append(idx_name)
            write_audit_log(
                f"[DB][SCHEMA] market_timeline BAD unique index found: {idx_name} "
                f"(missing ts) — will drop"
            )

    for idx_name in bad_indexes:
        write_audit_log(f"[DB][FIX] Dropping bad unique index: {idx_name}")
        cur.execute(f"DROP INDEX IF EXISTS {idx_name}")
        conn.commit()

    if bad_indexes or not correct_index_exists:
        write_audit_log(
            "[DB][FIX] Deduplicating market_timeline rows "
            "(keeping highest id per symbol+timeframe+ts)..."
        )
        cur.execute(
            """
            DELETE FROM market_timeline
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM market_timeline
                GROUP BY symbol, timeframe, ts
            )
            """
        )
        deleted = cur.rowcount
        conn.commit()

        if deleted:
            write_audit_log(
                f"[DB][FIX] Removed {deleted} duplicate rows from market_timeline"
            )

    if not correct_index_exists:
        write_audit_log(
            "[DB][FIX] Creating market_timeline unique index on "
            "(symbol, timeframe, ts)..."
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_market_timeline_symbol_tf_ts
            ON market_timeline (symbol, timeframe, ts)
            """
        )
        conn.commit()
        write_audit_log("[DB][FIX] market_timeline unique index created ✓")