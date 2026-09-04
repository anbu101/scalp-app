# backend/app/jobs/orb_live_eod.py
#
# ── ORB_V1 EOD BACKSTOP ── 13:05 scheduled sweep UNDER the engine's own
# 13:00 exit. Fence: ORB_LIVE_20260903. The generic 15:25 paper sweep is a
# second backstop below this one (no exemption — LD5).

from __future__ import annotations
from app.event_bus.audit_logger import write_audit_log


def orb_live_eod_job():
    try:
        from app.utils.market_hours import is_trading_day
        from datetime import date
        if not is_trading_day(date.today()):
            return
    except Exception:
        pass
    try:
        from app.engine.orb.orb_runtime import get_orb_manager
        mgr = get_orb_manager()
        if mgr is None or mgr.pos is None:
            return
        n = mgr.eod_squareoff()
        write_audit_log(f"[ORB][EOD_JOB] backstop closed {n} position(s) — "
                        f"the engine's 13:00 exit did not fire; investigate")
    except Exception as e:
        write_audit_log(f"[ORB][EOD_JOB][ERR] {e!r}")
