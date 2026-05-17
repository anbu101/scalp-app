# backend/app/jobs/bb_live_eod_v2.py
"""
BB_V2 Live EOD Square-Off Job.
Scheduled at 15:25 IST. Mirrors bb_live_eod.py for BB_V2 engines.
"""

from app.event_bus.audit_logger import write_audit_log
from app.core.engine_registry import BB_ENGINE_REGISTRY
from app.engine.bb_v2.bb_tick_engine_v2 import BBOptionsTickEngineV2


def bb_live_eod_v2_job():
    write_audit_log("[EOD][LIVE] BB_V2 live EOD square-off triggered")

    v2_engines = [
        e for e in BB_ENGINE_REGISTRY
        if isinstance(e, BBOptionsTickEngineV2)
    ]

    if not v2_engines:
        write_audit_log("[EOD][LIVE] No BB_V2 engines registered — nothing to do")
        return

    for engine in v2_engines:
        try:
            write_audit_log(
                f"[EOD][LIVE] BB_V2 eod_squareoff engine id={id(engine)}"
            )
            engine.eod_squareoff()
        except Exception as e:
            write_audit_log(
                f"[EOD][LIVE][ERROR] BB_V2 engine id={id(engine)} ERR={repr(e)}"
            )

    write_audit_log("[EOD][LIVE] BB_V2 EOD square-off complete")