from fastapi import APIRouter
from pathlib import Path
from datetime import date, datetime, timezone
import sqlite3

router = APIRouter(tags=["trade-history"])

# DB lives at ~/.scalp-app/data/app.db — Path.home() works on macOS and Windows
DB_PATH = Path.home() / ".scalp-app" / "data" / "app.db"

# States where a trade is fully closed and P&L is realised
CLOSED_STATES = {"SL_HIT", "TP_HIT", "EXITED", "CLOSED"}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)

    # entry_time and exit_time are stored as INTEGER unix timestamps
    # Convert to ISO string for the frontend
    if d.get("entry_time"):
        try:
            d["entry_time_iso"] = datetime.fromtimestamp(
                d["entry_time"], tz=timezone.utc
            ).isoformat()
        except Exception:
            d["entry_time_iso"] = None

    if d.get("exit_time"):
        try:
            d["exit_time_iso"] = datetime.fromtimestamp(
                d["exit_time"], tz=timezone.utc
            ).isoformat()
        except Exception:
            d["exit_time_iso"] = None

    # Compute realised P&L for closed trades
    # trades table has no pnl_value column — calculate from prices
    entry = d.get("entry_price")
    exit_ = d.get("exit_price")
    qty   = d.get("qty")
    if entry is not None and exit_ is not None and qty is not None:
        d["pnl_value"] = round((exit_ - entry) * qty, 2)
    else:
        d["pnl_value"] = None   # open trade — unrealised

    # Alias for frontend compatibility (Analytics uses tradingsymbol)
    d["tradingsymbol"] = d.get("symbol", "")

    return d


@router.get("/trades/today")
def get_today_trades():
    """
    Returns today's trades from the SQLite trades table.
    Split into open (not yet closed) and closed (terminal state) lists.
    P&L is computed server-side as (exit_price - entry_price) * qty.
    """
    if not DB_PATH.exists():
        return {"open": [], "closed": [], "error": f"DB not found at {DB_PATH}"}

    # Today's date range in unix timestamps (local time)
    today      = date.today()
    start_unix = int(datetime(today.year, today.month, today.day, 0, 0, 0).timestamp())
    end_unix   = start_unix + 86400  # next midnight

    conn = _get_db()
    try:
        rows = conn.execute(
            """
            SELECT * FROM trades
            WHERE entry_time >= ? AND entry_time < ?
            ORDER BY entry_time ASC
            """,
            (start_unix, end_unix),
        ).fetchall()
    finally:
        conn.close()

    open_trades   = []
    closed_trades = []

    for row in rows:
        d = _row_to_dict(row)
        if d.get("state") in CLOSED_STATES:
            closed_trades.append(d)
        else:
            open_trades.append(d)

    return {"open": open_trades, "closed": closed_trades}