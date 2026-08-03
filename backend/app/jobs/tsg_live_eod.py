# backend/app/jobs/tsg_live_eod.py
#
# TSG_V1 — scheduled EOD square-off (LD8). The engine's continuous
# >= exit_time backstop is the primary mechanism (APScheduler-silent-death
# mitigation); this cron is the belt to that suspenders. Safe no-op when
# nothing is open.

from app.event_bus.audit_logger import write_audit_log


def tsg_live_eod_job():
    try:
        from app.engine.tsg.tsg_runtime import get_tsg_manager
        gm = get_tsg_manager()
        if gm is None:
            return
        n = gm.square_off_all("EOD")
        if n:
            write_audit_log(f"[TSG][EOD_JOB] squared off {n} leg(s)")
    except Exception as e:
        write_audit_log(f"[TSG][EOD_JOB][ERR] {e!r}")