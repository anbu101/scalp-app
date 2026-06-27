# backend/app/backtest/bb/bt_pivots.py
#
# Compute session-frozen pivots for the backtest the SAME way PivotCache does
# live — but sourcing the previous trading day's daily OHLC from the corpus
# (BANKNIFTYFUT) instead of a live kite.historical_data() call.
#
# Live formulas (verified from pivot_cache.py):
#   pp = (h + l + c) / 3
#   r1 = 2*pp - l        r2 = pp + (h - l)
#   s1 = 2*pp - h        s2 = pp - (h - l)        s3 = s1 - (h - l)
# where h/l/c are the PREVIOUS trading day's daily High/Low/Close.

from __future__ import annotations
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Optional

IST = timezone(timedelta(hours=5, minutes=30))
FUT_SYMBOL = "BANKNIFTYFUT"


def _day_bounds(d: date):
    lo = int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())
    return lo, lo + 86400


def _prev_trading_day_ohlc(conn, sym: str, sim_day: date, max_lookback: int = 10):
    """Daily H/L/C of the most recent trading day strictly BEFORE sim_day that
    has FUT candles in the corpus. Returns (prev_day, h, l, c) or None."""
    cur = conn.cursor()
    cand = sim_day - timedelta(days=1)
    for _ in range(max_lookback):
        lo, hi = _day_bounds(cand)
        row = cur.execute(
            """
            SELECT MAX(high), MIN(low),
                   (SELECT close FROM backtest_candles_1m
                     WHERE tradingsymbol=? AND ts>=? AND ts<? ORDER BY ts DESC LIMIT 1)
            FROM backtest_candles_1m
            WHERE tradingsymbol=? AND ts>=? AND ts<?
            """,
            (sym, lo, hi, sym, lo, hi),
        ).fetchone()
        if row and row[0] is not None and row[2] is not None:
            return cand, row[0], row[1], row[2]
        cand -= timedelta(days=1)
    return None


def pivots_for_day(conn, sim_day: date, sym: str = FUT_SYMBOL) -> Optional[Dict[str, float]]:
    """Session-frozen pivots for sim_day, from the prior trading day's daily OHLC.
    Same formulas as live PivotCache. Returns dict or None if no prior data."""
    prev = _prev_trading_day_ohlc(conn, sym, sim_day)
    if not prev:
        return None
    _, h, l, c = prev
    pp = (h + l + c) / 3
    r1 = 2 * pp - l
    r2 = pp + (h - l)
    s1 = 2 * pp - h
    s2 = pp - (h - l)
    s3 = s1 - (h - l)
    return {"pp": pp, "r1": r1, "r2": r2, "s1": s1, "s2": s2, "s3": s3}