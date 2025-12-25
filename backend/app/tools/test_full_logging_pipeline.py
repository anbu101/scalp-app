import time

from app.marketdata.candle_builder import CandleBuilder
from app.engine.indicator_engine_pine_v1_9 import IndicatorEnginePineV19
from app.engine.strategy_engine import StrategyEngine
from app.trading.trade_state_manager import TradeStateManager
from app.execution.log_executor import LogOrderExecutor
from pathlib import Path


def run():
    print("=== FULL LOGGING PIPELINE TEST START ===")

    # -------------------------
    # Setup components
    # -------------------------
    token = 999999
    candle_builder = CandleBuilder(
        instrument_token=token,
        timeframe_sec=60,
    )

    indicator = IndicatorEnginePineV19()
    strategy = StrategyEngine()

    executor = LogOrderExecutor()
    manager = TradeStateManager(
        name="CE_TEST",
        executor=executor,
        state_file=Path("state_test.json"),
    )

    # Force test mode
    manager._TEST_MODE = True

    # -------------------------
    # Generate fake candles
    # -------------------------
    base_ts = int(time.time() // 60 * 60)

    prices = [
        100, 101, 102, 103, 104,
        105, 106, 107, 108, 109,
        110, 111, 112, 113, 114,
        115, 116, 117, 118, 119,
    ]

    for i, price in enumerate(prices):
        ts = base_ts + (i * 60)

        candle = candle_builder.on_tick(price, ts)
        if not candle:
            continue

        # Indicator update
        indicator.update(candle)

        # Strategy evaluation
        signal = strategy.on_candle(candle, indicator)

        # Trade handling
        if signal.is_buy:
            manager.on_buy_signal(
                symbol="TEST_OPTION",
                token=token,
                ltp=signal.entry_price,
                qty=75,
                sl_points=5,
                tp_points=10,
            )

        time.sleep(0.05)

    print("=== FULL LOGGING PIPELINE TEST END ===")


if __name__ == "__main__":
    run()
