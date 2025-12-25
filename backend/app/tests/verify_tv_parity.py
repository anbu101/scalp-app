"""
verify_tv_buy_parity.py

BUY PARITY WITH RSI RISING (PINE-CORRECT)
----------------------------------------
✔ Timestamp parity
✔ EMA parity
✔ EMA20 high no-touch parity
✔ RSI range parity
✔ RSI rising parity (CONFIRMED BAR)
"""

from datetime import datetime
from pathlib import Path

from backend.app.marketdata.zerodha_auth_dont_use import get_kite_client
from app.marketdata.zerodha_historical import ZerodhaHistoricalFetcher
from backend.app.indicators.indicator_eng_OLD_DO_NOT_USE import IndicatorEngine


# ============================================================
# BUY LOGIC — FINAL PINE PARITY
# ============================================================

def is_buy_signal(c, prev_buy_raw, rsi_rising):

    cond_close_gt_open = c.close > c.open
    cond_close_gt_ema8 = c.close > c.ema8
    cond_close_ge_ema20 = c.close >= c.ema20_low

    # EMA20 HIGH no-touch logic (Pine exact)
    if c.ema8 < c.ema20_high:
        cond_close_ema20 = (
            c.high < c.ema20_high
            and max(c.open, c.close) < c.ema20_high
        )
    else:
        cond_close_ema20 = c.close <= c.ema20_high

    rsi_val = c.rsi_raw
    cond_rsi_range = 40 <= rsi_val <= 65

    buy_raw = (
        cond_close_gt_open
        and cond_close_gt_ema8
        and cond_close_ge_ema20
        and cond_close_ema20
        and cond_rsi_range
        and rsi_rising
        and c.is_trading_time
        and c.is_minute_chart
        and c.sl_level is None
    )

    if not c.is_confirmed:
        return False, buy_raw

    buy = buy_raw and not prev_buy_raw if c.use_rising_edge else buy_raw
    return buy, buy_raw


# ============================================================
# MAIN
# ============================================================

def main():
    print("=== VERIFY TRADINGVIEW BUY PARITY (RSI RISING ENABLED) ===")

    kite = get_kite_client()

    instrument_token = 256265
    timeframe_sec = 60
    timeframe_label = "minute"

    base_dir = Path("~/.scalp-app/data").expanduser()

    fetcher = ZerodhaHistoricalFetcher(
        kite=kite,
        base_dir=base_dir,
        days=5,
    )

    candles = fetcher.fetch(
        symbol="NIFTY",
        instrument_token=instrument_token,
        expiry="2099-12-31",
        timeframe_sec=timeframe_sec,
    )

    print(f"[INFO] Candles fetched: {len(candles)}")

    engine = IndicatorEngine(
        instrument_token=instrument_token,
        timeframe_label=timeframe_label,
    )

    prev_buy_raw = False
    prev_rsi_raw = None

    for c in candles:
        snap = engine.on_candle(c)
        if snap is None:
            continue

        # attach indicators
        c.ema8 = snap.ema8
        c.ema20_low = snap.ema20_low
        c.ema20_high = snap.ema20_high
        c.rsi_raw = snap.rsi_raw
        c.rsi_smoothed = snap.rsi_smoothed

        # Pine context
        c.is_trading_time = True
        c.is_minute_chart = True
        c.sl_level = None
        c.is_confirmed = True
        c.use_rising_edge = True

        # === RSI rising (Pine parity) ===
        rsi_rising = (
            prev_rsi_raw is not None
            and c.rsi_raw > prev_rsi_raw
        )
        prev_rsi_raw = c.rsi_raw

        buy, prev_buy_raw = is_buy_signal(
            c,
            prev_buy_raw,
            rsi_rising
        )

        if buy:
            open_time = datetime.fromtimestamp(c.start_ts)
            print(f"[BUY] {open_time}")


if __name__ == "__main__":
    main()
