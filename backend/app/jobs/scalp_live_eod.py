# app/jobs/scalp_live_eod.py
"""
SCALP V1 EOD Square-Off Job
============================
Scheduled at 15:25 IST by api_server.py (alongside bb_live_eod_job).

Handles BOTH paper and live SCALP_V1 trades.

LIVE SHORT trades  → place_buy_exit()   (buy back the sold option)
LIVE LONG  trades  → place_market_sell() (sell the bought option)
PAPER trades       → close_paper_trade() with EOD_SQUARE_OFF
                     (direction read from DB — fix B ensures correctness)

Safe to run multiple times:
  - close_trade() has WHERE exit_time IS NULL guard
  - close_paper_trade() has WHERE state='OPEN' guard
"""

import time

from app.event_bus.audit_logger import write_audit_log
from app.trading.trade_state_manager import TradeStateManager
from app.db.trades_repo import close_trade
from app.marketdata.ltp_store import LTPStore


STRATEGY_ID = "SCALP_V1"
EXIT_REASON = "EOD_SQUARE_OFF"


def scalp_live_eod_job():
    write_audit_log("[EOD][SCALP] Square-off triggered")

    # ── PAPER trades ─────────────────────────────────────────────
    _squareoff_paper_trades()

    # ── LIVE trades (via TradeStateManager) ──────────────────────
    _squareoff_live_trades()

    write_audit_log("[EOD][SCALP] Square-off complete")


# ==============================================================
# PAPER
# ==============================================================

def _squareoff_paper_trades():
    """
    Force-close all OPEN paper trades for SCALP_V1 at EOD.
    direction is read from DB — close_paper_trade(trade_direction=None)
    ensures DB value is used (Fix B).
    """
    from app.db.sqlite import get_conn
    from app.db.paper_trades_repo import close_paper_trade

    conn = get_conn()

    rows = conn.execute(
        """
        SELECT paper_trade_id, symbol, entry_price, trade_direction
        FROM paper_trades
        WHERE strategy_name = ?
          AND state = 'OPEN'
        """,
        (STRATEGY_ID,),
    ).fetchall()

    if not rows:
        write_audit_log("[EOD][SCALP][PAPER] No open paper trades")
        return

    write_audit_log(f"[EOD][SCALP][PAPER] Squaring off {len(rows)} trades")

    for row in rows:
        trade_id  = row["paper_trade_id"]
        symbol    = row["symbol"]
        entry     = row["entry_price"]

        # Best-effort exit price from LTPStore
        ltp = LTPStore.get(symbol)
        exit_price = float(ltp) if ltp and ltp > 0 else float(entry)

        if not ltp or ltp <= 0:
            write_audit_log(
                f"[EOD][SCALP][PAPER][WARN] LTP unavailable for {symbol} "
                f"— using entry_price={entry} as fallback (P&L = 0)"
            )

        try:
            # trade_direction=None → DB value used (Fix B)
            close_paper_trade(
                paper_trade_id=trade_id,
                exit_price=exit_price,
                exit_reason=EXIT_REASON,
            )
            write_audit_log(
                f"[EOD][SCALP][PAPER] Closed {trade_id} {symbol} @ {exit_price}"
            )
        except Exception as e:
            write_audit_log(
                f"[EOD][SCALP][PAPER][ERROR] trade_id={trade_id} ERR={repr(e)}"
            )


# ==============================================================
# LIVE
# ==============================================================

def _squareoff_live_trades():
    """
    Close all LIVE open SCALP_V1 slots at EOD.

    SHORT trade → place_buy_exit()    (buy back to close the sold position)
    LONG  trade → place_market_sell() (sell to close the bought position)
    """
    from app.execution.executor_factory import get_executor_for_broker
    from app.config.global_loader import load_global_config

    if not load_global_config().get("trade_on", False):
        write_audit_log("[EOD][SCALP][LIVE] trade_on=FALSE — skipping live squareoff")
        return

    strategy_slots = TradeStateManager._REGISTRY.get(STRATEGY_ID, {})

    if not strategy_slots:
        write_audit_log("[EOD][SCALP][LIVE] No slots registered — nothing to do")
        return

    executor = get_executor_for_broker("ZERODHA")

    for slot_name, mgr in strategy_slots.items():

        trade = mgr.active_trade
        if not trade:
            continue

        symbol    = trade.symbol
        qty       = trade.qty
        trade_id  = trade.trade_id
        direction = getattr(trade, "trade_direction", "LONG")

        write_audit_log(
            f"[EOD][SCALP][LIVE] Closing slot={slot_name} "
            f"symbol={symbol} direction={direction} qty={qty}"
        )

        exit_order_id = None
        exit_price    = None

        try:
            if direction == "SHORT":
                # Sold entry → Buy back to close
                exit_order_id = executor.place_buy_exit(
                    symbol=symbol,
                    qty=qty,
                    reason=EXIT_REASON,
                )
            else:
                # Bought entry → Sell to close (original LONG behavior)
                exit_order_id = executor.place_market_sell(
                    symbol=symbol,
                    qty=qty,
                )

            write_audit_log(
                f"[EOD][SCALP][LIVE] Exit order placed "
                f"order_id={exit_order_id} slot={slot_name}"
            )

        except Exception as e:
            write_audit_log(
                f"[EOD][SCALP][LIVE][ERROR] Exit FAILED "
                f"slot={slot_name} symbol={symbol} ERR={repr(e)}"
            )
            # Do NOT skip DB close — position may have been hit by GTT already.
            # Mark as BROKER_EXIT so reconciliation handles it.
            exit_order_id = None

        # Best-effort fill price
        if exit_order_id:
            time.sleep(1.5)
            try:
                exit_price = executor.get_last_avg_price(exit_order_id)
            except Exception:
                pass

        if not exit_price or exit_price <= 0:
            exit_price = LTPStore.get(symbol)

        # Close in DB
        try:
            close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_order_id=exit_order_id,
                exit_reason=EXIT_REASON,
            )
        except Exception as e:
            write_audit_log(
                f"[EOD][SCALP][LIVE][DB_ERROR] "
                f"trade_id={trade_id} ERR={repr(e)}"
            )

        # Clear in-memory state
        mgr.active_trade     = None
        mgr.in_trade         = False
        mgr.selection_locked = False
        mgr._save_state()

        write_audit_log(
            f"[EOD][SCALP][LIVE] Slot cleared slot={slot_name} "
            f"trade_id={trade_id}"
        )

        # Telegram notification
        try:
            from app.api.telegram_api import notify_manual_exit
            notify_manual_exit({
                "strategy_id": STRATEGY_ID,
                "mode":        "live",
                "symbol":      symbol,
                "entry_price": trade.buy_price,
                "exit_price":  exit_price,
                "exit_reason": EXIT_REASON,
                "pnl":         None,  # DB net_pnl will reflect correct value
            })
        except Exception as e:
            write_audit_log(f"[EOD][SCALP][LIVE][TELEGRAM_ERROR] {e}")