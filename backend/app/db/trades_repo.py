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
    # ── IC_GROUPING BEGIN ──
    # Additive, both default None so every existing caller (BB/HA/etc.) is
    # byte-for-byte unaffected — a NULL group_id/trade_class is exactly what
    # those rows had before. Populated only by multi-leg strategies (IC_V1)
    # that need their legs tied into one logical trade for Analytics.
    #   group_id    : shared per-condor key; the four IC legs share one value.
    #   trade_class : per-leg role tag (e.g. leg_id L1..L4) for labelling.
    group_id: Optional[str] = None,
    trade_class: Optional[str] = None,
    # ── IC_GROUPING END ──
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
                trade_direction,
                group_id,
                trade_class
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                group_id,
                trade_class,
            ),
        )
        conn.commit()

        write_audit_log(
            f"[DB] TRADE INSERTED trade_id={trade_id} "
            f"strategy={strategy_id} slot={slot} "
            f"state={state} direction={trade_direction}"
            + (f" group_id={group_id} class={trade_class}" if group_id else "")
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
                   COALESCE(trade_direction, 'LONG') AS trade_direction,
                   group_id, trade_class
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
    
# ==================================================
# GET OPEN LIVE TRADES FOR A STRATEGY (READ ONLY)
#
# Used by ha_tick_engine._reload_active_trades to keep monitoring live
# positions. "Open" = exit_time IS NULL (the row hasn't been closed). Returns
# a list of dicts (may be empty). Fail-safe: returns [] on read error so a
# transient DB glitch never throws into the reconcile loop — the in-memory
# _live set remains the authority for live positions regardless.
# ==================================================

def get_open_trades_for_strategy(strategy_id: str) -> list:
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            SELECT trade_id, strategy_id, slot, symbol, token,
                   entry_price, qty, tp_price, sl_price, state,
                   group_id, trade_class, sl_order_id
            FROM trades
            WHERE strategy_id = ?
              AND exit_time IS NULL
            """,
            (strategy_id,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        write_audit_log(
            f"[DB][ERROR] GET_OPEN_TRADES_FAILED strategy={strategy_id} ERR={e}"
        )
        return []