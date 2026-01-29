from pathlib import Path
from datetime import datetime
import pandas as pd

from backend.app.marketdata.zerodha_auth_dont_use import get_kite_client
from app.marketdata.zerodha_historical import ZerodhaHistoricalFetcher


# =========================================================
# CONFIG — MUST MATCH PINE
# =========================================================

SYMBOL = "NIFTY25D1625800CE"
TIMEFRAME_SEC = 60

MIN_SL = 5.0
RR = 1.0

RSI_LOW = 40
RSI_HIGH = 65


# =========================================================
# INDICATOR HELPERS (PINE-EQUIVALENT)
# =========================================================

def ema(prev, price, length):
    alpha = 2 / (length + 1)
    return price if prev is None else (price - prev) * alpha + prev


def sma(buf, value, length):
    buf.append(value)
    if len(buf) > length:
        buf.pop(0)
    return sum(buf) / len(buf)


# =========================================================
# SL LOGIC (ALREADY VERIFIED)
# =========================================================

def find_sl(candles, idx, entry):
    signal_date = datetime.fromtimestamp(candles[idx].start_ts).date()

    for j in range(idx - 1, -1, -1):
        c = candles[j]
        c_date = datetime.fromtimestamp(c.start_ts).date()

        # STOP at session boundary
        if c_date != signal_date:
            return None

        # nearest RED candle only
        if c.close < c.open:
            sl = c.low
            if entry - sl < MIN_SL:
                return None
            return sl

    return None


# =========================================================
# MAIN
# =========================================================

def main():
    print("\n=== VERIFY TV BUY + SL/TP PARITY (FINAL) ===\n")

    kite = get_kite_client()

    instruments = pd.DataFrame(kite.instruments("NFO"))
    inst = instruments[instruments["tradingsymbol"] == SYMBOL].iloc[0]

    print("[INSTRUMENT]")
    print(f"  Symbol : {inst['tradingsymbol']}")
    print(f"  Token  : {inst['instrument_token']}")
    print(f"  Expiry : {inst['expiry']}\n")

    hist = ZerodhaHistoricalFetcher(
        kite=kite,
        base_dir=Path("~/.scalp-app/data"),
        days=3,
    )

    candles = hist.fetch(
        symbol=inst["tradingsymbol"],
        instrument_token=int(inst["instrument_token"]),
        expiry=str(inst["expiry"]),
        timeframe_sec=TIMEFRAME_SEC,
    )

    print(f"[INFO] Candles fetched: {len(candles)}\n")

    # -----------------------------------------------------
    # INDICATOR STATE (MATCH PINE)
    # -----------------------------------------------------

    ema8_raw = None
    ema20_low_raw = None
    ema20_high_raw = None

    ema8_buf = []
    ema20_low_buf = []
    ema20_high_buf = []

    prev_close = None
    avg_gain = None
    avg_loss = None
    rsi_prev = None
    rsi_buf = []

    prev_cond_all = False
    in_trade = False
    trade = None

    # -----------------------------------------------------
    # LOOP
    # -----------------------------------------------------

    for i, c in enumerate(candles):
        ts = datetime.fromtimestamp(c.start_ts)

        # ---------------- EMA ----------------
        ema8_raw = ema(ema8_raw, c.close, 8)
        ema8 = sma(ema8_buf, ema8_raw, 8)

        ema20_low_raw = ema(ema20_low_raw, c.low, 20)
        ema20_low = sma(ema20_low_buf, ema20_low_raw, 9)

        ema20_high_raw = ema(ema20_high_raw, c.high, 20)
        ema20_high = sma(ema20_high_buf, ema20_high_raw, 9)

        # ---------------- RSI ----------------
        if prev_close is None:
            prev_close = c.close
            continue

        change = c.close - prev_close
        gain = max(change, 0)
        loss = max(-change, 0)

        if avg_gain is None:
            avg_gain = gain
            avg_loss = loss
        else:
            avg_gain = ((avg_gain * 4) + gain) / 5
            avg_loss = ((avg_loss * 4) + loss) / 5

        rsi = 100 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
        rsi_sm = sma(rsi_buf, rsi, 5)
        rsi_rising = rsi_prev is not None and rsi > rsi_prev
        rsi_prev = rsi
        prev_close = c.close

        # ---------------- PINE cond_all ----------------
        cond_all = (
            c.close > c.open and
            c.close > ema8 and
            c.close >= ema20_low and
            c.close <= ema20_high and
            (True if ema8 >= ema20_high else
             (c.high < ema20_high and max(c.open, c.close) < ema20_high)) and
            RSI_LOW <= rsi <= RSI_HIGH and
            rsi_rising and
            not in_trade
        )

        # ---------------- RISING EDGE ----------------
        buySignal = cond_all and not prev_cond_all
        prev_cond_all = cond_all

        # ---------------- BUY ----------------
        if buySignal:
            entry = c.close
            sl = find_sl(candles, i, entry)
            if sl is None:
                continue

            tp = entry + (entry - sl) * RR

            print(
                f"[BUY ] {ts} | entry={entry:.2f}\n"
                f"  SL={sl:.2f} | TP={tp:.2f}\n"
            )

            trade = {"sl": sl, "tp": tp}
            in_trade = True
            continue

        # ---------------- EXIT ----------------
        if in_trade:
            if c.low <= trade["sl"]:
                print(f"[EXIT-SL] {ts} | price={trade['sl']:.2f}\n")
                in_trade = False
                trade = None
                continue

            if c.high >= trade["tp"]:
                print(f"[EXIT-TP] {ts} | price={trade['tp']:.2f}\n")
                in_trade = False
                trade = None
                continue

    print("\n=== PARITY CHECK COMPLETE ===\n")


if __name__ == "__main__":
    main()
