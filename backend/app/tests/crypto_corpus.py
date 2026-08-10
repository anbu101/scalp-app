#!/usr/bin/env python3
"""
crypto_corpus.py — Delta Exchange BTC options corpus builder (Phase A)
======================================================================
Standalone. Touches NOTHING in the app trees. Writes to its own DB:

    ~/.scalp-app/backtest/crypto_backtest.db      (separate from backtest.db)

Design (locked decisions D1–D9c):
  * D1  BTC first.
  * D2  Daily expiries (17:30 IST).
  * D4  MARK-price 1m candles primary; LTP fetched alongside as overlay.
  * D6  India entity primary source; rows tagged with source base.
  * D7  Contract life window = expiry-48h .. expiry (UTC epochs).
  * D8  Strike discovery by ATM-outward grid probing with a negative cache
        (symbol_miss) so re-runs are free.
  * D9c Lazy fetch: only strikes within ±SPAN_PCT of entry-time spot.
  * v2  Bulk fetch = MARK series only, TRADE window only (last 25h of the
        contract: 15m warmup before 17:45 IST entry .. 17:30 expiry).
        LTP is fetched lazily later (harness legs only, or --with-ltp).
        Cuts corpus from ~15 GB / ~8 h to ~4-5 GB / ~5 h for 24 months.

CLI:
  python3 crypto_corpus.py init
  python3 crypto_corpus.py backfill-perp --months 24
  python3 crypto_corpus.py fetch-day --expiry 050826
  python3 crypto_corpus.py backfill-days --months 24        # full corpus
  python3 crypto_corpus.py stats

All endpoints are public/read-only: /v2/history/candles only.
Resumable: every command is idempotent (INSERT OR IGNORE + negative cache).
"""

import argparse
import datetime as dt
import os
import sqlite3
import sys
import time

try:
    import requests
except ImportError:
    print("ERROR: `pip3 install requests` first.")
    sys.exit(1)

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
BASE_INDIA = "https://api.india.delta.exchange"
DB_PATH = os.path.expanduser("~/.scalp-app/backtest/crypto_backtest.db")

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
EXPIRY_HM_IST = (17, 30)
ENTRY_HM_IST = (17, 45)          # D7: entry on D-1 evening
CONTRACT_LIFE_H = 48             # full life (probe/reference only)
FETCH_WINDOW_H = 25              # v2: bulk-fetch only the trade window

SPAN_PCT = 4.0                   # D9c: strikes within ±4% of entry spot
                                 # (shorts 1–2.5% OTM; tightened from 6 for disk)
STRIKE_GRIDS = [200, 400, 500, 1000, 2000]
MAX_MISSES_PER_DIRECTION = 4     # stop walking a grid after N straight misses

PACE_S = 0.35
TIMEOUT = 15
CHUNK_S = 86400                  # 1 day per candles request (1440 rows < cap)

session = requests.Session()
session.headers.update({"User-Agent": "scalp-crypto-corpus/1.0"})


# ----------------------------------------------------------------------
# DB
# ----------------------------------------------------------------------
SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS perp_candles_1m (
    symbol TEXT NOT NULL,
    ts     INTEGER NOT NULL,          -- epoch seconds UTC
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE IF NOT EXISTS option_candles_1m (
    symbol TEXT NOT NULL,             -- e.g. C-BTC-116000-050826
    series TEXT NOT NULL,             -- 'MARK' or 'LTP'
    ts     INTEGER NOT NULL,
    open INTEGER, high INTEGER, low INTEGER, close INTEGER,  -- price*PRICE_SCALE
    volume REAL,                      -- NULL for MARK (meaningless)
    src    TEXT NOT NULL DEFAULT 'india',
    PRIMARY KEY (symbol, series, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS symbol_miss (   -- D8 negative cache
    symbol TEXT PRIMARY KEY,
    checked_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS expiry_chain (  -- discovered strikes per expiry
    expiry_ddmmyy TEXT NOT NULL,
    strike INTEGER NOT NULL,
    entry_spot REAL,
    PRIMARY KEY (expiry_ddmmyy, strike)
);

CREATE TABLE IF NOT EXISTS fetch_log (
    what TEXT, symbol TEXT, rows INTEGER, note TEXT, at INTEGER
);
"""


SCHEMA_VERSION = 2
PRICE_SCALE = 100                 # option prices stored as int(price*100)
MIN_FREE_GB = 4.0                 # fail-closed disk guard for bulk runs


def disk_free_gb() -> float:
    import shutil
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return shutil.disk_usage(os.path.dirname(DB_PATH)).free / (1024 ** 3)


def db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if ver < SCHEMA_VERSION:
        # v1 -> v2: option table layout changed (WITHOUT ROWID + int ticks).
        # Perp corpus, negative cache and chain map are unchanged — keep them.
        conn.execute("DROP TABLE IF EXISTS option_candles_1m")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA wal_autocheckpoint=500")   # keep WAL small on 95% disk
    return conn


def log(conn, what, symbol, rows, note=""):
    conn.execute(
        "INSERT INTO fetch_log(what,symbol,rows,note,at) VALUES(?,?,?,?,?)",
        (what, symbol, rows, note, int(time.time())))
    conn.commit()


# ----------------------------------------------------------------------
# Time / symbol helpers (pure — unit-testable offline)
# ----------------------------------------------------------------------
def expiry_dt_from_ddmmyy(ddmmyy: str) -> dt.datetime:
    d, m, y = int(ddmmyy[0:2]), int(ddmmyy[2:4]), 2000 + int(ddmmyy[4:6])
    return dt.datetime(y, m, d, *EXPIRY_HM_IST, tzinfo=IST)


def entry_dt_for_expiry(ddmmyy: str) -> dt.datetime:
    """D7: 17:45 IST on the evening BEFORE expiry day."""
    exp = expiry_dt_from_ddmmyy(ddmmyy)
    prev = exp - dt.timedelta(days=1)
    return prev.replace(hour=ENTRY_HM_IST[0], minute=ENTRY_HM_IST[1])


def life_window(ddmmyy: str) -> tuple[int, int]:
    end = int(expiry_dt_from_ddmmyy(ddmmyy).timestamp())
    return end - CONTRACT_LIFE_H * 3600, end


def trade_window(ddmmyy: str) -> tuple[int, int]:
    """v2 bulk-fetch window: 25h before expiry (covers 17:45 IST D-1 entry
    with ~15m warmup) .. expiry."""
    end = int(expiry_dt_from_ddmmyy(ddmmyy).timestamp())
    return end - FETCH_WINDOW_H * 3600, end


def opt_symbol(side: str, strike: int, ddmmyy: str) -> str:
    return f"{side}-BTC-{strike}-{ddmmyy}"


def strike_candidates(spot: float, span_pct: float = SPAN_PCT):
    """Yield (grid, ordered ATM-outward strike list within ±span_pct)."""
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
# HTTP
# ----------------------------------------------------------------------
def get_candles(symbol: str, start: int, end: int, resolution: str = "1m"):
    """Returns list of candle dicts (possibly empty) or None on transport
    error. Never raises."""
    try:
        r = session.get(BASE_INDIA + "/v2/history/candles", params={
            "symbol": symbol, "resolution": resolution,
            "start": start, "end": end}, timeout=TIMEOUT)
        time.sleep(PACE_S)
        if r.status_code != 200:
            return None
        return (r.json() or {}).get("result") or []
    except Exception:
        return None


# ----------------------------------------------------------------------
# Perp backfill
# ----------------------------------------------------------------------
def backfill_perp(months: int):
    conn = db()
    end = int(time.time())
    start = end - months * 30 * 86400
    t = start
    total = 0
    while t < end:
        rows = get_candles("BTCUSD", t, min(t + CHUNK_S, end))
        if rows is None:
            print(f"  transport error at {dt.datetime.fromtimestamp(t, UTC)} "
                  f"— retry this window on next run")
        else:
            conn.executemany(
                "INSERT OR IGNORE INTO perp_candles_1m VALUES('BTCUSD',?,?,?,?,?,?)",
                [(c["time"], c.get("open"), c.get("high"), c.get("low"),
                  c.get("close"), c.get("volume")) for c in rows])
            conn.commit()
            total += len(rows)
        t += CHUNK_S
        if (t - start) % (30 * CHUNK_S) == 0:
            print(f"  ...{dt.datetime.fromtimestamp(t, UTC).date()} "
                  f"({total} rows so far)")
    log(conn, "backfill-perp", "BTCUSD", total, f"months={months}")
    print(f"perp backfill done: {total} rows inserted (duplicates ignored)")


def spot_at(conn, epoch: int) -> float | None:
    """Perp close at-or-before epoch (within 10 min), from local corpus."""
    row = conn.execute(
        "SELECT close FROM perp_candles_1m WHERE symbol='BTCUSD' "
        "AND ts<=? AND ts>=? ORDER BY ts DESC LIMIT 1",
        (epoch, epoch - 600)).fetchone()
    return row[0] if row else None


# ----------------------------------------------------------------------
# Per-expiry fetch (discovery + leg candles)
# ----------------------------------------------------------------------
def _tick(v):
    return None if v is None else int(round(float(v) * PRICE_SCALE))


def _store_option_rows(conn, symbol, series, rows):
    conn.executemany(
        "INSERT OR IGNORE INTO option_candles_1m VALUES(?,?,?,?,?,?,?,?,'india')",
        [(symbol, series, c["time"],
          _tick(c.get("open")), _tick(c.get("high")),
          _tick(c.get("low")), _tick(c.get("close")),
          None if series == "MARK" else c.get("volume")) for c in rows])
    conn.commit()


def read_series(conn, symbol: str, series: str = "MARK"):
    """Harness accessor: [(ts, o, h, l, c), ...] as floats, ts ascending."""
    return [(ts,
             o / PRICE_SCALE if o is not None else None,
             h / PRICE_SCALE if h is not None else None,
             l / PRICE_SCALE if l is not None else None,
             c / PRICE_SCALE if c is not None else None)
            for ts, o, h, l, c in conn.execute(
                "SELECT ts,open,high,low,close FROM option_candles_1m "
                "WHERE symbol=? AND series=? ORDER BY ts",
                (symbol, series))]


def _have_symbol(conn, symbol) -> bool:
    return conn.execute(
        "SELECT 1 FROM option_candles_1m WHERE symbol=? LIMIT 1",
        (symbol,)).fetchone() is not None


def _is_miss(conn, symbol) -> bool:
    return conn.execute(
        "SELECT 1 FROM symbol_miss WHERE symbol=?",
        (symbol,)).fetchone() is not None


def _mark_miss(conn, symbol):
    conn.execute("INSERT OR IGNORE INTO symbol_miss VALUES(?,?)",
                 (symbol, int(time.time())))
    conn.commit()


def fetch_symbol(conn, symbol: str, start: int, end: int,
                 with_ltp: bool = False) -> int:
    """Fetch MARK (primary) for one option symbol; LTP only if with_ltp.
    Returns MARK rows. Uses positive cache (db rows) and negative cache
    (symbol_miss)."""
    if _have_symbol(conn, symbol):
        n = conn.execute(
            "SELECT COUNT(*) FROM option_candles_1m "
            "WHERE symbol=? AND series='MARK'", (symbol,)).fetchone()[0]
        if with_ltp and conn.execute(
                "SELECT 1 FROM option_candles_1m WHERE symbol=? AND "
                "series='LTP' LIMIT 1", (symbol,)).fetchone() is None:
            ltp = get_candles(symbol, start, end)
            if ltp:
                _store_option_rows(conn, symbol, "LTP", ltp)
        return n
    if _is_miss(conn, symbol):
        return 0
    mark = get_candles("MARK:" + symbol, start, end)
    if mark is None:                       # transport error: no verdict,
        return 0                           # do NOT negative-cache
    if not mark:
        _mark_miss(conn, symbol)
        return 0
    _store_option_rows(conn, symbol, "MARK", mark)
    if with_ltp:
        ltp = get_candles(symbol, start, end)
        if ltp:
            _store_option_rows(conn, symbol, "LTP", ltp)
    log(conn, "fetch-symbol", symbol, len(mark))
    return len(mark)


def fetch_day(ddmmyy: str, span_pct: float = SPAN_PCT,
              with_ltp: bool = False) -> bool:
    """Discover + fetch the chain for one daily expiry. True if usable."""
    free = disk_free_gb()
    if free < MIN_FREE_GB:
        print(f"[{ddmmyy}] DISK GUARD: {free:.1f} GB free < {MIN_FREE_GB} GB "
              f"— refusing to fetch. Free space, then re-run (resumable).")
        raise SystemExit(2)
    conn = db()
    start, end = trade_window(ddmmyy)
    entry_epoch = int(entry_dt_for_expiry(ddmmyy).timestamp())
    spot = spot_at(conn, entry_epoch)
    if spot is None:
        print(f"[{ddmmyy}] no perp spot at entry time — run backfill-perp "
              f"covering this date first")
        return False

    # D8: find the grid — first candidate grid whose ATM call has MARK data
    chosen = None
    for grid, strikes in strike_candidates(spot, span_pct):
        atm = strikes[0]
        if fetch_symbol(conn, opt_symbol("C", atm, ddmmyy), start, end) > 0:
            chosen = (grid, strikes)
            break
    if not chosen:
        print(f"[{ddmmyy}] spot={spot:,.0f}: no grid produced an ATM hit — "
              f"skipping (logged)")
        log(conn, "fetch-day", ddmmyy, 0, "no-grid-hit")
        return False

    grid, strikes = chosen
    got = 0
    misses_up = misses_down = 0
    for k in strikes:
        # stop walking a direction after repeated misses (grid edge)
        if k > strikes[0] and misses_up >= MAX_MISSES_PER_DIRECTION:
            continue
        if k < strikes[0] and misses_down >= MAX_MISSES_PER_DIRECTION:
            continue
        n_c = fetch_symbol(conn, opt_symbol("C", k, ddmmyy), start, end,
                           with_ltp=with_ltp)
        n_p = fetch_symbol(conn, opt_symbol("P", k, ddmmyy), start, end,
                           with_ltp=with_ltp)
        if n_c or n_p:
            got += 1
            conn.execute(
                "INSERT OR IGNORE INTO expiry_chain VALUES(?,?,?)",
                (ddmmyy, k, spot))
        else:
            if k > strikes[0]:
                misses_up += 1
            elif k < strikes[0]:
                misses_down += 1
    conn.commit()
    log(conn, "fetch-day", ddmmyy, got, f"grid={grid} spot={spot:.0f}")
    print(f"[{ddmmyy}] grid={grid} spot={spot:,.0f} strikes_stored={got}")
    return got >= 5        # need enough strikes for shorts + wings


def backfill_days(months: int):
    today = dt.datetime.now(IST).date()
    ok = fail = 0
    for i in range(2, months * 30):        # start 2 days back (fully expired)
        day = today - dt.timedelta(days=i)
        ddmmyy = day.strftime("%d%m%y")
        try:
            if fetch_day(ddmmyy):
                ok += 1
            else:
                fail += 1
        except KeyboardInterrupt:
            print("interrupted — resumable, just re-run")
            break
    print(f"backfill-days done: usable={ok} skipped={fail}")


# ----------------------------------------------------------------------
# Stats
# ----------------------------------------------------------------------
def stats():
    conn = db()
    p = conn.execute("SELECT COUNT(*),MIN(ts),MAX(ts) FROM perp_candles_1m"
                     ).fetchone()
    o = conn.execute("SELECT COUNT(*) FROM option_candles_1m").fetchone()[0]
    ex = conn.execute("SELECT COUNT(DISTINCT expiry_ddmmyy) FROM expiry_chain"
                      ).fetchone()[0]
    miss = conn.execute("SELECT COUNT(*) FROM symbol_miss").fetchone()[0]
    fmt = (lambda t: dt.datetime.fromtimestamp(t, UTC).date() if t else "-")
    size_mb = (os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0) / 1e6
    print(f"perp rows   : {p[0]:>10,}  ({fmt(p[1])} .. {fmt(p[2])})")
    print(f"option rows : {o:>10,}")
    print(f"expiries    : {ex:>10,}")
    print(f"neg-cache   : {miss:>10,}")
    print(f"db size     : {size_mb:>10,.1f} MB   (disk free {disk_free_gb():.1f} GB)")
    print(f"db          : {DB_PATH}")


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    b = sub.add_parser("backfill-perp"); b.add_argument("--months", type=int, default=24)
    f = sub.add_parser("fetch-day")
    f.add_argument("--expiry", required=True, help="DDMMYY")
    f.add_argument("--with-ltp", action="store_true",
                   help="also fetch LTP series (doubles requests/rows)")
    f.add_argument("--span", type=float, default=SPAN_PCT)
    d = sub.add_parser("backfill-days")
    d.add_argument("--months", type=int, default=24)
    d.add_argument("--pace", type=float, default=PACE_S,
                   help="seconds between requests (default %(default)s)")
    sub.add_parser("stats")
    a = ap.parse_args()

    if a.cmd == "init":
        db(); print(f"initialized {DB_PATH}")
    elif a.cmd == "backfill-perp":
        backfill_perp(a.months)
    elif a.cmd == "fetch-day":
        fetch_day(a.expiry, span_pct=a.span, with_ltp=a.with_ltp)
    elif a.cmd == "backfill-days":
        globals()["PACE_S"] = a.pace
        backfill_days(a.months)
    elif a.cmd == "stats":
        stats()


if __name__ == "__main__":
    main()