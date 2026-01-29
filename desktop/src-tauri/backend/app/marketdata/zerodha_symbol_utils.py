from datetime import datetime


class ZerodhaSymbolAdapter:
    """
    Build Zerodha option tradingsymbols using structured option data.

    Zerodha format:
        NIFTY26MAY26000CE
    """

    @staticmethod
    def to_zerodha(
        index: str,
        expiry: str,
        strike: int,
        option_type: str,
    ) -> str:
        """
        Convert structured option data to Zerodha tradingsymbol.
        """
        if option_type not in ("CE", "PE"):
            raise ValueError(f"Invalid option type: {option_type}")

        dt = datetime.strptime(expiry, "%Y-%m-%d")

        yy = dt.strftime("%y")     # 26
        mon = dt.strftime("%b").upper()  # MAY
        day = dt.strftime("%d")    # 29 (not used by Zerodha, but expiry validated)

        return f"{index}{yy}{mon}{int(strike)}{option_type}"
