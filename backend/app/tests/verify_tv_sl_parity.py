"""
verify_tv_sl_parity.py

OPTION-ONLY BUY + SL PARITY (FINAL, CLEAN)
-----------------------------------------
✔ Option chart only
✔ Instruments loaded via KiteConnect (no CSV paths)
✔ ZerodhaInstrumentResolver used correctly
✔ BUY + SL parity on option candles
"""

from datetime import datetime
from pathlib import Path
import pandas as pd

from backend.app.marketdata.zerodha_auth_dont_use import get_kite_client
from app.marketdata.zerodha_historical import ZerodhaHistoricalFetcher
from app.marketdata.zerodha_instrument_resolver import ZerodhaInstrumentResolver
from backend.app.indicators.indicator_eng_OLD_DO_NOT_USE import IndicatorEngine


# =========================
# CONFIG
# =========================
SYMBOL = "NIFTY25D1625800CE"   # TradingView option symbol
TIMEFRAME_SEC = 60
TIMEFRAME_LABEL = "minute"

SL_SEARCH_DEPTH = 20
MIN_SL_PTS = 5.0
EXCLUDE_CURRENT_BAR = True


# =========================
# BUY LOGIC (LOCKED)
# =========================
def is_buy_signal(c, prev_buy_raw, rsi_rising):

    cond_close_gt_open = c.close > c.open
    cond_close_gt_ema8 = c.close > c.ema8
    cond_close_ge_ema20 = c.close >= c.ema20_low

    if c.ema8 < c.ema20_high:
        cond_close_ema20 = (
            c.high < c.ema20_high
            and max(c.open, c.close) < c.ema20_high
        )
    else:
        cond_close_ema20 = c.close <= c.ema20_high

    cond_rsi_range = 40 <= c.rsi_raw <= 65

    buy_raw = (
        cond_close_gt_open
        and cond_close_gt_ema8
        and cond_close_ge_ema20
        and cond_close_ema20
        and cond_rsi_range
        and rsi_rising
    )

    buy = buy_raw and not prev_buy_raw
    return buy, buy_raw


# =========================
# SL LOGIC (PINE-EXACT)
# =========================
def compute_sl(candles, buy_idx, entry_price):

    start = 1 if EXCLUDE_CURRENT_BAR else 0

    # 1) recent red candle low
    for i in range(start, min(SL_SEARCH_DEPTH + 1, buy_idx + 1)):
        c = candles[buy_idx - i]
        if c.close < c.open and c.low < entry_price:
            return c.low, buy_idx - i, None

    # 2) lowest low in window
    lows = [
        candles[buy_idx - i].low
        for i in range(start, min(SL_SEARCH_DEPTH + 1, buy_idx + 1))
    ]
    if lows:
        low = min(lows)
        if low < entry_price:
            idx = next(
                buy_idx - i
                for i in range(start, min(SL_SEARCH_DEPTH + 1, buy_idx + 1))
                if candles[buy_idx - i].low == low
            )
            return low, idx, None

    # 3) fallback
    idx = buy_idx - start
    low = candles[idx].low

    if entry_price - low <= 0:
        return None, None, "INVALID_SL"

    if (entry_price - low) < MIN_SL_PTS:
        return None, None, "SL_TOO_SMALL"

    return low, idx, None


# =========================
# MAIN
# =========================
def main():
    print("\n=== VERIFY TV SL PARITY (OPTION ONLY) ===\n")

    # -------------------------------------------------
    # Kite + instruments
    # -------------------------------------------------
    kite = get_kite_client()

    print("[INSTRUMENTS] Fetching Zerodha instruments via Kite API...")
    instruments = kite.instruments("NFO")
    instruments_df = pd.DataFrame(instruments)

    row = instruments_df[instruments_df["tradingsymbol"] == SYMBOL]

    if row.empty:
        raise KeyError(f"Instrument not found for symbol: {SYMBOL}")

    inst = row.iloc[0].to_dict()


    print("[INSTRUMENT]")
    print(f"  Symbol : {inst['tradingsymbol']}")
    print(f"  Token  : {inst['instrument_token']}")
    print(f"  Expiry : {inst['expiry']}")
    print(f"  Strike : {inst['strike']}")
    print()

    # -------------------------------------------------
    # Historical candles (OPTION)
    # -------------------------------------------------
    hist = ZerodhaHistoricalFetcher(
        kite=kite,
        base_dir=Path("~/.scalp-app/candles"),
        days=3,
    )

    candles = hist.fetch(
        symbol=inst["tradingsymbol"],
        instrument_token=inst["instrument_token"],
        expiry=inst["expiry"].strftime("%Y-%m-%d"),
        timeframe_sec=TIMEFRAME_SEC,
    )

    print(f"[DATA] Option candles loaded: {len(candles)}\n")

    # -------------------------------------------------
    # Indicator engine
    # -------------------------------------------------
    engine = IndicatorEngine(
        instrument_token=inst["instrument_token"],
        timeframe_label=TIMEFRAME_LABEL,
    )

    prev_buy_raw = False
    prev_rsi = None

    # -------------------------------------------------
    # BUY + SL parity
    # -------------------------------------------------
    for idx, c in enumerate(candles):

        snap = engine.on_candle(c)
        if snap is None:
            continue

        c.ema8 = snap.ema8
        c.ema20_low = snap.ema20_low
        c.ema20_high = snap.ema20_high
        c.rsi_raw = snap.rsi_raw

        rsi_rising = prev_rsi is not None and c.rsi_raw > prev_rsi
        prev_rsi = c.rsi_raw

        buy, prev_buy_raw = is_buy_signal(c, prev_buy_raw, rsi_rising)
        if not buy:
            continue

        entry_price = c.close
        entry_time = datetime.fromtimestamp(c.end_ts)

        sl_price, sl_idx, reason = compute_sl(candles, idx, entry_price)

        if reason:
            print(f"[BUY BLOCKED] {entry_time} | reason={reason}")
        else:
            sl_time = datetime.fromtimestamp(candles[sl_idx].end_ts)
            print(
                f"[BUY] {entry_time} | entry={entry_price:.2f}\n"
                f"  [SL ] {sl_time} | price={sl_price:.2f}\n"
            )


if __name__ == "__main__":
    main()
