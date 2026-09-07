# ── CRYPTO_STFC_20260907 BEGIN ──
# backend/app/backtest/crypto/crypto_stfc_engine.py
#
# STFC — SuperTrend Flip-Confirm one-candle scalper on the BTCUSD perp corpus
# (perp_candles_1m in crypto_backtest.db, Delta Exchange India).
#
# Rule set (identical to the TradingView Pine reference "ST Flip-Confirm
# Scalper"; keep the two in lock-step — this module is the parity core):
#   * SuperTrend(len, mult) on N-minute bars resampled from the 1m corpus.
#   * Flip on candle C1 → C1 ignored. First later candle whose colour matches
#     the new trend (green after up-flip / red after down-flip) ARMS the next
#     candle. The armed candle is traded: enter at its OPEN, exit at its CLOSE.
#   * One trade per flip; a fresh flip while waiting restarts the setup.
#   * Stop-loss is walked MINUTE BY MINUTE inside the trade candle using the
#     1m sub-candles (the Pine version can only see the candle's low/high).
#
# Costs: spread (USD, once per trade) + taker fee % per side on notional.
# P/L is in USD per `size_btc` BTC.
#
# Grid: tf_list × st_len_list × st_mult_list × sl_list. Signals are computed
# once per (tf, len, mult); SL variants re-walk the same trade candles.
# Scoreboard ranking follows the house priority:
#   years positive ↓ → worst month ↓ → max consecutive losing months ↑ →
#   positive months ↓ → max drawdown ↑ → net ↓ (secondary).
#
# Backtest-scoped: no broker, no order paths.

from __future__ import annotations

import datetime as dt
import itertools
import json
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

from app.backtest.crypto.delta_corpus import db, IST

STRATEGY_TAG = "STFC"
SYMBOL = "BTCUSD"
MAX_TRADES_STORED = 20000       # per persisted run (primary cell)


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
@dataclass
class StfcConfig:
    tf_min: int = 3
    st_len: int = 10
    st_mult: float = 2.0
    sl_usd: float = 0.0            # 0 = off
    spread_usd: float = 0.0
    fee_pct_side: float = 0.05     # taker fee % per side (Delta ≈ 0.05)
    size_btc: float = 1.0
    trade_long: bool = True
    trade_short: bool = True
    date_from: str = ""            # YYYY-MM-DD (IST calendar)
    date_to: str = ""
    # grid lists — empty ⇒ single cell using the scalar above
    tf_list: list = field(default_factory=list)
    st_len_list: list = field(default_factory=list)
    st_mult_list: list = field(default_factory=list)
    sl_list: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "StfcConfig":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        cfg = cls(**known)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not (1 <= int(self.tf_min) <= 240):
            raise ValueError("tf_min must be 1..240")
        if not (2 <= int(self.st_len) <= 200):
            raise ValueError("st_len must be 2..200")
        if not (0.1 <= float(self.st_mult) <= 20):
            raise ValueError("st_mult must be 0.1..20")
        if self.sl_usd < 0 or self.spread_usd < 0 or self.fee_pct_side < 0:
            raise ValueError("sl/spread/fee cannot be negative")
        if self.size_btc <= 0:
            raise ValueError("size_btc must be > 0")
        if not (self.trade_long or self.trade_short):
            raise ValueError("at least one side must be enabled")
        for name, lst, lo, hi in (("tf_list", self.tf_list, 1, 240),
                                  ("st_len_list", self.st_len_list, 2, 200),
                                  ("st_mult_list", self.st_mult_list, 0.1, 20),
                                  ("sl_list", self.sl_list, 0, 1e9)):
            for v in lst:
                if not (lo <= float(v) <= hi):
                    raise ValueError(f"{name} value {v} out of range")
        for s in (self.date_from, self.date_to):
            if s:
                dt.date.fromisoformat(s)
        if len(self.cells()) > 400:
            raise ValueError("grid too large (max 400 cells)")

    def cells(self) -> list:
        tfs = [int(x) for x in self.tf_list] or [int(self.tf_min)]
        lens = [int(x) for x in self.st_len_list] or [int(self.st_len)]
        mults = [float(x) for x in self.st_mult_list] or [float(self.st_mult)]
        sls = [float(x) for x in self.sl_list] or [float(self.sl_usd)]
        return [dict(tf_min=a, st_len=b, st_mult=c, sl_usd=d)
                for a, b, c, d in itertools.product(tfs, lens, mults, sls)]


# ----------------------------------------------------------------------
# Corpus access
# ----------------------------------------------------------------------
def _range_epochs(cfg: StfcConfig) -> tuple:
    """[start, end) epoch bounds in IST calendar days; open-ended if blank."""
    start = 0
    end = 2 ** 40
    if cfg.date_from:
        d = dt.date.fromisoformat(cfg.date_from)
        start = int(dt.datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())
    if cfg.date_to:
        d = dt.date.fromisoformat(cfg.date_to) + dt.timedelta(days=1)
        end = int(dt.datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())
    return start, end


def load_1m(start: int, end: int, conn=None) -> list:
    """Ascending list of (ts, o, h, l, c) tuples from the perp corpus."""
    own = conn is None
    conn = conn or db()
    try:
        rows = conn.execute(
            "SELECT ts, open, high, low, close FROM perp_candles_1m "
            "WHERE symbol=? AND ts>=? AND ts<? AND open IS NOT NULL "
            "ORDER BY ts", (SYMBOL, start, end)).fetchall()
        return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]),
                 float(r[4])) for r in rows]
    finally:
        if own:
            conn.close()


def coverage(date_from: str, date_to: str) -> dict:
    """Cheap coverage report for a date window (IST days)."""
    cfg = StfcConfig(date_from=date_from, date_to=date_to)
    start, end = _range_epochs(cfg)
    conn = db()
    try:
        r = conn.execute(
            "SELECT COUNT(*), MIN(ts), MAX(ts) FROM perp_candles_1m "
            "WHERE symbol=? AND ts>=? AND ts<?", (SYMBOL, start, end)).fetchone()
        n, lo, hi = r[0], r[1], r[2]
        # per-day row counts → find thin/missing days
        days = conn.execute(
            "SELECT (ts + 19800) / 86400 AS d, COUNT(*) FROM perp_candles_1m "
            "WHERE symbol=? AND ts>=? AND ts<? GROUP BY d",
            (SYMBOL, start, end)).fetchall()
    finally:
        conn.close()
    have = {int(d): c for d, c in days}
    thin, missing = [], []
    if date_from and date_to:
        d0 = dt.date.fromisoformat(date_from)
        d1 = dt.date.fromisoformat(date_to)
        cur = d0
        while cur <= d1:
            key = int(dt.datetime(cur.year, cur.month, cur.day,
                                  tzinfo=IST).timestamp() + 19800) // 86400
            c = have.get(key, 0)
            if c == 0:
                missing.append(cur.isoformat())
            elif c < 1380:
                thin.append({"date": cur.isoformat(), "rows": c})
            cur += dt.timedelta(days=1)
    return {
        "rows": n,
        "from": dt.datetime.fromtimestamp(lo, IST).isoformat() if lo else None,
        "to": dt.datetime.fromtimestamp(hi, IST).isoformat() if hi else None,
        "days_with_data": len(have),
        "missing_days": missing[:60], "missing_count": len(missing),
        "thin_days": thin[:60], "thin_count": len(thin),
    }


# ----------------------------------------------------------------------
# Resample 1m → N-minute bars (epoch-aligned buckets, same as TradingView)
# ----------------------------------------------------------------------
def resample(m1: list, tf_min: int) -> list:
    """Returns list of dicts: ts (bucket start), o,h,l,c, and `subs` = list
    of the 1m tuples inside the bucket (ascending)."""
    if tf_min == 1:
        return [dict(ts=t, o=o, h=h, l=l, c=c, subs=[(t, o, h, l, c)])
                for (t, o, h, l, c) in m1]
    width = tf_min * 60
    out = []
    cur = None
    for row in m1:
        t, o, h, l, c = row
        b = t - (t % width)
        if cur is None or cur["ts"] != b:
            if cur is not None:
                out.append(cur)
            cur = dict(ts=b, o=o, h=h, l=l, c=c, subs=[row])
        else:
            cur["h"] = max(cur["h"], h)
            cur["l"] = min(cur["l"], l)
            cur["c"] = c
            cur["subs"].append(row)
    if cur is not None:
        out.append(cur)
    return out


# ----------------------------------------------------------------------
# SuperTrend — bit-for-bit port of TradingView's ta.supertrend()
#   ATR = RMA(TR, len) seeded with SMA of the first `len` TRs (ta.rma).
#   direction: -1 = up-trend (price above line), +1 = down-trend.
# ----------------------------------------------------------------------
def supertrend(bars: list, length: int, mult: float) -> list:
    """Returns list of (line, direction) per bar; direction None until ATR
    exists (mirrors Pine na)."""
    n = len(bars)
    out = [None] * n
    tr_sum = 0.0
    atr_prev = None
    prev_close = None
    prev_upper = 0.0
    prev_lower = 0.0
    prev_st = None
    direction = 1
    for i, b in enumerate(bars):
        h, l, c = b["h"], b["l"], b["c"]
        tr = (h - l) if prev_close is None else max(
            h - l, abs(h - prev_close), abs(l - prev_close))
        # RMA with SMA seed
        if i < length - 1:
            tr_sum += tr
            atr = None
        elif i == length - 1:
            tr_sum += tr
            atr = tr_sum / length
        else:
            atr = (atr_prev * (length - 1) + tr) / length
        atr_was_na = atr_prev is None
        if atr is None:
            out[i] = (None, None)
            prev_close = c
            continue
        src = (h + l) / 2.0
        upper = src + mult * atr
        lower = src - mult * atr
        pc = prev_close if prev_close is not None else c
        lower = lower if (lower > prev_lower or pc < prev_lower) else prev_lower
        upper = upper if (upper < prev_upper or pc > prev_upper) else prev_upper
        if atr_was_na:
            direction = 1
        elif prev_st == prev_upper:
            direction = -1 if c > upper else 1
        else:
            direction = 1 if c < lower else -1
        st = lower if direction == -1 else upper
        out[i] = (st, direction)
        prev_upper, prev_lower, prev_st = upper, lower, st
        atr_prev = atr
        prev_close = c
    return out


# ----------------------------------------------------------------------
# Signal engine: flip → confirm → armed candle (index list + direction)
# ----------------------------------------------------------------------
def signals(bars: list, st: list, trade_long: bool, trade_short: bool) -> list:
    """Returns list of (trade_bar_index, dir, flip_bar_index, confirm_bar_index).
    State machine is a 1:1 port of the Pine script."""
    out = []
    pend = 0
    armed = False
    armed_dir = 0
    flip_i = -1
    for i, b in enumerate(bars):
        # 1) execute (the bar armed by the previous bar)
        if armed:
            out.append((i, armed_dir, flip_i, i - 1))
        armed = False
        armed_dir = 0
        d = st[i][1]
        dprev = st[i - 1][1] if i > 0 else None
        if d is None or dprev is None:
            continue
        is_up, was_up = d < 0, dprev < 0
        flip_up = is_up and not was_up
        flip_dn = (not is_up) and was_up
        green = b["c"] > b["o"]
        red = b["c"] < b["o"]
        # 2) flip (C1 ignored) / 3) confirmation → arm next bar
        if flip_up:
            pend = 1 if trade_long else 0
            flip_i = i
        elif flip_dn:
            pend = -1 if trade_short else 0
            flip_i = i
        elif pend == 1 and green:
            armed, armed_dir, pend = True, 1, 0
        elif pend == -1 and red:
            armed, armed_dir, pend = True, -1, 0
    return out


# ----------------------------------------------------------------------
# Trade execution on the armed candle (1m SL walk + costs)
# ----------------------------------------------------------------------
def execute(bar: dict, direction: int, sl_usd: float, spread_usd: float,
            fee_pct_side: float, size_btc: float) -> dict:
    entry = bar["o"]
    exit_p = bar["c"]
    exit_ts = bar["subs"][-1][0] if bar["subs"] else bar["ts"]
    sl_hit = False
    sl_price = None
    if sl_usd > 0:
        sl_price = entry - sl_usd if direction == 1 else entry + sl_usd
        for (t, o, h, l, c) in bar["subs"]:
            if (direction == 1 and l <= sl_price) or \
               (direction == -1 and h >= sl_price):
                sl_hit = True
                exit_p = sl_price
                exit_ts = t
                break
    move = (exit_p - entry) * direction
    gross = move * size_btc
    spread = spread_usd * size_btc
    fees = (entry + exit_p) * size_btc * fee_pct_side / 100.0
    net = gross - spread - fees
    return dict(ts=bar["ts"], exit_ts=exit_ts, dir=direction,
                entry=entry, exit=exit_p, high=bar["h"], low=bar["l"],
                sl_price=sl_price, sl_hit=sl_hit, move=move,
                gross=round(gross, 4), spread=round(spread, 4),
                fees=round(fees, 4), net=round(net, 4))


# ----------------------------------------------------------------------
# Summary / scoreboard
# ----------------------------------------------------------------------
def _ist(ts: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts, IST)


def summarize(trades: list, cell: dict) -> dict:
    n = len(trades)
    net = sum(t["net"] for t in trades)
    gross = sum(t["gross"] for t in trades)
    spread = sum(t["spread"] for t in trades)
    fees = sum(t["fees"] for t in trades)
    wins = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] <= 0]
    longs = [t for t in trades if t["dir"] == 1]
    shorts = [t for t in trades if t["dir"] == -1]
    eq = peak = 0.0
    max_dd = 0.0
    cur_streak = worst_streak = 0
    for t in trades:
        eq += t["net"]
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
        if t["net"] > 0:
            cur_streak = 0
        else:
            cur_streak += 1
            worst_streak = max(worst_streak, cur_streak)
    # yearly / monthly / daily buckets (IST calendar)
    years, months, days = {}, {}, set()
    for t in trades:
        d = _ist(t["ts"])
        years[d.year] = years.get(d.year, 0.0) + t["net"]
        mk = f"{d.year}-{d.month:02d}"
        months[mk] = months.get(mk, 0.0) + t["net"]
        days.add(d.date())
    month_list = [{"month": k, "net": round(v, 2)} for k, v in sorted(months.items())]
    consec_neg = cur = 0
    for m in month_list:
        cur = cur + 1 if m["net"] < 0 else 0
        consec_neg = max(consec_neg, cur)
    year_list = [{"year": y, "net": round(v, 2)} for y, v in sorted(years.items())]
    sum_win = sum(t["net"] for t in wins)
    sum_loss = -sum(t["net"] for t in losses)
    return {
        "strategy": STRATEGY_TAG, "cell": cell,
        "trades": n, "wins": len(wins), "losses": len(losses),
        "win_rate": (len(wins) / n) if n else 0.0,
        "longs": len(longs), "shorts": len(shorts),
        "long_net": round(sum(t["net"] for t in longs), 2),
        "short_net": round(sum(t["net"] for t in shorts), 2),
        "long_win_rate": (sum(1 for t in longs if t["net"] > 0) / len(longs)) if longs else 0.0,
        "short_win_rate": (sum(1 for t in shorts if t["net"] > 0) / len(shorts)) if shorts else 0.0,
        "sl_hits": sum(1 for t in trades if t["sl_hit"]),
        "gross": round(gross, 2), "spread": round(spread, 2),
        "fees": round(fees, 2), "net": round(net, 2),
        "avg_win": round(sum_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum_loss / len(losses), 2) if losses else 0.0,
        "profit_factor": round(sum_win / sum_loss, 3) if sum_loss > 0 else None,
        "best": round(max((t["net"] for t in trades), default=0.0), 2),
        "worst": round(min((t["net"] for t in trades), default=0.0), 2),
        "max_dd": round(max_dd, 2),
        "net_over_dd": round(net / max_dd, 2) if max_dd > 0 else None,
        "max_consec_losses": worst_streak,
        "trading_days": len(days),
        "net_per_day": round(net / len(days), 2) if days else 0.0,
        "years": year_list,
        "years_positive": sum(1 for y in year_list if y["net"] > 0),
        "years_total": len(year_list),
        "months": month_list,
        "months_positive": sum(1 for m in month_list if m["net"] > 0),
        "months_total": len(month_list),
        "worst_month": round(min((m["net"] for m in month_list), default=0.0), 2),
        "max_consec_neg_months": consec_neg,
        # compat keys used by the shared run-history list
        "net_usd": round(net, 2), "traded": n,
    }


def score_key(s: dict) -> tuple:
    """House priority: yearly signs → worst month → consec losing months →
    positive months → drawdown → net. Larger tuple = better."""
    return (s["years_positive"] - s["years_total"],      # 0 when all positive
            s["worst_month"],
            -s["max_consec_neg_months"],
            s["months_positive"],
            -s["max_dd"],
            s["net"])


# ----------------------------------------------------------------------
# Run
# ----------------------------------------------------------------------
def run_stfc_backtest(cfg: StfcConfig,
                      progress_cb: Optional[Callable[[dict], None]] = None,
                      cancel: Optional[threading.Event] = None) -> dict:
    t0 = time.time()
    start, end = _range_epochs(cfg)
    m1 = load_1m(start, end)
    if len(m1) < 1000:
        raise ValueError(
            f"only {len(m1)} 1m candles in range — pull the perp corpus for "
            f"this window first")
    cells = cfg.cells()
    total = len(cells)
    results = []
    primary_trades = None
    done = 0
    # cache per tf and per (tf,len,mult)
    bars_cache = {}
    sig_cache = {}
    for cell in cells:
        if cancel is not None and cancel.is_set():
            break
        tf = cell["tf_min"]
        if tf not in bars_cache:
            bars_cache[tf] = resample(m1, tf)
        bars = bars_cache[tf]
        sk = (tf, cell["st_len"], cell["st_mult"])
        if sk not in sig_cache:
            st = supertrend(bars, cell["st_len"], cell["st_mult"])
            sig_cache[sk] = signals(bars, st, cfg.trade_long, cfg.trade_short)
        trades = [execute(bars[i], d, cell["sl_usd"], cfg.spread_usd,
                          cfg.fee_pct_side, cfg.size_btc)
                  for (i, d, _f, _c) in sig_cache[sk]]
        summ = summarize(trades, cell)
        results.append(summ)
        if primary_trades is None:
            primary_trades = trades
        done += 1
        if progress_cb:
            progress_cb({"phase": "cells", "done": done, "total": total,
                         "cell": cell, "trades": len(trades)})
    # rank
    order = sorted(range(len(results)), key=lambda i: score_key(results[i]),
                   reverse=True)
    for rank, i in enumerate(order, 1):
        results[i]["rank"] = rank
    best = results[order[0]] if results else None
    run_id = str(uuid.uuid4())
    params = asdict(cfg)
    params["strategy"] = STRATEGY_TAG
    params["cells"] = total
    summary = {
        "strategy": STRATEGY_TAG,
        "cells": results,
        "best": best,
        "primary": results[0] if results else None,
        "m1_rows": len(m1),
        "range_from": _ist(m1[0][0]).isoformat(),
        "range_to": _ist(m1[-1][0]).isoformat(),
        "elapsed_s": round(time.time() - t0, 1),
        # compat keys (history list)
        "net_usd": (results[0]["net"] if results else 0.0),
        "traded": (results[0]["trades"] if results else 0),
        "win_rate": (results[0]["win_rate"] if results else 0.0),
    }
    stored = (primary_trades or [])[-MAX_TRADES_STORED:]
    conn = db()
    try:
        conn.execute("INSERT INTO lab_runs VALUES(?,?,?,?,?)",
                     (run_id, int(time.time()), json.dumps(params),
                      json.dumps(summary), json.dumps(stored)))
        conn.commit()
    finally:
        conn.close()
    return {"run_id": run_id, "params": params, "summary": summary,
            "trades": stored}


def list_runs(limit: int = 50, strategy: str = "IC") -> list:
    """Shared lab_runs table; rows without a strategy key are legacy IC."""
    conn = db()
    try:
        rows = conn.execute(
            "SELECT run_id, created_at, params_json, summary_json FROM lab_runs "
            "ORDER BY created_at DESC LIMIT ?", (limit * 4,)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        p = json.loads(r[2])
        tag = p.get("strategy", "IC")
        if tag != strategy:
            continue
        out.append({"run_id": r[0], "created_at": r[1], "params": p,
                    "summary": json.loads(r[3])})
        if len(out) >= limit:
            break
    return out


CSV_COLS = ["ts_ist", "exit_ts_ist", "dir", "entry", "exit", "high", "low",
            "sl_price", "sl_hit", "move", "gross", "spread", "fees", "net"]


def trades_for_csv(trades: list) -> list:
    out = []
    for t in trades:
        row = dict(t)
        row["ts_ist"] = _ist(t["ts"]).strftime("%Y-%m-%d %H:%M")
        row["exit_ts_ist"] = _ist(t["exit_ts"]).strftime("%Y-%m-%d %H:%M")
        row["dir"] = "LONG" if t["dir"] == 1 else "SHORT"
        out.append(row)
    return out
# ── CRYPTO_STFC_20260907 END ──
