from pathlib import Path
import pandas as pd
from datetime import date
from kiteconnect import KiteConnect

# =================================================
# 🔑 SINGLE SOURCE OF TRUTH (DO NOT CHANGE)
# =================================================

INSTRUMENTS_PATH = (
    Path(__file__).resolve().parents[2] / "app" / "state" / "instruments.csv"
)

# =================================================
# Internal helpers
# =================================================

def _is_stale(path: Path) -> bool:
    """
    Instrument dump is considered stale if:
    - file does not exist
    - OR last modified date != today
    """
    if not path.exists():
        return True
    modified = date.fromtimestamp(path.stat().st_mtime)
    return modified != date.today()


# =================================================
# Instrument dump handling (SAFE, IDEMPOTENT)
# =================================================

def ensure_instruments_dump(kite: KiteConnect):
    """
    Ensures instruments.csv exists and is fresh (daily).
    Safe to call multiple times.
    """
    if not _is_stale(INSTRUMENTS_PATH):
        return

    df = pd.DataFrame(kite.instruments("NFO"))
    INSTRUMENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(INSTRUMENTS_PATH, index=False)


def load_instruments_df() -> pd.DataFrame:
    """
    Load normalized instruments dataframe.
    """
    if not INSTRUMENTS_PATH.exists():
        raise FileNotFoundError(
            f"Zerodha instrument dump not found at {INSTRUMENTS_PATH}"
        )

    df = pd.read_csv(INSTRUMENTS_PATH)

    # normalize once, centrally
    df["expiry"] = pd.to_datetime(df["expiry"]).dt.date
    df["strike"] = df["strike"].astype(float)

    return df


# =================================================
# Selection helpers (NO STRATEGY LOGIC HERE)
# =================================================

def load_nifty_weekly_options(api_key: str, access_token: str):
    """
    Returns ALL NIFTY CE/PE options (all expiries).
    Strategy layer decides what to trade.
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
    - Current weekly expiry (TRADING)
    - Next weekly expiry (WARMUP ONLY)

    Strategy MUST block next-week trades.
    """
    df = load_instruments_df()

    weekly = (
        df[
            (df["exchange"] == "NFO")
            & (df["name"] == "NIFTY")
            & (df["instrument_type"].isin(["CE", "PE"]))
        ]
        .sort_values("expiry")
    )

    # -------------------------------------------------
    # 🔥 PICK FIRST TWO WEEKLY EXPIRIES (CURRENT + NEXT)
    # -------------------------------------------------
    expiries = weekly["expiry"].drop_duplicates().iloc[:2].tolist()

    weekly = weekly[weekly["expiry"].isin(expiries)]

    spot = get_nifty_spot(api_key, access_token)
    atm = round(spot / strike_step) * strike_step

    min_strike = atm - atm_range
    max_strike = atm + atm_range

    universe = weekly[
        (weekly["strike"] >= min_strike)
        & (weekly["strike"] <= max_strike)
        & (weekly["strike"] % strike_step == 0)
    ]

    return universe.to_dict("records")


# =================================================
# Spot helper
# =================================================

def get_nifty_spot(api_key: str, access_token: str) -> float:
    """
    Fetch live NIFTY spot from Zerodha indices.
    """
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    data = kite.ltp(["NSE:NIFTY 50"])
    return float(data["NSE:NIFTY 50"]["last_price"])
