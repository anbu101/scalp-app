import pandas as pd
from datetime import date
from pathlib import Path

# -------------------------------------------------
# Global instrument cache (token -> tradingsymbol)
# -------------------------------------------------

_INSTRUMENT_MAP = None


def _load_instruments() -> pd.DataFrame:
    """
    Loads Zerodha instrument dump once.
    Adjust path ONLY if your dump location differs.
    """
    path = Path("app/state/instruments.csv")
    if not path.exists():
        raise FileNotFoundError(
            "Zerodha instrument dump not found at app/state/instruments.csv"
        )

    df = pd.read_csv(path)

    # Normalize
    df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce").dt.date
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")

    return df


def resolve_symbol(instrument_token: int) -> str:
    """
    Resolve human-readable tradingsymbol from instrument_token.
    Used by candle engine, logs, CSVs.
    """
    global _INSTRUMENT_MAP

    if _INSTRUMENT_MAP is None:
        df = _load_instruments()
        _INSTRUMENT_MAP = dict(
            zip(df["instrument_token"], df["tradingsymbol"])
        )

    return _INSTRUMENT_MAP.get(instrument_token, str(instrument_token))


# -------------------------------------------------
# Existing strict resolver (unchanged)
# -------------------------------------------------

class ZerodhaInstrumentResolver:
    """
    Resolves option instruments strictly from Zerodha instrument dump.
    Single source of truth. No symbol construction.
    """

    def __init__(self, instruments_df: pd.DataFrame):
        self.df = instruments_df

        # Normalize columns (safety)
        self.df["expiry"] = pd.to_datetime(self.df["expiry"]).dt.date
        self.df["strike"] = self.df["strike"].astype(float)

    def get_option(
        self,
        index: str,
        expiry: date,
        strike: int,
        option_type: str,
    ) -> dict:
        rows = self.df[
            (self.df["exchange"] == "NFO")
            & (self.df["name"] == index)
            & (self.df["expiry"] == expiry)
            & (self.df["strike"] == float(strike))
            & (self.df["instrument_type"] == option_type)
        ]

        if rows.empty:
            raise KeyError(
                f"Option not found: {index} {expiry} {strike} {option_type}"
            )

        return rows.iloc[0].to_dict()
