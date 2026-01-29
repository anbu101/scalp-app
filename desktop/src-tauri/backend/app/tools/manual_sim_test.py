"""
Manual Simulation Trigger
-------------------------
Purpose:
- Trigger BUY without market
- Manually move price to hit TP / SL
- Observe logs + UI behaviour

SAFE:
- Works ONLY when SIMULATION_MODE = True
- Does NOT place real orders
"""

import time

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


# 🔑 IMPORTANT: import app context FIRST
from app.api_server import executor
from app.engine.signal_router import signal_router

from pathlib import Path
from app.trading.trade_state_manager import TradeStateManager
from app.sim.sim_price_provider import SimPriceProvider
from app.sim.sim_mode import enable_simulation
enable_simulation()


price_provider = SimPriceProvider(seed_price=100)

# --- CREATE SLOTS (CRITICAL) ---
TradeStateManager(
    name="CE_1",
    executor=executor,
    state_file=Path("/tmp/CE_1.json"),
    price_provider=price_provider,
)
TradeStateManager(
    name="CE_2",
    executor=executor,
    state_file=Path("/tmp/CE_2.json"),
    price_provider=price_provider,
)
TradeStateManager(
    name="PE_1",
    executor=executor,
    state_file=Path("/tmp/PE_1.json"),
    price_provider=price_provider,
)
TradeStateManager(
    name="PE_2",
    executor=executor,
    state_file=Path("/tmp/PE_2.json"),
    price_provider=price_provider,
)

def banner(msg):
    print("\n" + "=" * 60)
    print(msg)
    print("=" * 60 + "\n")


def trigger_buy(symbol, ltp):
    banner(f"TRIGGER BUY → {symbol} @ {ltp}")
    signal_router.route_buy_signal(
        symbol=symbol,
        token=999999,
        candle_ts=int(time.time()),
        ltp=ltp,
    )


def move_price(symbol, ltp):
    banner(f"SET LTP → {symbol} = {ltp}")
    price_provider.set_price(symbol, ltp)



if __name__ == "__main__":
    SYMBOL = "NIFTY_SIM_24000CE"

    # -------------------------
    # STEP 1: BUY
    # -------------------------
    trigger_buy(SYMBOL, ltp=100)
    time.sleep(2)

    # -------------------------
    # STEP 2: HIT TP
    # -------------------------
    move_price(SYMBOL, ltp=150)
    time.sleep(2)

    # -------------------------
    # STEP 3: BUY AGAIN
    # -------------------------
    trigger_buy(SYMBOL, ltp=100)
    time.sleep(2)

    # -------------------------
    # STEP 4: HIT SL
    # -------------------------
    move_price(SYMBOL, ltp=50)
    time.sleep(2)

    banner("SIMULATION COMPLETE")
