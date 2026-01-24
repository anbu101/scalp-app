# backend/app/persistence/timeline_persist.py

from typing import Optional
from app.event_bus.audit_logger import write_audit_log

from app.db.timeline_repo import insert_timeline_row
from app.candles.candle_builder import Candle
from app.event_bus.audit_logger import write_audit_log


def persist_candle_snapshot(
    *,
    candle: Candle,
    indicators: dict,
    conditions: dict,
    signal: Optional[str],
    symbol: str,
    timeframe: str,
    strategy_version: str,
):
    """
    Persist one completed candle snapshot into market_timeline.
    No calculations here.
    """
    write_audit_log(
        f"[TRACE][TIMELINE_PERSIST] INSERT "
        f"symbol={symbol} timeframe={timeframe} ts={candle.ts}"
    )

    # 🔍 ENTRY LOG — MUST FIRE ON EVERY COMPLETED CANDLE
    #write_audit_log(
        #f"[TIMELINE][PERSIST] symbol={symbol} tf={timeframe} "
        #f"ts={candle.ts} close={candle.close} "
        #f"ema8={indicators.get('ema8')} rsi={indicators.get('rsi_raw')} "
        #f"signal={signal}"
    #)

    # 🚨 SANITY WARNING — SHOULD NEVER HAPPEN IN NORMAL FLOW
    if indicators.get("ema8") is None or indicators.get("rsi_raw") is None:
        write_audit_log(
            f"[TIMELINE][WARN] Indicators missing for symbol={symbol} ts={candle.ts}"
        )

    insert_timeline_row({
        # Instrument
        "symbol": symbol,
        "timeframe": timeframe,
        "ts": candle.ts,

        # Candle
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,

        # Indicators
        "ema8": indicators["ema8"],
        "ema20_low": indicators["ema20_low"],
        "ema20_high": indicators["ema20_high"],
        "rsi_raw": indicators["rsi_raw"],

        # Conditions
        "cond_close_gt_open": conditions["cond_close_gt_open"],
        "cond_close_gt_ema8": conditions["cond_close_gt_ema8"],
        "cond_close_ge_ema20": conditions["cond_close_ge_ema20"],
        "cond_close_not_above_ema20": conditions["cond_close_not_above_ema20"],
        "cond_not_touching_high": conditions["cond_not_touching_high"],

        "cond_rsi_ge_40": conditions["cond_rsi_ge_40"],
        "cond_rsi_le_65": conditions["cond_rsi_le_65"],
        "cond_rsi_range": conditions["cond_rsi_range"],
        "cond_rsi_rising": conditions["cond_rsi_rising"],

        "cond_is_trading_time": conditions["cond_is_trading_time"],
        "cond_no_open_trade": conditions["cond_no_open_trade"],

        "cond_all": conditions["cond_all"],

        # Signal
        "signal": signal,
        "strategy_version": strategy_version,
    })
