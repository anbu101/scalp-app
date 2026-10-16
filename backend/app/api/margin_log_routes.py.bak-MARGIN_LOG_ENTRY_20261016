# backend/app/api/margin_log_routes.py
#
# ── MARGIN_LOG_20261016 ── how much money does each strategy REALLY need?
#
# Every 5 minutes of the NFO session (09:20–15:25 IST, holiday-aware) this
# samples Zerodha's basket margin for every strategy's OPEN legs — paper and
# live books separately — and stores one row per (sample, strategy, book) in
# app.db `margin_samples`. After a few days the summary answers "peak /
# typical margin, and per lot" from real positions instead of a one-off
# estimate.
#
# Read-only against every engine: legs come from fleet_today_routes._legs_for
# (the SAME normaliser the dashboard and Trades page use), the margin from
# kite.basket_order_margins(consider_positions=False) — what that basket
# alone would block, BUY legs first (live sequencing → spread benefit),
# product NRML (carry strategies hold overnight). Places no orders.
#
# Per-lot unit: `base_lots` = lots of the largest SELL leg (the leg that
# drives SPAN); a buy-only strategy uses its largest leg. per_lot = total ÷
# base_lots. legs_json keeps every leg so any other normalisation can be
# recomputed from the export.
#
# Rows from one sampler pass share `ts`, so fleet-wide CONCURRENT margin per
# book (what the account must hold at once) is Σ over a ts.
#
# Never raises out of the job; a strategy whose margin call fails is stored
# with `error` set (so gaps are visible), and the pass continues.

from __future__ import annotations

import json
import sqlite3
import statistics
import threading
import time
from typing import Dict, List, Optional

from fastapi import APIRouter

router = APIRouter(tags=["margin-log"])

FENCE = "MARGIN_LOG_20261016"
IST_OFF = 5 * 3600 + 30 * 60
SAMPLE_FROM_MIN = 9 * 60 + 20      # skip the 09:15–09:19 entry/quote burst
SAMPLE_TO_MIN = 15 * 60 + 25       # last full sample before the 15:26 exits
KEEP_DAYS = 180
DEFAULT_LOT = 65
CALL_GAP_S = 0.35                   # between margin calls (Kite rate limits)
_lock = threading.Lock()


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


def _in_window(ts: int) -> bool:
    return SAMPLE_FROM_MIN <= _ist_min(ts) <= SAMPLE_TO_MIN


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
           "error": None}
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

def sample_now(now: Optional[int] = None, force: bool = False, kite=None) -> dict:
    """One pass over the fleet. Gated on the NFO session + 09:20–15:25 IST
    unless force=True. Never raises."""
    now = int(now or time.time())
    try:
        if not force and (not _market_open() or not _in_window(now)):
            return {"sampled": False, "reason": "outside sampling window"}
        if not _lock.acquire(blocking=False):
            return {"sampled": False, "reason": "a sample is already running"}
        try:
            from app.api import fleet_today_routes as F
            warns: list = []
            groups = []                                   # (sid, book, mode, open legs)
            for sid in F.FLEET_IDS:
                if not F._allowed(sid):
                    continue
                try:
                    legs = F._legs_for(sid, warns)
                except Exception as e:
                    warns.append(f"{sid}: {e!r}")
                    continue
                mode = F._mode(sid)
                for book in F.BOOKS:
                    mine = [l for l in legs if l.get("open") and l.get("book") == book]
                    if mine:
                        groups.append((sid, book, mode, mine))
            if not groups:
                return {"sampled": True, "rows": 0, "reason": "no open positions", "warnings": warns}
            if kite is None:
                kite = _kite()
            rows = []
            for i, (sid, book, mode, mine) in enumerate(groups):
                if kite is None:
                    rec = measure(_NoKite(), mine)
                else:
                    if i:
                        time.sleep(CALL_GAP_S)
                    rec = measure(kite, mine)
                rows.append((now, _day(now), _hhmm(now), sid, book, mode, rec["n_legs"], rec["base_lots"],
                             rec["total"], rec["span"], rec["exposure"], rec["premium"], rec["per_lot"],
                             rec["legs_json"], rec["error"]))
            with sqlite3.connect(_db_path(), timeout=30) as conn:
                _ensure_table(conn)
                conn.executemany("INSERT INTO margin_samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                conn.execute("DELETE FROM margin_samples WHERE ts < ?", (now - KEEP_DAYS * 86400,))
            return {"sampled": True, "rows": len(rows),
                    "errors": sum(1 for r in rows if r[-1]), "warnings": warns}
        finally:
            _lock.release()
    except Exception as e:
        return {"sampled": False, "reason": repr(e)}


class _NoKite:
    def basket_order_margins(self, *a, **k):
        raise RuntimeError("Zerodha session not available — log in")


def margin_log_sample_job() -> None:
    """APScheduler entry point (cron */5, 09–15 Mon–Fri IST; the session and
    09:20–15:25 gate live in sample_now). Never raises."""
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


def summarise(rows: List[dict]) -> dict:
    """Per (strategy, book) stats + fleet-concurrent per book. Pure."""
    by: Dict[tuple, List[dict]] = {}
    for r in rows:
        by.setdefault((r["strategy_id"], r["book"]), []).append(r)
    out = []
    for (sid, book), rs in sorted(by.items()):
        ok = [r for r in rs if r.get("total") is not None]
        tot = [float(r["total"]) for r in ok]
        pl = [float(r["per_lot"]) for r in ok if r.get("per_lot") is not None]
        lots = [r["base_lots"] for r in ok if r.get("base_lots")]
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
            "last_ts": max(r["ts"] for r in rs),
            "last_error": next((r["error"] for r in sorted(rs, key=lambda r: -r["ts"]) if r.get("error")), None),
        })
    fleet = {}
    for book in ("LIVE", "PAPER"):
        per_ts: Dict[int, float] = {}
        for r in rows:
            if r["book"] == book and r.get("total") is not None:
                per_ts[r["ts"]] = per_ts.get(r["ts"], 0.0) + float(r["total"])
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
                "window": "09:20–15:25 IST, every 5 min", "keep_days": KEEP_DAYS}
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
    """Manual pass (works outside hours — margins use current prices)."""
    return sample_now(force=True)
