"""
ORDER-ID → STRATEGY LOOKUP
app/execution/order_strategy_lookup.py

PURPOSE
-------
Given a broker order_id (e.g. from a REJECTED order postback in
zerodha_order_listener), recover which strategy placed it, so a critical /
rejection alert can respect a Telegram channel's strategy filter.

This is the data half of the "hybrid" order-rejection hook:
  - The listener catches EVERY rejection (it sees all order postbacks).
  - This lookup tags the rejection with its strategy WHEN the order_id has
    already been persisted to a trade table. If it has not (order rejected
    before the row was written, or a manual/non-strategy order), the lookup
    returns None and the caller treats the alert as SYSTEM-WIDE (fires to all
    criticalAlerts-on channels, ignoring the strategy filter).

ISOLATION
---------
Self-contained: queries the trade tables directly by their order-id columns.
Depends on no repo internals — only on get_conn() and the column layout, which
is stable. READS ONLY. Never raises to the caller (returns None on any error),
because a notification helper must never be able to crash the order stream.

TABLE → STRATEGY MAP (confirmed)
--------------------------------
  trades            shared table; strategy is in the strategy_id column.
                    order-id columns: buy_order_id, sl_order_id, exit_order_id.
                    Covers SCALP_V1, SCALP_V2, BB_V1, BB_V2, HA_V1.
  scalp_v3_trades   own table → SCALP_V3.
                    order-id columns: hedge_order_id, exit_order_id.
  scalp_v4_trades   own table → SCALP_V4.
                    order-id columns: hedge_order_id, exit_order_id.
"""

from __future__ import annotations

from typing import Optional

from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log


def find_strategy_by_order_id(order_id: str) -> Optional[str]:
    """
    Return the strategy id that owns `order_id`, or None if it cannot be
    resolved. Never raises.

    Resolution order (first hit wins):
      1. trades            (buy_order_id | sl_order_id | exit_order_id)
      2. scalp_v3_trades   (hedge_order_id | exit_order_id) -> "SCALP_V3"
      3. scalp_v4_trades   (hedge_order_id | exit_order_id) -> "SCALP_V4"
    """
    if not order_id:
        return None

    oid = str(order_id)

    try:
        conn = get_conn()
    except Exception as e:  # pragma: no cover - defensive
        write_audit_log(f"[ORDER_STRAT_LOOKUP] get_conn failed: {e}")
        return None

    # 1) Shared `trades` table — strategy lives in strategy_id column.
    try:
        row = conn.execute(
            """
            SELECT strategy_id
            FROM trades
            WHERE buy_order_id = ?
               OR sl_order_id = ?
               OR exit_order_id = ?
            ORDER BY entry_time DESC
            LIMIT 1
            """,
            (oid, oid, oid),
        ).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception as e:
        write_audit_log(f"[ORDER_STRAT_LOOKUP] trades query failed: {e}")

    # 2) SCALP_V3 own table.
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM scalp_v3_trades
            WHERE hedge_order_id = ?
               OR exit_order_id = ?
            LIMIT 1
            """,
            (oid, oid),
        ).fetchone()
        if row:
            return "SCALP_V3"
    except Exception as e:
        write_audit_log(f"[ORDER_STRAT_LOOKUP] scalp_v3 query failed: {e}")

    # 3) SCALP_V4 own table.
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM scalp_v4_trades
            WHERE hedge_order_id = ?
               OR exit_order_id = ?
            LIMIT 1
            """,
            (oid, oid),
        ).fetchone()
        if row:
            return "SCALP_V4"
    except Exception as e:
        write_audit_log(f"[ORDER_STRAT_LOOKUP] scalp_v4 query failed: {e}")

    return None