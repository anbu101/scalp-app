# ── CRYPTO_LAB BEGIN ──
# backend/app/backtest/crypto/delta_corpus.py
#
# Delta Exchange (India) BTC daily-options corpus fetcher — in-app adaptation
# of the standalone crypto_corpus.py CLI tool. SAME DB file and SAME schema
# (user_version=2), so a corpus built by either tool is readable by both.
#
#   DB: ~/.scalp-app/backtest/crypto_backtest.db   (separate from backtest.db)
#
# Differences vs the CLI tool (all in-app-safety driven):
#   * DiskGuardError exception instead of SystemExit (never kill a server
#     thread with SystemExit).
#   * progress_cb + threading.Event cancel support for UI-driven jobs.
#   * pace/span/window are per-call parameters (UI-configurable).
#   * No CLI. Public read-only endpoints only (/v2/history/candles). No auth,
#     no order paths, no broker coupling.
#
# Fail-closed rules preserved from the CLI tool:
#   * Transport errors NEVER populate the negative cache (symbol_miss) —
#     only a genuine empty result does. (Same bug class as get_gtts_or_none.)
#   * Disk guard checked before every expiry; refuses below MIN_FREE_GB.

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import threading
import time
from typing import Callable, Optional

import requests

BASE_INDIA = "https://api.india.delta.exchange"
DB_PATH = os.path.expanduser("~/.scalp-app/backtest/crypto_backtest.db")

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
EXPIRY_HM_IST = (17, 30)          # daily options expire 17:30 IST
CONTRACT_LIFE_H = 48              # D1/D2 chain => ~48h of life
DEFAULT_WINDOW_H = 25             # default bulk-fetch window (last 25h)
DEFAULT_SPAN_PCT = 4.0            # strikes within ±span% of entry-time spot
STRIKE_GRIDS = [200, 400, 500, 1000, 2000]
MAX_MISSES_PER_DIRECTION = 4
DEFAULT_PACE_S = 0.35
TIMEOUT = 15
CHUNK_S = 86400                   # perp backfill: 1 day per request

SCHEMA_VERSION = 2
PRICE_SCALE = 100                 # option prices stored as int(price*100)
MIN_FREE_GB = 4.0                 # fail-closed disk guard


class DiskGuardError(RuntimeError):
    """Raised when free disk falls below MIN_FREE_GB. Job must stop cleanly."""


_session = requests.Session()
_session.headers.update({"User-Agent": "scalp-crypto-lab/1.0"})
_session_lock = threading.Lock()


# ----------------------------------------------------------------------
# DB (schema identical to the CLI tool — do not diverge)
# ----------------------------------------------------------------------
SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS perp_candles_1m (
    symbol TEXT NOT NULL,
    ts     INTEGER NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE IF NOT EXISTS option_candles_1m (
    symbol TEXT NOT NULL,
    series TEXT NOT NULL,
    ts     INTEGER NOT NULL,
    open INTEGER, high INTEGER, low INTEGER, close INTEGER,
    volume REAL,
    src    TEXT NOT NULL DEFAULT 'india',
    PRIMARY KEY (symbol, series, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS symbol_miss (
    symbol TEXT PRIMARY KEY,
    checked_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS expiry_chain (
    expiry_ddmmyy TEXT NOT NULL,
    strike INTEGER NOT NULL,
    entry_spot REAL,
    PRIMARY KEY (expiry_ddmmyy, strike)
);

CREATE TABLE IF NOT EXISTS fetch_log (
    what TEXT, symbol TEXT, rows INTEGER, note TEXT, at INTEGER
);

CREATE TABLE IF NOT EXISTS lab_runs (
    run_id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    params_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    trades_json TEXT NOT NULL
);
"""


def disk_free_gb() -> float:
    import shutil
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return shutil.disk_usage(os.path.dirname(DB_PATH)).free / (1024 ** 3)


def db() -> sqlite3.Connection:
    """One connection per caller/thread. WAL handles concurrency."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if ver < SCHEMA_VERSION:
        conn.execute("DROP TABLE IF EXISTS option_candles_1m")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA wal_autocheckpoint=500")
    return conn


# ----------------------------------------------------------------------
# Time / symbol helpers
# ----------------------------------------------------------------------
def expiry_dt_from_ddmmyy(ddmmyy: str) -> dt.datetime:
    d, m, y = int(ddmmyy[0:2]), int(ddmmyy[2:4]), 2000 + int(ddmmyy[4:6])
    return dt.datetime(y, m, d, *EXPIRY_HM_IST, tzinfo=IST)


def trade_window(ddmmyy: str, window_h: int = DEFAULT_WINDOW_H) -> tuple:
    end = int(expiry_dt_from_ddmmyy(ddmmyy).timestamp())
    return end - min(int(window_h), CONTRACT_LIFE_H) * 3600, end


def opt_symbol(side: str, strike: int, ddmmyy: str) -> str:
    return f"{side}-BTC-{strike}-{ddmmyy}"


def strike_candidates(spot: float, span_pct: float):
    for g in STRIKE_GRIDS:
        atm = int(round(spot / g) * g)
        lo = spot * (1 - span_pct / 100.0)
        hi = spot * (1 + span_pct / 100.0)
        up, down, out = atm, atm - g, [atm]
        while True:
            up += g
            grew = False
            if up <= hi:
                out.append(up); grew = True
            if down >= lo:
                out.append(down); grew = True
            down -= g
            if not grew:
                break
        yield g, out


# ----------------------------------------------------------------------
# HTTP (public candles endpoint only)
# ----------------------------------------------------------------------
def get_candles(symbol: str, start: int, end: int,
                resolution: str = "1m", pace_s: float = DEFAULT_PACE_S):
    """List of candle dicts (possibly empty) or None on transport error."""
    try:
        with _session_lock:
            r = _session.get(BASE_INDIA + "/v2/history/candles", params={
                "symbol": symbol, "resolution": resolution,
                "start": start, "end": end}, timeout=TIMEOUT)
        time.sleep(pace_s)
        if r.status_code != 200:
            return None
        return (r.json() or {}).get("result") or []
    except Exception:
        return None


# ----------------------------------------------------------------------
# Perp backfill
# ----------------------------------------------------------------------
def backfill_perp(months: int,
                  progress_cb: Optional[Callable[[dict], None]] = None,
                  cancel: Optional[threading.Event] = None,
                  pace_s: float = DEFAULT_PACE_S) -> int:
    conn = db()
    try:
        end = int(time.time())
        start = end - months * 30 * 86400
        t = start
        total_days = max(1, (end - start) // CHUNK_S)
        done = inserted = 0
        while t < end:
            if cancel is not None and cancel.is_set():
                break
            rows = get_candles("BTCUSD", t, min(t + CHUNK_S, end), pace_s=pace_s)
            if rows is not None:
                conn.executemany(
                    "INSERT OR IGNORE INTO perp_candles_1m "
                    "VALUES('BTCUSD',?,?,?,?,?,?)",
                    [(c["time"], c.get("open"), c.get("high"), c.get("low"),
                      c.get("close"), c.get("volume")) for c in rows])
                conn.commit()
                inserted += len(rows)
            t += CHUNK_S
            done += 1
            if progress_cb and done % 10 == 0:
                progress_cb({"phase": "perp", "done": done,
                             "total": total_days, "rows": inserted})
        return inserted
    finally:
        conn.close()


def spot_at(conn, epoch: int):
    row = conn.execute(
        "SELECT close FROM perp_candles_1m WHERE symbol='BTCUSD' "
        "AND ts<=? AND ts>=? ORDER BY ts DESC LIMIT 1",
        (epoch, epoch - 600)).fetchone()
    return row[0] if row else None


# ----------------------------------------------------------------------
# Per-expiry fetch (discovery + leg candles)
# ----------------------------------------------------------------------
def _store_option_rows(conn, symbol, series, rows):
    def tick(v):
        return None if v is None else int(round(float(v) * PRICE_SCALE))
    conn.executemany(
        "INSERT OR IGNORE INTO option_candles_1m "
        "VALUES(?,?,?,?,?,?,?,?,'india')",
        [(symbol, series, c["time"], tick(c.get("open")), tick(c.get("high")),
          tick(c.get("low")), tick(c.get("close")),
          None if series == "MARK" else c.get("volume")) for c in rows])
    conn.commit()


def fetch_symbol(conn, symbol: str, start: int, end: int,
                 pace_s: float = DEFAULT_PACE_S) -> int:
    """MARK series only (bulk policy). Positive cache = existing rows;
    negative cache = symbol_miss; transport errors cache NOTHING."""
    have = conn.execute(
        "SELECT COUNT(*) FROM option_candles_1m WHERE symbol=? AND "
        "series='MARK'", (symbol,)).fetchone()[0]
    if have:
        return have
    if conn.execute("SELECT 1 FROM symbol_miss WHERE symbol=?",
                    (symbol,)).fetchone():
        return 0
    mark = get_candles("MARK:" + symbol, start, end, pace_s=pace_s)
    if mark is None:
        return 0
    if not mark:
        conn.execute("INSERT OR IGNORE INTO symbol_miss VALUES(?,?)",
                     (symbol, int(time.time())))
        conn.commit()
        return 0
    _store_option_rows(conn, symbol, "MARK", mark)
    return len(mark)


def fetch_day(conn, ddmmyy: str, span_pct: float = DEFAULT_SPAN_PCT,
              window_h: int = DEFAULT_WINDOW_H,
              pace_s: float = DEFAULT_PACE_S) -> bool:
    """Discover + fetch one daily expiry's chain. True if >=5 strikes stored.
    Raises DiskGuardError below MIN_FREE_GB free."""
    free = disk_free_gb()
    if free < MIN_FREE_GB:
        raise DiskGuardError(
            f"{free:.1f} GB free < {MIN_FREE_GB} GB floor — corpus fetch "
            f"refused. Free disk space, then resume (job is resumable).")
    start, end = trade_window(ddmmyy, window_h)
    # entry anchor for strike discovery = 45 min into the window's first hour
    entry_anchor = start + 45 * 60
    spot = spot_at(conn, entry_anchor) or spot_at(conn, start + 3600)
    if spot is None:
        return False

    chosen = None
    for grid, strikes in strike_candidates(spot, span_pct):
        atm = strikes[0]
        if fetch_symbol(conn, opt_symbol("C", atm, ddmmyy), start, end,
                        pace_s) > 0:
            chosen = (grid, strikes)
            break
    if not chosen:
        return False

    _grid, strikes = chosen
    got = 0
    misses_up = misses_down = 0
    for k in strikes:
        if k > strikes[0] and misses_up >= MAX_MISSES_PER_DIRECTION:
            continue
        if k < strikes[0] and misses_down >= MAX_MISSES_PER_DIRECTION:
            continue
        n_c = fetch_symbol(conn, opt_symbol("C", k, ddmmyy), start, end, pace_s)
        n_p = fetch_symbol(conn, opt_symbol("P", k, ddmmyy), start, end, pace_s)
        if n_c or n_p:
            got += 1
            conn.execute("INSERT OR IGNORE INTO expiry_chain VALUES(?,?,?)",
                         (ddmmyy, k, spot))
        else:
            if k > strikes[0]:
                misses_up += 1
            elif k < strikes[0]:
                misses_down += 1
    conn.commit()
    return got >= 5


def backfill_days(months: int,
                  span_pct: float = DEFAULT_SPAN_PCT,
                  window_h: int = DEFAULT_WINDOW_H,
                  pace_s: float = DEFAULT_PACE_S,
                  progress_cb: Optional[Callable[[dict], None]] = None,
                  cancel: Optional[threading.Event] = None) -> dict:
    """Fetch option chains for all expiries in range. Resumable; caches make
    re-runs nearly free. Raises DiskGuardError on the disk floor."""
    conn = db()
    try:
        today = dt.datetime.now(IST).date()
        total = max(1, months * 30 - 2)
        ok = fail = done = 0
        for i in range(2, months * 30):
            if cancel is not None and cancel.is_set():
                break
            day = today - dt.timedelta(days=i)
            ddmmyy = day.strftime("%d%m%y")
            if fetch_day(conn, ddmmyy, span_pct, window_h, pace_s):
                ok += 1
            else:
                fail += 1
            done += 1
            if progress_cb and done % 3 == 0:
                progress_cb({"phase": "options", "done": done, "total": total,
                             "current": ddmmyy, "usable": ok, "skipped": fail,
                             "disk_free_gb": round(disk_free_gb(), 1)})
        return {"usable": ok, "skipped": fail, "done": done, "total": total}
    finally:
        conn.close()


# ----------------------------------------------------------------------
# Cheap stats (NEVER COUNT(*) the 60M-row option table on a poll path)
# ----------------------------------------------------------------------
def corpus_stats() -> dict:
    conn = db()
    try:
        p = conn.execute(
            "SELECT COUNT(*), MIN(ts), MAX(ts) FROM perp_candles_1m "
            "WHERE symbol='BTCUSD'").fetchone()
        ex = conn.execute(
            "SELECT COUNT(DISTINCT expiry_ddmmyy) FROM expiry_chain"
        ).fetchone()[0]
        runs = conn.execute("SELECT COUNT(*) FROM lab_runs").fetchone()[0]
        size_mb = (os.path.getsize(DB_PATH)
                   if os.path.exists(DB_PATH) else 0) / 1e6

        def d(t):
            return (dt.datetime.fromtimestamp(t, UTC).date().isoformat()
                    if t else None)
        return {
            "perp_rows": p[0], "perp_from": d(p[1]), "perp_to": d(p[2]),
            "expiries": ex, "lab_runs": runs,
            "db_size_mb": round(size_mb, 1),
            "disk_free_gb": round(disk_free_gb(), 1),
            "db_path": DB_PATH,
        }
    finally:
        conn.close()
# ── CRYPTO_LAB END ──