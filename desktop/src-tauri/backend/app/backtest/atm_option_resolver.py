from datetime import datetime
from app.fetcher.zerodha_instruments import load_instruments_df


class ATMOptionResolver:
    """
    Resolves ATM and ATM+1 option symbols (CE or PE)
    for a given index price and timestamp.
    """

    def __init__(self, index_name="NIFTY"):
        self.df = load_instruments_df()
        self.index_name = index_name

    def resolve(self, spot_price, direction, ts):
        """
        spot_price : float (index close at signal)
        direction  : 'BULLISH' or 'BEARISH'
        ts         : epoch seconds
        """

        trade_date = datetime.fromtimestamp(ts).date()

        # round to nearest strike
        atm = int(round(spot_price / 50) * 50)
        strikes = [atm, atm + 50]

        opt_type = "CE" if direction == "BULLISH" else "PE"

        rows = self.df[
            (self.df["name"] == self.index_name)
            & (self.df["instrument_type"] == opt_type)
            & (self.df["strike"].isin(strikes))
            & (self.df["expiry"] >= trade_date)
        ].sort_values(["expiry", "strike"])

        resolved = []
        for strike in strikes:
            r = rows[rows["strike"] == strike].head(1)
            if not r.empty:
                resolved.append({
                    "symbol": r.iloc[0]["tradingsymbol"],
                    "token": int(r.iloc[0]["instrument_token"]),
                    "strike": strike,
                    "option_type": opt_type,
                    "expiry": r.iloc[0]["expiry"],
                    "atm_slot": strike - atm,   # 0 or +50
                })

        return resolved
