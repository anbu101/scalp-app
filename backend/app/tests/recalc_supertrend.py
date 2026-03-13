#!/usr/bin/env python3
"""
recalc_supertrend.py
====================
Standalone backfill script — recalculates SuperTrend(10,2) and RSI(14)
using the CORRECTED Wilder algorithm for all rows in futures_candles.

Modes
-----
  --mode csv   : Write a CSV of recalculated values for visual comparison
                 in Excel / Zerodha chart. Does NOT touch the DB. (default)
  --mode update: UPDATE the DB rows in-place after confirmation prompt.

Usage
-----
  python recalc_supertrend.py                        # CSV to stdout
  python recalc_supertrend.py --csv out.csv          # CSV to file
  python recalc_supertrend.py --mode update          # patch DB
  python recalc_supertrend.py --db /path/to/app.db   # explicit DB path

DB path auto-detection order
-----------------------------
  1. --db argument
  2. SCALP_DB_PATH env var
  3. ~/Library/Application Support/com.scalp.app/scalp.db  (macOS Tauri default)
  4. ~/.local/share/com.scalp.app/scalp.db                 (Linux)
  5. %APPDATA%/com.scalp.app/scalp.db                      (Windows)
"""

import argparse
import csv
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import mean

# India Standard Time = UTC + 5:30
IST = timezone(timedelta(hours=5, minutes=30))

def ts_to_ist(ts):
    """Convert Unix timestamp to IST datetime string e.g. 10-Mar-2026 09:15"""
    try:
        return datetime.fromtimestamp(int(ts), tz=IST).strftime("%d-%b-%Y %H:%M")
    except Exception:
        return ""


# ──────────────────────────────────────────────────────────────────────────────
# DB path resolution
# ──────────────────────────────────────────────────────────────────────────────

def find_db() -> Path:
    candidates = []

    if sys.platform == "darwin":
        candidates.append(
            Path.home() / "Library" / "Application Support" / "com.scalp.app" / "scalp.db"
        )
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        candidates.append(Path(appdata) / "com.scalp.app" / "scalp.db")
    else:
        candidates.append(
            Path.home() / ".local" / "share" / "com.scalp.app" / "scalp.db"
        )

    # Also check common dev locations
    candidates += [
        Path("scalp.db"),
        Path("app.db"),
        Path("data/scalp.db"),
    ]

    for p in candidates:
        if p.exists():
            return p

    raise FileNotFoundError(
        "Could not auto-detect DB. Pass --db /path/to/scalp.db explicitly.\n"
        f"Tried: {[str(c) for c in candidates]}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Corrected SuperTrend + RSI calculator
# Mirrors indicator_bundle.py exactly — standalone, no imports needed.
# ──────────────────────────────────────────────────────────────────────────────

class Recalculator:
    def __init__(self, st_length=10, st_mult=2.0, rsi_length=14, rsi_smooth=3):
        self.st_length  = st_length
        self.st_mult    = st_mult
        self.rsi_length = rsi_length
        self.rsi_smooth = rsi_smooth

        # SuperTrend state
        self.atr          = None
        self.prev_close   = None
        self.final_upper  = None
        self.final_lower  = None
        self.supertrend   = None
        self._atr_seed    = []

        # RSI state (Wilder's RMA)
        self._rsi_avg_gain   = None
        self._rsi_avg_loss   = None
        self._rsi_seed_gains = []
        self._rsi_seed_losses = []
        self._rsi_values     = []   # deque for rsi_smooth SMA

        # BB state
        self._bb_closes = []

    def feed(self, ts, open_, high, low, close):
        """Feed one candle. Returns dict of recalculated indicator values."""

        result = {
            "ts":           ts,
            "open":         open_,
            "high":         high,
            "low":          low,
            "close":        close,
            "supertrend":   None,
            "st_direction": None,
            "rsi_raw":      None,
            "rsi_smooth":   None,
            "bb_upper":     None,
            "bb_middle":    None,
            "bb_lower":     None,
            "bb_width":     None,
        }

        # ── Bollinger Bands ──────────────────────────────────────────────────
        self._bb_closes.append(close)
        if len(self._bb_closes) > 20:
            self._bb_closes.pop(0)

        if len(self._bb_closes) == 20:
            sma = mean(self._bb_closes)
            # Population std (matches TradingView / pstdev)
            variance = sum((x - sma) ** 2 for x in self._bb_closes) / 20
            std = variance ** 0.5
            result["bb_middle"] = round(sma, 4)
            result["bb_upper"]  = round(sma + 2 * std, 4)
            result["bb_lower"]  = round(sma - 2 * std, 4)
            result["bb_width"]  = round(result["bb_upper"] - result["bb_lower"], 4)

        # ── RSI — Wilder's RMA ───────────────────────────────────────────────
        if self.prev_close is not None:
            diff = close - self.prev_close
            gain = max(diff, 0.0)
            loss = abs(min(diff, 0.0))

            if self._rsi_avg_gain is None:
                self._rsi_seed_gains.append(gain)
                self._rsi_seed_losses.append(loss)

                if len(self._rsi_seed_gains) == self.rsi_length:
                    self._rsi_avg_gain  = mean(self._rsi_seed_gains)
                    self._rsi_avg_loss  = mean(self._rsi_seed_losses)
                    self._rsi_seed_gains  = []
                    self._rsi_seed_losses = []
            else:
                alpha = 1.0 / self.rsi_length
                self._rsi_avg_gain = alpha * gain + (1 - alpha) * self._rsi_avg_gain
                self._rsi_avg_loss = alpha * loss + (1 - alpha) * self._rsi_avg_loss

            if self._rsi_avg_gain is not None:
                if self._rsi_avg_loss == 0:
                    rsi_raw = 100.0
                else:
                    rs      = self._rsi_avg_gain / self._rsi_avg_loss
                    rsi_raw = 100.0 - (100.0 / (1.0 + rs))

                result["rsi_raw"] = round(rsi_raw, 4)

                self._rsi_values.append(rsi_raw)
                if len(self._rsi_values) > self.rsi_smooth:
                    self._rsi_values.pop(0)

                if len(self._rsi_values) == self.rsi_smooth:
                    result["rsi_smooth"] = round(mean(self._rsi_values), 4)

        # ── SuperTrend(10, 2) — corrected Wilder ATR ────────────────────────
        if self.prev_close is not None:
            tr = max(
                high - low,
                abs(high - self.prev_close),
                abs(low  - self.prev_close),
            )

            if self.atr is None:
                self._atr_seed.append(tr)
                if len(self._atr_seed) == self.st_length:
                    self.atr       = sum(self._atr_seed) / self.st_length
                    self._atr_seed = []
            else:
                self.atr = ((self.atr * (self.st_length - 1)) + tr) / self.st_length

            if self.atr is not None:
                hl2         = (high + low) / 2
                basic_upper = hl2 + self.st_mult * self.atr
                basic_lower = hl2 - self.st_mult * self.atr

                if self.final_upper is None:
                    self.final_upper = basic_upper
                    self.final_lower = basic_lower
                    self.supertrend  = (
                        basic_upper if close <= basic_upper else basic_lower
                    )
                else:
                    # Snapshot BEFORE updating bands — critical correctness fix
                    prev_fu = self.final_upper
                    prev_fl = self.final_lower

                    if basic_upper < self.final_upper or self.prev_close > self.final_upper:
                        self.final_upper = basic_upper
                    if basic_lower > self.final_lower or self.prev_close < self.final_lower:
                        self.final_lower = basic_lower

                    # Compare against SNAPSHOT values, not mutated ones
                    if self.supertrend == prev_fu:
                        self.supertrend = (
                            self.final_upper if close <= self.final_upper
                            else self.final_lower
                        )
                    else:
                        self.supertrend = (
                            self.final_lower if close >= self.final_lower
                            else self.final_upper
                        )

                result["supertrend"]   = round(self.supertrend, 4)
                result["st_direction"] = (
                    "UP" if self.supertrend == self.final_lower else "DOWN"
                )

        self.prev_close = close
        return result


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def load_candles(db_path: Path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Try to auto-detect symbol grouping
    cur.execute("""
        SELECT DISTINCT symbol FROM futures_candles ORDER BY symbol
    """)
    symbols = [r[0] for r in cur.fetchall()]
    print(f"[INFO] Found symbols: {symbols}", file=sys.stderr)

    all_candles = []
    for sym in symbols:
        cur.execute("""
            SELECT ts, open, high, low, close, symbol
            FROM futures_candles
            WHERE symbol = ? AND timeframe = '3m'
            ORDER BY ts ASC
        """, (sym,))
        rows = cur.fetchall()
        all_candles.append((sym, rows))
        print(f"[INFO] {sym}: {len(rows)} 3-min candles", file=sys.stderr)

    con.close()
    return all_candles


def recalculate(symbols_candles):
    results = []
    for sym, rows in symbols_candles:
        calc = Recalculator()
        for r in rows:
            out = calc.feed(
                ts=r["ts"],
                open_=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
            )
            out["symbol"] = sym
            results.append(out)
    return results


def write_csv(results, dest):
    fields = [
        "symbol", "datetime_ist", "ts", "open", "high", "low", "close",
        "supertrend", "st_direction",
        "rsi_raw", "rsi_smooth",
        "bb_upper", "bb_middle", "bb_lower", "bb_width",
    ]
    writer = csv.DictWriter(dest, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for r in results:
        row = dict(r)
        row["datetime_ist"] = ts_to_ist(r["ts"])
        writer.writerow(row)
    print(f"[INFO] Wrote {len(results)} rows", file=sys.stderr)


def update_db(db_path: Path, results):
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    updated = 0
    for r in results:
        if r["supertrend"] is None:
            continue
        try:
            cur.execute("""
                UPDATE futures_candles
                SET supertrend   = ?,
                    st_direction = ?,
                    rsi_smooth   = ?,
                    bb_upper     = ?,
                    bb_middle    = ?,
                    bb_lower     = ?,
                    bb_width     = ?
                WHERE symbol = ? AND ts = ?
            """, (
                r["supertrend"],
                r["st_direction"],
                r["rsi_smooth"],
                r["bb_upper"],
                r["bb_middle"],
                r["bb_lower"],
                r["bb_width"],
                r["symbol"],
                r["ts"],
            ))
            updated += cur.rowcount
        except sqlite3.OperationalError as e:
            # Column might not exist yet — gracefully skip
            print(f"[WARN] {e} (ts={r['ts']})", file=sys.stderr)

    con.commit()
    con.close()
    print(f"[INFO] Updated {updated} rows in {db_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Recalculate SuperTrend / RSI in futures_candles")
    parser.add_argument("--db",   help="Path to scalp.db", default=None)
    parser.add_argument("--csv",  help="Output CSV path (default: stdout)", default=None)
    parser.add_argument("--mode", choices=["csv", "update"], default="csv",
                        help="csv = export only (safe), update = patch DB in-place")
    args = parser.parse_args()

    # Resolve DB path
    if args.db:
        db_path = Path(args.db)
    else:
        try:
            db_path = find_db()
        except FileNotFoundError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)

    print(f"[INFO] Using DB: {db_path}", file=sys.stderr)

    symbols_candles = load_candles(db_path)
    results         = recalculate(symbols_candles)

    if args.mode == "csv":
        if args.csv:
            with open(args.csv, "w", newline="") as f:
                write_csv(results, f)
            print(f"[DONE] CSV written to {args.csv}", file=sys.stderr)
        else:
            write_csv(results, sys.stdout)

    elif args.mode == "update":
        non_null = sum(1 for r in results if r["supertrend"] is not None)
        print(f"\n[WARN] About to UPDATE {non_null} rows in {db_path}.", file=sys.stderr)
        print("[WARN] This is irreversible. Make a backup first!", file=sys.stderr)
        confirm = input("Type YES to proceed: ").strip()
        if confirm != "YES":
            print("[ABORT] Nothing changed.", file=sys.stderr)
            sys.exit(0)
        update_db(db_path, results)
        print("[DONE] DB updated.", file=sys.stderr)


if __name__ == "__main__":
    main()