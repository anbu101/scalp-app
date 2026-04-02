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

            # Backfill safety (older SQLite may not auto-fill)
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
    #
    # WHY THIS EXISTS:
    #   Migration 010 creates UNIQUE INDEX on (symbol, timeframe, ts).
    #   But if 010 was already recorded in schema_migrations from a
    #   partial run, or if a legacy migration (the missing 005) created
    #   a bad UNIQUE INDEX on (symbol, timeframe) WITHOUT ts under a
    #   different name not caught by 010's DROP list, the replacement
    #   bug persists silently.
    #
    #   This guard queries sqlite_master directly — it does NOT trust
    #   schema_migrations. It runs on every startup and is fully
    #   idempotent. It:
    #     a) Finds ALL unique indexes on market_timeline
    #     b) Drops any that do NOT include the 'ts' column
    #        (those are the bad ones causing row replacement)
    #     c) Deduplicates rows if any duplicates crept in
    #     d) Creates the correct unique index if it is missing
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

    # --------------------------------------------------
    # Step 1: Find all indexes on market_timeline from sqlite_master
    # --------------------------------------------------
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

        # The correct index includes all three columns
        has_symbol    = "SYMBOL"    in idx_sql_upper
        has_timeframe = "TIMEFRAME" in idx_sql_upper
        has_ts        = "TS"        in idx_sql_upper

        if is_unique and has_symbol and has_timeframe and has_ts:
            # This is correct — leave it alone
            correct_index_exists = True
            write_audit_log(
                f"[DB][SCHEMA] market_timeline correct unique index: {idx_name}"
            )

        elif is_unique and not has_ts:
            # This is the bad index — UNIQUE on (symbol, timeframe)
            # without ts causes row replacement on every new candle
            bad_indexes.append(idx_name)
            write_audit_log(
                f"[DB][SCHEMA] market_timeline BAD unique index found: {idx_name} "
                f"(missing ts) — will drop"
            )

    # --------------------------------------------------
    # Step 2: Drop bad indexes
    # --------------------------------------------------
    for idx_name in bad_indexes:
        write_audit_log(f"[DB][FIX] Dropping bad unique index: {idx_name}")
        cur.execute(f"DROP INDEX IF EXISTS {idx_name}")
        conn.commit()

    # --------------------------------------------------
    # Step 3: Deduplicate rows if any snuck in
    # Only run if we actually dropped a bad index or
    # the correct one doesn't exist yet.
    # --------------------------------------------------
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

    # --------------------------------------------------
    # Step 4: Create correct unique index if missing
    # --------------------------------------------------
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
    else:
        # Index is already correct — nothing to do
        pass