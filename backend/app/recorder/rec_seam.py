# backend/app/recorder/rec_seam.py
#
# ── SEAM_CHECK_20261031 ── does Kite data line up with the Dhan corpus?
# The gate before normal backtests may read recorded (Kite) days.
#
#   SPOT (exact): for sample sessions inside the Dhan era, NIFTY 1m candles are
#   pulled from Kite historical (index history does not expire) and compared
#   minute by minute with backtest.db's spot: minutes on both sides, minutes
#   missing on either side, the time shift (−3…+3 min) that best aligns the
#   closes (0 expected — the corpus was re-stamped once, SPOT_STAMP), and the
#   mean / max |difference| of open, high, low, close at that shift.
#   OPTIONS (shape only): Dhan and Kite never hold the same contract on the
#   same day (Dhan ends 31 Aug; Kite drops a contract at expiry), so the
#   chain is compared statistically: contracts per day and bars per contract-
#   day, Dhan's last month vs the recorder's clean days.
#   PASS: every sampled session has ≥ 370 common minutes, best shift 0, and
#   mean |Δclose| ≤ ₹0.50 (index points). The verdict is stored in recorder
#   meta "seam_check" and gates the backtest data-source toggle.

from __future__ import annotations

import json
import time
from datetime import date, timedelta, timezone
from typing import List, Optional

from app.recorder import rec_db as RD
from app.recorder import rec_pull as RP

FENCE = "SEAM_CHECK_20261031"
IST = timezone(timedelta(hours=5, minutes=30))
MIN_COMMON = 370
MAX_MEAN_CLOSE = 0.50


def _sessions_with_dhan_spot(main, n: int, before: date) -> List[date]:
    import sqlite3
    c = sqlite3.connect(f"file:{main}?mode=ro", uri=True, timeout=30)
    try:
        hi = RP.day_start(before)
        rows = c.execute("SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') FROM backtest_candles_1m "
                         "WHERE underlying='NIFTY' AND instrument_type='SPOT' AND ts < ? AND ts >= ? ORDER BY 1 DESC LIMIT ?",
                         (hi, hi - 60 * 86400, int(n))).fetchall()
    finally:
        c.close()
    return [date.fromisoformat(r[0]) for r in rows]


def _corpus_spot(main, d: date) -> dict:
    import sqlite3
    c = sqlite3.connect(f"file:{main}?mode=ro", uri=True, timeout=30)
    try:
        ds = RP.day_start(d)
        return {int(r[0]): r[1:] for r in c.execute(
            "SELECT ts, open, high, low, close FROM backtest_candles_1m WHERE underlying='NIFTY' AND instrument_type='SPOT' "
            "AND ts>=? AND ts<?", (ds, ds + 86400))}
    finally:
        c.close()


def compare_day(kite_rows: list, corpus: dict, d: date) -> dict:
    ds = RP.day_start(d)
    lo, hi = ds + RP.OPEN_MIN * 60, ds + (RP.CLOSE_MIN - 1) * 60
    k = {}
    for r in kite_rows:
        ts = RP._epoch(r)
        if ts is not None and lo <= ts <= hi:
            k[ts] = (float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
    c = {ts: tuple(float(x) for x in v) for ts, v in corpus.items() if lo <= ts <= hi}
    best = None
    for shift in range(-3, 4):
        common = [ts for ts in c if ts + shift * 60 in k]
        if not common:
            continue
        mean_close = sum(abs(c[ts][3] - k[ts + shift * 60][3]) for ts in common) / len(common)
        if best is None or (len(common), -mean_close) > (best[1], -best[2]):
            best = (shift, len(common), mean_close)
    if best is None:
        return {"day": d.isoformat(), "kite_bars": len(k), "corpus_bars": len(c), "common": 0, "shift_min": None, "ok": False}
    shift = best[0]
    common = [ts for ts in c if ts + shift * 60 in k]
    diffs = [[abs(c[ts][i] - k[ts + shift * 60][i]) for ts in common] for i in range(4)]
    out = {"day": d.isoformat(), "kite_bars": len(k), "corpus_bars": len(c), "common": len(common), "shift_min": shift,
           "only_kite": len([ts for ts in k if ts - shift * 60 not in c]), "only_corpus": len(c) - len(common)}
    for i, nm in enumerate(("open", "high", "low", "close")):
        out[f"mean_abs_{nm}"] = round(sum(diffs[i]) / len(diffs[i]), 3)
        out[f"max_abs_{nm}"] = round(max(diffs[i]), 2)
    out["ok"] = (len(common) >= MIN_COMMON and shift == 0 and out["mean_abs_close"] <= MAX_MEAN_CLOSE)
    return out


def chain_shape(main) -> dict:
    """Contracts per day and bars per contract-day: Dhan's last 20 sessions vs
    the recorder's clean days."""
    import sqlite3

    def shape(conn, where, args):
        rows = conn.execute("SELECT date(ts,'unixepoch','+5 hours','+30 minutes') AS d, tradingsymbol, COUNT(*) "
                            f"FROM backtest_candles_1m WHERE underlying='NIFTY' AND instrument_type IN ('CE','PE') AND {where} "
                            "GROUP BY d, tradingsymbol", args).fetchall()
        if not rows:
            return None
        days = {r[0] for r in rows}
        return {"days": len(days), "contracts_per_day": round(len(rows) / len(days), 1),
                "bars_per_contract_day": round(sum(r[2] for r in rows) / len(rows), 1)}
    out = {}
    c = sqlite3.connect(f"file:{main}?mode=ro", uri=True, timeout=60)
    try:
        last = c.execute("SELECT MAX(ts) FROM backtest_candles_1m WHERE underlying='NIFTY' AND instrument_type='CE' "
                         "AND instrument_token >= ?", (RD.DHAN_TOKEN_FLOOR,)).fetchone()[0]
        if last:
            out["dhan"] = shape(c, "instrument_token >= ? AND ts >= ?", (RD.DHAN_TOKEN_FLOOR, int(last) - 30 * 86400))
    finally:
        c.close()
    if RD.db_path().exists():
        r = RD.connect()
        try:
            clean = [x[0] for x in r.execute("SELECT day FROM day_status WHERE quality='clean'")]
            if clean:
                lo = min(RP.day_start(date.fromisoformat(x)) for x in clean)
                out["recorded"] = shape(r, "ts >= ?", (lo,))
        finally:
            r.close()
    return out


def run(n_days: int = 5, kite=None, main=None, today: Optional[date] = None) -> dict:
    main = main or RD.main_db_path()
    today = today or RP.ist_date(RP.now_ts())
    k = kite if kite is not None else RP.resolve_kite()
    if k is None:
        return {"ok": False, "error": "no Kite session (log in to Zerodha)"}
    rec_conn = RD.connect()
    try:
        cut = date.fromisoformat(RD.get_meta(rec_conn, "cutover") or RD.CUTOVER.isoformat())
    finally:
        rec_conn.close()
    days = _sessions_with_dhan_spot(main, n_days, min(cut, today))
    if not days:
        return {"ok": False, "error": "no Dhan-era spot sessions to compare"}
    per_day = []
    for d in sorted(days):
        ds = RP.day_start(d)
        rows, err = RP.fetch(k, RD.SPOT_TOKEN, ds + RP.OPEN_MIN * 60, ds + (RP.CLOSE_MIN - 1) * 60)
        if err and not rows:
            per_day.append({"day": d.isoformat(), "ok": False, "error": err})
            continue
        per_day.append(compare_day(rows, _corpus_spot(main, d), d))
    shape = {}
    try:
        shape = chain_shape(main)
    except Exception as e:
        shape = {"error": f"{type(e).__name__}: {e}"}
    res = {"ok": all(x.get("ok") for x in per_day), "checked_at": int(time.time()), "days": per_day, "chain": shape,
           "rule": f"≥{MIN_COMMON} common minutes, shift 0, mean |Δclose| ≤ {MAX_MEAN_CLOSE}"}
    c = RD.connect()
    try:
        RD.set_meta(c, "seam_check", json.dumps(res))
    finally:
        c.close()
    return res


def last() -> Optional[dict]:
    if not RD.db_path().exists():
        return None
    c = RD.connect()
    try:
        v = RD.get_meta(c, "seam_check")
    finally:
        c.close()
    return json.loads(v) if v else None
