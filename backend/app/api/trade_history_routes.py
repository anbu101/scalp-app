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

    # Compute P&L  (trades table has no pnl_value column — computed on read).
    # Direction-aware, mirroring close_paper_trade() in paper_trades_repo.py:
    #   LONG  (BB / HA): (exit - entry) * qty
    #   SHORT (SCALP_V1/V2 option selling): (entry - exit) * qty
    # trade_direction is written by TradeStateManager ('SHORT' for sells).
    # Falls back to LONG only when the column is missing/NULL (old rows).
    entry = d.get("entry_price")
    exit_ = d.get("exit_price")
    qty   = d.get("qty")
    direction = (d.get("trade_direction") or "LONG").upper()
    if entry is not None and exit_ is not None and qty is not None:
        if direction == "SHORT":
            d["pnl_value"] = round((entry - exit_) * qty, 2)
        else:
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

    result = [_row_to_dict(row) for row in rows]

    # ── SCALP_V3 LIVE union (isolated — never breaks live history) ──
    # Only add V3 rows when the strategy filter would include them.
    if (not strategy_id) or strategy_id == "all" or strategy_id == "SCALP_V3":
        try:
            result.extend(_query_scalp_v3_live(from_ts, to_ts))
        except Exception:
            # V3 table may not exist yet (strategy never ran) — ignore.
            pass

    # ── SCALP_V4 LIVE union (isolated — never breaks live history) ──
    # Same buy-hedge mapping as V3, from scalp_v4_trades (paper=0).
    if (not strategy_id) or strategy_id == "all" or strategy_id == "SCALP_V4":
        try:
            result.extend(_query_scalp_v4_live(from_ts, to_ts))
        except Exception:
            # V4 table may not exist yet (strategy never ran) — ignore.
            pass

    # SCALPV5_HISTORY BEGIN
    # ── SCALP_V5 LIVE union (isolated — never breaks live history) ──
    # V5 buys weekly options; the traded contract IS the position (no hedge).
    # Own table scalpv5_trades, paper=0 for live rows.
    if (not strategy_id) or strategy_id == "all" or strategy_id == "SCALP_V5":
        try:
            result.extend(_query_scalp_v5_live(from_ts, to_ts))
        except Exception:
            # V5 table may not exist yet (strategy never ran) — ignore.
            pass
    # SCALPV5_HISTORY END

    # PST_HISTORY BEGIN
    # ── PST_SELL / PST_HEDGE LIVE unions (isolated — never break history) ──
    # Own tables; paper rows (mode='PAPER') stay on the PST panels; only
    # LIVE rows join the live-history endpoint.
    for _pst_sid, _pst_table, _pst_dir in (
            ("PST_SELL", "pst_sell_trades", "SHORT"),
            ("PST_HEDGE", "pst_hedge_trades", "LONG")):
        if (not strategy_id) or strategy_id == "all" or strategy_id == _pst_sid:
            try:
                result.extend(_query_pst_live(_pst_sid, _pst_table, _pst_dir,
                                              from_ts, to_ts))
            except Exception:
                # table may not exist yet (strategy never ran live) — ignore.
                pass
    # PST_HISTORY END

    # TMA_HISTORY BEGIN
    # ── TMA_V1 LIVE union (isolated — never breaks live history) ──
    # Own table tma_trades; one row per leg (SELL monitored / BUY hedge)
    # linked by group_id; only LIVE rows join the live-history endpoint
    # (paper rows stay on the paper-trades page).
    if (not strategy_id) or strategy_id == "all" or strategy_id == "TMA_V1":
        try:
            result.extend(_query_tma_live(from_ts, to_ts))
        except Exception:
            # table may not exist yet (strategy never ran live) — ignore.
            pass
    # TMA_HISTORY END

    # Keep the merged list in entry-time order after the V3/V4/V5 unions.
    result.sort(key=lambda t: t.get("entry_time") or 0)

    return result


# ==================================================
# SCALP_V3 (live, paper=0) → trades-row shape for Analytics
# ==================================================
# The displayed "trade" is the HEDGE (the bought option) — the actual position
# carrying P&L. The signal contract is tracked-only and not shown. Mapped to the
# exact keys Analytics.jsx consumes:
#   trade_id, strategy_id, symbol/tradingsymbol, slot (=side), entry_price,
#   exit_price, qty, sl_price, tp_price(None), trade_direction="LONG",
#   pnl_value, state, sl_order_id(=hedge_gtt_id), entry_time, exit_time,
#   exit_reason.
# trade_direction="LONG" makes isShortTrade() return LONG immediately, so the
# P&L sign, direction pill, and open-trade track orientation are all correct.

def _query_scalp_v3_live(from_ts, to_ts):
    conn = _get_db()
    try:
        # Guard: table may not exist if V3 never ran.
        exists = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='scalp_v3_trades'"
        ).fetchone()
        if not exists:
            return []

        clauses = ["paper = 0"]
        params  = []
        if from_ts is not None:
            clauses.append("entry_time >= ?")
            params.append(from_ts)
        if to_ts is not None:
            clauses.append("entry_time < ?")
            params.append(to_ts)

        where = f"WHERE {' AND '.join(clauses)}"

        rows = conn.execute(
            f"""
            SELECT
                v3_trade_id,
                hedge_symbol,
                hedge_side,
                hedge_qty,
                hedge_entry_price,
                hedge_sl,
                hedge_gtt_id,
                entry_time,
                exit_time,
                exit_price,
                exit_reason,
                realized_pnl,
                state
            FROM scalp_v3_trades
            {where}
            ORDER BY entry_time ASC
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    out = []
    for r in rows:
        d = dict(r)
        entry = d.get("hedge_entry_price")
        exitp = d.get("exit_price")
        qty   = d.get("hedge_qty")
        rpnl  = d.get("realized_pnl")
        state = d.get("state")
        is_closed = (state == "CLOSED")

        # Prefer stored realized_pnl (closed); else compute LONG client-parity.
        if rpnl is not None:
            pnl_value = round(float(rpnl), 2)
        elif entry is not None and exitp is not None and qty is not None:
            pnl_value = round((float(exitp) - float(entry)) * int(qty), 2)
        else:
            pnl_value = None

        symbol = d.get("hedge_symbol") or ""
        trade = {
            "trade_id":        d.get("v3_trade_id"),
            "strategy_id":     "SCALP_V3",
            "symbol":          symbol,
            "tradingsymbol":   symbol,
            "slot":            d.get("hedge_side"),   # CE/PE → extractSide()
            "token":           None,

            "entry_price":     entry,
            "exit_price":      exitp,
            "qty":             qty,

            "sl_price":        d.get("hedge_sl"),
            "tp_price":        None,                  # hedge is SL-only
            "trade_direction": "LONG",                # hedge is a BUY

            "sl_order_id":     d.get("hedge_gtt_id"), # drives "✓ GTT" badge

            "pnl_value":       pnl_value,
            "exit_reason":     d.get("exit_reason"),

            "entry_time":      d.get("entry_time"),
            "exit_time":       d.get("exit_time"),

            # Normalise state to the frontend's OPEN/CLOSED contract.
            "state":           "CLOSED" if is_closed else "OPEN",
        }

        # ISO aliases for parity with _row_to_dict.
        for col in ("entry_time", "exit_time"):
            ts = trade.get(col)
            if ts:
                try:
                    trade[f"{col}_iso"] = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                except Exception:
                    trade[f"{col}_iso"] = None

        out.append(trade)

    return out


# ==================================================
# SCALP_V4 (live, paper=0) → trades-row shape for Analytics
# ==================================================
# Byte-for-byte the same mapping as _query_scalp_v3_live — V4 is a buy-hedge
# clone of V3 with one extra entry gate, stored in its OWN table. The displayed
# "trade" is the HEDGE (the bought option carrying P&L); the signal contract is
# tracked-only and not shown. trade_direction="LONG" so isShortTrade() returns
# LONG and the P&L sign / direction pill / track orientation are all correct.

def _query_scalp_v4_live(from_ts, to_ts):
    conn = _get_db()
    try:
        # Guard: table may not exist if V4 never ran.
        exists = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='scalp_v4_trades'"
        ).fetchone()
        if not exists:
            return []

        clauses = ["paper = 0"]
        params  = []
        if from_ts is not None:
            clauses.append("entry_time >= ?")
            params.append(from_ts)
        if to_ts is not None:
            clauses.append("entry_time < ?")
            params.append(to_ts)

        where = f"WHERE {' AND '.join(clauses)}"

        rows = conn.execute(
            f"""
            SELECT
                v4_trade_id,
                hedge_symbol,
                hedge_side,
                hedge_qty,
                hedge_entry_price,
                hedge_sl,
                hedge_gtt_id,
                entry_time,
                exit_time,
                exit_price,
                exit_reason,
                realized_pnl,
                state
            FROM scalp_v4_trades
            {where}
            ORDER BY entry_time ASC
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    out = []
    for r in rows:
        d = dict(r)
        entry = d.get("hedge_entry_price")
        exitp = d.get("exit_price")
        qty   = d.get("hedge_qty")
        rpnl  = d.get("realized_pnl")
        state = d.get("state")
        is_closed = (state == "CLOSED")

        # Prefer stored realized_pnl (closed); else compute LONG client-parity.
        if rpnl is not None:
            pnl_value = round(float(rpnl), 2)
        elif entry is not None and exitp is not None and qty is not None:
            pnl_value = round((float(exitp) - float(entry)) * int(qty), 2)
        else:
            pnl_value = None

        symbol = d.get("hedge_symbol") or ""
        trade = {
            "trade_id":        d.get("v4_trade_id"),
            "strategy_id":     "SCALP_V4",
            "symbol":          symbol,
            "tradingsymbol":   symbol,
            "slot":            d.get("hedge_side"),   # CE/PE → extractSide()
            "token":           None,

            "entry_price":     entry,
            "exit_price":      exitp,
            "qty":             qty,

            "sl_price":        d.get("hedge_sl"),
            "tp_price":        None,                  # hedge is SL-only
            "trade_direction": "LONG",                # hedge is a BUY

            "sl_order_id":     d.get("hedge_gtt_id"), # drives "✓ GTT" badge

            "pnl_value":       pnl_value,
            "exit_reason":     d.get("exit_reason"),

            "entry_time":      d.get("entry_time"),
            "exit_time":       d.get("exit_time"),

            # Normalise state to the frontend's OPEN/CLOSED contract.
            "state":           "CLOSED" if is_closed else "OPEN",
        }

        # ISO aliases for parity with _row_to_dict.
        for col in ("entry_time", "exit_time"):
            ts = trade.get(col)
            if ts:
                try:
                    trade[f"{col}_iso"] = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                except Exception:
                    trade[f"{col}_iso"] = None

        out.append(trade)

    return out


# SCALPV5_HISTORY_FN BEGIN
# ==================================================
# SCALP_V5 (live, paper=0) → trades-row shape for Analytics
# ==================================================
# V5 is option BUYING where the signalling contract IS the position (no hedge
# leg). Mapped to the exact keys Analytics.jsx consumes. Differences from V3/V4:
#   - real tp_price (not SL-only)
#   - direction already 'LONG' in-table; forced 'LONG' here for isShortTrade()
#   - PK is v5_trade_id; P&L stored in realized_pnl
# state in-table is 'OPEN'/'CLOSED' already, so no CLOSED_STATES remap needed.

def _query_scalp_v5_live(from_ts, to_ts):
    conn = _get_db()
    try:
        # Guard: table may not exist if V5 never ran.
        exists = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='scalpv5_trades'"
        ).fetchone()
        if not exists:
            return []

        clauses = ["paper = 0"]
        params  = []
        if from_ts is not None:
            clauses.append("entry_time >= ?")
            params.append(from_ts)
        if to_ts is not None:
            clauses.append("entry_time < ?")
            params.append(to_ts)

        where = f"WHERE {' AND '.join(clauses)}"

        rows = conn.execute(
            f"""
            SELECT
                v5_trade_id,
                symbol,
                side,
                qty,
                entry_price,
                sl_price,
                tp_price,
                gtt_id,
                entry_time,
                exit_time,
                exit_price,
                exit_reason,
                realized_pnl,
                state
            FROM scalpv5_trades
            {where}
            ORDER BY entry_time ASC
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    out = []
    for r in rows:
        d = dict(r)
        entry = d.get("entry_price")
        exitp = d.get("exit_price")
        qty   = d.get("qty")
        rpnl  = d.get("realized_pnl")
        state = d.get("state")
        is_closed = (state == "CLOSED")

        # Prefer stored realized_pnl (closed); else compute LONG client-parity.
        if rpnl is not None:
            pnl_value = round(float(rpnl), 2)
        elif entry is not None and exitp is not None and qty is not None:
            pnl_value = round((float(exitp) - float(entry)) * int(qty), 2)
        else:
            pnl_value = None

        symbol = d.get("symbol") or ""
        trade = {
            "trade_id":        d.get("v5_trade_id"),
            "strategy_id":     "SCALP_V5",
            "symbol":          symbol,
            "tradingsymbol":   symbol,
            "slot":            d.get("side"),         # CE/PE → extractSide()
            "token":           None,

            "entry_price":     entry,
            "exit_price":      exitp,
            "qty":             qty,

            "sl_price":        d.get("sl_price"),
            "tp_price":        d.get("tp_price"),     # V5 has a real TP
            "trade_direction": "LONG",                # V5 buys

            "sl_order_id":     d.get("gtt_id"),       # drives "✓ GTT" badge

            "pnl_value":       pnl_value,
            "exit_reason":     d.get("exit_reason"),

            "entry_time":      d.get("entry_time"),
            "exit_time":       d.get("exit_time"),

            # V5 state is already OPEN/CLOSED; pass through defensively.
            "state":           "CLOSED" if is_closed else "OPEN",
        }

        # ISO aliases for parity with _row_to_dict.
        for col in ("entry_time", "exit_time"):
            ts = trade.get(col)
            if ts:
                try:
                    trade[f"{col}_iso"] = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                except Exception:
                    trade[f"{col}_iso"] = None

        out.append(trade)

    return out
# SCALPV5_HISTORY_FN END


# ── /trades/today ─────────────────────────────────────────────
# Returns a FLAT LIST — Analytics.jsx does Array.isArray() check.
# PST_HISTORY BEGIN
def _query_pst_live(sid, table, direction, from_ts, to_ts):
    conn = _get_db()
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()
        if not exists:
            return []
        clauses = ["mode = 'LIVE'"]
        params = []
        if from_ts is not None:
            clauses.append("entry_ts >= ?"); params.append(from_ts)
        if to_ts is not None:
            clauses.append("entry_ts < ?"); params.append(to_ts)
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE {' AND '.join(clauses)} "
            f"ORDER BY entry_ts ASC", params).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        entry, exitp, qty = d.get("entry_price"), d.get("exit_price"), d.get("qty")
        npnl = d.get("net_pnl")
        if npnl is not None:
            pnl_value = round(float(npnl), 2)
        elif entry is not None and exitp is not None and qty is not None:
            sgn = -1 if direction == "SHORT" else 1
            pnl_value = round(sgn * (float(exitp) - float(entry)) * int(qty), 2)
        else:
            pnl_value = None
        sym = d.get("tradingsymbol") or ""
        trade = {
            "trade_id":        f"{table}:{d.get('id')}",
            "strategy_id":     sid,
            "symbol":          sym,
            "tradingsymbol":   sym,
            "slot":            d.get("instrument_type"),
            "token":           None,
            "entry_price":     entry,
            "exit_price":      exitp,
            "qty":             qty,
            "sl_price":        None,   # PST SL is a SPOT level — not a premium
            "tp_price":        d.get("tp"),
            "trade_direction": direction,
            "sl_order_id":     None,
            "pnl_value":       pnl_value,
            "exit_reason":     d.get("exit_reason"),
            "entry_time":      d.get("entry_ts"),
            "exit_time":       d.get("exit_ts"),
            "state":           "OPEN" if d.get("status") == "OPEN" else "CLOSED",
        }
        for col in ("entry_time", "exit_time"):
            ts = trade.get(col)
            if ts is not None:
                from datetime import datetime, timezone
                trade[col + "_iso"] = datetime.fromtimestamp(
                    int(ts), tz=timezone.utc).isoformat()
        out.append(trade)
    return out
# PST_HISTORY END


# TMA_HISTORY BEGIN
def _query_tma_live(from_ts, to_ts):
    """tma_trades (mode='LIVE') → trades-row shape for Analytics. Direction
    is PER ROW (SELL leg → SHORT, BUY hedge → LONG) — unlike PST's fixed
    per-table mapping. group_id + trade_class(direction) travel through so
    the Analytics page can pair the two legs of one spread."""
    conn = _get_db()
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tma_trades'"
        ).fetchone()
        if not exists:
            return []
        clauses = ["mode = 'LIVE'"]
        params = []
        if from_ts is not None:
            clauses.append("entry_ts >= ?"); params.append(from_ts)
        if to_ts is not None:
            clauses.append("entry_ts < ?"); params.append(to_ts)
        rows = conn.execute(
            f"SELECT * FROM tma_trades WHERE {' AND '.join(clauses)} "
            f"ORDER BY entry_ts ASC", params).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        direction = "SHORT" if d.get("direction") == "SELL" else "LONG"
        entry, exitp, qty = d.get("entry_price"), d.get("exit_price"), d.get("qty")
        npnl = d.get("net_pnl")
        if npnl is not None:
            pnl_value = round(float(npnl), 2)
        elif entry is not None and exitp is not None and qty is not None:
            sgn = -1 if direction == "SHORT" else 1
            pnl_value = round(sgn * (float(exitp) - float(entry)) * int(qty), 2)
        else:
            pnl_value = None
        sym = d.get("tradingsymbol") or ""
        trade = {
            "trade_id":        f"tma_trades:{d.get('id')}",
            "strategy_id":     "TMA_V1",
            "symbol":          sym,
            "tradingsymbol":   sym,
            "slot":            d.get("instrument_type"),
            "token":           d.get("token"),
            "entry_price":     entry,
            "exit_price":      exitp,
            "qty":             qty,
            "sl_price":        d.get("sl"),
            "tp_price":        d.get("tp"),
            "trade_direction": direction,
            "group_id":        d.get("group_id"),
            "trade_class":     d.get("direction"),   # SELL | BUY (leg role)
            "sl_order_id":     d.get("sell_gtt_id"),
            "pnl_value":       pnl_value,
            "exit_reason":     d.get("exit_reason"),
            "entry_time":      d.get("entry_ts"),
            "exit_time":       d.get("exit_ts"),
            "state":           "OPEN" if d.get("status") == "OPEN" else "CLOSED",
        }
        for col in ("entry_time", "exit_time"):
            ts = trade.get(col)
            if ts is not None:
                from datetime import datetime, timezone
                trade[col + "_iso"] = datetime.fromtimestamp(
                    int(ts), tz=timezone.utc).isoformat()
        out.append(trade)
    return out
# TMA_HISTORY END


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
    strategy_id: Optional[str] = Query(None, description="BB_V1 | BB_V2 | HA_V1 | SCALP_V3 | SCALP_V4 | SCALP_V5 | omit for all"),
):
    return _query_trades(from_ts, to_ts, strategy_id)