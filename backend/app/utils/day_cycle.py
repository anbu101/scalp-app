# backend/app/utils/day_cycle.py
#
# ── DAY_CYCLE ── shared arm-window helper for day-scoped strategy loops
# (TMA_V1, PST). Born 2026-08-13: a backend that survived overnight
# (battery hibernate) kept TMA/PST parked in the previous evening's
# "market over — idle until next app start" state through an entire
# trading session, while the perpetual loops (SCALP/BB) traded normally.
# The fix: day-scoped loops now run a perpetual cycle
# (arm → one trading day → teardown → wait), and this module owns the
# ONLY clock logic for that cycle.
#
# SAFETY DOCTRINE (carry-position incident): boot reconciliation, EOD,
# and anything position-touching must NEVER run outside a genuine trading
# morning. wait_for_arm_window() therefore does NOTHING while waiting —
# no kite calls, no config reads, no DB access — and only releases on a
# weekday inside [ARM_START, BOOT_CUTOFF) IST on a date STRICTLY AFTER
# the last completed run day. An evening app launch waits silently until
# the next morning; carry positions are untouched until the normal
# morning reconciliation path (with its broker cross-checks) runs.

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime
from typing import Optional

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:  # standalone tests
    def write_audit_log(msg: str) -> None:
        print(msg)

IST = 5 * 3600 + 30 * 60


# ── TRADING_DAY_GATE_20260816 BEGIN ── holiday awareness for the arm
# window. Import is deferred + wrapped: a market_hours import failure
# must degrade to weekday-only arming, never block TMA/PST/GC.
def _is_trading_day_safe(d) -> bool:
    try:
        from app.utils.market_hours import is_trading_day
        return is_trading_day(d)
    except Exception:
        return True
# ── TRADING_DAY_GATE_20260816 END ──

ARM_START_MIN = 8 * 60 + 30     # 08:30 IST — earliest daily arm
BOOT_CUTOFF_MIN = 15 * 60       # 15:00 IST — existing boot-cutoff doctrine
TEARDOWN_MIN = 15 * 60 + 45     # 15:45 IST — after the 15:25/15:28 EOD
                                # crons and the 15:40 NFO close (CAS_NOTE)

_POLL_S = 30


def ist_now_min() -> int:
    """Minute-of-day in IST for the current wall clock."""
    return (int(time.time()) + IST) % 86400 // 60


def ist_today() -> date:
    """Current calendar date in IST."""
    return datetime.utcfromtimestamp(int(time.time()) + IST).date()


async def wait_for_arm_window(tag: str, last_run_day: Optional[date]) -> date:
    """Sleep until the next weekday moment inside [ARM_START, BOOT_CUTOFF)
    IST on a date strictly after last_run_day. Returns the date being
    armed for. One log line on entering the wait, one on release.

    "Strictly after" enforces ONE attempt-window per day: after a day-run
    returns for ANY reason (clean teardown, fail-closed exit, crash), the
    next automatic arm is the NEXT day — same-day recovery remains what
    it is today: a manual app restart. This preserves the fail-closed
    "giving up for today" doctrine verbatim; it just stops "today" from
    meaning "forever".
    """
    waiting_logged = False
    while True:
        d = ist_today()
        m = ist_now_min()
        # ── TRADING_DAY_GATE_20260816 ── holiday-aware arm (GC/TMA/PST).
        # _is_trading_day_safe reads only the cached holiday constant/file
        # — no kite, no config, no DB — honoring the wait-does-NOTHING
        # doctrine. On any error → weekday-only (legacy behaviour).
        armable = (d.weekday() < 5
                   and _is_trading_day_safe(d)
                   and ARM_START_MIN <= m < BOOT_CUTOFF_MIN
                   and (last_run_day is None or d > last_run_day))
        if armable:
            write_audit_log(f"[{tag}][DAY_CYCLE] arming for {d.isoformat()}"
                            f" (daily re-arm — process age is irrelevant"
                            f" by design)")
            return d
        if not waiting_logged:
            if d.weekday() >= 5:
                why = "weekend"
            elif not _is_trading_day_safe(d):
                why = "NSE holiday"    # TRADING_DAY_GATE_20260816
            elif last_run_day is not None and last_run_day >= d:
                why = "already ran today"
            else:
                why = "outside trading window"
            write_audit_log(f"[{tag}][DAY_CYCLE] {why} — idle until the next"
                            f" session arm window"
                            f" ({ARM_START_MIN // 60:02d}:"
                            f"{ARM_START_MIN % 60:02d} IST); no boot/recon/"
                            f"EOD work runs while waiting")
            waiting_logged = True
        await asyncio.sleep(_POLL_S)


async def wait_for_teardown() -> None:
    """Day-run keep-alive: returns once the IST clock passes TEARDOWN_MIN
    (or sits before ARM_START — midnight crossed, defensive)."""
    while True:
        m = ist_now_min()
        if m >= TEARDOWN_MIN or m < ARM_START_MIN:
            return
        await asyncio.sleep(_POLL_S)