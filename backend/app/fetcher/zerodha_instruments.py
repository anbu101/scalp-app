from pathlib import Path
from datetime import date
import pandas as pd
from kiteconnect import KiteConnect

from app.event_bus.audit_logger import write_audit_log

# =================================================
# 🔑 SINGLE SOURCE OF TRUTH (NEW ARCHITECTURE)
# =================================================

STATE_DIR = Path.home() / ".scalp-app" / "state"
INSTRUMENTS_PATH = STATE_DIR / "instruments.csv"


# =================================================
# Instrument dump generation (SAFE)
# =================================================

def ensure_instruments_dump(api_key=None, access_token=None):
    """
    Generates instruments.csv ONLY IF:
    - file is missing
    - valid Zerodha creds are available

    Never fatal. Never overwrites.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    if INSTRUMENTS_PATH.exists():
        return

    if not api_key or not access_token:
        write_audit_log(
            "[INDEX][WARN] instruments.csv missing and Zerodha creds not available"
        )
        return

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

def load_instruments_df(
    api_key: str | None = None,
    access_token: str | None = None,
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

    opts = df[
        (df["exchange"] == "NFO")
        & (df["name"] == "NIFTY")
        & (df["instrument_type"].isin(["CE", "PE"]))
        & (df["expiry"] >= date.today())
    ]

    if opts.empty:
        write_audit_log("[UNIVERSE][WARN] No NIFTY options found")
        return []

    expiries = sorted(opts["expiry"].unique())
    weekly_expiries = expiries[:2]

    universe = opts[opts["expiry"].isin(weekly_expiries)]

    if universe.empty:
        write_audit_log("[UNIVERSE][WARN] Weekly universe empty")
        return []

    write_audit_log(
        f"[UNIVERSE] Weekly universe loaded: "
        f"{len(universe)} contracts, expiries={weekly_expiries}"
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
