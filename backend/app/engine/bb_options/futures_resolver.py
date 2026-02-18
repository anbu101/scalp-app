from datetime import date
from typing import Optional, Tuple

from app.fetcher.zerodha_instruments import load_instruments_df
from app.event_bus.audit_logger import write_audit_log


def resolve_current_month_nifty_fut() -> Optional[Tuple[int, str]]:
    """
    Returns (instrument_token, tradingsymbol)
    for nearest expiry NIFTY FUT contract.
    """

    df = load_instruments_df()

    if df.empty:
        write_audit_log("[BB] Instruments DF empty")
        return None

    fut_df = df[
        (df["segment"] == "NFO-FUT") &
        (df["name"] == "NIFTY")
    ]

    if fut_df.empty:
        write_audit_log("[BB] No NIFTY FUT contracts found")
        return None

    today = date.today()

    valid = fut_df[fut_df["expiry"] >= today]

    if valid.empty:
        write_audit_log("[BB] No valid FUT expiry found")
        return None

    nearest_expiry = valid["expiry"].min()

    contract = valid[valid["expiry"] == nearest_expiry].iloc[0]

    token = int(contract["instrument_token"])
    symbol = contract["tradingsymbol"]

    write_audit_log(
        f"[BB] Using FUT contract {symbol} (expiry {nearest_expiry})"
    )

    return token, symbol
