# backend/app/jobs/ha_live_eod.py
"""
HA Live EOD Square-Off Job
===========================
Scheduled at 15:25 IST by api_server.py alongside bb_live_eod_job.

Iterates HA_ENGINE_REGISTRY and calls eod_squareoff() on every
HAOptionsTickEngine instance running in LIVE mode.

Paper-mode engines are silently skipped inside the trade manager.
Safe to run multiple times (close_trade has WHERE exit_time IS NULL guard).

IN-APP ALERTS:
  - Fires ONE "EOD square-off complete" bell alert after all HA engines run.
"""

from app.event_bus.audit_logger import write_audit_log
from app.event_bus.inapp_events import record_alert
from app.core.ha_engine_registry import HA_ENGINE_REGISTRY


def ha_live_eod_job():
    # ── TRADING_DAY_GATE_20260816 ── NSE-holiday guard (the cron
    # trigger is already mon-fri; this covers weekday exchange holidays).
    from app.utils.market_hours import is_trading_day
    if not is_trading_day():
        write_audit_log("[EOD][HA] non-trading day — no-op")
        return

    write_audit_log("[EOD][HA] HA live EOD square-off triggered")

    if not HA_ENGINE_REGISTRY:
        write_audit_log("[EOD][HA] No HA engines registered — nothing to do")
        # CAS_NOTIF: backstop status bell removed — this cron is a 15:25 safety
        # sweep; actual closes are announced by the engines at their configured
        # exit times (<=15:15 post-CAS). Bell here rang at 15:25 even when there
        # was nothing to do, which read as a late close. Audit log retained.
        return

    ran = 0
    for engine in HA_ENGINE_REGISTRY:
        try:
            write_audit_log(
                f"[EOD][HA] Running eod_squareoff on engine id={id(engine)}"
            )
            engine.eod_squareoff()
            ran += 1
        except Exception as e:
            write_audit_log(f"[EOD][HA][ERROR] engine id={id(engine)} ERR={repr(e)}")

    write_audit_log("[EOD][HA] HA live EOD square-off complete")

    # CAS_NOTIF: backstop status bell removed — this cron is a 15:25 safety
    # sweep; actual closes are announced by the engines at their configured
    # exit times (<=15:15 post-CAS). Bell here rang at 15:25 even when there
    # was nothing to do, which read as a late close. Audit log retained.