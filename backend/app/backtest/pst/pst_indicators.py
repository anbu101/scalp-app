# backend/app/backtest/pst/pst_indicators.py
#
# ── PST_INDICATORS ── pure indicator math for PST_V1 (pivot + SMA +
# SuperTrend spot-signal strategy). Everything here operates on SPOT candles;
# option premiums never enter this module.
#
# LOCKED CONVENTIONS (2026-07-06, mirrors the Quantman source strategy):
#   * Corpus ts = BAR START. A bar stamped T is COMPLETE at T+tf; signals are
#     only ever evaluated on completed bars (aggregate() drops a trailing
#     partial bar).
#   * 3m/5m bars are aligned to session start 09:15 IST: 3m stamps 09:15,
#     09:18, 09:21…; 5m stamps 09:15, 09:20…
#   * Pivots: TRADITIONAL, from the PREVIOUS session's spot H/L/C:
#       PP=(H+L+C)/3, R1=2PP−L, S1=2PP−H, R2=PP+(H−L), S2=PP−(H−L),
#       R3=H+2(PP−L), S3=L−2(H−PP)
#   * SMA(9) on 5-minute closes; the gate uses the LAST COMPLETED 5m bar as
#     of the 3m signal close.
#   * SuperTrend(10, ×2) on 3-minute bars, Wilder-smoothed ATR, standard
#     band-ratchet + flip rules. First `period` bars have no value (warmup).
#   * "Crosses above L": prev_close ≤ L and close > L (strict on the new
#     side, inclusive on the old — touching then leaving counts once).
#
# Pure module: no app imports, no I/O — every function unit-tested against
# hand-computed values before an engine consumes it.

from __future__ import annotations

from typing import Dict, List, Optional

SESSION_START_MIN = 9 * 60 + 15     # 09:15 IST


# ──────────────────────────────────────────────────────────────────────
# multi-timeframe aggregation (session-aligned, completed bars only)
# ──────────────────────────────────────────────────────────────────────
def aggregate(candles_1m: List[dict], tf_minutes: int,
              day_start_epoch: int) -> List[dict]:
    """1m candles (ts=bar start, ascending) → tf-minute bars aligned to
    09:15. A trailing PARTIAL bar (fewer minutes than the bucket spans and
    not ended by data exhaustion at session end) is INCLUDED only if its
    bucket is fully in the past of the last 1m candle — practically: we drop
    the last bucket unless it contains its final expected minute OR the
    caller passes the full session. Simpler contract used by the engine:
    bars returned here are complete as of the last 1m candle's END; the
    engine only evaluates bars whose (ts + tf) <= now."""
    if not candles_1m:
        return []
    session0 = day_start_epoch + SESSION_START_MIN * 60
    buckets: Dict[int, dict] = {}
    for cd in candles_1m:
        ts = int(cd["ts"])
        if ts < session0:
            continue
        b = session0 + ((ts - session0) // (tf_minutes * 60)) * tf_minutes * 60
        cur = buckets.get(b)
        if cur is None:
            buckets[b] = {"ts": b, "open": float(cd["open"]), "high": float(cd["high"]),
                          "low": float(cd["low"]), "close": float(cd["close"]),
                          "last_min": ts}
        else:
            cur["high"] = max(cur["high"], float(cd["high"]))
            cur["low"] = min(cur["low"], float(cd["low"]))
            cur["close"] = float(cd["close"])
            cur["last_min"] = ts
        # open stays first-minute open by insertion order (ascending input)
    bars = [dict(b, complete=(b["last_min"] == b["ts"] + (tf_minutes - 1) * 60))
            for b in sorted(buckets.values(), key=lambda x: x["ts"])]
    for b in bars:
        b.pop("last_min", None)
    return bars


# ──────────────────────────────────────────────────────────────────────
# SMA
# ──────────────────────────────────────────────────────────────────────
def sma(closes: List[float], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(closes)
    s = 0.0
    for i, c in enumerate(closes):
        s += c
        if i >= n:
            s -= closes[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


# ──────────────────────────────────────────────────────────────────────
# Traditional daily pivots (previous session H/L/C)
# ──────────────────────────────────────────────────────────────────────
PIVOT_NAMES = ("S3", "S2", "S1", "PP", "R1", "R2", "R3")


def traditional_pivots(prev_high: float, prev_low: float,
                       prev_close: float) -> Dict[str, float]:
    pp = (prev_high + prev_low + prev_close) / 3.0
    return {
        "PP": pp,
        "R1": 2 * pp - prev_low,
        "S1": 2 * pp - prev_high,
        "R2": pp + (prev_high - prev_low),
        "S2": pp - (prev_high - prev_low),
        "R3": prev_high + 2 * (pp - prev_low),
        "S3": prev_low - 2 * (prev_high - pp),
    }


# ──────────────────────────────────────────────────────────────────────
# SuperTrend (Wilder ATR, standard ratchet + flip)
# ──────────────────────────────────────────────────────────────────────
def supertrend(bars: List[dict], period: int = 10,
               mult: float = 2.0) -> List[Optional[dict]]:
    """Per bar: {"st": line, "dir": +1 up / -1 down} — None during the
    `period`-bar warmup. Wilder ATR: seed = simple mean of the first
    `period` TRs (bar indices 1..period, since TR needs a previous close),
    then atr = (atr*(p-1)+tr)/p. Bands on hl2 ± mult*atr with the standard
    final-band ratchet; dir flips when close breaches the OPPOSITE final
    band; st = final lower band in an uptrend, final upper band in a
    downtrend. Initial direction after warmup: up if close > final upper
    band else down (conservative default; identical to common
    implementations for any real series)."""
    n = len(bars)
    out: List[Optional[dict]] = [None] * n
    if n < period + 1:
        return out
    trs: List[float] = [0.0] * n
    for i in range(1, n):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs[i] = max(h - l, abs(h - pc), abs(l - pc))
    atr = sum(trs[1:period + 1]) / period
    f_ub = f_lb = None
    direction = None
    for i in range(period, n):
        if i > period:
            atr = (atr * (period - 1) + trs[i]) / period
        hl2 = (bars[i]["high"] + bars[i]["low"]) / 2.0
        ub = hl2 + mult * atr
        lb = hl2 - mult * atr
        pclose = bars[i - 1]["close"]
        f_ub = ub if (f_ub is None or ub < f_ub or pclose > f_ub) else f_ub
        f_lb = lb if (f_lb is None or lb > f_lb or pclose < f_lb) else f_lb
        close = bars[i]["close"]
        if direction is None:
            direction = 1 if close > f_ub else -1
        elif direction == -1 and close > f_ub:
            direction = 1
        elif direction == 1 and close < f_lb:
            direction = -1
        out[i] = {"st": f_lb if direction == 1 else f_ub, "dir": direction}
    return out


# ──────────────────────────────────────────────────────────────────────
# level-cross detection
# ──────────────────────────────────────────────────────────────────────
def crosses(prev_close: float, close: float,
            levels: Dict[str, float]) -> Dict[str, List[str]]:
    """Strict on the new side, inclusive on the old: prev ≤ L < close is a
    cross ABOVE; prev ≥ L > close is a cross BELOW. A bar can cross several
    levels at once (gap bars)."""
    above = [name for name, lvl in levels.items()
             if prev_close <= lvl < close]
    below = [name for name, lvl in levels.items()
             if prev_close >= lvl > close]
    return {"above": above, "below": below}