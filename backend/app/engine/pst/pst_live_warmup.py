# backend/app/engine/pst/pst_live_warmup.py
#
# ── PST LIVE WARMUP ── (Phase 1 — D24, Kite historical API)
#
# Boot-time fetch of the PRIOR SESSION's 1-minute NIFTY spot from Kite
# historical, shaped exactly like the backtest corpus candles, plus the
# prior session's H/L/C (pivot inputs) and its day_start epoch. Feeds
# PSTLiveSignalEngine.seed_warmup(), mirroring the backtest runner's
# cross-day warmup (PST_XDAY_WARMUP) so live indicators are hot from the
# first bar — the live-parity guarantee.
#
# DOCTRINE (mirrors scalp_common.warmup_backfill + pivot_cache):
#   * NEVER raises. Any failure returns None; the caller leaves the engine
#     unseeded and the engine stays fail-closed (no signals, no trades) —
#     the exact live analogue of the backtest skipping a day with no prior
#     session (days_no_prev_session).
#   * Previous TRADING day discovered from Kite day-interval data (weekends
#     and holidays handled by the exchange's own calendar, like
#     pivot_cache._get_previous_trading_day).
#   * NIFTY 50 index token resolved against the instrument master with the
#     well-known 256265 as fallback.

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List, Optional

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:  # standalone tests
    def write_audit_log(msg: str) -> None:
        print(msg)

NIFTY_INDEX_TOKEN_FALLBACK = 256265
IST = 5 * 3600 + 30 * 60


def _day_start_epoch(d: date) -> int:
    """Identical formula to the PST backtest runners — one definition of
    'day start' across backtest and live."""
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)
                ).total_seconds()) - IST


def resolve_nifty_index_token(instruments_df) -> int:
    """NIFTY 50 index token from the instrument master; fallback constant."""
    try:
        rows = instruments_df[
            (instruments_df["segment"] == "INDICES")
            & (instruments_df["tradingsymbol"] == "NIFTY 50")
        ]
        if not rows.empty:
            return int(rows.iloc[0]["instrument_token"])
    except Exception as e:
        write_audit_log(f"[PST_WARMUP] token resolve failed ({e}) — using fallback")
    return NIFTY_INDEX_TOKEN_FALLBACK


def _previous_trading_day(kite, token: int, today: date) -> Optional[date]:
    try:
        rows = kite.historical_data(
            instrument_token=int(token),
            from_date=today - timedelta(days=14),
            to_date=today - timedelta(days=1),
            interval="day",
        )
        days = sorted({r["date"].date() for r in rows})
        days = [d for d in days if d < today]
        return days[-1] if days else None
    except Exception as e:
        write_audit_log(f"[PST_WARMUP] prev-trading-day lookup failed: {e}")
        return None


def fetch_prev_session_spot(kite, *, today: Optional[date] = None,
                            instruments_df=None) -> Optional[dict]:
    """Returns {"spot_1m": [...], "day_start": int, "prev_hlc": {...},
    "prev_date": iso} or None (fail closed). Candle dicts carry ts =
    bar-START epoch — the backtest corpus shape."""
    try:
        today = today or datetime.now().date()
        token = (resolve_nifty_index_token(instruments_df)
                 if instruments_df is not None else NIFTY_INDEX_TOKEN_FALLBACK)
        prev = _previous_trading_day(kite, token, today)
        if prev is None:
            write_audit_log("[PST_WARMUP] no previous trading day found — fail closed")
            return None
        rows = kite.historical_data(
            instrument_token=int(token),
            from_date=datetime(prev.year, prev.month, prev.day, 9, 0),
            to_date=datetime(prev.year, prev.month, prev.day, 15, 45),
            interval="minute",
        )
        if not rows:
            write_audit_log(f"[PST_WARMUP] empty minute data for {prev} — fail closed")
            return None
        spot_1m: List[dict] = [{
            "ts": int(r["date"].timestamp()),
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
        } for r in rows]
        spot_1m.sort(key=lambda c: c["ts"])
        prev_hlc = {"high": max(c["high"] for c in spot_1m),
                    "low": min(c["low"] for c in spot_1m),
                    "close": spot_1m[-1]["close"]}
        out = {"spot_1m": spot_1m, "day_start": _day_start_epoch(prev),
               "prev_hlc": prev_hlc, "prev_date": prev.isoformat()}
        write_audit_log(f"[PST_WARMUP] {prev}: {len(spot_1m)} candles, "
                        f"H/L/C {prev_hlc['high']}/{prev_hlc['low']}/{prev_hlc['close']}")
        return out
    except Exception as e:
        write_audit_log(f"[PST_WARMUP] unexpected failure: {e} — fail closed")
        return None


# ── MIDSESSION_BACKFILL BEGIN ──
def fetch_today_spot(kite, *, token: Optional[int] = None,
                     instruments_df=None):
    """Mid-session restart repair: today's completed 1m spot candles from
    Kite historical (09:15 → now). PST's ENTIRE signal state derives from
    this single instrument, so one call restores a complete replay prefix
    after an outage — options need no history (selection/fills/exits are
    minute-local, gap rules parity-proven). Returns a possibly-empty list;
    [] before ~09:16 or on any failure (caller logs and continues — a
    gapped prefix is degraded, not fatal, and is loudly logged)."""
    try:
        if token is None:
            token = resolve_nifty_index_token(instruments_df)
        today = date.today()
        rows = kite.historical_data(token,
                                    from_date=today, to_date=today,
                                    interval="minute") or []
        out = []
        for r in rows:
            ts = int(r["date"].timestamp())
            out.append({"ts": ts, "open": float(r["open"]),
                        "high": float(r["high"]), "low": float(r["low"]),
                        "close": float(r["close"])})
        out.sort(key=lambda c: c["ts"])
        return out
    except Exception as e:
        write_audit_log(f"[PST_WARMUP] today-backfill failed: {e}")
        return []
# ── MIDSESSION_BACKFILL END ──