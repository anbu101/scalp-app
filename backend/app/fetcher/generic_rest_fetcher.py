# backend/app/fetcher/generic_rest_fetcher.py
from . import InstrumentFetcher
from typing import List, Dict, Optional
import requests
import time

class RestFetcher(InstrumentFetcher):
    """
    Generic REST fetcher that calls a user-specified endpoint.
    Config in config.json should provide:
      fetcher: {
        "type": "rest",
        "url": "https://api.yourbroker.com/instruments",
        "api_key": "...",           # optional
        "method": "GET"            # optional
      }
    The REST endpoint should return a JSON array of instrument objects (any shape).
    You can also provide a 'map_fn' later to translate fields if needed.
    """
    def __init__(self, url: str, api_key: Optional[str] = None, timeout: int = 5):
        self.url = url
        self.api_key = api_key
        self.timeout = timeout

    def fetch_instruments(self, underlying: str = "NIFTY") -> List[Dict]:
        headers = {}
        params = {"underlying": underlying}
        if self.api_key:
            # Many brokers use Authorization header — adjust if needed
            headers["Authorization"] = f"Bearer {self.api_key}"
            # Also include api_key as param for some providers
            params["apikey"] = self.api_key

        resp = requests.get(self.url, headers=headers, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        # Expect either a list or a dict with a results key; handle both
        if isinstance(data, dict):
            if "data" in data and isinstance(data["data"], list):
                data_list = data["data"]
            elif "results" in data and isinstance(data["results"], list):
                data_list = data["results"]
            else:
                # fallback: try to find first list in dict
                for v in data.values():
                    if isinstance(v, list):
                        data_list = v
                        break
                else:
                    raise ValueError("REST fetch returned unexpected shape; expected array")
        else:
            data_list = data

        # Try to normalize common fields if possible, but caller may postprocess
        normalized = []
        for item in data_list:
            # Very defensive normalization: try common key names
            symbol = item.get("symbol") or item.get("instrument") or item.get("tradingsymbol")
            strike = item.get("strike") or item.get("strike_price") or item.get("strikePrice")
            opt_type = item.get("option_type") or item.get("optionType") or item.get("type")
            last_price = item.get("last_price") or item.get("lastPrice") or item.get("last traded price") or item.get("ltp")
            expiry = item.get("expiry") or item.get("expiry_date") or item.get("expiryDate")
            volume = item.get("volume") or item.get("open_interest") or item.get("oi")
            normalized.append({
                "symbol": symbol,
                "strike": float(strike) if strike is not None else None,
                "option_type": (opt_type or "").upper() if opt_type else None,
                "last_price": float(last_price) if last_price is not None else None,
                "expiry": expiry,
                "volume": float(volume) if volume is not None else None,
                "raw": item
            })
        return normalized
