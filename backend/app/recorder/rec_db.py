# backend/app/recorder/rec_db.py
#
# ── DATA_RECORDER_20261029 ── the recorder database.
#
# WHERE
#   ~/.scalp-app/backtest/recorder/NIFTY.db (CHECKS_LAYOUT_20261029: beside
#   backtest.db) — its own folder, its own backups. The Dhan
#   corpus (~/.scalp-app/backtest/backtest.db) is never opened for writing by
#   the recorder (decided by Anbu 2026-10-29: "a different DB so that the
#   backtest data received from Dhan will not be corrupted by any chance").
#
# SCHEMA
#   backtest_candles_1m — byte-for-byte the corpus table (repo/schema.sql):
#     same name, columns, PK and indexes, so every reader that already speaks
#     the corpus (inventory scan, replay Corpus, backtest runners later) reads
#     it unchanged. Spot = 256265 / NIFTYSPOT / SPOT / expiry ''.
#   band            — the day's universe (one row per instrument per day).
#   day_status      — per-day pull state + quality gate.
#   pull_log        — one row per Kite historical call (retries, catch-up).
#   session_config  — every licensed strategy's config + mode at 09:14
#                     (input for the parity ledger, collected from day one).
#   config_changes  — ── PARITY_CAPTURE_20261029 ── every later Settings save,
#                     timestamped (deduplicated by hash).
#   meta            — schema version, cutover, one-time migration records.
#
# READING BOTH
#   open_union_ro(main) → a read-only connection on backtest.db with the
#   recorder ATTACHed and a TEMP VIEW named backtest_candles_1m over
#   corpus ∪ recorder (corpus wins on any (tradingsymbol, ts) it holds). An
#   unqualified `backtest_candles_1m` resolves to the TEMP view first, so code
#   written against the corpus needs no change. Used by Trade Replay now; the
#   backtest data-source toggle (phase 3) will reuse it.

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

FENCE = "DATA_RECORDER_20261029"
SCHEMA_VERSION = 1
UNDERLYING = "NIFTY"
CUTOVER = date(2026, 9, 1)            # first day the Dhan corpus lacks a full chain
SPOT_TOKEN = 256265                    # Kite NIFTY 50 (= the corpus's Dhan spot identity)
SPOT_SYMBOL = "NIFTYSPOT"
DHAN_TOKEN_FLOOR = 9_000_000_000       # Dhan surrogate tokens live at/above this
IST = timezone(timedelta(hours=5, minutes=30))

COLS = ("instrument_token, ts, underlying, tradingsymbol, instrument_type, "
        "strike, expiry, open, high, low, close, volume, oi")

_SCHEMA = """
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
CREATE INDEX IF NOT EXISTS idx_bt1m_sym_ts ON backtest_candles_1m (tradingsymbol, ts);
CREATE INDEX IF NOT EXISTS idx_bt1m_under_exp_ts ON backtest_candles_1m (underlying, expiry, ts);
CREATE INDEX IF NOT EXISTS idx_bt1m_under_type_ts ON backtest_candles_1m (underlying, instrument_type, ts);

CREATE TABLE IF NOT EXISTS band (
    day              TEXT    NOT NULL,
    instrument_token INTEGER NOT NULL,
    tradingsymbol    TEXT    NOT NULL,
    instrument_type  TEXT    NOT NULL,
    strike           REAL    NOT NULL,
    expiry           TEXT    NOT NULL,
    kind             TEXT    NOT NULL,      -- spot | week | next | month
    reason           TEXT    NOT NULL,      -- open | widened | catchup
    added_at         INTEGER NOT NULL,
    PRIMARY KEY (day, instrument_token)
);

CREATE TABLE IF NOT EXISTS day_status (
    day          TEXT PRIMARY KEY,
    band_n       INTEGER NOT NULL DEFAULT 0,
    pulled_n     INTEGER NOT NULL DEFAULT 0,
    lost_n       INTEGER NOT NULL DEFAULT 0,
    error_n      INTEGER NOT NULL DEFAULT 0,
    bars         INTEGER NOT NULL DEFAULT 0,
    spot_bars    INTEGER NOT NULL DEFAULT 0,
    completeness REAL,
    quality      TEXT,                      -- recording | clean | partial | lost
    last_pull_at INTEGER,
    final_at     INTEGER,
    attempts     INTEGER NOT NULL DEFAULT 0,
    notes        TEXT
);

CREATE TABLE IF NOT EXISTS pull_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    day              TEXT    NOT NULL,
    instrument_token INTEGER NOT NULL,
    tradingsymbol    TEXT,
    pulled_at        INTEGER NOT NULL,
    from_ts          INTEGER,
    to_ts            INTEGER,
    rows             INTEGER NOT NULL DEFAULT 0,
    status           TEXT    NOT NULL,      -- ok | empty | error | expired
    error            TEXT
);
CREATE INDEX IF NOT EXISTS idx_pull_log_day ON pull_log (day, instrument_token);

CREATE TABLE IF NOT EXISTS session_config (
    day          TEXT NOT NULL,
    strategy_id  TEXT NOT NULL,
    mode         TEXT,
    degraded     INTEGER NOT NULL DEFAULT 0,
    config_json  TEXT,
    config_hash  TEXT,
    app_version  TEXT,
    captured_at  INTEGER NOT NULL,
    PRIMARY KEY (day, strategy_id)
);

CREATE TABLE IF NOT EXISTS config_changes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           INTEGER NOT NULL,
    strategy_id  TEXT NOT NULL,
    mode         TEXT,
    config_json  TEXT,
    config_hash  TEXT,
    app_version  TEXT,
    source       TEXT
);
CREATE INDEX IF NOT EXISTS idx_config_changes ON config_changes (strategy_id, ts);

CREATE TABLE IF NOT EXISTS meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


# ─────────────────────────────────────────────────────────────────────
# paths
# ─────────────────────────────────────────────────────────────────────
def app_home() -> Path:
    from app.utils.app_paths import APP_HOME
    return APP_HOME


def main_db_path() -> Path:
    return app_home() / "backtest" / "backtest.db"


def db_path() -> Path:
    # ── CHECKS_LAYOUT_20261029 ── beside backtest.db (Anbu: "store it wherever
    # the backtest DB is stored"); was ~/.scalp-app/recorder/ in DATA_RECORDER.
    return main_db_path().parent / "recorder" / f"{UNDERLYING}.db"


def old_db_dir() -> Path:
    """Where DATA_RECORDER_20261029 first put it (moved by the apply script)."""
    return app_home() / "recorder"


def db_path_for(main_path) -> Path:
    """The recorder that pairs with a corpus file: a recorder/ folder BESIDE
    it (~/.scalp-app/backtest/backtest.db → ~/.scalp-app/backtest/recorder/
    NIFTY.db), so a test suite's temp corpus can never touch the real one."""
    p = Path(str(main_path))
    try:
        if p.resolve() == main_db_path().resolve():
            return db_path()
    except Exception:
        pass
    return p.parent / "recorder" / f"{UNDERLYING}.db"


# ─────────────────────────────────────────────────────────────────────
# connections
# ─────────────────────────────────────────────────────────────────────
_SCHEMA_DONE: set = set()      # ── PARITY_CAPTURE_20261029 ── schema written once per file per process:
                               # a write on every connect contends with the pass's own writes


def ensure_schema(conn: sqlite3.Connection, key: Optional[str] = None) -> None:
    if key and key in _SCHEMA_DONE:
        return
    conn.executescript(_SCHEMA)
    now = int(datetime.now(timezone.utc).timestamp())
    conn.execute("INSERT OR IGNORE INTO meta (key, value, updated_at) VALUES ('schema_version', ?, ?)",
                 (str(SCHEMA_VERSION), now))
    conn.execute("INSERT OR IGNORE INTO meta (key, value, updated_at) VALUES ('cutover', ?, ?)",
                 (CUTOVER.isoformat(), now))
    conn.commit()
    if key:
        _SCHEMA_DONE.add(key)


import threading as _threading
_HEAL_LOCK = _threading.Lock()


def _heal(p: Path) -> None:
    """── CHECKS_LAYOUT_20261029 ── first touch of the real recorder after the
    upgrade: if it still sits at ~/.scalp-app/recorder, move it beside
    backtest.db before anything creates an empty one there."""
    try:
        if p != db_path() or p.exists():
            return
        with _HEAL_LOCK:
            if not p.exists() and (old_db_dir() / f"{UNDERLYING}.db").exists():
                relocate_from_old()
    except Exception:
        pass


def connect(path=None) -> sqlite3.Connection:
    """Read-write connection on the recorder (created with its schema)."""
    p = Path(str(path)) if path else db_path()
    _heal(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        _SCHEMA_DONE.discard(str(p))                       # a new / replaced file needs its schema
    # URI-enabled connection: ATTACH 'file:…?mode=ro' only works on a URI
    # connection unless SQLite was built with SQLITE_USE_URI (Linux builds
    # usually are, the macOS Python build is not — apply scar 2026-10-29).
    conn = sqlite3.connect(_uri(p), uri=True, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    ensure_schema(conn, str(p))
    return conn


def _has_candles(conn: sqlite3.Connection, schema: str) -> bool:
    try:
        return bool(conn.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' "
                                 "AND name='backtest_candles_1m'").fetchone())
    except Exception:
        return False


def _uri(p: Path) -> str:
    return "file:" + quote(str(p), safe="/:")


def _uri_ro(p: Path) -> str:
    return _uri(p) + "?mode=ro"


def _view_sql(corpus: str, rec: Optional[str]) -> str:
    """corpus ∪ recorder; the corpus wins on any (tradingsymbol, ts) it holds."""
    if not rec:
        return f"SELECT {COLS} FROM {corpus}.backtest_candles_1m"
    return (f"SELECT {COLS} FROM {corpus}.backtest_candles_1m UNION ALL "
            f"SELECT {COLS} FROM {rec}.backtest_candles_1m r WHERE NOT EXISTS "
            f"(SELECT 1 FROM {corpus}.backtest_candles_1m m "
            f"WHERE m.tradingsymbol = r.tradingsymbol AND m.ts = r.ts)")


def attached(conn: sqlite3.Connection) -> set:
    try:
        return {r[1] for r in conn.execute("PRAGMA database_list").fetchall()}
    except Exception:
        return set()


def union_select(conn: sqlite3.Connection) -> str:
    """The SELECT behind the TEMP view for a connection made by
    open_union_ro / open_fill (for code that rebuilds the view, e.g. the
    replay's forming-bar hook)."""
    names = attached(conn)
    if "rec" in names:
        return _view_sql("main", "rec")
    if "bt" in names:
        return (f"SELECT {COLS} FROM bt.backtest_candles_1m UNION ALL "
                f"SELECT {COLS} FROM main.backtest_candles_1m r WHERE NOT EXISTS "
                f"(SELECT 1 FROM bt.backtest_candles_1m m "
                f"WHERE m.tradingsymbol = r.tradingsymbol AND m.ts = r.ts)")
    return f"SELECT {COLS} FROM main.backtest_candles_1m"


def open_union_ro(main_path, underlying: str = UNDERLYING) -> sqlite3.Connection:
    """Read-only connection on the corpus; for NIFTY with a recorder present,
    the recorder is attached and `backtest_candles_1m` becomes the union.
    Any attach failure falls back to the plain corpus connection."""
    conn = sqlite3.connect(_uri_ro(Path(str(main_path))), uri=True, timeout=30)
    if (underlying or UNDERLYING).upper() != UNDERLYING:
        return conn
    rp = db_path_for(main_path)
    _heal(rp)
    if not rp.exists():
        return conn
    try:
        conn.execute("ATTACH DATABASE ? AS rec", (_uri_ro(rp),))
        if not _has_candles(conn, "rec"):
            conn.execute("DETACH DATABASE rec")
            return conn
        conn.execute("CREATE TEMP VIEW IF NOT EXISTS backtest_candles_1m AS " + _view_sql("main", "rec"))
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        conn = sqlite3.connect(_uri_ro(Path(str(main_path))), uri=True, timeout=30)
    return conn


def open_fill(main_path) -> sqlite3.Connection:
    """Read-write connection on the RECORDER that pairs with main_path, with
    the corpus attached read-only as `bt` and a TEMP view `backtest_candles_1m`
    over corpus ∪ recorder for the gap checks. Writes must name
    main.backtest_candles_1m (main = the recorder here)."""
    conn = connect(db_path_for(main_path))
    mp = Path(str(main_path))
    if mp.exists():
        try:
            conn.execute("ATTACH DATABASE ? AS bt", (_uri_ro(mp),))
        except Exception as e:
            # fail closed: without the corpus the gap checks would see holes
            # that are not there and copy Dhan minutes into the recorder
            conn.close()
            raise RuntimeError(f"could not open the backtest corpus read-only: {e}")
        if not _has_candles(conn, "bt"):
            conn.execute("DETACH DATABASE bt")
    conn.execute("CREATE TEMP VIEW IF NOT EXISTS backtest_candles_1m AS " + union_select(conn))
    return conn


def corpus_minutes(conn: sqlite3.Connection, sym: str, lo: int, hi: int) -> set:
    """Minutes the Dhan corpus already holds for a contract (open_fill
    connections only) — the replay fill never copies those into the recorder."""
    if "bt" not in attached(conn):
        return set()
    return {int(r[0]) for r in conn.execute(
        "SELECT ts FROM bt.backtest_candles_1m WHERE tradingsymbol=? AND ts>=? AND ts<?", (sym, lo, hi))}


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta (key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                 "value=excluded.value, updated_at=excluded.updated_at",
                 (key, value, int(datetime.now(timezone.utc).timestamp())))
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else None


# ─────────────────────────────────────────────────────────────────────
# one-time move of the October replay-filled option rows (decided
# 2026-10-29): Kite-token NIFTY CE/PE rows dated on/after CUTOVER leave
# backtest.db for the recorder. Copy → verify → delete; the copy commits
# before the delete, so an interruption leaves rows in both (harmless — the
# union view shows the corpus copy) and a re-run finishes the job.
# ─────────────────────────────────────────────────────────────────────
def cutover_epoch() -> int:
    return int(datetime(CUTOVER.year, CUTOVER.month, CUTOVER.day, tzinfo=IST).timestamp())


_MOVE_WHERE = ("underlying='NIFTY' AND instrument_type IN ('CE','PE') "
               "AND instrument_token < ? AND ts >= ?")
_MOVE_WHERE_B = ("b.underlying='NIFTY' AND b.instrument_type IN ('CE','PE') "
                 "AND b.instrument_token < ? AND b.ts >= ?")


def move_post_cutover_options(main_path, rec_path=None) -> dict:
    mp = Path(str(main_path))
    if not mp.exists():
        return {"ok": True, "skipped": "no backtest.db"}
    conn = connect(rec_path or db_path_for(mp))
    try:
        conn.execute("ATTACH DATABASE ? AS bt", (str(mp),))
        if not _has_candles(conn, "bt"):
            return {"ok": True, "skipped": "backtest.db has no candle table"}
        args = (DHAN_TOKEN_FLOOR, cutover_epoch())
        n_src = conn.execute(f"SELECT COUNT(*) FROM bt.backtest_candles_1m WHERE {_MOVE_WHERE}", args).fetchone()[0]
        if n_src == 0:
            return {"ok": True, "moved": 0, "already": get_meta(conn, "moved_post_cutover_options")}
        conn.execute(f"INSERT OR IGNORE INTO main.backtest_candles_1m ({COLS}) "
                     f"SELECT {COLS} FROM bt.backtest_candles_1m WHERE {_MOVE_WHERE}", args)
        conn.commit()
        n_have = conn.execute(
            f"SELECT COUNT(*) FROM bt.backtest_candles_1m b WHERE {_MOVE_WHERE_B} "
            "AND EXISTS (SELECT 1 FROM main.backtest_candles_1m r WHERE r.instrument_token=b.instrument_token "
            "AND r.ts=b.ts AND r.open=b.open AND r.high=b.high AND r.low=b.low AND r.close=b.close)",
            args).fetchone()[0]
        if n_have != n_src:
            return {"ok": False, "error": f"verify failed: {n_have} of {n_src} rows present in the recorder — "
                                          "nothing deleted from backtest.db"}
        conn.execute(f"DELETE FROM bt.backtest_candles_1m WHERE {_MOVE_WHERE}", args)
        conn.commit()
        rec = {"moved": n_src, "at": datetime.now(IST).isoformat(timespec="seconds")}
        import json
        set_meta(conn, "moved_post_cutover_options", json.dumps(rec))
        return {"ok": True, **rec}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────
# ── CHECKS_LAYOUT_20261029 ── one-time move ~/.scalp-app/recorder →
# ~/.scalp-app/backtest/recorder (DB + settings + backups). The WAL is folded
# into the DB first, then the folder is renamed (same disk). Run only while
# the app is closed (the apply script checks).
# ─────────────────────────────────────────────────────────────────────
def relocate_from_old() -> dict:
    import shutil
    old, new = old_db_dir(), db_path().parent
    if not old.exists():
        return {"ok": True, "moved": False, "reason": "nothing at the old place"}
    if new.exists() and any(new.iterdir()):
        return {"ok": False, "error": f"both {old} and {new} exist — not merging automatically"}
    odb = old / f"{UNDERLYING}.db"
    if odb.exists():
        c = sqlite3.connect(str(odb), timeout=30)
        try:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            n = c.execute("SELECT COUNT(*) FROM backtest_candles_1m").fetchone()[0]
        finally:
            c.close()
    else:
        n = 0
    new.parent.mkdir(parents=True, exist_ok=True)
    if new.exists():
        new.rmdir()                                # empty
    shutil.move(str(old), str(new))
    n2 = 0
    if db_path().exists():
        m = sqlite3.connect(str(db_path()), timeout=30)
        try:
            n2 = m.execute("SELECT COUNT(*) FROM backtest_candles_1m").fetchone()[0]
        finally:
            m.close()
    if n2 != n:
        return {"ok": False, "error": f"row count changed in the move ({n} → {n2})"}
    return {"ok": True, "moved": True, "rows": n, "to": str(new)}
