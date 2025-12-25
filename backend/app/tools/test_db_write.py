# backend/app/tools/test_db_write.py

from app.db.sqlite import init_db
from app.persistence.market_timeline_writer import write_market_timeline_row
from app.marketdata.candle import Candle


def run():
    init_db()

    candle = Candle(
        start_ts=1730000000,
        end_ts=1730000060,
        open=100.0,
        high=105.0,
        low=99.5,
        close=104.0,
        source="MANUAL_TEST",
    )

    write_market_timeline_row(
        candle=candle,
        indicators={
            "ema8": None,
            "ema20_low": None,
            "ema20_high": None,
            "rsi_raw": None,
        },
        conditions={
            "cond_close_gt_open": True,
            "cond_close_gt_ema8": False,
            "cond_close_ge_ema20": False,
            "cond_close_not_above_ema20": True,
            "cond_not_touching_high": True,
            "cond_rsi_ge_40": False,
            "cond_rsi_le_65": False,
            "cond_rsi_range": False,
            "cond_rsi_rising": False,
            "cond_is_trading_time": False,
            "cond_no_open_trade": True,
            "cond_all": False,
        },
        signal=None,
        symbol="TEST_SYMBOL",
        timeframe="1m",
        strategy_version="MANUAL_TEST",
    )


if __name__ == "__main__":
    run()
