from kiteconnect import KiteConnect
import os

def get_historical_kite() -> KiteConnect:
    """
    Read-only Kite client for:
    - historical candles
    - backtests
    - NO filesystem writes
    - NO live state
    """

    api_key = os.environ.get("ZERODHA_API_KEY")
    access_token = os.environ.get("ZERODHA_ACCESS_TOKEN")

    if not api_key or not access_token:
        raise RuntimeError("Missing Zerodha API credentials for historical fetch")

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    return kite
