"""
Sanity test for Candle → Indicator → Strategy pipeline
Safe to run on market holidays.
No Zerodha, no WebSocket, no orders.
"""

import time
import logging

from engine.candle_engine import CandleEngine

# ------------------------------------------------------
# Logging setup (important)
# ------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# ------------------------------------------------------
# Test configuration
# ------------------------------------------------------

INSTRUMENT_TOKEN = 999999
TIMEFRAME = "1m"

# ------------------------------------------------------
# Test runner
# ------------------------------------------------------

def run_sanity_test():
    print("\n=== SANITY TEST STARTED ===\n")

    engine = CandleEngine(
        instrument_token=INSTRUMENT_TOKEN,
        timeframe=TIMEFRAME,
        last_candle_end_ts=None,
    )

    # Simulated tick stream (price gradually rising)
    base_ts = int(time.time())

    prices = [
        100, 101, 102, 101, 103,
        104, 103, 105, 106, 107,
        108, 109, 108, 110, 111,
        112, 113, 112, 114, 115,
        116, 115, 117, 118, 119,
        120, 121, 122, 121, 123,
        124, 125, 124, 126, 127,
        128, 129, 130, 129, 131,
    ]

    for i, price in enumerate(prices):
        ts = base_ts + i * 10
        engine.on_tick(price, ts)
        time.sleep(0.01)  # tiny delay to keep logs readable

    print("\n=== SANITY TEST COMPLETED ===\n")


if __name__ == "__main__":
    run_sanity_test()
