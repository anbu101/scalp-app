from fastapi import APIRouter, Query
from pathlib import Path
from datetime import date, datetime, timezone
from typing import Optional
import sqlite3

router = APIRouter(tags=["trade-history"])

DB_PATH = Path.home() / ".scalp-app" / "data" / "app.db"

# All terminal states — normalised to "CLOSED" for the frontend
CLOSED_STATES = {"SL_HIT", "TP_HIT", "EXITED", "CLOSED", "BROKER_EXIT"}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)

    # Convert unix timestamps
    for col in ("entry_time", "exit_time"):
        ts = d.get(col)
        if ts:
            try:
                d[f"{col}_iso"] = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            except Exception:
                d[f"{col}_iso"] = None

    # Compute P&L  (trades table has no pnl_value column)
    entry = d.get("entry_price")
    exit_ = d.get("exit_price")
    qty   = d.get("qty")
    if entry is not None and exit_ is not None and qty is not None:
        d["pnl_value"] = round((exit_ - entry) * qty, 2)
    else:
        d["pnl_value"] = None

    # Normalise state so frontend `t.state === "CLOSED"` filter works
    if d.get("state") in CLOSED_STATES:
        d["state"] = "CLOSED"

    # Alias for frontend compatibility
    d["tradingsymbol"] = d.get("symbol", "")

    return d


def _query_trades(from_ts, to_ts, strategy_id):
    if not DB_PATH.exists():
        return []

    conn = _get_db()
    try:
        clauses = []
        params  = []

        if from_ts is not None:
            clauses.append("entry_time >= ?")
            params.append(from_ts)
        if to_ts is not None:
            clauses.append("entry_time < ?")
            params.append(to_ts)
        if strategy_id and strategy_id != "all":
            clauses.append("strategy_id = ?")
            params.append(strategy_id)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        rows = conn.execute(
            f"SELECT * FROM trades {where} ORDER BY entry_time ASC",
            params,
        ).fetchall()
    finally:
        conn.close()

    return [_row_to_dict(row) for row in rows]


# ── /trades/today ─────────────────────────────────────────────
# Returns a FLAT LIST — Analytics.jsx does Array.isArray() check.

@router.get("/trades/today")
def get_today_trades():
    today      = date.today()
    start_unix = int(datetime(today.year, today.month, today.day, 0, 0, 0).timestamp())
    end_unix   = start_unix + 86400
    return _query_trades(start_unix, end_unix, None)


# ── /trades/history ────────────────────────────────────────────
# Supports arbitrary date range + optional strategy filter.
# Used by the full Analytics page.

@router.get("/trades/history")
def get_trade_history(
    from_ts:     Optional[int] = Query(None, description="Unix timestamp start (inclusive)"),
    to_ts:       Optional[int] = Query(None, description="Unix timestamp end (exclusive)"),
    strategy_id: Optional[str] = Query(None, description="BB_V1 | SCALP_V1 | omit for all"),
):
    return _query_trades(from_ts, to_ts, strategy_id)