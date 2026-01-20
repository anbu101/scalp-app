from datetime import datetime, time as dt_time
from app.backtest.indicators import ema

TRADE_START = dt_time(9, 45)
TRADE_END   = dt_time(13, 30)


class IndexInsideCandleDetector:
    """
    Detects INSIDE_CANDLE pattern on NIFTY index (5m).
    """

    def __init__(self, candles):
        """
        candles: list of dicts ordered by ts asc
        keys: ts, open, high, low, close, volume
        """
        self.candles = candles

    def detect(self):
        signals = []

        for i in range(51, len(self.candles)):
            c2 = self.candles[i]     # inside candle
            c1 = self.candles[i-1]   # mother candle

            ts = datetime.fromtimestamp(c2["ts"])
            if not self._time_ok(ts):
                continue

            # same-day condition
            if datetime.fromtimestamp(c1["ts"]).date() != ts.date():
                continue

            # inside candle check
            if not (c2["high"] < c1["high"] and c2["low"] > c1["low"]):
                continue


            # EMA trend
            closes = [c["close"] for c in self.candles[i-50:i+1]]
            ema20 = ema(closes[-20:], 20)
            ema50 = ema(closes[-50:], 50)
            price = closes[-1]

            if price > ema20 > ema50:
                direction = "BULLISH"
            elif price < ema20 < ema50:
                direction = "BEARISH"
            else:
                continue

            signals.append({
                "signal_ts": c2["ts"],
                "mother_ts": c1["ts"],
                "direction": direction,
                "mother_high": c1["high"],
                "mother_low": c1["low"],
            })

        return signals

    def _time_ok(self, dt):
        t = dt.time()
        return TRADE_START <= t <= TRADE_END
