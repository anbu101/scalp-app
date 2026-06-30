#!/usr/bin/env python3
"""
extract_priceN.py  —  SCALP_V5 checkpoint-price extractor (for scale-in simulation)

PURPOSE
  For each closed trade, replay the option's candles from entry and record:
    - close_at_cN   the actual CLOSE price at the Nth candle after entry
                    (N = 8, 10, 11, 12), if the trade was still open then
    - open_at_cN1   the OPEN of candle N+1 — a more realistic add-on FILL price
                    than close_at_cN (you decide at candle N's close, you fill at
                    next candle's open). Both recorded so we can pick.
    - alive_cN      1 if the trade was still open at candle N (held > N candles)

  This is what the prior MAE pass was MISSING: it recorded the worst low through
  candle N, not the actual price AT candle N. The scale-in tranche enters at the
  candle-N price, so we need the real value, not the watermark.

  3-min strategy on 1-min candle data: a "3-min candle" = 3 stored 1-min rows.
  Candle N (3-min) ends at entry + N*3 minutes. We take the 1-min close at that
  boundary as the 3-min close, and the next 1-min open as the next-candle open.

SAFETY
  * READ-ONLY (sqlite mode=ro). Never writes. No app imports. stdlib only.

USAGE  (after the same --discover step already done for extract_mae.py)
  python3 extract_priceN.py \
    --db /Users/anbu/.scalp-app/backtest/backtest.db \
    --trades /Users/anbu/Downloads/2023.csv \
    --out /Users/anbu/Downloads/2023_priceN.csv \
    --year 2023 \
    --candle-table backtest_candles_1m \
    --col-symbol tradingsymbol --col-ts ts \
    --col-open open --col-close close \
    --ts-unit s --candle-min 3

  --candle-min 3  => a strategy candle is 3 stored 1-min rows (your 3-min setup).
"""

import argparse
import csv
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
CHECKPOINTS = [8, 10, 11, 12]          # candle numbers to record (3-min candles)


def open_ro(db_path):
    if not os.path.exists(db_path):
        sys.exit(f"ERROR: DB not found at {db_path}")
    con = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def parse_trade_ts(s, year):
    s = s.strip().strip('"')
    dm, hm = s.split(",")
    d, mo = dm.strip().split("/")
    h, mi = hm.strip().split(":")
    return datetime(year, int(mo), int(d), int(h), int(mi), tzinfo=IST)


def load_trades(path, year):
    rows = list(csv.reader(open(path, newline="")))
    ti = next((i for i, r in enumerate(rows) if r and r[0] == "TRADES"), None)
    if ti is None:
        sys.exit("ERROR: no TRADES block found in trades CSV.")
    out = []
    for r in rows[ti + 2:]:
        if not r or len(r) < 12:
            continue
        sym, ets, entry, sl, tp, xts, exit_, reason, gross, charges, net, amb = r[:12]
        try:
            e = parse_trade_ts(ets, year)
            x = parse_trade_ts(xts, year)
        except Exception:
            continue
        if x < e:
            x += timedelta(days=365)
        out.append(dict(symbol=sym.strip(), entry=float(entry),
                        sl=float(sl) if sl else None, tp=float(tp) if tp else None,
                        exitp=float(exit_) if exit_ else None,
                        reason=reason.strip(), gross=float(gross), charges=float(charges),
                        net=float(net), entry_dt=e, exit_dt=x))
    return out


def to_db_ts(dt, unit):
    if unit == "s":  return int(dt.timestamp())
    if unit == "ms": return int(dt.timestamp() * 1000)
    if unit == "iso": return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
    raise ValueError(unit)


def fetch_minute_rows(con, table, col_sym, col_ts, col_open, col_close,
                      symbol, start_dt, end_dt, unit):
    cur = con.cursor()
    lo = to_db_ts(start_dt - timedelta(minutes=1), unit)
    hi = to_db_ts(end_dt + timedelta(minutes=2), unit)
    q = (f'SELECT "{col_ts}" AS ts, "{col_open}" AS o, "{col_close}" AS c '
         f'FROM "{table}" WHERE "{col_sym}" = ? AND "{col_ts}" >= ? AND "{col_ts}" <= ? '
         f'ORDER BY "{col_ts}" ASC')
    try:
        rows = cur.execute(q, (symbol, lo, hi)).fetchall()
    except sqlite3.DatabaseError as e:
        return None, str(e)
    out = []
    for r in rows:
        ts = r["ts"]
        if unit == "s":   dt = datetime.fromtimestamp(int(ts), IST)
        elif unit == "ms": dt = datetime.fromtimestamp(int(ts) / 1000, IST)
        else: dt = datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        out.append((dt, float(r["o"]), float(r["c"])))
    return out, None


def nearest_row(rows_by_minute, target_dt, tol_min=2):
    """Find the 1-min row whose timestamp is closest to target_dt within tol."""
    best = None; best_gap = None
    for dt, o, c in rows_by_minute:
        gap = abs((dt - target_dt).total_seconds())
        if best_gap is None or gap < best_gap:
            best_gap = gap; best = (dt, o, c)
    if best is None or best_gap > tol_min * 60 + 1:
        return None
    return best


def compute(trade, minute_rows, candle_min):
    entry_dt = trade["entry_dt"]
    res = {}
    total_min = (trade["exit_dt"] - entry_dt).total_seconds() / 60.0
    for N in CHECKPOINTS:
        res[f"alive_c{N}"] = 1 if total_min > N * candle_min else 0
        res[f"close_at_c{N}"] = ""
        res[f"open_at_c{N}1"] = ""
        if res[f"alive_c{N}"]:
            cN_end = entry_dt + timedelta(minutes=N * candle_min)
            row = nearest_row(minute_rows, cN_end)
            if row:
                res[f"close_at_c{N}"] = round(row[2], 2)
            nxt = nearest_row(minute_rows, cN_end + timedelta(minutes=1))
            if nxt:
                res[f"open_at_c{N}1"] = round(nxt[1], 2)
    return res


def main():
    ap = argparse.ArgumentParser(description="Read-only checkpoint-price extractor for scale-in sim.")
    ap.add_argument("--db", required=True)
    ap.add_argument("--trades", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--candle-table", required=True)
    ap.add_argument("--col-symbol", required=True)
    ap.add_argument("--col-ts", required=True)
    ap.add_argument("--col-open", required=True)
    ap.add_argument("--col-close", required=True)
    ap.add_argument("--ts-unit", choices=["s", "ms", "iso"], default="s")
    ap.add_argument("--candle-min", type=int, default=3, help="minutes per strategy candle (3-min=3)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    con = open_ro(args.db)
    trades = load_trades(args.trades, args.year)
    if args.limit:
        trades = trades[:args.limit]
    print(f"Loaded {len(trades)} trades from {args.trades} (year {args.year}).")

    # probe
    probe, perr = fetch_minute_rows(con, args.candle_table, args.col_symbol, args.col_ts,
                                    args.col_open, args.col_close,
                                    trades[0]["symbol"], trades[0]["entry_dt"],
                                    trades[0]["exit_dt"], args.ts_unit)
    if perr:
        sys.exit(f"ERROR querying candle table: {perr}")
    if not probe:
        print("WARNING: 0 candles matched the first trade — symbol/ts mismatch. Re-check flags.")

    base_cols = ["symbol", "entry", "sl", "exitp", "reason", "gross", "charges", "net",
                 "total_candles"]
    cp_cols = []
    for N in CHECKPOINTS:
        cp_cols += [f"alive_c{N}", f"close_at_c{N}", f"open_at_c{N}1"]
    out_cols = base_cols + cp_cols

    n_matched = 0
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_cols)
        w.writeheader()
        for i, t in enumerate(trades):
            rows, err = fetch_minute_rows(con, args.candle_table, args.col_symbol, args.col_ts,
                                          args.col_open, args.col_close,
                                          t["symbol"], t["entry_dt"], t["exit_dt"], args.ts_unit)
            if err:
                sys.exit(f"ERROR on trade {i}: {err}")
            if rows:
                n_matched += 1
            total_candles = round((t["exit_dt"] - t["entry_dt"]).total_seconds() / 60.0 / args.candle_min, 1)
            row = {"symbol": t["symbol"], "entry": t["entry"], "sl": t["sl"],
                   "exitp": t["exitp"], "reason": t["reason"], "gross": t["gross"],
                   "charges": t["charges"], "net": t["net"], "total_candles": total_candles}
            row.update(compute(t, rows or [], args.candle_min))
            w.writerow(row)
            if (i + 1) % 200 == 0:
                print(f"  ...{i + 1}/{len(trades)} ({n_matched} matched)")

    print(f"\nDone. {n_matched}/{len(trades)} trades had matching candles.")
    print(f"Wrote {args.out}")
    if n_matched == 0:
        print("NOTE: nothing matched — schema/format mismatch, not a real result.")
    else:
        print("\nNext: upload the *_priceN.csv files and I'll run the scale-in simulation")
        print("with REAL checkpoint prices — net P&L AND drawdown, candle 8/10/11/12 swept.")


if __name__ == "__main__":
    main()