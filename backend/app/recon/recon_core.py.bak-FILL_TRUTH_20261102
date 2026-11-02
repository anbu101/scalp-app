# backend/app/recon/recon_core.py
#
# ── BROKER_RECON_20261029 ── does the broker hold what the app thinks it holds?
#
# Phase 1 (decided by Anbu 2026-10-29): ALERT-ONLY, Zerodha only, a 45-second
# poll that stays clear of the 09:15–09:18 open burst; manual trades in the
# same account are marked "ignore". Admin licence only for now.
#
# EVERY POLL (09:10–15:40 on trading days; 09:10–09:14 is the carried-position
# check before any strategy acts):
#   app    = every licensed strategy bound to ZERODHA → its OPEN LIVE legs from
#            fleet_today_routes._legs_for (the dashboard's own reader — a DB
#            read), summed per tradingsymbol, SHORT negative, in units.
#   broker = kite.positions()["net"], NFO/BFO only, quantity summed per
#            tradingsymbol across products (the broker nets per instrument).
#   expected broker qty = app qty + ignored manual qty.
#   A symbol with an order in flight, or any order update in the last
#   SETTLE_S, is "settling" and judged next poll.
#   Any other difference is a mismatch:
#     ORPHAN  broker holds it, no strategy does
#     GHOST   a strategy holds it, the broker is flat
#     QTY     both hold it, quantities differ (partial fill / partial exit)
#   A mismatch is CONFIRMED when the same (symbol, kind, broker, expected) is
#   seen on two consecutive polls → one alert (bell + your private Telegram).
#   When the symbol matches again → one "resolved" note.
#
# Never places, modifies or cancels an order; never edits a trade row. The
# older SCALP_V1-slot job (engine/broker_reconciliation.py) is untouched.

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

FENCE = "BROKER_RECON_20261029"
IST = timezone(timedelta(hours=5, minutes=30))
POLL_S = 45
WINDOW_FROM, WINDOW_TO = 9 * 60 + 10, 15 * 60 + 40       # IST minutes
BURST_FROM, BURST_TO = 9 * 60 + 15, 9 * 60 + 18          # never call Kite here
SETTLE_S = 20
CONFIRM_POLLS = 2
KEEP_RUNS_DAYS = 5
OPTION_EXCHANGES = ("NFO", "BFO")
IN_FLIGHT = {"OPEN", "OPEN PENDING", "PUT ORDER REQ RECEIVED", "VALIDATION PENDING", "MODIFY VALIDATION PENDING",
             "MODIFY PENDING", "CANCEL PENDING", "AMO REQ RECEIVED", "TRIGGER PENDING_MODIFY"}

_lock = threading.Lock()
_STATE: Dict[str, object] = {"pending": {}, "last": None}


# ─────────────────────────────────────────────────────────────────────
# clock + paths (patched by the suite)
# ─────────────────────────────────────────────────────────────────────
def now_ts() -> int:
    return int(time.time())


def ist(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts), IST)


def mod_of(ts: int) -> int:
    d = ist(ts)
    return d.hour * 60 + d.minute


def db_path() -> Path:
    from app.utils.app_paths import APP_HOME
    return APP_HOME / "recon" / "recon.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS recon_runs (
    ts INTEGER PRIMARY KEY, ok INTEGER NOT NULL, n_app INTEGER, n_broker INTEGER, n_mismatch INTEGER,
    settling TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS recon_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, kind TEXT NOT NULL, broker_qty INTEGER NOT NULL, app_qty INTEGER NOT NULL,
    ignored_qty INTEGER NOT NULL DEFAULT 0, strategies TEXT,
    first_seen INTEGER NOT NULL, confirmed_at INTEGER NOT NULL, resolved_at INTEGER, alerted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_recon_issues_open ON recon_issues (resolved_at, symbol);
CREATE TABLE IF NOT EXISTS recon_ignores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, qty INTEGER NOT NULL, note TEXT, added_at INTEGER NOT NULL, removed_at INTEGER,
    removed_why TEXT
);
"""


def connect() -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), timeout=30)
    c.execute("PRAGMA journal_mode=WAL;")
    c.executescript(_SCHEMA)
    return c


# ─────────────────────────────────────────────────────────────────────
# the two books
# ─────────────────────────────────────────────────────────────────────
def _audit(msg: str) -> None:
    try:
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log(f"[RECON2] {msg}")
    except Exception:
        pass


def zerodha_strategies() -> List[str]:
    from app.api import fleet_today_routes as F
    from app.config.account_bindings import resolve_broker
    out = []
    for sid in F.FLEET_IDS:
        try:
            if F._allowed(sid) and resolve_broker(sid) == "ZERODHA":
                out.append(sid)
        except Exception:
            continue
    return out


def any_live_mode(sids: List[str]) -> bool:
    from app.api import fleet_today_routes as F
    return any(F._mode(s) in ("LIVE", "PAPER_LIVE") for s in sids)


def app_book(sids: List[str]) -> Tuple[Dict[str, int], Dict[str, Dict[str, int]], List[str]]:
    """({symbol: signed qty}, {symbol: {strategy: signed qty}}, unreadable)"""
    from app.api import fleet_today_routes as F
    qty: Dict[str, int] = {}
    who: Dict[str, Dict[str, int]] = {}
    bad: List[str] = []
    for sid in sids:
        warns: list = []
        try:
            legs = F._legs_for(sid, warns)
        except Exception as e:
            bad.append(f"{sid}: {type(e).__name__}")
            continue
        if any("live mapper" in str(w) or "trades:" in str(w) or "paper_trades:" in str(w) for w in warns):
            bad.append(f"{sid}: {warns[0]}"[:160])
            continue
        for l in legs:
            if l.get("book") != "LIVE" or not l.get("open"):
                continue
            sym, q = str(l.get("symbol") or ""), int(l.get("qty") or 0)
            if not sym or not q:
                continue
            s = -q if l.get("short") else q
            qty[sym] = qty.get(sym, 0) + s
            who.setdefault(sym, {})[sid] = who.get(sym, {}).get(sid, 0) + s
    return qty, who, bad


def broker_book(kite) -> Dict[str, int]:
    out: Dict[str, int] = {}
    pos = kite.positions() or {}
    for p in pos.get("net") or []:
        if str(p.get("exchange") or "") not in OPTION_EXCHANGES:
            continue
        sym = str(p.get("tradingsymbol") or "")
        q = int(p.get("quantity") or 0)
        if sym:
            out[sym] = out.get(sym, 0) + q
    return {s: q for s, q in out.items() if q}


def _ts(v) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    try:
        d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
        if d.tzinfo is None:
            d = d.replace(tzinfo=IST)            # Kite timestamps are IST wall-clock
        return int(d.timestamp())
    except Exception:
        return None


def settling(kite, now: int) -> set:
    out = set()
    for o in kite.orders() or []:
        sym = str(o.get("tradingsymbol") or "")
        if not sym:
            continue
        if str(o.get("status") or "").upper() in IN_FLIGHT:
            out.add(sym)
            continue
        t = max([x for x in (_ts(o.get("exchange_update_timestamp")), _ts(o.get("order_timestamp"))) if x] or [0])
        if t and now - t < SETTLE_S:
            out.add(sym)
    return out


# ─────────────────────────────────────────────────────────────────────
# pure comparison
# ─────────────────────────────────────────────────────────────────────
def compare(app: Dict[str, int], broker: Dict[str, int], ignored: Dict[str, int], skip: set) -> List[dict]:
    out = []
    for sym in sorted(set(app) | set(broker) | set(ignored)):
        if sym in skip:
            continue
        a, b, ig = int(app.get(sym, 0)), int(broker.get(sym, 0)), int(ignored.get(sym, 0))
        if b == a + ig:
            continue
        kind = "ORPHAN" if a == 0 and b != 0 else ("GHOST" if b == 0 and a != 0 else "QTY")
        out.append({"symbol": sym, "kind": kind, "broker_qty": b, "app_qty": a, "ignored_qty": ig})
    return out


def _key(m: dict) -> tuple:
    return (m["symbol"], m["kind"], m["broker_qty"], m["app_qty"] + m["ignored_qty"])


# ─────────────────────────────────────────────────────────────────────
# alerts
# ─────────────────────────────────────────────────────────────────────
def _names(who: Dict[str, int]) -> str:
    try:
        from app.config.strategy_display import codename
    except Exception:
        def codename(s):
            return s
    return ", ".join(f"{codename(s)} {q:+d}" for s, q in sorted(who.items())) or "no strategy"


def message_for(m: dict, who: Dict[str, int]) -> str:
    s, b, a, ig = m["symbol"], m["broker_qty"], m["app_qty"], m["ignored_qty"]
    ign = f" (+{ig:+d} marked manual)" if ig else ""
    if m["kind"] == "ORPHAN":
        what = (f"Broker holds {b:+d} of {s}; no strategy holds it{ign}.\n"
                "Check Kite. If it is your manual trade, mark it ignored in the app.")
    elif m["kind"] == "GHOST":
        what = (f"{_names(who)} hold {a:+d} of {s}, but the broker is flat.\n"
                "It may have been closed outside the app (GTT, manual exit, RMS square-off).")
    else:
        what = (f"Broker holds {b:+d} of {s}; the app expects {a + ig:+d} ({_names(who)}{ign}).\n"
                "Likely a partial fill or a partial exit.")
    return f"🔎 Broker check — {m['kind']}\n{what}"


def _alert(text: str, severity: str = "error") -> None:
    try:
        from app.api.telegram_api import notify_critical
        notify_critical({"severity": severity, "message": text, "mode": "LIVE", "personal": True})
    except Exception as e:
        _audit(f"alert failed: {e!r} — {text}")


# ─────────────────────────────────────────────────────────────────────
# one poll
# ─────────────────────────────────────────────────────────────────────
def ignores(conn) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for sym, q in conn.execute("SELECT symbol, qty FROM recon_ignores WHERE removed_at IS NULL"):
        out[sym] = out.get(sym, 0) + int(q)
    return out


def _open_issues(conn) -> Dict[str, dict]:
    conn.row_factory = sqlite3.Row
    try:
        return {r["symbol"]: dict(r) for r in conn.execute("SELECT * FROM recon_issues WHERE resolved_at IS NULL")}
    finally:
        conn.row_factory = None


def run_once(now: Optional[int] = None, *, kite=None, force: bool = False) -> dict:
    """One poll. Gates: window + burst (unless force); some strategy LIVE on
    Zerodha or an open LIVE leg; a Kite trade session. Never raises."""
    now = now_ts() if now is None else int(now)
    if not _lock.acquire(blocking=False):
        return {"ran": False, "reason": "a poll is already running"}
    conn = None
    try:
        m = mod_of(now)
        if not force:
            if not (WINDOW_FROM <= m < WINDOW_TO) or not trading_day(ist(now).date()):
                return {"ran": False, "reason": "outside the reconciliation window"}
            if BURST_FROM <= m < BURST_TO:
                return {"ran": False, "reason": "09:15–09:18 open burst — no Kite calls"}
        sids = zerodha_strategies()
        app, who, bad = app_book(sids)
        if not app and not any_live_mode(sids):
            return {"ran": False, "reason": "no strategy is LIVE on Zerodha and the app holds no live legs"}
        conn = connect()
        if bad:
            conn.execute("INSERT OR REPLACE INTO recon_runs (ts, ok, error) VALUES (?,?,?)",
                         (now, 0, "unreadable: " + "; ".join(bad)))
            conn.commit()
            return {"ran": True, "ok": False, "error": "could not read every strategy's legs", "unreadable": bad}
        k = kite if kite is not None else trade_kite()
        if k is None:
            conn.execute("INSERT OR REPLACE INTO recon_runs (ts, ok, error) VALUES (?,?,?)", (now, 0, "no Kite session"))
            conn.commit()
            return {"ran": True, "ok": False, "error": "no Kite session"}
        broker = broker_book(k)
        skip = settling(k, now)
        ign = ignores(conn)
        # a manual position the broker no longer holds is done → its ignore lapses
        for sym in [s for s in ign if broker.get(s, 0) == 0 and app.get(s, 0) == 0]:
            conn.execute("UPDATE recon_ignores SET removed_at=?, removed_why='broker flat' "
                         "WHERE symbol=? AND removed_at IS NULL", (now, sym))
            ign.pop(sym, None)
        mism = compare(app, broker, ign, skip)
        pending: Dict[tuple, int] = _STATE["pending"]          # type: ignore[assignment]
        seen_now = {_key(x): x for x in mism}
        opened = _open_issues(conn)
        confirmed, resolved = [], []
        new_pending: Dict[tuple, int] = {}
        for key, x in seen_now.items():
            cnt = pending.get(key, 0) + 1
            new_pending[key] = cnt
            iss = opened.get(x["symbol"])
            same = iss and (iss["kind"], iss["broker_qty"], iss["app_qty"] + iss["ignored_qty"]) == key[1:]
            if same:
                continue                                       # already alerted, unchanged
            if cnt >= CONFIRM_POLLS:
                if iss:                                        # changed shape → close the old, open the new
                    conn.execute("UPDATE recon_issues SET resolved_at=? WHERE id=?", (now, iss["id"]))
                conn.execute("INSERT INTO recon_issues (symbol, kind, broker_qty, app_qty, ignored_qty, strategies, "
                             "first_seen, confirmed_at, alerted) VALUES (?,?,?,?,?,?,?,?,1)",
                             (x["symbol"], x["kind"], x["broker_qty"], x["app_qty"], x["ignored_qty"],
                              json.dumps(who.get(x["symbol"], {})), now - (cnt - 1) * POLL_S, now))
                confirmed.append(x)
        _STATE["pending"] = new_pending
        bad_syms = {x["symbol"] for x in mism}
        for sym, iss in opened.items():
            if sym not in bad_syms and sym not in skip:
                conn.execute("UPDATE recon_issues SET resolved_at=? WHERE id=?", (now, iss["id"]))
                resolved.append(iss)
        conn.execute("INSERT OR REPLACE INTO recon_runs (ts, ok, n_app, n_broker, n_mismatch, settling) "
                     "VALUES (?,?,?,?,?,?)", (now, 1, len(app), len(broker), len(mism), json.dumps(sorted(skip))))
        conn.execute("DELETE FROM recon_runs WHERE ts < ?", (now - KEEP_RUNS_DAYS * 86400,))
        conn.commit()
        for x in confirmed:
            text = message_for(x, who.get(x["symbol"], {}))
            _audit(text.replace("\n", " | "))
            _alert(text)
        for iss in resolved:
            text = f"✅ Broker check — resolved\n{iss['symbol']} matches again (was {iss['kind']})."
            _audit(text.replace("\n", " | "))
            _alert(text, severity="info")
        out = {"ran": True, "ok": True, "app": app, "broker": broker, "settling": sorted(skip),
               "mismatches": mism, "confirmed": confirmed, "resolved": [r["symbol"] for r in resolved]}
        _STATE["last"] = {"ts": now, "ok": True, "n_mismatch": len(mism)}
        return out
    except Exception as e:
        _audit(f"poll failed: {type(e).__name__}: {e}")
        try:
            if conn is not None:
                conn.execute("INSERT OR REPLACE INTO recon_runs (ts, ok, error) VALUES (?,?,?)",
                             (now, 0, f"{type(e).__name__}: {e}"[:300]))
                conn.commit()
        except Exception:
            pass
        return {"ran": True, "ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if conn is not None:
            conn.close()
        _lock.release()


# ─────────────────────────────────────────────────────────────────────
# environment
# ─────────────────────────────────────────────────────────────────────
def trading_day(d) -> bool:
    try:
        from app.utils.market_hours import is_trading_day
        return bool(is_trading_day(d))
    except Exception:
        return d.weekday() < 5


def trade_kite():
    try:
        from kiteconnect import KiteConnect
        from app.brokers.zerodha_manager import load_access_token, load_credentials
        api_key = (load_credentials() or {}).get("api_key")
        token = load_access_token()
        if not api_key or not token:
            return None
        k = KiteConnect(api_key=api_key)
        k.set_access_token(token)
        return k
    except Exception:
        return None


def is_admin() -> bool:
    try:
        from app.license import license_state
        return license_state.ui_level() == "admin"
    except Exception:
        return False


def recon_job() -> None:
    """APScheduler entry, every POLL_S seconds. Admin licence only (phase 1)."""
    try:
        if is_admin():
            run_once()
    except Exception as e:                                     # pragma: no cover
        _audit(f"job error: {e!r}")
