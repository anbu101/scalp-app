from datetime import date
from typing import Optional
from app.fetcher.zerodha_instruments import load_instruments_df


# 🔒 Cache instruments once per process
_INSTRUMENTS_DF = None


def resolve_current_monthly_expiry() -> Optional[date]:
    global _INSTRUMENTS_DF

    if _INSTRUMENTS_DF is None:
        _INSTRUMENTS_DF = load_instruments_df()

    df = _INSTRUMENTS_DF

    if df is None or df.empty:
        return None

    opt_df = df[
        (df["segment"] == "NFO-OPT")
        & (df["name"] == "NIFTY")
    ]

    if opt_df.empty:
        return None

    today = date.today()

    # 🔒 Ensure expiry is date (defensive safety)
    valid = opt_df[
        opt_df["expiry"].apply(lambda x: isinstance(x, date) and x >= today)
    ]

    if valid.empty:
        return None

    current_month = (today.year, today.month)

    monthly = valid[
        valid["expiry"].apply(lambda d: (d.year, d.month) == current_month)
    ]

    if monthly.empty:
        return None

    return monthly["expiry"].max()
