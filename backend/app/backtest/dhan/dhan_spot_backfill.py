# backend/app/backtest/dhan/dhan_spot_backfill.py
#
# ── SPOT_BACKFILL ── NIFTY 50 INDEX 1m candles → backtest corpus. Powers
# spot-signal strategies (PST_V1: pivots/SMA/SuperTrend computed on spot,
# spot-point StopGains) and the real CandleSource.spot_at.
#
# Deliberately a SIBLING of dhan_backfill, not an extension of DhanDataClient:
# that client's charter is "rolling-option endpoint only, never anything
# else" — this module owns its one HTTP call (same `requests` dep) and, like
# the client, has ZERO order/trade capability. Dhan remains data-only;
# live trading remains 100% Zerodha.
#
# Facts encoded from the 2026-07-06 probe + DhanHQ v2 docs:
#   * /v2/charts/intraday serves index 1m for the FULL corpus span (docs say
#     5y; 2021-02 worked empirically). 90-day/request cap → 14-day chunks.
#   * Stamp semantics are NOT assumed: a one-day 5m probe decides START- vs
#     CLOSE-anchored stamps (first 5m stamp 09:15 vs 09:20) and candles are
#     normalized to the corpus convention ts = BAR START. A silent 1-minute
#     shift would corrupt every pivot/cross downstream.
#   * The probe saw duplicate-stamp days (~766 candles) → in-batch dedupe,
#     LAST wins, collapse count reported.
#
# Corpus row identity: instrument_token=256265 (Kite's real NIFTY 50 index
# token — collision-free, future Kite fills merge), tradingsymbol='NIFTYSPOT',
# instrument_type='SPOT', strike=0, expiry=''. Invisible to all option paths
# (they filter instrument_type IN ('CE','PE')).

from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

import requests

from app.event_bus.audit_logger import write_audit_log

IST = timezone(timedelta(hours=5, minutes=30))
_URL = "https://api.dhan.co/v2/charts/intraday"
NIFTY_INDEX_SECURITY_ID = "13"
SPOT_TOKEN = 256265
SPOT_SYMBOL = "NIFTYSPOT"
CHUNK_DAYS = 14

_ERROR_HINTS = {
    "806": "Data APIs not subscribed",
    "807": "Access token expired — regenerate on web.dhan.co",
    "808": "Authentication failed — client id or access token invalid",
    "809": "Access token invalid",
    "813": "Invalid securityId",
    "DH-905": "Input exception — bad/missing parameter",
}

_UPSERT = """
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


class DhanSpotError(Exception):
    pass


def _fetch(client_id: str, access_token: str, interval: str,
           dfrom: str, dto: str) -> dict:
    payload = {"securityId": NIFTY_INDEX_SECURITY_ID, "exchangeSegment": "IDX_I",
               "instrument": "INDEX", "interval": interval, "oi": False,
               "fromDate": dfrom, "toDate": dto}
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "access-token": access_token, "client-id": client_id}
    last_err = "unknown"
    for attempt in range(3):
        try:
            r = requests.post(_URL, json=payload, headers=headers, timeout=90)
            if r.status_code == 200:
                return r.json() or {}
            body = (r.text or "")[:200]
            for code, hint in _ERROR_HINTS.items():
                if code in body:
                    raise DhanSpotError(f"Dhan {code}: {hint}")
            last_err = f"HTTP {r.status_code}: {body}"
            if r.status_code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(3.0 * (attempt + 1))
                continue
            raise DhanSpotError(last_err)
        except DhanSpotError:
            raise
        except Exception as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(3.0 * (attempt + 1))
                continue
    raise DhanSpotError(f"network error: {last_err}")


# ── CAS_2026 BEGIN ──────────────────────────────────────────────────────────
# From 2026-08-03 the NSE equity DERIVATIVES segment closes at 15:40 (CAS
# rollout). The NIFTY index itself is disseminated through the auction window,
# so the spot corpus must be fetched to a bound PAST the new close or the last
# ~10 minutes of every session silently vanish from the corpus.
#
# WHY THIS MATTERS MORE THAN IT LOOKS: fut_backfill.py and
# bnf_options_backfill.py ALREADY fetch to 15:40. Leaving spot at 15:30 makes
# the corpus internally inconsistent (options/futures have a tail that spot
# does not) and breaks backtest↔live parity for every spot-driven strategy —
# PST and TMA derive prev-day H/L/C (their pivot inputs) from this data, while
# their LIVE warmup paths (pst_live_warmup / tma_live_warmup) already fetch to
# 15:45. Different pivots in backtest vs live = different entries.
#
# 15:45 (not 15:40) for the same reason the live warmups use it: harmless
# padding that tolerates stamp-anchoring differences at the boundary.
_SESSION_FROM_HM = "09:15:00"
_SESSION_TO_HM   = "15:45:00"
# ── CAS_2026 END ────────────────────────────────────────────────────────────


def detect_stamp_offset(client_id: str, access_token: str) -> int:
    """Empirical stamp semantics, probed at the SAME interval we store.

    ── SPOT_STAMP_FIX 20260818 ────────────────────────────────────────────
    WAS: probed with interval "5" and mapped first-stamp 09:20 → offset -60.
    That was wrong twice over:
      1. It inferred 1m semantics from a 5m series. Dhan returned 09:20 for
         the 5m probe, so the code declared "close-anchored" and subtracted
         60s from every 1m bar — but the 1m series was already START-
         anchored. Result: EVERY corpus spot bar landed one minute early
         (verified 2026-08-18: corpus ts + 60 == Kite ts on 370/376 bars),
         which silently dropped each day's real 09:15 candle in
         resample_spot() and shifted every time gate by a minute.
      2. A genuinely CLOSE-anchored 5m bar needs -300, not -60.
    NOW: probe at "1" and test the 1m stamps directly — 09:15 → START
    (offset 0), 09:16 → CLOSE (offset -60). No cross-interval inference.
    """
    d = date.today() - timedelta(days=1)
    for _ in range(10):
        data = _fetch(client_id, access_token, "1",
                      f"{d} {_SESSION_FROM_HM}", f"{d} {_SESSION_TO_HM}")
        ts1 = data.get("timestamp") or []
        if ts1:
            first = datetime.fromtimestamp(int(ts1[0]), IST)
            hm = first.hour * 60 + first.minute
            if hm == 9 * 60 + 15:
                return 0            # START-anchored: store as-is
            if hm == 9 * 60 + 16:
                return -60          # CLOSE-anchored: shift back to START
            raise DhanSpotError(
                f"unexpected first 1m stamp {first:%H:%M} IST — stamp "
                f"semantics changed; refusing to guess an offset")
        d -= timedelta(days=1)
    raise DhanSpotError("no recent 1m data — token/securityId problem?")


def _normalize_batch(data: dict, offset: int) -> Dict[int, tuple]:
    """Columnar Dhan arrays → {normalized_ts: corpus_row}. In-batch dedupe,
    LAST occurrence wins (vendor duplicate-stamp days observed)."""
    ts = data.get("timestamp") or []
    o, h, l, c = (data.get(k) or [] for k in ("open", "high", "low", "close"))
    v = data.get("volume") or [0] * len(ts)
    out: Dict[int, tuple] = {}
    for i in range(len(ts)):
        t = int(ts[i]) + offset
        out[t] = (SPOT_TOKEN, t, "NIFTY", SPOT_SYMBOL, "SPOT", 0, "",
                  float(o[i]), float(h[i]), float(l[i]), float(c[i]),
                  int(v[i] or 0), 0)
    return out


def backfill_nifty_spot(
    *,
    db_path: str,
    client_id: str,
    access_token: str,
    date_from: date,
    date_to: date,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    """Chunked, idempotent, resumable. Returns a report the UI renders:
    {requests, rows_upserted, dupes_collapsed, stamp_offset, years,
     thin_days, first_candle_ist, cancelled?}."""
    offset = detect_stamp_offset(client_id, access_token)
    write_audit_log(f"[BACKTEST][SPOT] stamp offset {offset}s "
                    f"({'close' if offset else 'start'}-anchored vendor stamps)")

    chunks: List[tuple] = []
    cs = date_from
    while cs <= date_to:
        ce = min(cs + timedelta(days=CHUNK_DAYS - 1), date_to)
        chunks.append((cs, ce))
        cs = ce + timedelta(days=1)

    conn = sqlite3.connect(db_path, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    rows_total = 0
    dupes_total = 0
    calls = 0
    cancelled = False
    try:
        for i, (cfrom, cto) in enumerate(chunks, start=1):
            if cancel_cb and cancel_cb():
                cancelled = True
                break
            data = _fetch(client_id, access_token, "1",
                          f"{cfrom} {_SESSION_FROM_HM}", f"{cto} {_SESSION_TO_HM}")
            calls += 1
            batch = _normalize_batch(data, offset)
            raw_n = len(data.get("timestamp") or [])
            dupes_total += raw_n - len(batch)
            if batch:
                conn.executemany(_UPSERT, list(batch.values()))
                conn.commit()
            rows_total += len(batch)
            if progress_cb:
                progress_cb({"chunk": i, "total_chunks": len(chunks),
                             "date_from": str(cfrom), "date_to": str(cto),
                             "rows": rows_total})
            time.sleep(0.4)

        # ── SPOT_STAMP_FIX ── self-check: the first bar of any backfilled
        # day MUST be 09:15 IST. A 09:14 first bar means the offset was
        # mis-detected again; surface it loudly instead of silently seeding
        # a shifted corpus (this is exactly how the 2026 shift went unseen).
        try:
            _chk = conn.execute(
                "SELECT MIN(ts) FROM backtest_candles_1m "
                "WHERE instrument_type='SPOT' AND ts>=? AND ts<?",
                (int(datetime(date_to.year, date_to.month, date_to.day,
                              tzinfo=IST).timestamp()),
                 int(datetime(date_to.year, date_to.month, date_to.day,
                              tzinfo=IST).timestamp()) + 86400)).fetchone()
            if _chk and _chk[0]:
                _f = datetime.fromtimestamp(int(_chk[0]), IST)
                if (_f.hour, _f.minute) != (9, 15):
                    write_audit_log(
                        f"[BACKTEST][SPOT][STAMP_WARN] first bar of "
                        f"{date_to} is {_f:%H:%M} IST, expected 09:15 — "
                        f"corpus may be stamp-shifted")
        except Exception:
            pass

        # ── verification summary (rendered by the UI, kept in the report) ──
        cur = conn.cursor()
        years = {}
        for y, days, n, lo_px, hi_px in cur.execute("""
            SELECT strftime('%Y', ts, 'unixepoch', '+5 hours', '+30 minutes') y,
                   COUNT(DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes')),
                   COUNT(*), MIN(close), MAX(close)
            FROM backtest_candles_1m WHERE instrument_type='SPOT'
            GROUP BY y ORDER BY y""").fetchall():
            years[y] = {"days": days, "candles": n,
                        "close_min": round(lo_px, 1), "close_max": round(hi_px, 1)}
        thin = [{"date": d, "candles": n} for d, n in cur.execute("""
            SELECT date(ts,'unixepoch','+5 hours','+30 minutes') d, COUNT(*) n
            FROM backtest_candles_1m WHERE instrument_type='SPOT'
            GROUP BY d HAVING n < 370 ORDER BY n ASC LIMIT 10""").fetchall()]
        first_row = cur.execute(
            "SELECT MIN(ts) FROM backtest_candles_1m WHERE instrument_type='SPOT'"
        ).fetchone()
        first_ist = (datetime.fromtimestamp(first_row[0], IST)
                     .strftime("%Y-%m-%d %H:%M IST") if first_row and first_row[0]
                     else None)
    finally:
        conn.close()

    report = {"requests": calls, "rows_upserted": rows_total,
              "dupes_collapsed": dupes_total, "stamp_offset": offset,
              "years": years, "thin_days": thin,
              "first_candle_ist": first_ist}
    if cancelled:
        report["cancelled"] = True
    write_audit_log(f"[BACKTEST][SPOT] backfill {date_from}→{date_to}: "
                    f"{rows_total} rows, {dupes_total} dupes collapsed, "
                    f"{calls} calls{' (CANCELLED)' if cancelled else ''}")
    return report