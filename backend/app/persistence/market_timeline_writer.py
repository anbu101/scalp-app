from app.db.timeline_repo import (
    insert_timeline_row,
    update_timeline_row,
)
from app.candles.candle_builder import Candle
from app.event_bus.audit_logger import write_audit_log
from app.db.db_lock import DB_LOCK
from typing import Optional


def write_market_timeline_row(
    *,
    candle: Candle,
    indicators: dict,
    conditions: dict,
    signal: Optional[str],
    symbol: str,
    timeframe: str,
    strategy_version: str,
    mode: str = "insert",   # "insert" | "update"
):
    """
    Persist one completed candle snapshot.

    Design (PHASE-1 LOCKED):
      - INSERT once per candle (OHLC only)
      - UPDATE same row with indicators + conditions + signal
      - UPDATE returns rowcount
      - Safety-insert if UPDATE happens before INSERT
    """

    # -----------------------------
    # NORMALIZE INPUTS (ONCE)
    # -----------------------------
    timeframe = timeframe.lower().strip()

    if not candle or not candle.end_ts:
        # This should NEVER happen – but if it does, fail loudly
        raise RuntimeError(
            f"[TIMELINE] Invalid candle or missing end_ts | "
            f"symbol={symbol} timeframe={timeframe}"
        )

    # -----------------------------
    # TRACE (CRITICAL FOR DEBUG)
    # -----------------------------
    print(
        f"[TIMELINE WRITE] mode={mode} "
        f"symbol={symbol} tf={timeframe} ts={candle.end_ts}"
    )

    # -----------------------------
    # INSERT: OHLC ONLY
    # -----------------------------
    if mode == "insert":
        with DB_LOCK:
            insert_timeline_row({
                "symbol": symbol,
                "timeframe": timeframe,
                "ts": candle.end_ts,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": None,
                "strategy_version": strategy_version,
            })
        return

    # -----------------------------
    # UPDATE: indicators + conditions + signal
    # -----------------------------
    if mode == "update":
        with DB_LOCK:
            updated = update_timeline_row(
                symbol=symbol,
                timeframe=timeframe,
                ts=candle.end_ts,
                data={
                    # Indicators
                    "ema8": indicators.get("ema8"),
                    "ema20_low": indicators.get("ema20_low"),
                    "ema20_high": indicators.get("ema20_high"),
                    "rsi_raw": indicators.get("rsi_raw"),

                    # Conditions
                    "cond_close_gt_open": conditions.get("cond_close_gt_open"),
                    "cond_close_gt_ema8": conditions.get("cond_close_gt_ema8"),
                    "cond_close_ge_ema20": conditions.get("cond_close_ge_ema20"),
                    "cond_close_not_above_ema20": conditions.get("cond_close_not_above_ema20"),
                    "cond_not_touching_high": conditions.get("cond_not_touching_high"),

                    "cond_rsi_ge_40": conditions.get("cond_rsi_ge_40"),
                    "cond_rsi_le_65": conditions.get("cond_rsi_le_65"),
                    "cond_rsi_range": conditions.get("cond_rsi_range"),
                    "cond_rsi_rising": conditions.get("cond_rsi_rising"),

                    "cond_is_trading_time": conditions.get("cond_is_trading_time"),
                    "cond_no_open_trade": conditions.get("cond_no_open_trade"),
                    "cond_all": conditions.get("cond_all"),

                    "signal": signal,
                },
            )

        print(
            f"[TIMELINE UPDATE] rows={updated} "
            f"symbol={symbol} tf={timeframe} ts={candle.end_ts}"
        )

        # -----------------------------
        # SAFETY NET (restart / race)
        # -----------------------------
        if updated == 0:
            print(
                f"[TIMELINE SAFETY-INSERT] "
                f"symbol={symbol} tf={timeframe} ts={candle.end_ts}"
            )

            with DB_LOCK:
                insert_timeline_row({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "ts": candle.end_ts,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": None,
                    "strategy_version": strategy_version,
                })

                update_timeline_row(
                    symbol=symbol,
                    timeframe=timeframe,
                    ts=candle.end_ts,
                    data={
                        **indicators,
                        **conditions,
                        "signal": signal,
                    },
                )
        return

    # -----------------------------
    # INVALID MODE
    # -----------------------------
    raise ValueError(f"Invalid write mode: {mode}")
