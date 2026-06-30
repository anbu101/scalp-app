#!/usr/bin/env python3
"""
extract_mae.py  —  SCALP_V5 Maximum Adverse Excursion (MAE) extractor

PURPOSE
  For each closed trade in a per-trade backtest CSV, replay the option's 3-min
  candles between entry and exit and record how far underwater the trade went,
  candle by candle. Long option buying => "adverse" = price BELOW entry.

  Emits an augmented CSV with, per trade:
    mae_pts           worst (entry - low) over the whole hold, in premium points
    mae_pts_byN       worst adverse excursion within the first N candles
                      (N = 1,2,3,4,5,7,10) — the columns that decide whether a
                      weakness-gated early exit would have killed the winners.
    low_by_candleN    the running low watermark at candle N (debug/inspection)

SAFETY
  * READ-ONLY. Opens the DB with SQLite URI mode=ro. Never writes to the DB.
  * Touches nothing in the live trading path. Standalone — no app imports.
  * Self-contained: stdlib only (sqlite3, csv, argparse, datetime).

USAGE
  1) DISCOVERY (run this first — it inspects the DB and tells us the schema):
       python3 extract_mae.py --db /path/to/your.db --discover

  2) EXTRACTION (after we confirm table/column names from discovery):
       python3 extract_mae.py \
         --db /path/to/your.db \
         --trades 2023.csv \
         --out 2023_with_mae.csv \
         --candle-table CANDLE_TABLE \
         --col-symbol SYMBOL_COL \
         --col-ts TS_COL \
         --col-low LOW_COL \
         [--col-high HIGH_COL] [--col-close CLOSE_COL] \
         [--ts-unit s|ms|iso] [--year 2023] [--candle-seconds 180]

  The trades CSV is the Scalp Terminal per-trade export (the file with the
  TRADES block: Symbol, Entry Time, Entry, SL, TP, Exit Time, Exit, Reason,...).
"""

import argparse
import csv
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

# IST is fixed +5:30 in this system.
IST = timezone(timedelta(hours=5, minutes=30))
EARLY_NS = [1, 2, 3, 4, 5, 7, 10]


# ---------------------------------------------------------------------------
# DB helpers (read-only)
# ---------------------------------------------------------------------------
def open_ro(db_path):
    if not os.path.exists(db_path):
        sys.exit(f"ERROR: DB not found at {db_path}")
    # mode=ro guarantees we cannot write. immutable=0 so WAL readers still see latest.
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def discover(con):
    cur = con.cursor()
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    print("=" * 78)
    print("DB DISCOVERY — tables and columns (read-only)")
    print("=" * 78)
    candle_like = []
    for t in tables:
        try:
            cols = cur.execute(f"PRAGMA table_info('{t}')").fetchall()
        except sqlite3.DatabaseError:
            continue
        colnames = [c["name"] for c in cols]
        # heuristic: a candle/ohlc table has low + (open/high/close) and a ts + a symbol-ish col
        low = [c for c in colnames if c.lower() in ("low", "l", "low_price")]
        has_ohlc = any(c.lower() in ("open", "high", "close", "o", "h", "c") for c in colnames)
        ts = [c for c in colnames if c.lower() in
              ("ts", "timestamp", "time", "date", "datetime", "candle_ts", "epoch", "dt")]
        sym = [c for c in colnames if c.lower() in
               ("symbol", "tradingsymbol", "instrument", "instrument_token", "token", "name")]
        rowcount = cur.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
        flag = ""
        if low and has_ohlc and ts:
            candle_like.append(t)
            flag = "   <-- looks like a CANDLE/OHLC table"
        print(f"\nTABLE {t}  ({rowcount:,} rows){flag}")
        print("  columns: " + ", ".join(colnames))
        if low:    print("  low-ish:    " + ", ".join(low))
        if ts:     print("  ts-ish:     " + ", ".join(ts))
        if sym:    print("  symbol-ish: " + ", ".join(sym))

    print("\n" + "=" * 78)
    if candle_like:
        print("Likely candle table(s):", ", ".join(candle_like))
        # show a couple of sample rows from the best candidate
        t = candle_like[0]
        print(f"\nSample rows from '{t}':")
        for r in cur.execute(f"SELECT * FROM '{t}' LIMIT 3").fetchall():
            print("  " + dict(r).__repr__())
    else:
        print("No obvious candle table found by heuristic — inspect the list above")
        print("and tell me which table holds 3-min option OHLC.")
    print("=" * 78)
    print("\nNEXT: share this output. I'll map --candle-table / --col-* flags for"
          "\nthe extraction run. Nothing was written; this was read-only.")


# ---------------------------------------------------------------------------
# Trades CSV parsing (Scalp Terminal per-trade export)
# ---------------------------------------------------------------------------
def parse_trade_ts(s, year):
    """Entry/Exit time look like  '03/01, 12:21'  (DD/MM, HH:MM), no year/secs."""
    s = s.strip().strip('"')
    dm, hm = s.split(",")
    d, mo = dm.strip().split("/")
    h, mi = hm.strip().split(":")
    return datetime(year, int(mo), int(d), int(h), int(mi), tzinfo=IST)


def load_trades(path, year):
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    ti = next((i for i, r in enumerate(rows) if r and r[0] == "TRADES"), None)
    if ti is None:
        sys.exit("ERROR: no TRADES block found in trades CSV.")
    header = rows[ti + 1]
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
        if x < e:                       # year rollover safety (rare)
            x += timedelta(days=365)
        out.append(dict(
            symbol=sym.strip(), entry=float(entry),
            sl=float(sl) if sl else None,
            reason=reason.strip(), net=float(net),
            entry_dt=e, exit_dt=x,
        ))
    return out, header


# ---------------------------------------------------------------------------
# Timestamp conversion for the candle table
# ---------------------------------------------------------------------------
def to_db_ts(dt, ts_unit):
    """Convert an aware datetime to the DB's stored representation for WHERE clauses."""
    if ts_unit == "s":
        return int(dt.timestamp())
    if ts_unit == "ms":
        return int(dt.timestamp() * 1000)
    if ts_unit == "iso":
        # store as naive-IST 'YYYY-MM-DD HH:MM:SS' (common in these stores)
        return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
    raise ValueError(ts_unit)


def db_ts_to_dt(v, ts_unit):
    if ts_unit == "s":
        return datetime.fromtimestamp(int(v), IST)
    if ts_unit == "ms":
        return datetime.fromtimestamp(int(v) / 1000, IST)
    if ts_unit == "iso":
        return datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    raise ValueError(ts_unit)


# ---------------------------------------------------------------------------
# Candle fetch + MAE computation
# ---------------------------------------------------------------------------
def fetch_candles(con, table, col_sym, col_ts, col_low, col_high, col_close,
                  symbol, start_dt, end_dt, ts_unit):
    cur = con.cursor()
    lo = to_db_ts(start_dt - timedelta(minutes=1), ts_unit)   # 1-min slack
    hi = to_db_ts(end_dt + timedelta(minutes=1), ts_unit)
    sel = f'"{col_ts}" AS ts, "{col_low}" AS low'
    if col_high:  sel += f', "{col_high}" AS high'
    if col_close: sel += f', "{col_close}" AS close'
    q = (f'SELECT {sel} FROM "{table}" '
         f'WHERE "{col_sym}" = ? AND "{col_ts}" >= ? AND "{col_ts}" <= ? '
         f'ORDER BY "{col_ts}" ASC')
    try:
        rows = cur.execute(q, (symbol, lo, hi)).fetchall()
    except sqlite3.DatabaseError as e:
        return None, str(e)
    out = []
    for r in rows:
        try:
            dt = db_ts_to_dt(r["ts"], ts_unit)
        except Exception:
            continue
        out.append((dt, float(r["low"])))
    return out, None


def compute_mae(trade, candles, candle_seconds):
    """Return dict of MAE metrics. Adverse = entry - low (long option)."""
    entry = trade["entry"]
    res = {"candles_found": len(candles), "mae_pts": "", "mae_pct": ""}
    for n in EARLY_NS:
        res[f"mae_pts_by{n}"] = ""
        res[f"low_by_candle{n}"] = ""
    if not candles:
        return res
    # candles already sorted; index 0 = entry candle. Walk and track running low.
    running_low = None
    worst_all = None
    by_n_low = {}
    for idx, (dt, low) in enumerate(candles):
        candle_no = idx + 1
        running_low = low if running_low is None else min(running_low, low)
        adverse = entry - running_low          # positive => underwater
        worst_all = adverse if worst_all is None else max(worst_all, adverse)
        if candle_no in EARLY_NS:
            by_n_low[candle_no] = (running_low, adverse)
        # also capture the largest N if hold shorter than 10
    res["mae_pts"] = round(worst_all, 2)
    res["mae_pct"] = round(100.0 * worst_all / entry, 2) if entry else ""
    # fill early-N columns; if the trade closed before candle N, carry last known low
    last_low = None
    last_adv = None
    for n in EARLY_NS:
        if n in by_n_low:
            last_low, last_adv = by_n_low[n]
        if last_low is not None:
            res[f"low_by_candle{n}"] = round(last_low, 2)
            res[f"mae_pts_by{n}"] = round(last_adv, 2)
    return res


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Read-only MAE extractor for SCALP_V5 backtest trades.")
    ap.add_argument("--db", required=True, help="path to the SQLite candle DB (opened read-only)")
    ap.add_argument("--discover", action="store_true", help="inspect DB schema and exit")
    ap.add_argument("--trades", help="per-trade backtest CSV (TRADES block export)")
    ap.add_argument("--out", help="output CSV path")
    ap.add_argument("--year", type=int, help="calendar year of the trades CSV (e.g. 2023). For 2026 H1 use 2026.")
    ap.add_argument("--candle-table")
    ap.add_argument("--col-symbol")
    ap.add_argument("--col-ts")
    ap.add_argument("--col-low")
    ap.add_argument("--col-high", default=None)
    ap.add_argument("--col-close", default=None)
    ap.add_argument("--ts-unit", choices=["s", "ms", "iso"], default="s",
                    help="how candle timestamps are stored: epoch seconds / millis / ISO text")
    ap.add_argument("--candle-seconds", type=int, default=180, help="candle width in seconds (3-min = 180)")
    ap.add_argument("--limit", type=int, default=0, help="process only first N trades (smoke test)")
    args = ap.parse_args()

    con = open_ro(args.db)

    if args.discover:
        discover(con)
        return

    # Extraction mode — require the mapping flags.
    missing = [f for f in ("trades", "out", "year", "candle_table", "col_symbol", "col_ts", "col_low")
               if not getattr(args, f.replace("-", "_"))]
    if missing:
        sys.exit("ERROR: extraction needs: --" + ", --".join(m.replace("_", "-") for m in missing) +
                 "\nRun with --discover first to find the right table/column names.")

    trades, header = load_trades(args.trades, args.year)
    if args.limit:
        trades = trades[:args.limit]
    print(f"Loaded {len(trades)} trades from {args.trades} (year {args.year}).")

    # quick probe: does the symbol/ts match anything at all?
    probe, perr = fetch_candles(con, args.candle_table, args.col_symbol, args.col_ts,
                                args.col_low, args.col_high, args.col_close,
                                trades[0]["symbol"], trades[0]["entry_dt"],
                                trades[0]["exit_dt"], args.ts_unit)
    if perr:
        sys.exit(f"ERROR querying candle table: {perr}\nCheck --candle-table / --col-* names via --discover.")
    if not probe:
        print("WARNING: 0 candles matched the first trade. Likely a symbol-format or ts-unit mismatch.")
        print(f"  trade symbol = {trades[0]['symbol']!r}")
        print(f"  window       = {trades[0]['entry_dt']} -> {trades[0]['exit_dt']}")
        print("  -> Re-run --discover, check the symbol column's actual values and the ts unit.")
        # keep going so the output still lists which trades matched (all blank here)

    out_cols = (["symbol", "entry", "sl", "reason", "net", "candles_found", "mae_pts", "mae_pct"]
                + [f"mae_pts_by{n}" for n in EARLY_NS]
                + [f"low_by_candle{n}" for n in EARLY_NS])

    n_matched = 0
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_cols)
        w.writeheader()
        for i, t in enumerate(trades):
            candles, err = fetch_candles(con, args.candle_table, args.col_symbol, args.col_ts,
                                         args.col_low, args.col_high, args.col_close,
                                         t["symbol"], t["entry_dt"], t["exit_dt"], args.ts_unit)
            if err:
                sys.exit(f"ERROR on trade {i}: {err}")
            m = compute_mae(t, candles or [], args.candle_seconds)
            if m["candles_found"]:
                n_matched += 1
            row = {"symbol": t["symbol"], "entry": t["entry"], "sl": t["sl"],
                   "reason": t["reason"], "net": t["net"]}
            row.update(m)
            w.writerow(row)
            if (i + 1) % 200 == 0:
                print(f"  ...{i + 1}/{len(trades)} processed ({n_matched} matched candles)")

    print(f"\nDone. {n_matched}/{len(trades)} trades had matching candles.")
    print(f"Wrote {args.out}")
    if n_matched == 0:
        print("NOTE: nothing matched — this is a schema/format mismatch, not a real result.")
        print("      Send me the --discover output and a sample candle row and I'll fix the flags.")
    else:
        print("\nNext: upload the output CSV and I'll run the winner/loser MAE split —")
        print("specifically 'how many top-10 winners were underwater at candle 4'.")


if __name__ == "__main__":
    main()