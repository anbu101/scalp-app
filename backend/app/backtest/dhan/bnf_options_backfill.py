# backend/app/backtest/dhan/bnf_options_backfill.py
#
# Backfill BANKNIFTY OPTION candles into backtest_candles_1m via per-contract
# /charts/intraday (NOT the rolling endpoint — that returns empty for BANKNIFTY
# monthly). For each monthly expiry, for each strike in an ATM±band, for each
# side, look up the contract's securityId in the scrip master and fetch its
# intraday candles by securityId. Store under the ZERODHA BANKNIFTY symbol.
#
# ATM per day is taken from the BANKNIFTYFUT continuous series we already
# backfilled (instrument_type='FUT'), so the band tracks where price actually
# was. Far-OTM strikes not in the master, or that return empty (untraded), are
# skipped silently.
#
# DATA-ONLY. Overlap-safe delete-then-insert on (tradingsymbol, ts). Surrogate
# token in the reserved 9e9+ range.

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from app.event_bus.audit_logger import write_audit_log
from app.backtest.dhan.dhan_client import DhanDataClient, RollingSeries
from app.backtest.dhan.scrip_master import (
    download_master_text, parse_index_options, build_option_index,
    monthly_expiries_in_range,
)
from app.backtest.util.bnf_symbol import build_banknifty_symbol

IST = timezone(timedelta(hours=5, minutes=30))
_SURROGATE_BASE = 9_000_000_000
STRIKE_STEP = 100   # BANKNIFTY


def _surrogate_token(symbol: str) -> int:
    h = 0
    for ch in symbol:
        h = (h * 131 + ord(ch)) & 0x7FFFFFFF
    return _SURROGATE_BASE + h


def _day_bounds(day: date):
    lo = int(datetime(day.year, day.month, day.day, tzinfo=IST).timestamp())
    return lo, lo + 86400


def _atm_for_expiry_window(conn, expiry: date, wstart: date, wend: date) -> Optional[int]:
    """Median BANKNIFTYFUT close over the window → ATM anchor (rounded to step).
    Uses the FUT series we already stored. Returns None if no FUT data."""
    lo, _ = _day_bounds(wstart)
    _, hi = _day_bounds(wend)
    rows = conn.execute(
        """
        SELECT close FROM backtest_candles_1m
        WHERE tradingsymbol = 'BANKNIFTYFUT' AND ts >= ? AND ts < ?
        ORDER BY close
        """,
        (lo, hi),
    ).fetchall()
    if not rows:
        return None
    mid = rows[len(rows) // 2][0]
    return int(round(mid / STRIKE_STEP) * STRIKE_STEP)


def _chunks(d0: date, d1: date, span_days: int = 80):
    cur = d0
    while cur <= d1:
        end = min(cur + timedelta(days=span_days), d1)
        yield cur, end
        if end >= d1:
            break
        cur = end + timedelta(days=1)


def backfill_banknifty_options(
    *,
    db_path: str,
    client: DhanDataClient,
    date_from: date,
    date_to: date,
    atm_band: int = 50,                 # ATM±50 strikes (step 100) = ±5000 pts
    underlying: str = "BANKNIFTY",
    master_text: Optional[str] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    """Backfill BANKNIFTY options around ATM for each monthly expiry in range."""
    if master_text is None:
        master_text = download_master_text()
    contracts = parse_index_options(master_text, underlying)
    if not contracts:
        return {"aborted": True, "reason": f"no {underlying} OPTIDX in master",
                "rows_upserted": 0, "expiries": [], "errors": []}
    index = build_option_index(contracts)
    expiries = monthly_expiries_in_range(contracts, date_from, date_to)
    if not expiries:
        return {"aborted": True,
                "reason": f"no {underlying} monthly expiries intersect "
                          f"{date_from}..{date_to}",
                "rows_upserted": 0, "expiries": [], "errors": []}

    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    cur = conn.cursor()

    rows_upserted = 0
    errors: List[str] = []
    expiries_done: List[str] = []
    days_seen = set()
    strikes_with_data = 0
    strikes_empty = 0

    # For each expiry, fetch the month window (prev-expiry+1 .. this expiry).
    prev_exp = None
    # plan size for progress (strikes×2 per expiry)
    per_exp_contracts = (2 * atm_band + 1) * 2
    planned = len(expiries) * per_exp_contracts
    done = 0

    for expiry in expiries:
        # window for this monthly contract
        wstart = (prev_exp + timedelta(days=1)) if prev_exp else (expiry - timedelta(days=45))
        wstart = max(wstart, date_from)
        wend = min(expiry, date_to)
        prev_exp = expiry
        if wstart > wend:
            continue

        atm = _atm_for_expiry_window(cur.connection if hasattr(cur, "connection") else conn,
                                     expiry, wstart, wend)
        if atm is None:
            errors.append(f"{expiry}: no BANKNIFTYFUT data to anchor ATM "
                          f"(backfill futures for this window first)")
            done += per_exp_contracts
            continue

        strikes = [atm + i * STRIKE_STEP for i in range(-atm_band, atm_band + 1)]
        for strike in strikes:
            for side in ("CE", "PE"):
                if cancel_cb and cancel_cb():
                    conn.commit(); conn.close()
                    return {"cancelled": True, "rows_upserted": rows_upserted,
                            "expiries": expiries_done, "errors": errors}
                done += 1
                if progress_cb and (done % 10 == 0 or done == planned):
                    progress_cb({"done": done, "planned": planned,
                                 "expiry": expiry.isoformat(), "strike": strike,
                                 "side": side, "rows": rows_upserted})

                secid = index.get((expiry.isoformat(), strike, side))
                if not secid:
                    continue  # strike not listed in master — skip
                # fetch the whole window in <=80d chunks (usually 1)
                for (cf, ct) in _chunks(wstart, wend):
                    f_str = cf.strftime("%Y-%m-%d 09:00:00")
                    t_str = ct.strftime("%Y-%m-%d 15:40:00")
                    try:
                        series = client.fetch_intraday(
                            security_id=secid, from_date=f_str, to_date=t_str,
                            interval="1", instrument="OPTIDX",
                            exchange_segment="NSE_FNO", oi=True,
                        )
                    except Exception as ex:
                        errors.append(f"{expiry} {strike}{side} {cf}..{ct}: {ex}")
                        continue
                    if not series or len(series) == 0:
                        strikes_empty += 1
                        continue
                    n = _write_opt_series(cur, series, expiry, strike, side,
                                          underlying, days_seen)
                    rows_upserted += n
                    if n:
                        strikes_with_data += 1
        conn.commit()
        expiries_done.append(expiry.isoformat())

    conn.commit()
    conn.close()

    report = {
        "rows_upserted": rows_upserted,
        "expiries": expiries_done,
        "days_covered": len(days_seen),
        "strikes_with_data": strikes_with_data,
        "strikes_empty": strikes_empty,
        "errors": errors,
    }
    write_audit_log(
        f"[BNF_OPT_BACKFILL] {underlying}: {rows_upserted} rows, "
        f"{len(expiries_done)} expiries, {strikes_with_data} strikes w/data, "
        f"{len(errors)} errors"
    )
    return report


def _write_opt_series(cur, series: RollingSeries, expiry: date, strike: int,
                      side: str, underlying: str, days_seen: set) -> int:
    symbol = build_banknifty_symbol(expiry, strike, side)
    token = _surrogate_token(symbol)
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
               expiry=excluded.expiry, instrument_type=excluded.instrument_type
            """,
            (token, ts, underlying, symbol, side, float(strike),
             expiry.isoformat(), float(o), float(h), float(lo), float(c), vol, oi),
        )
        written += 1
        days_seen.add(datetime.fromtimestamp(ts, IST).date().isoformat())
    return written