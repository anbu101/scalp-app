from datetime import date
from typing import Optional
from app.fetcher.zerodha_instruments import load_instruments_df


# 🔒 Cache instruments once per process
_INSTRUMENTS_DF = None


def resolve_current_monthly_expiry() -> Optional[date]:
    """
    Returns the nearest MONTHLY expiry for BANKNIFTY >= today.

    Monthly expiry = the last (maximum) expiry of a calendar month.
    For BANKNIFTY, this is the last Thursday of the month.
    Weekly expiries are earlier Thursdays of the same month.

    Taking max expiry per (year, month) group automatically identifies
    the monthly expiry without any hardcoded calendar or holiday logic.

    BUG FIXED:
    The old code filtered for expiries in the CURRENT calendar month only.
    On the day after expiry (e.g. Apr 29 after Apr 28 expiry), no current-month
    expiries remain, so it returned None and the engine skipped all entries.

    The fix: group ALL future expiries by month, take the max per month
    (= monthly expiry), then return the nearest one >= today.
    This correctly rolls over to the next month's series after expiry.
    """
    global _INSTRUMENTS_DF

    if _INSTRUMENTS_DF is None:
        _INSTRUMENTS_DF = load_instruments_df()

    df = _INSTRUMENTS_DF

    if df is None or df.empty:
        return None

    opt_df = df[
        (df["segment"] == "NFO-OPT")
        & (df["name"] == "BANKNIFTY")
    ]

    if opt_df.empty:
        return None

    today = date.today()

    # All future expiries >= today (past expiries filtered out)
    valid = opt_df[
        opt_df["expiry"].apply(lambda x: isinstance(x, date) and x >= today)
    ]

    if valid.empty:
        return None

    # Identify monthly expiries:
    # Group all unique future expiries by (year, month) and take the max.
    # max expiry per month = last Thursday = monthly expiry.
    # Weekly expiries (earlier Thursdays) are implicitly excluded.
    unique_expiries = sorted(valid["expiry"].unique())

    monthly_expiries: dict = {}
    for exp in unique_expiries:
        key = (exp.year, exp.month)
        if key not in monthly_expiries or exp > monthly_expiries[key]:
            monthly_expiries[key] = exp

    # Sort ascending — first entry is the nearest monthly expiry
    sorted_monthly = sorted(monthly_expiries.values())

    if not sorted_monthly:
        return None

    nearest = sorted_monthly[0]

    return nearest