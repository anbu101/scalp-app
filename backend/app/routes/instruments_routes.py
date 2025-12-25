# backend/app/routes/instruments_routes.py
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import time, os, json
from fastapi import Depends
from datetime import datetime

router = APIRouter(prefix="/api", tags=["instruments"])

# simple file cache (TTL seconds)
CACHE_FILE = ".instruments_cache.json"
DEFAULT_TTL = 10  # seconds

def _load_config():
    cfg_path = "config.json"
    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            return json.load(f)
    return {}

def _get_fetcher_from_config():
    cfg = _load_config().get("fetcher", {"type":"mock"})
    ftype = cfg.get("type", "mock")
    if ftype == "mock":
        from ..fetcher.mock_fetcher import MockFetcher
        return MockFetcher()
    elif ftype == "rest":
        from ..fetcher.generic_rest_fetcher import RestFetcher
        url = cfg.get("url")
        api_key = cfg.get("api_key")
        if not url:
            raise ValueError("fetcher.type=rest requires fetcher.url in config.json")
        return RestFetcher(url, api_key)
    else:
        raise ValueError(f"Unknown fetcher type: {ftype}")

@router.get("/fetch_instruments")
def fetch_instruments(min_price: Optional[float] = Query(None), max_price: Optional[float] = Query(None),
                      underlying: str = Query("NIFTY"), refresh: bool = Query(False), ttl: int = Query(DEFAULT_TTL)):
    """
    Fetch instruments (CE/PE). Query params:
      min_price, max_price - optional filters on last_price
      underlying - e.g., NIFTY
      refresh - force bypass cache
      ttl - cache TTL in seconds
    """
    # Cache read
    try:
        if not refresh and os.path.exists(CACHE_FILE):
            with open(CACHE_FILE) as f:
                cached = json.load(f)
            age = time.time() - cached.get("_ts", 0)
            if age <= ttl and cached.get("underlying") == underlying:
                instruments = cached.get("instruments", [])
            else:
                instruments = None
        else:
            instruments = None
    except Exception:
        instruments = None

    if instruments is None:
        # build fetcher and fetch live instruments
        fetcher = _get_fetcher_from_config()
        instruments = fetcher.fetch_instruments(underlying=underlying)
        # save cache
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump({"_ts": time.time(), "underlying": underlying, "instruments": instruments}, f)
        except Exception:
            pass

    # apply min/max price filters
    def price_ok(inst):
        lp = inst.get("last_price")
        if lp is None:
            return True
        if min_price is not None and lp < min_price:
            return False
        if max_price is not None and lp > max_price:
            return False
        return True

    filtered = [i for i in instruments if price_ok(i)]
    return {"count": len(filtered), "instruments": filtered}
