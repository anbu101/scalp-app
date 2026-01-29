from pathlib import Path
from datetime import datetime
import csv
import inspect

from app.marketdata.candle_builder import Candle
from app.engine.indicator_engine_pine_v1_9 import IndicatorEnginePineV19

print("ENGINE FILE:", inspect.getfile(IndicatorEnginePineV19))

CSV_PATH = Path(
    "~/.scalp-app/data/NIFTY/2025-12-16/NIFTY25D1625800CE/minute.csv"
).expanduser()

WARMUP_CANDLES = 200


def load_candles(csv_path: Path):
    candles = []
    with open(csv_path, "r") as f:
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
                    source="CSV",   # ✅ REQUIRED
                )
            )
    return candles


def main():
    print("\n=== VERIFY TV BUY PARITY (PINE v1.9 — FINAL) ===\n")
    print(f"[CSV] {CSV_PATH}")

    candles = load_candles(CSV_PATH)
    print(f"[INFO] Candles loaded: {len(candles)}")
    print(f"[WARMUP] {WARMUP_CANDLES} candles\n")

    ind = IndicatorEnginePineV19()

    prev_rsi = None
    prev_signal_ok = False

    print(
        "Time | O | H | L | C | EMA8 | EMA20L | EMA20H | RSI | RSI↑ | "
        "green | >ema8 | >=ema20L | <=ema20H | notTouchEMA20H | BUY"
    )
    print("-" * 160)

    for i, c in enumerate(candles):
        ind.update(c)

        if not ind.is_ready() or i < WARMUP_CANDLES:
            continue

        snap = ind.snapshot()

        ema8 = snap["ema8"]
        ema20l = snap["ema20_low"]
        ema20h = snap["ema20_high"]
        rsi = snap["rsi_raw"]

        ts = datetime.fromtimestamp(c.start_ts).strftime("%H:%M")

        green = c.close > c.open
        rsi_up = prev_rsi is not None and rsi > prev_rsi

        cond_green = green
        cond_gt_ema8 = c.close > ema8
        cond_ge_ema20l = c.close >= ema20l
        cond_le_ema20h = c.close <= ema20h

        # Pine v1.9 critical rule
        not_touch_ema20h = not (ema8 < ema20h and c.high >= ema20h)

        signal_ok = (
            cond_green
            and cond_gt_ema8
            and cond_ge_ema20l
            and cond_le_ema20h
            and not_touch_ema20h
            and rsi_up
        )

        buy = prev_signal_ok

        print(
            f"{ts} | {c.open:.2f} | {c.high:.2f} | {c.low:.2f} | {c.close:.2f} | "
            f"{ema8:.2f} | {ema20l:.2f} | {ema20h:.2f} | "
            f"{rsi:.2f} | {rsi_up} | "
            f"{cond_green} | {cond_gt_ema8} | {cond_ge_ema20l} | "
            f"{cond_le_ema20h} | {not_touch_ema20h} | {buy}"
        )

        prev_rsi = rsi
        prev_signal_ok = signal_ok

    print("\n=== PARITY CHECK COMPLETE ===\n")


if __name__ == "__main__":
    main()
