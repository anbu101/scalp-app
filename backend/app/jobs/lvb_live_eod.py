# backend/app/jobs/lvb_live_eod.py
#
# ── LVB_V1_20261023 ── 15:22 backstop UNDER every condition's own EOD (latest
# allowed 15:20). The generic 15:25 paper sweep is a second backstop below
# this one for PAPER rows (no exemption — LVB is intraday).

from __future__ import annotations
from app.event_bus.audit_logger import write_audit_log


def lvb_live_eod_job():
    try:
        from app.utils.market_hours import is_trading_day
        if not is_trading_day():
            return
    except Exception:
        pass
    try:
        from app.engine.lvb.lvb_runtime import get_lvb_manager
        mgr = get_lvb_manager()
        if mgr is None or not mgr.pos:
            return
        n = mgr.close_all("EOD")
        left = len(mgr.pos)
        mgr._alert("EOD_BACKSTOP", f"backstop closed {n} position(s)"
                   + (f", {left} STILL OPEN — square off at the broker" if left else "")
                   + " — the engine's own EOD exit did not fire; investigate", "critical")
    except Exception as e:
        write_audit_log(f"[LVB][EOD_JOB][ERR] {e!r}")
