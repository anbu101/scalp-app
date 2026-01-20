import time
from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log

from app.trading.zerodha_charges_calc import calculate_option_charges


# ==================================================
# CHECK OPEN PAPER TRADE (LOCK)
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
# INSERT PAPER TRADE (OPEN)
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
# CLOSE PAPER TRADE
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
