# backend/app/api/margin_log_routes.py
#
# ── MARGIN_LOG_20261016 ── how much money does each strategy REALLY need?
# ── MARGIN_LOG_ENTRY_20261016 ── plus: the margin AT ENTRY.
#
# The job runs every minute of the NFO session (holiday-aware). Each minute it
# reads every strategy's OPEN legs per book (PAPER / LIVE) from
# fleet_today_routes._legs_for — the dashboard's own reader; a DB read, no
# Kite call — and compares them with what was last recorded:
#
#   ENTRY   the strategy-book was flat and now holds legs   → margin call
#   CHANGE  its legs changed (adjustment, roll, one leg out) → margin call
#   CLOSED  it went flat                                     → zero row, NO call
#   TIMER   every 5th minute 09:20–15:25: every open book    → margin call
#   MANUAL  "Sample now" (any time)                          → margin call
#
# Change samples start at 09:17 IST — one minute after the 09:16 entries, so a
# recording tool never adds Kite calls inside the open's order burst (the
# 8–11 Sep rate-limit scars) — and run until 15:29 to see the 15:26 exits. At
# most MAX_CHANGE_CALLS change samples per minute, CALL_GAP_S apart; the rest
# wait for the next minute (their recorded legs are only updated once a row is
# written, so nothing is lost, just delayed).
#
# Margin = kite.basket_order_margins(consider_positions=False) — what that
# basket alone blocks — BUY legs first (live sequencing → spread benefit),
# product NRML. Places no orders; touches no engine.
#
# Per-lot unit: `base_lots` = lots of the largest SELL leg (the leg that
# drives SPAN); a buy-only strategy uses its largest leg. legs_json keeps
# every leg so other normalisations can be recomputed from the export.
#
# Fleet "at once" per book: within a day, each strategy's latest row is
# carried forward (a CLOSED row zeroes it), and the books are summed at every
# sample time — so a change sample of one strategy no longer under-counts the
# others, and the 09:17–09:19 entry minutes are part of the peak.
#
# Never raises out of the job; a failed margin call is stored with `error`.

from __future__ import annotations

import json
import sqlite3
import statistics
import threading
import time
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter

router = APIRouter(tags=["margin-log"])

FENCE = "MARGIN_LOG_20261016"
FENCE_ENTRY = "MARGIN_LOG_ENTRY_20261016"
IST_OFF = 5 * 3600 + 30 * 60
CHANGE_FROM_MIN = 9 * 60 + 17      # first change sample: a minute after the 09:16 entries
CHANGE_TO_MIN = 15 * 60 + 29       # see the 15:26 exits
TIMER_FROM_MIN = 9 * 60 + 20       # regular samples, every 5th minute
TIMER_TO_MIN = 15 * 60 + 25
TIMER_EVERY_MIN = 5
MAX_CHANGE_CALLS = 6                # change-triggered margin calls per minute
KEEP_DAYS = 180
DEFAULT_LOT = 65
CALL_GAP_S = 0.35                   # between margin calls (Kite rate limits)
SEED_LOOKBACK_S = 5 * 86400         # recorded-legs memory is seeded from the DB on boot
_lock = threading.Lock()
_last: Dict[Tuple[str, str], tuple] = {}     # (sid, book) → recorded leg signature (() = flat)
_seeded = [False]


# ───────────────────────────── plumbing ──────────────────────────────────

def _db_path() -> str:
    from app.api import fleet_today_routes as F
    return F._db_path()


def _ensure_table(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS margin_samples ("
        " ts INTEGER NOT NULL, day TEXT NOT NULL, hhmm TEXT NOT NULL,"
        " strategy_id TEXT NOT NULL, book TEXT NOT NULL, mode TEXT,"
        " n_legs INTEGER, base_lots REAL, total REAL, span REAL, exposure REAL,"
        " premium REAL, per_lot REAL, legs_json TEXT, error TEXT)")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(margin_samples)")}
    if "trigger" not in cols:                                     # ── MARGIN_LOG_ENTRY_20261016 ──
        conn.execute("ALTER TABLE margin_samples ADD COLUMN trigger TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_margin_samples_sid_day"
                 " ON margin_samples(strategy_id, day)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_margin_samples_ts ON margin_samples(ts)")


def _ist_min(ts: int) -> int:
    return ((int(ts) + IST_OFF) % 86400) // 60


def _day(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts) + IST_OFF))


def _hhmm(ts: int) -> str:
    m = _ist_min(ts)
    return f"{m // 60:02d}:{m % 60:02d}"


def _in_change_window(ts: int) -> bool:
    return CHANGE_FROM_MIN <= _ist_min(ts) <= CHANGE_TO_MIN


def _is_timer_minute(ts: int) -> bool:
    m = _ist_min(ts)
    return TIMER_FROM_MIN <= m <= TIMER_TO_MIN and m % TIMER_EVERY_MIN == 0


def _in_window(ts: int) -> bool:          # kept for callers of the first version
    return _is_timer_minute(ts) or _in_change_window(ts)


def _market_open() -> bool:
    from app.api import fleet_today_routes as F
    return F._market_open()


def _kite():
    from kiteconnect import KiteConnect
    from app.brokers.zerodha_manager import load_access_token, load_credentials
    api_key = (load_credentials() or {}).get("api_key")
    token = load_access_token()
    if not api_key or not token:
        return None
    k = KiteConnect(api_key=api_key)
    k.set_access_token(token)
    return k


_lot_cache: Dict[str, int] = {}


def _lot_size(symbol: str) -> int:
    if symbol in _lot_cache:
        return _lot_cache[symbol]
    lot = DEFAULT_LOT
    try:
        from app.fetcher.zerodha_instruments import load_instruments_df
        df = load_instruments_df()
        hit = df[df["tradingsymbol"] == symbol]
        if len(hit):
            lot = int(hit["lot_size"].iloc[0]) or DEFAULT_LOT
    except Exception:
        pass
    _lot_cache[symbol] = lot
    return lot


def _exchange(symbol: str) -> str:
    s = symbol.upper()
    return "BFO" if s.startswith(("SENSEX", "BANKEX")) else "NFO"


# ───────────────────────────── one basket ────────────────────────────────

def basket_for(legs: List[dict]) -> List[dict]:
    """Open normalised legs → Kite basket, BUY legs first. Pure."""
    out = []
    for l in legs:
        sym, qty = str(l.get("symbol") or ""), int(l.get("qty") or 0)
        if not sym or qty <= 0:
            continue
        out.append({"exchange": _exchange(sym), "tradingsymbol": sym,
                    "transaction_type": "SELL" if l.get("short") else "BUY",
                    "variety": "regular", "product": "NRML", "order_type": "MARKET",
                    "quantity": qty, "price": 0, "trigger_price": 0})
    out.sort(key=lambda o: 0 if o["transaction_type"] == "BUY" else 1)
    return out


def signature(basket: List[dict]) -> tuple:
    """Order-free identity of a basket: what counts as 'the legs changed'. Pure."""
    return tuple(sorted((o["tradingsymbol"], o["transaction_type"][0], int(o["quantity"])) for o in basket))


def _sig_from_json(legs_json: Optional[str]) -> tuple:
    try:
        return tuple(sorted((d["s"], d["t"], int(d["q"])) for d in json.loads(legs_json or "[]")))
    except Exception:
        return ()


def base_lots(basket: List[dict]) -> float:
    """Lots of the largest SELL leg; buy-only → largest leg. Pure."""
    sells = [o for o in basket if o["transaction_type"] == "SELL"]
    pool = sells or basket
    if not pool:
        return 0.0
    return max(o["quantity"] / float(_lot_size(o["tradingsymbol"])) for o in pool)


def _f(d: dict, *keys) -> Optional[float]:
    for k in keys:
        v = (d or {}).get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def measure(kite, legs: List[dict]) -> dict:
    """Margin for one strategy-book's open legs. Never raises."""
    basket = basket_for(legs)
    rec = {"n_legs": len(basket), "base_lots": round(base_lots(basket), 3),
           "legs_json": json.dumps([{"s": o["tradingsymbol"], "t": o["transaction_type"][0],
                                     "q": o["quantity"]} for o in basket], separators=(",", ":")),
           "total": None, "span": None, "exposure": None, "premium": None, "per_lot": None,
           "error": None, "sig": signature(basket)}
    if not basket:
        rec["error"] = "no priceable legs"
        return rec
    try:
        resp = kite.basket_order_margins(basket, consider_positions=False)
        fin = (resp or {}).get("final") or {}
        tot = _f(fin, "total")
        if tot is None:
            raise ValueError("no final.total in margin response")
        rec.update(total=round(tot, 2), span=_f(fin, "span"), exposure=_f(fin, "exposure"),
                   premium=_f(fin, "option_premium"))
        if rec["base_lots"]:
            rec["per_lot"] = round(tot / rec["base_lots"], 2)
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"[:300]
    return rec


# ───────────────────────────── the sampler ───────────────────────────────

def _seed(now: int) -> None:
    """Recorded-legs memory survives a restart: each (strategy, book)'s latest
    row in the last few days (a CLOSED / 0-leg row = flat)."""
    if _seeded[0]:
        return
    _seeded[0] = True
    try:
        with sqlite3.connect(_db_path(), timeout=30) as conn:
            _ensure_table(conn)
            latest: Dict[Tuple[str, str], tuple] = {}
            for sid, book, legs_json, n in conn.execute(
                    "SELECT strategy_id, book, legs_json, n_legs FROM margin_samples"
                    " WHERE ts >= ? ORDER BY ts", (now - SEED_LOOKBACK_S,)):
                latest[(sid, book)] = _sig_from_json(legs_json) if n else ()
            for key, sig in latest.items():
                _last.setdefault(key, sig)
    except Exception:
        pass


def _row(now: int, sid: str, book: str, mode: str, rec: dict, trigger: str) -> tuple:
    return (now, _day(now), _hhmm(now), sid, book, mode, rec["n_legs"], rec["base_lots"],
            rec["total"], rec["span"], rec["exposure"], rec["premium"], rec["per_lot"],
            rec["legs_json"], rec["error"], trigger)


def sample_now(now: Optional[int] = None, force: bool = False, kite=None) -> dict:
    """One minute's pass. force=True = MANUAL full pass at any time.
    Otherwise: NFO session only; ENTRY/CHANGE/CLOSED from 09:17 to 15:29,
    TIMER every 5th minute 09:20–15:25. Never raises."""
    now = int(now or time.time())
    try:
        timer = force or _is_timer_minute(now)
        if not force and (not _market_open() or not _in_change_window(now)):
            return {"sampled": False, "reason": "outside sampling window"}
        if not _lock.acquire(blocking=False):
            return {"sampled": False, "reason": "a sample is already running"}
        try:
            from app.api import fleet_today_routes as F
            _seed(now)
            warns: list = []
            groups: Dict[Tuple[str, str], tuple] = {}     # (sid, book) → (mode, legs, basket sig)
            unreadable = set()
            for sid in F.FLEET_IDS:
                if not F._allowed(sid):
                    continue
                try:
                    legs = F._legs_for(sid, warns)
                except Exception as e:
                    warns.append(f"{sid}: {e!r}")
                    unreadable.add(sid)
                    continue
                mode = F._mode(sid)
                for book in F.BOOKS:
                    mine = [l for l in legs if l.get("open") and l.get("book") == book]
                    if mine:
                        groups[(sid, book)] = (mode, mine, signature(basket_for(mine)))
            rows: List[tuple] = []
            # CLOSED: was holding, now flat (never for a strategy we could not read)
            for key, sig in list(_last.items()):
                if sig and key not in groups and key[0] not in unreadable and (force or _in_change_window(now)):
                    rows.append((now, _day(now), _hhmm(now), key[0], key[1], None, 0, 0, 0.0, None, None,
                                 None, None, "[]", None, "CLOSED"))
                    _last[key] = ()
            # what needs a margin call this minute
            todo: List[Tuple[Tuple[str, str], str]] = []
            changed = []
            for key, (mode, mine, sig) in groups.items():
                prev = _last.get(key)
                if prev != sig:
                    changed.append((key, "ENTRY" if not prev else "CHANGE"))
            if timer:
                ch = dict(changed)
                todo = [(k, ch.get(k, "MANUAL" if force else "TIMER")) for k in groups]
            else:
                changed.sort(key=lambda kv: 0 if kv[1] == "ENTRY" else 1)   # entries first
                todo = changed[:MAX_CHANGE_CALLS]
            deferred = max(0, len(changed) - len(todo)) if not timer else 0
            if todo:
                if kite is None:
                    kite = _kite() or _NoKite()
                for i, (key, trig) in enumerate(todo):
                    mode, mine, sig = groups[key]
                    if i:
                        time.sleep(CALL_GAP_S)
                    rec = measure(kite, mine)
                    rows.append(_row(now, key[0], key[1], mode, rec, trig))
                    _last[key] = sig          # recorded (an error row too — no hammering)
            if rows:
                with sqlite3.connect(_db_path(), timeout=30) as conn:
                    _ensure_table(conn)
                    conn.executemany("INSERT INTO margin_samples (ts, day, hhmm, strategy_id, book, mode,"
                                     " n_legs, base_lots, total, span, exposure, premium, per_lot, legs_json,"
                                     " error, trigger) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                    conn.execute("DELETE FROM margin_samples WHERE ts < ?", (now - KEEP_DAYS * 86400,))
            calls = [r for r in rows if r[-1] != "CLOSED"]
            return {"sampled": True, "rows": len(rows), "calls": len(calls),
                    "errors": sum(1 for r in calls if r[14]), "deferred": deferred,
                    "triggers": sorted({r[-1] for r in rows}),
                    "reason": None if rows else ("no open positions" if not groups else "no change"),
                    "warnings": warns}
        finally:
            _lock.release()
    except Exception as e:
        return {"sampled": False, "reason": repr(e)}


class _NoKite:
    def basket_order_margins(self, *a, **k):
        raise RuntimeError("Zerodha session not available — log in")


def margin_log_sample_job() -> None:
    """APScheduler entry point (cron every minute, 09–15 Mon–Fri IST; the
    session / 09:17–15:29 / timer logic lives in sample_now). Never raises."""
    try:
        r = sample_now()
        if not r.get("sampled") and r.get("reason") not in (None, "outside sampling window",
                                                             "a sample is already running"):
            from app.event_bus.audit_logger import write_audit_log
            write_audit_log(f"[MARGIN_LOG][WARN] {r.get('reason')}")
    except Exception:
        pass


# ───────────────────────────── reading ───────────────────────────────────

def _rows(days: int, strategy_id: Optional[str] = None, book: Optional[str] = None,
          now: Optional[int] = None) -> List[dict]:
    now = int(now or time.time())
    q = "SELECT * FROM margin_samples WHERE ts >= ?"
    args: list = [now - int(days) * 86400]
    if strategy_id:
        q += " AND strategy_id = ?"; args.append(strategy_id)
    if book in ("LIVE", "PAPER"):
        q += " AND book = ?"; args.append(book)
    q += " ORDER BY ts, strategy_id, book"
    with sqlite3.connect(_db_path(), timeout=30) as conn:
        _ensure_table(conn)
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(q, args)]


def _pct(xs: List[float], p: float) -> Optional[float]:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 2)


def fleet_concurrent(rows: List[dict], book: str) -> Dict[int, float]:
    """Σ per sample time of each strategy's LATEST margin that day (carried
    forward; a CLOSED row zeroes it; an error row keeps the last good value).
    Pure."""
    out: Dict[int, float] = {}
    cur: Dict[str, float] = {}
    day = None
    for r in sorted((r for r in rows if r["book"] == book), key=lambda r: (r["ts"], r["strategy_id"])):
        if r["day"] != day:
            day, cur = r["day"], {}
        if r.get("trigger") == "CLOSED" or not r.get("n_legs"):
            cur[r["strategy_id"]] = 0.0
        elif r.get("total") is not None:
            cur[r["strategy_id"]] = float(r["total"])
        out[r["ts"]] = round(sum(cur.values()), 2)
    return out


def summarise(rows: List[dict]) -> dict:
    """Per (strategy, book) stats + fleet-concurrent per book. Pure."""
    by: Dict[tuple, List[dict]] = {}
    for r in rows:
        if r.get("trigger") == "CLOSED" or not r.get("n_legs"):
            continue                                   # a flat marker, not a margin sample
        by.setdefault((r["strategy_id"], r["book"]), []).append(r)
    out = []
    for (sid, book), rs in sorted(by.items()):
        ok = [r for r in rs if r.get("total") is not None]
        tot = [float(r["total"]) for r in ok]
        pl = [float(r["per_lot"]) for r in ok if r.get("per_lot") is not None]
        lots = [r["base_lots"] for r in ok if r.get("base_lots")]
        ent = [float(r["total"]) for r in ok if r.get("trigger") == "ENTRY"]
        ent_pl = [float(r["per_lot"]) for r in ok if r.get("trigger") == "ENTRY" and r.get("per_lot") is not None]
        peak = max(ok, key=lambda r: r["total"]) if ok else None
        out.append({
            "strategy_id": sid, "book": book, "samples": len(ok), "errors": len(rs) - len(ok),
            "days": len({r["day"] for r in ok}),
            "lots_typical": (statistics.mode(lots) if lots else None),
            "lots_max": (max(lots) if lots else None),
            "median": _pct(tot, 0.5), "p90": _pct(tot, 0.9), "peak": (round(max(tot), 2) if tot else None),
            "peak_at": (f"{peak['day']} {peak['hhmm']}" if peak else None),
            "peak_lots": (peak["base_lots"] if peak else None),
            "per_lot_median": _pct(pl, 0.5), "per_lot_p90": _pct(pl, 0.9),
            "per_lot_peak": (round(max(pl), 2) if pl else None),
            "entries": len(ent), "entry_median": _pct(ent, 0.5),
            "entry_peak": (round(max(ent), 2) if ent else None),
            "entry_per_lot_median": _pct(ent_pl, 0.5),
            "last_ts": max(r["ts"] for r in rs),
            "last_error": next((r["error"] for r in sorted(rs, key=lambda r: -r["ts"]) if r.get("error")), None),
        })
    fleet = {}
    for book in ("LIVE", "PAPER"):
        per_ts = {t: v for t, v in fleet_concurrent(rows, book).items() if v > 0}
        if per_ts:
            t_peak = max(per_ts, key=per_ts.get)
            vals = list(per_ts.values())
            fleet[book] = {"samples": len(per_ts), "median": _pct(vals, 0.5), "p90": _pct(vals, 0.9),
                           "peak": round(per_ts[t_peak], 2), "peak_at": f"{_day(t_peak)} {_hhmm(t_peak)}"}
    return {"strategies": out, "fleet": fleet}


@router.get("/api/margin-log/summary")
def get_summary(days: int = 14, book: str = "ALL"):
    try:
        rows = _rows(max(1, min(int(days), KEEP_DAYS)), book=book.upper())
        s = summarise(rows)
        first = min((r["ts"] for r in rows), default=None)
        return {"ok": True, "days": days, "first_ts": first, "rows": len(rows), **s,
                "window": "on every entry / leg change from 09:17, and every 5 min 09:20–15:25 IST",
                "keep_days": KEEP_DAYS}
    except Exception as e:
        return {"ok": False, "error": repr(e), "strategies": [], "fleet": {}}


@router.get("/api/margin-log/samples")
def get_samples(days: int = 14, strategy_id: Optional[str] = None, book: str = "ALL", limit: int = 20000):
    try:
        rows = _rows(max(1, min(int(days), KEEP_DAYS)), strategy_id=strategy_id, book=book.upper())
        return {"ok": True, "rows": rows[-max(1, min(int(limit), 50000)):]}
    except Exception as e:
        return {"ok": False, "error": repr(e), "rows": []}


@router.post("/api/margin-log/sample-now")
def post_sample_now():
    """Manual full pass (works outside hours — margins use current prices)."""
    return sample_now(force=True)
