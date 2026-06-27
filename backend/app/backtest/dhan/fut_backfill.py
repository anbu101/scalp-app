# backend/app/backtest/dhan/fut_backfill.py
#
# Backfill BANKNIFTY (or any index) FUTURES into backtest_candles_1m as a single
# CONTINUOUS front-month 1-minute series, for the BB backtest to read.
#
# WHY a continuous series: live BB resolves the CURRENT-MONTH BANKNIFTY future
# (resolve_current_month_banknifty_fut) and runs all indicators on that one
# series, rolling at each monthly expiry. We reconstruct the same: each month's
# contract contributes only its FRONT-MONTH window (prev expiry+1 .. own expiry),
# stitched into one continuous series under a single symbol 'BANKNIFTYFUT'.
#
# STORAGE: rows go in backtest_candles_1m with
#   underlying='BANKNIFTY', instrument_type='FUT', strike=0,
#   tradingsymbol='BANKNIFTYFUT' (continuous), expiry=<that month's expiry>.
# Overlap-safe delete-then-insert on (tradingsymbol, ts): each minute is owned by
# exactly one contract (the front-month one), so the stitch is clean and re-runs
# are idempotent.
#
# DATA-ONLY. Never an order path.

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from app.event_bus.audit_logger import write_audit_log
from app.backtest.dhan.dhan_client import DhanDataClient, RollingSeries
from app.backtest.dhan.scrip_master import (
    FutContract, download_master_text, parse_index_futures, front_month_windows,
)

IST = timezone(timedelta(hours=5, minutes=30))
_SURROGATE_BASE = 9_000_000_000

CONTINUOUS_SYMBOL = "BANKNIFTYFUT"   # one continuous front-month series


def _surrogate_token(symbol: str) -> int:
    h = 0
    for ch in symbol:
        h = (h * 131 + ord(ch)) & 0x7FFFFFFF
    return _SURROGATE_BASE + h


def _chunks(d0: date, d1: date, span_days: int = 80):
    """<=90-day windows (Dhan intraday cap); we use 80 for headroom. Inclusive
    end (we pass explicit end-of-day in the fetch)."""
    cur = d0
    while cur <= d1:
        end = min(cur + timedelta(days=span_days), d1)
        yield cur, end
        if end >= d1:
            break
        cur = end + timedelta(days=1)


def backfill_banknifty_futures(
    *,
    db_path: str,
    client: DhanDataClient,
    date_from: date,
    date_to: date,
    underlying: str = "BANKNIFTY",
    master_text: Optional[str] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    """Build the continuous front-month futures series for [date_from, date_to]
    and store it. Resolves contracts from the scrip master (downloaded unless
    master_text is supplied for testing). Returns a report."""
    if master_text is None:
        master_text = download_master_text()
    contracts = parse_index_futures(master_text, underlying)
    if not contracts:
        return {"aborted": True, "reason": f"no {underlying} FUTIDX contracts in master",
                "rows_upserted": 0, "contracts_used": [], "errors": []}

    # Keep only contracts whose front-month window intersects [date_from, date_to].
    windows = front_month_windows(contracts)
    sel = []
    for c, wstart, wend in windows:
        # clip window to requested range
        s = max(wstart, date_from)
        e = min(wend, date_to)
        if s <= e:
            sel.append((c, s, e))
    if not sel:
        return {"aborted": True,
                "reason": f"no {underlying} front-month windows intersect "
                          f"{date_from}..{date_to} (master lists "
                          f"{[c.symbol for c in contracts]})",
                "rows_upserted": 0, "contracts_used": [], "errors": []}

    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    cur = conn.cursor()

    rows_upserted = 0
    errors: List[str] = []
    contracts_used: List[str] = []
    days_seen = set()

    # plan = sum of chunks across selected contracts
    plan = []
    for (c, s, e) in sel:
        for (cf, ct) in _chunks(s, e):
            plan.append((c, cf, ct))
    planned = len(plan)
    done = 0

    for (c, cfrom, cto) in plan:
        if cancel_cb and cancel_cb():
            conn.commit(); conn.close()
            return {"cancelled": True, "rows_upserted": rows_upserted,
                    "contracts_used": contracts_used, "errors": errors}
        done += 1
        if progress_cb:
            progress_cb({"done": done, "planned": planned,
                         "contract": c.symbol, "window": f"{cfrom}..{cto}",
                         "rows": rows_upserted})
        # Dhan intraday: fromDate inclusive, toDate end-of-day to capture full day
        f_str = cfrom.strftime("%Y-%m-%d 09:00:00")
        t_str = cto.strftime("%Y-%m-%d 15:40:00")
        try:
            series = client.fetch_intraday_futures(
                security_id=c.security_id, from_date=f_str, to_date=t_str,
                interval="1", instrument="FUTIDX", exchange_segment="NSE_FNO",
                oi=True,
            )
        except Exception as ex:
            errors.append(f"{c.symbol} {cfrom}..{cto}: {ex}")
            continue
        if not series:
            continue
        n = _write_fut_series(cur, series, c.expiry, underlying, days_seen)
        rows_upserted += n
        if c.symbol not in contracts_used:
            contracts_used.append(c.symbol)
        conn.commit()

    conn.commit()
    conn.close()

    report = {
        "rows_upserted": rows_upserted,
        "contracts_used": contracts_used,
        "days_covered": len(days_seen),
        "errors": errors,
        "symbol": CONTINUOUS_SYMBOL,
    }
    write_audit_log(
        f"[FUT_BACKFILL] {underlying} continuous: {rows_upserted} rows, "
        f"{len(contracts_used)} contracts, {len(days_seen)} days, "
        f"{len(errors)} errors"
    )
    return report


def _write_fut_series(cur, series: RollingSeries, expiry: date,
                      underlying: str, days_seen: set) -> int:
    """Write a futures series under the continuous symbol with overlap-safe
    delete-then-insert on (tradingsymbol, ts)."""
    token = _surrogate_token(CONTINUOUS_SYMBOL)
    written = 0
    n = len(series)
    for i in range(n):
        ts = series.timestamp[i]
        if i >= len(series.close):
            continue
        o = series.open[i] if i < len(series.open) else series.close[i]
        h = series.high[i] if i < len(series.high) else series.close[i]
        lo = series.low[i] if i < len(series.low) else series.close[i]
        c = series.close[i]
        vol = int(series.volume[i]) if i < len(series.volume) and series.volume[i] else 0
        oi = int(series.oi[i]) if i < len(series.oi) and series.oi[i] else 0

        cur.execute(
            "DELETE FROM backtest_candles_1m WHERE tradingsymbol = ? AND ts = ?",
            (CONTINUOUS_SYMBOL, ts),
        )
        cur.execute(
            """
            INSERT INTO backtest_candles_1m
              (instrument_token, ts, underlying, tradingsymbol, instrument_type,
               strike, expiry, open, high, low, close, volume, oi)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(instrument_token, ts) DO UPDATE SET
               open=excluded.open, high=excluded.high, low=excluded.low,
               close=excluded.close, volume=excluded.volume, oi=excluded.oi,
               expiry=excluded.expiry
            """,
            (token, ts, underlying, CONTINUOUS_SYMBOL, "FUT",
             0.0, expiry.isoformat(), float(o), float(h), float(lo), float(c), vol, oi),
        )
        written += 1
        days_seen.add(datetime.fromtimestamp(ts, IST).date().isoformat())
    return written