# backend/app/slippage/slip_store.py
#
# ── SLIP_CAPTURE_20261029 ── what slippage needs, captured per order.
#
# WHY HERE: the Paper+Live twin (trading/shadow_book.py) is booked with the
# LIVE row's own entry / exit prices, so live − twin is ~0 by construction —
# it cannot measure slippage. The reference has to be taken when the order
# leaves the app.
#
#   at send   ZerodhaExecutor._relay_call (every market/limit order the
#             executors place: BUY, LIMIT_BUY, BUY_EXIT, SELL, SELL_ENTRY)
#             stamps the LTPStore price for the symbol and how old it was,
#             the send time, the ack time, the order id (or the error).
#             Written on a short daemon thread: the engine never waits.
#   fills     fetch_fills(kite) reads kite.orders() (today's book) and adds
#             status, average fill, filled qty, exchange timestamps and the
#             strategy (order_strategy_lookup) to every stamped order. Run by
#             the scheduler at 15:36 and 15:50 and by the Analytics view; Kite
#             only returns TODAY's orders, so it must run the same day.
#
#   slippage per unit = (fill − ref) for a BUY, (ref − fill) for a SELL;
#   positive = paid away.
#   ── SLIP_GTT_20261031 ── GTT-triggered exits (the broker-side stop-loss /
#   target) are not orders the app sends; fetch_fills finds them in
#   kite.get_gtts() (the triggered leg carries its order id) and stamps them
#   op GTT_TRIGGER with the TRIGGER price as the reference.
#
# ~/.scalp-app/slippage/slippage.db. Never raises into the order path.

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

FENCE = "SLIP_CAPTURE_20261029"
IST = timezone(timedelta(hours=5, minutes=30))
SIDE_OF = {"BUY": "BUY", "LIMIT_BUY": "BUY", "BUY_EXIT": "BUY", "SELL": "SELL", "SELL_ENTRY": "SELL"}
FINAL = {"COMPLETE", "REJECTED", "CANCELLED", "LAPSED"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS order_stamps (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     TEXT,
    op           TEXT NOT NULL,
    side         TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    ref_ltp      REAL,
    ref_age_s    REAL,
    sent_at      REAL NOT NULL,
    ack_at       REAL,
    error        TEXT,
    strategy_id  TEXT,
    status       TEXT,
    qty          INTEGER,
    filled_qty   INTEGER,
    fill_price   REAL,
    order_ts     INTEGER,
    fill_ts      INTEGER,
    fetched_at   INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_stamps_oid ON order_stamps (order_id) WHERE order_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_stamps_sent ON order_stamps (sent_at);
"""
_done: set = set()
_threads: list = []


def db_path() -> Path:
    from app.utils.app_paths import APP_HOME
    return APP_HOME / "slippage" / "slippage.db"


def connect(timeout: float = 30) -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        _done.discard(str(p))
    c = sqlite3.connect(str(p), timeout=timeout)
    c.execute("PRAGMA journal_mode=WAL;")
    if str(p) not in _done:
        c.executescript(_SCHEMA)
        _done.add(str(p))
    return c


def _write(row: tuple) -> None:
    try:
        c = connect(timeout=10)
        try:
            c.execute("INSERT INTO order_stamps (order_id, op, side, symbol, ref_ltp, ref_age_s, sent_at, ack_at, error) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", row)
            c.commit()
        finally:
            c.close()
    except Exception as e:
        try:
            from app.event_bus.audit_logger import write_audit_log
            write_audit_log(f"[SLIP] stamp not stored ({type(e).__name__}: {e}) {row[1]} {row[3]} {row[0]}")
        except Exception:
            pass


def record_send(op: str, symbol: str, ref, sent_at: float, ack_at: float, order_id, error: Optional[str]) -> None:
    """Called from the executor's order chokepoint AFTER the order call
    returned (or failed). ref = LTPStore.get_with_timestamp(symbol) taken just
    before sending. Returns at once; the row is written on its own thread."""
    side = SIDE_OF.get(op)
    if not side:
        return
    try:
        price, ts = (ref if ref else (None, None))
        age = round(sent_at - float(ts), 3) if ts else None
        row = (str(order_id) if order_id else None, op, side, str(symbol or ""),
               float(price) if price else None, age, float(sent_at), float(ack_at), error)
        t = threading.Thread(target=_write, args=(row,), daemon=True, name="slip-stamp")
        _threads[:] = [x for x in _threads if x.is_alive()] + [t]
        t.start()
    except Exception:
        pass


def wait(timeout: float = 5.0) -> None:
    for t in list(_threads):
        t.join(timeout)


def _ts(v) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
        if d.tzinfo is None:
            d = d.replace(tzinfo=IST)            # Kite timestamps are IST wall-clock
        return int(d.timestamp())
    except Exception:
        return None


def _strategy(order_id: str) -> Optional[str]:
    try:
        from app.execution.order_strategy_lookup import find_strategy_by_order_id
        return find_strategy_by_order_id(order_id)
    except Exception:
        return None


def _gtt_triggers(kite, orders: dict) -> list:
    """── SLIP_GTT_20261031 ── today's GTT-triggered orders: (order_id, side,
    symbol, trigger price). The triggered leg of a GTT carries
    result.order_result.order_id; its trigger is trigger_values[i] (single-
    trigger GTTs have one value)."""
    out = []
    try:
        gtts = kite.get_gtts() or []
    except Exception:
        return out
    for g in gtts:
        try:
            cond = g.get("condition") or {}
            tv = list(cond.get("trigger_values") or [])
            sym = str(cond.get("tradingsymbol") or "")
            for i, o in enumerate(g.get("orders") or []):
                res = (o or {}).get("result") or {}
                oid = str(((res.get("order_result") or {}).get("order_id")) or "")
                if not oid or oid not in orders or not tv:
                    continue
                trig = float(tv[i] if i < len(tv) else tv[0])
                side = str(o.get("transaction_type") or res.get("transaction_type") or "").upper()
                if side in ("BUY", "SELL"):
                    out.append((oid, side, sym or str(orders[oid].get("tradingsymbol") or ""), trig))
        except Exception:
            continue
    return out


def fetch_fills(kite, now: Optional[int] = None) -> dict:
    """Fill details for today's stamped orders from kite.orders(). Never raises."""
    now = int(now or time.time())
    out = {"updated": 0, "pending": 0, "unknown": 0, "gtt_added": 0}
    try:
        orders = {str(o.get("order_id")): o for o in (kite.orders() or []) if o.get("order_id")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    day0 = int(datetime.fromtimestamp(now, IST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    c = connect()
    try:
        for oid, side, sym, trig in _gtt_triggers(kite, orders):        # ── SLIP_GTT_20261031 ──
            if c.execute("SELECT 1 FROM order_stamps WHERE order_id=?", (oid,)).fetchone():
                continue
            sent = _ts(orders[oid].get("order_timestamp")) or now
            c.execute("INSERT INTO order_stamps (order_id, op, side, symbol, ref_ltp, ref_age_s, sent_at) "
                      "VALUES (?,?,?,?,?,?,?)", (oid, "GTT_TRIGGER", side, sym, trig, 0.0, sent))
            out["gtt_added"] += 1
        c.commit()
        rows = c.execute("SELECT id, order_id, strategy_id FROM order_stamps WHERE sent_at >= ? AND order_id IS NOT NULL "
                         "AND (status IS NULL OR status NOT IN ('COMPLETE','REJECTED','CANCELLED','LAPSED'))",
                         (day0,)).fetchall()
        for rid, oid, sid in rows:
            o = orders.get(str(oid))
            if not o:
                out["unknown"] += 1
                continue
            st = str(o.get("status") or "").upper()
            c.execute("UPDATE order_stamps SET status=?, qty=?, filled_qty=?, fill_price=?, order_ts=?, fill_ts=?, "
                      "fetched_at=?, strategy_id=COALESCE(strategy_id, ?) WHERE id=?",
                      (st, int(o.get("quantity") or 0), int(o.get("filled_quantity") or 0),
                       float(o.get("average_price") or 0) or None, _ts(o.get("order_timestamp")),
                       _ts(o.get("exchange_update_timestamp") or o.get("exchange_timestamp")), now,
                       sid or _strategy(oid), rid))
            out["updated" if st in FINAL else "pending"] += 1
        c.commit()
    finally:
        c.close()
    return out


def slip_fill_job() -> None:
    """Scheduler entry (15:36 and 15:50, Mon–Fri). Never raises."""
    try:
        c = connect()
        try:
            day0 = int(datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
            if not c.execute("SELECT 1 FROM order_stamps WHERE sent_at >= ? LIMIT 1", (day0,)).fetchone():
                return
        finally:
            c.close()
        from app.recon.recon_core import trade_kite
        k = trade_kite()
        if k is None:
            return
        res = fetch_fills(k)
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log(f"[SLIP] fills fetched: {res}")
    except Exception:
        pass
