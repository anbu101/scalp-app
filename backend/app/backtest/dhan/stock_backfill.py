# backend/app/backtest/dhan/stock_backfill.py
#
# ── STOCK_BACKFILL (2026-08-15, GC-on-stocks trial) ── one F&O stock's SPOT
# 1m + expired MONTHLY option 1m candles → backtest_candles_1m, via Dhan.
# First target: DIXON. DATA-ONLY (the dhan/ charter): zero order capability,
# live trading remains 100% Zerodha.
#
# Reuses the two confirmed Dhan contracts already encoded in this package:
#   * OPTIONS — DhanDataClient.fetch_rolling_option, now with
#     instrument="OPTSTK", expiryFlag="MONTH", expiryCode=1 (1-BASED!),
#     strikes ATM±N, ≤30-day windows. Dhan resolves the ABSOLUTE strike per
#     candle; we split by IST day, stamp each day's front monthly expiry via
#     expected_stock_monthly_expiry_for_day (era-aware last Thu→Tue), and
#     synthesize the Zerodha-style monthly symbol (DIXON25AUG14000CE).
#     SELF-CONSISTENCY DOCTRINE: the SAME calendar function will drive the
#     stock-mode runner's want_expiry, so holiday-shifted real-world expiry
#     dates (not modeled) cancel out — corpus and selector cannot disagree.
#   * SPOT — /v2/charts/intraday with the stock's NSE_EQ EQUITY securityId,
#     the dhan_spot_backfill mechanics copied faithfully: one-day 5m probe
#     decides START- vs CLOSE-anchored vendor stamps (a silent 1-minute shift
#     would corrupt every close-decision downstream), in-batch dedupe LAST
#     wins, 14-day chunks, CAS-era 15:45 session bound.
#
# Identity + idempotency (dhan_backfill doctrine): surrogate tokens in the
# reserved 9e9 range, delete-then-insert keyed on (tradingsymbol, ts) for
# option rows so re-runs and any future Kite overlap can never double-count a
# contract-minute; spot rows upsert on the surrogate PK.
#
# securityId + LOT_SIZE come from the public detailed scrip master
# (scrip_master._MASTER_URL, no auth): the stock's NSE EQ row for spot, any
# OPTSTK row with UNDERLYING_SYMBOL == <stock> for the rolling underlying id
# and the CURRENT lot size. Historical lot revisions are NOT modeled — the
# printed lot is a present-day fact, recorded in the report for the runner
# work to come.
#
# RUN (from the repo backend/, needs requests + a live Dhan token):
#   python3 -m app.backtest.dhan.stock_backfill \
#       --underlying DIXON --client-id 100xxxxxxx --access-token eyJ... \
#       [--from 2021-08-16] [--to 2026-08-14] [--atm-window 10]
#       [--spot-only | --options-only] [--db ~/.scalp-app/backtest/backtest.db]
# Token can also come from DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN env vars.

from __future__ import annotations

import csv
import io
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

import requests

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:                                        # CLI outside app ctx
    def write_audit_log(msg: str) -> None:                 # type: ignore
        print(msg)

from app.backtest.dhan.dhan_client import DhanDataClient, RollingSeries
from app.backtest.dhan.scrip_master import download_master_text
from app.backtest.engine.expiry_calendar import (
    expected_stock_monthly_expiry_for_day,
)

IST = timezone(timedelta(hours=5, minutes=30))
_INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
_SURROGATE_BASE = 9_000_000_000
_SESSION_FROM_HM = "09:15:00"
_SESSION_TO_HM = "15:45:00"     # CAS_2026 bound, same as dhan_spot_backfill
_SPOT_CHUNK_DAYS = 14
_OPT_CHUNK_DAYS = 25

_MON3 = {1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
         7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC"}

_ERROR_HINTS = {
    "806": "Data APIs not subscribed",
    "807": "Access token expired — regenerate on web.dhan.co",
    "808": "Authentication failed — client id or access token invalid",
    "809": "Access token invalid",
    "813": "Invalid securityId",
    "DH-905": "Input exception — bad/missing parameter",
}


class StockBackfillError(Exception):
    pass


# ── SCHEMA (verbatim from repo/schema.sql) ── a per-stock corpus DB is born
# empty; the NIFTY backfills always wrote into an app-initialized backtest.db
# so they never needed this. CREATE IF NOT EXISTS — harmless on the main DB,
# and keeps the corpus DDL bit-identical to production so CandleSource and
# the runners see exactly one shape everywhere.
_CANDLES_DDL = """
CREATE TABLE IF NOT EXISTS backtest_candles_1m (
    instrument_token  INTEGER NOT NULL,
    ts                INTEGER NOT NULL,
    underlying        TEXT    NOT NULL,
    tradingsymbol     TEXT    NOT NULL,
    instrument_type   TEXT    NOT NULL,
    strike            REAL    NOT NULL,
    expiry            TEXT    NOT NULL,
    open              REAL    NOT NULL,
    high              REAL    NOT NULL,
    low               REAL    NOT NULL,
    close             REAL    NOT NULL,
    volume            INTEGER NOT NULL DEFAULT 0,
    oi                INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (instrument_token, ts)
);
CREATE INDEX IF NOT EXISTS idx_bt1m_sym_ts
    ON backtest_candles_1m (tradingsymbol, ts);
CREATE INDEX IF NOT EXISTS idx_bt1m_under_exp_ts
    ON backtest_candles_1m (underlying, expiry, ts);
CREATE INDEX IF NOT EXISTS idx_bt1m_under_type_ts
    ON backtest_candles_1m (underlying, instrument_type, ts);
"""
# ^ idx_bt1m_under_type_ts is CORPUS-specific (not in repo/schema.sql): in a
# single-underlying db the (underlying, ...) prefixes of the two production
# indexes select the WHOLE db, so the per-day SPOT query (underlying +
# instrument_type + ts range) degenerates to a full scan per day — 13.5s/day
# on the 13M-row DIXON corpus, 2026-08-15. This index makes it a flat ~0.4ms.



def _ensure_candles_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_CANDLES_DDL)
    conn.commit()


def _surrogate_token(key: str) -> int:
    h = 0
    for ch in key:
        h = (h * 131 + ord(ch)) & 0x7FFFFFFF
    return _SURROGATE_BASE + h


def _ist_day(epoch: int) -> date:
    return datetime.fromtimestamp(epoch, IST).date()


def build_stock_symbol(underlying: str, expiry: date, strike, t: str) -> str:
    """Zerodha monthly OPTSTK format: DIXON25AUG14000CE. Fractional strikes
    (2.5-spacing stocks) keep their decimals; integral strikes render bare."""
    f = float(strike)
    s = str(int(round(f))) if abs(f - round(f)) < 1e-9 else \
        (f"{f}".rstrip("0").rstrip("."))
    return f"{underlying}{expiry.year % 100:02d}{_MON3[expiry.month]}{s}{t.upper()}"


# ── SCRIP MASTER RESOLUTION ─────────────────────────────────────────────────

def resolve_stock_ids(underlying: str,
                      master_text: Optional[str] = None) -> Dict:
    """From the public detailed scrip master: the stock's NSE EQ securityId
    (spot fetches), the OPTSTK underlying securityId (rolling fetches), and
    the CURRENT lot size. Fails loudly when the stock has no OPTSTK rows —
    a not-in-F&O stock cannot be backfilled, better to say so than to spin
    through 3000 empty calls."""
    text = master_text if master_text is not None else download_master_text()
    rdr = csv.DictReader(io.StringIO(text))
    eq_id = None
    und_id = None
    lot = None
    opt_rows = 0
    u = underlying.upper().strip()
    for row in rdr:
        try:
            if (row.get("EXCH_ID") or "").strip() != "NSE":
                continue
            instr = (row.get("INSTRUMENT") or "").strip()
            if (instr == "EQUITY"
                    and (row.get("UNDERLYING_SYMBOL") or "").strip().upper() == u
                    and (row.get("SERIES") or "").strip() == "EQ"
                    and eq_id is None):
                eq_id = (row.get("SECURITY_ID") or "").strip()
            elif (instr == "OPTSTK"
                    and (row.get("UNDERLYING_SYMBOL") or "").strip().upper() == u):
                opt_rows += 1
                if und_id is None:
                    und_id = (row.get("UNDERLYING_SECURITY_ID") or "").strip()
                if lot is None:
                    try:
                        lot = int(float(row.get("LOT_SIZE") or 0)) or None
                    except Exception:
                        pass
        except Exception:
            continue
    if not und_id or not opt_rows:
        raise StockBackfillError(
            f"{u}: no OPTSTK rows in the scrip master — not an F&O stock "
            f"(or the symbol differs; check UNDERLYING_SYMBOL spelling)")
    if not eq_id:
        raise StockBackfillError(f"{u}: NSE EQ row not found in scrip master")
    return {"underlying": u, "eq_security_id": eq_id,
            "underlying_security_id": int(und_id), "lot_size": lot,
            "optstk_rows": opt_rows}


# ── SPOT (dhan_spot_backfill mechanics, parameterized for a stock) ──────────

def _fetch_intraday(client_id: str, token: str, *, security_id: str,
                    interval: str, dfrom: str, dto: str) -> dict:
    payload = {"securityId": str(security_id), "exchangeSegment": "NSE_EQ",
               "instrument": "EQUITY", "interval": interval, "oi": False,
               "fromDate": dfrom, "toDate": dto}
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "access-token": token, "client-id": client_id}
    last_err = "unknown"
    for attempt in range(3):
        try:
            r = requests.post(_INTRADAY_URL, json=payload, headers=headers,
                              timeout=90)
            if r.status_code == 200:
                return r.json() or {}
            body = (r.text or "")[:200]
            for code, hint in _ERROR_HINTS.items():
                if code in body:
                    raise StockBackfillError(f"Dhan {code}: {hint}")
            last_err = f"HTTP {r.status_code}: {body}"
            if r.status_code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(3.0 * (attempt + 1))
                continue
            raise StockBackfillError(last_err)
        except StockBackfillError:
            raise
        except Exception as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(3.0 * (attempt + 1))
                continue
    raise StockBackfillError(f"network error: {last_err}")


def detect_stock_stamp_offset(client_id: str, token: str,
                              eq_security_id: str) -> int:
    """One-day 5m probe ON THE STOCK ITSELF (equity-segment stamps are not
    assumed to match the index segment's): first stamp 09:15 IST → START-
    anchored (0); 09:20 → CLOSE-anchored (−60s). Anything else refuses to
    guess — a silent shift corrupts every candle-close decision downstream."""
    d = date.today() - timedelta(days=1)
    for _ in range(10):
        data = _fetch_intraday(client_id, token, security_id=eq_security_id,
                               interval="5",
                               dfrom=f"{d} {_SESSION_FROM_HM}",
                               dto=f"{d} {_SESSION_TO_HM}")
        ts5 = data.get("timestamp") or []
        if ts5:
            first = datetime.fromtimestamp(int(ts5[0]), IST)
            hm = first.hour * 60 + first.minute
            if hm == 9 * 60 + 15:
                return 0
            if hm == 9 * 60 + 20:
                return -60
            raise StockBackfillError(
                f"unexpected first 5m stamp {first:%H:%M} IST — stamp "
                f"semantics changed; refusing to guess")
        d -= timedelta(days=1)
    raise StockBackfillError("no recent 5m data — securityId/token problem?")


def backfill_stock_spot(*, db_path: str, client_id: str, access_token: str,
                        underlying: str, eq_security_id: str,
                        date_from: date, date_to: date,
                        progress_cb: Optional[Callable[[dict], None]] = None,
                        cancel_cb: Optional[Callable[[], bool]] = None) -> Dict:
    offset = detect_stock_stamp_offset(client_id, access_token, eq_security_id)
    write_audit_log(f"[BACKTEST][STOCK_SPOT][{underlying}] stamp offset "
                    f"{offset}s ({'close' if offset else 'start'}-anchored)")
    spot_symbol = f"{underlying}SPOT"
    spot_token = _surrogate_token(spot_symbol)

    chunks: List[Tuple[date, date]] = []
    cs = date_from
    while cs <= date_to:
        ce = min(cs + timedelta(days=_SPOT_CHUNK_DAYS - 1), date_to)
        chunks.append((cs, ce))
        cs = ce + timedelta(days=1)

    conn = sqlite3.connect(db_path, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_candles_schema(conn)
    rows_total = dupes_total = calls = 0
    cancelled = False
    upsert = """
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
    try:
        for i, (cfrom, cto) in enumerate(chunks, start=1):
            if cancel_cb and cancel_cb():
                cancelled = True
                break
            data = _fetch_intraday(client_id, access_token,
                                   security_id=eq_security_id, interval="1",
                                   dfrom=f"{cfrom} {_SESSION_FROM_HM}",
                                   dto=f"{cto} {_SESSION_TO_HM}")
            calls += 1
            ts = data.get("timestamp") or []
            o, h, l, c = (data.get(k) or [] for k in
                          ("open", "high", "low", "close"))
            v = data.get("volume") or [0] * len(ts)
            batch: Dict[int, tuple] = {}
            for j in range(len(ts)):        # in-batch dedupe, LAST wins
                t = int(ts[j]) + offset
                batch[t] = (spot_token, t, underlying, spot_symbol, "SPOT",
                            0, "", float(o[j]), float(h[j]), float(l[j]),
                            float(c[j]), int(v[j] or 0), 0)
            dupes_total += len(ts) - len(batch)
            if batch:
                conn.executemany(upsert, list(batch.values()))
                conn.commit()
            rows_total += len(batch)
            if progress_cb:
                progress_cb({"phase": "spot", "chunk": i,
                             "total_chunks": len(chunks), "rows": rows_total})
            time.sleep(0.4)
    finally:
        conn.close()
    report = {"requests": calls, "rows_upserted": rows_total,
              "dupes_collapsed": dupes_total, "stamp_offset": offset,
              "cancelled": cancelled or None}
    write_audit_log(f"[BACKTEST][STOCK_SPOT][{underlying}] "
                    f"{date_from}→{date_to}: {rows_total} rows, {calls} calls"
                    f"{' (CANCELLED)' if cancelled else ''}")
    return report


# ── OPTIONS (rolling OPTSTK MONTH, dhan_backfill doctrine) ──────────────────

def _offsets(n: int) -> List[str]:
    out = ["ATM"]
    for i in range(1, n + 1):
        out += [f"ATM+{i}", f"ATM-{i}"]
    return out


def _opt_chunks(d0: date, d1: date):
    cur = d0
    while cur <= d1:
        end = min(cur + timedelta(days=_OPT_CHUNK_DAYS), d1 + timedelta(days=1))
        yield cur, end                      # toDate non-inclusive per Dhan
        cur = end


def _write_stock_series(cur, series: RollingSeries, *, underlying: str,
                        type_code: str, days_seen: set,
                        expiries_seen: set) -> int:
    """Split by IST day, stamp the front MONTHLY expiry, synthesize the
    monthly symbol, delete-then-insert (overlap-safe, dhan_backfill rule)."""
    written = 0
    for i in range(len(series)):
        ts = series.timestamp[i]
        if i >= len(series.strike) or i >= len(series.close):
            continue
        strike = series.strike[i]
        if not strike:
            continue
        d = _ist_day(ts)
        expiry = expected_stock_monthly_expiry_for_day(d)
        symbol = build_stock_symbol(underlying, expiry, strike, type_code)
        token = _surrogate_token(symbol)
        o = series.open[i] if i < len(series.open) else series.close[i]
        h = series.high[i] if i < len(series.high) else series.close[i]
        lo = series.low[i] if i < len(series.low) else series.close[i]
        c = series.close[i]
        vol = int(series.volume[i]) if i < len(series.volume) and series.volume[i] else 0
        oi = int(series.oi[i]) if i < len(series.oi) and series.oi[i] else 0
        cur.execute("DELETE FROM backtest_candles_1m "
                    "WHERE tradingsymbol = ? AND ts = ?", (symbol, ts))
        cur.execute(
            """INSERT INTO backtest_candles_1m
                 (instrument_token, ts, underlying, tradingsymbol,
                  instrument_type, strike, expiry, open, high, low, close,
                  volume, oi)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(instrument_token, ts) DO UPDATE SET
                  open=excluded.open, high=excluded.high, low=excluded.low,
                  close=excluded.close, volume=excluded.volume,
                  oi=excluded.oi, tradingsymbol=excluded.tradingsymbol,
                  strike=excluded.strike, expiry=excluded.expiry,
                  instrument_type=excluded.instrument_type""",
            (token, ts, underlying, symbol, type_code, float(strike),
             expiry.isoformat(), float(o), float(h), float(lo), float(c),
             vol, oi))
        written += 1
        days_seen.add(d.isoformat())
        expiries_seen.add(expiry.isoformat())
    return written


def backfill_stock_options(*, db_path: str, client: DhanDataClient,
                           underlying: str, underlying_security_id: int,
                           date_from: date, date_to: date,
                           atm_window: int = 10,
                           progress_cb: Optional[Callable[[dict], None]] = None,
                           cancel_cb: Optional[Callable[[], bool]] = None) -> Dict:
    offsets = _offsets(atm_window)
    sides = [("CALL", "CE"), ("PUT", "PE")]
    chunk_list = list(_opt_chunks(date_from, date_to))
    planned = len(chunk_list) * len(offsets) * len(sides)
    done = calls = rows = 0
    errors: List[str] = []
    empty_windows = 0        # pre-F&O-listing months return empty — expected
    days_seen: set = set()
    expiries_seen: set = set()

    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    _ensure_candles_schema(conn)
    cur = conn.cursor()
    for (cfrom, cto) in chunk_list:
        for off in offsets:
            for (dhan_side, type_code) in sides:
                if cancel_cb and cancel_cb():
                    conn.commit(); conn.close()
                    return {"cancelled": True, "requests": calls,
                            "rows_upserted": rows,
                            "days_covered": len(days_seen),
                            "expiries": sorted(expiries_seen),
                            "errors": errors}
                done += 1
                if progress_cb and (done % 10 == 0 or done == planned):
                    progress_cb({"phase": "options", "done": done,
                                 "planned": planned,
                                 "chunk": f"{cfrom}..{cto}", "rows": rows})
                try:
                    series = client.fetch_rolling_option(
                        expiry_flag="MONTH", expiry_code=1, strike=off,
                        option_type=dhan_side,
                        from_date=cfrom.isoformat(), to_date=cto.isoformat(),
                        security_id=underlying_security_id,
                        instrument="OPTSTK")          # ── OPTSTK_BACKFILL ──
                    calls += 1
                except Exception as e:
                    errors.append(f"{cfrom}..{cto} {off} {type_code}: {e}")
                    continue
                if not series:
                    empty_windows += 1
                    continue
                rows += _write_stock_series(
                    cur, series, underlying=underlying, type_code=type_code,
                    days_seen=days_seen, expiries_seen=expiries_seen)
        conn.commit()
    conn.commit(); conn.close()
    report = {"requests": calls, "rows_upserted": rows,
              "days_covered": len(days_seen),
              "expiries": sorted(expiries_seen),
              "empty_windows": empty_windows, "errors": errors}
    write_audit_log(
        f"[BACKTEST][STOCK_OPT][{underlying}] done: {calls} calls, "
        f"{rows} rows, {len(days_seen)} days, {len(expiries_seen)} expiries, "
        f"{empty_windows} empty windows, {len(errors)} errors")
    return report


def coverage_report(db_path: str, underlying: str) -> Dict:
    """Per-year corpus summary for one underlying: spot days/candles + option
    days/rows/contracts, plus the 10 thinnest option days (liquidity gate
    material — stock options are sparse and this is where it shows first)."""
    conn = sqlite3.connect(db_path)
    _ensure_candles_schema(conn)
    cur = conn.cursor()
    out: Dict = {"underlying": underlying, "years": {}}
    for y, sd, sn in cur.execute("""
        SELECT strftime('%Y', ts,'unixepoch','+5 hours','+30 minutes') y,
               COUNT(DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes')),
               COUNT(*)
        FROM backtest_candles_1m
        WHERE underlying=? AND instrument_type='SPOT'
        GROUP BY y ORDER BY y""", (underlying,)):
        out["years"].setdefault(y, {})
        out["years"][y].update({"spot_days": sd, "spot_candles": sn})
    for y, od, orows, ncon in cur.execute("""
        SELECT strftime('%Y', ts,'unixepoch','+5 hours','+30 minutes') y,
               COUNT(DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes')),
               COUNT(*), COUNT(DISTINCT tradingsymbol)
        FROM backtest_candles_1m
        WHERE underlying=? AND instrument_type IN ('CE','PE')
        GROUP BY y ORDER BY y""", (underlying,)):
        out["years"].setdefault(y, {})
        out["years"][y].update({"opt_days": od, "opt_rows": orows,
                                "opt_contracts": ncon})
    out["thin_option_days"] = [
        {"date": d, "rows": n} for d, n in cur.execute("""
            SELECT date(ts,'unixepoch','+5 hours','+30 minutes') d, COUNT(*) n
            FROM backtest_candles_1m
            WHERE underlying=? AND instrument_type IN ('CE','PE')
            GROUP BY d ORDER BY n ASC LIMIT 10""", (underlying,))]
    conn.close()
    return out


# ── CLI ─────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse
    import json
    import os
    from pathlib import Path

    ap = argparse.ArgumentParser(
        description="Backfill one F&O stock's spot 1m + expired monthly "
                    "option 1m candles from Dhan into backtest.db")
    ap.add_argument("--underlying", default="DIXON")
    ap.add_argument("--db", default=str(Path.home()
                    / ".scalp-app" / "backtest" / "backtest.db"))
    ap.add_argument("--from", dest="date_from",
                    default=(date.today() - timedelta(days=5 * 365)).isoformat())
    ap.add_argument("--to", dest="date_to",
                    default=(date.today() - timedelta(days=1)).isoformat())
    ap.add_argument("--atm-window", type=int, default=10)
    ap.add_argument("--client-id", default=os.environ.get("DHAN_CLIENT_ID"))
    ap.add_argument("--access-token",
                    default=os.environ.get("DHAN_ACCESS_TOKEN"))
    ap.add_argument("--spot-only", action="store_true")
    ap.add_argument("--options-only", action="store_true")
    a = ap.parse_args()
    if not a.client_id or not a.access_token:
        ap.error("--client-id/--access-token (or DHAN_CLIENT_ID/"
                 "DHAN_ACCESS_TOKEN env) required")
    d0, d1 = date.fromisoformat(a.date_from), date.fromisoformat(a.date_to)
    u = a.underlying.upper().strip()

    client = DhanDataClient(a.client_id, a.access_token, throttle_s=0.25)
    tok = client.check_token()
    if not tok.get("ok"):
        raise SystemExit(f"Dhan token pre-flight failed: {tok.get('reason')}")
    hl = tok.get("hours_left")
    print(f"token OK ({hl:.1f}h left)" if hl else "token OK")

    print(f"resolving {u} in the scrip master…")
    ids = resolve_stock_ids(u)
    print(f"  EQ securityId {ids['eq_security_id']} · OPTSTK underlying id "
          f"{ids['underlying_security_id']} · current lot {ids['lot_size']} "
          f"· {ids['optstk_rows']} live option rows")

    if not a.options_only:
        print(f"[spot] {u} {d0} → {d1}")
        r = backfill_stock_spot(
            db_path=a.db, client_id=a.client_id, access_token=a.access_token,
            underlying=u, eq_security_id=ids["eq_security_id"],
            date_from=d0, date_to=d1,
            progress_cb=lambda p: print(f"  spot chunk {p['chunk']}/"
                                        f"{p['total_chunks']} rows {p['rows']}",
                                        end="\r"))
        print(f"\n  spot: {json.dumps({k: v for k, v in r.items() if v is not None})}")
    if not a.spot_only:
        print(f"[options] {u} MONTH ATM±{a.atm_window} {d0} → {d1}")
        r = backfill_stock_options(
            db_path=a.db, client=client, underlying=u,
            underlying_security_id=ids["underlying_security_id"],
            date_from=d0, date_to=d1, atm_window=a.atm_window,
            progress_cb=lambda p: print(f"  opt {p['done']}/{p['planned']} "
                                        f"rows {p['rows']}", end="\r"))
        print(f"\n  options: requests {r['requests']} rows {r['rows_upserted']} "
              f"days {r['days_covered']} expiries {len(r['expiries'])} "
              f"empty {r['empty_windows']} errors {len(r['errors'])}")
        for e in r["errors"][:8]:
            print(f"    ERR {e}")

    print("coverage:")
    print(json.dumps(coverage_report(a.db, u), indent=2))


if __name__ == "__main__":
    _cli()