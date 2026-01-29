import csv
from pathlib import Path

from app.engine.strategy_engine import StrategyEngine, Signal
from app.marketdata.candle_builder import Candle


def load_candles(csv_path):
    candles = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            candles.append(
                Candle(
                    start_ts=int(r["start_ts"]),
                    end_ts=int(r["end_ts"]),
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                )
            )
    return candles


def main():
    print("\n=== STRATEGY ENTRY TEST ===")

    csv_path = Path(
        "~/.scalp-app/candles/NIFTY/2025-12-16/"
        "NIFTY25D1625800CE/1m.csv"
    ).expanduser()

    candles = load_candles(csv_path)

    engine = StrategyEngine(timeframe_sec=60)

    buy_count = 0

    for c in candles[-200:]:  # last ~200 candles
        sig = engine.on_candle(c)
        if sig == Signal.BUY:
            buy_count += 1
            print(
                f"[BUY] {c.end_ts} "
                f"O={c.open} H={c.high} L={c.low} C={c.close}"
            )

    print(f"\nTotal BUY signals: {buy_count}")
    print("\n=== TEST COMPLETE ===\n")


if __name__ == "__main__":
    main()
