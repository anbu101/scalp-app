from typing import Optional, Tuple, List
from datetime import datetime
from app.fetcher.zerodha_instruments import load_instruments_df
from app.marketdata.ltp_store import LTPStore
from app.event_bus.audit_logger import write_audit_log
from app.engine.bb_options.monthly_expiry_resolver import resolve_current_monthly_expiry


class OptionSelector:

    def __init__(
        self,
        max_premium: float,
        scan_strikes: int,
        ltp_stale_seconds: int = 2,
    ):
        self.max_premium = max_premium
        self.scan_strikes = scan_strikes
        self.ltp_stale_seconds = ltp_stale_seconds

        self.instruments_df = load_instruments_df()

    # ==================================================
    # PUBLIC
    # ==================================================

    def select(
        self,
        futures_price: float,
        direction: str,  # "CE" or "PE"
    ) -> Optional[Tuple[str, float]]:

        if direction not in ("CE", "PE"):
            raise ValueError(f"Invalid direction: {direction}")

        # Deterministic ATM rounding (no banker’s rounding)
        atm = int((futures_price + 25) // 50) * 50

        # Always resolve expiry dynamically (expiry-safe)
        monthly_expiry = resolve_current_monthly_expiry()

        candidate_strikes = self._build_strike_list(atm)

        best_symbol = None
        best_price = None
        best_diff = float("inf")

        for strike in candidate_strikes:

            symbol = self._find_option_symbol(strike, direction, monthly_expiry)
            if not symbol:
                continue

            ltp_data = LTPStore.get(symbol)

            if ltp_data is None:
                continue

            # Support both (price) and (price, timestamp)
            if isinstance(ltp_data, tuple):
                ltp, ts = ltp_data
                if ts is None:
                    continue

                if (datetime.utcnow().timestamp() - ts) > self.ltp_stale_seconds:
                    continue
            else:
                ltp = ltp_data

            if ltp is None or ltp <= 0:
                continue

            if ltp <= self.max_premium:
                diff = self.max_premium - ltp

                if diff < best_diff:
                    best_diff = diff
                    best_symbol = symbol
                    best_price = ltp

        if best_symbol:
            write_audit_log(f"[BB] Selected {best_symbol} @ {best_price}")
            return best_symbol, best_price

        write_audit_log("[BB] No option selected within premium constraints")
        return None

    # ==================================================
    # INTERNAL
    # ==================================================

    def _build_strike_list(self, atm: int) -> List[int]:
        strikes = []
        for i in range(-self.scan_strikes, self.scan_strikes + 1):
            strikes.append(atm + i * 50)
        return strikes

    def _find_option_symbol(
        self,
        strike: int,
        direction: str,
        expiry,
    ) -> Optional[str]:

        df = self.instruments_df

        opt_df = df[
            (df["segment"] == "NFO-OPT")
            & (df["name"] == "NIFTY")
            & (df["strike"] == strike)
            & (df["instrument_type"] == direction)
            & (df["expiry"] == expiry)
        ]

        if opt_df.empty:
            return None

        return opt_df.iloc[0]["tradingsymbol"]
