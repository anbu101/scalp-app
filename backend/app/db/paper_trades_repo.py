# backend/app/db/paper_trades_repo.py

import time
from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log
from app.db.db_lock import DB_LOCK
from app.trading.zerodha_charges_calc import calculate_option_charges


def get_all_open_paper_trades(strategy_name: str):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute(
        """
        SELECT * FROM paper_trades
        WHERE strategy_name = ? AND exit_price IS NULL
        """,
        (strategy_name,),
    )
    rows    = cur.fetchall()
    columns = [col[0] for col in cur.description]
    return [dict(zip(columns, row)) for row in rows]


# ==================================================
# CHECK OPEN PAPER TRADE BY EXACT SYMBOL (READ ONLY)
#
# Consistency note:
#   "Open" is defined as state='OPEN' AND exit_price IS NULL.
#   Requiring BOTH predicates means a row that is half-closed
#   (only one of the two columns set — should never happen via
#   close_paper_trade(), which sets both atomically, but can
#   arise from a manual UPDATE, an aborted migration, or a crash
#   mid-write) will NOT be reported as open. This protects the
#   strategy-wide single-trade gate from being held by a corrupt
#   row. A genuinely open trade still has both, so normal behaviour
#   is unchanged.
# ==================================================

def has_open_paper_trade(*, strategy_name: str, symbol: str) -> bool:
    conn = get_conn()
    cur  = conn.execute(
        """
        SELECT 1 FROM paper_trades
        WHERE strategy_name = ? AND symbol = ?
          AND state = 'OPEN' AND exit_price IS NULL
        LIMIT 1
        """,
        (strategy_name, symbol),
    )
    return cur.fetchone() is not None


# ==================================================
# CHECK OPEN PAPER TRADE BY SIDE (CE / PE)
# ==================================================

def has_open_paper_trade_by_side(*, strategy_name: str, side: str) -> bool:
    conn = get_conn()
    cur  = conn.execute(
        """
        SELECT 1 FROM paper_trades
        WHERE strategy_name = ? AND symbol LIKE ?
          AND state = 'OPEN' AND exit_price IS NULL
        LIMIT 1
        """,
        (strategy_name, f"%{side}"),
    )
    return cur.fetchone() is not None


# ==================================================
# GET OPEN PAPER TRADES BY SIDE
# ==================================================

def get_open_paper_trades_by_side(*, strategy_name: str, side: str) -> list:
    conn = get_conn()
    cur  = conn.execute(
        """
        SELECT paper_trade_id, symbol, sl_price, tp_price,
               entry_price, qty,
               COALESCE(trade_direction, 'LONG') AS trade_direction
        FROM paper_trades
        WHERE strategy_name = ? AND symbol LIKE ?
          AND state = 'OPEN' AND exit_price IS NULL
        """,
        (strategy_name, f"%{side}"),
    )
    columns = [col[0] for col in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


# ==================================================
# ANY OPEN PAPER TRADE FOR A STRATEGY (READ ONLY)
#
# NEW: single source of truth for the router's strategy-wide
#      single-trade gate. Returns (holder_symbol, trade_id) of an
#      open trade if one exists, else None. SignalRouter should call
#      this instead of running its own inline query, so the "open"
#      definition lives in exactly one place.
# ==================================================

def get_any_open_paper_trade(strategy_name: str):
    conn = get_conn()
    cur  = conn.execute(
        """
        SELECT paper_trade_id, symbol FROM paper_trades
        WHERE strategy_name = ?
          AND state = 'OPEN' AND exit_price IS NULL
        LIMIT 1
        """,
        (strategy_name,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {"paper_trade_id": row["paper_trade_id"], "symbol": row["symbol"]}


# ==================================================
# INSERT PAPER TRADE
#
# SCALP_V2 ADDITIVE CHANGE:
#   group_id + trade_class kwargs added (both default None).
#   - SCALP_V1, BB_V1, BB_V2, HA_V1 call WITHOUT these kwargs → NULL stored
#     → those strategies are completely unaffected.
#   - Only SCALP_V2 passes them (one paper_trades row per leg, linked by group_id).
#   - Columns already exist on the table (added by guarded block in runner.py).
# ==================================================

def insert_paper_trade(
    *,
    paper_trade_id: str,
    strategy_name: str,
    trade_mode: str,
    symbol: str,
    token: int,
    side: str,
    entry_price: float,
    candle_ts: int,
    sl_price: float,
    tp_price: float,
    rr: float,
    lots: int,
    lot_size: int,
    qty: int,
    trade_direction: str = "LONG",   # "LONG" | "SHORT"
    group_id: str = None,            # SCALP_V2 only — NULL for all other strategies
    trade_class: str = None,         # SCALP_V2 only — NULL for all other strategies
):
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO paper_trades (
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
                trade_direction,
                group_id,
                trade_class,
                state,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
            """,
            (
                paper_trade_id,
                strategy_name,
                trade_mode,
                symbol,
                token,
                side,
                int(time.time()),
                entry_price,
                candle_ts,
                sl_price,
                tp_price,
                rr,
                lots,
                lot_size,
                qty,
                trade_direction,
                group_id,
                trade_class,
                int(time.time()),
            ),
        )
        conn.commit()
        write_audit_log(
            f"[DB][PAPER] OPEN trade_id={paper_trade_id} "
            f"symbol={symbol} dir={trade_direction}"
        )
    except Exception as e:
        write_audit_log(
            f"[DB][PAPER][FATAL] INSERT FAILED trade_id={paper_trade_id} ERR={e}"
        )
        raise


# ==================================================
# GET OPEN PAPER TRADES (READ ONLY)
# ==================================================

def get_open_paper_trades_for_symbol(*, strategy_name: str, symbol: str):
    conn = get_conn()
    cur  = conn.execute(
        """
        SELECT paper_trade_id, sl_price, tp_price
        FROM paper_trades
        WHERE strategy_name = ? AND symbol = ?
          AND state = 'OPEN' AND exit_price IS NULL
        """,
        (strategy_name, symbol),
    )
    return cur.fetchall()


# ==================================================
# GET PAPER TRADE BY ID (READ ONLY)
# ==================================================

def get_paper_trade_by_id(paper_trade_id: str):
    conn = get_conn()
    cur  = conn.execute(
        "SELECT * FROM paper_trades WHERE paper_trade_id = ?",
        (paper_trade_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    columns = [col[0] for col in cur.description]
    return dict(zip(columns, row))


# ==================================================
# CLOSE PAPER TRADE
# Direction-aware P&L:
#   LONG  → gross_pnl = (exit - entry) × qty
#   SHORT → gross_pnl = (entry - exit) × qty
# Charges are always computed on turnover (same formula).
#
# Atomicity note:
#   exit_price AND state='CLOSED' are written in ONE UPDATE, so a
#   row never ends up with only one of them set via this function.
#   The loud SKIP log below catches the case where the row is no
#   longer OPEN when we try to close it (double-close, or a row
#   already half-modified by something outside this function).
# ==================================================

def close_paper_trade(
    *,
    paper_trade_id: str,
    exit_price: float,
    exit_reason: str,
    trade_direction: str = None,      # None = read from DB; "LONG"/"SHORT" = caller-supplied override
):
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            SELECT entry_price, qty,
                   COALESCE(trade_direction, 'LONG') AS trade_direction
            FROM paper_trades
            WHERE paper_trade_id = ? AND state = 'OPEN'
            """,
            (paper_trade_id,),
        )
        row = cur.fetchone()

        if not row:
            # Loud SKIP: report the row's ACTUAL state so a half-closed
            # or already-closed row is never silently ignored. This is
            # the line that makes a stale/half-open trade visible.
            diag = conn.execute(
                "SELECT state, exit_price FROM paper_trades "
                "WHERE paper_trade_id = ?",
                (paper_trade_id,),
            ).fetchone()
            if diag is None:
                write_audit_log(
                    f"[DB][PAPER][SKIP] CLOSE IGNORED trade_id={paper_trade_id} "
                    f"(row MISSING — not found in table)"
                )
            else:
                write_audit_log(
                    f"[DB][PAPER][SKIP] CLOSE IGNORED trade_id={paper_trade_id} "
                    f"(current state={diag['state']} exit_price={diag['exit_price']})"
                )
            return

        entry_price, qty, db_direction = row

        # None = caller didn't supply direction → use DB value (correct for EOD squareoff)
        # Explicit "LONG"/"SHORT" from caller → use as override (for direct calls that know direction)
        effective_direction = trade_direction if trade_direction is not None else (db_direction or "LONG")

        # ── Direction-aware gross P&L ─────────────────
        if effective_direction == "SHORT":
            gross_pnl = (float(entry_price) - float(exit_price)) * int(qty)
        else:
            gross_pnl = (float(exit_price) - float(entry_price)) * int(qty)

        # ── Zerodha option charges (turnover-based, same formula) ──
        charges = calculate_option_charges(
            entry_price=float(entry_price),
            exit_price=float(exit_price),
            qty=int(qty),
        )

        # Override gross_pnl in charges result with direction-corrected value
        # (calculate_option_charges always uses exit-entry, fine for charges calc
        # but we need to store the correct signed P&L)
        corrected_net_pnl = gross_pnl - charges.total_charges

        conn.execute(
            """
            UPDATE paper_trades
            SET
                exit_time        = ?,
                exit_price       = ?,
                exit_reason      = ?,
                pnl_points       = ?,
                pnl_value        = ?,
                brokerage        = ?,
                stt              = ?,
                exchange_charges = ?,
                sebi_charges     = ?,
                stamp_duty       = ?,
                gst              = ?,
                total_charges    = ?,
                net_pnl          = ?,
                state            = 'CLOSED'
            WHERE paper_trade_id = ? AND state = 'OPEN'
            """,
            (
                int(time.time()),
                exit_price,
                exit_reason,
                gross_pnl / int(qty) if int(qty) else 0,   # pnl_points per unit
                gross_pnl,
                charges.brokerage,
                charges.stt,
                charges.exchange_charges,
                charges.sebi_charges,
                charges.stamp_duty,
                charges.gst,
                charges.total_charges,
                corrected_net_pnl,
                paper_trade_id,
            ),
        )

        conn.commit()

        write_audit_log(
            f"[DB][PAPER] CLOSED trade_id={paper_trade_id} "
            f"dir={effective_direction} "
            f"gross={gross_pnl:.2f} "
            f"charges={charges.total_charges:.2f} "
            f"net={corrected_net_pnl:.2f}"
        )

    except Exception as e:
        write_audit_log(
            f"[DB][PAPER][ERROR] CLOSE FAILED trade_id={paper_trade_id} ERR={e}"
        )
        raise


# ==================================================
# STARTUP RECONCILE — CLEAR STALE OPEN TRADES
#
# NEW: call once per strategy at engine boot, BEFORE the first
#      candle is processed, to guarantee a clean slate. Any row
#      still state='OPEN' from a prior session (e.g. an EOD close
#      that was skipped, or an app crash mid-session) is force-closed
#      so it cannot hold the strategy-wide single-trade gate the next
#      morning.
#
#      Diagnostic-only by default (dry_run=True): it LOGS what it
#      would close without touching anything. Pass dry_run=False to
#      actually force-close. This lets you confirm the 09:30 cause
#      first, then enable the auto-clear once verified.
#
#      exit_reason is tagged STALE_RECONCILE so these are
#      distinguishable from EOD_SQUARE_OFF in the trade list.
# ==================================================

def reconcile_stale_open_trades(strategy_name: str, *, dry_run: bool = True) -> int:
    """
    Find rows for `strategy_name` that are still OPEN at startup and,
    unless dry_run, force-close them at entry_price (P&L 0) tagged
    STALE_RECONCILE. Returns the count found.
    """
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT paper_trade_id, symbol, entry_price, exit_price, state, entry_time
        FROM paper_trades
        WHERE strategy_name = ?
          AND (state = 'OPEN' OR exit_price IS NULL)
        ORDER BY entry_time DESC
        """,
        (strategy_name,),
    ).fetchall()

    if not rows:
        write_audit_log(
            f"[RECONCILE][STALE] {strategy_name} — no stale OPEN trades at startup"
        )
        return 0

    for r in rows:
        write_audit_log(
            f"[RECONCILE][STALE] {strategy_name} STALE_OPEN "
            f"id={r['paper_trade_id']} symbol={r['symbol']} "
            f"state={r['state']} exit_price={r['exit_price']} "
            f"entry_time={r['entry_time']} dry_run={dry_run}"
        )

    if dry_run:
        write_audit_log(
            f"[RECONCILE][STALE] {strategy_name} — dry_run=True, "
            f"{len(rows)} stale row(s) LEFT UNTOUCHED (diagnostic only)"
        )
        return len(rows)

    closed = 0
    for r in rows:
        try:
            close_paper_trade(
                paper_trade_id=r["paper_trade_id"],
                exit_price=float(r["entry_price"]),   # P&L 0 — we have no real exit
                exit_reason="STALE_RECONCILE",
            )
            closed += 1
        except Exception as e:
            write_audit_log(
                f"[RECONCILE][STALE][ERROR] {strategy_name} "
                f"id={r['paper_trade_id']} ERR={e}"
            )

    write_audit_log(
        f"[RECONCILE][STALE] {strategy_name} — force-closed {closed}/{len(rows)} "
        f"stale OPEN trade(s) at startup"
    )
    return closed