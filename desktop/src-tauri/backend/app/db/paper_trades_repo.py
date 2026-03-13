import time
from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log
from app.db.db_lock import DB_LOCK
from app.trading.zerodha_charges_calc import calculate_option_charges

def get_all_open_paper_trades(strategy_name: str):

    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM paper_trades
        WHERE strategy_name = ?
          AND exit_price IS NULL
        """,
        (strategy_name,),
    )

    rows = cur.fetchall()

    columns = [col[0] for col in cur.description]

    return [
        dict(zip(columns, row))
        for row in rows
    ]

# ==================================================
# CHECK OPEN PAPER TRADE BY EXACT SYMBOL (READ ONLY)
# ==================================================

def has_open_paper_trade(
    *,
    strategy_name: str,
    symbol: str,
) -> bool:
    conn = get_conn()

    cur = conn.execute(
        """
        SELECT 1
        FROM paper_trades
        WHERE strategy_name = ?
          AND symbol = ?
          AND state = 'OPEN'
        LIMIT 1
        """,
        (strategy_name, symbol),
    )

    return cur.fetchone() is not None


# ==================================================
# CHECK OPEN PAPER TRADE BY SIDE — CE or PE
#
# FIX: Guards by option side (CE/PE suffix in symbol)
# rather than exact strike symbol. This prevents duplicate
# entries on app restart when the option selector picks a
# different strike because LTP is momentarily missing.
# ==================================================

def has_open_paper_trade_by_side(
    *,
    strategy_name: str,
    side: str,          # "CE" or "PE"
) -> bool:
    """
    Returns True if there is any open paper trade for this strategy
    whose symbol ends with the given side suffix (CE or PE).
    
    This is restart-safe: it does NOT require the exact same strike
    symbol, only the same directional side.
    """
    conn = get_conn()

    cur = conn.execute(
        """
        SELECT 1
        FROM paper_trades
        WHERE strategy_name = ?
          AND symbol LIKE ?
          AND state = 'OPEN'
        LIMIT 1
        """,
        (strategy_name, f"%{side}"),
    )

    return cur.fetchone() is not None




# ==================================================
# GET OPEN PAPER TRADES BY SIDE — for exit routing
#
# Returns list of dicts with paper_trade_id, symbol,
# sl_price, tp_price for all open trades of given side.
# Used by bb_trade_manager._exit() in PAPER mode.
# ==================================================

def get_open_paper_trades_by_side(
    *,
    strategy_name: str,
    side: str,          # "CE" or "PE"
) -> list:
    conn = get_conn()
    cur = conn.execute(
        """
        SELECT paper_trade_id, symbol, sl_price, tp_price, entry_price, qty
        FROM paper_trades
        WHERE strategy_name = ?
          AND symbol LIKE ?
          AND state = 'OPEN'
        """,
        (strategy_name, f"%{side}"),
    )
    columns = [col[0] for col in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]

# ==================================================
# INSERT PAPER TRADE (OPEN) — LOCKED
# ==================================================

def insert_paper_trade(
    *,
    paper_trade_id: str,
    strategy_name: str,
    trade_mode: str,          # PAPER
    symbol: str,
    token: int,
    side: str,                # CE / PE / BOTH
    entry_price: float,
    candle_ts: int,
    sl_price: float,
    tp_price: float,
    rr: float,
    lots: int,
    lot_size: int,
    qty: int,
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
                state,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
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
                int(time.time()),
            ),
        )
        conn.commit()

        write_audit_log(
            f"[DB][PAPER] OPEN trade_id={paper_trade_id} symbol={symbol}"
        )

    except Exception as e:
        write_audit_log(
            f"[DB][PAPER][FATAL] INSERT FAILED trade_id={paper_trade_id} ERR={e}"
        )
        raise


# ==================================================
# GET OPEN PAPER TRADES (READ ONLY — NO LOCK)
# ==================================================

def get_open_paper_trades_for_symbol(*, strategy_name: str, symbol: str):
    conn = get_conn()
    cur = conn.execute(
        """
        SELECT paper_trade_id, sl_price, tp_price
        FROM paper_trades
        WHERE strategy_name = ?
          AND symbol = ?
          AND state = 'OPEN'
        """,
        (strategy_name, symbol),
    )
    return cur.fetchall()

# ==================================================
# GET PAPER TRADE BY ID (READ ONLY)
# ==================================================

def get_paper_trade_by_id(paper_trade_id: str):
    conn = get_conn()
    cur = conn.execute(
        """
        SELECT *
        FROM paper_trades
        WHERE paper_trade_id = ?
        """,
        (paper_trade_id,),
    )

    row = cur.fetchone()

    if not row:
        return None

    columns = [col[0] for col in cur.description]

    return dict(zip(columns, row))

# ==================================================
# CLOSE PAPER TRADE — LOCKED
# ==================================================

def close_paper_trade(
    *,
    paper_trade_id: str,
    exit_price: float,
    exit_reason: str,
):
    conn = get_conn()

    try:
        cur = conn.execute(
            """
            SELECT entry_price, qty
            FROM paper_trades
            WHERE paper_trade_id = ?
                AND state = 'OPEN'
            """,
            (paper_trade_id,),
        )
        row = cur.fetchone()

        if not row:
            write_audit_log(
                f"[DB][PAPER][SKIP] CLOSE IGNORED trade_id={paper_trade_id}"
            )
            return

        entry_price, qty = row

        # -------------------------------------------------
        # Zerodha OPTION charges (AUTHORITATIVE)
        # -------------------------------------------------
        charges = calculate_option_charges(
            entry_price=entry_price,
            exit_price=exit_price,
            qty=qty,
        )

        # -------------------------------------------------
        # Persist
        # -------------------------------------------------
        conn.execute(
            """
            UPDATE paper_trades
            SET
                exit_time = ?,
                exit_price = ?,
                exit_reason = ?,

                pnl_points = ?,
                pnl_value = ?,

                brokerage = ?,
                stt = ?,
                exchange_charges = ?,
                sebi_charges = ?,
                stamp_duty = ?,
                gst = ?,
                total_charges = ?,
                net_pnl = ?,

                state = 'CLOSED'
            WHERE paper_trade_id = ?
                AND state = 'OPEN'
            """,
            (
                int(time.time()),
                exit_price,
                exit_reason,

                charges.gross_pnl / qty if qty else 0,
                charges.gross_pnl,

                charges.brokerage,
                charges.stt,
                charges.exchange_charges,
                charges.sebi_charges,
                charges.stamp_duty,
                charges.gst,
                charges.total_charges,
                charges.net_pnl,

                paper_trade_id,
            ),
        )

        conn.commit()

        write_audit_log(
            f"[DB][PAPER] CLOSED trade_id={paper_trade_id} "
            f"gross={charges.gross_pnl:.2f} "
            f"charges={charges.total_charges:.2f} "
            f"net={charges.net_pnl:.2f}"
        )

    except Exception as e:
        write_audit_log(
            f"[DB][PAPER][ERROR] CLOSE FAILED trade_id={paper_trade_id} ERR={e}"
        )
        raise