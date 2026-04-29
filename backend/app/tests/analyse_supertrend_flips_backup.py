#!/usr/bin/env python3
"""
analyse_supertrend_flips.py — BB Strategy Exit Analysis (v5)
=============================================================

Key fix vs v4:
    BSM option pricing now uses the CORRECT monthly expiry for each trade,
    inferred from the trade's entry date — not from the DB symbol name.

    A trade entered on 2026-02-01 was on the FEBRUARY contract (expiry
    2026-02-26), not the April one. v4 was mispricing every Jan/Feb/Mar
    trade by using a far-off April expiry, which dramatically
    underestimated theta decay and overstated option P&L.

    The DB symbol stays as-is (BANKNIFTY26APRFUT for all rows since that's
    what the engine stored). The expiry is now computed from the trade date
    using last_thursday_of_month(), making the BSM numbers correct regardless
    of DB label.

All other logic unchanged from v4.
"""

import sqlite3
import argparse
import csv
import math
from collections import deque
from datetime import datetime, date, timedelta
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / ".scalp-app" / "data" / "app.db"

BB_PERIOD = 20
BB_STD    = 2

RSI_LENGTH = 14
RSI_SMOOTH = 3

ST_LENGTH     = 10
ST_MULTIPLIER = 2.0

RSI_LONG_EXHAUST  = 60
RSI_SHORT_EXHAUST = 40
SWING_LOOKBACK    = 2
WEAK_PROFIT_RATIO = 0.5
WEAK_PROFIT_MIN   = 10

MOMENTUM_MIN_BARS = 5

EOD_HOUR   = 15
EOD_MINUTE = 25

INDICATOR_WARMUP_BARS = 50

MAX_ENTRY_PREMIUM = 600.0
RISK_FREE_RATE    = 0.065
FALLBACK_IV       = 0.25

# ─────────────────────────────────────────────────────────────────────────────
# CORRECT EXPIRY FROM TRADE DATE  (key fix in v5)
# ─────────────────────────────────────────────────────────────────────────────

def last_thursday(year: int, month: int) -> date:
    """Last Thursday of the given month — BANKNIFTY monthly expiry."""
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    while last.weekday() != 3:   # 3 = Thursday
        last -= timedelta(days=1)
    return last


def expiry_for_trade_date(trade_date: date) -> date:
    """
    Given a trade entry date, returns the expiry of the BANKNIFTY monthly
    futures contract that was active on that date.

    Rule: the active contract is the one whose expiry is the earliest
    last-Thursday-of-month that is >= trade_date.

    Examples:
        2026-02-01  ->  2026-02-26  (Feb contract)
        2026-02-27  ->  2026-03-26  (Mar contract, Feb expired the day before)
        2026-04-10  ->  2026-04-28  (Apr contract)
    """
    year, month = trade_date.year, trade_date.month

    # Check this month's expiry first
    exp = last_thursday(year, month)
    if exp >= trade_date:
        return exp

    # Trade is after this month's expiry — use next month's
    if month == 12:
        return last_thursday(year + 1, 1)
    return last_thursday(year, month + 1)


# ─────────────────────────────────────────────────────────────────────────────
# DB LOAD
# ─────────────────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser()
parser.add_argument("--symbol", default=None)
parser.add_argument("--tf",     default="3m")
args = parser.parse_args()

conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row

if args.symbol:
    rows = conn.execute(
        "SELECT symbol, ts, open, high, low, close "
        "FROM futures_candles WHERE symbol=? AND timeframe=? ORDER BY ts ASC",
        (args.symbol, args.tf),
    ).fetchall()
else:
    rows = conn.execute(
        "SELECT symbol, ts, open, high, low, close "
        "FROM futures_candles WHERE timeframe=? ORDER BY ts ASC",
        (args.tf,),
    ).fetchall()

raw_candles   = [dict(r) for r in rows]
symbols_found = sorted({c["symbol"] for c in raw_candles})

if not raw_candles:
    print("No candles found. Check --symbol / --tf or DB path.")
    raise SystemExit(1)

print(f"Loaded    : {len(raw_candles)} raw OHLC candles")
print(f"Symbols   : {', '.join(symbols_found)}")
print(f"Date range: {datetime.fromtimestamp(raw_candles[0]['ts']):%Y-%m-%d} "
      f"-> {datetime.fromtimestamp(raw_candles[-1]['ts']):%Y-%m-%d}")
print()

# Verify expiry inference — show mapping for sanity check
sample_dates = sorted({
    date.fromtimestamp(c["ts"])
    for c in raw_candles[::200]   # sample every 200th candle
})
print("Expiry mapping (sample):")
seen_expiries = set()
for d in sample_dates:
    exp = expiry_for_trade_date(d)
    key = (d.year, d.month)
    if key not in seen_expiries:
        seen_expiries.add(key)
        print(f"  Trade {d}  ->  expiry {exp}")
print()

# ─────────────────────────────────────────────────────────────────────────────
# BLACK-SCHOLES OPTION PRICING
# ─────────────────────────────────────────────────────────────────────────────

def _erf(x: float) -> float:
    a = [0, 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429]
    p = 0.3275911
    s = 1 if x >= 0 else -1
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a[5]*t + a[4])*t) + a[3])*t + a[2])*t + a[1])*t * math.exp(-x*x)
    return s * y

def ncdf(x): return 0.5 * (1.0 + _erf(x / math.sqrt(2)))
def npdf(x): return math.exp(-0.5*x*x) / math.sqrt(2*math.pi)

def bsm(spot, strike, t, iv, r, kind):
    if t <= 0 or iv <= 0:
        return max(spot - strike, 0) if kind == "CE" else max(strike - spot, 0)
    d1 = (math.log(spot/strike) + (r + 0.5*iv*iv)*t) / (iv*math.sqrt(t))
    d2 = d1 - iv*math.sqrt(t)
    if kind == "CE":
        return spot*ncdf(d1) - strike*math.exp(-r*t)*ncdf(d2)
    return strike*math.exp(-r*t)*ncdf(-d2) - spot*ncdf(-d1)

def solve_iv(price, spot, strike, t, r, kind, tol=0.01, max_iter=100):
    if t <= 0 or price <= 0:
        return FALLBACK_IV
    iv = 0.3
    for _ in range(max_iter):
        p    = bsm(spot, strike, t, iv, r, kind)
        diff = p - price
        if abs(diff) < tol:
            return iv
        d1   = (math.log(spot/strike) + (r + 0.5*iv*iv)*t) / (iv*math.sqrt(t))
        vega = spot * npdf(d1) * math.sqrt(t)
        if abs(vega) < 1e-8:
            break
        iv = max(0.01, min(iv - diff/vega, 10.0))
    return iv if 0.01 <= iv <= 10.0 else FALLBACK_IV

def pick_strike(spot, kind, t, iv, target):
    atm  = round(spot / 100) * 100
    for i in range(60):
        k = (atm + i*100) if kind == "CE" else (atm - i*100)
        if bsm(spot, k, t, iv, RISK_FREE_RATE, kind) <= target:
            return k
    return atm


class OptionPricer:
    """
    Prices one option leg. Constructed at trade entry with the CORRECT
    expiry for the trade's calendar month.
    """
    def __init__(self, entry_fut: float, kind: str,
                 expiry: date, entry_close_ts: int):
        self.kind   = kind
        self.expiry = expiry

        entry_dt  = datetime.fromtimestamp(entry_close_ts)
        expiry_dt = datetime(expiry.year, expiry.month, expiry.day, 15, 30)
        secs      = max((expiry_dt - entry_dt).total_seconds(), 1)
        self.entry_t = secs / (365 * 24 * 3600)

        target       = MAX_ENTRY_PREMIUM * 0.95
        self.strike  = pick_strike(entry_fut, kind, self.entry_t, FALLBACK_IV, target)
        self.entry_px = bsm(entry_fut, self.strike, self.entry_t,
                            FALLBACK_IV, RISK_FREE_RATE, kind)
        self.iv       = solve_iv(self.entry_px, entry_fut, self.strike,
                                 self.entry_t, RISK_FREE_RATE, kind)

    def price_at(self, fut: float, close_ts: int) -> float:
        expiry_dt = datetime(self.expiry.year, self.expiry.month,
                             self.expiry.day, 15, 30)
        dt        = datetime.fromtimestamp(close_ts)
        secs      = max((expiry_dt - dt).total_seconds(), 1)
        t         = secs / (365 * 24 * 3600)
        return bsm(fut, self.strike, t, self.iv, RISK_FREE_RATE, self.kind)

    def pnl(self, fut: float, close_ts: int) -> float:
        return round(self.price_at(fut, close_ts) - self.entry_px, 2)


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR COMPUTATION
# ─────────────────────────────────────────────────────────────────────────────

def compute_indicators(candles: list) -> list:
    # Daily pivots from candle data
    daily = {}
    for c in candles:
        d = date.fromtimestamp(c["ts"])
        if d not in daily:
            daily[d] = {"h": c["high"], "l": c["low"], "c": c["close"]}
        else:
            daily[d]["h"] = max(daily[d]["h"], c["high"])
            daily[d]["l"] = min(daily[d]["l"], c["low"])
            daily[d]["c"] = c["close"]

    sorted_dates = sorted(daily.keys())
    pivot_cache  = {}
    for i, d in enumerate(sorted_dates):
        if i == 0:
            continue
        pd  = sorted_dates[i - 1]
        h, l, cp = daily[pd]["h"], daily[pd]["l"], daily[pd]["c"]
        pp  = (h + l + cp) / 3
        pivot_cache[d] = {"r1": 2*pp - l, "s1": 2*pp - h}

    # Indicator state
    bb_buf = deque(maxlen=BB_PERIOD)

    rsi_buf      = deque(maxlen=RSI_LENGTH + 1)
    rsi_gain     = None
    rsi_loss     = None
    rsi_sg       = []
    rsi_sl       = []
    rsi_smooth_q = deque(maxlen=RSI_SMOOTH)

    atr_val    = None
    atr_seed   = []
    prev_close = None
    fu = fl = st = None

    enriched = []
    for idx, c in enumerate(candles):
        h, l, close = c["high"], c["low"], c["close"]
        today = date.fromtimestamp(c["ts"])
        close_ts = c["ts"] + 180   # candle close time

        # BB
        bb_buf.append(close)
        bb_up = bb_lo = None
        if len(bb_buf) == BB_PERIOD:
            mean = sum(bb_buf) / BB_PERIOD
            std  = math.sqrt(sum((x-mean)**2 for x in bb_buf) / BB_PERIOD)
            bb_up = mean + BB_STD * std
            bb_lo = mean - BB_STD * std

        # RSI
        rsi_buf.append(close)
        rsi_raw = None
        if len(rsi_buf) >= 2:
            diff = rsi_buf[-1] - rsi_buf[-2]
            g, ls = max(diff, 0.0), abs(min(diff, 0.0))
            if rsi_gain is None:
                rsi_sg.append(g); rsi_sl.append(ls)
                if len(rsi_sg) == RSI_LENGTH:
                    rsi_gain = sum(rsi_sg) / RSI_LENGTH
                    rsi_loss = sum(rsi_sl) / RSI_LENGTH
                    rsi_sg = []; rsi_sl = []
            else:
                a = 1.0 / RSI_LENGTH
                rsi_gain = a*g  + (1-a)*rsi_gain
                rsi_loss = a*ls + (1-a)*rsi_loss

            if rsi_gain is not None:
                rsi_raw = 100.0 if rsi_loss == 0 else \
                          100.0 - 100.0/(1.0 + rsi_gain/rsi_loss)
                rsi_smooth_q.append(rsi_raw)

        # SuperTrend
        st_val = st_dir = None
        if prev_close is not None:
            tr = max(h-l, abs(h-prev_close), abs(l-prev_close))
            if atr_val is None:
                atr_seed.append(tr)
                if len(atr_seed) == ST_LENGTH:
                    atr_val  = sum(atr_seed) / ST_LENGTH
                    atr_seed = []
            else:
                atr_val = ((atr_val*(ST_LENGTH-1)) + tr) / ST_LENGTH

            if atr_val is not None:
                hl2 = (h+l) / 2
                bu  = hl2 + ST_MULTIPLIER * atr_val
                bl  = hl2 - ST_MULTIPLIER * atr_val
                if fu is None:
                    fu, fl, st = bu, bl, bu if close <= bu else bl
                else:
                    pfu, pfl = fu, fl
                    if bu < fu or prev_close > fu: fu = bu
                    if bl > fl or prev_close < fl: fl = bl
                    if st == pfu:
                        st = fu if close <= fu else fl
                    else:
                        st = fl if close >= fl else fu
                st_val = st
                st_dir = "UP" if st == fl else "DOWN"

        prev_close = close
        piv = pivot_cache.get(today, {})

        enriched.append({
            **c,
            "candle_close_ts": close_ts,
            "bb_upper":    bb_up,
            "bb_lower":    bb_lo,
            "rsi_raw":     rsi_raw,
            "supertrend":  st_val,
            "st_direction": st_dir,
            "r1":          piv.get("r1"),
            "s1":          piv.get("s1"),
            "_warmup":     idx < INDICATOR_WARMUP_BARS,
        })
    return enriched


by_symbol = {}
for c in raw_candles:
    by_symbol.setdefault(c["symbol"], []).append(c)

all_enriched = []
for sym, sym_c in by_symbol.items():
    all_enriched.extend(compute_indicators(sym_c))

all_enriched.sort(key=lambda x: x["ts"])
candles = all_enriched

print(f"Indicators computed: {len(candles)} candles")
print()

# ─────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def fmt(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

def r2(x):
    return round(x, 2) if x is not None else None

def fut_pnl(ep, xp, direction):
    return r2(xp - ep if direction == "LONG" else ep - xp)

def has_indicators(c):
    return (not c["_warmup"]
            and c["bb_upper"]    is not None
            and c["bb_lower"]    is not None
            and c["rsi_raw"]     is not None
            and c["supertrend"]  is not None
            and c["r1"]          is not None
            and c["s1"]          is not None)

def is_eod(ts):
    dt = datetime.fromtimestamp(ts)
    return dt.hour > EOD_HOUR or (dt.hour == EOD_HOUR and dt.minute >= EOD_MINUTE)

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY CONDITIONS
# ─────────────────────────────────────────────────────────────────────────────

def is_long(i):
    c = candles[i]
    return has_indicators(c) and (
        c["close"] > c["bb_upper"]
        and c["close"] > c["r1"]
        and c["rsi_raw"] > 70)

def is_short(i):
    c = candles[i]
    return has_indicators(c) and (
        c["close"] < c["bb_lower"]
        and c["close"] < c["s1"]
        and c["rsi_raw"] < 35)

# ─────────────────────────────────────────────────────────────────────────────
# EXIT CONDITIONS
# ─────────────────────────────────────────────────────────────────────────────

def range_exp(i, n=10):
    if i < n: return False
    vals = [candles[j]["high"] - candles[j]["low"]
            for j in range(i-n, i)
            if candles[j]["high"] and candles[j]["low"]]
    avg = sum(vals)/len(vals) if vals else 0
    return avg > 0 and (candles[i]["high"] - candles[i]["low"]) > 1.5 * avg

def bos_v1(i, d):
    if i < 1: return False
    p, c = candles[i-1], candles[i]
    return c["close"] < p["low"] if d == "LONG" else c["close"] > p["high"]

def st_dist(i, thr):
    c = candles[i]
    return c["supertrend"] is not None and abs(c["close"] - c["supertrend"]) <= thr

def momentum_v1(i, d, entry_idx):
    if (i - entry_idx) < MOMENTUM_MIN_BARS or i < 2: return False
    c0, c1, c2 = candles[i], candles[i-1], candles[i-2]
    return (c0["high"] < c1["high"] < c2["high"] if d == "LONG"
            else c0["low"] > c1["low"] > c2["low"])

def rsi_exhausted(i, d):
    if i < 1: return False
    pr, cr = candles[i-1]["rsi_raw"], candles[i]["rsi_raw"]
    if pr is None or cr is None: return False
    return (pr >= RSI_LONG_EXHAUST  and cr < RSI_LONG_EXHAUST  if d == "LONG"
            else pr <= RSI_SHORT_EXHAUST and cr > RSI_SHORT_EXHAUST)

def _swing_low(i):
    lb = SWING_LOOKBACK
    if i < lb or i+lb >= len(candles): return False
    p = candles[i]["low"]
    return all(candles[i-k]["low"] > p and candles[i+k]["low"] > p
               for k in range(1, lb+1))

def _swing_high(i):
    lb = SWING_LOOKBACK
    if i < lb or i+lb >= len(candles): return False
    p = candles[i]["high"]
    return all(candles[i-k]["high"] < p and candles[i+k]["high"] < p
               for k in range(1, lb+1))

def _structure(entry_idx, d):
    lb = SWING_LOOKBACK
    for i in range(entry_idx-1, lb-1, -1):
        if d == "LONG"  and _swing_low(i):  return candles[i]["low"]
        if d == "SHORT" and _swing_high(i): return candles[i]["high"]
    if entry_idx >= 1:
        c = candles[entry_idx-1]
        return c["low"] if d == "LONG" else c["high"]
    return None

def swing_bos(i, lvl, d):
    if lvl is None: return False
    return candles[i]["close"] < lvl if d == "LONG" else candles[i]["close"] > lvl

def dyn_threshold(idx):
    c = candles[idx]
    if c["supertrend"] and c["close"]:
        return max(abs(c["close"] - c["supertrend"]) * WEAK_PROFIT_RATIO, WEAK_PROFIT_MIN)
    return 30

# ─────────────────────────────────────────────────────────────────────────────
# HARD STOP — EOD + ST flip, whichever is earlier
# ─────────────────────────────────────────────────────────────────────────────

def hard_stop(entry_idx):
    entry_date = date.fromtimestamp(candles[entry_idx]["ts"])
    entry_stdir = candles[entry_idx].get("st_direction")
    eod_idx = st_idx = None

    for j in range(entry_idx+1, len(candles)):
        c      = candles[j]
        bar_dt = date.fromtimestamp(c["ts"])

        if bar_dt == entry_date and eod_idx is None:
            if is_eod(c["candle_close_ts"]):
                eod_idx = j

        if st_idx is None:
            d = c.get("st_direction")
            if d and entry_stdir and d != entry_stdir:
                st_idx = j

        if eod_idx is not None and st_idx is not None:
            break

    if eod_idx is not None and st_idx is not None:
        return min(eod_idx, st_idx)
    return eod_idx or st_idx


def first_signal(entry_idx, stop_idx, fn):
    for j in range(entry_idx+1, len(candles)):
        if stop_idx is not None and j > stop_idx:
            return stop_idx
        if fn(j):
            return j
    return stop_idx

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY EXIT FINDERS
# ─────────────────────────────────────────────────────────────────────────────

def exit_st_flip(ei, d):   return hard_stop(ei)
def exit_bos(ei, d):       return first_signal(ei, hard_stop(ei), lambda j: bos_v1(j, d))
def exit_range(ei, d):     return first_signal(ei, hard_stop(ei), lambda j: range_exp(j))
def exit_st20(ei, d):      return first_signal(ei, hard_stop(ei), lambda j: st_dist(j, 20))
def exit_st30(ei, d):      return first_signal(ei, hard_stop(ei), lambda j: st_dist(j, 30))

def exit_momentum(ei, d):
    stop = hard_stop(ei)
    return first_signal(ei, stop, lambda j: momentum_v1(j, d, ei))

def exit_rsi_exhaust(ei, d):
    stop = hard_stop(ei)
    return first_signal(ei, stop, lambda j: rsi_exhausted(j, d))

def exit_swing_bos(ei, d):
    stop = hard_stop(ei)
    lvl  = _structure(ei, d)
    return first_signal(ei, stop, lambda j: swing_bos(j, lvl, d))

def exit_tiered(ei, d):
    stop      = hard_stop(ei)
    ep        = candles[ei]["close"]
    threshold = dyn_threshold(ei)
    lvl       = _structure(ei, d)

    for j in range(ei+1, len(candles)):
        if stop is not None and j > stop: return stop
        curr_profit = fut_pnl(ep, candles[j]["close"], d)
        if rsi_exhausted(j, d):
            if curr_profit is not None and curr_profit <= 0:        return j
            elif curr_profit is not None and curr_profit < threshold: return j
        if momentum_v1(j, d, ei):  return j
        if swing_bos(j, lvl, d):   return j
    return stop

STRATEGIES = {
    "st_flip":     exit_st_flip,
    "bos":         exit_bos,
    "range":       exit_range,
    "st20":        exit_st20,
    "st30":        exit_st30,
    "momentum_v1": exit_momentum,
    "rsi_exhaust": exit_rsi_exhaust,
    "swing_bos":   exit_swing_bos,
    "tiered":      exit_tiered,
}

# ─────────────────────────────────────────────────────────────────────────────
# BACKTEST LOOP
# ─────────────────────────────────────────────────────────────────────────────

trade_rows = []
fut_totals = {s: 0.0 for s in STRATEGIES}
opt_totals = {s: 0.0 for s in STRATEGIES}
wins_f     = {s: 0   for s in STRATEGIES}
losses_f   = {s: 0   for s in STRATEGIES}
wins_o     = {s: 0   for s in STRATEGIES}
losses_o   = {s: 0   for s in STRATEGIES}

i = trade_id = 1

while i < len(candles):
    if   is_long(i):  direction, kind = "LONG",  "CE"
    elif is_short(i): direction, kind = "SHORT", "PE"
    else:
        i += 1
        continue

    ec        = candles[i]
    ep        = ec["close"]
    entry_ts  = ec["candle_close_ts"]
    entry_dt  = date.fromtimestamp(ec["ts"])

    # KEY FIX: expiry from trade date, not DB symbol
    expiry    = expiry_for_trade_date(entry_dt)

    pricer = OptionPricer(
        entry_fut=ep, kind=kind,
        expiry=expiry, entry_close_ts=entry_ts,
    )

    init_sl = abs(ep - ec["supertrend"]) if ec["supertrend"] else None

    row = {
        "trade_id":      trade_id,
        "symbol":        ec["symbol"],
        "entry_time":    fmt(entry_ts),
        "direction":     direction,
        "option_type":   kind,
        "entry_fut_px":  r2(ep),
        "entry_rsi":     r2(ec["rsi_raw"]),
        "expiry":        str(expiry),
        "strike":        pricer.strike,
        "entry_opt_px":  r2(pricer.entry_px),
        "iv_used":       r2(pricer.iv),
        "init_sl_dist":  r2(init_sl),
        "dyn_threshold": r2(dyn_threshold(i)),
    }

    stop_idx = hard_stop(i)

    for s, fn in STRATEGIES.items():
        eidx = fn(i, direction)
        if eidx is None:
            row[f"{s}_exit"]    = "—"
            row[f"{s}_fut_pnl"] = None
            row[f"{s}_opt_pnl"] = None
            row[f"{s}_bars"]    = None
            continue

        xc  = candles[eidx]
        xp  = xc["close"]
        xts = xc["candle_close_ts"]

        fp = fut_pnl(ep, xp, direction)
        op = pricer.pnl(xp, xts)

        row[f"{s}_exit"]    = fmt(xts)
        row[f"{s}_fut_pnl"] = fp
        row[f"{s}_opt_pnl"] = r2(op)
        row[f"{s}_bars"]    = eidx - i

        fut_totals[s] += fp or 0
        opt_totals[s] += op or 0
        if fp is not None:
            (wins_f if fp > 0 else losses_f)[s] += 1
        if op is not None:
            (wins_o if op > 0 else losses_o)[s] += 1

    trade_rows.append(row)
    trade_id += 1
    i = (stop_idx + 1) if stop_idx else (i + 1)

# ─────────────────────────────────────────────────────────────────────────────
# SAVE CSV
# ─────────────────────────────────────────────────────────────────────────────

total_row = {"trade_id": "TOTAL"}
for s in STRATEGIES:
    total_row[f"{s}_fut_pnl"] = r2(fut_totals[s])
    total_row[f"{s}_opt_pnl"] = r2(opt_totals[s])
trade_rows.append(total_row)

csv_file = "trade_analysis.csv"
if len(trade_rows) > 1:
    keys = list(trade_rows[0].keys())
    with open(csv_file, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(trade_rows)
    print(f"Saved -> {csv_file}  ({trade_id - 1} trades)")

# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

data_rows = trade_rows[:-1]
STRATS    = list(STRATEGIES.keys())
COL       = 14

def print_block(label, totals, wins, losses, pnl_sfx):
    print(f"\n── {label} ──")
    print(f"{'Strategy':<18}", end="")
    for s in STRATS: print(f"{s:>{COL}}", end="")
    print()
    print("─" * (18 + COL * len(STRATS)))

    for metric, fn in [
        ("Total PnL",     lambda s: f"{totals[s]:>+{COL}.1f}"),
        ("Win / Loss",    lambda s: f"{wins[s]}W/{losses[s]}L".rjust(COL)),
        ("Win %",         lambda s: f"{(wins[s]/(wins[s]+losses[s])*100 if wins[s]+losses[s] else 0):>{COL-1}.0f}%"),
        ("Avg PnL/trade", lambda s: f"{(totals[s]/(wins[s]+losses[s]) if wins[s]+losses[s] else 0):>+{COL}.1f}"),
        ("Avg Bars",      lambda s: f"{(sum(r[f'{s}_bars'] for r in data_rows if r.get(f'{s}_bars'))/(len([r for r in data_rows if r.get(f'{s}_bars')]) or 1)):>{COL}.1f}"),
        ("Max Win",       lambda s: f"{max((r[f'{s}_{pnl_sfx}'] for r in data_rows if r.get(f'{s}_{pnl_sfx}') is not None), default=0):>+{COL}.1f}"),
        ("Max Loss",      lambda s: f"{min((r[f'{s}_{pnl_sfx}'] for r in data_rows if r.get(f'{s}_{pnl_sfx}') is not None), default=0):>+{COL}.1f}"),
    ]:
        print(f"{metric:<18}", end="")
        for s in STRATS: print(fn(s), end="")
        print()

print_block("FUTURES P&L (points)", fut_totals, wins_f, losses_f, "fut_pnl")
print_block("OPTION P&L — BSM theta-adjusted, CORRECT expiry per trade", opt_totals, wins_o, losses_o, "opt_pnl")

print(f"""
Config:
  BB         : period={BB_PERIOD}, std={BB_STD}
  RSI        : length={RSI_LENGTH}, smooth={RSI_SMOOTH}
  SuperTrend : length={ST_LENGTH}, mult={ST_MULTIPLIER}
  RSI exhaust: LONG<{RSI_LONG_EXHAUST}, SHORT>{RSI_SHORT_EXHAUST}
  Swing LB   : {SWING_LOOKBACK} bars each side
  Momentum guard: first {MOMENTUM_MIN_BARS} bars ignored
  EOD exit   : {EOD_HOUR}:{EOD_MINUTE:02d} IST
  Max premium: {MAX_ENTRY_PREMIUM}
  Trades     : {trade_id - 1}
""")

# ─────────────────────────────────────────────────────────────────────────────
# PER-TRADE DETAIL
# ─────────────────────────────────────────────────────────────────────────────

print(f"{'#':<4} {'Time':<18} {'D':<6} {'Exp':^10} {'Strk':>6} "
      f"{'EntOpt':>7} {'sfF':>7} {'sfO':>7} "
      f"{'s20F':>7} {'s20O':>7} {'tiF':>7} {'tiO':>7} {'reF':>7} {'reO':>7}")
print("─" * 110)

for r in data_rows:
    def v(k): return f"{r[k]:>+7.1f}" if r.get(k) is not None else "      —"
    print(
        f"{r['trade_id']:<4} {r['entry_time']:<18} {r['direction']:<6} "
        f"{r['expiry']:^10} {r.get('strike',0):>6.0f} "
        f"{r.get('entry_opt_px',0):>7.1f} "
        f"{v('st_flip_fut_pnl')} {v('st_flip_opt_pnl')} "
        f"{v('st20_fut_pnl')} {v('st20_opt_pnl')} "
        f"{v('tiered_fut_pnl')} {v('tiered_opt_pnl')} "
        f"{v('rsi_exhaust_fut_pnl')} {v('rsi_exhaust_opt_pnl')}"
    )

print("\nColumns: F=futures pts, O=option pts (BSM, theta with correct expiry)")