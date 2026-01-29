# tools/test_pick.py
from fetcher.dhan_fetcher import DhanFetcher
from datastore import DataStore
import json, os

# minimal dummy config to pass required keys (we won't start feed)
cfg = {
    "dhan": {"token":"DUMMY","clientId":"DUMMY"},
    "fetcher": {"underlying": "NIFTY"}
}

# DataStore stub
store = DataStore("test_db.json")

#df = DhanFetcher(cfg, store)

# try listing options (downloads master csv) - can take a few seconds
items = df.list_nifty_options(refresh_master=True)
print("Total items found:", len(items))

# pick CE/PE in a sample price range
picks = df.pick_ce_pe(min_price=150, max_price=200, expiry_filter="nearest")
print("Picks:", json.dumps(picks, indent=2, default=str))
