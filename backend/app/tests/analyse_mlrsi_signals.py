#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# MLRSI_V1 — PHASE A SIGNAL ENGINE (standalone analysis, NOT app-integrated)
#
# Python port of "Machine Learning RSI | AI Classification & Ranking"
# (Zeiierman, TradingView, Pine v6, CC BY-NC-SA 4.0).
# Logic reimplementation with credit to the original author. No trade model
# here — this script exists ONLY to reproduce the indicator's signal stream
# (L/S triangles + Supertrend flips) on NIFTY spot so we can diff it against
# the TradingView chart before building the Phase B option-leg backtest.
#
# WHAT IT DOES
#   1. Loads 1m NIFTY spot candles from the backtest corpus
#      (~/.scalp-app/backtest/backtest.db, table backtest_candles_1m)
#   2. Aggregates to the requested timeframe (default 30m, IST 9:15-anchored;
#      the 15:15 stub bar is kept, matching TradingView)
#   3. Runs the full engine bar-by-bar: 8 RSI features -> feature bank ->
#      Fisher auto-weights -> kNN analog vote -> adaptive Supertrend ->
#      gates -> rank/confidence -> cooldown -> signals
#   4. Writes signals + Supertrend flips to CSV and prints a per-year summary
#
# PINE-FIDELITY NOTES (read before comparing against TV)
#   * All Pine ta.* functions are replicated with Pine semantics:
#     rsi/atr use Wilder RMA (SMA-seeded), ema is SMA-seeded, stdev is
#     population stdev, percentrank counts previous `len` values <= current
#     (current bar excluded), lowest/highest include the current bar.
#     All return NaN until their window is full, like Pine `na`.
#   * ORDER OF OPERATIONS per bar matches the script exactly:
#     features -> bank row (t-4 features + outcome) -> auto-weight update ->
#     neighbor scan -> vote -> supertrend -> stance -> rank/conf -> trigger.
#     The row banked THIS bar is eligible as a neighbor THIS bar (idx 0,
#     spacing 4 includes it) — same as Pine.
#   * KNOWN DIVERGENCE (intentional): Pine banks rows whose features are still
#     na during warmup; those rows occupy memory slots and poison the Fisher
#     sums until they roll out. We skip NaN rows instead. Effect is confined
#     to roughly the first ~600 engine bars, which are inside the warmup
#     discard window anyway (see WARMUP_BARS).
#   * PATH DEPENDENCE: the bank + EMA states depend on where history starts.
#     TV computes from its own first loaded bar. Expect signal parity only
#     after WARMUP_BARS of overlapping history, and expect "near-parity"
#     (occasional borderline rank/conf differences), not bit-exactness.
#     Use --debug-from/--debug-to to inspect any mismatched bar.
#
# USAGE
#   python3 analyse_mlrsi_signals.py --list-symbols
#   python3 analyse_mlrsi_signals.py --symbol "NIFTY 50" --tf 30 \
#       --from 2024-01-01 --to 2026-07-01 --csv mlrsi_signals.csv
#   python3 analyse_mlrsi_signals.py --symbol "NIFTY 50" --tf 30 \
#       --debug-from 2025-03-10 --debug-to 2025-03-14
#
# Pure stdlib. Read-only against the DB. Safe to run any time.
# ─────────────────────────────────────────────────────────────────────────────

import argparse
import csv
import math
import sqlite3
import sys
from collections import deque
from datetime import datetime, timedelta, date
from pathlib import Path

NAN = float("nan")
IST_OFFSET = timedelta(hours=5, minutes=30)   # fixed offset, never DST

DEFAULT_DB    = Path.home() / ".scalp-app" / "backtest" / "backtest.db"
DEFAULT_TABLE = "backtest_candles_1m"

SESSION_OPEN_MIN  = 9 * 60 + 15    # 09:15 IST
SESSION_CLOSE_MIN = 15 * 60 + 30   # 15:30 IST (exclusive)

# ── ENGINE PARAMETERS (TradingView input defaults — keep in sync for parity) ─
RSI_BASE      = 14
MEMORY_DEPTH  = 500
K_NEIGHBORS   = 8
WIN_LEN       = 100
SPACING_BARS  = 4
HORIZON_BARS  = 4
STEP_LEN      = 3
ATR_FACTOR    = 0.5      # learning sensitivity (xATR)
GATE_RANK     = 60
GATE_CONF     = 50
USE_TREND_GATE = True
USE_VOL_BAND   = True
VOL_BAND_LO    = 20
VOL_BAND_HI    = 85
USE_CHOP       = True
TREND_LEN      = 50
CHOP_CUT       = 0.5
AUTO_WEIGHTS_ON = True
AUTO_SPEED      = 1.0    # TV default: weights snap fully each bar
AUTO_FLOOR      = 0.5
AUTO_MIN_ROWS   = 60
ST_MULT_BASE    = 1.5
ST_ML_RESP      = 1.0
ST_ATR_LEN      = 10
SMOOTH_LEN      = 10
COOL_BARS       = 5

# Engine bars to discard before trusting signals (bank turnover + EMA settle).
WARMUP_BARS = 700

# ─────────────────────────────────────────────────────────────────────────────
# PINE-SEMANTICS ROLLING PRIMITIVES
# ─────────────────────────────────────────────────────────────────────────────

class RMA:
    """Pine ta.rma: alpha=1/len, seeded with SMA of first len values."""
    def __init__(self, length):
        self.length = length
        self.seed = []
        self.value = NAN

    def update(self, x):
        if math.isnan(x):
            return NAN
        if math.isnan(self.value):
            self.seed.append(x)
            if len(self.seed) == self.length:
                self.value = sum(self.seed) / self.length
                self.seed = []
            return self.value if not math.isnan(self.value) else NAN
        self.value = (self.value * (self.length - 1) + x) / self.length
        return self.value


class EMA:
    """Pine ta.ema: alpha=2/(len+1), seeded with SMA of first len values."""
    def __init__(self, length):
        self.length = length
        self.alpha = 2.0 / (length + 1)
        self.seed = []
        self.value = NAN

    def update(self, x):
        if math.isnan(x):
            return NAN
        if math.isnan(self.value):
            self.seed.append(x)
            if len(self.seed) == self.length:
                self.value = sum(self.seed) / self.length
                self.seed = []
            return self.value if not math.isnan(self.value) else NAN
        self.value = self.value + self.alpha * (x - self.value)
        return self.value


class RSI:
    """Pine ta.rsi: Wilder RMA of gains/losses."""
    def __init__(self, length):
        self.up = RMA(length)
        self.dn = RMA(length)
        self.prev = NAN

    def update(self, x):
        if math.isnan(self.prev):
            self.prev = x
            return NAN
        ch = x - self.prev
        self.prev = x
        u = self.up.update(max(ch, 0.0))
        d = self.dn.update(max(-ch, 0.0))
        if math.isnan(u) or math.isnan(d):
            return NAN
        if d == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + u / d)


class ATR:
    """Pine ta.atr: RMA of true range (first bar TR = high-low)."""
    def __init__(self, length):
        self.rma = RMA(length)
        self.prev_close = NAN

    def update(self, high, low, close):
        if math.isnan(self.prev_close):
            tr = high - low
        else:
            tr = max(high - low,
                     abs(high - self.prev_close),
                     abs(low - self.prev_close))
        self.prev_close = close
        return self.rma.update(tr)


class RollingWindow:
    """Fixed-size window of the most recent values (NaN-aware)."""
    def __init__(self, length):
        self.length = length
        self.buf = deque(maxlen=length)

    def push(self, x):
        self.buf.append(x)

    def full_clean(self):
        return len(self.buf) == self.length and not any(
            math.isnan(v) for v in self.buf)

    def lowest(self):
        return min(self.buf) if self.full_clean() else NAN

    def highest(self):
        return max(self.buf) if self.full_clean() else NAN

    def stdev_pop(self):
        if not self.full_clean():
            return NAN
        m = sum(self.buf) / self.length
        return math.sqrt(sum((v - m) ** 2 for v in self.buf) / self.length)


class Scale01:
    """Pine scale01: (v - lowest(v,len)) / (highest - lowest), 0.5 if flat.
    Window includes the current value (ta.lowest/highest semantics)."""
    def __init__(self, length):
        self.win = RollingWindow(length)

    def update(self, v):
        self.win.push(v)
        lo, hi = self.win.lowest(), self.win.highest()
        if math.isnan(lo) or math.isnan(hi):
            return NAN
        if hi == lo:
            return 0.5
        return (v - lo) / (hi - lo)


class PercentRank:
    """Pine ta.percentrank: % of the previous `len` values <= current.
    Current bar excluded from the comparison set."""
    def __init__(self, length):
        self.length = length
        self.buf = deque(maxlen=length)

    def update(self, x):
        if math.isnan(x):
            # keep alignment: Pine's window still advances on na? na values
            # are not comparable; safest is to not pollute the window.
            return NAN
        if len(self.buf) < self.length:
            self.buf.append(x)
            return NAN
        cnt = sum(1 for v in self.buf if v <= x)
        out = 100.0 * cnt / self.length
        self.buf.append(x)
        return out


class Delay:
    """series[n] — value from n bars ago (NaN until available)."""
    def __init__(self, n):
        self.buf = deque(maxlen=n + 1)

    def update(self, x):
        self.buf.append(x)
        if len(self.buf) == self.buf.maxlen:
            return self.buf[0]
        return NAN


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def nz_false(x):
    """Pine conditionals treat na as false; NaN comparisons in Python already
    evaluate False, this is for explicit checks."""
    return (not math.isnan(x)) and x


# ─────────────────────────────────────────────────────────────────────────────
# FISHER AUTO-WEIGHTS (verbatim logic port)
# ─────────────────────────────────────────────────────────────────────────────

def auto_feature_weights(bank, min_rows, floor):
    """bank: list of rows [f0..f7, outcome], newest first. Returns 8 weights."""
    n = len(bank)
    imp = [1.0] * 8
    if n < min_rows:
        return imp
    sum_b = [0.0] * 8
    sum_e = [0.0] * 8
    sq_b = [0.0] * 8
    sq_e = [0.0] * 8
    cnt_b = 0
    cnt_e = 0
    for row in bank:
        o = row[8]
        if o > 0 or o < 0:
            is_b = o > 0
            for j in range(8):
                v = row[j]
                if is_b:
                    sum_b[j] += v
                    sq_b[j] += v * v
                else:
                    sum_e[j] += v
                    sq_e[j] += v * v
            if is_b:
                cnt_b += 1
            else:
                cnt_e += 1
    if cnt_b > 2 and cnt_e > 2:
        fish = [0.0] * 8
        max_f = 0.0
        for j in range(8):
            m_b = sum_b[j] / cnt_b
            m_e = sum_e[j] / cnt_e
            v_b = max(0.0, sq_b[j] / cnt_b - m_b * m_b)
            v_e = max(0.0, sq_e[j] / cnt_e - m_e * m_e)
            f = (m_b - m_e) ** 2 / (v_b + v_e + 1e-6)
            fish[j] = f
            max_f = max(max_f, f)
        for j in range(8):
            norm = fish[j] / max_f if max_f > 0 else 1.0
            imp[j] = max(floor, norm * 10.0)
    return imp


# ─────────────────────────────────────────────────────────────────────────────
# ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class MLRSIEngine:
    """Bar-by-bar stateful port. Feed confirmed OHLC bars in order."""

    def __init__(self):
        # RSI family
        self.rsi = RSI(RSI_BASE)
        self.rsi_fast = RSI(max(2, round(RSI_BASE / 2)))
        self.rsi_slow = RSI(RSI_BASE * 2)
        self.atr14 = ATR(14)
        # feature scalers (each raw series gets its own 100-bar window)
        self.sc_slope = Scale01(WIN_LEN)
        self.sc_accel = Scale01(WIN_LEN)
        self.sc_churn = Scale01(WIN_LEN)
        self.sc_spread = Scale01(WIN_LEN)
        self.sc_regime = Scale01(WIN_LEN)
        self.pr_rsi = PercentRank(WIN_LEN)
        self.win_rsi_sd = RollingWindow(14)         # stdev(rOsc,14)
        self.ema_rsi20 = EMA(20)                    # oscReg + regime raw
        self.ema_rsi5 = EMA(5)                      # oscSmoothUp
        self.prev_ema_rsi5 = NAN
        # delays
        self.d_rsi3 = Delay(STEP_LEN)               # rOsc[3]
        self.d_rsi6 = Delay(2 * STEP_LEN)           # rOsc[6]
        self.d_close4 = Delay(HORIZON_BARS)         # priceSrc[4]
        self.d_atr4 = Delay(HORIZON_BARS)           # atr[4]
        self.d_feat4 = Delay(HORIZON_BARS)          # cur features tuple, 4 ago
        # bank: list of [f0..f7, outcome], newest first
        self.bank = []
        self.w_auto = [1.0] * 8
        # context
        self.ema_trend = EMA(TREND_LEN)
        self.ema_quick = EMA(5)
        self.pr_atr = PercentRank(100)
        # supertrend
        self.st_atr = ATR(ST_ATR_LEN)
        self.st_long = NAN
        self.st_short = NAN
        self.st_long_prev = NAN                     # stLong[1] / stShort[1]
        self.st_short_prev = NAN                    # (Pine history refs)
        self.st_dir = None                          # None = na on bar 0
        self.prev_close = NAN
        self.ema_conv = EMA(SMOOTH_LEN)
        # stance / signal state
        self.stance = 0
        self.stance_age = 0
        self.changed_hist = deque([False, False, False], maxlen=3)
        self.last_entry_bar = None
        self.bar_index = -1

    # ── per-bar step; returns a dict of everything relevant ────────────────
    def step(self, o, h, l, c):
        self.bar_index += 1

        # ── RSI FEATURE ENGINE ─────────────────────────────────────────────
        r = self.rsi.update(c)
        rf = self.rsi_fast.update(c)
        rs = self.rsi_slow.update(c)
        atr = self.atr14.update(h, l, c)

        r3 = self.d_rsi3.update(r)
        r6 = self.d_rsi6.update(r)
        slope_raw = r - r3 if not (math.isnan(r) or math.isnan(r3)) else NAN
        accel_raw = (slope_raw - (r3 - r6)
                     if not (math.isnan(slope_raw) or math.isnan(r3)
                             or math.isnan(r6)) else NAN)
        self.win_rsi_sd.push(r)
        churn_raw = self.win_rsi_sd.stdev_pop()
        spread_raw = rf - rs if not (math.isnan(rf) or math.isnan(rs)) else NAN
        ereg = self.ema_rsi20.update(r)
        regime_raw = ereg - 50.0 if not math.isnan(ereg) else NAN

        f_value = r / 100.0 if not math.isnan(r) else NAN
        f_slope = self.sc_slope.update(slope_raw) if not math.isnan(slope_raw) else self._push_nan(self.sc_slope)
        f_accel = self.sc_accel.update(accel_raw) if not math.isnan(accel_raw) else self._push_nan(self.sc_accel)
        f_mid = abs(r - 50.0) / 50.0 if not math.isnan(r) else NAN
        f_pct_raw = self.pr_rsi.update(r)
        f_pct = f_pct_raw / 100.0 if not math.isnan(f_pct_raw) else NAN
        f_churn = self.sc_churn.update(churn_raw) if not math.isnan(churn_raw) else self._push_nan(self.sc_churn)
        f_spread = self.sc_spread.update(spread_raw) if not math.isnan(spread_raw) else self._push_nan(self.sc_spread)
        f_regime = self.sc_regime.update(regime_raw) if not math.isnan(regime_raw) else self._push_nan(self.sc_regime)

        cur = (f_value, f_slope, f_accel, f_mid, f_pct, f_churn, f_spread, f_regime)

        # ── FEATURE BANK (outcome labels features from HORIZON_BARS ago) ───
        c4 = self.d_close4.update(c)
        a4 = self.d_atr4.update(atr)
        past = self.d_feat4.update(cur)

        if self.bar_index > HORIZON_BARS:
            move = c - c4 if not math.isnan(c4) else NAN
            band = ATR_FACTOR * a4 if not math.isnan(a4) else NAN
            if not (math.isnan(move) or math.isnan(band)) and isinstance(past, tuple):
                if not any(math.isnan(v) for v in past):
                    if move > 2 * band:
                        outc = 3.0
                    elif move > band:
                        outc = 2.0
                    elif move > 0:
                        outc = 1.0
                    elif move < -2 * band:
                        outc = -3.0
                    elif move < -band:
                        outc = -2.0
                    elif move < 0:
                        outc = -1.0
                    else:
                        outc = 0.0
                    self.bank.insert(0, list(past) + [outc])
                    if len(self.bank) > MEMORY_DEPTH:
                        self.bank.pop()

        # ── AUTO WEIGHT OPTIMIZER ──────────────────────────────────────────
        if AUTO_WEIGHTS_ON:
            w_raw = auto_feature_weights(self.bank, AUTO_MIN_ROWS, AUTO_FLOOR)
            for j in range(8):
                self.w_auto[j] += AUTO_SPEED * (w_raw[j] - self.w_auto[j])
            wts = list(self.w_auto)
        else:
            wts = [1.0] * 8
        w_sum = sum(wts)

        # ── NEIGHBOR ENGINE (true top-K, worst-replacement, spacing 4) ─────
        nbrs = []  # list of [gap, cls]
        n_bank = len(self.bank)
        cur_ok = not any(math.isnan(v) for v in cur)
        if n_bank > 1 and cur_ok:
            scan_end = min(MEMORY_DEPTH - 1, n_bank - 1)
            for idx in range(0, scan_end + 1):
                if idx % SPACING_BARS != 0:
                    continue
                row = self.bank[idx]
                g = 0.0
                for j in range(8):
                    g += wts[j] * math.log(1.0 + abs(cur[j] - row[j]))
                cand = (g, int(row[8]))
                if len(nbrs) < K_NEIGHBORS:
                    nbrs.append(cand)
                else:
                    worst = 0
                    worst_gap = nbrs[0][0]
                    for i in range(1, len(nbrs)):
                        if nbrs[i][0] > worst_gap:
                            worst_gap = nbrs[i][0]
                            worst = i
                    if g < worst_gap:
                        nbrs[worst] = cand

        # ── DISTANCE-WEIGHTED VOTING ───────────────────────────────────────
        v_total = v_bull = v_bear = v_score = 0.0
        gap_sum = 0.0
        k_count = len(nbrs)
        for g, cls in nbrs:
            w = 1.0 / (1.0 + g)
            v_total += w
            v_score += cls * w
            if cls > 0:
                v_bull += w
            elif cls < 0:
                v_bear += w
            gap_sum += g

        analog_score = v_score / v_total if v_total > 0 else 0.0
        bias = 1 if analog_score > 0.15 else (-1 if analog_score < -0.15 else 0)
        agree = ((v_bull if bias == 1 else v_bear if bias == -1 else 0.0)
                 / v_total) if v_total > 0 else 0.0
        avg_gap = gap_sum / k_count if k_count > 0 else 0.0
        gap_scale = w_sum * 0.45 + 1e-9
        gap_tight = clamp(1.0 - avg_gap / gap_scale, 0.0, 1.0)

        # ── CONTEXT / REGIME ───────────────────────────────────────────────
        ema_t = self.ema_trend.update(c)
        ema_q = self.ema_quick.update(c)
        atr_pct = self.pr_atr.update(atr)
        trend_force = (abs(ema_q - ema_t) / atr
                       if not math.isnan(ema_q) and not math.isnan(ema_t)
                       and not math.isnan(atr) and atr > 0 else 0.0)
        chop_raw = trend_force < CHOP_CUT
        chop_now = chop_raw if USE_CHOP else False
        slope_up = nz_false(r > r3) if not (math.isnan(r) or math.isnan(r3)) else False
        osc_reg = ereg
        e5 = self.ema_rsi5.update(r)
        osc_smooth_up = (not math.isnan(e5) and not math.isnan(self.prev_ema_rsi5)
                         and e5 > self.prev_ema_rsi5)
        self.prev_ema_rsi5 = e5

        # ── ML ADAPTIVE SUPERTREND ─────────────────────────────────────────
        conv_inst = clamp(analog_score / 1.5, -1.0, 1.0)
        conv_sm = self.ema_conv.update(conv_inst)
        conv_sm_v = conv_sm if not math.isnan(conv_sm) else 0.0
        ml_drive = clamp(abs(conv_sm_v) * 0.5 + gap_tight * 0.3 + agree * 0.2,
                         0.0, 1.0)
        if chop_now:
            ml_drive *= 0.35
        adapt_mult = ST_MULT_BASE * (1.0 + ST_ML_RESP * (1.0 - ml_drive))
        st_atr = self.st_atr.update(h, l, c)
        src = (h + l) / 2.0  # hl2
        st_flip_up = st_flip_dn = False
        if not math.isnan(st_atr):
            up_band = src - adapt_mult * st_atr
            dn_band = src + adapt_mult * st_atr
            if math.isnan(self.st_long):
                self.st_long = up_band
            else:
                self.st_long = (max(up_band, self.st_long)
                                if self.prev_close > self.st_long else up_band)
            if math.isnan(self.st_short):
                self.st_short = dn_band
            else:
                self.st_short = (min(dn_band, self.st_short)
                                 if self.prev_close < self.st_short else dn_band)
            prev_dir = self.st_dir
            if prev_dir is None:
                self.st_dir = 1
            elif prev_dir == -1 and c > self.st_short_prev:
                self.st_dir = 1
            elif prev_dir == 1 and c < self.st_long_prev:
                self.st_dir = -1
            else:
                self.st_dir = prev_dir
            st_flip_up = prev_dir == -1 and self.st_dir == 1
            st_flip_dn = prev_dir == 1 and self.st_dir == -1
        # store prev-bar band references for next bar's direction test
        self.st_long_prev = self.st_long
        self.st_short_prev = self.st_short
        self.prev_close = c

        up_trend = self.st_dir == 1
        dn_trend = self.st_dir == -1
        vol_healthy = (not math.isnan(atr_pct)
                       and VOL_BAND_LO <= atr_pct <= VOL_BAND_HI)
        aligned = (bias == 1 and up_trend) or (bias == -1 and dn_trend)
        slope_fit = (bias == 1 and slope_up) or (bias == -1 and not slope_up)
        stretched = ((bias == 1 and nz_false(r > 70))
                     or (bias == -1 and nz_false(r < 30)))

        # ── SIGNAL STATE / PERSISTENCE ─────────────────────────────────────
        gates_pass = ((not USE_TREND_GATE or aligned)
                      and (not USE_VOL_BAND or vol_healthy)
                      and not chop_now)
        prev_stance = self.stance
        if bias == 1 and gates_pass:
            self.stance = 1
        elif bias == -1 and gates_pass:
            self.stance = -1
        # else hold previous
        changed = self.stance != prev_stance
        self.stance_age = 0 if changed else self.stance_age + 1
        early_flip = changed and any(self.changed_hist)
        self.changed_hist.append(changed)

        # ── RANK / CONFIDENCE ──────────────────────────────────────────────
        rank = self._rank(bias, agree, gap_tight, slope_fit, stretched,
                          aligned, vol_healthy, atr_pct, osc_reg,
                          osc_smooth_up, chop_raw, early_flip, k_count,
                          self.stance_age)
        conf = self._conf(bias, agree, gap_tight, slope_fit, early_flip,
                          k_count, self.stance_age)

        # ── SIGNALS (close-only; all backtest bars are confirmed) ──────────
        flip_long = self.stance == 1 and prev_stance != 1
        flip_short = self.stance == -1 and prev_stance != -1
        qualifies = rank >= GATE_RANK and conf >= GATE_CONF
        cool_ok = (self.last_entry_bar is None
                   or self.bar_index - self.last_entry_bar >= COOL_BARS)
        trig_long = flip_long and qualifies and cool_ok
        trig_short = flip_short and qualifies and cool_ok
        if trig_long or trig_short:
            self.last_entry_bar = self.bar_index

        return {
            "bias": bias, "rank": rank, "conf": conf,
            "stance": self.stance, "stance_age": self.stance_age,
            "st_dir": self.st_dir if self.st_dir is not None else 0,
            "st_line": self.st_long if up_trend else self.st_short,
            "st_flip_up": st_flip_up, "st_flip_dn": st_flip_dn,
            "trig_long": trig_long, "trig_short": trig_short,
            "atr_pct": atr_pct, "chop": chop_now, "vol_ok": vol_healthy,
            "agree": agree, "gap_tight": gap_tight, "k": k_count,
            "analog_score": analog_score, "rsi": r,
        }

    @staticmethod
    def _push_nan(scaler):
        scaler.win.push(NAN)
        return NAN

    @staticmethod
    def _rank(bias, agree, tight, slope_fit, stretched, aligned, vol_ok,
              atr_pct, osc_reg, osc_smooth_up, chop_raw, flip, k, age):
        if bias == 0:
            return 0.0
        p_agree = 25.0 * agree
        p_gap = 15.0 * tight
        p_struct = (10.0 if slope_fit else 0.0) + (0.0 if stretched else 5.0)
        p_trend = 10.0 if aligned else 0.0
        if vol_ok:
            p_vol = 10.0
        elif not math.isnan(atr_pct) and atr_pct < VOL_BAND_LO:
            p_vol = 5.0
        else:
            p_vol = 3.0
        reg_fit = ((bias == 1 and nz_false(osc_reg > 55))
                   or (bias == -1 and nz_false(osc_reg < 45)))
        if reg_fit:
            p_reg = 10.0
        elif not math.isnan(osc_reg) and 45 <= osc_reg <= 55:
            p_reg = 4.0
        else:
            p_reg = 6.0
        p_smooth = 5.0 if ((bias == 1 and osc_smooth_up)
                           or (bias == -1 and not osc_smooth_up)) else 0.0
        p_hold = min(5.0, float(age))
        p_pen = min(20.0, (8.0 if chop_raw else 0.0)
                    + (6.0 if stretched else 0.0)
                    + (6.0 if flip else 0.0)
                    + (5.0 * (K_NEIGHBORS - k) / K_NEIGHBORS
                       if k < K_NEIGHBORS else 0.0))
        raw = (p_agree + p_gap + p_struct + p_trend + p_vol + p_reg
               + p_smooth + p_hold - p_pen)
        return clamp(raw, 0.0, 100.0)

    @staticmethod
    def _conf(bias, agree, tight, slope_fit, flip, k, age):
        if bias == 0:
            return 0.0
        raw = (40.0 * agree + 25.0 * tight + 15.0 * min(1.0, age / 5.0)
               + 10.0 * (1.0 if slope_fit else 0.0)
               - (15.0 if flip else 0.0)
               - (10.0 * (K_NEIGHBORS - k) / K_NEIGHBORS
                  if k < K_NEIGHBORS else 0.0))
        return clamp(raw, 0.0, 100.0)


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOAD & AGGREGATION
# ─────────────────────────────────────────────────────────────────────────────

def ist(ts):
    return datetime(1970, 1, 1) + timedelta(seconds=ts) + IST_OFFSET


def detect_columns(conn, table):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return cols


SYMBOL_COL_CANDIDATES = ("symbol", "tradingsymbol")


def pick_symbol_col(cols):
    for c in SYMBOL_COL_CANDIDATES:
        if c in cols:
            return c
    return None


def load_1m(db_path, table, symbol, itype=None):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cols = detect_columns(conn, table)
    if not cols:
        sys.exit(f"Table '{table}' not found in {db_path}")
    sym_col = pick_symbol_col(cols)
    need = {"ts", "open", "high", "low", "close"}
    if not need.issubset(set(cols)):
        sys.exit(f"Table '{table}' columns {cols} missing one of {sorted(need)}")
    where = []
    params = []
    if sym_col and symbol:
        where.append(f"{sym_col} = ?")
        params.append(symbol)
    if itype and "instrument_type" in cols:
        where.append("instrument_type = ?")
        params.append(itype)
    wsql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"SELECT ts, open, high, low, close FROM {table} {wsql} "
        f"ORDER BY ts ASC", params).fetchall()
    conn.close()
    return rows


def list_symbols(db_path, table):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    print("Tables in DB:")
    for (t,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "ORDER BY name"):
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            n = "?"
        print(f"  {t}  ({n} rows)")
    print()

    cols = detect_columns(conn, table)
    if not cols:
        print(f"Target table '{table}' not found.")
        conn.close()
        return
    sym_col = pick_symbol_col(cols)
    print(f"Target table '{table}' columns: {cols}")
    print()

    if "instrument_type" in cols:
        print("instrument_type breakdown:")
        for it, n in conn.execute(
                f"SELECT instrument_type, COUNT(*) FROM {table} "
                f"GROUP BY instrument_type"):
            print(f"  {it!r}: {n} rows")
        print()
        print(f"Candidate SPOT/INDEX rows (instrument_type not CE/PE), "
              f"distinct {sym_col}:")
        q = (f"SELECT {sym_col}, instrument_type, COUNT(*), "
             f"MIN(ts), MAX(ts) FROM {table} "
             f"WHERE instrument_type IS NULL "
             f"OR instrument_type NOT IN ('CE','PE') "
             f"GROUP BY {sym_col}, instrument_type LIMIT 50")
        found = False
        for s, it, n, lo, hi in conn.execute(q):
            found = True
            print(f"  {s!r} type={it!r} rows={n} "
                  f"{ist(lo):%Y-%m-%d} -> {ist(hi):%Y-%m-%d}")
        if not found:
            print("  (none — spot is likely in a different table, "
                  "see table list above)")
    elif sym_col:
        for s, n in conn.execute(
                f"SELECT {sym_col}, COUNT(*) FROM {table} "
                f"GROUP BY {sym_col} LIMIT 50"):
            print(f"  {s}  ({n} rows)")
    else:
        print("No symbol-like column found; table is single-instrument?")
    conn.close()


def aggregate(rows_1m, tf_min):
    """1m -> tf bars, IST 9:15-anchored. Last stub bar (e.g. 15:15 on 30m)
    is kept, matching TradingView. Returns list of dicts with ts = bucket
    start (epoch seconds, UTC-based like the source)."""
    out = []
    cur_key = None
    bar = None
    for ts, o, h, l, c in rows_1m:
        dt = ist(ts)
        mod = dt.hour * 60 + dt.minute
        if mod < SESSION_OPEN_MIN or mod >= SESSION_CLOSE_MIN:
            continue
        bucket = (mod - SESSION_OPEN_MIN) // tf_min
        key = (dt.date(), bucket)
        if key != cur_key:
            if bar is not None:
                out.append(bar)
            start_mod = SESSION_OPEN_MIN + bucket * tf_min
            start_dt = datetime(dt.year, dt.month, dt.day,
                                start_mod // 60, start_mod % 60)
            start_ts = int((start_dt - IST_OFFSET
                            - datetime(1970, 1, 1)).total_seconds())
            bar = {"ts": start_ts, "open": o, "high": h, "low": l, "close": c}
            cur_key = key
        else:
            bar["high"] = max(bar["high"], h)
            bar["low"] = min(bar["low"], l)
            bar["close"] = c
    if bar is not None:
        out.append(bar)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def _close_trade(pos, bar, dt, reason, side):
    """side +1 long / -1 short. Excursions in % of entry."""
    entry = pos["entry"]
    exitp = bar["close"]
    pct = side * (exitp - entry) / entry * 100.0
    mae_pct = side * (pos["mae"] - entry) / entry * 100.0
    mfe_pct = side * (pos["mfe"] - entry) / entry * 100.0
    return {
        "entry_dt": pos["entry_dt"].strftime("%Y-%m-%d %H:%M"),
        "exit_dt": dt.strftime("%Y-%m-%d %H:%M"),
        "entry": round(entry, 2), "exit": round(exitp, 2),
        "pct": round(pct, 3),
        "mae_pct": round(mae_pct, 3), "mfe_pct": round(mfe_pct, 3),
        "bars": pos["bars"], "reason": reason,
        "year": pos["entry_dt"].year,
    }


def _pctile(vals, p):
    if not vals:
        return NAN
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def _edge_report(title, trades):
    print(f"── EDGE: {title} " + "─" * max(1, 58 - len(title)))
    if not trades:
        print("  (no trades)")
        return
    header = (f"  {'year':<6}{'n':>4}{'win%':>7}{'avg%':>8}{'med%':>8}"
              f"{'maeP50%':>9}{'maeP90%':>9}{'maeMax%':>9}{'avgBars':>9}")
    print(header)
    years = sorted({t['year'] for t in trades})
    for y in years + ["ALL"]:
        sub = trades if y == "ALL" else [t for t in trades if t["year"] == y]
        n = len(sub)
        wins = sum(1 for t in sub if t["pct"] > 0)
        pcts = [t["pct"] for t in sub]
        maes = [t["mae_pct"] for t in sub]
        print(f"  {str(y):<6}{n:>4}{100.0*wins/n:>7.1f}"
              f"{sum(pcts)/n:>8.3f}{_pctile(pcts,50):>8.3f}"
              f"{_pctile(maes,50):>9.3f}{_pctile(maes,10):>9.3f}"
              f"{min(maes):>9.3f}{sum(t['bars'] for t in sub)/n:>9.1f}")
    de = [t for t in trades if t["reason"] == "DATA_END"]
    if de:
        print(f"  NOTE: {len(de)} trade(s) force-closed at data end")


def main():
    ap = argparse.ArgumentParser(description="MLRSI Phase A signal engine")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--table", default=DEFAULT_TABLE)
    ap.add_argument("--symbol", default=None,
                    help="symbol filter (use --list-symbols first)")
    ap.add_argument("--itype", default=None,
                    help="instrument_type filter, e.g. INDEX/EQ (optional)")
    ap.add_argument("--list-symbols", action="store_true")
    ap.add_argument("--tf", type=int, default=30, help="timeframe minutes")
    ap.add_argument("--from", dest="date_from", default=None,
                    help="report signals from this date (YYYY-MM-DD)")
    ap.add_argument("--to", dest="date_to", default=None,
                    help="report signals up to this date (YYYY-MM-DD)")
    ap.add_argument("--csv", default="mlrsi_signals.csv")
    ap.add_argument("--debug-from", default=None,
                    help="dump per-bar engine state from date")
    ap.add_argument("--debug-to", default=None)
    ap.add_argument("--bars-csv", default=None,
                    help="also dump aggregated OHLC bars for the debug "
                         "window to this CSV (for TV feed comparison)")
    ap.add_argument("--edge", action="store_true",
                    help="run spot-level edge simulation: LONG at signal "
                         "close, exit on ST flip-down or short signal (D2/"
                         "D4). Shorts simulated mirror-wise for info.")
    ap.add_argument("--start-offset", type=int, default=0,
                    help="skip N leading 30m bars before engine start "
                         "(robustness check for path dependence)")
    args = ap.parse_args()

    if args.list_symbols:
        list_symbols(args.db, args.table)
        return

    rows = load_1m(args.db, args.table, args.symbol, args.itype)
    if not rows:
        sys.exit("No candles loaded — check --symbol / --table "
                 "(try --list-symbols).")

    bars = aggregate(rows, args.tf)
    if args.start_offset > 0:
        bars = bars[args.start_offset:]
        print(f"Start offset: skipped first {args.start_offset} bars "
              f"(robustness run)")
    print(f"Loaded    : {len(rows)} x 1m -> {len(bars)} x {args.tf}m bars")
    print(f"Date range: {ist(bars[0]['ts']):%Y-%m-%d} -> "
          f"{ist(bars[-1]['ts']):%Y-%m-%d}")
    print(f"Warmup    : first {WARMUP_BARS} bars discarded from report "
          f"(bank turnover + EMA settle)")
    print()

    d_from = parse_date(args.date_from) if args.date_from else None
    d_to = parse_date(args.date_to) if args.date_to else None
    dbg_from = parse_date(args.debug_from) if args.debug_from else None
    dbg_to = parse_date(args.debug_to) if args.debug_to else None

    eng = MLRSIEngine()
    events = []
    by_year = {}
    dbg_bars = []
    # ── edge simulation state (only used with --edge) ──────────────────────
    long_pos = None
    short_pos = None
    long_trades = []
    short_trades = []

    for i, b in enumerate(bars):
        st = eng.step(b["open"], b["high"], b["low"], b["close"])
        dt = ist(b["ts"])
        d = dt.date()

        if dbg_from and dbg_from <= d <= (dbg_to or dbg_from):
            print(f"[DBG] {dt:%Y-%m-%d %H:%M} "
                  f"O={b['open']:.2f} H={b['high']:.2f} "
                  f"L={b['low']:.2f} C={b['close']:.2f} | "
                  f"bias={st['bias']:+d} rank={st['rank']:5.1f} "
                  f"conf={st['conf']:5.1f} stance={st['stance']:+d} "
                  f"age={st['stance_age']:3d} stDir={st['st_dir']:+d} "
                  f"k={st['k']} score={st['analog_score']:+.3f} "
                  f"agree={st['agree']:.2f} tight={st['gap_tight']:.2f} "
                  f"chop={int(st['chop'])} volOK={int(st['vol_ok'])} "
                  f"atrPct={st['atr_pct']:.0f}"
                  if not math.isnan(st['atr_pct']) else
                  f"[DBG] {dt:%Y-%m-%d %H:%M} warmup")
            dbg_bars.append({
                "dt_ist": dt.strftime("%Y-%m-%d %H:%M"),
                "open": round(b["open"], 2), "high": round(b["high"], 2),
                "low": round(b["low"], 2), "close": round(b["close"], 2),
            })

        # ── EDGE SIMULATION (spot-level, D2/D4 exit model) ─────────────────
        if args.edge and i >= WARMUP_BARS:
            in_window = ((not d_from or d >= d_from)
                         and (not d_to or d <= d_to))
            # update open positions with this bar's excursion, then exits
            if long_pos is not None:
                long_pos["mae"] = min(long_pos["mae"], b["low"])
                long_pos["mfe"] = max(long_pos["mfe"], b["high"])
                long_pos["bars"] += 1
                if st["st_flip_dn"] or st["trig_short"]:
                    reason = "ST_FLIP_DN" if st["st_flip_dn"] else "SHORT_SIG"
                    long_trades.append(_close_trade(long_pos, b, dt, reason, +1))
                    long_pos = None
            if short_pos is not None:
                short_pos["mae"] = max(short_pos["mae"], b["high"])
                short_pos["mfe"] = min(short_pos["mfe"], b["low"])
                short_pos["bars"] += 1
                if st["st_flip_up"] or st["trig_long"]:
                    reason = "ST_FLIP_UP" if st["st_flip_up"] else "LONG_SIG"
                    short_trades.append(_close_trade(short_pos, b, dt, reason, -1))
                    short_pos = None
            # entries at signal-bar close
            if long_pos is None and st["trig_long"] and in_window:
                long_pos = {"entry_dt": dt, "entry": b["close"],
                            "mae": b["close"], "mfe": b["close"], "bars": 0}
            if short_pos is None and st["trig_short"] and in_window:
                short_pos = {"entry_dt": dt, "entry": b["close"],
                             "mae": b["close"], "mfe": b["close"], "bars": 0}

        if i < WARMUP_BARS:
            continue
        if d_from and d < d_from:
            continue
        if d_to and d > d_to:
            continue

        evs = []
        if st["trig_long"]:
            evs.append("SIGNAL_LONG")
        if st["trig_short"]:
            evs.append("SIGNAL_SHORT")
        if st["st_flip_up"]:
            evs.append("ST_FLIP_UP")
        if st["st_flip_dn"]:
            evs.append("ST_FLIP_DN")
        for e in evs:
            events.append({
                "dt_ist": dt.strftime("%Y-%m-%d %H:%M"),
                "event": e,
                "close": round(b["close"], 2),
                "rank": round(st["rank"], 1),
                "conf": round(st["conf"], 1),
                "st_line": round(st["st_line"], 2)
                if not math.isnan(st["st_line"]) else "",
                "rsi": round(st["rsi"], 2) if not math.isnan(st["rsi"]) else "",
            })
            if e.startswith("SIGNAL"):
                y = d.year
                by_year.setdefault(y, {"L": 0, "S": 0})
                by_year[y]["L" if e == "SIGNAL_LONG" else "S"] += 1

    with open(args.csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["dt_ist", "event", "close", "rank",
                                          "conf", "st_line", "rsi"])
        w.writeheader()
        w.writerows(events)

    if args.bars_csv and dbg_bars:
        with open(args.bars_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["dt_ist", "open", "high",
                                              "low", "close"])
            w.writeheader()
            w.writerows(dbg_bars)
        print(f"Debug-window bars written: {len(dbg_bars)} -> "
              f"{args.bars_csv}")

    # ── EDGE REPORT ─────────────────────────────────────────────────────────
    if args.edge:
        # force-close anything still open at data end (flagged)
        last = bars[-1]
        last_dt = ist(last["ts"])
        if long_pos is not None:
            long_trades.append(_close_trade(long_pos, last, last_dt,
                                            "DATA_END", +1))
        if short_pos is not None:
            short_trades.append(_close_trade(short_pos, last, last_dt,
                                             "DATA_END", -1))
        _edge_report("LONG  (sell PE candidate)", long_trades)
        _edge_report("SHORT (informational)", short_trades)
        stem = args.csv.rsplit(".", 1)[0]
        for name, trades in ((f"{stem}_trades_long.csv", long_trades),
                             (f"{stem}_trades_short.csv", short_trades)):
            if trades:
                with open(name, "w", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
                    w.writeheader()
                    w.writerows(trades)
                print(f"Trades written: {len(trades)} -> {name}")
        print()

    n_sig = sum(v["L"] + v["S"] for v in by_year.values())
    print(f"Events written: {len(events)} -> {args.csv}")
    print(f"Entry signals : {n_sig}")
    for y in sorted(by_year):
        v = by_year[y]
        print(f"  {y}: LONG={v['L']:3d}  SHORT={v['S']:3d}")
    print()
    print("PARITY CHECK: load the indicator on TradingView (NIFTY, same TF, "
          "all default inputs), pick a window well inside both histories, "
          "and diff triangle timestamps against SIGNAL_* rows. Use "
          "--debug-from/--debug-to on any mismatch to see rank/conf/gates.")


if __name__ == "__main__":
    main()