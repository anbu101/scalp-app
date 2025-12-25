from typing import Optional
from app.marketdata.candle import Candle


class CandleBuilder:
    """
    Builds OHLC candles from LTP-only ticks for a fixed timeframe.
    """

    def __init__(
        self,
        instrument_token: int,
        timeframe_sec: int,
        last_candle_end_ts: Optional[int] = None,
        source: str = "LIVE_1M",
    ):
        self.instrument_token = instrument_token
        self.tf = timeframe_sec
        self.source = source

        self.current_start: Optional[int] = None
        self.current_end: Optional[int] = None

        self.o = None
        self.h = None
        self.l = None
        self.c = None

        self.last_emitted_end_ts = last_candle_end_ts

    def on_tick(self, ltp: float, ts: int) -> Optional[Candle]:
        bucket_start = (ts // self.tf) * self.tf
        bucket_end = bucket_start + self.tf

        if self.current_start is None:
            self._start_new(bucket_start, bucket_end, ltp)
            return None

        if bucket_start == self.current_start:
            self._update(ltp)
            return None

        finished = self._finish_current()
        self._start_new(bucket_start, bucket_end, ltp)

        if (
            self.last_emitted_end_ts is None
            or finished.end_ts > self.last_emitted_end_ts
        ):
            self.last_emitted_end_ts = finished.end_ts
            return finished

        return None

    def _start_new(self, start_ts: int, end_ts: int, ltp: float):
        self.current_start = start_ts
        self.current_end = end_ts
        self.o = self.h = self.l = self.c = ltp

    def _update(self, ltp: float):
        self.h = max(self.h, ltp)
        self.l = min(self.l, ltp)
        self.c = ltp

    from datetime import datetime, timezone, timedelta

    IST = timezone(timedelta(hours=5, minutes=30))

    def _finish_current(self) -> Candle:
        candle = Candle(
            start_ts=self.current_start,
            end_ts=self.current_end,
            open=self.o,
            high=self.h,
            low=self.l,
            close=self.c,
        )

        # -------- LOG WITH TIMESTAMP --------
        start_ist = datetime.fromtimestamp(self.current_start, IST)
        end_ist = datetime.fromtimestamp(self.current_end, IST)

        print(
            f"[CANDLE][1M]"
            f" token={self.instrument_token}"
            f" {start_ist.strftime('%H:%M:%S')} → {end_ist.strftime('%H:%M:%S')}"
            f" | O={self.o:.2f} H={self.h:.2f} L={self.l:.2f} C={self.c:.2f}"
        )

        # Reset
        self.current_start = None
        self.current_end = None
        self.o = self.h = self.l = self.c = None

        return candle

