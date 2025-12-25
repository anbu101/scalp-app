from pathlib import Path
import time

from trading.trade_state_manager import TradeStateManager
from execution.log_executor import LogOrderExecutor


executor = LogOrderExecutor()
state_file = Path("state_ce.json")

mgr = TradeStateManager(
    name="CE",
    executor=executor,
    state_file=state_file,
)

print("\n--- FORCED BUY + EXIT TEST ---\n")

# FORCE TEST MODE
mgr._TEST_MODE = True

mgr.on_buy_signal(
    symbol="NIFTY25JAN25800CE",
    token=123,
    ltp=133.45,
    qty=75,
    sl_points=10,
    tp_points=20,
)



# FORCE ticks to cross TP
ticks = [135, 140, 150, 155]

for price in ticks:
    time.sleep(0.2)
    mgr.on_tick(price)

print("\n--- TEST COMPLETE ---\n")
