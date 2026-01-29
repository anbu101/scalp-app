from app.event_bus.audit_logger import write_audit_log


def table_exists(cur, table_name: str) -> bool:
    row = cur.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name=?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def column_exists(cur, table_name: str, column_name: str) -> bool:
    rows = cur.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()
    return any(r[1] == column_name for r in rows)


def ensure_schema(conn):
    """
    🔒 DEFENSIVE SCHEMA GUARD

    Rules:
    - NEVER assumes tables exist
    - NEVER assumes columns exist
    - NEVER raises
    - ONLY logs warnings
    - SAFE for old + new DBs
    """

    cur = conn.cursor()

    # --------------------------------------------------
    # paper_trades
    # --------------------------------------------------
    if table_exists(cur, "paper_trades"):
        if not column_exists(cur, "paper_trades", "exit_time"):
            write_audit_log(
                "[DB][SCHEMA][paper_trades] exit_time column missing (older DB, safe)"
            )
    else:
        write_audit_log(
            "[DB][SCHEMA] paper_trades table not found (will be created by migrations)"
        )

    # --------------------------------------------------
    # trades
    # --------------------------------------------------
    if table_exists(cur, "trades"):
        if not column_exists(cur, "trades", "exit_time"):
            write_audit_log(
                "[DB][SCHEMA][trades] exit_time column missing (older DB, safe)"
            )
    else:
        write_audit_log("[DB][SCHEMA] trades table not found")

    # --------------------------------------------------
    # backtest_trades
    # --------------------------------------------------
    if table_exists(cur, "backtest_trades"):
        if not column_exists(cur, "backtest_trades", "atm_slot"):
            write_audit_log(
                "[DB][SCHEMA][backtest_trades] atm_slot column missing (older DB, safe)"
            )
    else:
        write_audit_log("[DB][SCHEMA] backtest_trades table not found")

    # --------------------------------------------------
    # NEVER mutate schema here
    # --------------------------------------------------
    conn.commit()
