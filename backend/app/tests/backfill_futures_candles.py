#!/usr/bin/env python3
"""
backfill_futures_candles.py v3 — Multi-contract BANKNIFTY Futures Backfill
============================================================================

What this script does:
    1. CLEARS all existing futures_candles data (with confirmation prompt)
    2. For each calendar month in the lookback window, resolves the correct
       monthly BANKNIFTY FUT contract
    3. Fetches 3m candles and inserts with the correct symbol per month

Contract resolution — three tiers:
    Tier 1: kite.instruments("NFO") — retains recently expired contracts
            (typically last 4-8 weeks after expiry). Good for Mar/Feb.
    Tier 2: MANUAL_TOKENS — hardcode tokens you find manually (see below)
    Tier 3: Skip + print instructions for that month

Finding tokens for expired contracts:
    Method A: Zerodha Kite web chart
              Open chart for BANKNIFTY FUT, change to the expired contract,
              look at the browser URL — contains instrument_token=XXXXXXX
    Method B: Historical Kite Connect API explorer
              https://kite.trade/docs/connect/v3/historical/
    Method C: Save tokens from your running engine BEFORE they expire
              (we'll add a token-saver to the engine after this backfill)

Usage:
    python3 backfill_futures_candles.py --months 4
    python3 backfill_futures_candles.py --months 4 --dry-run
    python3 backfill_futures_candles.py --months 1          # April only, no clear
"""

import sys
import sqlite3
import argparse
from pathlib import Path
from datetime import date, datetime, timedelta

# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────

APP_HOME = Path.home() / ".scalp-app"
DB_PATH  = APP_HOME / "data" / "app.db"

# ─────────────────────────────────────────────────────────────────────────────
# MANUAL TOKENS — fill these in as you find them
# ─────────────────────────────────────────────────────────────────────────────
# Format: (year, month): (instrument_token, tradingsymbol, expiry_date)
#
# HOW TO FIND A TOKEN:
#   1. Open https://kite.zerodha.com/chart/ext/ciq/NFO/BANKNIFTY26MARFUT/XXXXXX
#      The token is the last number in the URL.
#   2. Or: kite.ltp("NFO:BANKNIFTY26MARFUT") returns {token: price} for
#      contracts still in the active instrument master.
#   3. Once we add the token-logger to the live engine, this fills itself.

MANUAL_TOKENS = {
    # (year, month): (token,     symbol,               expiry)
    (2026, 4): (17072130, "BANKNIFTY26APRFUT", date(2026, 4, 28)),
    # Add confirmed tokens below:
    # (2026, 3): (XXXXXXX, "BANKNIFTY26MARFUT", date(2026, 3, 26)),
    # (2026, 2): (XXXXXXX, "BANKNIFTY26FEBFUT", date(2026, 2, 26)),
    # (2026, 1): (XXXXXXX, "BANKNIFTY26JANFUT", date(2026, 1, 29)),
}

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

TIMEFRAME     = "3m"
KITE_INTERVAL = "3minute"
CHUNK_DAYS    = 60
UNDERLYING    = "BANKNIFTY"

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def last_thursday(year: int, month: int) -> date:
    if month == 12:
        last = date(year+1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month+1, 1) - timedelta(days=1)
    while last.weekday() != 3:
        last -= timedelta(days=1)
    return last


def month_list(months_back: int):
    today  = date.today()
    result = []
    seen   = set()
    for m in range(months_back, -1, -1):
        t   = date(today.year, today.month, 1) - timedelta(days=m*28)
        key = (t.year, t.month)
        if key not in seen:
            seen.add(key)
            result.append(key)
    return sorted(result)


def month_date_range(year: int, month: int):
    """Full calendar month, capped at today."""
    from_d = date(year, month, 1)
    to_d   = (date(year, month+1, 1) if month < 12 else date(year+1, 1, 1)) - timedelta(days=1)
    return from_d, min(to_d, date.today())


def search_nfo_dump(df, year: int, month: int):
    """Search the NFO instrument DataFrame for this month's BANKNIFTY FUT."""
    expiry = last_thursday(year, month)
    for offset in range(-2, 3):
        check = expiry + timedelta(days=offset)
        mask  = (
            (df.get("segment", df.get("exchange_token", None)) == "NFO-FUT")
            & (df["name"]   == UNDERLYING)
            & (df["expiry"] == check)
        )
        if mask is None:
            break
        hits = df[mask]
        if not hits.empty:
            row = hits.iloc[0]
            return (int(row["instrument_token"]), str(row["tradingsymbol"]), check)
    return None, None, None


def resolve(df, year: int, month: int):
    """Resolve contract: Tier1=NFO dump, Tier2=MANUAL_TOKENS."""
    # Tier 1
    token, sym, exp = search_nfo_dump(df, year, month)
    if token:
        return token, sym, exp, "NFO-dump"
    # Tier 2
    entry = MANUAL_TOKENS.get((year, month))
    if entry:
        return entry[0], entry[1], entry[2], "MANUAL_TOKENS"
    return None, None, None, None


def fetch_chunks(kite, token: int, from_d: date, to_d: date):
    candles = []
    cursor  = from_d
    while cursor <= to_d:
        end = min(cursor + timedelta(days=CHUNK_DAYS), to_d)
        print(f"      Fetching {cursor} -> {end} ...", end=" ", flush=True)
        try:
            data = kite.historical_data(
                instrument_token=token,
                from_date=cursor,
                to_date=end,
                interval=KITE_INTERVAL,
            )
            print(f"{len(data)} candles")
            candles.extend(data)
        except Exception as e:
            print(f"FAILED: {e}")
        cursor = end + timedelta(days=1)
    return candles


def insert(conn, symbol: str, candles: list, dry_run: bool) -> int:
    if dry_run:
        return len(candles)
    n = 0
    for c in candles:
        ts = int(c["date"].timestamp())
        cur = conn.execute(
            "INSERT OR IGNORE INTO futures_candles "
            "(symbol, timeframe, ts, open, high, low, close) "
            "VALUES (?,?,?,?,?,?,?)",
            (symbol, TIMEFRAME, ts,
             c["open"], c["high"], c["low"], c["close"]),
        )
        n += cur.rowcount
    conn.commit()
    return n

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--months",   type=int, default=4)
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--no-clear", action="store_true",
                        help="Skip the DB clear step (append only)")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}")
        sys.exit(1)

    # ── Zerodha ──────────────────────────────────────────────────────────────
    print("Connecting to Zerodha...")
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from app.brokers.zerodha_manager import ZerodhaManager
        broker = ZerodhaManager()
        kite   = broker.get_data_kite() or broker.get_trade_kite()
        if not kite:
            print("Not connected — log in via Scalp Terminal first.")
            sys.exit(1)
        print(f"  Connected as {kite.profile()['user_name']}")
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # ── NFO instrument dump (Tier 1) ─────────────────────────────────────────
    import pandas as pd
    print("Fetching NFO instrument master...")
    try:
        raw = kite.instruments("NFO")
        df  = pd.DataFrame(raw)
        df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce").dt.date
        # Normalise 'name' column — varies by kiteconnect version
        if "name" not in df.columns and "underlying" in df.columns:
            df["name"] = df["underlying"]
        bn = df[(df.get("name", pd.Series()) == UNDERLYING) &
                (df.get("instrument_type", pd.Series()) == "FUT")]
        print(f"  {len(df)} NFO instruments, {len(bn)} BANKNIFTY FUT contracts found")
        if not bn.empty:
            print("  Contracts in dump:")
            for _, row in bn.sort_values("expiry").iterrows():
                print(f"    {row['tradingsymbol']:30s} expiry={row['expiry']}  token={row['instrument_token']}")
    except Exception as e:
        print(f"  NFO dump failed: {e}")
        df = pd.DataFrame()

    # ── DB connection ─────────────────────────────────────────────────────────
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS futures_candles (
               symbol TEXT, timeframe TEXT, ts INTEGER,
               open REAL, high REAL, low REAL, close REAL,
               PRIMARY KEY (symbol, timeframe, ts))"""
    )
    conn.commit()

    # ── Current DB state ──────────────────────────────────────────────────────
    existing = conn.execute(
        "SELECT symbol, COUNT(*) as n, MIN(ts) as mn, MAX(ts) as mx "
        "FROM futures_candles WHERE timeframe=? GROUP BY symbol ORDER BY mn",
        (TIMEFRAME,),
    ).fetchall()

    print("\nCurrent DB contents (3m):")
    if existing:
        for sym, n, mn, mx in existing:
            print(f"  {sym}: {n} candles  "
                  f"{datetime.fromtimestamp(mn):%Y-%m-%d} -> "
                  f"{datetime.fromtimestamp(mx):%Y-%m-%d}")
    else:
        print("  (empty)")

    # ── Clear prompt ──────────────────────────────────────────────────────────
    if not args.no_clear and not args.dry_run and existing:
        print()
        print("This script will DELETE all existing futures_candles data")
        print("and re-fetch with correct monthly contract symbols.")
        ans = input("Type 'yes' to continue, anything else to abort: ").strip().lower()
        if ans != "yes":
            print("Aborted.")
            sys.exit(0)
        conn.execute("DELETE FROM futures_candles WHERE timeframe=?", (TIMEFRAME,))
        conn.commit()
        print(f"  Cleared all {TIMEFRAME} candles from futures_candles.")

    # ── Month-by-month fetch ──────────────────────────────────────────────────
    months   = month_list(args.months)
    skipped  = []
    total_c  = 0
    total_in = 0

    print(f"\nProcessing {len(months)} months "
          f"({months[0][0]}-{months[0][1]:02d} -> {months[-1][0]}-{months[-1][1]:02d})")

    for year, month in months:
        token, sym, expiry, source = resolve(df, year, month)

        if token is None:
            skipped.append((year, month))
            print(f"\n  {year}-{month:02d}: *** No token found — SKIPPED ***")
            continue

        from_d, to_d = month_date_range(year, month)
        if from_d > date.today():
            continue

        print(f"\n  {year}-{month:02d}: {sym}  expiry={expiry}  "
              f"token={token}  [{source}]")
        print(f"    Range: {from_d} -> {to_d}")

        if args.dry_run:
            print("    [DRY RUN] Would fetch and insert")
            continue

        candles = fetch_chunks(kite, token, from_d, to_d)
        if not candles:
            print("    No candles returned")
            continue

        n_in = insert(conn, sym, candles, dry_run=False)
        total_c  += len(candles)
        total_in += n_in
        print(f"    Fetched {len(candles)}  |  Inserted {n_in} new rows")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "─" * 60)
    print(f"Candles fetched  : {total_c}")
    print(f"New rows inserted: {total_in}")

    if skipped:
        print(f"\nSkipped (token unknown): "
              f"{', '.join(f'{y}-{m:02d}' for y, m in skipped)}")
        print()
        print("To fix skipped months, find the token and add to MANUAL_TOKENS:")
        for y, m in skipped:
            mon  = date(y, m, 1).strftime("%b").upper()
            sym  = f"BANKNIFTY{str(y)[-2:]}{mon}FUT"
            exp  = last_thursday(y, m)
            print(f"  ({y}, {m}): (TOKEN, '{sym}', date({y}, {m}, {exp.day})),")
        print()
        print("Quick way to find the token while the engine is running:")
        print("  grep 'FUTURES_SUBSCRIBED' ~/.scalp-app/logs/*.log")
        print("  (Roll month before it expires to capture next month's token)")

    if not args.dry_run:
        print("\nUpdated DB:")
        updated = conn.execute(
            "SELECT symbol, COUNT(*) as n, MIN(ts) as mn, MAX(ts) as mx "
            "FROM futures_candles WHERE timeframe=? GROUP BY symbol ORDER BY mn",
            (TIMEFRAME,),
        ).fetchall()
        for sym, n, mn, mx in updated:
            print(f"  {sym}: {n} candles  "
                  f"{datetime.fromtimestamp(mn):%Y-%m-%d} -> "
                  f"{datetime.fromtimestamp(mx):%Y-%m-%d}")

    conn.close()
    print()
    print("Next step: run the analysis script")
    print("  python3 analyse_supertrend_flips.py")


if __name__ == "__main__":
    main()