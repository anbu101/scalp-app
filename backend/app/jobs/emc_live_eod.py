# backend/app/jobs/emc_live_eod.py
#
# ── EMC_V1 EOD BACKSTOP ── 15:17 sweep UNDER the engine's own 15:15 square-off.
# Fence: EMC_V1_LIVE_20261011. The generic 15:25 paper sweep is a second
# backstop below this one for PAPER rows (no exemption — EMC is intraday).

from __future__ import annotations
from app.event_bus.audit_logger import write_audit_log


def emc_live_eod_job():
    try:
        from app.utils.market_hours import is_trading_day
        from datetime import date
        if not is_trading_day(date.today()):
            return
    except Exception:
        pass
    try:
        from app.engine.emc.emc_runtime import get_emc_manager
        mgr = get_emc_manager()
        if mgr is None or mgr.pos is None:
            return
        n = mgr.eod_squareoff()
        mgr._alert("EOD_BACKSTOP", f"backstop closed {n} position(s) — the engine's 15:15 exit did not fire; "
                   "investigate", "critical")
    except Exception as e:
        write_audit_log(f"[EMC][EOD_JOB][ERR] {e!r}")
