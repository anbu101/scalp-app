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
    # Monday = 0 ... Sunday = 6
    return now.weekday() < 5


def is_market_open() -> bool:
    """
    True while the EQUITY DERIVATIVES (NFO) segment is open: 09:15 → 15:40 IST
    on weekdays. This is the correct gate for option/futures LTP-driven logic.

    Upper bound is EXCLUSIVE (was inclusive pre-CAS_2026; 15:40:00 itself is
    past the close).

    Does NOT account for exchange holidays — callers that need holiday
    awareness must resolve the trading calendar separately (see
    pivot_cache._get_previous_trading_day / pst_live_warmup._previous_trading_day).
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
