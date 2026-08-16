# backend/app/jobs/scalp_v3_live_eod.py
"""
SCALP_V3 EOD Square-Off Job
===========================
Scheduled at 15:25 IST by api_server.py (alongside the other *_live_eod jobs).

SCALP_V3 is the option-BUYING hedge test strategy. One logical trade = a LONG
hedge (bought option) protected by an SL-only GTT. eod_squareoff() in the
manager handles BOTH paper and live:

  PAPER → close the open V3 row at hedge LTP (reason EOD).
  LIVE  → cancel the hedge SL-GTT, verify the position, sell to flat, close row.

Safe to run multiple times:
  - close_v3_trade() has a WHERE state='OPEN' guard (idempotent).
  - eod_squareoff() iterates only OPEN rows (≤1 with the global single-trade gate).

The running engine/manager is reached via the selection loop's module accessor
get_manager() (same pattern SCALP_V2's EOD uses via get_group_manager()).
"""

from app.event_bus.audit_logger import write_audit_log
from app.event_bus.inapp_events import record_alert
from app.risk.strategy_max_loss_guard import reset_strategy_risk_alerts
from app.engine.scalp_v3.scalp_v3_selection_loop import get_manager


STRATEGY_ID = "SCALP_V3"


def scalp_v3_live_eod_job():
    # ── TRADING_DAY_GATE_20260816 ── NSE-holiday guard (the cron
    # trigger is already mon-fri; this covers weekday exchange holidays).
    from app.utils.market_hours import is_trading_day
    if not is_trading_day():
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log("[EOD][SCALP_V3] non-trading day — no-op")
        return
    write_audit_log("[EOD][SCALP_V3] Square-off triggered")

    closed = 0
    try:
        manager = get_manager()
        if manager is None:
            write_audit_log("[EOD][SCALP_V3] No manager (engine not started) — nothing to do")
        else:
            closed = manager.eod_squareoff()
    except Exception as e:
        write_audit_log(f"[EOD][SCALP_V3][ERROR] {repr(e)}")

    # Daily reset of risk-alert keys (idempotent — other EOD jobs also call it).
    try:
        reset_strategy_risk_alerts()
    except Exception as e:
        write_audit_log(f"[EOD][SCALP_V3][RISK_RESET_ERR] {repr(e)}")

    write_audit_log("[EOD][SCALP_V3] Square-off complete")

    record_alert(
        "EOD_SQUAREOFF",
        f"SCALP_V3: end-of-day square-off complete — {closed} position(s) closed.",
        severity="info",
        strategy_id=STRATEGY_ID,
    )