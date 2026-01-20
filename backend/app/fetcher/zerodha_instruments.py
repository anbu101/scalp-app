from pathlib import Path
from datetime import date
import pandas as pd
from kiteconnect import KiteConnect

from app.event_bus.audit_logger import write_audit_log

def ensure_instruments_dump(*args, **kwargs):
    # Instruments are generated offline.
    # This is intentionally a no-op.
    return

# =================================================
# 🔑 SINGLE SOURCE OF TRUTH
# =================================================

INSTRUMENTS_PATH = (
    Path(__file__).resolve().parents[2] / "app" / "state" / "instruments.csv"
)


# =================================================
# Instrument dump handling
# =================================================

def load_instruments_df() -> pd.DataFrame:
    """
    Load normalized Zerodha instruments dataframe.
    CSV MUST already exist (generated externally).
    """

    if not INSTRUMENTS_PATH.exists():
        raise FileNotFoundError(
            f"Instrument dump not found at {INSTRUMENTS_PATH}"
        )

    df = pd.read_csv(INSTRUMENTS_PATH)

    # Normalize
    if "expiry" in df.columns:
        df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce").dt.date

    if "strike" in df.columns:
        df["strike"] = pd.to_numeric(df["strike"], errors="coerce").fillna(0.0)

    # Hard validation
    if not df["segment"].isin(["INDICES", "BSE-INDICES"]).any():
        raise RuntimeError(
            "NSE index instruments missing in instrument dump"
        )

    return df


# =================================================
# Selection helpers
# =================================================

def load_nifty_weekly_options(api_key: str, access_token: str):
    """
    Returns ALL NIFTY CE/PE options (ALL expiries).
    """
    df = load_instruments_df()

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
    """
    Returns OPTION UNIVERSE for:
    - Current weekly expiry
    - Next weekly expiry
    """

    df = load_instruments_df()

    # Only NIFTY options
    opts = df[
        (df["exchange"] == "NFO")
        & (df["name"] == "NIFTY")
        & (df["instrument_type"].isin(["CE", "PE"]))
        & (df["expiry"] >= date.today())
    ]

    if opts.empty:
        write_audit_log("[UNIVERSE][FATAL] No NIFTY options found")
        return []

    expiries = sorted(opts["expiry"].unique())

    if not expiries:
        write_audit_log("[UNIVERSE][FATAL] No future expiries found")
        return []

    weekly_expiries = expiries[:2]

    universe = opts[opts["expiry"].isin(weekly_expiries)]

    if universe.empty:
        write_audit_log("[UNIVERSE][FATAL] Weekly universe empty")
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
