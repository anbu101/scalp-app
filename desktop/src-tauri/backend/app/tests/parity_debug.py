from datetime import datetime
from pathlib import Path
import csv

from app.indicators.ema import EMA, SMA
from app.indicators.rsi import WilderRSI
from app.marketdata.candle import Candle

# -----------------------------
# CONFIG (LOCKED)
# -----------------------------
SYMBOL = "NIFTY25D1625800CE"
TIMEFRAME = "1m"
CSV_PATH = Path.home() / ".scalp-app" / "candles" / "NIFTY" / "2025-12-16" / SYMBOL / "1m.csv"

RSI_LEN = 5
RSI_SMOOTH = 5

# -----------------------------
# LOAD CANDLES
# -----------------------------
def load_candles(path: Path):
    candles = []
    with path.open() as f:
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
                    source="hist",
                )
            )
    return candles


# -----------------------------
# PARITY DEBUG
# -----------------------------
def main():
    print("\n=== PARITY DEBUG START ===\n")

    candles = load_candles(CSV_PATH)
    print(f"[INFO] Loaded {len(candles)} candles\n")

    ema8 = EMA(8)

    ema20_low_raw = EMA(20)
    ema20_high_raw = EMA(20)
    ema20_low = SMA(9)
    ema20_high = SMA(9)

    rsi = WilderRSI(RSI_LEN, RSI_SMOOTH)

    prev_rsi_raw = None

    for c in candles:
        ts = datetime.fromtimestamp(c.end_ts)

        e8 = ema8.update(c.close)

        elr = ema20_low_raw.update(c.low)
        ehr = ema20_high_raw.update(c.high)

        el = ema20_low.update(elr) if elr is not None else None
        eh = ema20_high.update(ehr) if ehr is not None else None

        rsi_raw, rsi_sm = rsi.update(c.close)

        if None in (e8, el, eh, rsi_raw):
            prev_rsi_raw = rsi_raw
            continue

        # -----------------------------
        # CONDITIONS (MATCH PINE)
        # -----------------------------
        cond_green = c.close > c.open
        cond_ema8 = c.close > e8
        cond_ema20_low = c.close >= el
        cond_ema20_high = c.close <= eh

        if e8 < eh:
            cond_no_touch = (c.high < eh) and (max(c.open, c.close) < eh)
        else:
            cond_no_touch = True

        cond_rsi_range = 40 <= rsi_raw <= 65
        cond_rsi_rising = (
            prev_rsi_raw is not None and rsi_raw > prev_rsi_raw
        )

        buy = (
            cond_green
            and cond_ema8
            and cond_ema20_low
            and cond_ema20_high
            and cond_no_touch
            and cond_rsi_range
            and cond_rsi_rising
        )

        # -----------------------------
        # PRINT DEBUG LINE
        # -----------------------------
        print(
            f"{ts} | C={c.close:.2f} "
            f"| EMA8={e8:.2f} "
            f"| EL={el:.2f} EH={eh:.2f} "
            f"| RSI={rsi_raw:.2f} "
            f"| green={cond_green} "
            f"ema8={cond_ema8} "
            f"el={cond_ema20_low} "
            f"eh={cond_ema20_high} "
            f"touch={cond_no_touch} "
            f"rsi_rng={cond_rsi_range} "
            f"rsi_up={cond_rsi_rising} "
            f"=> BUY={buy}"
        )

        prev_rsi_raw = rsi_raw

    print("\n=== PARITY DEBUG END ===\n")


if __name__ == "__main__":
    main()
