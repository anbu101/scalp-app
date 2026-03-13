# app/jobs/bb_live_eod.py
"""
BB Live EOD Square-Off Job
===========================
Scheduled at 15:25 IST by api_server.py.

Iterates BB_ENGINE_REGISTRY and calls eod_squareoff() on every
BBOptionsTickEngine instance running in LIVE mode.

Each engine delegates to BBTradeManager.eod_squareoff() which:
  1. Cancels the live GTT (prevents double-fill race)
  2. Places a market SELL to close the position
  3. Closes the trade in DB with exit_reason="EOD_SQUARE_OFF"
  4. Clears in-memory state (BBTradeStateManager + signal engine flags)
  5. Fires a Telegram notification

Paper-mode engines are silently skipped inside the trade manager.
Safe to run multiple times (close_trade has WHERE exit_time IS NULL guard).
"""

from app.event_bus.audit_logger import write_audit_log
from app.core.engine_registry import BB_ENGINE_REGISTRY


def bb_live_eod_job():

    write_audit_log("[EOD][LIVE] BB live EOD square-off triggered")

    if not BB_ENGINE_REGISTRY:
        write_audit_log("[EOD][LIVE] No BB engines registered — nothing to do")
        return

    for engine in BB_ENGINE_REGISTRY:
        try:
            write_audit_log(
                f"[EOD][LIVE] Running eod_squareoff on engine id={id(engine)}"
            )
            engine.eod_squareoff()
        except Exception as e:
            write_audit_log(f"[EOD][LIVE][ERROR] engine id={id(engine)} ERR={repr(e)}")

    write_audit_log("[EOD][LIVE] BB live EOD square-off complete")