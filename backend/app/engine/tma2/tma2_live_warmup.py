# backend/app/engine/tma2/tma2_live_warmup.py
#
# ── TMA LIVE WARMUP ── (TMA_XDAY_WARMUP live analogue, Kite historical)
# ============================================================================
# EMA144@5m cannot warm up inside one session (~75 bars << 144-bar SMA seed).
# Boot-time fetch of the prior FIVE trading sessions' 1m NIFTY spot, shaped
# exactly like the backtest corpus candles, plus each session's day_start —
# feeding TMA2LiveSignalEngine the same warmup_sessions list the backtest
# runner builds (rolling WARMUP_DAYS window), so live indicators equal
# backtest indicators BY CONSTRUCTION.
#
# DOCTRINE (pst_live_warmup, verbatim):
#   * NEVER raises. Any failure returns None; the caller stays fail-closed
#     (no signals, no trades) — the live analogue of the backtest's day-1
#     blocked_warmup honesty.
#   * Previous trading days discovered from Kite day-interval data (weekends
#     and holidays handled by the exchange's own calendar).
#   * Mid-session restart backfill reuses PST's fetch_today_spot directly.
# ============================================================================

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:  # standalone tests
    def write_audit_log(msg: str) -> None:
        print(msg)

try:
    from app.engine.pst.pst_live_warmup import (resolve_nifty_index_token,
                                                fetch_today_spot)
except ImportError:  # standalone tests
    from pst_live_warmup import (resolve_nifty_index_token,   # type: ignore
                                 fetch_today_spot)

IST = 5 * 3600 + 30 * 60
WARMUP_DAYS = 5            # backtest runner's WARMUP_DAYS — keep identical
# (V2 needs FIVE: EMA144@5m needs 144 bars for the SMA seed alone, ~2
# sessions, plus convergence — see backtest_tma_v2_runner.WARMUP_DAYS)


def _day_start_epoch(d: date) -> int:
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)
                ).total_seconds()) - IST


def _prev_trading_days(kite, token: int, today: date, n: int) -> List[date]:
    try:
        rows = kite.historical_data(
            instrument_token=int(token),
            from_date=today - timedelta(days=21),
            to_date=today - timedelta(days=1),
            interval="day",
        )
        days = sorted({r["date"].date() for r in rows})
        days = [d for d in days if d < today]
        return days[-n:] if days else []
    except Exception as e:
        write_audit_log(f"[TMA2_WARMUP] prev-trading-days lookup failed: {e}")
        return []


def fetch_warmup_sessions(kite, *, today: Optional[date] = None,
                          instruments_df=None,
                          days: int = WARMUP_DAYS
                          ) -> Optional[List[Tuple[List[dict], int]]]:
    """Returns [(spot_1m, day_start), ...] oldest-first for the prior `days`
    trading sessions — the exact warmup_sessions shape the backtest engine's
    warmup_bars() consumes — or None on any failure (fail closed). Candle ts
    is bar-START epoch (corpus shape). Fewer than `days` sessions available
    (long holiday runs, brand-new instrument) is honest degradation: the
    engine's EMA89 warms later in the day exactly as the backtest's early
    range days do — we return what exists as long as at least one session
    came back."""
    try:
        today = today or datetime.now().date()
        token = (resolve_nifty_index_token(instruments_df)
                 if instruments_df is not None else 256265)
        prevs = _prev_trading_days(kite, token, today, days)
        if not prevs:
            write_audit_log("[TMA2_WARMUP] no previous trading days found — fail closed")
            return None
        out: List[Tuple[List[dict], int]] = []
        for d in prevs:
            rows = kite.historical_data(
                instrument_token=int(token),
                from_date=datetime(d.year, d.month, d.day, 9, 0),
                to_date=datetime(d.year, d.month, d.day, 15, 45),
                interval="minute",
            )
            if not rows:
                write_audit_log(f"[TMA2_WARMUP] empty minute data for {d} — skipping day")
                continue
            spot_1m = [{
                "ts": int(r["date"].timestamp()),
                "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]),
            } for r in rows]
            spot_1m.sort(key=lambda c: c["ts"])
            out.append((spot_1m, _day_start_epoch(d)))
        if not out:
            write_audit_log("[TMA2_WARMUP] all warmup sessions empty — fail closed")
            return None
        write_audit_log(f"[TMA2_WARMUP] {len(out)} prior session(s) fetched: "
                        + ", ".join(f"{d.isoformat()}" for d in prevs[-len(out):]))
        return out
    except Exception as e:
        write_audit_log(f"[TMA2_WARMUP] unexpected failure: {e} — fail closed")
        return None


# re-export for the selection loop (single import site)
__all__ = ["fetch_warmup_sessions", "fetch_today_spot",
           "resolve_nifty_index_token", "WARMUP_DAYS"]