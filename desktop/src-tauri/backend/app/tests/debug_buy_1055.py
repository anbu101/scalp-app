from pathlib import Path
from datetime import datetime
import pandas as pd

from backend.app.marketdata.zerodha_auth_dont_use import get_kite_client
from app.marketdata.zerodha_historical import ZerodhaHistoricalFetcher


# =========================
# CONFIG
# =========================

SYMBOL = "NIFTY25D1625800CE"
TIMEFRAME_SEC = 60

RSI_LOW = 40
RSI_HIGH = 65


# =========================
# INDICATORS (PINE-EQUIV)
# =========================

def ema(prev, price, length):
    alpha = 2 / (length + 1)
    return price if prev is None else (price - prev) * alpha + prev


def sma(buf, value, length):
    buf.append(value)
    if len(buf) > length:
        buf.pop(0)
    return sum(buf) / len(buf)


# =========================
# MAIN
# =========================

def main():
    print("\n=== DEBUG BUY @ 10:55 (PINE PARITY) ===\n")

    kite = get_kite_client()

    instruments = pd.DataFrame(kite.instruments("NFO"))
    inst = instruments[instruments["tradingsymbol"] == SYMBOL].iloc[0]

    hist = ZerodhaHistoricalFetcher(
        kite=kite,
        base_dir=Path("~/.scalp-app/data"),
        days=2,
    )

    candles = hist.fetch(
        symbol=inst["tradingsymbol"],
        instrument_token=int(inst["instrument_token"]),
        expiry=str(inst["expiry"]),
        timeframe_sec=TIMEFRAME_SEC,
    )

    # ---------------- STATE ----------------
    ema8_raw = ema20_low_raw = ema20_high_raw = None
    ema8_buf, ema20_low_buf, ema20_high_buf = [], [], []

    prev_close = None
    avg_gain = avg_loss = None
    rsi_prev = None
    rsi_buf = []

    prev_cond_all = False

    # ---------------- LOOP ----------------
    for c in candles:
        ts = datetime.fromtimestamp(c.start_ts)

        if ts.date().isoformat() != "2025-12-12":
            continue
        if ts.hour != 10 or ts.minute not in (53, 54, 55, 56):
            continue

        # EMA
        ema8_raw = ema(ema8_raw, c.close, 8)
        ema8 = sma(ema8_buf, ema8_raw, 8)

        ema20_low_raw = ema(ema20_low_raw, c.low, 20)
        ema20_low = sma(ema20_low_buf, ema20_low_raw, 9)

        ema20_high_raw = ema(ema20_high_raw, c.high, 20)
        ema20_high = sma(ema20_high_buf, ema20_high_raw, 9)

        # RSI
        if prev_close is None:
            prev_close = c.close
            continue

        change = c.close - prev_close
        gain = max(change, 0)
        loss = max(-change, 0)

        if avg_gain is None:
            avg_gain, avg_loss = gain, loss
        else:
            avg_gain = ((avg_gain * 4) + gain) / 5
            avg_loss = ((avg_loss * 4) + loss) / 5

        rsi = 100 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
        rsi_sm = sma(rsi_buf, rsi, 5)
        rsi_rising = rsi_prev is not None and rsi > rsi_prev
        rsi_prev = rsi
        prev_close = c.close

        # BUY CONDITIONS
        cond_close_gt_open = c.close > c.open
        cond_close_gt_ema8 = c.close > ema8
        cond_close_ge_ema20 = c.close >= ema20_low
        cond_close_not_above_ema20 = c.close <= ema20_high

        if ema8 < ema20_high:
            not_touching_high = (
                c.high < ema20_high and
                max(c.open, c.close) < ema20_high
            )
        else:
            not_touching_high = True

        cond_rsi_range = RSI_LOW <= rsi <= RSI_HIGH
        cond_rsi_rising = rsi_rising

        cond_all = (
            cond_close_gt_open and
            cond_close_gt_ema8 and
            cond_close_ge_ema20 and
            cond_close_not_above_ema20 and
            not_touching_high and
            cond_rsi_range and
            cond_rsi_rising
        )

        buySignal = cond_all and not prev_cond_all

        print(
            f"\n[{ts.strftime('%H:%M')}] "
            f"O={c.open:.2f} H={c.high:.2f} "
            f"L={c.low:.2f} C={c.close:.2f}"
        )
        print(
            f" EMA8={ema8:.2f} EMA20L={ema20_low:.2f} EMA20H={ema20_high:.2f}"
        )
        print(
            f" RSI={rsi:.2f} RSI↑={cond_rsi_rising}"
        )
        print(
            f" close>open={cond_close_gt_open} "
            f"close>ema8={cond_close_gt_ema8} "
            f"close>=ema20L={cond_close_ge_ema20}"
        )
        print(
            f" close<=ema20H={cond_close_not_above_ema20} "
            f"notTouchHigh={not_touching_high}"
        )
        print(
            f" rsiRange={cond_rsi_range} "
            f"cond_all={cond_all} "
            f"prev_cond_all={prev_cond_all} "
            f"BUY={buySignal}"
        )

        prev_cond_all = cond_all

    print("\n=== DEBUG COMPLETE ===\n")


if __name__ == "__main__":
    main()
