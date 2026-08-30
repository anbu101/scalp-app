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
      V4 is a clone of V3 (buy-hedge) with one extra entry gate, and lives in its
      OWN table — so it needs its own mapping/union, also fully isolated.
    - UNIONS in SCALP_V5 paper rows (scalpv5_trades, paper=1). V5 is
      SINGLE-INSTRUMENT (option BUYING) — it buys the signalling contract itself,
      so there is NO hedge leg: symbol/entry/sl/tp/qty map DIRECTLY (not hedge_*).
      Also fully isolated in its own try/except.

    NOTE: This endpoint intentionally returns ALL paper trades (all strategies,
    all time). Date-range scoping is a CONSUMER concern — the Paper Trades page
    owns its own Today/Week/Month/All-Time tabs and slices client-side, and each
    strategy dashboard panel scopes to its own window client-side. Do NOT add a
    date filter here: it silently breaks the Paper Trades page's date tabs.
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
                COALESCE(trade_direction, 'LONG') AS trade_direction,

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
    except Exception as e:
        # V3 table may not exist yet (strategy never ran) — that's fine.
        write_audit_log(f"[API][PAPER_TRADES][V3][SKIP] {repr(e)}")

    # --------------------------------------------------
    # 4) SCALP_V5 paper rows (isolated — never breaks the above)
    #    SINGLE-INSTRUMENT (no hedge): symbol/entry/sl/tp/qty map directly.
    # --------------------------------------------------
    try:
        v5_open, v5_closed = _load_scalpv5_paper(conn)
        open_trades.extend(v5_open)
        closed_trades.extend(v5_closed)
    except Exception as e:
        # V5 table may not exist yet (strategy never opened a trade) — that's fine.
        write_audit_log(f"[API][PAPER_TRADES][V5][SKIP] {repr(e)}")

    # --------------------------------------------------
    # 5) PST_SELL / PST_HEDGE paper rows (isolated — never breaks the above).
    #    Own tables (mode='PAPER'); rows carry authoritative net_pnl +
    #    charges from the backtest's charges_model — passed through,
    #    never recomputed, so the page matches backtest CSVs to the rupee.
    # --------------------------------------------------
    for _pst_sid, _pst_table in (("PST_SELL", "pst_sell_trades"),
                                 ("PST_HEDGE", "pst_hedge_trades")):
        try:
            _po, _pc = _load_pst_paper(conn, _pst_sid, _pst_table)
            open_trades.extend(_po)
            closed_trades.extend(_pc)
        except Exception as e:
            # table may not exist yet (strategy never ran) — that's fine.
            write_audit_log(f"[API][PAPER_TRADES][{_pst_sid}][SKIP] {repr(e)}")

    # --------------------------------------------------
    # 6) TMA_V1 paper rows (isolated — never breaks the above).
    #    Own table tma_trades (mode='PAPER'); one row PER LEG (SELL
    #    monitored / BUY hedge) linked by group_id; net_pnl + charges come
    #    from leg_net (backtest charges_model) — passed through.
    # --------------------------------------------------
    # TMA_PAPER BEGIN
    try:
        _to, _tc = _load_tma_paper(conn)
        open_trades.extend(_to)
        closed_trades.extend(_tc)
    except Exception as e:
        # table may not exist yet (strategy never ran) — that's fine.
        write_audit_log(f"[API][PAPER_TRADES][TMA_V1][SKIP] {repr(e)}")
    # TMA_PAPER END

    # --------------------------------------------------
    # 7) TMA_V2 paper rows — same private-table shape, own table.
    # --------------------------------------------------
    # TMA2_PAPER BEGIN
    try:
        _t2o, _t2c = _load_tma_paper(conn, table="tma2_trades",
                                     strategy_name="TMA_V2")
        open_trades.extend(_t2o)
        closed_trades.extend(_t2c)
    except Exception as e:
        write_audit_log(f"[API][PAPER_TRADES][TMA_V2][SKIP] {repr(e)}")
    # TMA2_PAPER END

    # VET_PAPER BEGIN — vet_trades PAPER rows through the SAME widened
    # mapper: SELECT * makes the absent sl/tp read None (VET has no GTT
    # layer by design), and the direction check accepts SHORT.
    try:
        _vo, _vc = _load_tma_paper(conn, table="vet_trades",
                                   strategy_name="VET_V1")
        open_trades.extend(_vo)
        closed_trades.extend(_vc)
    except Exception as e:
        write_audit_log(f"[API][PAPER_TRADES][VET_V1][SKIP] {repr(e)}")
    # VET_PAPER END

    # Keep each list newest-first after all merges.
    open_trades.sort(key=lambda t: t.get("entry_time") or 0, reverse=True)
    closed_trades.sort(key=lambda t: t.get("entry_time") or 0, reverse=True)

    return {"open": open_trades, "closed": closed_trades}


# ==================================================
# SCALP_V3 → legacy paper_trades shape (hedge leg)
# ==================================================

# ==================================================
# PST_SELL / PST_HEDGE → legacy paper_trades shape
# ==================================================

def _load_pst_paper(conn, sid, table):
    """Map a PST table's PAPER rows onto the legacy display shape.

      symbol/side   ← tradingsymbol / instrument_type (the HELD contract;
                      PST_HEDGE's tracked signal contract is not shown here)
      sl_price      ← None (PST's SL is a SPOT level, not an option premium)
      tp_price      ← tp   (PST_SELL: own premium level · PST_HEDGE: the
                      SIGNAL contract's level — display-only either way)
      pnl/charges   ← passed through from the table (backtest charges_model)
      STALE rows (restart hygiene) map to CLOSED, no P&L.

    Display-only caveat: the legacy shape has no direction field, so an
    OPEN PST_SELL row's live-priced P&L may show the LONG sign for the
    minutes it is open; the stored (correct) short P&L takes over at close.
    """
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        return [], []
    cur = conn.execute(
        f"""
        SELECT id, leg_id, tradingsymbol, instrument_type, qty,
               entry_ts, entry_price, tp, exit_ts, exit_price, exit_reason,
               pnl, charges, net_pnl, status
        FROM {table}
        WHERE mode = 'PAPER'
        ORDER BY entry_ts DESC
        """
    )
    out_open, out_closed = [], []
    for r in cur.fetchall():
        row = dict(r)
        is_open = (row.get("status") == "OPEN")
        qty = row.get("qty")
        npnl = row.get("net_pnl")
        pnl_points = None
        if (not is_open) and row.get("pnl") is not None and qty:
            try:
                pnl_points = float(row.get("pnl")) / float(qty)
            except Exception:
                pnl_points = None
        trade = {
            "paper_trade_id": f"{table}:{row.get('id')}",
            "strategy_name":  sid,
            "trade_mode":     "PAPER",
            "symbol":         row.get("tradingsymbol"),
            "token":          None,
            "side":           row.get("instrument_type"),
            # direction fix 2026-08-03: legacy tables lack the column, but
            # the strategy id determines it — PST_SELL shorts, others long.
            "trade_direction": "SHORT" if sid == "PST_SELL" else "LONG",
            "entry_time":     row.get("entry_ts"),
            "entry_price":    row.get("entry_price"),
            "candle_ts":      None,
            "sl_price":       None,
            "tp_price":       row.get("tp"),
            "rr":             None,
            "lots":           (int(qty) // 65 if qty else None),
            "lot_size":       65,
            "qty":            qty,
            "exit_time":      row.get("exit_ts"),
            "exit_price":     row.get("exit_price"),
            "exit_reason":    row.get("exit_reason"),
            "pnl_points":     pnl_points,
            # pnl_value must be GROSS — the page deducts its own recomputed
            # charges from it (2026-07-14: mapping stored NET here double-
            # charged PST rows by ~₹1,048 on the page).
            "pnl_value":      (row.get("pnl") if not is_open else None),
            "brokerage":        None,
            "stt":              None,
            "exchange_charges": None,
            "sebi_charges":     None,
            "stamp_duty":       None,
            "gst":              None,
            "total_charges":    (row.get("charges") if not is_open else None),
            "net_pnl":          (npnl if not is_open else None),
            "state":          "OPEN" if is_open else "CLOSED",
            "created_at":     row.get("entry_ts"),
        }
        (out_open if is_open else out_closed).append(trade)
    return out_open, out_closed


# ==================================================
# TMA_V1 → legacy paper_trades shape (both legs)
# ==================================================

# TMA_PAPER BEGIN
def _load_tma_paper(conn, table="tma_trades", strategy_name="TMA_V1"):
    """Map tma_trades PAPER rows onto the legacy display shape. Each spread
    shows as TWO rows sharing a group_id suffix in the id: the SELL leg
    (carries sl/tp) and the BUY hedge (no levels).

    Display-only caveat (PST precedent): the legacy shape has no direction
    field, so an OPEN SELL row's live-priced P&L may show the LONG sign
    while open; the stored (correct) short P&L takes over at close.
    pnl_value is GROSS (2026-07-14 learning: the page deducts its own
    recomputed charges — storing NET here double-charges the row)."""
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    ).fetchone()
    if not exists:
        return [], []
    # table is NOT user input — it is a literal from this module's own call
    # sites (tma_trades / tma2_trades) and was existence-checked above, so
    # interpolating it is safe; SQLite cannot bind a table name as a param.
    cur = conn.execute(
        f"""
        SELECT *
        FROM {table}
        WHERE mode = 'PAPER'
        ORDER BY entry_ts DESC
        """
    )
    out_open, out_closed = [], []
    for r in cur.fetchall():
        row = dict(r)
        is_open = (row.get("status") == "OPEN")
        qty = row.get("qty")
        npnl = row.get("net_pnl")
        pnl_points = None
        if (not is_open) and row.get("pnl") is not None and qty:
            try:
                pnl_points = float(row.get("pnl")) / float(qty)
            except Exception:
                pnl_points = None
        # SELL = tma legs · SHORT = vet_trades main leg (widened 2026-08-29)
        is_sell = (row.get("direction") in ("SELL", "SHORT"))
        trade = {
            "paper_trade_id": f"{table}:{row.get('id')}",
            "strategy_name":  strategy_name,
            "trade_mode":     "PAPER",
            "symbol":         row.get("tradingsymbol"),
            "token":          None,
            "side":           row.get("instrument_type"),
            "trade_direction": "SHORT" if is_sell else "LONG",   # 2026-08-03
            "entry_time":     row.get("entry_ts"),
            "entry_price":    row.get("entry_price"),
            "candle_ts":      None,
            "sl_price":       (row.get("sl") if is_sell else None),
            "tp_price":       (row.get("tp") if is_sell else None),
            "rr":             None,
            "lots":           (int(qty) // 65 if qty else None),
            "lot_size":       65,
            "qty":            qty,
            "exit_time":      row.get("exit_ts"),
            "exit_price":     row.get("exit_price"),
            "exit_reason":    row.get("exit_reason"),
            "pnl_points":     pnl_points,
            "pnl_value":      (row.get("pnl") if not is_open else None),
            "brokerage":        None,
            "stt":              None,
            "exchange_charges": None,
            "sebi_charges":     None,
            "stamp_duty":       None,
            "gst":              None,
            "total_charges":    (row.get("charges") if not is_open else None),
            "net_pnl":          (npnl if not is_open else None),
            "state":          "OPEN" if is_open else "CLOSED",
            "created_at":     row.get("entry_ts"),
        }
        (out_open if is_open else out_closed).append(trade)
    return out_open, out_closed
# TMA_PAPER END


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


# ==================================================
# SCALP_V5 → legacy paper_trades shape (SINGLE-INSTRUMENT)
# ==================================================

def _load_scalpv5_paper(conn):
    """
    Map scalpv5_trades (paper=1) onto the legacy paper_trades display shape.

    UNLIKE V3/V4, V5 is SINGLE-INSTRUMENT (option BUYING) — it buys the
    signalling contract itself, so there is NO hedge leg. The displayed trade IS
    the bought option, so the columns map DIRECTLY:

      symbol      ← symbol
      side        ← side
      entry_price ← entry_price
      sl_price    ← sl_price          (sl < entry ⇒ frontend infers LONG ✓)
      tp_price    ← tp_price          (V5 has a real TP column; None if disabled)
      qty         ← qty
      pnl_value   ← realized_pnl      (closed only; open rows priced live by UI)
      state       ← OPEN | CLOSED

    Charge-breakdown fields are returned as None — the frontend recomputes
    charges itself from entry/exit/qty (V5 stores gross realized_pnl only,
    matching the V3/V4 convention on this page).
    """
    # Guard: only query if the table exists (V5 may never have opened a trade).
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scalpv5_trades'"
    ).fetchone()
    if not exists:
        return [], []

    cur = conn.execute(
        """
        SELECT
            v5_trade_id,
            strategy_name,
            symbol,
            side,
            qty,
            entry_price,
            sl_price,
            tp_price,
            entry_time,
            exit_time,
            exit_price,
            exit_reason,
            realized_pnl,
            state
        FROM scalpv5_trades
        WHERE paper = 1
        ORDER BY entry_time DESC
        """
    )

    open_v5: List[Dict[str, Any]] = []
    closed_v5: List[Dict[str, Any]] = []

    for r in cur.fetchall():
        row = dict(r)
        entry = row.get("entry_price")
        qty   = row.get("qty")
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
            "paper_trade_id": row.get("v5_trade_id"),
            "strategy_name":  row.get("strategy_name") or "SCALP_V5",
            "trade_mode":     "PAPER",
            "symbol":         row.get("symbol"),
            "token":          None,
            "side":           row.get("side"),

            "entry_time":     row.get("entry_time"),
            "entry_price":    entry,
            "candle_ts":      None,

            "sl_price":       row.get("sl_price"),
            "tp_price":       row.get("tp_price"),   # V5 has a real TP (None if disabled)
            "rr":             None,

            "lots":           None,
            "lot_size":       None,
            "qty":            qty,

            "exit_time":      row.get("exit_time"),
            "exit_price":     exitp,
            "exit_reason":    row.get("exit_reason"),

            "pnl_points":     pnl_points,
            "pnl_value":      (rpnl if not is_open else None),

            # Charge breakdown not stored for V5 — frontend recomputes.
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
            open_v5.append(trade)
        else:
            closed_v5.append(trade)

    return open_v5, closed_v5