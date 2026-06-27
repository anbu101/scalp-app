# backend/app/backtest/bb/bt_candle_agg.py
#
# Aggregate corpus 1-minute candles into 3-minute candles EXACTLY as the live
# CandleBuilder does, so the indicator bundle sees identical bars.
#
# Live CandleBuilder: bucket_start = (ts // tf) * tf, tf=180. OHLC = first open,
# max high, min low, last close within the bucket. We reproduce this by grouping
# our 1m bars by floor(ts/180)*180. Since 1m bars sit on :00 second boundaries,
# exactly three 1m bars [b, b+60, b+120] map to each 3m bucket b — a clean tile.

from __future__ import annotations
from dataclasses import dataclass
from typing import List


@dataclass
class Bar:
    start_ts: int
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    oi: int = 0


def aggregate_1m_to_3m(bars_1m: List[Bar]) -> List[Bar]:
    """Group ascending 1m bars into 3m bars on the floor(ts/180) grid.
    Bars must be ascending by start_ts. Partial buckets (fewer than 3 bars at a
    session edge) still emit a bar from whatever 1m bars fall in the bucket —
    matching live, where the builder emits whatever ticks arrived."""
    TF = 180
    out: List[Bar] = []
    cur_bucket = None
    o = h = l = c = None
    vol = oi = 0
    for b in bars_1m:
        bucket = (b.start_ts // TF) * TF
        if cur_bucket is None:
            cur_bucket = bucket
            o, h, l, c = b.open, b.high, b.low, b.close
            vol, oi = b.volume, b.oi
        elif bucket == cur_bucket:
            h = max(h, b.high)
            l = min(l, b.low)
            c = b.close
            vol += b.volume
            oi = b.oi  # last
        else:
            out.append(Bar(cur_bucket, o, h, l, c, vol, oi))
            cur_bucket = bucket
            o, h, l, c = b.open, b.high, b.low, b.close
            vol, oi = b.volume, b.oi
    if cur_bucket is not None:
        out.append(Bar(cur_bucket, o, h, l, c, vol, oi))
    return out