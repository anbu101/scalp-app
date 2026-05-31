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
    Hotfixes that MUST run before each SQL migration (called inside the
    migration loop, not just once at the start).

    Migration 009 (009_fix_exit_reason_constraint.sql) does:
        INSERT INTO trades_v2 SELECT strategy_id ... FROM trades

    On a fresh install, 'trades' does not exist before migrations run,
    so a single pre-loop call returns early. Migration 001 then creates
    'trades' (without strategy_id), and by the time 009 executes the
    column is still missing — causing:
        sqlite3.OperationalError: no such column: strategy_id

    Fix: call this function before EVERY unapplied migration. It is
    fully idempotent — all operations are guarded by table_exists /
    column_exists — so repeated calls on an already-correct DB are
    instant no-ops.
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

def _ensure_scalp_v2_trade_columns(conn):
    """
    Add SCALP_V2's group_id + trade_class columns to BOTH trades and
    paper_trades, guarded by column_exists (SQLite has no
    ADD COLUMN IF NOT EXISTS, and this runner marks partially-failed
    migrations complete — so a bare ALTER in a .sql file that fails on
    re-run would be silently skipped forever).

    Columns are NULLABLE with NO default: existing rows from BB_V1,
    BB_V2, HA_V1, and SCALP_V1 receive NULL and are completely
    unaffected. Only SCALP_V2 reads/writes these columns.

    Fully idempotent — safe to call on every startup.
    """
    cur = conn.cursor()

    for table in ("trades", "paper_trades"):
        if not table_exists(cur, table):
            continue

        if not column_exists(cur, table, "group_id"):
            write_audit_log(f"[DB][FIX] Adding {table}.group_id (SCALP_V2)")
            cur.execute(f"ALTER TABLE {table} ADD COLUMN group_id TEXT")
            conn.commit()

        if not column_exists(cur, table, "trade_class"):
            write_audit_log(f"[DB][FIX] Adding {table}.trade_class (SCALP_V2)")
            cur.execute(f"ALTER TABLE {table} ADD COLUMN trade_class TEXT")
            conn.commit()

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
    # INITIAL PRE-MIGRATION HOTFIX PASS
    # Handles existing installs where 'trades' already
    # exists before any migration runs (e.g. upgrades).
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

        # Re-run hotfixes before EACH unapplied migration.
        #
        # Why: on a fresh install, _apply_pre_migration_hotfixes above
        # returns early because 'trades' doesn't exist yet. Migration 001
        # then creates 'trades' (without strategy_id / slot). By calling
        # this again here, we ensure those columns are added before
        # migration 009 runs its:
        #   INSERT INTO trades_v2 SELECT strategy_id ... FROM trades
        #
        # The function is idempotent — safe to call on every iteration.
        _apply_pre_migration_hotfixes(conn)

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
    # 3️⃣ SCALP_V2 group_id + trade_class columns
    # Additive, nullable, guarded. Other strategies unaffected.
    # --------------------------------------------------
    _ensure_scalp_v2_trade_columns(conn)

    # --------------------------------------------------
    # 4️⃣ market_timeline UNIQUE INDEX GUARD
    # --------------------------------------------------
    if table_exists(cur, "market_timeline"):
        _fix_market_timeline_unique_index(cur, conn)


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