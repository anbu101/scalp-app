import time
import logging

from app.engine.strategy_engine import StrategyEngine
from app.engine.candle_engine import CandleEngine
from app.marketdata.candle import Candle

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# ---------------------------------------------------
# Fake instruments (CE + PE)
# ---------------------------------------------------

CE_TOKEN = 111111
PE_TOKEN = 222222

TIMEFRAME_SEC = 60
TF_LABEL = "1m"


def generate_candle(ts, o, h, l, c):
    return Candle(
        start_ts=ts - TIMEFRAME_SEC,
        end_ts=ts,
        open=o,
        high=h,
        low=l,
        close=c,
    )


def feed_sequence(strategy, prices, start_ts):
    """
    Feed candles directly into strategy.
    """
    ts = start_ts
    for o, h, l, c in prices:
        candle = generate_candle(ts, o, h, l, c)
        strategy.on_candle(candle)
        ts += TIMEFRAME_SEC
        time.sleep(0.02)


def main():
    print("\n=== DRY RUN TEST STARTED ===\n")

    ce_strategy = StrategyEngine(
        instrument_token=CE_TOKEN,
        timeframe_label=TF_LABEL,
        timeframe_sec=TIMEFRAME_SEC,
    )

    pe_strategy = StrategyEngine(
        instrument_token=PE_TOKEN,
        timeframe_label=TF_LABEL,
        timeframe_sec=TIMEFRAME_SEC,
    )

    start_ts = int(time.time())

    # ---------------------------------------------------
    # 1️⃣ Warm-up candles (no trades expected)
    # ---------------------------------------------------
    warmup = [
        (100, 101, 99, 100)
        for _ in range(35)
    ]

    feed_sequence(ce_strategy, warmup, start_ts)
    feed_sequence(pe_strategy, warmup, start_ts)

    # ---------------------------------------------------
    # 2️⃣ Entry-triggering sequence (GREEN candle)
    # ---------------------------------------------------
    entry_seq = [
        (100, 101, 99, 100),   # red / neutral
        (100, 103, 99, 102),   # GREEN breakout → BUY
    ]

    feed_sequence(ce_strategy, entry_seq, start_ts + 40 * TIMEFRAME_SEC)
    feed_sequence(pe_strategy, entry_seq, start_ts + 40 * TIMEFRAME_SEC)

    # ---------------------------------------------------
    # 3️⃣ TP hit candles
    # ---------------------------------------------------
    tp_seq = [
        (102, 105, 101, 104),  # should hit TP
        (104, 106, 103, 105),
    ]

    feed_sequence(ce_strategy, tp_seq, start_ts + 45 * TIMEFRAME_SEC)
    feed_sequence(pe_strategy, tp_seq, start_ts + 45 * TIMEFRAME_SEC)

    print("\n=== DRY RUN TEST COMPLETED ===\n")


if __name__ == "__main__":
    main()
