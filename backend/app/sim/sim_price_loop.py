import asyncio
import random

class SimPriceLoop:
    """
    Periodically nudges prices up/down to trigger TP / SL.
    SAFE:
    - Only touches SimPriceProvider
    - No broker interaction
    """

    def __init__(
        self,
        price_provider,
        interval_sec: float = 1.0,
        step_range=(1.0, 3.0),
    ):
        self.price_provider = price_provider
        self.interval = interval_sec
        self.step_min, self.step_max = step_range
        self._running = False

    async def run(self):
        self._running = True
        while self._running:
            await asyncio.sleep(self.interval)
            self._tick()

    def stop(self):
        self._running = False

    # -------------------------
    # Internal
    # -------------------------

    def _tick(self):
        for symbol in list(self.price_provider._prices.keys()):
            step = random.uniform(self.step_min, self.step_max)
            direction = random.choice([-1, 1])
            self.price_provider.bump(symbol, step * direction)
