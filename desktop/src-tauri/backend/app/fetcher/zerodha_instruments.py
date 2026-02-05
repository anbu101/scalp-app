from pathlib import Path
from datetime import date
from datetime import datetime, timedelta
from kiteconnect import KiteConnect
from app.event_bus.audit_logger import write_audit_log

import pandas as pd
import os




# =================================================
# 🔑 SINGLE SOURCE OF TRUTH (NEW ARCHITECTURE)
# =================================================

STATE_DIR = Path.home() / ".scalp-app" / "state"
INSTRUMENTS_PATH = STATE_DIR / "instruments.csv"
MAX_AGE_HOURS = 24  # safe default

def ensure_instruments_dump(api_key=None, access_token=None):
    """
    Generates instruments.csv ONLY IF:
    - file is missing
    - OR file is stale (older than MAX_AGE_HOURS)
    - valid Zerodha creds are available

    Never fatal. Safe refresh.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------
    # Check if dump exists and is fresh
    # -------------------------------------------------
    if INSTRUMENTS_PATH.exists():
        try:
            mtime = datetime.fromtimestamp(
                INSTRUMENTS_PATH.stat().st_mtime
            )
            age = datetime.now() - mtime

            if age < timedelta(hours=MAX_AGE_HOURS):
                return  # ✅ Fresh enough, do nothing

            write_audit_log(
                f"[INDEX][INFO] instruments.csv stale "
                f"({age.days}d {age.seconds//3600}h old), refreshing"
            )
        except Exception as e:
            write_audit_log(
                f"[INDEX][WARN] Failed to stat instruments.csv: {e}, regenerating"
            )

    # -------------------------------------------------
    # Require creds to (re)generate
    # -------------------------------------------------
    if not api_key or not access_token:
        write_audit_log(
            "[INDEX][WARN] instruments.csv stale/missing but Zerodha creds not available"
        )
        return

    # -------------------------------------------------
    # Generate fresh dump
    # -------------------------------------------------
    try:
        write_audit_log("[INDEX] Generating instruments.csv from Zerodha")

        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)

        data = kite.instruments()
        pd.DataFrame(data).to_csv(INSTRUMENTS_PATH, index=False)

        write_audit_log("[INDEX] instruments.csv generated successfully")

    except Exception as e:
        write_audit_log(f"[INDEX][ERROR] Instrument generation failed: {e}")



# =================================================
# Instrument dump handling
# =================================================

from typing import Optional

def load_instruments_df(
    api_key: Optional[str] = None,
    access_token: Optional[str] = None,
) -> pd.DataFrame:

    """
    Load normalized Zerodha instruments dataframe.

    - Missing file → try auto-generate if creds exist
    - Still NON-FATAL
    """

    if not INSTRUMENTS_PATH.exists():
        ensure_instruments_dump(api_key, access_token)

    if not INSTRUMENTS_PATH.exists():
        write_audit_log(
            f"[INDEX][WARN] Instrument dump missing at {INSTRUMENTS_PATH}"
        )
        return pd.DataFrame()

    df = pd.read_csv(INSTRUMENTS_PATH)

    if df.empty:
        write_audit_log("[INDEX][WARN] Instrument dump is empty")
        return df

    # Normalize
    if "expiry" in df.columns:
        df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce").dt.date

    if "strike" in df.columns:
        df["strike"] = (
            pd.to_numeric(df["strike"], errors="coerce")
            .fillna(0.0)
        )

    # Validate index presence (only if data exists)
    if "segment" in df.columns:
        if not df["segment"].isin(["INDICES", "BSE-INDICES"]).any():
            raise RuntimeError(
                "NSE index instruments missing in instrument dump"
            )

    return df


# =================================================
# Selection helpers
# =================================================

def load_nifty_weekly_options(api_key: str, access_token: str):
    df = load_instruments_df(api_key, access_token)
    if df.empty:
        return []

    return df[
        (df["exchange"] == "NFO")
        & (df["name"] == "NIFTY")
        & (df["instrument_type"].isin(["CE", "PE"]))
    ].to_dict("records")


def load_nifty_weekly_universe(
    api_key: str,
    access_token: str,
    atm_range: int,
    strike_step: int,
):
    df = load_instruments_df(api_key, access_token)
    if df.empty:
        return []

    # 1️⃣ Load all NIFTY options (future-safe)
    opts = df[
        (df["exchange"] == "NFO")
        & (df["name"] == "NIFTY")
        & (df["instrument_type"].isin(["CE", "PE"]))
        & (df["expiry"] >= date.today())
    ]

    if opts.empty:
        write_audit_log("[UNIVERSE][WARN] No NIFTY options found")
        return []

    # 2️⃣ Weekly expiries only
    expiries = sorted(opts["expiry"].unique())
    weekly_expiries = expiries[:2]
    opts = opts[opts["expiry"].isin(weekly_expiries)]

    # 3️⃣ Fetch live NIFTY spot
    spot = get_nifty_spot(api_key, access_token)

    # 4️⃣ Compute ATM (rounded to strike_step)
    atm = round(spot / strike_step) * strike_step

    low = atm - atm_range
    high = atm + atm_range

    # 5️⃣ FILTER BY ATM RANGE (🔥 MISSING PIECE)
    universe = opts[
        (opts["strike"] >= low)
        & (opts["strike"] <= high)
    ]

    write_audit_log(
        f"[UNIVERSE] Weekly universe loaded: {len(universe)} contracts | "
        f"ATM={atm} range=[{low}, {high}] expiries={weekly_expiries}"
    )

    return universe.to_dict("records")



# =================================================
# Spot helper
# =================================================

def get_nifty_spot(api_key: str, access_token: str) -> float:
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    data = kite.ltp(["NSE:NIFTY 50"])
    return float(data["NSE:NIFTY 50"]["last_price"])
