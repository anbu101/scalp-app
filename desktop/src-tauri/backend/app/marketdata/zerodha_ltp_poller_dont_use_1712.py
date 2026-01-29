import time
from typing import Dict, List

from kiteconnect import KiteConnect

from app.candles.candle_builder import CandleBuilder
from app.candles.candle_store import CandleStore
from app.engine.trade_engine import TradeEngine


class ZerodhaLTPPoller:
    """
    Polls LTP from Zerodha and builds candles.
    One poller = one instrument.
    """

    def __init__(
        self,
        kite: KiteConnect,
        instrument_token: int,
        exchange: str,
        timeframes_sec: List[int],
        candle_base_dir: str,
        poll_interval: float = 1.0,
        symbol: str | None = None,
        slot: str = "AUTO",
    ):
        self.kite = kite
        self.instrument_token = instrument_token
        self.exchange = exchange
        self.poll_interval = poll_interval

        self.symbol = symbol or str(instrument_token)
        self.slot = slot

        # Candle infra
        self.builders: Dict[int, CandleBuilder] = {}
        self.stores: Dict[int, CandleStore] = {}

        # Strategy pipeline (one per token)
        self.trade_engine = TradeEngine(
            symbol=self.symbol,
            slot=self.slot,
        )

        for tf in timeframes_sec:
            store = CandleStore(
                base_dir=candle_base_dir,
                exchange=exchange,
                instrument_token=instrument_token,
                timeframe_sec=tf,
            )

            last_ts = store.load_last_candle_end_ts()

            builder = CandleBuilder(
                instrument_token=instrument_token,
                timeframe_sec=tf,
                last_candle_end_ts=last_ts,
            )

            self.builders[tf] = builder
            self.stores[tf] = store

    # --------------------------------------------------

    def run(self):
        print(f"[LTP] Starting poller for token={self.instrument_token}")

        while True:
            try:
                ltp_data = self.kite.ltp([self.instrument_token])
                ltp = ltp_data[str(self.instrument_token)]["last_price"]
                ts = int(time.time())

                for tf, builder in self.builders.items():
                    candle = builder.on_tick(ltp, ts)

                    if candle:
                        # 1️⃣ Persist candle
                        self.stores[tf].append(candle)

                        # 2️⃣ Feed strategy pipeline
                        self.trade_engine.on_candle(candle)

                        print(
                            f"[CANDLE] token={self.instrument_token} "
                            f"tf={tf}s end={candle.end_ts} "
                            f"O={candle.open} H={candle.high} "
                            f"L={candle.low} C={candle.close}"
                        )

                time.sleep(self.poll_interval)

            except Exception as e:
                print("[LTP ERROR]", e)
                time.sleep(2)
