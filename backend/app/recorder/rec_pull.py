# backend/app/recorder/rec_pull.py
#
# ── DATA_RECORDER_20261029 ── one recorder pass for one session day.
#
# BAND (decided 2026-10-29: NIFTY only; ATM±10; current week, next week,
# monthly) — plus (PARITY_CAPTURE_20261029) every contract any strategy held
# that day, wherever it is: every 50-pt strike from the day's spot LOW − 10 steps to its HIGH
# + 10 steps, CE + PE, for the three expiries — every strike that was ATM±10
# at any minute, with no intraday re-centring. The band only ever grows
# during a day (a widened range adds strikes, nothing is dropped).
#
# PULL: exchange 1m candles from Kite historical (the DATA session when
# present — kite_backfill._resolve_kite), ~2.5 req/s with 429 back-off, only
# CLOSED minutes inside 09:15–15:29. Each instrument resumes from its last
# stored minute, so the 30-minute intraday passes are incremental and a
# strike added late is fetched from 09:15.
#
# WRITES: ONLY the recorder DB (main.backtest_candles_1m of rec_db.connect()).
# Kite is the source of truth inside the recorder: rows are upserted.
#
# EXPIRED CONTRACTS: Kite drops a contract's token at expiry, so a past day's
# contract that has expired cannot be fetched — logged once as `expired` and
# counted in day_status.lost_n; never invented.

from __future__ import annotations

import json
import math
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.recorder import rec_db as RD

STRIKE_STEP = 50
BAND_STEPS = 10
OPEN_MIN = 9 * 60 + 15
CLOSE_MIN = 15 * 60 + 30            # last bar starts 15:29
EXPECTED_BARS = CLOSE_MIN - OPEN_MIN  # 375
CLOSE_GRACE_S = 90                  # a minute is written once it closed this long ago
FINAL_RETRIES = 3
_MONTHLY_RE = re.compile(r"^NIFTY\d{2}[A-Z]{3}\d")


# ─────────────────────────────────────────────────────────────────────
# time (patched by the suite)
# ─────────────────────────────────────────────────────────────────────
def now_ts() -> int:
    return int(time.time())


def ist_date(ts: int) -> date:
    return datetime.fromtimestamp(int(ts), RD.IST).date()


def day_start(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=RD.IST).timestamp())


def mod_of(ts: int) -> int:
    return datetime.fromtimestamp(int(ts), RD.IST).hour * 60 + datetime.fromtimestamp(int(ts), RD.IST).minute


def _ist_str(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), RD.IST).strftime("%Y-%m-%d %H:%M:%S")


# ─────────────────────────────────────────────────────────────────────
# instruments
# ─────────────────────────────────────────────────────────────────────
def _snapshot_dir() -> Path:
    try:
        from app.fetcher.instruments_snapshot import SNAPSHOT_DIR
        return Path(SNAPSHOT_DIR)
    except Exception:
        return RD.app_home() / "state" / "instruments_history"


def _rows_from_df(df) -> List[dict]:
    out = []
    if df is None or getattr(df, "empty", True):
        return out
    for r in df.to_dict("records"):
        try:
            if str(r.get("name") or "").upper() != RD.UNDERLYING:
                continue
            if str(r.get("instrument_type") or "") not in ("CE", "PE"):
                continue
            seg = str(r.get("segment") or "NFO-OPT")
            if not seg.startswith("NFO"):
                continue
            exp = r.get("expiry")
            if hasattr(exp, "isoformat"):
                exp = exp.isoformat()[:10]
            exp = str(exp or "")[:10]
            if len(exp) != 10:
                continue
            out.append({"token": int(r["instrument_token"]), "tradingsymbol": str(r["tradingsymbol"]),
                        "instrument_type": str(r["instrument_type"]), "strike": float(r.get("strike") or 0.0),
                        "expiry": exp})
        except Exception:
            continue
    return out


def load_current_instruments() -> List[dict]:
    from app.fetcher.zerodha_instruments import load_instruments_df
    return _rows_from_df(load_instruments_df())


def load_snapshot_instruments(d: date) -> List[dict]:
    p = _snapshot_dir() / f"NFO_{d.isoformat()}.csv"
    if not p.exists():
        return []
    import pandas as pd
    return _rows_from_df(pd.read_csv(p))


def instruments_for_day(d: date) -> Tuple[List[dict], List[str]]:
    """NIFTY options listed on day d (expiry >= d): that day's dated snapshot
    ∪ the current dump. Notes say what was missing."""
    notes: List[str] = []
    by_tok: Dict[int, dict] = {}
    snap = []
    try:
        snap = load_snapshot_instruments(d)
    except Exception as e:
        notes.append(f"snapshot unreadable: {type(e).__name__}")
    try:
        cur = load_current_instruments()
    except Exception as e:
        cur = []
        notes.append(f"instrument dump unavailable: {type(e).__name__}")
    for r in snap + cur:
        if r["expiry"] >= d.isoformat():
            by_tok.setdefault(r["token"], r)
    if not snap and d < ist_date(now_ts()):
        notes.append("no dated instrument snapshot for this day — expired contracts unknown")
    return list(by_tok.values()), notes


def expiry_kinds(rows: List[dict], d: date) -> Dict[str, str]:
    """{expiry: kind} for current week, next week and the monthly expiry."""
    exps = sorted({r["expiry"] for r in rows if r["expiry"] >= d.isoformat()})
    out: Dict[str, str] = {}
    if exps:
        out[exps[0]] = "week"
    if len(exps) > 1:
        out[exps[1]] = "next"
    monthly = sorted({r["expiry"] for r in rows if _MONTHLY_RE.match(r["tradingsymbol"])
                      and r["expiry"] >= d.isoformat()})
    if monthly and monthly[0] not in out:
        out[monthly[0]] = "month"
    return out


def app_contracts(d: date) -> set:
    """── PARITY_CAPTURE_20261029 ── every NIFTY option any strategy held at
    any time on day d, paper or live, open or closed — read through the
    dashboard's leg reader. These are always recorded, even when far outside
    the ±10-step band (hedge wings, a positional short the market moved away
    from): the parity ledger must be able to price every leg the app traded."""
    from app.api import fleet_today_routes as F
    ds = day_start(d)
    de = ds + 86400
    out = set()
    for sid in F.FLEET_IDS:
        try:
            legs = F._legs_for(sid, [])
        except Exception:
            continue
        for l in legs:
            sym = str(l.get("symbol") or "")
            if not sym.startswith(RD.UNDERLYING) or sym[len(RD.UNDERLYING):len(RD.UNDERLYING) + 1].isalpha():
                continue                                   # NIFTY only (not NIFTYNXT50 etc.)
            et, xt = int(l.get("entry_ts") or 0), l.get("exit_ts")
            if et and et < de and (xt is None or int(xt) >= ds):
                out.add(sym)
    return out


def strike_range(spot_lo: float, spot_hi: float) -> Tuple[float, float]:
    lo = math.floor(spot_lo / STRIKE_STEP) * STRIKE_STEP - BAND_STEPS * STRIKE_STEP
    hi = math.ceil(spot_hi / STRIKE_STEP) * STRIKE_STEP + BAND_STEPS * STRIKE_STEP
    return float(lo), float(hi)


def build_band(rows: List[dict], d: date, spot_lo: float, spot_hi: float) -> List[dict]:
    kinds = expiry_kinds(rows, d)
    lo, hi = strike_range(spot_lo, spot_hi)
    band = [{"token": RD.SPOT_TOKEN, "tradingsymbol": RD.SPOT_SYMBOL, "instrument_type": "SPOT",
             "strike": 0.0, "expiry": "", "kind": "spot"}]
    for r in rows:
        k = kinds.get(r["expiry"])
        if k and lo <= r["strike"] <= hi:
            band.append(dict(r, kind=k))
    return band


# ─────────────────────────────────────────────────────────────────────
# Kite
# ─────────────────────────────────────────────────────────────────────
def resolve_kite():
    from app.backtest.backfill.kite_backfill import _resolve_kite
    return _resolve_kite()


def fetch(kite, token: int, lo_ts: int, hi_ts: int) -> Tuple[list, Optional[str]]:
    from app.backtest.backfill import kite_backfill as KB
    attempt = 0
    while True:
        try:
            rows = kite.historical_data(instrument_token=int(token), from_date=_ist_str(lo_ts),
                                        to_date=_ist_str(hi_ts + 59), interval=KB.INTERVAL, oi=True) or []
            time.sleep(KB.THROTTLE_S)
            return rows, None
        except Exception as e:
            msg = str(e)
            attempt += 1
            if attempt > 3:
                return [], f"{type(e).__name__}: {msg[:160]}"
            rate = ("Too many requests" in msg) or ("429" in msg)
            time.sleep((KB.BACKOFF_BASE_S ** attempt) if rate else KB.THROTTLE_S * 2)


def _epoch(r) -> Optional[int]:
    from app.backtest.backfill.kite_backfill import _to_epoch_ist
    try:
        v = r["date"]
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        return _to_epoch_ist(v)
    except Exception:
        return None


_UPS = (f"INSERT INTO main.backtest_candles_1m ({RD.COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(instrument_token, ts) DO UPDATE SET open=excluded.open, high=excluded.high, "
        "low=excluded.low, close=excluded.close, volume=excluded.volume, oi=excluded.oi")


def write_rows(conn, inst: dict, rows: list, d: date, upto_ts: int) -> int:
    ds = day_start(d)
    lo, hi = ds + OPEN_MIN * 60, min(ds + (CLOSE_MIN - 1) * 60, upto_ts)
    recs = []
    for r in rows:
        ts = _epoch(r)
        if ts is None or ts < lo or ts > hi or ts % 60:
            continue
        try:
            recs.append((int(inst["token"]), ts, RD.UNDERLYING, inst["tradingsymbol"], inst["instrument_type"],
                         float(inst.get("strike") or 0.0), inst.get("expiry") or "",
                         float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                         int(r.get("volume") or 0), int(r.get("oi") or 0)))
        except Exception:
            continue
    if recs:
        conn.executemany(_UPS, recs)
        conn.commit()
    return len(recs)


# ─────────────────────────────────────────────────────────────────────
# one pass
# ─────────────────────────────────────────────────────────────────────
def _log(conn, d: date, inst: dict, lo: Optional[int], hi: Optional[int], rows: int, status: str,
         error: Optional[str] = None) -> None:
    conn.execute("INSERT INTO pull_log (day, instrument_token, tradingsymbol, pulled_at, from_ts, to_ts, rows, "
                 "status, error) VALUES (?,?,?,?,?,?,?,?,?)",
                 (d.isoformat(), int(inst["token"]), inst["tradingsymbol"], now_ts(), lo, hi, rows, status, error))
    conn.commit()


def _last_ts(conn, token: int, d: date) -> Optional[int]:
    ds = day_start(d)
    r = conn.execute("SELECT MAX(ts) FROM main.backtest_candles_1m WHERE instrument_token=? AND ts>=? AND ts<?",
                     (int(token), ds, ds + 86400)).fetchone()
    return int(r[0]) if r and r[0] is not None else None


def _spot_range(conn, d: date) -> Optional[Tuple[float, float, int]]:
    ds = day_start(d)
    r = conn.execute("SELECT MIN(low), MAX(high), COUNT(*) FROM main.backtest_candles_1m WHERE instrument_token=? "
                     "AND ts>=? AND ts<?", (RD.SPOT_TOKEN, ds, ds + 86400)).fetchone()
    if not r or not r[2]:
        return None
    return float(r[0]), float(r[1]), int(r[2])


def _status(conn, d: date) -> dict:
    conn.row_factory = None
    r = conn.execute("SELECT day, band_n, pulled_n, lost_n, error_n, bars, spot_bars, completeness, quality, "
                     "last_pull_at, final_at, attempts, notes FROM day_status WHERE day=?", (d.isoformat(),)).fetchone()
    keys = ("day", "band_n", "pulled_n", "lost_n", "error_n", "bars", "spot_bars", "completeness", "quality",
            "last_pull_at", "final_at", "attempts", "notes")
    return dict(zip(keys, r)) if r else {}


def day_status(conn, d: date) -> dict:
    return _status(conn, d)


def pull_day(conn, kite, d: date, *, final: bool, reason: str, now: Optional[int] = None) -> dict:
    """Fetch every closed minute of day d not yet stored, for the day's band.
    `final` = the day is over (or a catch-up): quality is graded. Never raises
    on a Kite error — errors are counted and logged."""
    now = now_ts() if now is None else int(now)
    today = ist_date(now)
    ds = day_start(d)
    first = ds + OPEN_MIN * 60
    end = min(ds + (CLOSE_MIN - 1) * 60, now - CLOSE_GRACE_S - 60)   # last CLOSED minute start
    end -= end % 60
    notes: List[str] = []
    errors = 0
    if end < first:
        return {"day": d.isoformat(), "skipped": "no closed minute yet"}

    # 1) spot first — its range sets the band
    spot = {"token": RD.SPOT_TOKEN, "tradingsymbol": RD.SPOT_SYMBOL, "instrument_type": "SPOT", "strike": 0.0,
            "expiry": "", "kind": "spot"}
    last = _last_ts(conn, RD.SPOT_TOKEN, d)
    lo = max(first, (last + 60) if last else first)
    if lo <= end:
        rows, err = fetch(kite, RD.SPOT_TOKEN, lo, end)
        n = write_rows(conn, spot, rows, d, end) if rows else 0
        _log(conn, d, spot, lo, end, n, "error" if err and not rows else ("ok" if n else "empty"), err)
        if err and not rows:
            errors += 1
    rng = _spot_range(conn, d)
    if not rng:
        _save_status(conn, d, final=final, notes=notes + ["no NIFTY spot bars from Kite for this day"],
                     errors=errors + 1, now=now)
        return {"day": d.isoformat(), "error": "no spot bars"}

    # 2) band (grows only)
    insts, inotes = instruments_for_day(d)
    notes += inotes
    band = build_band(insts, d, rng[0], rng[1])
    try:                                                   # ── PARITY_CAPTURE_20261029 ──
        held = app_contracts(d)
    except Exception as e:
        held = set()
        notes.append(f"held contracts unreadable: {type(e).__name__}")
    by_sym = {r["tradingsymbol"]: r for r in insts}
    in_band = {b["token"] for b in band}
    for sym in sorted(held):
        r = by_sym.get(sym)
        if r is None:
            notes.append(f"held contract missing from the instrument list: {sym}")
        elif r["token"] not in in_band:
            band.append(dict(r, kind="held", reason="held"))
    have = {int(r[0]) for r in conn.execute("SELECT instrument_token FROM band WHERE day=?", (d.isoformat(),))}
    for b in band:
        if b["token"] not in have:
            conn.execute("INSERT OR IGNORE INTO band (day, instrument_token, tradingsymbol, instrument_type, strike, "
                         "expiry, kind, reason, added_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (d.isoformat(), b["token"], b["tradingsymbol"], b["instrument_type"], b["strike"],
                          b["expiry"], b["kind"], b.get("reason") or (reason if not have else "widened"), now))
    conn.commit()
    stored = [dict(zip(("token", "tradingsymbol", "instrument_type", "strike", "expiry", "kind"), r))
              for r in conn.execute("SELECT instrument_token, tradingsymbol, instrument_type, strike, expiry, kind "
                                    "FROM band WHERE day=? AND kind<>'spot' ORDER BY expiry, strike, instrument_type",
                                    (d.isoformat(),))]

    # 3) options
    logged_expired = {int(r[0]) for r in conn.execute(
        "SELECT DISTINCT instrument_token FROM pull_log WHERE day=? AND status='expired'", (d.isoformat(),))}
    for inst in stored:
        if inst["expiry"] < today.isoformat():
            if inst["token"] not in logged_expired and _last_ts(conn, inst["token"], d) is None:
                _log(conn, d, inst, None, None, 0, "expired", "contract expired before it was recorded")
            continue
        last = _last_ts(conn, inst["token"], d)
        lo = max(first, (last + 60) if last else first)
        if lo > end:
            continue
        rows, err = fetch(kite, inst["token"], lo, end)
        n = write_rows(conn, inst, rows, d, end) if rows else 0
        _log(conn, d, inst, lo, end, n, "error" if err and not rows else ("ok" if n else "empty"), err)
        if err and not rows:
            errors += 1
    st = _save_status(conn, d, final=final, notes=notes, errors=errors, now=now)
    return st


def _save_status(conn, d: date, *, final: bool, notes: List[str], errors: int, now: int) -> dict:
    ds = day_start(d)
    key = d.isoformat()
    band_n = conn.execute("SELECT COUNT(*) FROM band WHERE day=?", (key,)).fetchone()[0]
    per = dict(conn.execute("SELECT instrument_token, COUNT(*) FROM main.backtest_candles_1m WHERE ts>=? AND ts<? "
                            "GROUP BY instrument_token", (ds, ds + 86400)).fetchall())
    band_toks = [int(r[0]) for r in conn.execute("SELECT instrument_token FROM band WHERE day=?", (key,))]
    pulled = sum(1 for t in band_toks if per.get(t))
    lost = conn.execute("SELECT COUNT(DISTINCT instrument_token) FROM pull_log WHERE day=? AND status='expired'",
                        (key,)).fetchone()[0]
    bars = sum(min(per.get(t, 0), EXPECTED_BARS) for t in band_toks)
    spot_bars = int(per.get(RD.SPOT_TOKEN, 0))
    completeness = round(bars / (band_n * EXPECTED_BARS), 4) if (final and band_n) else None
    prev = _status(conn, d)
    attempts = int(prev.get("attempts") or 0) + (1 if final else 0)
    if not final:
        quality = "recording"
    elif pulled == 0:
        quality = "lost"
    elif errors == 0 and lost == 0 and spot_bars >= EXPECTED_BARS:
        quality = "clean"
    else:
        quality = "partial"
    done = final and (errors == 0 or attempts >= FINAL_RETRIES)
    conn.execute("INSERT INTO day_status (day, band_n, pulled_n, lost_n, error_n, bars, spot_bars, completeness, "
                 "quality, last_pull_at, final_at, attempts, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                 "ON CONFLICT(day) DO UPDATE SET band_n=excluded.band_n, pulled_n=excluded.pulled_n, "
                 "lost_n=excluded.lost_n, error_n=excluded.error_n, bars=excluded.bars, spot_bars=excluded.spot_bars, "
                 "completeness=excluded.completeness, quality=excluded.quality, last_pull_at=excluded.last_pull_at, "
                 "final_at=excluded.final_at, attempts=excluded.attempts, notes=excluded.notes",
                 (key, band_n, pulled, lost, errors, bars, spot_bars, completeness, quality, now,
                  now if done else None, attempts, json.dumps(sorted(set(notes)))[:2000] if notes else None))
    conn.commit()
    return _status(conn, d)
