class SimPriceProvider:
    """
    Simple in-memory LTP provider for simulation.
    Prices can be manually nudged for TP / SL testing.
    """

    def __init__(self, seed_price: float = 100.0):
        self._prices = {}
        self._default = seed_price

    # -------------------------
    # Read
    # -------------------------

    def get_ltp(self, symbol: str):
        return self._prices.get(symbol, self._default)

    # -------------------------
    # Write (manual control)
    # -------------------------

    def set_price(self, symbol: str, price: float):
        self._prices[symbol] = round(float(price), 2)

    def bump(self, symbol: str, delta: float):
        self.set_price(
            symbol,
            self.get_ltp(symbol) + delta
        )
