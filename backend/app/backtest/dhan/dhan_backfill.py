# backend/app/backtest/dhan/dhan_backfill.py
#
# Backfill backtest.db's backtest_candles_1m table from Dhan's rolling expired-
# options data. Dhan is DATA-ONLY (never an order path). This fills the corpus
# with the REAL per-day front-week contracts live would have traded — closing
# the gap where Zerodha can't return expired-weekly history.
#
# MODEL (confirmed live):
#   For each strike offset in ATM-N..ATM+N and each side (CALL/PUT), we fetch a
#   ROLLING series over a <=30-day window. Dhan returns, per 1-min candle, the
#   absolute strike + spot for the front (expiryCode=1) weekly AS OF that day.
#   The series rolls identity at each Tuesday expiry. We therefore:
#     1) split candles by IST trading day,
#     2) compute each day's true expiry = expected_expiry_for_day(day)
#        (front Tuesday; monthly if last Tue of month),
#     3) synthesize the Zerodha tradingsymbol via build_nifty_symbol,
#     4) upsert each candle into backtest_candles_1m.
#
# Idempotent AND overlap-safe: each candle is written delete-then-insert keyed
# on (tradingsymbol, ts), so Dhan is authoritative for any minute it writes and
# can never double-count against an existing Zerodha row for the same
# contract-minute. Dhan rows still carry a STABLE surrogate token (reserved high
# range, no Kite collision) so the PK holds and re-runs don't duplicate.

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from app.event_bus.audit_logger import write_audit_log
from app.backtest.dhan.dhan_client import DhanDataClient, RollingSeries
from app.backtest.engine.expiry_calendar import expected_expiry_for_day
from app.backtest.util.nifty_symbol import build_nifty_symbol, is_monthly_expiry

IST = timezone(timedelta(hours=5, minutes=30))

# Surrogate instrument_token space for Dhan-sourced rows. Real Zerodha NFO
# tokens are well below this; 9_000_000_000+ cannot collide.
_SURROGATE_BASE = 9_000_000_000


def _surrogate_token(tradingsymbol: str) -> int:
    """Stable per-symbol surrogate token (deterministic hash in reserved range)."""
    h = 0
    for ch in tradingsymbol:
        h = (h * 131 + ord(ch)) & 0x7FFFFFFF
    return _SURROGATE_BASE + h


def _ist_day(epoch: int) -> date:
    return datetime.fromtimestamp(epoch, IST).date()


def _chunks(d0: date, d1: date, span_days: int = 25):
    """Yield (from,to) <=span_days windows; to is non-inclusive per Dhan."""
    cur = d0
    while cur <= d1:
        end = min(cur + timedelta(days=span_days), d1 + timedelta(days=1))
        yield cur, end
        cur = end


def _offsets(n: int) -> List[str]:
    out = ["ATM"]
    for i in range(1, n + 1):
        out.append(f"ATM+{i}")
        out.append(f"ATM-{i}")
    return out


def backfill_nifty_dhan(
    *,
    db_path: str,
    client: DhanDataClient,
    date_from: date,
    date_to: date,
    atm_window: int = 10,           # ATM±10 (Dhan's max for index near expiry)
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    """Backfill NIFTY weekly option candles from Dhan into backtest_candles_1m.

    Returns a report: {requests, rows_upserted, days_covered, expiries, errors}.
    """
    offsets = _offsets(atm_window)
    sides = [("CALL", "CE"), ("PUT", "PE")]
    total_calls = 0
    rows_upserted = 0
    errors: List[str] = []
    expiries_seen = set()
    days_seen = set()

    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    cur = conn.cursor()

    # Plan calls: per chunk × per offset × per side. expiryCode=1 (front weekly).
    chunk_list = list(_chunks(date_from, date_to))
    planned = len(chunk_list) * len(offsets) * len(sides)
    done = 0

    UPSERT = """
        INSERT INTO backtest_candles_1m
          (instrument_token, ts, underlying, tradingsymbol, instrument_type,
           strike, expiry, open, high, low, close, volume, oi)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(instrument_token, ts) DO UPDATE SET
           open=excluded.open, high=excluded.high, low=excluded.low,
           close=excluded.close, volume=excluded.volume, oi=excluded.oi,
           tradingsymbol=excluded.tradingsymbol, strike=excluded.strike,
           expiry=excluded.expiry, instrument_type=excluded.instrument_type
    """

    for (cfrom, cto) in chunk_list:
        # Determine WEEK vs MONTH per the front expiry of the chunk's first day.
        # The rolling series rolls daily, so we always request WEEK with code 1 to
        # follow the front weekly; monthly expiries are themselves weeklies in the
        # WEEK series (the last Tue of a month), so WEEK code 1 still returns the
        # correct front contract on those days. (We classify per-DAY at write time
        # via expected_expiry_for_day + is_monthly_expiry for the symbol.)
        for off in offsets:
            for (dhan_side, type_code) in sides:
                if cancel_cb and cancel_cb():
                    conn.commit(); conn.close()
                    return {"cancelled": True, "requests": total_calls,
                            "rows_upserted": rows_upserted,
                            "days_covered": len(days_seen),
                            "expiries": sorted(expiries_seen), "errors": errors}
                done += 1
                if progress_cb and (done % 5 == 0 or done == planned):
                    progress_cb({"done": done, "planned": planned,
                                 "chunk": f"{cfrom}..{cto}", "offset": off,
                                 "side": type_code, "rows": rows_upserted})
                try:
                    series = client.fetch_rolling_option(
                        expiry_flag="WEEK", expiry_code=1, strike=off,
                        option_type=dhan_side,
                        from_date=cfrom.isoformat(), to_date=cto.isoformat(),
                    )
                    total_calls += 1
                except Exception as e:
                    errors.append(f"{cfrom}..{cto} {off} {type_code}: {e}")
                    continue
                if not series:
                    continue

                rows_upserted += _write_series(
                    cur, series, type_code, days_seen, expiries_seen)
        conn.commit()  # commit per chunk

    conn.commit()
    conn.close()

    report = {
        "requests": total_calls,
        "rows_upserted": rows_upserted,
        "days_covered": len(days_seen),
        "expiries": sorted(expiries_seen),
        "errors": errors,
    }
    write_audit_log(
        f"[DHAN_BACKFILL] done: {total_calls} calls, {rows_upserted} rows, "
        f"{len(days_seen)} days, {len(expiries_seen)} expiries, {len(errors)} errors"
    )
    return report


def _write_series(cur, series: RollingSeries, type_code: str,
                  days_seen: set, expiries_seen: set) -> int:
    """Split a rolling series by day, assign each day its front-week expiry,
    synthesize the symbol, and upsert. Returns rows written."""
    n = len(series)
    written = 0
    for i in range(n):
        ts = series.timestamp[i]
        # Skip incomplete rows defensively.
        if i >= len(series.strike) or i >= len(series.close):
            continue
        strike = series.strike[i]
        if not strike:
            continue
        d = _ist_day(ts)
        expiry = expected_expiry_for_day(d)   # front-week Tuesday for that day
        symbol = build_nifty_symbol(expiry, strike, type_code)
        token = _surrogate_token(symbol)

        o = series.open[i] if i < len(series.open) else series.close[i]
        h = series.high[i] if i < len(series.high) else series.close[i]
        lo = series.low[i] if i < len(series.low) else series.close[i]
        c = series.close[i]
        vol = int(series.volume[i]) if i < len(series.volume) and series.volume[i] else 0
        oi = int(series.oi[i]) if i < len(series.oi) and series.oi[i] else 0

        # OVERLAP-SAFE WRITE (delete-then-insert by symbol+ts):
        # The corpus PK is (instrument_token, ts). Zerodha rows carry real Kite
        # tokens; Dhan rows carry surrogate tokens — DIFFERENT PKs for the SAME
        # contract-minute. Without this delete, a Dhan backfill over dates the
        # Kite backfill already covers would leave TWO rows for the same
        # (tradingsymbol, ts), and the candle source (which keys on symbol/ts,
        # not token) would DOUBLE-COUNT that minute in replay.
        #
        # So we delete ANY existing row for this (tradingsymbol, ts) first —
        # Zerodha's or a prior Dhan run's — making Dhan authoritative for every
        # minute it writes. Re-runs stay idempotent (delete is a no-op the second
        # time except it removes the prior identical Dhan row, then re-inserts).
        cur.execute(
            "DELETE FROM backtest_candles_1m WHERE tradingsymbol = ? AND ts = ?",
            (symbol, ts),
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
               tradingsymbol=excluded.tradingsymbol, strike=excluded.strike,
               expiry=excluded.expiry, instrument_type=excluded.instrument_type
            """,
            (token, ts, "NIFTY", symbol, type_code, float(strike),
             expiry.isoformat(), float(o), float(h), float(lo), float(c), vol, oi),
        )
        written += 1
        days_seen.add(d.isoformat())
        expiries_seen.add(expiry.isoformat())
    return written