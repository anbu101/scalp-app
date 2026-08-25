# backend/app/jobs/bb_live_eod_v2.py
"""
BB_V2 Live EOD Square-Off Job.
Scheduled at 15:25 IST. Mirrors bb_live_eod.py for BB_V2 engines.

IN-APP ALERTS:
  - Fires ONE "EOD square-off complete" bell alert after all BB_V2 engines run.
"""

from app.event_bus.audit_logger import write_audit_log
from app.event_bus.inapp_events import record_alert
from app.core.engine_registry import BB_ENGINE_REGISTRY
from app.engine.bb_v2.bb_tick_engine_v2 import BBOptionsTickEngineV2


def bb_live_eod_v2_job():
    # ── TRADING_DAY_GATE_20260816 ── NSE-holiday guard (the cron
    # trigger is already mon-fri; this covers weekday exchange holidays).
    from app.utils.market_hours import is_trading_day
    if not is_trading_day():
        write_audit_log("[EOD][BB_V2] non-trading day — no-op")
        return
    write_audit_log("[EOD][LIVE] BB_V2 live EOD square-off triggered")

    v2_engines = [
        e for e in BB_ENGINE_REGISTRY
        if isinstance(e, BBOptionsTickEngineV2)
    ]

    if not v2_engines:
        write_audit_log("[EOD][LIVE] No BB_V2 engines registered — nothing to do")
        # CAS_NOTIF: backstop status bell removed — this cron is a 15:25 safety
        # sweep; actual closes are announced by the engines at their configured
        # exit times (<=15:15 post-CAS). Bell here rang at 15:25 even when there
        # was nothing to do, which read as a late close. Audit log retained.
        return

    ran = 0
    for engine in v2_engines:
        try:
            write_audit_log(
                f"[EOD][LIVE] BB_V2 eod_squareoff engine id={id(engine)}"
            )
            engine.eod_squareoff()
            ran += 1
        except Exception as e:
            write_audit_log(
                f"[EOD][LIVE][ERROR] BB_V2 engine id={id(engine)} ERR={repr(e)}"
            )

    write_audit_log("[EOD][LIVE] BB_V2 EOD square-off complete")

    # CAS_NOTIF: backstop status bell removed — this cron is a 15:25 safety
    # sweep; actual closes are announced by the engines at their configured
    # exit times (<=15:15 post-CAS). Bell here rang at 15:25 even when there
    # was nothing to do, which read as a late close. Audit log retained.