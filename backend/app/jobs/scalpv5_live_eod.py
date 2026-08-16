# backend/app/jobs/scalpv5_live_eod.py
#
# SCALP_V5 — End-of-day square-off job.
# ============================================================================
# Scheduled at 15:25 IST (same cron slot as the other strategies). Squares off
# any OPEN V5 trade (paper + live) via the manager's close_trade(EOD) path, then
# resets the V5-local MTM re-entry latch so the next session starts clean.
#
# Isolated: touches only the V5 selection-loop singleton (for the manager) and
# the V5 risk latch. No other strategy is affected. If V5 never ran today, the
# manager is None and this is a no-op.
# ============================================================================

from app.event_bus.audit_logger import write_audit_log


def scalpv5_live_eod_job():
    # ── TRADING_DAY_GATE_20260816 ── NSE-holiday guard (the cron
    # trigger is already mon-fri; this covers weekday exchange holidays).
    from app.utils.market_hours import is_trading_day
    if not is_trading_day():
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log("[EOD][SCALP_V5] non-trading day — no-op")
        return
    try:
        from app.engine.scalpv5.scalpv5_selection_loop import get_manager
        manager = get_manager()
    except Exception as e:
        write_audit_log(f"[V5][EOD][ERROR] could not resolve manager: {e!r}")
        manager = None

    if manager is None:
        write_audit_log("[V5][EOD] manager not available (V5 not running) — nothing to square off")
    else:
        try:
            closed = manager.eod_squareoff()
            write_audit_log(f"[V5][EOD] square-off complete — closed {closed} trade(s)")
        except Exception as e:
            write_audit_log(f"[V5][EOD][ERROR] square-off failed: {e!r}")

    # Reset the V5-local MTM re-entry latch for the next session (always; safe
    # even if the manager was unavailable).
    try:
        from app.engine.scalpv5.scalpv5_manager import reset_v5_risk_latch
        reset_v5_risk_latch()
    except Exception as e:
        write_audit_log(f"[V5][EOD][ERROR] risk-latch reset failed: {e!r}")