# backend/app/db/trades_repo.py

import sqlite3
import time
from typing import Optional

from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log


# ==================================================
# INSERT TRADE
# ==================================================

def insert_trade(
    *,
    trade_id: str,
    strategy_id: str,
    slot: str,
    symbol: str,
    token: int,
    entry_price: float,
    qty: int,
    buy_order_id: str,
    sl_price: float,
    tp_price: float,
    tp_mode: str,
    state: str = "BUY_PLACED",
    sl_order_id: Optional[str] = None,
    trade_direction: str = "LONG",   # "LONG" | "SHORT"
):
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO trades (
                trade_id,
                strategy_id,
                slot,
                symbol,
                token,
                entry_time,
                entry_price,
                qty,
                buy_order_id,
                sl_price,
                sl_order_id,
                tp_price,
                tp_mode,
                state,
                trade_direction
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                strategy_id,
                slot,
                symbol,
                token,
                int(time.time()),
                entry_price,
                qty,
                buy_order_id,
                sl_price,
                sl_order_id,
                tp_price,
                tp_mode,
                state,
                trade_direction,
            ),
        )
        conn.commit()

        write_audit_log(
            f"[DB] TRADE INSERTED trade_id={trade_id} "
            f"strategy={strategy_id} slot={slot} "
            f"state={state} direction={trade_direction}"
        )

    except Exception as e:
        conn.rollback()
        write_audit_log(
            f"[DB][FATAL] INSERT FAILED trade_id={trade_id} ERR={e}"
        )
        raise


# ==================================================
# UPDATE GTT
# ==================================================

def update_gtt(
    *,
    trade_id: str,
    gtt_id: str,
):
    conn = get_conn()
    try:
        conn.execute(
            """
            UPDATE trades
            SET
                sl_order_id = ?,
                state = 'PROTECTED'
            WHERE trade_id = ?
              AND exit_time IS NULL
            """,
            (gtt_id, trade_id),
        )

        conn.commit()

        write_audit_log(
            f"[DB] GTT LINKED trade_id={trade_id} gtt_id={gtt_id}"
        )

    except Exception as e:
        conn.rollback()
        write_audit_log(
            f"[DB][ERROR] GTT UPDATE FAILED trade_id={trade_id} ERR={e}"
        )
        raise


# ==================================================
# CLOSE TRADE
# ==================================================

def close_trade(
    *,
    trade_id: str,
    exit_price: Optional[float],
    exit_order_id: Optional[str],
    exit_reason: str,
):
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            UPDATE trades
            SET
                exit_time = ?,
                exit_price = ?,
                exit_order_id = ?,
                exit_reason = ?,
                state = 'CLOSED'
            WHERE trade_id = ?
              AND exit_time IS NULL
            """,
            (
                int(time.time()),
                exit_price,
                exit_order_id,
                exit_reason,
                trade_id,
            ),
        )

        conn.commit()

        if cur.rowcount == 0:
            write_audit_log(
                f"[DB][SKIP] CLOSE IGNORED trade_id={trade_id}"
            )
        else:
            write_audit_log(
                f"[DB] TRADE CLOSED trade_id={trade_id} reason={exit_reason}"
            )

    except Exception as e:
        conn.rollback()
        write_audit_log(
            f"[DB][ERROR] CLOSE FAILED trade_id={trade_id} ERR={e}"
        )
        raise


# ==================================================
# STRATEGY PnL  (direction-aware)
# ==================================================

def get_total_pnl_for_strategy(strategy_id: str) -> float:
    """
    Calculates realized PnL for CLOSED trades only.
    Direction-aware:
      LONG  → pnl = (exit - entry) * qty
      SHORT → pnl = (entry - exit) * qty
    Fail-safe: return 0.0 if DB read fails.
    """

    conn = get_conn()

    try:
        rows = conn.execute(
            """
            SELECT entry_price, exit_price, qty,
                   COALESCE(trade_direction, 'LONG') AS trade_direction
            FROM trades
            WHERE strategy_id = ?
              AND state = 'CLOSED'
              AND exit_price IS NOT NULL
            """,
            (strategy_id,),
        ).fetchall()

        total = 0.0

        for entry_price, exit_price, qty, direction in rows:
            if direction == "SHORT":
                total += (entry_price - exit_price) * qty
            else:
                total += (exit_price - entry_price) * qty

        return float(total)

    except Exception as e:
        write_audit_log(
            f"[DB][ERROR] PNL_FETCH_FAILED strategy={strategy_id} ERR={e}"
        )
        return 0.0


# ==================================================
# GET TRADE BY ID
# ==================================================

def get_trade_by_id(trade_id: str) -> Optional[dict]:
    """
    Returns a single trade row as a dict, or None if not found.
    Includes trade_direction for downstream P&L calculations.
    """
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            SELECT trade_id, strategy_id, slot, symbol, token,
                   entry_time, entry_price, qty, buy_order_id,
                   sl_price, sl_order_id, tp_price, tp_mode,
                   exit_time, exit_price, exit_order_id, exit_reason,
                   state,
                   COALESCE(trade_direction, 'LONG') AS trade_direction
            FROM trades
            WHERE trade_id = ?
            """,
            (trade_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    except Exception as e:
        write_audit_log(
            f"[DB][ERROR] GET_TRADE_FAILED trade_id={trade_id} ERR={e}"
        )
        return None