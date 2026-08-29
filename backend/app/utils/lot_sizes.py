# backend/app/backtest/util/lot_sizes.py
#
# ── STOCK_LOT_AUTO_20260828 ──
# Single source of truth for "how many shares is one lot of <SYMBOL>, right now".
#
# WHY THIS EXISTS
#   Stock runners (GC, VET) previously resolved the lot from a hand-edited dict
#   (STOCK_LOT_SIZES) that had exactly one entry. Every new stock corpus meant a
#   code edit, a rebuild and a release. That does not scale and it goes stale:
#   NSE revises lots on a rolling basis and corporate actions re-cut them
#   overnight. This module makes the system resolve the lot by itself.
#
# SOURCE
#   Dhan's public detailed scrip master (no auth) — the SAME CSV that
#   app.backtest.dhan.scrip_master and stock_backfill already consume, so we add
#   no new vendor dependency. OPTSTK rows carry UNDERLYING_SYMBOL + LOT_SIZE +
#   SM_EXPIRY_DATE, which is the whole F&O stock universe in one fetch.
#
# CONVENTION (locked 2026-08-28, Anbu)
#   Backtests use the CURRENT lot for the WHOLE window. Historical lot revisions
#   are NOT modelled — same convention NIFTY has always run under. The lot is a
#   position-sizing / margin decision, not a market fact being simulated, so the
#   run answers "trade this strategy at today's contract size across history".
#   Every run stamps lot_source into the diag so a result can always be traced
#   back to the exact snapshot that produced it.
#
#   NOTE this convention says NOTHING about price continuity. A stock that had a
#   split/bonus inside the window still has a discontinuous corpus and that is a
#   DATA problem, not a lot problem. See corpus_gap_scan() below.
#
# "NOW IN MARKET" = NEAREST-EXPIRY LOT
#   During a lot revision NSE lets live contracts keep the old lot and issues new
#   far months at the new one, so the master legitimately holds two lots for the
#   same symbol. The tradeable-today number is the NEAREST expiry's lot; that is
#   what we return. When a farther expiry disagrees we record it as `pending` so
#   the UI/diag can warn instead of silently flipping under you one morning.
#   (The pre-existing resolve_stock_ids() takes whichever OPTSTK row it sees
#   first — arbitrary CSV order — which is the latent bug this replaces.)
#
# CACHE
#   ~/.scalp-app/backtest/lot_sizes.json, refreshed lazily when older than
#   MAX_AGE_DAYS. A backtest never blocks on the network for more than the
#   timeout, and a stale cache is always preferred over an abort.
#
# INDEXES ARE DELIBERATELY NOT WIRED
#   OPTIDX lots are recorded in the cache for eyeballing but resolve_lot() still
#   returns the caller's index constant for NIFTY/BANKNIFTY. Every sealed
#   strategy (SCALP V1/V5, PST Sell, BB ...) has results locked against
#   LOT_SIZE = 65. Sourcing that from a live CSV would silently restate every
#   sealed backtest the next time NSE moves it. Not worth it; not doing it.

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"

MAX_AGE_DAYS = 7          # refresh the cache when older than this
FETCH_TIMEOUT = 25        # seconds; a backtest must never hang on this
CACHE_NAME = "lot_sizes.json"


# ── paths ──────────────────────────────────────────────────────────────────

def default_cache_path() -> Path:
    try:
        from app.utils.app_paths import APP_HOME
        base = Path(APP_HOME) / "backtest"
    except Exception:                                   # standalone harness
        base = Path(os.path.expanduser("~/.scalp-app/backtest"))
    return base / CACHE_NAME


# ── master parsing ─────────────────────────────────────────────────────────

def _parse_expiry(s: str) -> Optional[date]:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    return None


def parse_master_lots(master_text: str, *, today: Optional[date] = None) -> Dict:
    """Build {SYMBOL: lot} for every F&O stock in the master.

    Returns the cache payload shape (without fetched_at). For each underlying we
    keep the lot of the NEAREST expiry >= today (falling back to the nearest
    expiry at all, so an all-expired master still yields something), plus a
    `pending` entry when a farther expiry carries a different lot.
    """
    today = today or date.today()
    # sym -> list[(expiry, lot)]
    stock: Dict[str, list] = {}
    index: Dict[str, list] = {}
    rdr = csv.DictReader(io.StringIO(master_text))
    for row in rdr:
        try:
            if (row.get("EXCH_ID") or "").strip() != "NSE":
                continue
            instr = (row.get("INSTRUMENT") or "").strip()
            if instr not in ("OPTSTK", "OPTIDX"):
                continue
            sym = (row.get("UNDERLYING_SYMBOL") or "").strip().upper()
            if not sym:
                continue
            lot = int(float(row.get("LOT_SIZE") or 0))
            if lot <= 0:
                continue
            exp = _parse_expiry(row.get("SM_EXPIRY_DATE") or "")
            if exp is None:
                continue
            (stock if instr == "OPTSTK" else index).setdefault(sym, []).append((exp, lot))
        except Exception:
            continue

    def _pick(rows: list) -> Tuple[int, str, Optional[Dict]]:
        rows = sorted(set(rows))
        live = [r for r in rows if r[0] >= today] or rows
        exp0, lot0 = live[0]
        pending = None
        for exp, lot in live[1:]:
            if lot != lot0:
                pending = {"lot": lot, "from_expiry": exp.isoformat()}
                break
        return lot0, exp0.isoformat(), pending

    lots: Dict[str, int] = {}
    detail: Dict[str, Dict] = {}
    for sym, rows in stock.items():
        lot, exp, pending = _pick(rows)
        lots[sym] = lot
        d = {"lot": lot, "near_expiry": exp}
        if pending:
            d["pending"] = pending
        detail[sym] = d

    index_lots: Dict[str, int] = {}
    for sym, rows in index.items():
        index_lots[sym] = _pick(rows)[0]

    return {"lots": lots, "detail": detail, "index_lots": index_lots,
            "count": len(lots)}


def download_master_text(timeout: int = FETCH_TIMEOUT) -> str:
    import requests
    r = requests.get(_MASTER_URL, timeout=timeout)
    r.raise_for_status()
    return r.text


# ── cache ──────────────────────────────────────────────────────────────────

def load_cache(cache_path: Optional[Path] = None) -> Optional[Dict]:
    p = Path(cache_path or default_cache_path())
    try:
        with open(p, "r") as f:
            c = json.load(f)
        if isinstance(c.get("lots"), dict) and c["lots"]:
            return c
    except Exception:
        pass
    return None


def cache_age_days(cache: Dict) -> float:
    try:
        return max(0.0, (time.time() - float(cache["fetched_epoch"])) / 86400.0)
    except Exception:
        return 1e9


def refresh_cache(*, master_text: Optional[str] = None,
                  cache_path: Optional[Path] = None,
                  today: Optional[date] = None) -> Dict:
    """Fetch (or accept) the master, parse it, write the cache atomically."""
    text = master_text if master_text is not None else download_master_text()
    payload = parse_master_lots(text, today=today)
    now = time.time()
    payload.update({
        "source": "dhan-scrip-master-detailed",
        "fetched_epoch": now,
        "fetched_at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
    })
    p = Path(cache_path or default_cache_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=1, sort_keys=True)
    os.replace(tmp, p)                       # atomic; never a half-written cache
    return payload


def get_cache(*, allow_network: bool = True,
              max_age_days: float = MAX_AGE_DAYS,
              cache_path: Optional[Path] = None) -> Tuple[Optional[Dict], bool]:
    """(cache, is_fresh). Never raises — a dead network yields the stale cache."""
    cache = load_cache(cache_path)
    fresh = cache is not None and cache_age_days(cache) <= max_age_days
    if fresh or not allow_network:
        return cache, bool(fresh)
    try:
        return refresh_cache(cache_path=cache_path), True
    except Exception:
        return cache, False                  # stale beats nothing


# ── corpus meta (offline fallback, stamped at backfill time) ────────────────

_CORPUS_META_DDL = """
CREATE TABLE IF NOT EXISTS corpus_meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def ensure_corpus_meta(conn: sqlite3.Connection) -> None:
    conn.executescript(_CORPUS_META_DDL)
    conn.commit()


def write_corpus_meta(db_path: str, **kv) -> None:
    """Stamp key/values onto a corpus DB. Best-effort: never breaks a backfill."""
    try:
        conn = sqlite3.connect(db_path)
        try:
            ensure_corpus_meta(conn)
            now = datetime.now().isoformat(timespec="seconds")
            conn.executemany(
                "INSERT INTO corpus_meta(key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                [(str(k), str(v), now) for k, v in kv.items() if v is not None])
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def read_corpus_meta(db_path: str, key: str) -> Optional[str]:
    try:
        if not Path(db_path).exists():
            return None
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM corpus_meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


# ── the ladder ─────────────────────────────────────────────────────────────

def resolve_lot(*, underlying: str, is_stock: bool, cfg_lot: int,
                index_lot: int, db_path: Optional[str] = None,
                static_map: Optional[Dict[str, int]] = None,
                allow_network: bool = True,
                cache_path: Optional[Path] = None) -> Tuple[Optional[int], str]:
    """(lot, source). lot is None only when nothing on earth knew the answer.

    Order: explicit config > index constant > live/fresh scrip master >
    corpus meta > stale scrip master > legacy static map > unresolved.
    """
    u = (underlying or "").upper().strip()
    if cfg_lot and cfg_lot > 0:
        return int(cfg_lot), "config"
    if not is_stock:
        return int(index_lot), "index-const"

    cache, fresh = get_cache(allow_network=allow_network, cache_path=cache_path)
    if cache and fresh and u in cache["lots"]:
        return int(cache["lots"][u]), f"scrip-master@{cache.get('fetched_at', '?')}"

    if db_path:
        v = read_corpus_meta(db_path, "lot_size")
        try:
            if v and int(v) > 0:
                asof = read_corpus_meta(db_path, "lot_size_asof") or "?"
                return int(v), f"corpus-meta@{asof}"
        except Exception:
            pass

    if cache and u in cache["lots"]:
        return int(cache["lots"][u]), \
            f"scrip-master-STALE@{cache.get('fetched_at', '?')}"

    if static_map and u in static_map:
        return int(static_map[u]), "static-map"

    return None, "unresolved"


def unresolved_reason(underlying: str) -> str:
    return (f"{underlying}: lot size could not be resolved — the scrip-master "
            f"cache is missing and the master could not be fetched (check the "
            f"network), and this corpus carries no lot stamp. Either connect "
            f"and re-run, or set LOT SIZE explicitly in the run params. "
            f"Refresh manually: python3 -m app.backtest.util.lot_sizes --refresh")


def pending_revision(underlying: str,
                     cache_path: Optional[Path] = None) -> Optional[Dict]:
    """{'lot': n, 'from_expiry': iso} when a farther expiry disagrees."""
    cache = load_cache(cache_path)
    if not cache:
        return None
    return (cache.get("detail", {}).get((underlying or "").upper().strip())
            or {}).get("pending")


# ── corporate-action tripwire ──────────────────────────────────────────────

def corpus_gap_scan(db_path: str, underlying: str,
                    *, threshold_pct: float = 25.0) -> list:
    """Day-over-day SPOT close jumps beyond threshold — split/bonus detector.

    A lot size is a sizing choice; a 2:1 price cut inside the corpus is a
    correctness bug, and it looks exactly like a tradeable overnight gap to any
    strategy that carries positions. Cheap to check, so check.
    Returns [{'date','prev_close','close','pct'}, ...] — empty is clean.
    """
    out: list = []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except Exception:
        return out
    try:
        rows = conn.execute(
            "SELECT date(ts,'unixepoch','+330 minutes') d, close, ts "
            "FROM backtest_candles_1m "
            "WHERE underlying=? AND instrument_type='SPOT' ORDER BY ts",
            (underlying.upper(),)).fetchall()
    except Exception:
        return out
    finally:
        conn.close()
    last_by_day: Dict[str, float] = {}
    for d, c, _ in rows:
        last_by_day[d] = float(c)
    prev_d = prev_c = None
    for d in sorted(last_by_day):
        c = last_by_day[d]
        if prev_c and prev_c > 0:
            pct = 100.0 * (c - prev_c) / prev_c
            if abs(pct) >= threshold_pct:
                out.append({"date": d, "prev_date": prev_d,
                            "prev_close": prev_c, "close": c,
                            "pct": round(pct, 2)})
        prev_d, prev_c = d, c
    return out


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="stock lot-size cache")
    ap.add_argument("--refresh", action="store_true", help="force a master fetch")
    ap.add_argument("--show", metavar="SYMBOL", help="print one symbol's lot")
    ap.add_argument("--gap-scan", metavar="SYMBOL",
                    help="scan corpus/<SYMBOL>.db spot for split/bonus jumps")
    a = ap.parse_args()
    if a.refresh:
        c = refresh_cache()
        print(f"cached {c['count']} F&O stocks + {len(c['index_lots'])} indexes "
              f"at {c['fetched_at']} -> {default_cache_path()}")
    if a.show:
        c = load_cache() or {}
        s = a.show.upper()
        print(json.dumps(c.get("detail", {}).get(s, {"error": "not found"}),
                         indent=1))
    if a.gap_scan:
        p = default_cache_path().parent / "corpus" / f"{a.gap_scan.upper()}.db"
        hits = corpus_gap_scan(str(p), a.gap_scan)
        print(json.dumps(hits, indent=1) if hits else "clean — no jumps >= 25%")