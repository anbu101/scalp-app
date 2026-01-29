# engine/strategy_v2.py
# Minimal Python translation of your Pine entry logic (V1.9) for backend automation.
# Exposes:
#   - StrategyV2().evaluate(candles, config) -> dict (may include "buy": True, "buyPrice": float)
#   - StrategyV2().find_candidate_sl(candles, buy_price, config) -> float (SL price)
#   - StrategyV2().compute_tp(buy_price, sl_price, config) -> float (TP price)

from typing import List, Dict, Any, Optional
import math

class StrategyV2:
    def __init__(self):
        # defaults if not supplied in config
        self.defaults = {
            "rsi_len": 5,
            "rsi_smooth_len": 5,
            "ema8_len": 8,
            "ema20_len": 20,
            "ema8_smoothing_len": 8,  # for SMA-smoothed path if ever used
            "rrMultiplier": 1.0,
            "minSLpts": 5.0,
            "slSearchDepth": 100,
            "excludeCurrentBarForSL": True,
            "manualTPpts": 0.0
        }

    # -------------------------
    # --- Helpers for series ---
    # -------------------------
    def _get_series(self, candles: List[Dict[str, Any]], key: str) -> List[float]:
        return [float(c.get(key, 0.0)) for c in candles]

    def _ema(self, series: List[float], period: int) -> List[float]:
        # simple EMA implementation (alpha smoothing)
        if not series or period <= 0:
            return []
        ema = []
        k = 2.0 / (period + 1.0)
        for i, v in enumerate(series):
            if i == 0:
                ema.append(v)
            else:
                ema.append((v - ema[-1]) * k + ema[-1])
        return ema

    def _sma(self, series: List[float], period: int) -> List[float]:
        if not series or period <= 0:
            return []
        sma = []
        window = []
        s = 0.0
        for v in series:
            window.append(v)
            s += v
            if len(window) > period:
                s -= window.pop(0)
            if len(window) < period:
                sma.append(s / len(window))  # warm-up: average of available
            else:
                sma.append(s / period)
        return sma

    def _rsi(self, closes: List[float], length: int = 5) -> List[float]:
        # Wilder-style RSI approximation using SMA of gains/losses
        if not closes or length <= 0:
            return []
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        # compute average gain/loss with simple SMA for first value, then Wilder smoothing approx
        rsi = [50.0]  # align length: we'll pad to original length with a neutral value first
        if len(gains) == 0:
            return [50.0] * len(closes)
        avg_gain = sum(gains[:length]) / max(length, 1)
        avg_loss = sum(losses[:length]) / max(length, 1)
        # first RSI corresponds to position length (we'll pad in front)
        first_rsi = 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss) if avg_loss != 0 else 1e9))
        # we will build RSI aligned to closes length
        rsi_vals = [None] * (length)  # no RSI for first few bars
        rsi_vals.append(first_rsi)
        ag = avg_gain
        al = avg_loss
        for i in range(length, len(gains)):
            ag = (ag * (length - 1) + gains[i]) / length
            al = (al * (length - 1) + losses[i]) / length
            rs = ag / al if al != 0 else 1e9
            rsi_vals.append(100.0 - (100.0 / (1.0 + rs)))
        # Ensure final list length = len(closes)
        # If still short, pad with last value
        while len(rsi_vals) < len(closes):
            rsi_vals.insert(0, rsi_vals[0] if rsi_vals else 50.0)
        # Trim / ensure equal size
        if len(rsi_vals) > len(closes):
            rsi_vals = rsi_vals[-len(closes):]
        return rsi_vals

    # -------------------------
    # --- Public interface ----
    # -------------------------
    def evaluate(self, candles: List[Dict[str, Any]], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Evaluate recent candles and return potential buy signal.
        Returns dict: {"buy": True/False, "buyPrice": float (close), "debug": {...}}
        """
        cfg = dict(self.defaults)
        if config:
            cfg.update(config)

        # minimal safety
        if not candles or len(candles) < 5:
            return {"buy": False, "reason": "insufficient_candles"}

        closes = self._get_series(candles, "close")
        highs = self._get_series(candles, "high")
        lows = self._get_series(candles, "low")
        opens = self._get_series(candles, "open")

        # compute EMAs on close/open/high/low as needed
        ema8_close = self._ema(closes, cfg["ema8_len"])
        ema20_low = self._ema(lows, cfg["ema20_len"])
        ema20_high = self._ema(highs, cfg["ema20_len"])

        # align: take last available values
        last = len(candles) - 1
        try:
            ema8_val = ema8_close[last]
        except:
            ema8_val = ema8_close[-1] if ema8_close else closes[-1]
        try:
            ema20_low_val = ema20_low[last]
        except:
            ema20_low_val = ema20_low[-1] if ema20_low else lows[-1]
        try:
            ema20_high_val = ema20_high[last]
        except:
            ema20_high_val = ema20_high[-1] if ema20_high else highs[-1]

        # Last candle values
        c_open = opens[last]
        c_close = closes[last]
        c_high = highs[last]
        c_low = lows[last]

        # Conditions (V1.9)
        cond_close_gt_open = c_close > c_open
        cond_close_gt_ema8 = c_close > ema8_val
        cond_close_ge_ema20 = c_close >= ema20_low_val
        cond_close_not_above_ema20 = c_close <= ema20_high_val

        # notTouchingHigh: when ema8 < ema20_high, body or wick must be strictly < ema20_high
        if ema8_val < ema20_high_val:
            not_touching_high = (c_high < ema20_high_val) and (max(c_open, c_close) < ema20_high_val)
        else:
            not_touching_high = True

        # RSI checks
        rsi_series = self._rsi(closes, cfg["rsi_len"])
        # choose latest RSI (aligned to closes)
        rsi_last = rsi_series[-1] if rsi_series else 50.0
        rsi_prev = rsi_series[-2] if len(rsi_series) >= 2 else rsi_last
        cond_rsi_range = (rsi_last >= 40.0 and rsi_last <= 65.0)
        cond_rsi_rising = rsi_last > rsi_prev

        # trading session / noOpenTrade checks are left to caller (main loop) — here only pure entry conditions
        ok = all([
            cond_close_gt_open,
            cond_close_gt_ema8,
            cond_close_ge_ema20,
            cond_close_not_above_ema20,
            not_touching_high,
            cond_rsi_range,
            cond_rsi_rising
        ])

        debug = {
            "c_close": c_close,
            "c_open": c_open,
            "ema8": ema8_val,
            "ema20_low": ema20_low_val,
            "ema20_high": ema20_high_val,
            "rsi_last": rsi_last,
            "rsi_prev": rsi_prev,
            "conds": {
                "close_gt_open": cond_close_gt_open,
                "close_gt_ema8": cond_close_gt_ema8,
                "close_ge_ema20": cond_close_ge_ema20,
                "close_not_above_ema20": cond_close_not_above_ema20,
                "not_touching_high": not_touching_high,
                "rsi_range": cond_rsi_range,
                "rsi_rising": cond_rsi_rising
            }
        }

        if ok:
            # buyPrice is close price of last completed candle per your rule
            return {"buy": True, "buyPrice": float(c_close), "debug": debug}
        else:
            return {"buy": False, "debug": debug}

    def find_candidate_sl(self, candles: List[Dict[str, Any]], buy_price: float, config: Optional[Dict[str, Any]] = None) -> float:
        """
        Find candidate SL based on most recent red candle low (within slSearchDepth),
        fallback to lowest low in the window, then final fallback to previous or current low.
        """
        cfg = dict(self.defaults)
        if config:
            cfg.update(config)

        depth = int(cfg.get("slSearchDepth", 100))
        exclude_current = bool(cfg.get("excludeCurrentBarForSL", True))

        start_idx = 1 if exclude_current else 0
        candidate = None

        # 1) prefer most recent red candle with low < buy_price
        for i in range(start_idx, min(len(candles), depth + 1)):
            idx = i
            # index from end: last = -1, previous -2 etc -> map i to offset from end
            off = -(i+1)
            if abs(off) <= len(candles):
                c = candles[off]
                if c and float(c.get("close", 0)) < float(c.get("open", 0)) and float(c.get("low", 1e9)) < buy_price:
                    candidate = float(c.get("low"))
                    break

        # 2) fallback to lowest low in the window if not found
        if candidate is None:
            lowest = None
            for i in range(start_idx, min(len(candles), depth + 1)):
                off = -(i+1)
                c = candles[off]
                if c:
                    lowv = float(c.get("low", 1e9))
                    if lowest is None or lowv < lowest:
                        lowest = lowv
            if lowest is not None and lowest < buy_price:
                candidate = lowest

        # 3) final fallback: use start_idx bar low (previous bar low if start_idx==1)
        if candidate is None:
            off = -(start_idx + 1)
            if abs(off) <= len(candles):
                candidate = float(candles[off].get("low", candles[-1].get("low")))
            else:
                candidate = float(candles[-1].get("low"))

        # Safety: ensure numeric and below buy_price
        candidate = float(candidate)
        if candidate >= buy_price:
            # If not below entry, move a tiny margin below buy_price
            candidate = buy_price - max(0.5, cfg.get("minSLpts", 5.0))

        return candidate

    def compute_tp(self, buy_price: float, sl_price: float, config: Optional[Dict[str, Any]] = None) -> float:
        """
        Compute TP: if manualTPpts > 0 use that; otherwise use RR multiplier.
        """
        cfg = dict(self.defaults)
        if config:
            cfg.update(config)

        manual = float(cfg.get("manualTPpts", 0.0) or 0.0)
        rr = float(cfg.get("rrMultiplier", 1.0) or 1.0)
        minSL = float(cfg.get("minSLpts", 5.0) or 5.0)

        sl_distance = buy_price - sl_price
        if sl_distance <= 0:
            sl_distance = max(minSL, 1.0)

        if manual > 0:
            tp = buy_price + manual
        else:
            tp = buy_price + sl_distance * rr

        return float(tp)
