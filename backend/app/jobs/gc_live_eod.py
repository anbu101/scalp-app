# backend/app/jobs/gc_live_eod.py
#
# ── GC_V1 ── scheduled EOD backstop (checklist 2.5). The engine's own EOD
# (replay at the exit boundary, ≤15:20 clamped) is primary; the generic
# 15:25 paper sweep is the second layer; this 15:22 cron is the belt to
# both. Safe no-op when flat.

from app.event_bus.audit_logger import write_audit_log


def gc_live_eod_job():
    # ── TRADING_DAY_GATE_20260816 ── NSE-holiday guard (the cron
    # trigger is already mon-fri; this covers weekday exchange holidays).
    from app.utils.market_hours import is_trading_day
    if not is_trading_day():
        write_audit_log("[EOD][GC] non-trading day — no-op")
        return
    try:
        from app.engine.gc.gc_runtime import get_gc_manager
        gm = get_gc_manager()
        if gm is None:
            return
        n = gm.square_off_all("EOD")
        if n:
            write_audit_log(f"[GC][EOD_JOB] squared off {n} leg(s)")
    except Exception as e:
        write_audit_log(f"[GC][EOD_JOB][ERR] {e!r}")