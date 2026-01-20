from datetime import date
from typing import List, Dict
import pandas as pd

from app.fetcher.zerodha_instruments import load_instruments_df
from app.event_bus.audit_logger import write_audit_log


class BacktestInstrumentResolver:
    """
    Resolves option instruments AS-OF a historical date.

    ✅ Uses instruments.csv only
    ✅ Weekly expiry correct for candle date
    ✅ Token-safe for historical fetch
    ❌ Never uses today's tokens for past dates
    """

    def __init__(
        self,
        *,
        as_of_date: date,
        spot_price: float,
        index_name: str = "NIFTY",
    ):
        self.as_of_date = as_of_date
        self.spot_price = spot_price
        self.index_name = index_name

        self.df = load_instruments_df()

    # --------------------------------------------------
    # PUBLIC
    # --------------------------------------------------
    def get_option_universe(
        self,
        *,
        atm_range: int,
        strike_step: int,
    ) -> List[Dict]:
        expiry = self._get_weekly_expiry_for_date()

        atm = self._round_to_strike(self.spot_price, strike_step)

        strikes = range(
            atm - atm_range,
            atm + atm_range + strike_step,
            strike_step,
        )

        opts = self.df[
            (self.df["exchange"] == "NFO")
            & (self.df["name"] == self.index_name)
            & (self.df["instrument_type"].isin(["CE", "PE"]))
            & (self.df["expiry"] == expiry)
            & (self.df["strike"].isin(strikes))
        ]

        if opts.empty:
            write_audit_log(
                f"[BACKTEST][RESOLVER][WARN] "
                f"No options for {self.index_name} {expiry}"
            )
            return []

        out = []
        for _, r in opts.iterrows():
            out.append({
                "symbol": r["tradingsymbol"],
                "token": int(r["instrument_token"]),
                "strike": int(r["strike"]),
                "option_type": r["instrument_type"],
                "expiry": r["expiry"],
            })

        return out

    # --------------------------------------------------
    # INTERNALS
    # --------------------------------------------------
    def _get_weekly_expiry_for_date(self) -> date:
        """
        Finds the first expiry >= as_of_date
        """
        expiries = (
            self.df[
                (self.df["exchange"] == "NFO")
                & (self.df["name"] == self.index_name)
                & (self.df["instrument_type"].isin(["CE", "PE"]))
                & (self.df["expiry"] >= self.as_of_date)
            ]["expiry"]
            .dropna()
            .unique()
        )

        if len(expiries) == 0:
            raise RuntimeError(
                f"No weekly expiry found after {self.as_of_date}"
            )

        return sorted(expiries)[0]

    @staticmethod
    def _round_to_strike(price: float, step: int) -> int:
        return int(round(price / step) * step)
