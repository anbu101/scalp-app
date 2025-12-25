import os
import json
import pandas as pd
from datetime import date
from kiteconnect import KiteConnect

from app.marketdata.zerodha_symbol_utils import ZerodhaSymbolAdapter


class ZerodhaSymbolMapper:
    """
    Maps structured option definitions to Zerodha instrument_token.
    """

    def __init__(self, token_path: str):
        self.token_path = token_path
        self.kite = self._init_kite()
        self.df = None

    # --------------------------------------------------
    # Init Kite client
    # --------------------------------------------------

    def _init_kite(self):
        if not os.path.exists(self.token_path):
            raise FileNotFoundError(
                f"Zerodha token file not found: {self.token_path}"
            )

        with open(self.token_path, "r") as f:
            data = json.load(f)

        if data.get("date") != str(date.today()):
            raise RuntimeError(
                "Zerodha access token is not from today. "
                "Please regenerate access token."
            )

        kite = KiteConnect(api_key=data["api_key"])
        kite.set_access_token(data["access_token"])
        return kite

    # --------------------------------------------------
    # Load instruments dump
    # --------------------------------------------------

    def load(self):
        print("[ZSYM] Loading Zerodha instruments dump...")
        instruments = self.kite.instruments()
        self.df = pd.DataFrame(instruments)
        print(f"[ZSYM] Loaded {len(self.df)} instruments")

    # --------------------------------------------------
    # Get token for structured option
    # --------------------------------------------------

    def get_token(self, option: dict, exchange: str = "NFO") -> int:
        """
        option = {
            "index": "NIFTY",
            "expiry": "2026-05-29",
            "strike": 26000,
            "type": "CE"
        }
        """
        if self.df is None:
            raise RuntimeError("Instrument dump not loaded")

        tradingsymbol = ZerodhaSymbolAdapter.to_zerodha(
            index=option["index"],
            expiry=option["expiry"],
            strike=option["strike"],
            option_type=option["type"],
        )

        rows = self.df[
            (self.df["tradingsymbol"] == tradingsymbol)
            & (self.df["exchange"] == exchange)
        ]

        if rows.empty:
            raise KeyError(
                f"Instrument token not found for {tradingsymbol}"
            )

        return int(rows.iloc[0]["instrument_token"])

    # --------------------------------------------------
    # Bulk mapping
    # --------------------------------------------------

    def map_options(self, options: list[dict], exchange: str = "NFO") -> dict:
        """
        Returns:
            { internal_key : instrument_token }
        """
        mapping = {}
        for opt in options:
            key = f"{opt['index']}-{opt['expiry']}-{opt['strike']}-{opt['type']}"
            mapping[key] = self.get_token(opt, exchange)
        return mapping
