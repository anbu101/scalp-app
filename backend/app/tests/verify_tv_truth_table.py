from datetime import datetime

from app.marketdata.candle_store import CandleStore
from app.marketdata.candle import Candle
from backend.app.engine.indicator_engine_pine_v1_9 import IndicatorEngine


BASE_DIR = "~/.scalp-app/data"
INDEX = "NIFTY"
SYMBOL = "NIFTY25D1625800CE"
EXPIRY = "2025-12-16"
TIMEFRAME = "minute"


def main():
    print("\n=== TV vs PY TRUTH TABLE (FINAL — RSI FIXED) ===\n")

    store = CandleStore(BASE_DIR)

    rows = store.load(
        index=INDEX,
        expiry=EXPIRY,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
    )

    print(f"[INFO] Candles loaded: {len(rows)}\n")

    ind = IndicatorEngine()

    prev_rsi = None

    print(
        "Time | O | H | L | C | "
        "EMA8 | EMA20L | EMA20H | RSI | RSI↑ | "
        "green | >ema8 | >=ema20L | <=ema20H | high<ema20H"
    )
    print("-" * 140)

    for r in rows:
        ts: datetime = r["time"]
        start_ts = int(ts.timestamp())
        end_ts = start_ts + 60

        c = Candle(
            start_ts=start_ts,
            end_ts=end_ts,
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
            source="ZERODHA",
        )

        ind.update(c)

        if not ind.is_ready():
            continue

        snap = ind.snapshot()

        rsi = snap["rsi_raw"]
        rsi_rising = False if prev_rsi is None else rsi > prev_rsi
        prev_rsi = rsi

        green = c.close > c.open
        cond_gt_ema8 = c.close > snap["ema8"]
        cond_ge_ema20l = c.close >= snap["ema20_low"]
        cond_le_ema20h = c.close <= snap["ema20_high"]
        cond_high_lt_ema20h = c.high < snap["ema20_high"]

        print(
            f"{ts.strftime('%H:%M')} | "
            f"{c.open:.2f} | {c.high:.2f} | {c.low:.2f} | {c.close:.2f} | "
            f"{snap['ema8']:.2f} | {snap['ema20_low']:.2f} | {snap['ema20_high']:.2f} | "
            f"{rsi:.2f} | {rsi_rising} | "
            f"{green} | {cond_gt_ema8} | {cond_ge_ema20l} | "
            f"{cond_le_ema20h} | {cond_high_lt_ema20h}"
        )

    print("\n=== DEBUG COMPLETE ===\n")


if __name__ == "__main__":
    main()
