# backend/app/api/futures_candles_routes.py
"""
PHASE 2b REPLACEMENT — adds ui_level-based response masking.

For non-admin licenses (license_state.ui_level() != "admin"), each candle
is reduced to a WHITELIST of public fields: ts, ts_iso, open, high, low,
close, volume, oi. Everything else — BB bands/width, RSI, SuperTrend +
direction, signal_action/reason, rejection_reason, pivots (r1/s1/r2/pp/
s2/s3) and any future indicator column — is stripped server-side, so the
strategy internals never leave the backend. BBPanel already null-guards
every indicator render, so non-admin users simply see a clean OHLC chart
with their own trades/P&L. No frontend change required.

Whitelist (not blacklist) on purpose: a new indicator column added to
futures_candles later is masked by default instead of leaking.

ADMIN licenses (ui_level "admin") get the exact same response as before
this change — the masking branch is skipped entirely.

Everything else in this file is verbatim from the previous version.
"""

from fastapi import APIRouter, Query
from pathlib import Path
from datetime import datetime, timezone
import sqlite3

from app.license import license_state

router = APIRouter(tags=["futures-candles"])

DB_PATH = Path.home() / ".scalp-app" / "data" / "app.db"

# Fields a NON-ADMIN client is allowed to see per candle. Anything not in
# this set is stripped before the response leaves the backend.
_PUBLIC_CANDLE_FIELDS = {
    "ts", "ts_iso", "open", "high", "low", "close", "volume", "oi",
}


def _mask_candle(d: dict) -> dict:
    return {k: v for k, v in d.items() if k in _PUBLIC_CANDLE_FIELDS}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='futures_candles'"
    ).fetchone()
    return row is not None


def _resolve_symbol(conn, symbol: str, timeframe: str):
    """
    If symbol is 'auto' (or empty), return the symbol with the most
    recent candle for the given timeframe. This handles monthly expiry
    rollovers automatically — no hardcoded symbol names needed.

    Returns None if the table doesn't exist yet (fresh install).
    """
    if not _table_exists(conn):
        return None

    if symbol and symbol.lower() != "auto":
        return symbol

    row = conn.execute(
        """
        SELECT symbol FROM futures_candles
        WHERE  timeframe = ?
        ORDER  BY ts DESC
        LIMIT  1
        """,
        (timeframe,),
    ).fetchone()

    return row["symbol"] if row else None


@router.get("/futures/symbols")
def list_symbols():
    """Returns all distinct symbols available in futures_candles table."""
    if not DB_PATH.exists():
        return {"symbols": []}

    conn = _get_db()
    try:
        if not _table_exists(conn):
            return {"symbols": []}

        rows = conn.execute(
            "SELECT DISTINCT symbol FROM futures_candles ORDER BY symbol"
        ).fetchall()
    finally:
        conn.close()

    return {"symbols": [r["symbol"] for r in rows]}


@router.get("/futures/candles")
def get_futures_candles(
    symbol:    str = Query("auto"),
    timeframe: str = Query("3m"),
    limit:     int = Query(80, ge=10, le=3000),
):
    """
    Returns the last `limit` candles for the given symbol+timeframe.

    symbol defaults to 'auto' — the backend picks whichever symbol has
    the most recent candle. This handles monthly expiry rollovers without
    any frontend changes.

    Returns an empty candles list (not a 500) when the futures_candles
    table does not yet exist — this happens on fresh installs before the
    BB engine has started for the first time.

    PHASE 2b: indicator/signal columns are stripped for non-admin
    licenses (see _PUBLIC_CANDLE_FIELDS above).
    """
    if not DB_PATH.exists():
        return {"symbol": None, "timeframe": timeframe, "count": 0, "candles": []}

    conn = _get_db()
    try:
        resolved_symbol = _resolve_symbol(conn, symbol, timeframe)

        # Table not yet created (fresh install) or no data yet
        if resolved_symbol is None:
            return {
                "symbol":    None,
                "timeframe": timeframe,
                "count":     0,
                "candles":   [],
            }

        rows = conn.execute(
            """
            SELECT *
            FROM   futures_candles
            WHERE  symbol = ? AND timeframe = ?
            ORDER  BY ts DESC
            LIMIT  ?
            """,
            (resolved_symbol, timeframe, limit),
        ).fetchall()
    finally:
        conn.close()

    # PHASE 2b: decide masking ONCE per request. ui_level() returns
    # "standard" for any non-usable/non-admin state, so this fails closed.
    mask = license_state.ui_level() != "admin"

    # Reverse so candles are chronological (oldest → newest = left → right on chart)
    candles = []
    for row in reversed(rows):
        d = dict(row)
        if d.get("ts"):
            try:
                d["ts_iso"] = datetime.fromtimestamp(d["ts"], tz=timezone.utc).isoformat()
            except Exception:
                d["ts_iso"] = None
        if mask:
            d = _mask_candle(d)
        candles.append(d)

    return {
        "symbol":    resolved_symbol,
        "timeframe": timeframe,
        "count":     len(candles),
        "candles":   candles,
    }