from kiteconnect import KiteConnect
import os


def get_login_url() -> str:
    kite = KiteConnect(api_key=os.getenv("ZERODHA_API_KEY"))
    return kite.login_url()


def generate_session(request_token: str) -> dict:
    kite = KiteConnect(api_key=os.getenv("ZERODHA_API_KEY"))

    data = kite.generate_session(
        request_token,
        api_secret=os.getenv("ZERODHA_API_SECRET")
    )

    # IMPORTANT:
    # Do NOT store kite or set access token here
    # Just return the token to the caller
    return data
