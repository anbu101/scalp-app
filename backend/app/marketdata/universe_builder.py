from typing import List, Dict
from datetime import datetime

from app.fetcher.zerodha_instruments import load_nifty_weekly_options
from app.brokers.zerodha_auth import get_kite


ATM_RANGE = 500
STRIKE_STEP = 50
INDEX_SYMBOL = "NIFTY"


def build_nifty_option_universe() -> List[Dict]:
    """
    Returns option dicts for NIFTY weekly options:
    ATM ±500, step 50, CE + PE
    """

    kite = get_kite()

    instruments = load_nifty_weekly_options(
        api_key=kite.api_key,
        access_token=kite.access_token,
    )

    # --- find NIFTY spot ---
    spot = kite.ltp(["NSE:NIFTY 50"])["NSE:NIFTY 50"]["last_price"]
    atm = round(spot / STRIKE_STEP) * STRIKE_STEP

    min_strike = atm - ATM_RANGE
    max_strike = atm + ATM_RANGE

    universe = []

    for opt in instruments:
        strike = opt.get("strike")
        if strike is None:
            continue

        if min_strike <= strike <= max_strike:
            if opt.get("type") in ("CE", "PE"):
                universe.append(opt)

    print(
        f"[UNIVERSE] Built candle universe: "
        f"ATM={atm}, strikes={min_strike}-{max_strike}, "
        f"count={len(universe)}"
    )

    return universe
