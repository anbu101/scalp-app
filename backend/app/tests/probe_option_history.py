#!/usr/bin/env python3
"""
probe_option_history.py — ONE-SHOT diagnostic.

Question it answers: does Zerodha's historical_data return clean 1-minute
candles for an OPTION contract going back to yesterday? That single fact
decides whether the market_timeline warmup-backfill is viable.

It makes EXACTLY ONE historical_data call. Do not loop it.

Run from the repo root so `backend/app/...` imports resolve:

    cd /Users/anbu/dev/scalp-app
    python3 probe_option_history.py

It authenticates the SAME way the app does (same api_key via load_credentials,
same data access token file), and resolves the instrument token from the same
instruments.csv the app uses. No secrets are typed or printed.
"""

import sys
from datetime import date, timedelta

# --- make the app package importable when run from repo root ---
sys.path.insert(0, "backend")

# The symbol that bit you (06-23 weekly). Change ONLY the symbol if you want a
# different contract — everything else resolves from it.
TARGET_SYMBOL = "NIFTY2662324150PE"

# How many days back to probe. 2 calendar days guarantees at least yesterday's
# full session even if today is a Monday-ish gap. Keep this small — this is a
# probe, not the real backfill.
DAYS_BACK = 2


def main() -> int:
    # ---- 1. Credentials, loaded exactly like the app ----
    try:
        from app.brokers.zerodha_manager import load_access_token
    except Exception as e:
        print(f"[PROBE][FATAL] could not import load_access_token: {e!r}")
        print("  Are you running from the repo root (/Users/anbu/dev/scalp-app)?")
        return 1

    # api_key via the app's credential loader (same source the manager uses).
    api_key = None
    try:
        # load_credentials is imported INTO zerodha_manager; grab it from there
        # so we use the identical resolution the app uses.
        from app.brokers.zerodha_manager import load_credentials  # type: ignore
        creds = load_credentials() or {}
        api_key = creds.get("api_key")
    except Exception as e:
        print(f"[PROBE][WARN] load_credentials import/exec failed: {e!r}")

    if not api_key:
        print("[PROBE][FATAL] no api_key from load_credentials() — cannot continue.")
        return 1

    data_token = load_access_token("data")
    if not data_token:
        print("[PROBE][FATAL] no data access token (access_token_data.json missing/empty).")
        print("  The app needs a live data session for this probe to work.")
        return 1

    print(f"[PROBE] api_key loaded: {'yes' if api_key else 'no'} "
          f"(…{api_key[-4:] if api_key else ''})")
    print(f"[PROBE] data token loaded: yes (…{data_token[-4:]})")

    # ---- 2. Resolve the instrument token from instruments.csv ----
    try:
        from app.fetcher.zerodha_instruments import INSTRUMENTS_PATH
        import pandas as pd
    except Exception as e:
        print(f"[PROBE][FATAL] could not import instruments path / pandas: {e!r}")
        return 1

    if not INSTRUMENTS_PATH.exists():
        print(f"[PROBE][FATAL] instruments.csv not found at {INSTRUMENTS_PATH}")
        return 1

    df = pd.read_csv(INSTRUMENTS_PATH)
    row = df[df["tradingsymbol"] == TARGET_SYMBOL]
    if row.empty:
        print(f"[PROBE][FATAL] {TARGET_SYMBOL} not found in instruments.csv")
        print("  (Maybe the dump rotated. Pick a currently-listed weekly strike.)")
        return 1

    token = int(row.iloc[0]["instrument_token"])
    print(f"[PROBE] resolved {TARGET_SYMBOL} -> token={token}")

    # ---- 3. Build an authenticated KiteConnect (data session) ----
    try:
        from kiteconnect import KiteConnect
    except Exception as e:
        print(f"[PROBE][FATAL] kiteconnect import failed: {e!r}")
        return 1

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(data_token)

    # ---- 4. EXACTLY ONE historical_data call ----
    end_date = date.today()
    start_date = end_date - timedelta(days=DAYS_BACK)
    print(f"[PROBE] historical_data token={token} {start_date} -> {end_date} interval=minute")

    try:
        candles = kite.historical_data(
            instrument_token=token,
            from_date=start_date,
            to_date=end_date,
            interval="minute",
        )
    except Exception as e:
        print(f"[PROBE][RESULT] historical_data FAILED: {e!r}")
        print("  If this is a permissions/subscription error, 1-min option")
        print("  history may not be available on your plan — that changes the")
        print("  whole backfill design. Note the exact error.")
        return 1

    # ---- 5. Report ----
    n = len(candles) if candles else 0
    print(f"[PROBE][RESULT] candles returned: {n}")
    if n == 0:
        print("  ZERO candles — 1-min option history not available for this")
        print("  contract/window. Backfill-from-Zerodha approach is NOT viable")
        print("  as-is; we'd need a different strategy.")
        return 0

    first, last = candles[0], candles[-1]
    print(f"[PROBE]   first: {first.get('date')}  O={first.get('open')} "
          f"H={first.get('high')} L={first.get('low')} C={first.get('close')} "
          f"V={first.get('volume')}")
    print(f"[PROBE]   last : {last.get('date')}  O={last.get('open')} "
          f"H={last.get('high')} L={last.get('low')} C={last.get('close')} "
          f"V={last.get('volume')}")

    # Quick per-day count so you can see if YESTERDAY specifically is covered.
    by_day = {}
    for c in candles:
        d = c.get("date")
        key = d.date().isoformat() if hasattr(d, "date") else str(d)[:10]
        by_day[key] = by_day.get(key, 0) + 1
    print("[PROBE]   candles per day:")
    for k in sorted(by_day):
        print(f"             {k}: {by_day[k]}  "
              f"({'looks like a full session' if by_day[k] >= 300 else 'SPARSE — partial/illiquid'})")

    print()
    print("[PROBE] INTERPRETATION:")
    print("  • ~375/day with continuous timestamps  -> backfill is VIABLE.")
    print("  • Sparse / big gaps / <300 per day      -> illiquid history;")
    print("    near-ATM strikes may still be fine, far-OTM may not.")
    print("  • Remember: this was ONE contract. Liquid ATM strikes return")
    print("    more complete data than far-OTM ones.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())