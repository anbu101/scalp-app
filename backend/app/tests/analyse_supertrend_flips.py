#!/usr/bin/env python3

import sqlite3
import argparse
import sys
from pathlib import Path
import csv
from datetime import datetime

DB_PATH = Path.home() / ".scalp-app" / "data" / "app.db"

parser = argparse.ArgumentParser()
parser.add_argument("--symbol", default=None)
parser.add_argument("--tf", default="3m")
args = parser.parse_args()

conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row

symbol = args.symbol or conn.execute(
    "SELECT symbol FROM futures_candles WHERE timeframe=? LIMIT 1",
    (args.tf,)
).fetchone()["symbol"]

rows = conn.execute("""
SELECT ts, open, high, low, close,
       supertrend, st_direction,
       bb_upper, bb_lower,
       rsi_raw,
       r1, s1
FROM futures_candles
WHERE symbol=? AND timeframe=?
ORDER BY ts ASC
""", (symbol, args.tf)).fetchall()

candles = [dict(r) for r in rows]

print(f"Loaded {len(candles)} candles")

# ------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------
def format_ts(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

def round2(x):
    return round(x, 2) if x is not None else None

def has_all(c):
    return all([
        c["close"], c["bb_upper"], c["bb_lower"],
        c["rsi_raw"], c["r1"], c["s1"]
    ])

def profit(entry, price, direction):
    val = (price - entry) if direction=="LONG" else (entry - price)
    return round2(val)

def st_distance(i):
    c = candles[i]
    if not c["supertrend"]:
        return None
    return abs(c["close"] - c["supertrend"])

def avg_range(i, n=10):
    if i < n:
        return None
    vals = []
    for j in range(i-n, i):
        if candles[j]["high"] and candles[j]["low"]:
            vals.append(candles[j]["high"] - candles[j]["low"])
    return sum(vals)/len(vals) if vals else None

# ------------------------------------------------------------
# CONDITIONS
# ------------------------------------------------------------
def is_long(i):
    c = candles[i]
    return has_all(c) and (
        c["close"] > c["bb_upper"] and
        c["close"] > c["r1"] and
        c["rsi_raw"] > 70
    )

def is_short(i):
    c = candles[i]
    return has_all(c) and (
        c["close"] < c["bb_lower"] and
        c["close"] < c["s1"] and
        c["rsi_raw"] < 35
    )

def range_exp(i):
    ar = avg_range(i)
    return ar and (candles[i]["high"] - candles[i]["low"]) > 1.5 * ar

def bos(i, d):
    p = candles[i-1]
    c = candles[i]
    return c["close"] < p["low"] if d=="LONG" else c["close"] > p["high"]

def st_exit(i, t):
    d = st_distance(i)
    return d and d <= t

def momentum(i, d):
    if i < 2:
        return False
    c0, c1, c2 = candles[i], candles[i-1], candles[i-2]
    return (
        c0["high"] < c1["high"] < c2["high"]
        if d=="LONG"
        else c0["low"] > c1["low"] > c2["low"]
    )

def st_flip_exit(i, direction):
    for j in range(i+1, len(candles)):
        if candles[j]["st_direction"] != candles[j-1]["st_direction"]:
            return j
    return None

# ------------------------------------------------------------
# EXIT FINDER
# ------------------------------------------------------------
def find_exit(i, direction, strategy):

    st_idx = st_flip_exit(i, direction)
    strat_idx = None

    for j in range(i+1, len(candles)):

        if strategy == "bos" and bos(j, direction):
            strat_idx = j
            break

        if strategy == "range" and range_exp(j):
            strat_idx = j
            break

        if strategy == "st20" and st_exit(j, 20):
            strat_idx = j
            break

        if strategy == "st30" and st_exit(j, 30):
            strat_idx = j
            break

        if strategy == "momentum" and momentum(j, direction):
            strat_idx = j
            break

    if strat_idx is None:
        return st_idx

    if st_idx is None:
        return strat_idx

    return min(strat_idx, st_idx)

# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------
strategies = ["st_flip", "bos", "range", "st20", "st30", "momentum"]

trade_data = []
totals = {s: 0 for s in strategies}

i = 0
trade_id = 1

while i < len(candles):

    if is_long(i):
        direction = "LONG"
    elif is_short(i):
        direction = "SHORT"
    else:
        i += 1
        continue

    entry_idx = i
    entry_price = candles[i]["close"]
    entry_time = format_ts(candles[i]["ts"])

    row = {
        "trade_id": trade_id,
        "entry_time": entry_time,
        "direction": direction,
        "entry_price": round2(entry_price)
    }

    st_idx = st_flip_exit(entry_idx, direction)

    for s in strategies:

        exit_idx = find_exit(entry_idx, direction, s)

        if exit_idx is None:
            continue

        exit_price = candles[exit_idx]["close"]
        pnl = profit(entry_price, exit_price, direction)

        row[f"{s}_exit_time"] = format_ts(candles[exit_idx]["ts"])
        row[f"{s}_pnl"] = pnl

        totals[s] += pnl if pnl else 0

    trade_data.append(row)

    if st_idx:
        i = st_idx + 1
    else:
        i += 1

    trade_id += 1

# ------------------------------------------------------------
# ADD TOTAL ROW
# ------------------------------------------------------------
total_row = {"trade_id": "TOTAL"}

for s in strategies:
    total_row[f"{s}_pnl"] = round2(totals[s])

trade_data.append(total_row)

# ------------------------------------------------------------
# SAVE CSV
# ------------------------------------------------------------
csv_file = "trade_analysis.csv"

keys = trade_data[0].keys()

with open(csv_file, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=keys)
    writer.writeheader()
    writer.writerows(trade_data)

print(f"\nSaved to {csv_file}")