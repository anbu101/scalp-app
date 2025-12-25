from kiteconnect import KiteTicker
from app.brokers.zerodha_auth import get_kite
from app.engine.price_cache import PriceCache

class ZerodhaMarketWS:
    def __init__(self, price_cache: PriceCache):
        kite = get_kite()

        self.price_cache = price_cache
        self.kws = KiteTicker(kite.api_key, kite.access_token)

        self.subscribed = set()

        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.on_close = self.on_close
        self.kws.on_error = self.on_error

    def connect(self):
        self.kws.connect(threaded=True)

    def subscribe(self, tokens: list[int]):
        new = set(tokens) - self.subscribed
        if not new:
            return
        self.subscribed |= new
        self.kws.subscribe(list(new))
        self.kws.set_mode(self.kws.MODE_LTP, list(new))

    def on_ticks(self, ws, ticks):
        for t in ticks:
            symbol = t["tradingsymbol"]
            ltp = t["last_price"]
            self.price_cache.update_price(symbol, ltp)

    def on_connect(self, ws, _):
        print("[MARKET-WS] Connected")

    def on_close(self, ws, code, reason):
        print("[MARKET-WS] Closed", code, reason)

    def on_error(self, ws, code, reason):
        print("[MARKET-WS] Error", code, reason)
