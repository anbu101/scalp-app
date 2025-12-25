from pathlib import Path
from datetime import datetime, timedelta
import json
import pandas as pd

from kiteconnect import KiteConnect

# ==============================
# CONFIG — DO NOT GUESS
# ==============================
STRIKE = 25800
EXPIRY = "2025-12-16"
INDEX  = "NIFTY"
TF_MIN = 1

FROM_DT = datetime(2025, 12, 12, 9, 15)
TO_DT   = datetime(2025, 12, 12, 15, 30)

INSTRUMENTS_CSV = Path("~/.scalp-app/zerodha_instruments.csv").expanduser()
TOKEN_JSON      = Path("~/.scalp-app/zerodha_token.json").expanduser()

# ==============================
# AUTH
# ==============================
def load_kite():
    with TOKEN_JSON.open() as f:
        data = json.load(f)

    kite = KiteConnect(api_key=data["api_key"])
    kite.set_access_token(data["access_token"])
    return kite

# ==============================
# MAIN
# ==============================
print("\n=== STEP 1: Load Zerodha instruments ===")

if not INSTRUMENTS_CSV.exists():
    raise FileNotFoundError(f"Instruments file not found: {INSTRUMENTS_CSV}")

df = pd.read_csv(INSTRUMENTS_CSV)
df["expiry"] = pd.to_datetime(df["expiry"]).dt.date

matches = df[
    (df["name"] == INDEX) &
    (df["instrument_type"] == "CE") &
    (df["strike"] == STRIKE) &
    (df["expiry"] == datetime.strptime(EXPIRY, "%Y-%m-%d").date())
]

if matches.empty:
    raise RuntimeError("No matching contracts found")

print(f"\n[FOUND] {len(matches)} matching contracts\n")

kite = load_kite()

# ==============================
# PRICE SANITY CHECK
# ==============================
results = []

for _, row in matches.iterrows():
    token = int(row["instrument_token"])
    symbol = row["tradingsymbol"]

    try:
        candles = kite.historical_data(
            instrument_token=token,
            from_date=FROM_DT,
            to_date=TO_DT,
            interval="minute"
        )
    except Exception as e:
        print(f"[ERROR] {symbol}: {e}")
        continue

    if not candles:
        print(f"[NO DATA] {symbol}")
        continue

    dfc = pd.DataFrame(candles)
    min_p = dfc["low"].min()
    max_p = dfc["high"].max()
    last_p = dfc.iloc[-1]["close"]

    results.append({
        "symbol": symbol,
        "token": token,
        "min": round(min_p, 2),
        "max": round(max_p, 2),
        "last": round(last_p, 2),
        "rows": len(dfc),
    })

# ==============================
# REPORT
# ==============================
print("\n=== PRICE SANITY REPORT (12-Dec) ===\n")

results = sorted(results, key=lambda x: x["max"], reverse=True)

for r in results:
    flag = "✅" if r["max"] > 200 else "❌"
    print(
        f"{flag} {r['symbol']:<22} "
        f"MIN={r['min']:<7} MAX={r['max']:<7} LAST={r['last']:<7} "
        f"ROWS={r['rows']} TOKEN={r['token']}"
    )

print("\n=== ACTION REQUIRED ===")
print("Pick the symbol that matches your TradingView prices (₹200+).")
print("Reply with the EXACT tradingsymbol.")
print("We will LOCK it permanently.\n")
