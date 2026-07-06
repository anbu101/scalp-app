# backend/app/jobs/ic_v1_live_eod.py
#
# IC_V1 — Live EOD Square-Off Job (scheduled PRIMARY layer)
# ============================================================================
# Registered in api_server's APScheduler at 15:25 IST (wiring day), mirroring
# scalp_v2_live_eod_job. IC's exit_time is CONFIGURABLE (default 15:28), so
# this job waits from its 15:25 fire until exit_time, then squares off.
#
# LAYERED WITH THE ENGINE BACKSTOP: ICEngine._step() also force-squares-off on
# every iteration past exit_time (the continuous post-15:25 backstop, house
# mitigation for APScheduler BackgroundScheduler silent death). Either layer
# alone closes the day; both running is a harmless double-check because
# force_square_off_all() is idempotent on closed legs.
#
# MISFIRE: if the scheduler fires this late (missed 15:25, ran at 15:40),
# the wait loop sees exit_time already past and squares off IMMEDIATELY.
# ============================================================================

import time
from datetime import datetime, timedelta, timezone

from app.event_bus.audit_logger import write_audit_log
from app.event_bus.inapp_events import record_alert

_IST = timezone(timedelta(minutes=330))
_WAIT_POLL_S = 5
_MAX_WAIT_S  = 15 * 60      # sanity cap: never block a scheduler thread longer


def _now():
    return datetime.now(_IST)


def ic_v1_live_eod_job(*, sleep_fn=time.sleep, now_fn=_now):
    """sleep_fn / now_fn injectable for tests."""
    try:
        from app.engine.ic_v1.ic_runtime import get_ic_manager
        from app.config.strategy_loader import load_strategy_config

        gm = get_ic_manager()
        if gm is None:
            write_audit_log("[IC_EOD] manager not initialized — nothing to do")
            return

        try:
            exit_hm = (load_strategy_config("IC_V1") or {}).get("exit_time", "15:28")
        except Exception:
            exit_hm = "15:28"
        h, m = exit_hm.strip().split(":")

        deadline = now_fn().replace(hour=int(h), minute=int(m),
                                    second=0, microsecond=0)
        waited = 0
        while now_fn() < deadline and waited < _MAX_WAIT_S:
            if not gm.has_open_group():
                # nothing open and nothing can open again today (D7 latch) —
                # keep waiting anyway; a group could be mid-MTC bookkeeping
                pass
            sleep_fn(_WAIT_POLL_S)
            waited += _WAIT_POLL_S

        if not gm.has_open_group():
            write_audit_log("[IC_EOD] no open group at exit_time — no-op")
            record_alert("EOD_SQUAREOFF",
                         "IC_V1: EOD ran — no open group to close.",
                         severity="info", strategy_id="IC_V1")
            return

        n = gm.force_square_off_all(reason="EOD")
        write_audit_log(f"[IC_EOD] square-off complete legs={n}")
        record_alert("EOD_SQUAREOFF",
                     f"IC_V1: EOD square-off complete — {n} leg(s) closed.",
                     severity="info", strategy_id="IC_V1")

    except Exception as e:
        write_audit_log(f"[IC_EOD][ERROR] {repr(e)}")
