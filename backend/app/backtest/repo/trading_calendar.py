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


# ── PARITY_V2_20261030 ── the parity ledger replays a day on a small day pack
# whose own CE/PE days stop at the replayed day, so sessions-to-expiry came out
# 0 (every day looked like expiry day → DTE-multiplier skips). It sets the real
# session calendar here for its own thread only (ContextVar).
import contextvars as _cv
CAL_OVERRIDE: "_cv.ContextVar" = _cv.ContextVar("trading_calendar_override", default=None)


def trading_dates(underlying: str = "NIFTY", db_path: Optional[str] = None) -> List[str]:
    """Sorted ISO dates on which the corpus has CE/PE candles for `underlying`.
    Empty list when the corpus is absent/empty (callers treat as unknown)."""
    _ov = CAL_OVERRIDE.get()
    if _ov is not None:
        return list(_ov)
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
        try:
            out = _probe_days(c, underlying)
        finally:
            c.close()
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


# ── BT_CALENDAR_FAST_20260929 ── the calendar used to be
#   SELECT DISTINCT date(ts) … WHERE underlying=? AND instrument_type IN ('CE','PE')
# which walks EVERY option row of the underlying (tens of millions for NIFTY:
# ~3 s per 6M rows, ~15–20 s on the full corpus). The cache above is keyed on
# the backtest.db mtime, and runs are persisted into that same file — so the
# first results fetch after every completed run paid the full scan. Now: one
# indexed existence probe per calendar day (idx_bt1m_under_type_ts seek,
# LIMIT 1), CE first then PE — the identical set of dates in milliseconds.
_MAX_PROBE_DAYS = 366 * 40          # a stray timestamp decades off → fall back to the scan

_PROBE_SQL = ("SELECT 1 FROM backtest_candles_1m WHERE underlying = ? AND instrument_type = ?"
              " AND ts >= ? AND ts < ? LIMIT 1")


def _scan_days(c: sqlite3.Connection, underlying: str) -> List[str]:
    """The original full DISTINCT scan (reference definition; fallback only)."""
    rows = c.execute(
        "SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') AS d"
        " FROM backtest_candles_1m WHERE underlying = ?"
        " AND instrument_type IN ('CE','PE') ORDER BY d", (underlying,)).fetchall()
    return [r[0] for r in rows if r[0]]


def _probe_days(c: sqlite3.Connection, underlying: str) -> List[str]:
    """IST dates with ≥1 CE or PE candle for `underlying` — same set as
    _scan_days, via per-day index seeks between the first and last print."""
    lo = hi = None
    for typ in ("CE", "PE"):
        # separate MIN and MAX: SQLite only turns a LONE min()/max() into a
        # single index seek — MIN(ts), MAX(ts) together walks the whole range
        a = c.execute("SELECT MIN(ts) FROM backtest_candles_1m"
                      " WHERE underlying = ? AND instrument_type = ?", (underlying, typ)).fetchone()[0]
        b = c.execute("SELECT MAX(ts) FROM backtest_candles_1m"
                      " WHERE underlying = ? AND instrument_type = ?", (underlying, typ)).fetchone()[0]
        if a is not None:
            lo = a if lo is None else min(lo, a)
            hi = b if hi is None else max(hi, b)
    if lo is None:
        return []
    d = datetime.fromtimestamp(int(lo), _IST).date()
    end = datetime.fromtimestamp(int(hi), _IST).date()
    if (end - d).days > _MAX_PROBE_DAYS:
        return _scan_days(c, underlying)
    out: List[str] = []
    while d <= end:
        ds = int(datetime(d.year, d.month, d.day, tzinfo=_IST).timestamp())
        if (c.execute(_PROBE_SQL, (underlying, "CE", ds, ds + 86400)).fetchone()
                or c.execute(_PROBE_SQL, (underlying, "PE", ds, ds + 86400)).fetchone()):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


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
