# backend/app/utils/market_hours.py
#
# ── CAS_2026 BEGIN ──────────────────────────────────────────────────────────
# NSE Closing Auction Session (CAS) rollout, effective 2026-08-03.
#
# WHAT CHANGED AT THE EXCHANGE:
#   * Equity DERIVATIVES (NFO — the only segment this app trades) now close at
#     15:40 instead of 15:30. Open is UNCHANGED at 09:15. Pre-open unchanged.
#   * Equity CASH for F&O-underlying stocks stops continuous trading at 15:15;
#     CAS runs 15:15 → 15:35 and fixes the official closing price. Non-F&O
#     cash stocks still close 15:30.
#   * Consequence for us: NIFTY/BANKNIFTY INDEX values between 15:15 and 15:35
#     are INDICATIVE auction values, not continuously-traded prices, and after
#     CAS matching (~15:35) the index is expected to stop updating entirely
#     while NFO options keep trading to 15:40.
#
# DOCTRINE — TWO CLOCKS, NEVER ONE:
#   * Anything driven by OPTION or FUTURES LTPs  → FNO_CLOSE (15:40).
#   * Anything driven by SPOT/INDEX candles, or any "the stream looks dead"
#     staleness heuristic that reads spot → CAS_START (15:15) or CASH_CLOSE.
#     Collapsing these into one bound re-creates the 2026-07-21 TMA false-SL
#     incident at the other end of the day: a dead-spot-stream detector armed
#     during 15:35–15:40 will fire on a spot feed that is legitimately silent.
#
# is_market_open() is FNO-scoped because every strategy in this app trades
# NFO options only. Callers that need the spot/index view must use
# is_spot_continuous_session() instead — do not reuse is_market_open() for it.
# ── CAS_2026 END ────────────────────────────────────────────────────────────

from datetime import datetime, time
import pytz

IST = pytz.timezone("Asia/Kolkata")
UTC = pytz.utc

MARKET_OPEN = time(9, 15)

# ── CAS_2026 BEGIN ── session boundaries (single source of truth)
FNO_CLOSE  = time(15, 40)   # equity derivatives close (from 2026-08-03)
CASH_CLOSE = time(15, 30)   # non-CAS cash close; also the pre-CAS legacy close
CAS_START  = time(15, 15)   # continuous trading stops for F&O-underlying cash
CAS_END    = time(15, 35)   # auction matching complete; official close fixed

# Retained for backward compatibility with any caller that imported it.
# NOTE: this is the CASH close, NOT the derivatives close. New code should
# reference FNO_CLOSE / CASH_CLOSE explicitly rather than this alias.
MARKET_CLOSE = CASH_CLOSE
# ── CAS_2026 END ──


def _now_ist() -> datetime:
    """Always derive IST from UTC — platform-safe regardless of host tz."""
    return datetime.now(UTC).astimezone(IST)


def _is_weekday(now: datetime) -> bool:
    # ── TRADING_DAY_GATE_20260816 ── name retained for diff-minimality;
    # semantics are now "is a TRADING day": Mon-Fri AND not an NSE holiday.
    # All four session functions inherit holiday awareness through this
    # single choke point. Holiday resolution never raises (see loader).
    # Monday = 0 ... Sunday = 6
    return now.weekday() < 5 and not is_nse_holiday(now.date())


def is_market_open() -> bool:
    """
    True while the EQUITY DERIVATIVES (NFO) segment is open: 09:15 → 15:40 IST
    on weekdays. This is the correct gate for option/futures LTP-driven logic.

    Upper bound is EXCLUSIVE (was inclusive pre-CAS_2026; 15:40:00 itself is
    past the close).

    TRADING_DAY_GATE_20260816: NOW holiday-aware — weekday gate routes
    through is_nse_holiday() (bundled 2026 NSE list, optionally extended by
    ~/.scalp-app/state/nse_holidays.json — no rebuild needed for updates).
    """
    now = _now_ist()
    if not _is_weekday(now):
        return False
    return MARKET_OPEN <= now.time() < FNO_CLOSE


# ── CAS_2026 BEGIN ──
def is_spot_continuous_session() -> bool:
    """
    True while the NIFTY/BANKNIFTY INDEX is being computed from CONTINUOUSLY
    TRADED constituents: 09:15 → 15:15 IST on weekdays.

    Use this — never is_market_open() — for any check of the form "the spot
    candle stream has gone quiet, something is wrong". From 15:15 the index is
    an indicative auction value, and after ~15:35 it is expected to stop
    updating while NFO options still trade. A silent spot feed in that window
    is CORRECT behaviour, not a fault.
    """
    now = _now_ist()
    if not _is_weekday(now):
        return False
    return MARKET_OPEN <= now.time() < CAS_START


def is_cash_session() -> bool:
    """True 09:15 → 15:30 IST on weekdays (non-CAS cash-segment view)."""
    now = _now_ist()
    if not _is_weekday(now):
        return False
    return MARKET_OPEN <= now.time() < CASH_CLOSE


def is_in_cas_window() -> bool:
    """
    True 15:15 → 15:35 IST on weekdays — the closing auction is in progress,
    the index is indicative, and cash trading in F&O-underlying stocks is
    halted. NFO options continue to trade normally throughout.
    """
    now = _now_ist()
    if not _is_weekday(now):
        return False
    return CAS_START <= now.time() < CAS_END
# ── CAS_2026 END ──


# ── TRADING_DAY_GATE_20260816 BEGIN ─────────────────────────────────────────
# Born 2026-08-15 (Saturday incident): TSG evaluated entries and IC_V2's
# carry-morning machine attacked a Friday carry on a Saturday — every clock
# gate in the app was calendar-blind. This block is the single source of
# truth for "is today a session at all".
#
# HOLIDAY SOURCE (frozen-bundle safe): the official NSE trading-holiday list
# is a Python constant (PyInstaller needs no data-file spec changes), and an
# OPTIONAL state file ~/.scalp-app/state/nse_holidays.json
#   {"holidays": ["YYYY-MM-DD", ...]}
# EXTENDS it (union) — so a new year's list, or an ad-hoc exchange closure,
# can be dropped in without a rebuild. Missing/malformed file → constant
# only. Everything here is engineered to NEVER raise: for EXIT paths a
# broken calendar must never block a real square-off (fail open), while
# ENTRY paths add their own fail-closed handling on top.
#
# KNOWN LIMITATION: special sessions on non-trading days (Muhurat trading,
# e.g. Sun 2026-11-08) are treated as closed — intentional; no strategy in
# this app should trade a 1-hour symbolic session.
import json as _tdg_json
import threading as _tdg_threading
from datetime import date as _tdg_date, timedelta as _tdg_timedelta
from pathlib import Path as _tdg_Path
from typing import Optional as _tdg_Optional

# Official NSE equity/derivatives trading holidays 2026 (weekday closures).
# Source: NSE "Trading Holidays 2026" circular. Verify on yearly refresh.
NSE_HOLIDAYS_DEFAULT = frozenset({
    "2026-01-15",  # Maharashtra municipal elections (Thu)
    "2026-01-26",  # Republic Day (Mon)
    "2026-03-03",  # Holi (Tue)
    "2026-03-26",  # Shri Ram Navami (Thu)
    "2026-03-31",  # Shri Mahavir Jayanti (Tue)
    "2026-04-03",  # Good Friday (Fri)
    "2026-04-14",  # Dr. Ambedkar Jayanti (Tue)
    "2026-05-01",  # Maharashtra Day (Fri)
    "2026-05-28",  # Bakri Id (Thu)
    "2026-06-26",  # Muharram (Fri)
    "2026-09-14",  # Ganesh Chaturthi (Mon)
    "2026-10-02",  # Mahatma Gandhi Jayanti (Fri)
    "2026-10-20",  # Dussehra (Tue)
    "2026-11-10",  # Diwali - Balipratipada (Tue)
    "2026-11-24",  # Guru Nanak Jayanti (Tue)
    "2026-12-25",  # Christmas (Fri)
})

_TDG_FILE = _tdg_Path.home() / ".scalp-app" / "state" / "nse_holidays.json"
_tdg_cache = {"mtime": None, "dates": None}
_tdg_lock = _tdg_threading.Lock()


def _load_holiday_dates() -> frozenset:
    """Constant list ∪ optional state-file list. mtime-cached (a mid-day
    file edit takes effect on the next check). NEVER raises."""
    base = None
    try:
        base = frozenset(_tdg_date.fromisoformat(s)
                         for s in NSE_HOLIDAYS_DEFAULT)
    except Exception:
        base = frozenset()
    try:
        st = _TDG_FILE.stat()
    except OSError:
        return base
    try:
        with _tdg_lock:
            if _tdg_cache["mtime"] == st.st_mtime and                     _tdg_cache["dates"] is not None:
                return base | _tdg_cache["dates"]
            raw = _tdg_json.loads(_TDG_FILE.read_text(encoding="utf-8"))
            extra = frozenset(
                _tdg_date.fromisoformat(str(s).strip())
                for s in (raw.get("holidays") or []))
            _tdg_cache["mtime"] = st.st_mtime
            _tdg_cache["dates"] = extra
            return base | extra
    except Exception:
        return base


def is_nse_holiday(d) -> bool:
    """True iff d (datetime.date) is an NSE trading holiday. Never raises."""
    try:
        return d in _load_holiday_dates()
    except Exception:
        return False


def is_trading_day(d=None) -> bool:
    """THE calendar gate: weekday AND not an NSE holiday. d defaults to
    today (IST). Never raises. Fail direction is the CALLER's contract:
      * ENTRY paths  → treat any doubt as 'skip today' (fail closed).
      * EXIT  paths  → this function's never-raise + empty-fallback design
        means it can only ever be MORE permissive than reality when the
        holiday data is absent — it can never block a genuine trading-day
        square-off (fail open by construction)."""
    if d is None:
        d = _now_ist().date()
    return d.weekday() < 5 and not is_nse_holiday(d)


def intervening_trading_days(a, b) -> int:
    """Count of trading days strictly between dates a and b (a < b).
    ONE_NIGHT_MAX is satisfied iff this returns 0 — b is the very next
    session after a (Fri→Mon over a weekend = 0; Fri→Tue over a holiday
    Monday = 0; Fri→Wed = 1 → violated). Scan bounded at 30 days; b<=a
    or garbage → 0 (callers keep their own staleness ceilings)."""
    try:
        if b <= a:
            return 0
        n = 0
        d = a + _tdg_timedelta(days=1)
        steps = 0
        while d < b and steps < 30:
            if is_trading_day(d):
                n += 1
            d += _tdg_timedelta(days=1)
            steps += 1
        return n
    except Exception:
        return 0
# ── TRADING_DAY_GATE_20260816 END ───────────────────────────────────────────