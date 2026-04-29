#!/usr/bin/env python3
"""
analyse_supertrend_flips.py — BB Strategy Exit Analysis (v4)
=============================================================

Changes vs v3:
  FIX 1 - Time display: show candle CLOSE time (ts + 180) not open time
  FIX 2 - EOD hard exit at 15:25 IST — no overnight carry
  FIX 3 - Black-Scholes option P&L replaces raw futures P&L
           Picks the nearest strike <= MAX_ENTRY_PREMIUM using
           back-solved IV from entry option price, then prices
           both entry and exit to get real option P&L
  FIX 4 - momentum_v1 guard: won't fire in first MOMENTUM_MIN_BARS
           bars of a trade (prevents consolidation noise exits)

Run:
    python3 analyse_supertrend_flips.py
    python3 analyse_supertrend_flips.py --symbol BANKNIFTY26APRFUT
    python3 analyse_supertrend_flips.py --tf 3m

Output:
    trade_analysis.csv   — full per-trade detail
    console              — summary table + per-trade comparison
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

# Bollinger Bands
BB_PERIOD = 20
BB_STD    = 2

# RSI (Wilder's RMA, matches live IndicatorBundle exactly)
RSI_LENGTH = 14
RSI_SMOOTH = 3

# SuperTrend
ST_LENGTH     = 10
ST_MULTIPLIER = 2.0

# Exit thresholds
RSI_LONG_EXHAUST  = 60    # LONG momentum exhausted when RSI drops below this
RSI_SHORT_EXHAUST = 40    # SHORT momentum exhausted when RSI rises above this
SWING_LOOKBACK    = 2     # bars each side for swing pivot confirmation
WEAK_PROFIT_RATIO = 0.5   # profit < ratio * initial SL distance -> "weak"
WEAK_PROFIT_MIN   = 10    # absolute floor on weak-profit threshold (futures pts)

# FIX 4: momentum guard
MOMENTUM_MIN_BARS = 5     # momentum_v1 won't fire in first N bars of a trade

# FIX 2: EOD exit
EOD_HOUR   = 15
EOD_MINUTE = 25

# Warmup bars per contract before trusting indicators
INDICATOR_WARMUP_BARS = 50

# FIX 3: Option pricing config
# Your typical entry is in options priced just below this level
MAX_ENTRY_PREMIUM = 600.0

# Option lot size (for P&L in rupees alongside points)
LOT_SIZE = 30

# Approximate risk-free rate (negligible but included for correctness)
RISK_FREE_RATE = 0.065

# If BSM IV solve fails, fall back to this IV
FALLBACK_IV = 0.25

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
        """
        SELECT symbol, ts, open, high, low, close
        FROM futures_candles
        WHERE symbol = ? AND timeframe = ?
        ORDER BY ts ASC
        """,
        (args.symbol, args.tf),
    ).fetchall()
else:
    rows = conn.execute(
        """
        SELECT symbol, ts, open, high, low, close
        FROM futures_candles
        WHERE timeframe = ?
        ORDER BY ts ASC
        """,
        (args.tf,),
    ).fetchall()

raw_candles = [dict(r) for r in rows]
if not raw_candles:
    print("No candles found. Check --symbol / --tf or DB path.")
    raise SystemExit(1)

symbols_found = sorted({c["symbol"] for c in raw_candles})
print(f"Loaded    : {len(raw_candles)} raw OHLC candles")
print(f"Symbols   : {', '.join(symbols_found)}")
print(f"Date range: {datetime.fromtimestamp(raw_candles[0]['ts']):%Y-%m-%d} "
      f"-> {datetime.fromtimestamp(raw_candles[-1]['ts']):%Y-%m-%d}")
print()

# ─────────────────────────────────────────────────────────────────────────────
# FIX 3 — BLACK-SCHOLES OPTION PRICING
# ─────────────────────────────────────────────────────────────────────────────

def _erf_approx(x: float) -> float:
    """Abramowitz & Stegun approximation — good to 4 decimal places."""
    a = [0.0, 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429]
    p = 0.3275911
    sign = 1 if x >= 0 else -1
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a[5]*t + a[4])*t) + a[3])*t + a[2])*t + a[1])*t * math.exp(-x*x)
    return sign * y

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + _erf_approx(x / math.sqrt(2)))

def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

def bsm_price(
    spot:    float,
    strike:  float,
    t_years: float,
    iv:      float,
    r:       float,
    option_type: str,   # "CE" or "PE"
) -> float:
    """Black-Scholes-Merton option price. Returns 0 on bad inputs."""
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        # At / past expiry: intrinsic value only
        if option_type == "CE":
            return max(spot - strike, 0.0)
        return max(strike - spot, 0.0)

    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / (iv * math.sqrt(t_years))
    d2 = d1 - iv * math.sqrt(t_years)

    if option_type == "CE":
        return spot * _norm_cdf(d1) - strike * math.exp(-r * t_years) * _norm_cdf(d2)
    # PE
    return strike * math.exp(-r * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def implied_vol(
    market_price: float,
    spot:         float,
    strike:       float,
    t_years:      float,
    r:            float,
    option_type:  str,
    tol:          float = 0.01,
    max_iter:     int   = 100,
) -> float:
    """
    Newton-Raphson IV solver.
    Falls back to FALLBACK_IV if it doesn't converge.
    """
    if t_years <= 0 or market_price <= 0:
        return FALLBACK_IV

    iv = 0.3   # initial guess

    for _ in range(max_iter):
        price = bsm_price(spot, strike, t_years, iv, r, option_type)
        diff  = price - market_price

        if abs(diff) < tol:
            return iv

        # Vega
        d1   = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / (iv * math.sqrt(t_years))
        vega = spot * _norm_pdf(d1) * math.sqrt(t_years)

        if abs(vega) < 1e-8:
            break

        iv -= diff / vega
        iv  = max(0.01, min(iv, 10.0))   # clamp to sane range

    return iv if 0.01 <= iv <= 10.0 else FALLBACK_IV


def _parse_expiry_from_symbol(symbol: str) -> date:
    """
    Parse expiry date from Zerodha tradingsymbol.
    Format: BANKNIFTY26APRFUT  -> last trading day of April 2026
            BANKNIFTY26MARFUT  -> last trading day of March 2026

    For monthly futures we approximate as the last Thursday of that month.
    """
    MONTHS = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
        "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
        "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    }

    # Try to find YYMMM in symbol
    sym = symbol.upper().replace("BANKNIFTY", "").replace("FUT", "").replace("NIFTY", "")
    # sym should be like "26APR"
    if len(sym) >= 5:
        yy_str  = sym[:2]
        mon_str = sym[2:5]
        if mon_str in MONTHS:
            year  = 2000 + int(yy_str)
            month = MONTHS[mon_str]
            # Last Thursday of the month
            # Find last day, walk back to Thursday (weekday 3)
            if month == 12:
                last_day = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                last_day = date(year, month + 1, 1) - timedelta(days=1)
            while last_day.weekday() != 3:   # 3 = Thursday
                last_day -= timedelta(days=1)
            return last_day

    # Fallback: 30 days from now
    return date.today() + timedelta(days=30)


def _pick_option_strike(
    futures_price: float,
    option_type:   str,
    target_premium: float,
    t_years:       float,
    iv:            float,
) -> float:
    """
    Walk strikes in 100-point steps away from ATM until BSM price
    is just at or below target_premium.
    Returns the best strike found.
    """
    atm = round(futures_price / 100) * 100
    best_strike = atm
    step = 100

    for i in range(0, 60):
        if option_type == "CE":
            strike = atm + i * step
        else:
            strike = atm - i * step

        price = bsm_price(futures_price, strike, t_years, iv, RISK_FREE_RATE, option_type)

        if price <= target_premium:
            best_strike = strike
            break

    return best_strike


class OptionPricer:
    """
    Prices the option for one trade.
    Constructed at trade entry, called at each exit bar.
    """

    def __init__(
        self,
        entry_futures_price: float,
        option_type:         str,   # "CE" or "PE"
        expiry:              date,
        entry_bar_ts:        int,   # candle close ts (ts + 180)
    ):
        self.option_type = option_type
        self.expiry      = expiry
        self.entry_ts    = entry_bar_ts   # seconds

        # Time to expiry at entry (years)
        entry_dt = datetime.fromtimestamp(entry_bar_ts)
        expiry_dt = datetime(expiry.year, expiry.month, expiry.day, 15, 30)
        secs_to_expiry = max((expiry_dt - entry_dt).total_seconds(), 1)
        self.entry_t = secs_to_expiry / (365 * 24 * 3600)

        # Estimate IV from a target entry price just below MAX_ENTRY_PREMIUM
        # We pick a strike that gives a premium close to MAX_ENTRY_PREMIUM and
        # back-solve IV — this is the vol regime the strategy operates in.
        target = MAX_ENTRY_PREMIUM * 0.95   # aim just below cap
        iv_guess = FALLBACK_IV

        # Quick IV estimation: try BSM with fallback IV to find a reasonable strike
        self.strike = _pick_option_strike(
            entry_futures_price, option_type, target, self.entry_t, iv_guess
        )

        self.entry_option_price = bsm_price(
            entry_futures_price, self.strike, self.entry_t,
            iv_guess, RISK_FREE_RATE, option_type
        )

        # Back-solve IV from the estimated entry price
        self.iv = implied_vol(
            self.entry_option_price,
            entry_futures_price,
            self.strike,
            self.entry_t,
            RISK_FREE_RATE,
            option_type,
        )

    def exit_option_price(self, exit_futures_price: float, exit_bar_ts: int) -> float:
        """Price the option at exit using the same IV (simplified — holds vol flat)."""
        exit_dt   = datetime.fromtimestamp(exit_bar_ts)
        expiry_dt = datetime(self.expiry.year, self.expiry.month, self.expiry.day, 15, 30)
        secs_left = max((expiry_dt - exit_dt).total_seconds(), 1)
        t_exit    = secs_left / (365 * 24 * 3600)

        return bsm_price(
            exit_futures_price, self.strike,
            t_exit, self.iv, RISK_FREE_RATE, self.option_type
        )

    def option_pnl(self, exit_futures_price: float, exit_bar_ts: int) -> float:
        """Option P&L in points (premium)."""
        exit_px = self.exit_option_price(exit_futures_price, exit_bar_ts)
        return round(exit_px - self.entry_option_price, 2)

# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR COMPUTATION (same as v3, matches live IndicatorBundle)
# ─────────────────────────────────────────────────────────────────────────────

def compute_indicators(candles: list) -> list:

    # Daily pivot map (same contract)
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
    prev_day_map = {d: sorted_dates[i-1] for i, d in enumerate(sorted_dates) if i > 0}

    pivot_cache = {}
    for d, pd in prev_day_map.items():
        h, l, cp = daily[pd]["h"], daily[pd]["l"], daily[pd]["c"]
        pp = (h + l + cp) / 3
        pivot_cache[d] = {"pp": pp, "r1": 2*pp - l, "s1": 2*pp - h}

    # Indicator state
    bb_closes = deque(maxlen=BB_PERIOD)

    rsi_closes      = deque(maxlen=RSI_LENGTH + 1)
    rsi_avg_gain    = None
    rsi_avg_loss    = None
    rsi_seed_gains  = []
    rsi_seed_losses = []
    rsi_values      = deque(maxlen=RSI_SMOOTH)

    atr           = None
    atr_seed      = []
    prev_close_st = None
    final_upper   = None
    final_lower   = None
    supertrend    = None

    enriched = []

    for idx, c in enumerate(candles):
        h, l, close = c["high"], c["low"], c["close"]
        today = date.fromtimestamp(c["ts"])

        # FIX 1: candle_time = close time = ts + 180
        candle_close_ts = c["ts"] + 180

        # Bollinger Bands
        bb_closes.append(close)
        bb_mid = bb_up = bb_lo = None
        if len(bb_closes) == BB_PERIOD:
            mean     = sum(bb_closes) / BB_PERIOD
            variance = sum((x - mean)**2 for x in bb_closes) / BB_PERIOD
            std      = math.sqrt(variance)
            bb_mid, bb_up, bb_lo = mean, mean + BB_STD*std, mean - BB_STD*std

        # RSI
        rsi_closes.append(close)
        rsi_raw = rsi_smooth = None
        if len(rsi_closes) >= 2:
            diff = rsi_closes[-1] - rsi_closes[-2]
            gain = max(diff, 0.0)
            loss = abs(min(diff, 0.0))

            if rsi_avg_gain is None:
                rsi_seed_gains.append(gain)
                rsi_seed_losses.append(loss)
                if len(rsi_seed_gains) == RSI_LENGTH:
                    rsi_avg_gain    = sum(rsi_seed_gains)  / RSI_LENGTH
                    rsi_avg_loss    = sum(rsi_seed_losses) / RSI_LENGTH
                    rsi_seed_gains  = []
                    rsi_seed_losses = []
            else:
                alpha        = 1.0 / RSI_LENGTH
                rsi_avg_gain = alpha * gain + (1 - alpha) * rsi_avg_gain
                rsi_avg_loss = alpha * loss + (1 - alpha) * rsi_avg_loss

            if rsi_avg_gain is not None:
                rsi_raw = 100.0 if rsi_avg_loss == 0 else \
                          100.0 - (100.0 / (1.0 + rsi_avg_gain / rsi_avg_loss))
                rsi_values.append(rsi_raw)
                if len(rsi_values) == RSI_SMOOTH:
                    rsi_smooth = sum(rsi_values) / RSI_SMOOTH

        # SuperTrend
        st_value = st_dir = None
        if prev_close_st is not None:
            tr = max(h - l, abs(h - prev_close_st), abs(l - prev_close_st))
            if atr is None:
                atr_seed.append(tr)
                if len(atr_seed) == ST_LENGTH:
                    atr = sum(atr_seed) / ST_LENGTH
                    atr_seed = []
            else:
                atr = ((atr * (ST_LENGTH - 1)) + tr) / ST_LENGTH

            if atr is not None:
                hl2         = (h + l) / 2
                basic_upper = hl2 + ST_MULTIPLIER * atr
                basic_lower = hl2 - ST_MULTIPLIER * atr

                if final_upper is None:
                    final_upper = basic_upper
                    final_lower = basic_lower
                    supertrend  = basic_upper if close <= basic_upper else basic_lower
                else:
                    prev_fu, prev_fl = final_upper, final_lower
                    if basic_upper < final_upper or prev_close_st > final_upper:
                        final_upper = basic_upper
                    if basic_lower > final_lower or prev_close_st < final_lower:
                        final_lower = basic_lower
                    if supertrend == prev_fu:
                        supertrend = final_upper if close <= final_upper else final_lower
                    else:
                        supertrend = final_lower if close >= final_lower else final_upper

                st_value = supertrend
                st_dir   = "UP" if supertrend == final_lower else "DOWN"

        prev_close_st = close
        piv = pivot_cache.get(today, {})

        enriched.append({
            **c,
            "candle_close_ts": candle_close_ts,   # FIX 1
            "bb_upper":    bb_up,
            "bb_lower":    bb_lo,
            "bb_middle":   bb_mid,
            "rsi_raw":     rsi_raw,
            "rsi_smooth":  rsi_smooth,
            "supertrend":  st_value,
            "st_direction": st_dir,
            "r1":          piv.get("r1"),
            "s1":          piv.get("s1"),
            "_warmup":     idx < INDICATOR_WARMUP_BARS,
        })

    return enriched


# Build per-symbol enriched lists, merge by ts
by_symbol = {}
for c in raw_candles:
    by_symbol.setdefault(c["symbol"], []).append(c)

enriched_all = []
for sym, sym_candles in by_symbol.items():
    enriched_all.extend(compute_indicators(sym_candles))

enriched_all.sort(key=lambda x: x["ts"])
candles = enriched_all

# Pre-compute expiry per symbol
expiry_map = {sym: _parse_expiry_from_symbol(sym) for sym in by_symbol}

print(f"Indicators computed: {len(candles)} candles across {len(by_symbol)} contract(s)")
for sym, exp in expiry_map.items():
    print(f"  {sym} -> expiry {exp}")
print()

# ─────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def fmt(ts: int) -> str:
    """FIX 1: ts here is candle_close_ts (already +180)."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

def r2(x):
    return round(x, 2) if x is not None else None

def futures_pnl(entry_price, exit_price, direction):
    if direction == "LONG":
        return r2(exit_price - entry_price)
    return r2(entry_price - exit_price)

def has_indicators(c):
    return (
        not c["_warmup"]
        and c["bb_upper"]   is not None
        and c["bb_lower"]   is not None
        and c["rsi_raw"]    is not None
        and c["supertrend"] is not None
        and c["r1"]         is not None
        and c["s1"]         is not None
    )

def is_eod(ts: int) -> bool:
    """FIX 2: True if this candle close time is at or past 15:25."""
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
        and c["rsi_raw"] > 70
    )

def is_short(i):
    c = candles[i]
    return has_indicators(c) and (
        c["close"] < c["bb_lower"]
        and c["close"] < c["s1"]
        and c["rsi_raw"] < 35
    )

# ─────────────────────────────────────────────────────────────────────────────
# EXIT CONDITIONS
# ─────────────────────────────────────────────────────────────────────────────

def range_exp(i, n=10):
    if i < n:
        return False
    vals = [candles[j]["high"] - candles[j]["low"]
            for j in range(i-n, i) if candles[j]["high"] and candles[j]["low"]]
    avg = sum(vals) / len(vals) if vals else 0
    return avg > 0 and (candles[i]["high"] - candles[i]["low"]) > 1.5 * avg

def bos_v1(i, direction):
    if i < 1:
        return False
    p, c = candles[i-1], candles[i]
    return c["close"] < p["low"] if direction == "LONG" else c["close"] > p["high"]

def st_dist_exit(i, threshold):
    c = candles[i]
    return c["supertrend"] is not None and abs(c["close"] - c["supertrend"]) <= threshold

def momentum_v1(i, direction, entry_idx):
    """FIX 4: won't fire in first MOMENTUM_MIN_BARS bars."""
    if (i - entry_idx) < MOMENTUM_MIN_BARS:
        return False
    if i < 2:
        return False
    c0, c1, c2 = candles[i], candles[i-1], candles[i-2]
    if direction == "LONG":
        return c0["high"] < c1["high"] < c2["high"]
    return c0["low"] > c1["low"] > c2["low"]

def rsi_exhausted(i, direction):
    if i < 1:
        return False
    pr, cr = candles[i-1]["rsi_raw"], candles[i]["rsi_raw"]
    if pr is None or cr is None:
        return False
    if direction == "LONG":
        return pr >= RSI_LONG_EXHAUST and cr < RSI_LONG_EXHAUST
    return pr <= RSI_SHORT_EXHAUST and cr > RSI_SHORT_EXHAUST

def _is_swing_low(i):
    lb = SWING_LOOKBACK
    if i < lb or i + lb >= len(candles):
        return False
    pivot = candles[i]["low"]
    return all(candles[i-k]["low"] > pivot and candles[i+k]["low"] > pivot
               for k in range(1, lb+1))

def _is_swing_high(i):
    lb = SWING_LOOKBACK
    if i < lb or i + lb >= len(candles):
        return False
    pivot = candles[i]["high"]
    return all(candles[i-k]["high"] < pivot and candles[i+k]["high"] < pivot
               for k in range(1, lb+1))

def _find_structure_level(entry_idx, direction):
    lb = SWING_LOOKBACK
    for i in range(entry_idx-1, lb-1, -1):
        if direction == "LONG" and _is_swing_low(i):
            return candles[i]["low"]
        if direction == "SHORT" and _is_swing_high(i):
            return candles[i]["high"]
    if entry_idx >= 1:
        c = candles[entry_idx-1]
        return c["low"] if direction == "LONG" else c["high"]
    return None

def swing_bos_fired(i, structure, direction):
    if structure is None:
        return False
    c = candles[i]
    return c["close"] < structure if direction == "LONG" else c["close"] > structure

def dynamic_threshold(entry_idx):
    c = candles[entry_idx]
    if c["supertrend"] and c["close"]:
        return max(abs(c["close"] - c["supertrend"]) * WEAK_PROFIT_RATIO, WEAK_PROFIT_MIN)
    return 30

# ─────────────────────────────────────────────────────────────────────────────
# FIX 2 — EOD + ST FLIP combined stop
# All exit finders call this to get the hard outer boundary.
# Returns the earlier of: EOD bar OR SuperTrend flip bar.
# ─────────────────────────────────────────────────────────────────────────────

def find_hard_stop(entry_idx: int) -> int | None:
    """
    Returns the index of the hard stop bar: the earlier of
    the EOD 15:25 bar or the SuperTrend flip bar.
    None only if neither is found (shouldn't happen in practice).
    """
    entry_date = date.fromtimestamp(candles[entry_idx]["ts"])
    eod_idx    = None
    st_idx     = None
    entry_st   = candles[entry_idx].get("st_direction")

    for j in range(entry_idx + 1, len(candles)):
        c = candles[j]
        bar_date = date.fromtimestamp(c["ts"])

        # FIX 2: EOD check — only fire on same calendar day
        if bar_date == entry_date and eod_idx is None:
            if is_eod(c["candle_close_ts"]):
                eod_idx = j

        # ST flip
        if st_idx is None:
            d = c.get("st_direction")
            if d and entry_st and d != entry_st:
                st_idx = j

        # Once both are found we can stop scanning
        if eod_idx is not None and st_idx is not None:
            break

    # Return the earlier stop
    if eod_idx is not None and st_idx is not None:
        return min(eod_idx, st_idx)
    return eod_idx or st_idx


def _first_signal_before_stop(entry_idx, stop_idx, condition_fn):
    """Walk bars between entry and stop; return first bar where condition fires."""
    for j in range(entry_idx + 1, len(candles)):
        if stop_idx is not None and j > stop_idx:
            return stop_idx
        if condition_fn(j):
            return j
    return stop_idx

# ─────────────────────────────────────────────────────────────────────────────
# PER-STRATEGY EXIT FINDERS
# ─────────────────────────────────────────────────────────────────────────────

def exit_st_flip(entry_idx, direction):
    # For st_flip strategy we still apply EOD stop
    return find_hard_stop(entry_idx)

def exit_bos(entry_idx, direction):
    stop = find_hard_stop(entry_idx)
    return _first_signal_before_stop(entry_idx, stop, lambda j: bos_v1(j, direction))

def exit_range(entry_idx, direction):
    stop = find_hard_stop(entry_idx)
    return _first_signal_before_stop(entry_idx, stop, lambda j: range_exp(j))

def exit_st20(entry_idx, direction):
    stop = find_hard_stop(entry_idx)
    return _first_signal_before_stop(entry_idx, stop, lambda j: st_dist_exit(j, 20))

def exit_st30(entry_idx, direction):
    stop = find_hard_stop(entry_idx)
    return _first_signal_before_stop(entry_idx, stop, lambda j: st_dist_exit(j, 30))

def exit_momentum_v1(entry_idx, direction):
    stop = find_hard_stop(entry_idx)
    return _first_signal_before_stop(
        entry_idx, stop,
        lambda j: momentum_v1(j, direction, entry_idx),
    )

def exit_rsi_exhaust(entry_idx, direction):
    stop = find_hard_stop(entry_idx)
    return _first_signal_before_stop(entry_idx, stop, lambda j: rsi_exhausted(j, direction))

def exit_swing_bos(entry_idx, direction):
    stop      = find_hard_stop(entry_idx)
    structure = _find_structure_level(entry_idx, direction)
    return _first_signal_before_stop(
        entry_idx, stop,
        lambda j: swing_bos_fired(j, structure, direction),
    )

def exit_tiered(entry_idx, direction):
    stop      = find_hard_stop(entry_idx)
    entry_p   = candles[entry_idx]["close"]
    threshold = dynamic_threshold(entry_idx)
    structure = _find_structure_level(entry_idx, direction)

    for j in range(entry_idx + 1, len(candles)):
        if stop is not None and j > stop:
            return stop

        curr_profit = futures_pnl(entry_p, candles[j]["close"], direction)
        exhausted   = rsi_exhausted(j, direction)

        if exhausted:
            if curr_profit is not None and curr_profit <= 0:
                return j                       # Arm 1: loss protection
            elif curr_profit is not None and curr_profit < threshold:
                return j                       # Arm 2: weak profit
            # Arm 3: strong trend — hold

        if momentum_v1(j, direction, entry_idx):
            return j                           # Arm 4: fast reversal (guarded)

        if swing_bos_fired(j, structure, direction):
            return j                           # Arm 5: structure break

    return stop

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

STRATEGIES = {
    "st_flip":     exit_st_flip,
    "bos":         exit_bos,
    "range":       exit_range,
    "st20":        exit_st20,
    "st30":        exit_st30,
    "momentum_v1": exit_momentum_v1,
    "rsi_exhaust": exit_rsi_exhaust,
    "swing_bos":   exit_swing_bos,
    "tiered":      exit_tiered,
}

# ─────────────────────────────────────────────────────────────────────────────
# BACKTEST LOOP
# ─────────────────────────────────────────────────────────────────────────────

trade_rows = []

# Aggregate stats: futures AND option P&L
fut_totals = {s: 0.0 for s in STRATEGIES}
opt_totals = {s: 0.0 for s in STRATEGIES}
wins_f     = {s: 0   for s in STRATEGIES}
losses_f   = {s: 0   for s in STRATEGIES}
wins_o     = {s: 0   for s in STRATEGIES}
losses_o   = {s: 0   for s in STRATEGIES}

i        = 0
trade_id = 1

while i < len(candles):
    if is_long(i):
        direction   = "LONG"
        option_type = "CE"
    elif is_short(i):
        direction   = "SHORT"
        option_type = "PE"
    else:
        i += 1
        continue

    entry_c   = candles[i]
    entry_p   = entry_c["close"]
    entry_sym = entry_c["symbol"]
    expiry    = expiry_map.get(entry_sym, date.today() + timedelta(days=30))

    init_sl   = (abs(entry_p - entry_c["supertrend"])
                 if entry_c["supertrend"] else None)
    dyn_thr   = dynamic_threshold(i)

    # FIX 1: display candle close time
    entry_display_ts = entry_c["candle_close_ts"]

    # FIX 3: build option pricer at entry
    pricer = OptionPricer(
        entry_futures_price=entry_p,
        option_type=option_type,
        expiry=expiry,
        entry_bar_ts=entry_display_ts,
    )

    row = {
        "trade_id":       trade_id,
        "symbol":         entry_sym,
        "entry_time":     fmt(entry_display_ts),      # FIX 1
        "direction":      direction,
        "option_type":    option_type,
        "entry_fut_px":   r2(entry_p),
        "entry_rsi":      r2(entry_c["rsi_raw"]),
        "strike":         pricer.strike,
        "entry_opt_px":   r2(pricer.entry_option_price),
        "init_sl_dist":   r2(init_sl),
        "dyn_threshold":  r2(dyn_thr),
    }

    stop_idx = find_hard_stop(i)

    for s, exit_fn in STRATEGIES.items():
        eidx = exit_fn(i, direction)

        if eidx is None:
            row[f"{s}_exit"]    = "no exit"
            row[f"{s}_fut_pnl"] = None
            row[f"{s}_opt_pnl"] = None
            row[f"{s}_bars"]    = None
            continue

        exit_c   = candles[eidx]
        exit_p   = exit_c["close"]
        exit_ts  = exit_c["candle_close_ts"]   # FIX 1

        fp = futures_pnl(entry_p, exit_p, direction)
        op = pricer.option_pnl(exit_p, exit_ts)    # FIX 3

        row[f"{s}_exit"]    = fmt(exit_ts)          # FIX 1
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

    # Advance past the hard stop to avoid overlapping trades
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
else:
    print("No trades found.")

# ─────────────────────────────────────────────────────────────────────────────
# CONSOLE SUMMARY TABLE
# ─────────────────────────────────────────────────────────────────────────────

data_rows = trade_rows[:-1]
STRATS    = list(STRATEGIES.keys())
COL       = 14

def summary_block(label, totals, wins, losses, pnl_key_suffix):
    print(f"\n{'─'*18}  {label}  {'─'*18}")
    print(f"{'Strategy':<18}", end="")
    for s in STRATS:
        print(f"{s:>{COL}}", end="")
    print()
    print("─" * (18 + COL * len(STRATS)))

    print(f"{'Total PnL':<18}", end="")
    for s in STRATS:
        print(f"{totals[s]:>+{COL}.1f}", end="")
    print()

    print(f"{'Win / Loss':<18}", end="")
    for s in STRATS:
        print(f"{wins[s]}W/{losses[s]}L".rjust(COL), end="")
    print()

    print(f"{'Win %':<18}", end="")
    for s in STRATS:
        n   = wins[s] + losses[s]
        pct = (wins[s] / n * 100) if n else 0
        print(f"{pct:>{COL-1}.0f}%", end="")
    print()

    print(f"{'Avg PnL/trade':<18}", end="")
    for s in STRATS:
        n   = wins[s] + losses[s]
        avg = totals[s] / n if n else 0
        print(f"{avg:>+{COL}.1f}", end="")
    print()

    print(f"{'Avg Bars Held':<18}", end="")
    for s in STRATS:
        bars = [r[f"{s}_bars"] for r in data_rows if r.get(f"{s}_bars") is not None]
        avg  = sum(bars) / len(bars) if bars else 0
        print(f"{avg:>{COL}.1f}", end="")
    print()

    print(f"{'Max Win':<18}", end="")
    for s in STRATS:
        vals = [r[f"{s}_{pnl_key_suffix}"] for r in data_rows
                if r.get(f"{s}_{pnl_key_suffix}") is not None]
        print(f"{max(vals) if vals else 0:>+{COL}.1f}", end="")
    print()

    print(f"{'Max Loss':<18}", end="")
    for s in STRATS:
        vals = [r[f"{s}_{pnl_key_suffix}"] for r in data_rows
                if r.get(f"{s}_{pnl_key_suffix}") is not None]
        print(f"{min(vals) if vals else 0:>+{COL}.1f}", end="")
    print()

summary_block("FUTURES P&L (points)", fut_totals, wins_f, losses_f, "fut_pnl")
summary_block("OPTION P&L  (points, BSM, theta-adjusted)", opt_totals, wins_o, losses_o, "opt_pnl")

print()
print("Config:")
print(f"  BB          : period={BB_PERIOD}, std={BB_STD}")
print(f"  RSI         : length={RSI_LENGTH}, smooth={RSI_SMOOTH}")
print(f"  SuperTrend  : length={ST_LENGTH}, multiplier={ST_MULTIPLIER}")
print(f"  RSI exhaust : LONG<{RSI_LONG_EXHAUST}, SHORT>{RSI_SHORT_EXHAUST}")
print(f"  Swing LB    : {SWING_LOOKBACK} bars each side")
print(f"  Weak profit : {WEAK_PROFIT_RATIO}x SL dist, min {WEAK_PROFIT_MIN}pts")
print(f"  Momentum guard: first {MOMENTUM_MIN_BARS} bars ignored")
print(f"  EOD exit    : {EOD_HOUR}:{EOD_MINUTE:02d} IST (no overnight carry)")
print(f"  Max premium : {MAX_ENTRY_PREMIUM}")
print(f"  Lot size    : {LOT_SIZE}")
print(f"  Trades found: {trade_id - 1}")

# ─────────────────────────────────────────────────────────────────────────────
# PER-TRADE DETAIL — futures vs option for st_flip and tiered
# ─────────────────────────────────────────────────────────────────────────────

print()
print(f"{'#':<4} {'Symbol':<22} {'Time':<17} {'Dir':<6} "
      f"{'Strk':>6} {'EntOpt':>7} "
      f"{'stFlipF':>8} {'stFlipO':>8} "
      f"{'tieredF':>8} {'tieredO':>8} "
      f"{'rsiExhF':>8} {'rsiExhO':>8}")
print("─" * 110)

for r in data_rows:
    sfF = r.get("st_flip_fut_pnl")
    sfO = r.get("st_flip_opt_pnl")
    tiF = r.get("tiered_fut_pnl")
    tiO = r.get("tiered_opt_pnl")
    reF = r.get("rsi_exhaust_fut_pnl")
    reO = r.get("rsi_exhaust_opt_pnl")

    def fmt_val(v):
        return f"{v:>+.1f}" if v is not None else "    —"

    print(
        f"{r['trade_id']:<4} "
        f"{r.get('symbol',''):<22} "
        f"{r['entry_time']:<17} "
        f"{r['direction']:<6} "
        f"{r.get('strike', 0):>6.0f} "
        f"{r.get('entry_opt_px', 0):>7.1f} "
        f"{fmt_val(sfF):>8} {fmt_val(sfO):>8} "
        f"{fmt_val(tiF):>8} {fmt_val(tiO):>8} "
        f"{fmt_val(reF):>8} {fmt_val(reO):>8}"
    )

print()
print("Columns: F = futures points, O = option points (BSM, theta-adjusted)")
print("         Negative option P&L on a 'winning' futures trade = theta ate the gain")