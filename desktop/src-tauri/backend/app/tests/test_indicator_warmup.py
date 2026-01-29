from pathlib import Path
from backend.app.engine.indicator_engine_pine_v1_9 import IndicatorEngine
from app.marketdata.candle_builder import Candle
import csv


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
    print("\n=== INDICATOR WARM-UP TEST ===")

    csv_path = Path(
        "~/.scalp-app/candles/NIFTY/2025-12-16/"
        "NIFTY25D1625800CE/1m.csv"
    ).expanduser()

    candles = load_candles(csv_path)

    engine = IndicatorEngine()

    for c in candles:
        engine.update(c)

    print("Indicators ready:", engine.is_ready())
    print("Snapshot:", engine.snapshot())

    print("\n=== TEST COMPLETE ===\n")


if __name__ == "__main__":
    main()
