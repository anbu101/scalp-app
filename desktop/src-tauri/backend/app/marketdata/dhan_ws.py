import asyncio
import threading
import logging
from datetime import datetime

from dhanhq import dhanhq

logger = logging.getLogger(__name__)


class DhanWebSocket:
    """
    Dhan WebSocket runner.
    Receives ticks and forwards them to CandleBuilders.
    """

    def __init__(
        self,
        client_id: str,
        access_token: str,
        symbol_builders: dict,
    ):
        self.client_id = client_id
        self.access_token = access_token
        self.symbol_builders = symbol_builders

        self._thread = None
        self._loop = None
        self._running = False

        self.client = dhanhq(
            client_id=self.client_id,
            access_token=self.access_token,
        )

    # --------------------------------------------------
    # Public API
    # --------------------------------------------------

    def start(self):
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            name="DhanWS",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    # --------------------------------------------------
    # Thread runner
    # --------------------------------------------------

    def _run(self):
        try:
            # IMPORTANT: create event loop for THIS thread
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            self._loop.run_until_complete(self._run_ws())

        except Exception as e:
            logger.exception(f"[WS ERROR] {e}")

    # --------------------------------------------------
    # Async WebSocket logic
    # --------------------------------------------------

    async def _run_ws(self):
        symbols = list(self.symbol_builders.keys())

        if not symbols:
            logger.warning("[WS] No symbols to subscribe")
            return

        logger.info(f"[WS] Subscribing to {len(symbols)} symbols")

        try:
            async for tick in self.client.market_feed(symbols):
                if not self._running:
                    break

                self._handle_tick(tick)

        except Exception as e:
            logger.exception(f"[WS ERROR] {e}")

    # --------------------------------------------------
    # Tick handler
    # --------------------------------------------------

    def _handle_tick(self, tick: dict):
        """
        Expected tick format (Dhan):
        {
            'symbol': 'NIFTY29MAY2026CE',
            'ltp': 175.0,
            'volume': 1234,
            'timestamp': 169...
        }
        """
        symbol = tick.get("symbol")
        ltp = tick.get("ltp")

        if symbol not in self.symbol_builders:
            return

        if ltp is None:
            return

        ts = datetime.now()

        builder = self.symbol_builders[symbol]

        builder.on_tick(
            price=float(ltp),
            volume=float(tick.get("volume", 0)),
            timestamp=ts,
        )
