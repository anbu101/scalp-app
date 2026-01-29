from typing import Optional
from app.marketdata.candle import Candle
from app.event_bus.audit_logger import write_audit_log


class CandleBuilder:
    """
    Builds OHLC candles from LTP-only ticks using STRICT time buckets.
    Timeframe is in seconds (e.g. 60 = 1 minute).

    This implementation is TradingView-parity:
    - Candle boundaries are bucket-based, NOT tick-driven
    - Late ticks do NOT shift candle closes
    """

    def __init__(
        self,
        instrument_token: int,
        timeframe_sec: int,
        last_candle_end_ts: Optional[int] = None,
    ):
        self.instrument_token = instrument_token
        self.tf = int(timeframe_sec)

        self.current_bucket_start: Optional[int] = None
        self.current_bucket_end: Optional[int] = None

        self.o: Optional[float] = None
        self.h: Optional[float] = None
        self.l: Optional[float] = None
        self.c: Optional[float] = None
        
        self.last_emitted_end_ts = (
            int(last_candle_end_ts)
            if last_candle_end_ts is not None
            else None
        )

    # --------------------------------------------------

    def on_tick(self, ltp: float, ts) -> Optional[Candle]:
        try:
            ts = int(ts)
        except Exception:
            return None

        bucket_start = (ts // self.tf) * self.tf
        bucket_end = bucket_start + self.tf

        # First tick ever
        if self.current_bucket_start is None:
            self._start_bucket(bucket_start, bucket_end, ltp)
            return None

        # Still inside same bucket
        if bucket_start == self.current_bucket_start:
            self._update_ohlc(ltp)
            return None

        # -----------------------------
        # BUCKET ROLLOVER
        # -----------------------------
        finished = Candle(
            start_ts=self.current_bucket_start,
            end_ts=self.current_bucket_end,
            open=self.o,
            high=self.h,
            low=self.l,
            close=self.c,
            source="ZERODHA_WS",
        )

        # ✅ SAFE: finished exists here
        #write_audit_log(
            #f"[CANDLE][EMIT] token={self.instrument_token} "
            #f"start={finished.start_ts} end={finished.end_ts}"
        #)

        # Start new bucket FIRST
        self._start_bucket(bucket_start, bucket_end, ltp)

        # Dedup guard
        if (
            self.last_emitted_end_ts is None
            or finished.end_ts > self.last_emitted_end_ts
        ):
            self.last_emitted_end_ts = finished.end_ts
            return finished

        return None


    # --------------------------------------------------
    # Internal helpers
    # --------------------------------------------------

    def _start_bucket(self, start_ts: int, end_ts: int, ltp: float):
        self.current_bucket_start = start_ts
        self.current_bucket_end = end_ts
        self.o = ltp
        self.h = ltp
        self.l = ltp
        self.c = ltp

    def _update_ohlc(self, ltp: float):
        if ltp > self.h:
            self.h = ltp
        if ltp < self.l:
            self.l = ltp
        self.c = ltp
