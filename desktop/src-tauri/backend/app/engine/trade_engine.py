from typing import Dict, Optional

from app.engine.indicator_engine_pine_v1_9 import IndicatorEnginePineV19
from app.engine.strategy_engine import StrategyEngine
from app.utils.candle_debug_logger import CandleDebugLogger
from app.persistence.timeline_persist import persist_candle_snapshot
from app.event_bus.audit_logger import write_audit_log


class TradeEngine:
    """
    One TradeEngine per option instrument.
    Consumes CLOSED candles only.

    timeframe: string label stored in market_timeline DB and used for
    debug logging. Must match the CandleBuilder timeframe driving this
    engine — e.g. "5m" for SCALP_V1, "3m" for BB_V1.
    """

    def __init__(self, symbol: str, slot: str, timeframe: str = "5m"):
        self.symbol    = symbol
        self.slot      = slot
        self.timeframe = timeframe      # e.g. "5m", "3m", "1m"

        self.ind      = IndicatorEnginePineV19()
        self.strategy = StrategyEngine(slot_name=slot, symbol=symbol)
        self.debug    = CandleDebugLogger(symbol=symbol, slot=slot)

    def on_candle(self, candle):
        """
        candle: Candle (closed, at self.timeframe resolution)
        """
        write_audit_log(
            f"[TRACE][TRADE_ENGINE] on_candle ENTER "
            f"symbol={self.symbol} slot={self.slot} tf={self.timeframe} "
            f"start_ts={getattr(candle, 'start_ts', None)} "
            f"end_ts={getattr(candle, 'end_ts', None)}"
        )

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

        # 3️⃣ Build condition map (Pine parity)
        conditions = {
            "cond_close_gt_open":          candle.close > candle.open,
            "cond_close_gt_ema8":          candle.close > snap["ema8"],
            "cond_close_ge_ema20":         candle.close >= snap["ema20_low"],
            "cond_close_not_above_ema20":  candle.close <= snap["ema20_high"],
            "cond_not_touching_high":      candle.high < snap["ema20_high"]
                                           if candle.close < snap["ema20_high"]
                                           else True,

            "cond_rsi_ge_40":   snap["rsi_raw"] >= 40,
            "cond_rsi_le_65":   snap["rsi_raw"] <= 65,
            "cond_rsi_range":   snap["rsi_in_range"],
            "cond_rsi_rising":  snap["rsi_rising"],

            "cond_is_trading_time": True,   # enforced upstream
            "cond_no_open_trade":   True,   # slot-level enforcement
        }

        conditions["cond_all"] = all(conditions.values())

        # 4️⃣ Persist TIMELINE SNAPSHOT with the correct timeframe label
        persist_candle_snapshot(
            candle=candle,
            indicators={
                "ema8":      snap["ema8"],
                "ema20_low": snap["ema20_low"],
                "ema20_high":snap["ema20_high"],
                "rsi_raw":   snap["rsi_raw"],
            },
            conditions=conditions,
            signal=signal.action if signal else None,
            symbol=self.symbol,
            timeframe=self.timeframe,       # ← was hardcoded "1m"
            strategy_version=self.strategy.version,
        )

        # 5️⃣ Debug log (UI table)
        self.debug.log(
            candle_ts=candle.start_ts,
            o=candle.open,
            h=candle.high,
            l=candle.low,
            c=candle.close,
            ind=snap,
            checks=conditions,
            buy_allowed=conditions["cond_all"],
        )

        return signal