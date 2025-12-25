import pandas as pd

# ----------------------------
# 1. Load candle data
# ----------------------------
# Replace with your actual candle file or loader
df = pd.read_csv("candles.csv", parse_dates=["timestamp"])

# IMPORTANT: sort by time
df = df.sort_values("timestamp").reset_index(drop=True)

# ----------------------------
# 2. REQUIRED columns check
# ----------------------------
required_cols = [
    "timestamp", "open", "high", "low", "close",
    "ema8", "ema20_low", "ema20_high",
    "rsiRaw", "rsiSm",
    "cond_rsi_rising",
    "inSession1", "inSession2",
    "isMinuteChart",
    "useRisingEdge",
    "isConfirmed",
    "slLevel",
    "rsiRisingMethod"
]

missing = [c for c in required_cols if c not in df.columns]
if missing:
    raise Exception(f"Missing columns: {missing}")

# ----------------------------
# 3. BUY signal function
# ----------------------------
def is_buy_signal(i, df, prev_buy_raw):
    row = df.iloc[i]

    cond_close_gt_open  = row.close > row.open
    cond_close_gt_ema8  = row.close > row.ema8
    cond_close_ge_ema20 = row.close >= row.ema20_low
    cond_close_le_ema20 = row.close <= row.ema20_high

    if row.ema8 < row.ema20_high:
        not_touching_high = (
            row.high < row.ema20_high and
            max(row.open, row.close) < row.ema20_high
        )
    else:
        not_touching_high = True

    if row.rsiRisingMethod == "Raw":
        cond_rsi_range = 40 <= row.rsiRaw <= 65
    else:
        cond_rsi_range = 40 <= row.rsiSm <= 65

    is_trading_time = row.inSession1 or row.inSession2
    cond_no_open_trade = pd.isna(row.slLevel)

    buy_raw = (
        row.isMinuteChart
        and cond_close_gt_open
        and cond_close_gt_ema8
        and cond_close_ge_ema20
        and cond_close_le_ema20
        and not_touching_high
        and cond_rsi_range
        and row.cond_rsi_rising
        and is_trading_time
        and cond_no_open_trade
    )

    if not row.isConfirmed:
        return False, buy_raw

    if row.useRisingEdge:
        buy = buy_raw and not prev_buy_raw
    else:
        buy = buy_raw

    return buy, buy_raw

# ----------------------------
# 4. Run loop & log BUYs
# ----------------------------
prev_buy_raw = False

print("==== BUY SIGNALS ====")
for i in range(1, len(df)):
    buy, buy_raw = is_buy_signal(i, df, prev_buy_raw)
    prev_buy_raw = buy_raw

    if buy:
        r = df.iloc[i]
        print(
            f"[BUY] {r.timestamp} | "
            f"O={r.open} H={r.high} L={r.low} C={r.close}"
        )
