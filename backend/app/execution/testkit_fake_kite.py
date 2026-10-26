# backend/app/execution/testkit_fake_kite.py
#
# ── E2E_TESTKIT_20261027 ── a Kite-shaped fake broker for END-TO-END strategy
# tests. TEST-ONLY: nothing in the app imports this module.
#
# It implements the ZerodhaOrderExecutor contract the strategies actually use
# (tuple returns, raises on placement failure, get_order_fill dict shape,
# GTT dicts with orders[].result.order_result.order_id), so tests wrap it in
# the REAL ExecutionRouter — exactly as live runs do — and a missing router
# forward fails the test the way it fails in production.
#
# Market model (deliberately the behaviours that broke things on 2026-10-26):
#   * LIMIT orders fill only when the market trades through the limit; an
#     unfilled order keeps WORKING until cancelled (and can fill much later).
#   * Exit limits are priced off `stale_ltp` when set (the executor's LTP can
#     lag a fast market), else the current price.
#   * GTTs trigger on price crossing and place their LIMIT order; on a gap
#     past the buffer that order rests unfilled while the GTT reads triggered.
#   * Selling a long that covers an open short of the same option type (CE/PE)
#     is rejected on margin; optionally a naked short entry is too.
#   * Injectable failures: RMS rejection per symbol, placement exceptions,
#     cancel / modify failures, unreadable positions, margin available.
from __future__ import annotations

import itertools
from typing import Dict, List, Optional

TICK = 0.05
WORKING = "OPEN"
DEAD = ("REJECTED", "CANCELLED", "LAPSED")


def r05(x: float) -> float:
    return max(TICK, round(round(x / TICK) * TICK, 2))


class SimClock:
    """Simulated time for the exit driver: sleep advances time and lets the
    market move (FakeKite.tick)."""

    def __init__(self, t: float = 1_793_000_000.0):
        self.t = float(t)
        self.kite: Optional["FakeKite"] = None

    def __call__(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += float(dt)
        if self.kite is not None:
            self.kite.tick()


class FakeKite:
    def __init__(self, prices: Dict[str, float], *, lot_size: int = 65,
                 clock: Optional[SimClock] = None):
        self.px: Dict[str, float] = dict(prices)
        self.lot = lot_size
        self.clock = clock or SimClock()
        self.clock.kite = self
        self.orders: List[dict] = []
        self.net: Dict[str, int] = {}
        self.gtts: Dict[str, dict] = {}
        self._ids = itertools.count(1)
        # injections
        self.stale_ltp: Dict[str, float] = {}
        self.reject: Dict[str, str] = {}          # symbol -> RMS message (entry or exit)
        self.place_raises: Dict[str, str] = {}    # symbol -> exception text
        self.cancel_ok = True
        self.modify_ok = True
        self.positions_readable = True
        self.margin_available = 1_000_000.0
        self.margin_required = 50_000.0
        self.naked_short_reject = False
        self.gtt_fail = False                     # GTT placement raises
        self.events: List[tuple] = []             # (kind, symbol, qty, oid)

    # ── market ───────────────────────────────────────────────────────
    def set_px(self, sym: str, p: float) -> None:
        self.px[sym] = float(p)
        self.tick()

    def ltp(self, sym: str) -> float:
        return float(self.px[sym])

    def tick(self) -> None:
        for g in self.gtts.values():
            if g["status"] != "active":
                continue
            p = self.px.get(g["symbol"])
            if p is None:
                continue
            up = g["side"] == "BUY" or g.get("tp")
            hit = p >= g["trigger"] if up else p <= g["trigger"]
            if hit:
                g["status"] = "triggered"
                oid = self._new_order(g["symbol"], g["side"], g["qty"], g["limit"],
                                      src="GTT", match=False)
                g["orders"][0]["result"] = {"order_result": {"order_id": oid}}
        for o in self.orders:
            if o["status"] != WORKING:
                continue
            p = self.px.get(o["tradingsymbol"])
            buy = o["transaction_type"] == "BUY"
            if p is not None and ((buy and p <= o["price"]) or (not buy and p >= o["price"])):
                o.update(status="COMPLETE", filled_quantity=o["quantity"],
                         pending_quantity=0, average_price=p)
                s = o["tradingsymbol"]
                self.net[s] = self.net.get(s, 0) + (o["quantity"] if buy else -o["quantity"])

    def _covers_short(self, sym: str) -> bool:
        return self.net.get(sym, 0) > 0 and any(
            q < 0 for k, q in self.net.items() if k != sym and k[-2:] == sym[-2:])

    def _new_order(self, sym, txn, qty, price, *, src="APP", match=True) -> str:
        if sym in self.place_raises:
            raise RuntimeError(self.place_raises[sym])
        if qty <= 0 or qty % self.lot:
            raise RuntimeError(f"INVALID_QTY qty={qty} lot_size={self.lot} SYMBOL={sym}")
        oid = f"OID{next(self._ids)}"
        status, msg = WORKING, ""
        if sym in self.reject:
            status, msg = "REJECTED", self.reject[sym]
        elif txn == "SELL" and self._covers_short(sym):
            raise RuntimeError('Relay /relay/place_order failed HTTP 500: {"error": '
                               '"Insufficient funds. This order will create a higher '
                               'margin requirement than available."}')
        elif (txn == "SELL" and self.naked_short_reject and self.net.get(sym, 0) <= 0
              and not any(q > 0 for k, q in self.net.items() if k[-2:] == sym[-2:])):
            status, msg = "REJECTED", "Insufficient funds (naked short)"
        self.orders.append(dict(order_id=oid, tradingsymbol=sym, transaction_type=txn,
                                quantity=int(qty), price=float(price), status=status,
                                filled_quantity=0,
                                pending_quantity=0 if status != WORKING else int(qty),
                                average_price=0.0, status_message=msg, src=src))
        self.events.append((txn, sym, int(qty), oid))
        if match:
            self.tick()
        return oid

    # ── ZerodhaOrderExecutor contract ────────────────────────────────
    def place_buy(self, symbol, token, qty):
        return (self._new_order(symbol, "BUY", qty, r05(self.ltp(symbol) * 1.01)), 0.0, qty)

    def place_limit_buy(self, symbol, qty, price, product="MIS", tag=""):
        return self._new_order(symbol, "BUY", qty, price)

    def place_sell_entry(self, symbol, token, qty):
        lim = r05(self.ltp(symbol) * 0.98)
        return (self._new_order(symbol, "SELL", qty, lim), lim, qty)

    def place_buy_exit(self, symbol, qty, reason):
        ltp = self.stale_ltp.get(symbol) or self.ltp(symbol)
        return self._new_order(symbol, "BUY", qty, r05(ltp * 1.01))

    def place_market_sell(self, symbol, qty):
        ltp = self.stale_ltp.get(symbol) or self.ltp(symbol)
        return self._new_order(symbol, "SELL", qty, r05(ltp * 0.99))

    def place_exit(self, symbol, qty, reason):
        return self.place_market_sell(symbol, qty)

    def get_order_fill(self, order_id):
        for o in self.orders:
            if o["order_id"] == order_id:
                return {"status": o["status"], "avg_price": o["average_price"],
                        "filled_qty": o["filled_quantity"],
                        "pending_qty": o["pending_quantity"], "found": True,
                        "status_message": o["status_message"]}
        return {"status": None, "avg_price": 0.0, "filled_qty": 0,
                "pending_qty": 0, "found": False, "status_message": ""}

    def get_last_avg_price(self, order_id):
        return self.get_order_fill(order_id)["avg_price"]

    def get_orders(self):
        return [dict(o) for o in self.orders]

    def modify_order(self, order_id, price, symbol=""):
        if not self.modify_ok:
            return None
        for o in self.orders:
            if o["order_id"] == order_id and o["status"] == WORKING:
                o["price"] = float(price)
                self.tick()
                return order_id
        return None

    def cancel_order(self, order_id, symbol=""):
        if not self.cancel_ok:
            raise RuntimeError("relay timeout")
        for o in self.orders:
            if o["order_id"] == order_id and o["status"] == WORKING:
                o["status"] = "CANCELLED"
                o["pending_quantity"] = 0

    def fresh_buy_entry_limit(self, symbol):
        p = self.ltp(symbol)
        return (r05(p * 1.02), p, "ask")

    def fresh_sell_entry_limit(self, symbol):
        p = self.ltp(symbol)
        return (r05(p * 0.98), p, "bid")

    def get_open_positions(self):
        return [{"tradingsymbol": s, "quantity": q} for s, q in self.net.items() if q]

    def get_open_positions_or_none(self):
        return self.get_open_positions() if self.positions_readable else None

    def positions(self):                       # raw-kite shape (adoption)
        return {"net": self.get_open_positions()}

    def get_basket_margin(self, basket):
        return {"required": self.margin_required, "available": self.margin_available}

    def resolve_symbol(self, symbol):
        return symbol

    def _resolve_ltp(self, symbol):
        return self.px.get(symbol)

    # GTTs
    def _gtt(self, symbol, qty, trigger, side, limit_buffer):
        if self.gtt_fail:
            raise RuntimeError("GTT service unavailable")
        last = self.ltp(symbol)
        trig = r05(trigger)
        if side == "BUY" and not last < trig:
            raise RuntimeError(f"Invalid SL-GTT band LAST={last} !< SL={trig}")
        if side == "SELL" and not last > trig and limit_buffer is not None:
            raise RuntimeError(f"Invalid SL-GTT-LONG band LAST={last} !> SL={trig}")
        buf = max(1.0, float(limit_buffer or 1.003))
        lim = r05(trig * buf) if side == "BUY" else r05(trig / buf)
        gid = str(next(self._ids) + 900000)
        self.gtts[gid] = dict(id=gid, symbol=symbol, qty=int(qty), trigger=trig,
                              side=side, limit=lim, status="active",
                              orders=[{"result": None}])
        self.events.append(("GTT", symbol, int(qty), gid))
        return gid

    def place_gtt_sl_only_short(self, symbol, qty, sl_price, last_price=None,
                                limit_buffer=1.003):
        return self._gtt(symbol, qty, sl_price, "BUY", limit_buffer)

    def place_gtt_sl_only_long(self, symbol, qty, sl_price, last_price=None,
                               limit_buffer=1.003):
        return self._gtt(symbol, qty, sl_price, "SELL", limit_buffer)

    def place_gtt_tp_only_long(self, symbol, qty, tp_price, last_price=None):
        gid = self._gtt(symbol, qty, tp_price, "SELL", None)
        self.gtts[gid]["tp"] = True                 # SELL when price RISES to tp
        return gid

    def get_gtts(self):
        return [{"id": g["id"], "status": g["status"], "orders": g["orders"],
                 "condition": {"tradingsymbol": g["symbol"]}}
                for g in self.gtts.values() if g["status"] != "deleted"]

    def get_gtts_or_none(self):
        return self.get_gtts()

    def get_gtt_status(self, gtt_id):
        g = self.gtts.get(str(gtt_id))
        if g is None or g["status"] == "deleted":
            return "missing"
        return g["status"]

    def cancel_gtt(self, gtt_id):
        g = self.gtts.get(str(gtt_id))
        if g is not None and g["status"] == "active":
            g["status"] = "deleted"

    def cancel_gtt_verified(self, gtt_id, retries=4):
        g = self.gtts.get(str(gtt_id))
        if g is None or g["status"] != "active":
            return True                              # gone / spent
        self.cancel_gtt(gtt_id)
        return True

    # ── test helpers ─────────────────────────────────────────────────
    def open_orders(self):
        return [o for o in self.orders if o["status"] == WORKING]

    def active_gtts(self):
        return [g for g in self.gtts.values() if g["status"] == "active"]

    def flat(self) -> bool:
        return all(q == 0 for q in self.net.values())

    def kinds(self, sym: Optional[str] = None):
        return [(e[0], e[1]) for e in self.events if sym is None or e[1] == sym]


# ── E2E_TESTKIT_20261027 ── hermetic isolation for tests run on a REAL install.
# A strategy test must never page the live Telegram group (2026-10-15 did),
# write the real app.db (shadow twins of LIVE rows go there when a strategy is
# PAPER_LIVE in the real config), or append to the real audit log. isolate()
# cuts every sink at its source, then sweeps already-imported app modules for
# names bound by `from … import …`. Call it AFTER importing the modules under
# test. Returns what would have been sent, for assertions.
def isolate() -> dict:
    import sys
    import tempfile
    from pathlib import Path

    sent = {"telegram": [], "alerts": [], "audit": []}

    def _tg(name):
        return lambda *a, **k: sent["telegram"].append((name, a, k))

    def _alert(*a, **k):
        sent["alerts"].append((a, k))

    def _audit(msg, *a, **k):
        sent["audit"].append(msg)

    def _noop(*a, **k):
        return None

    tmp = Path(tempfile.mkdtemp(prefix="scalp_e2e_"))
    try:                                    # 1) any DB write → a temp file
        import app.db.sqlite as S
        S.DB_PATH = tmp / "app.db"
        if hasattr(S._local, "conn"):
            del S._local.conn
    except Exception:
        pass
    try:                                    # 2) Telegram: the network sink + every notifier
        import app.api.telegram_api as TG
        TG.send_telegram_message = lambda *a, **k: (sent["telegram"].append(("send", a, k)), True)[1]
        if hasattr(TG, "_fanout"):
            TG._fanout = _tg("_fanout")
        for n in dir(TG):
            if n.startswith("notify_") and callable(getattr(TG, n)):
                setattr(TG, n, _tg(n))
    except Exception:
        pass
    try:                                    # 3) in-app alerts / events
        import app.event_bus.inapp_events as IE
        for n in ("record_alert", "record_alert_once", "record_event"):
            if hasattr(IE, n):
                setattr(IE, n, _alert)
    except Exception:
        pass
    try:                                    # 4) audit log
        import app.event_bus.audit_logger as AL
        AL.write_audit_log = _audit
    except Exception:
        pass
    try:                                    # 5) PAPER_LIVE shadow twins
        import app.trading.shadow_book as SB
        SB.mirror_open = _noop
        SB.mirror_close = _noop
    except Exception:
        pass
    for name, mod in list(sys.modules.items()):   # 6) names bound at import
        if mod is None or not (name == "app" or name.startswith("app.")):
            continue
        if name.endswith("testkit_fake_kite"):
            continue
        for attr, fn in (("write_audit_log", _audit), ("record_alert", _alert),
                         ("record_alert_once", _alert), ("record_event", _alert)):
            if callable(getattr(mod, attr, None)):
                try:
                    setattr(mod, attr, fn)
                except Exception:
                    pass
        for attr in dir(mod):
            if attr.startswith("notify_") and callable(getattr(mod, attr, None)):
                try:
                    setattr(mod, attr, _tg(attr))
                except Exception:
                    pass
        sb = getattr(mod, "_shadow", None)
        if sb is not None and hasattr(sb, "mirror_open"):
            sb.mirror_open = _noop
            sb.mirror_close = _noop
    sent["tmp"] = tmp
    return sent
