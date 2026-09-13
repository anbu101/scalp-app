# backend/app/backtest/repo/trading_calendar.py
#
# ── BT_DTE_SESSIONS_20260911 ── trading-day calendar from the corpus.
#
# A trading day is "a date with CE/PE candles for the underlying" — the SAME
# definition every backtest runner uses to build sim_days, so anything
# derived here agrees with what the run simulated. Holiday-proof by
# construction (2020–2026 needs no hand-maintained list). Cached per
# underlying, invalidated when the corpus file changes.
from __future__ import annotations

import bisect
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.event_bus.audit_logger import write_audit_log

_IST = timezone(timedelta(hours=5, minutes=30))
_lock = threading.Lock()
_cache: Dict[Tuple[str, str, float], List[str]] = {}   # (db, underlying, mtime) -> sorted ISO dates


def _db_path() -> str:
    from app.backtest.repo.backtest_repo import _db_path as _p
    return str(_p())


def trading_dates(underlying: str = "NIFTY", db_path: Optional[str] = None) -> List[str]:
    """Sorted ISO dates on which the corpus has CE/PE candles for `underlying`.
    Empty list when the corpus is absent/empty (callers treat as unknown)."""
    db = db_path or _db_path()
    try:
        mtime = os.path.getmtime(db)
    except OSError:
        return []
    key = (db, underlying, mtime)
    with _lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit
    try:
        c = sqlite3.connect(db)
        rows = c.execute(
            "SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') AS d"
            " FROM backtest_candles_1m WHERE underlying = ?"
            " AND instrument_type IN ('CE','PE') ORDER BY d", (underlying,)).fetchall()
        c.close()
        out = [r[0] for r in rows if r[0]]
    except Exception as e:
        write_audit_log(f"[BACKTEST][CALENDAR] read failed ({e!r}) — DTE unavailable")
        return []
    with _lock:
        _cache.clear()          # one underlying/mtime live at a time; tiny
        _cache[key] = out
    return out


def sessions_to_expiry(dates_sorted: List[str], entry_iso: str, expiry_iso: str) -> Optional[int]:
    """Count trading dates d with entry < d <= expiry. 0 on expiry day.
    None when the calendar is empty or the expiry precedes the entry."""
    if not dates_sorted or not entry_iso or not expiry_iso:
        return None
    e, x = entry_iso[:10], expiry_iso[:10]
    if x < e:
        return None
    lo = bisect.bisect_right(dates_sorted, e)
    hi = bisect.bisect_right(dates_sorted, x)
    return max(0, hi - lo)


def entry_date_ist(entry_ts) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(entry_ts), _IST).strftime("%Y-%m-%d")
    except Exception:
        return None


def attach_dte(run: dict, db_path: Optional[str] = None) -> dict:
    """Best-effort: add `dte` (sessions to expiry) to every trade dict in
    run["trades"] that carries entry_ts and expiry. Never raises."""
    try:
        trades = run.get("trades") or []
        if not trades:
            return run
        cal = trading_dates(str(run.get("underlying") or "NIFTY"), db_path)
        if not cal:
            return run
        n = 0
        for t in trades:
            e = entry_date_ist(t.get("entry_ts"))
            x = t.get("expiry")
            if not e or not x:
                continue
            d = sessions_to_expiry(cal, e, str(x))
            if d is not None:
                t["dte"] = d
                n += 1
        if n:
            write_audit_log(f"[BACKTEST][CALENDAR] dte attached to {n}/{len(trades)} trades")
    except Exception as e:
        write_audit_log(f"[BACKTEST][CALENDAR] attach failed ({e!r})")
    return run
