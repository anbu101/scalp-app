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
# ==================================================

def has_open_paper_trade(*, strategy_name: str, symbol: str) -> bool:
    conn = get_conn()
    cur  = conn.execute(
        """
        SELECT 1 FROM paper_trades
        WHERE strategy_name = ? AND symbol = ? AND state = 'OPEN'
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
        WHERE strategy_name = ? AND symbol LIKE ? AND state = 'OPEN'
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
        WHERE strategy_name = ? AND symbol LIKE ? AND state = 'OPEN'
        """,
        (strategy_name, f"%{side}"),
    )
    columns = [col[0] for col in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


# ==================================================
# INSERT PAPER TRADE
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
                state,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
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
        WHERE strategy_name = ? AND symbol = ? AND state = 'OPEN'
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
            write_audit_log(
                f"[DB][PAPER][SKIP] CLOSE IGNORED trade_id={paper_trade_id}"
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