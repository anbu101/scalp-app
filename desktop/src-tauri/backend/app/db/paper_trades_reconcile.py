# backend/app/db/paper_trades_reconcile.py

import math
from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log


def reconcile_closed_paper_trades():
    """
    One-time recalculation of charges + net_pnl for CLOSED paper trades
    that have not yet been reconciled (net_pnl IS NULL).

    SAFE to run multiple times (idempotent).
    Zerodha OPTION charges — LOCKED v2.

    Direction-aware:
      LONG  → gross_pnl = (exit - entry) × qty
      SHORT → gross_pnl = (entry - exit) × qty   ← SCALP_V1 short selling
    """

    conn = get_conn()

    rows = conn.execute(
        """
        SELECT
            paper_trade_id,
            entry_price,
            exit_price,
            qty,
            COALESCE(trade_direction, 'LONG') AS trade_direction
        FROM paper_trades
        WHERE state       = 'CLOSED'
          AND exit_price  IS NOT NULL
          AND entry_price IS NOT NULL
          AND qty         IS NOT NULL
          AND net_pnl     IS NULL
        """
    ).fetchall()

    write_audit_log(
        f"[RECONCILE][PAPER] Found {len(rows)} unreconciled CLOSED trades"
    )

    updated = 0

    for r in rows:
        trade_id        = r["paper_trade_id"]
        entry_price     = float(r["entry_price"])
        exit_price      = float(r["exit_price"])
        qty             = int(r["qty"])
        trade_direction = r["trade_direction"] or "LONG"

        # ── Direction-aware gross P&L ─────────────────
        if trade_direction == "SHORT":
            pnl_points = entry_price - exit_price   # positive when premium fell
        else:
            pnl_points = exit_price - entry_price   # positive when premium rose

        pnl_value = pnl_points * qty

        # ── Zerodha OPTION charges (LOCKED v2) ────────
        buy_value  = entry_price * qty
        sell_value = exit_price  * qty
        turnover   = buy_value + sell_value

        brokerage        = 40.0
        stt              = 0.0005   * sell_value
        exchange_charges = 0.00053  * turnover
        sebi_charges     = 0.000001 * turnover
        stamp_duty       = 0.00003  * buy_value
        gst              = 0.18     * (brokerage + exchange_charges)

        total_charges = (
            brokerage
            + stt
            + exchange_charges
            + sebi_charges
            + stamp_duty
            + gst
        )

        net_pnl = pnl_value - total_charges

        conn.execute(
            """
            UPDATE paper_trades
            SET
                pnl_points       = ?,
                pnl_value        = ?,
                brokerage        = ?,
                stt              = ?,
                exchange_charges = ?,
                sebi_charges     = ?,
                stamp_duty       = ?,
                gst              = ?,
                total_charges    = ?,
                net_pnl          = ?
            WHERE paper_trade_id = ?
            """,
            (
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
                trade_id,
            ),
        )

        updated += 1

    conn.commit()

    write_audit_log(
        f"[RECONCILE][PAPER] Updated {updated} CLOSED trades"
    )