from datetime import date
from typing import List, Dict
import pandas as pd


class ZerodhaOptionDiscovery:
    """
    Discovers tradable NIFTY options using Zerodha instrument dump only.
    No symbol construction. No assumptions.
    """

    def __init__(self, instruments_df: pd.DataFrame):
        if not isinstance(instruments_df, pd.DataFrame):
            raise TypeError("instruments_df must be a pandas DataFrame")
        self.df = instruments_df.copy()

        # Normalize fields we care about
        self._normalize()

    # --------------------------------------------------
    # Normalization
    # --------------------------------------------------

    def _normalize(self):
        """
        Normalize columns to predictable types.
        """
        required_cols = {
            "instrument_token",
            "name",
            "instrument_type",
            "expiry",
            "strike",
            "tradingsymbol",
            "exchange",
        }
        missing = required_cols - set(self.df.columns)
        if missing:
            raise ValueError(f"Instrument dump missing columns: {missing}")

        # Normalize types
        self.df["expiry"] = pd.to_datetime(self.df["expiry"]).dt.date
        self.df["strike"] = self.df["strike"].astype(float)
        self.df["instrument_type"] = self.df["instrument_type"].astype(str)
        self.df["name"] = self.df["name"].astype(str)

    # --------------------------------------------------
    # Step 3.2.1 — Nearest weekly expiry
    # --------------------------------------------------

    def nearest_weekly_expiry(self, index_name: str = "NIFTY") -> date:
        """
        Finds the nearest expiry >= today that has BOTH CE and PE.
        """
        today = date.today()

        df = self.df[
            (self.df["name"] == index_name)
            & (self.df["instrument_type"].isin(["CE", "PE"]))
            & (self.df["expiry"] >= today)
        ]

        if df.empty:
            raise RuntimeError("No future NIFTY options found in instrument dump")

        # Group by expiry and require both CE and PE
        grouped = df.groupby("expiry")["instrument_type"].nunique()
        valid_expiries = grouped[grouped >= 2].index.tolist()

        if not valid_expiries:
            raise RuntimeError("No expiry found with both CE and PE")

        return sorted(valid_expiries)[0]

    # --------------------------------------------------
    # Step 3.2.2 — Median strike (ATM proxy)
    # --------------------------------------------------

    def median_strike(self, expiry: date, index_name: str = "NIFTY") -> float:
        """
        Uses median strike of the expiry as ATM proxy.
        """
        strikes = (
            self.df[
                (self.df["name"] == index_name)
                & (self.df["expiry"] == expiry)
                & (self.df["instrument_type"].isin(["CE", "PE"]))
            ]["strike"]
            .unique()
        )

        if len(strikes) == 0:
            raise RuntimeError("No strikes found for expiry")

        strikes = sorted(strikes)
        mid = len(strikes) // 2
        return strikes[mid]

    # --------------------------------------------------
    # Step 3.2.3 — Final option selection
    # --------------------------------------------------

    def select_options(
        self,
        index_name: str = "NIFTY",
        option_mode: str = "BOTH",   # "CE", "PE", "BOTH"
        strike_window: int = 10,     # number of strikes on each side
    ) -> List[Dict]:
        """
        Returns a list of option dicts for tracking.
        """
        if option_mode not in ("CE", "PE", "BOTH"):
            raise ValueError("option_mode must be CE, PE, or BOTH")

        expiry = self.nearest_weekly_expiry(index_name)
        atm = self.median_strike(expiry, index_name)

        df = self.df[
            (self.df["name"] == index_name)
            & (self.df["expiry"] == expiry)
            & (self.df["instrument_type"].isin(["CE", "PE"]))
        ]

        # Build sorted unique strikes
        strikes = sorted(df["strike"].unique())
        if atm not in strikes:
            # Find closest strike if median not exact
            atm = min(strikes, key=lambda s: abs(s - atm))

        atm_idx = strikes.index(atm)
        lo = max(0, atm_idx - strike_window)
        hi = min(len(strikes), atm_idx + strike_window + 1)
        allowed_strikes = set(strikes[lo:hi])

        df = df[df["strike"].isin(allowed_strikes)]

        if option_mode != "BOTH":
            df = df[df["instrument_type"] == option_mode]

        # Return clean records
        records = []
        for _, r in df.iterrows():
            records.append(
                {
                    "instrument_token": int(r["instrument_token"]),
                    "tradingsymbol": r["tradingsymbol"],
                    "strike": float(r["strike"]),
                    "expiry": r["expiry"],
                    "type": r["instrument_type"],
                    "exchange": r["exchange"],
                }
            )

        # Sort for determinism
        records.sort(key=lambda x: (x["type"], x["strike"]))
        return records
