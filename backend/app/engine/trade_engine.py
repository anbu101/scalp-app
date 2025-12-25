from typing import Dict

from app.engine.indicator_engine_pine_v1_9 import IndicatorEnginePineV19
from app.engine.strategy_engine import StrategyEngine
from app.utils.candle_debug_logger import CandleDebugLogger


class TradeEngine:
    """
    One TradeEngine per option instrument.
    Consumes CLOSED candles only.
    """

    def __init__(self, symbol: str, slot: str):
        self.symbol = symbol
        self.slot = slot

        self.ind = IndicatorEnginePineV19()
        self.strategy = StrategyEngine(slot_name=slot, symbol=symbol)
        self.debug = CandleDebugLogger(symbol=symbol, slot=slot)

    def on_candle(self, candle):
        """
        candle: Candle (1m, closed)
        """

        # 1️⃣ Update indicators
        self.ind.on_candle(
            o=candle.open,
            h=candle.high,
            l=candle.low,
            c=candle.close,
        )

        snap = self.ind.snapshot()
        if snap is None:
            return

        # 2️⃣ Evaluate strategy
        signal = self.strategy.on_candle(candle, self.ind)

        # 3️⃣ Build condition map (EXACT Pine parity)
        checks = {
            "green": candle.close > candle.open,
            "close_gt_ema8": candle.close > snap["ema8"],
            "close_ge_ema20_low": candle.close >= snap["ema20_low"],
            "close_le_ema20_high": candle.close <= snap["ema20_high"],
            "rsi_in_range": snap["rsi_in_range"],
            "rsi_rising": snap["rsi_rising"],
        }

        buy_allowed = all(checks.values())

        # 4️⃣ DEBUG LOG (ONE ROW PER CANDLE)
        self.debug.log(
            candle_ts=candle.start_ts,
            o=candle.open,
            h=candle.high,
            l=candle.low,
            c=candle.close,
            ind=snap,
            checks=checks,
            buy_allowed=buy_allowed,
        )

        return signal
