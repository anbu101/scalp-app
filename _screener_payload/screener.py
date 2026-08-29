# backend/app/backtest/util/screener.py
#
# ── STOCK_SCREENER_20260828 ──
# A daily-timeframe equity screener used as an ENTRY GATE for stock backtests.
#
# Reproduces a Chartink-style scan:
#     EMA(close, fast)  >  EMA(close, slow)
#     EMA(close, slow)  crossed above  SMA(close, trend)
#     volume            >  SMA(volume, vol_len)
#     volume            >  min_volume
#
# All four are evaluated on ONE completed daily bar. When they hold on day i the
# screener FIRES, and entries are permitted on the next `cross_window_days`
# TRADING days — i+1 .. i+N. Never day i itself.
#
# NO LOOKAHEAD, AND WHY IT MATTERS HERE
#   Day i's close, high, low and volume are unknown until day i has ended. A
#   gate that used day i's bar to permit entries on day i would be reading the
#   future, and — because these conditions select for days that closed strong —
#   it would inflate results while looking entirely plausible. The firing bar is
#   therefore always STRICTLY BEFORE the first permitted day. Asserted in tests.
#
# WINDOW SEMANTICS (D1, locked 2026-08-28, Anbu)
#   cross_window_days = 1 reproduces the screener exactly: fire on i, trade i+1,
#   done. Larger N holds the gate open longer and lets you falsify how fast the
#   cross decays, without another code change. N is in TRADING days, not
#   calendar days, so a weekend or holiday never silently eats the window.
#
# WARMUP AND FAIL-CLOSED
#   The longest lookback is `trend` (40) plus one bar for the cross comparison.
#   Callers must load bars from well before date_from — see required_warmup().
#   A day with insufficient history is NOT gated open; it is closed. An
#   ungated-by-accident day is a silent false entry, which is the failure mode
#   we can least afford to explain away later.
#
# Volume is the exchange share count summed from the 1m corpus. It will differ
# slightly from Chartink's daily figure, which includes auction and block deals,
# so do not expect a row-for-row reconciliation against a live scan.

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence

DEFAULTS = {
    "screener_enabled": False,      # default OFF — opt in per run
    "screener_ema_fast": 10,
    "screener_ema_slow": 20,
    "screener_sma_trend": 40,
    "screener_vol_sma": 10,
    "screener_min_volume": 2000000,
    "screener_cross_window_days": 1,
}

WARMUP_PAD_DAYS = 90            # calendar days of daily bars to preload


@dataclass
class DailyBar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


def required_warmup(cfg: Dict) -> int:
    """Completed daily bars needed before the first gateable day."""
    return max(int(cfg.get("screener_sma_trend", 40)),
               int(cfg.get("screener_ema_slow", 20)),
               int(cfg.get("screener_vol_sma", 10))) + 1


# ── indicators ─────────────────────────────────────────────────────────────

def sma(values: Sequence[float], length: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if length <= 0:
        return out
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= length:
            run -= values[i - length]
        if i >= length - 1:
            out[i] = run / length
    return out


def ema(values: Sequence[float], length: int) -> List[Optional[float]]:
    """SMA-seeded EMA — the convention Chartink and TradingView both use."""
    out: List[Optional[float]] = [None] * len(values)
    if length <= 0 or len(values) < length:
        return out
    k = 2.0 / (length + 1.0)
    seed = sum(values[:length]) / length
    out[length - 1] = seed
    prev = seed
    for i in range(length, len(values)):
        prev = (values[i] - prev) * k + prev
        out[i] = prev
    return out


# ── daily bars from the 1m corpus ──────────────────────────────────────────

def load_daily_bars(db_path: str, underlying: str, *,
                    date_from: date, date_to: date) -> List[DailyBar]:
    """Aggregate 1m SPOT candles into daily OHLCV, IST day boundaries."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT date(ts,'unixepoch','+330 minutes') d, ts, open, high, "
            "low, close, volume FROM backtest_candles_1m "
            "WHERE underlying=? AND instrument_type='SPOT' "
            "AND date(ts,'unixepoch','+330 minutes') BETWEEN ? AND ? "
            "ORDER BY ts", (underlying.upper(), date_from.isoformat(),
                            date_to.isoformat())).fetchall()
    finally:
        conn.close()

    bars: List[DailyBar] = []
    cur_d = None
    o = h = lo = c = None
    vol = 0.0
    for d, _ts, ro, rh, rl, rc, rv in rows:
        if d != cur_d:
            if cur_d is not None:
                bars.append(DailyBar(_iso(cur_d), o, h, lo, c, vol))
            cur_d, o, h, lo, c, vol = d, ro, rh, rl, rc, 0.0
        h = rh if h is None else max(h, rh if rh is not None else h)
        lo = rl if lo is None else min(lo, rl if rl is not None else lo)
        c = rc if rc is not None else c
        vol += float(rv or 0)
    if cur_d is not None:
        bars.append(DailyBar(_iso(cur_d), o, h, lo, c, vol))
    return bars


def _iso(s: str) -> date:
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


# ── the gate ───────────────────────────────────────────────────────────────

def compute_allowed_days(bars: List[DailyBar], cfg: Dict) -> Dict[date, bool]:
    """{day: entries_allowed}. Only days STRICTLY AFTER a firing bar are True."""
    n = len(bars)
    allowed: Dict[date, bool] = {b.day: False for b in bars}
    if n == 0:
        return allowed

    closes = [b.close for b in bars]
    vols = [b.volume for b in bars]
    ef = ema(closes, int(cfg.get("screener_ema_fast", 10)))
    es = ema(closes, int(cfg.get("screener_ema_slow", 20)))
    st = sma(closes, int(cfg.get("screener_sma_trend", 40)))
    vs = sma(vols, int(cfg.get("screener_vol_sma", 10)))
    min_vol = float(cfg.get("screener_min_volume", 2000000))
    window = max(1, int(cfg.get("screener_cross_window_days", 1)))

    fired: List[int] = []
    for i in range(n):
        if i == 0:
            continue
        if None in (ef[i], es[i], st[i], vs[i], es[i - 1], st[i - 1]):
            continue                        # fail-closed: not enough history
        if not (ef[i] > es[i]):
            continue
        if not (es[i] > st[i] and es[i - 1] <= st[i - 1]):
            continue                        # "crossed above", an EVENT
        if not (vols[i] > vs[i]):
            continue
        if not (vols[i] > min_vol):
            continue
        fired.append(i)

    for i in fired:                          # i+1 .. i+window, TRADING days
        for j in range(i + 1, min(i + 1 + window, n)):
            allowed[bars[j].day] = True
    return allowed


def build_gate(db_path: str, underlying: str, *, date_from: date,
               date_to: date, cfg: Dict) -> Dict:
    """Everything a runner needs: the map, plus diagnosis of its own warmup."""
    bars = load_daily_bars(
        db_path, underlying,
        date_from=date_from - timedelta(days=WARMUP_PAD_DAYS), date_to=date_to)
    need = required_warmup(cfg)
    allowed = compute_allowed_days(bars, cfg)
    warm = [b for b in bars if b.day < date_from]
    in_range = [b for b in bars if date_from <= b.day <= date_to]
    return {
        "allowed": allowed,
        "daily_bars": len(bars),
        "warmup_bars": len(warm),
        "warmup_required": need,
        "warmup_ok": len(warm) >= need,
        "range_days": len(in_range),
        "allowed_days": sum(1 for b in in_range if allowed.get(b.day)),
    }
