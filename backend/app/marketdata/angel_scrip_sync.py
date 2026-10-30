# backend/app/marketdata/angel_scrip_sync.py
#
# ── ANGEL_SCRIP_SYNC_20261030 ── keeps Angel One's daily contract list
# ("scrip master") current so LIVE orders on Account 2 can be placed.
#
# Before this, nothing ever called AngelInstrumentResolver.sync(): the cache
# was never written, and every LIVE Angel order failed with "Angel scrip
# master stale (synced=None); LIVE resolution refused" (IC_V2, 2026-10-30).
#
#   boot          background download (never blocks start-up — BOOT_ISOLATION)
#   08:30 / 08:50 daily download, Mon–Fri (08:50 = retry; no-op if 08:30 worked)
#   09:05         pre-open check: list still stale AND a strategy bound to
#                 Account 2 is in LIVE / PAPER+LIVE → error in the bell (+ Telegram)
#   on demand     the resolver refreshes itself when an order finds it stale or
#                 meets a strike newer than the cached copy (cool-down 10 min)
#
# Nothing is downloaded on installs where no strategy is bound to Angel One
# (ACC2 doctrine: users without a secondary account pay zero cost).

from __future__ import annotations

import threading
from typing import List

FENCE = "ANGEL_SCRIP_SYNC_20261030"


def _audit(msg: str) -> None:
    try:
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log(msg)
    except Exception:
        pass


def angel_bound_strategies() -> List[str]:
    """Bindable strategies currently assigned to Account 2 · Angel One."""
    try:
        from app.config.account_bindings import load_bindings, BINDABLE_STRATEGIES
        return sorted(s for s, b in load_bindings().items()
                      if b == "ANGELONE" and s in BINDABLE_STRATEGIES)
    except Exception:
        return []


def angel_live_strategies() -> List[str]:
    """Angel-bound strategies whose mode sends real orders."""
    out = []
    try:
        from app.risk.execution_modes import strategy_mode, wants_live
        for sid in angel_bound_strategies():
            if wants_live(strategy_mode(sid)):
                out.append(sid)
    except Exception:
        pass
    return out


def sync_job(reason: str = "daily") -> bool:
    bound = angel_bound_strategies()
    if not bound:
        _audit(f"[ANGEL_INSTR][SYNC][{reason}] skipped — no strategy on Account 2")
        return False
    try:
        from app.marketdata.angel_instrument_resolver import get_shared_resolver
        ok = get_shared_resolver().sync()
    except Exception as e:
        _audit(f"[ANGEL_INSTR][SYNC][{reason}] failed: {e!r}")
        return False
    _audit(f"[ANGEL_INSTR][SYNC][{reason}] {'ok' if ok else 'FAILED'} "
           f"(Account 2: {', '.join(bound)})")
    return bool(ok)


def preopen_check_job() -> bool:
    """True = an alert was raised."""
    live = angel_live_strategies()
    if not live:
        return False
    try:
        from app.marketdata.angel_instrument_resolver import get_shared_resolver
        r = get_shared_resolver()
        if not r.is_fresh():
            r.sync()                            # one more try before alerting
        if r.is_fresh():
            return False
        synced = r._synced_date
    except Exception as e:
        synced = f"unknown ({e!r})"
    msg = (f"Angel One contract list could not be downloaded (last: {synced}). "
           f"LIVE orders on Account 2 will fail today for: {', '.join(live)}. "
           f"Check the internet connection, or move these strategies to Account 1.")
    _audit(f"[ANGEL_INSTR][PREOPEN][STALE] {msg}")
    try:
        from app.event_bus.inapp_events import record_alert
        record_alert("ANGEL_SCRIP_STALE", msg, severity="error", mode="live")
    except Exception:
        pass
    try:
        from app.api.telegram_api import notify_critical
        notify_critical({"message": msg, "severity": "error"})
    except Exception:
        pass
    return True


def start_boot_sync() -> None:
    """Fire-and-forget: never delays port binding or strategy launches."""
    t = threading.Thread(target=sync_job, args=("boot",), name="angel-scrip-sync",
                         daemon=True)
    t.start()
