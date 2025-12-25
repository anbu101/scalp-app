from datetime import datetime
from typing import Optional, List

# =========================
# Candle model
# =========================

class Candle:
    def __init__(self, ts, o, h, l, c):
        self.ts = ts
        self.open = o
        self.high = h
        self.low = l
        self.close = c


# =========================
# Indicator Engine (PINE PARITY)
# =========================

class IndicatorEngine:
    EMA8_LEN = 8
    EMA20_LEN = 20
    RSI_LEN = 5

    def __init__(self):
        self.ema8 = None
        self.ema20_low = None
        self.ema20_high = None

        self.prev_close = None
        self.avg_gain = None
        self.avg_loss = None
        self.prev_rsi_raw = None

    def _ema(self, prev, value, length):
        alpha = 2 / (length + 1)
        return value if prev is None else prev + alpha * (value - prev)

    def _rsi(self, close):
        if self.prev_close is None:
            self.prev_close = close
            return None, None

        change = close - self.prev_close
        gain = max(change, 0)
        loss = max(-change, 0)

        if self.avg_gain is None:
            self.avg_gain = gain
            self.avg_loss = loss
        else:
            self.avg_gain = (self.avg_gain * (self.RSI_LEN - 1) + gain) / self.RSI_LEN
            self.avg_loss = (self.avg_loss * (self.RSI_LEN - 1) + loss) / self.RSI_LEN

        rs = float("inf") if self.avg_loss == 0 else self.avg_gain / self.avg_loss
        rsi = 100 - (100 / (1 + rs))

        rising = self.prev_rsi_raw is not None and rsi > self.prev_rsi_raw
        self.prev_rsi_raw = rsi
        self.prev_close = close

        return rsi, rising

    def on_candle(self, c: Candle):
        # --- EMA updates (Pine timing: evaluate AFTER close) ---
        self.ema8 = self._ema(self.ema8, c.close, self.EMA8_LEN)
        self.ema20_low = self._ema(self.ema20_low, c.low, self.EMA20_LEN)
        self.ema20_high = self._ema(self.ema20_high, c.high, self.EMA20_LEN)

        rsi, rsi_rising = self._rsi(c.close)

        if None in (self.ema8, self.ema20_low, self.ema20_high, rsi):
            return None

        # =========================
        # EXACT PINE CONDITIONS
        # =========================
        cond_close_green = c.close > c.open
        cond_close_gt_ema8 = c.close > self.ema8
        cond_close_ge_ema20L = c.close >= self.ema20_low
        cond_close_le_ema20H = c.close <= self.ema20_high

        # 🔥 CRITICAL RULE
        cond_not_touch_high = c.high < self.ema20_high   # STRICT <

        buy = (
            cond_close_green
            and cond_close_gt_ema8
            and cond_close_ge_ema20L
            and cond_close_le_ema20H
            and cond_not_touch_high
            and rsi_rising
        )

        return {
            "ema8": self.ema8,
            "ema20L": self.ema20_low,
            "ema20H": self.ema20_high,
            "rsi": rsi,
            "rsi_rising": rsi_rising,
            "conds": {
                "green": cond_close_green,
                "gt_ema8": cond_close_gt_ema8,
                "ge_ema20L": cond_close_ge_ema20L,
                "le_ema20H": cond_close_le_ema20H,
                "not_touch_high": cond_not_touch_high,
            },
            "BUY": buy,
        }


# =========================
# DEBUG RUNNER
# =========================

def main():
    candles = [
        Candle("10:53", 214.35, 216.95, 209.00, 212.25),
        Candle("10:54", 212.25, 215.40, 210.00, 212.00),
        Candle("10:55", 212.00, 221.60, 212.00, 219.35),
        Candle("10:56", 219.35, 219.80, 214.30, 214.95),
    ]

    ind = IndicatorEngine()

    print("\n=== TV vs PY DEBUG (PINE PARITY) ===\n")

    for c in candles:
        snap = ind.on_candle(c)
        if not snap:
            continue

        print(f"[{c.ts}] O={c.open:.2f} H={c.high:.2f} L={c.low:.2f} C={c.close:.2f}")
        print(
            f" EMA8={snap['ema8']:.2f} EMA20L={snap['ema20L']:.2f} EMA20H={snap['ema20H']:.2f}"
        )
        print(
            f" RSI={snap['rsi']:.2f} RSI↑={snap['rsi_rising']}"
        )
        print(
            f" conds: green={snap['conds']['green']} "
            f">ema8={snap['conds']['gt_ema8']} "
            f">=ema20L={snap['conds']['ge_ema20L']} "
            f"<=ema20H={snap['conds']['le_ema20H']} "
            f"high<ema20H={snap['conds']['not_touch_high']}"
        )
        print(f" BUY={snap['BUY']}\n")

    print("=== DEBUG COMPLETE ===\n")


if __name__ == "__main__":
    main()
