import threading
from typing import List

from kiteconnect import KiteConnect

from app.marketdata.zerodha_ltp_poller import ZerodhaLTPPoller


class MultiOptionCandleEngine:
    """
    Runs candle builders for multiple option instruments in parallel.
    One poller thread per instrument token.
    """

    def __init__(
        self,
        kite: KiteConnect,
        instrument_tokens: List[int],
        exchange: str = "NFO",
        candle_base_dir: str = "app/logs/candles",
        timeframes_sec: List[int] = [60],   # 1m only (as requested)
        poll_interval: float = 1.0,
    ):
        self.kite = kite
        self.instrument_tokens = instrument_tokens
        self.exchange = exchange
        self.candle_base_dir = candle_base_dir
        self.timeframes_sec = timeframes_sec
        self.poll_interval = poll_interval

        self._threads: List[threading.Thread] = []

    # --------------------------------------------------

    def start(self):
        print(
            f"[ENGINE] Starting candle engine for "
            f"{len(self.instrument_tokens)} instruments"
        )

        for token in self.instrument_tokens:
            poller = ZerodhaLTPPoller(
                kite=self.kite,
                instrument_token=token,
                exchange=self.exchange,
                timeframes_sec=self.timeframes_sec,
                candle_base_dir=self.candle_base_dir,
                poll_interval=self.poll_interval,
            )

            t = threading.Thread(
                target=poller.run,
                name=f"Poller-{token}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)

        print("[ENGINE] Candle pollers started")

    # --------------------------------------------------

    def join(self):
        for t in self._threads:
            t.join()


# ======================================================
# PUBLIC ENTRYPOINT (THIS IS WHAT api_server IMPORTS)
# ======================================================

def start_candle_engine(kite: KiteConnect, instrument_tokens: List[int]):
    engine = MultiOptionCandleEngine(
        kite=kite,
        instrument_tokens=instrument_tokens,
    )
    engine.start()
    return engine
