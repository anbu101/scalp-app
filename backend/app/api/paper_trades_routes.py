from fastapi import APIRouter
from typing import List, Dict, Any
from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log

router = APIRouter(tags=["paper-trades"])


@router.get("/paper_trades")
def get_paper_trades():
    """
    📄 Paper Trades – UI API

    - Returns OPEN and CLOSED separately
    - Includes Zerodha option charges + net PnL
    - Matches frontend contract
    - UNIONS in SCALP_V3 paper rows (scalp_v3_trades, paper=1), mapped to the
      legacy paper_trades display shape using the HEDGE leg (the bought option
      is the position; the signal contract is only tracked). SCALP_V3 mapping is
      fully isolated in its own try/except so it can NEVER break the existing
      paper_trades response.
    """

    conn = get_conn()

    open_trades: List[Dict[str, Any]] = []
    closed_trades: List[Dict[str, Any]] = []

    # --------------------------------------------------
    # 1) Existing paper_trades (unchanged)
    # --------------------------------------------------
    try:
        cur = conn.execute(
            """
            SELECT
                paper_trade_id,
                strategy_name,
                trade_mode,
                symbol,
                token,
                side,

                entry_time,
                entry_price,
                candle_ts,

                sl_price,
                tp_price,
                rr,

                lots,
                lot_size,
                qty,

                exit_time,
                exit_price,
                exit_reason,

                pnl_points,
                pnl_value,

                brokerage,
                stt,
                exchange_charges,
                sebi_charges,
                stamp_duty,
                gst,
                total_charges,
                net_pnl,

                state,
                created_at
            FROM paper_trades
            ORDER BY entry_time DESC
            """
        )

        for r in cur.fetchall():
            trade = dict(r)
            if trade["state"] == "OPEN":
                open_trades.append(trade)
            else:
                closed_trades.append(trade)

    except Exception as e:
        write_audit_log(f"[API][PAPER_TRADES][ERROR] {repr(e)}")
        return {"open": [], "closed": [], "error": str(e)}

    # --------------------------------------------------
    # 2) SCALP_V3 paper rows (isolated — never breaks the above)
    #    Mapped to the legacy shape using the HEDGE leg.
    # --------------------------------------------------
    try:
        v3_open, v3_closed = _load_scalp_v3_paper(conn)
        open_trades.extend(v3_open)
        closed_trades.extend(v3_closed)

        # Keep each list newest-first after the merge.
        open_trades.sort(key=lambda t: t.get("entry_time") or 0, reverse=True)
        closed_trades.sort(key=lambda t: t.get("entry_time") or 0, reverse=True)
    except Exception as e:
        # V3 table may not exist yet (strategy never ran) — that's fine.
        write_audit_log(f"[API][PAPER_TRADES][V3][SKIP] {repr(e)}")

    return {"open": open_trades, "closed": closed_trades}


# ==================================================
# SCALP_V3 → legacy paper_trades shape (hedge leg)
# ==================================================

def _load_scalp_v3_paper(conn):
    """
    Map scalp_v3_trades (paper=1) onto the legacy paper_trades display shape.

    The displayed "trade" is the HEDGE (the bought option) — that is the actual
    position carrying P&L. The signal contract is tracked-only and not shown.

      symbol      ← hedge_symbol
      side        ← hedge_side
      entry_price ← hedge_entry_price
      sl_price    ← hedge_sl          (hedge_sl < entry ⇒ frontend infers LONG ✓)
      tp_price    ← None              (SL-only GTT — no hedge TP; renders "—")
      qty         ← hedge_qty
      pnl_value   ← realized_pnl      (closed only; open rows priced live by UI)
      state       ← OPEN | CLOSED

    Charge-breakdown fields are returned as None/0 — the frontend recomputes
    charges itself from entry/exit/qty (V3 stores gross P&L only, by design).
    """
    # Guard: only query if the table exists (V3 may never have run).
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scalp_v3_trades'"
    ).fetchone()
    if not exists:
        return [], []

    cur = conn.execute(
        """
        SELECT
            v3_trade_id,
            strategy_name,
            hedge_symbol,
            hedge_side,
            hedge_qty,
            hedge_entry_price,
            hedge_sl,
            entry_time,
            exit_time,
            exit_price,
            exit_reason,
            realized_pnl,
            state
        FROM scalp_v3_trades
        WHERE paper = 1
        ORDER BY entry_time DESC
        """
    )

    open_v3: List[Dict[str, Any]] = []
    closed_v3: List[Dict[str, Any]] = []

    for r in cur.fetchall():
        row = dict(r)
        entry = row.get("hedge_entry_price")
        qty   = row.get("hedge_qty")
        exitp = row.get("exit_price")
        rpnl  = row.get("realized_pnl")
        is_open = (row.get("state") == "OPEN")

        # pnl_points (per-unit) for parity with the legacy shape.
        pnl_points = None
        if (not is_open) and rpnl is not None and qty:
            try:
                pnl_points = float(rpnl) / float(qty)
            except Exception:
                pnl_points = None

        trade = {
            "paper_trade_id": row.get("v3_trade_id"),
            "strategy_name":  row.get("strategy_name") or "SCALP_V3",
            "trade_mode":     "PAPER",
            "symbol":         row.get("hedge_symbol"),
            "token":          None,
            "side":           row.get("hedge_side"),

            "entry_time":     row.get("entry_time"),
            "entry_price":    entry,
            "candle_ts":      None,

            "sl_price":       row.get("hedge_sl"),
            "tp_price":       None,          # hedge is SL-only
            "rr":             None,

            "lots":           None,
            "lot_size":       None,
            "qty":            qty,

            "exit_time":      row.get("exit_time"),
            "exit_price":     exitp,
            "exit_reason":    row.get("exit_reason"),

            "pnl_points":     pnl_points,
            "pnl_value":      (rpnl if not is_open else None),

            # Charge breakdown not stored for V3 — frontend recomputes.
            "brokerage":        None,
            "stt":              None,
            "exchange_charges": None,
            "sebi_charges":     None,
            "stamp_duty":       None,
            "gst":              None,
            "total_charges":    None,
            "net_pnl":          (rpnl if not is_open else None),

            "state":          row.get("state"),
            "created_at":     row.get("entry_time"),
        }

        if is_open:
            open_v3.append(trade)
        else:
            closed_v3.append(trade)

    return open_v3, closed_v3