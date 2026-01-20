#!/usr/bin/env python3

"""
One-time Zerodha instrument dump generator.

This script fetches:
- NFO instruments (options/futures)
- NSE instruments (cash + INDICES)

and writes a single combined CSV file.

Run this ONCE, then place the CSV at INSTRUMENTS_PATH
used by your application.
"""

from kiteconnect import KiteConnect
import pandas as pd
import os
import sys


# -----------------------------
# CONFIG — FILL THESE
# -----------------------------
API_KEY = "ak1k5sv8byhjv53i"
ACCESS_TOKEN = "WEMtknIQMGcSPk4eOtIzmeGIo7DYk3uW"

OUTPUT_FILE = "zerodha_instruments.csv"


def main():
    if API_KEY.startswith("YOUR_") or ACCESS_TOKEN.startswith("YOUR_"):
        print("❌ ERROR: Please set API_KEY and ACCESS_TOKEN in the script")
        sys.exit(1)

    print("🔐 Connecting to Zerodha...")

    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(ACCESS_TOKEN)

    print("📥 Fetching NFO instruments...")
    nfo = pd.DataFrame(kite.instruments("NFO"))

    print("📥 Fetching NSE instruments (includes indices)...")
    nse = pd.DataFrame(kite.instruments("NSE"))

    df = pd.concat([nfo, nse], ignore_index=True)

    print(f"💾 Writing CSV → {OUTPUT_FILE}")
    df.to_csv(OUTPUT_FILE, index=False)

    # -----------------------------
    # VALIDATION
    # -----------------------------
    index_rows = df[df["segment"].isin(["INDICES", "BSE-INDICES"])]

    print("\n✅ Instrument dump summary")
    print("Total instruments :", len(df))
    print("Index instruments :", len(index_rows))

    if index_rows.empty:
        print("❌ ERROR: No index instruments found!")
        sys.exit(2)

    print("\n📊 Index instruments found:")
    print(index_rows[["tradingsymbol", "instrument_token", "segment"]])

    print("\n✅ SUCCESS: Instrument dump generated correctly")


if __name__ == "__main__":
    main()
