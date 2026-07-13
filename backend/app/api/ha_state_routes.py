# backend/app/api/ha_state_routes.py
"""
HA_V1 state route — LIVE trades for the dashboard panel.
=========================================================
2026-07-13: HAPanel read ONLY /paper_trades, so an open LIVE trade
(shared `trades` table) rendered as an idle slot — no SL→TP distance
bar, no unrealized P&L — and the "today" strip stayed at zero after a
live exit. This route is the LIVE counterpart the panel merges in.

Read-only. NEVER throws (the panel polls it every 3s).

Payload:
  {
    "open":         rows with exit_time IS NULL (via trades_repo;
                    includes sl_order_id = the linked TP GTT id,
                    which drives the panel's GTT badge),
    "closed_today": state='CLOSED' rows with exit_time >= IST midnight,
                    each with pnl_value computed here (gross, no
                    charges — same basis as the paper strip) so the
                    panel treats live rows exactly like paper rows.
  }
"""

import time

from fastapi import APIRouter

from app.db.sqlite import get_conn
from app.db.trades_repo import get_open_trades_for_strategy
from app.event_bus.audit_logger import write_audit_log

router = APIRouter(tags=["ha-v1"])

STRATEGY_ID = "HA_V1"

# IST is a FIXED +05:30 offset (no DST) — project convention. Computed
# from epoch so the result is identical regardless of the machine TZ.
_IST_OFFSET_S = 5 * 3600 + 30 * 60


def _ist_day_start_epoch() -> int:
    """Epoch (UTC seconds) of today's 00:00 IST."""
    now_ist = int(time.time()) + _IST_OFFSET_S
    return (now_ist // 86400) * 86400 - _IST_OFFSET_S


def _closed_today_rows() -> list:
    """Closed LIVE HA_V1 trades since IST midnight, pnl_value per row."""
    day_start = _ist_day_start_epoch()
    conn = get_conn()
    cur = conn.execute(
        """
        SELECT symbol, entry_price, exit_price, qty, exit_reason, exit_time,
               COALESCE(trade_direction, 'LONG') AS trade_direction
        FROM trades
        WHERE strategy_id = ?
          AND state = 'CLOSED'
          AND exit_price IS NOT NULL
          AND exit_time >= ?
        """,
        (STRATEGY_ID, day_start),
    )
    cols = [d[0] for d in cur.description]
    out = []
    for raw in cur.fetchall():
        row = dict(zip(cols, raw))
        try:
            entry = float(row.get("entry_price") or 0)
            exit_ = float(row.get("exit_price") or 0)
            qty   = int(row.get("qty") or 0)
            if row.get("trade_direction") == "SHORT":
                row["pnl_value"] = (entry - exit_) * qty
            else:
                row["pnl_value"] = (exit_ - entry) * qty
        except Exception:
            row["pnl_value"] = None
        out.append(row)
    return out


@router.get("/api/ha/state")
def get_ha_state():
    """Open LIVE trades + today's closed LIVE trades for HA_V1. NEVER throws."""
    try:
        open_rows = get_open_trades_for_strategy(STRATEGY_ID)
    except Exception as e:
        write_audit_log(f"[HA][STATE_ROUTE][OPEN_ERR] {e}")
        open_rows = []

    try:
        closed_today = _closed_today_rows()
    except Exception as e:
        write_audit_log(f"[HA][STATE_ROUTE][CLOSED_ERR] {e}")
        closed_today = []

    return {"open": open_rows, "closed_today": closed_today}