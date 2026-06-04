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

    write_audit_log("[EOD][HA] HA live EOD square-off triggered")

    if not HA_ENGINE_REGISTRY:
        write_audit_log("[EOD][HA] No HA engines registered — nothing to do")
        record_alert(
            "EOD_SQUAREOFF",
            "HA_V1: end-of-day square-off ran — no live engines to close.",
            severity="info",
            strategy_id="HA_V1",
        )
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

    record_alert(
        "EOD_SQUAREOFF",
        f"HA_V1: end-of-day square-off complete — {ran} engine(s) processed.",
        severity="info",
        strategy_id="HA_V1",
    )