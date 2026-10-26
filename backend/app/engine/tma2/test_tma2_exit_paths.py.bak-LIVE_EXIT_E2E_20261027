# backend/app/engine/tma2/test_tma2_exit_paths.py
#
# ── EXIT_STATE_MACHINE_20261026 ── replay of the 2026-10-26 live incident
# (NIFTY26OCT22400CE short @159.85 / 23200CE hedge @4.50, SL 179.03) against
# a fake broker that models Kite closely enough to reproduce it:
#   * a LIMIT BUY fills only if the market trades at/below the limit;
#   * selling the hedge while the short is open is REJECTED on margin
#     (the exact 09:29:48 rejection);
#   * an order nobody cancels keeps working and fills later (09:35:49).
# Run standalone:  python3 test_tma2_exit_paths.py
from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.config import strategy_loader as SL                     # noqa: E402
from app.engine.tma2 import tma2_trade_manager as TM             # noqa: E402
from app.engine.tma2 import tma2_gtt_monitor as GM               # noqa: E402
from app.engine.tma2.tma2_common import TMA2Repo                 # noqa: E402
from app.execution.execution_router import ExecutionRouter       # noqa: E402

# ── version under test (test_tma_exit_paths.py re-targets these at TMA_V1)
V = {"TM": TM, "GM": GM, "Manager": TM.TMA2TradeManager, "Repo": TMA2Repo,
     "Monitor": GM.TMA2GTTMonitor, "table": "tma2_trades",
     "cfg": lambda main, hedge: {"s1": {"main": main, "hedge": hedge,
                                        "max_trades_per_day": 0}}}


def configure(**kw):
    global TM, GM
    V.update(kw)
    TM, GM = V["TM"], V["GM"]

FAILS = []
SHORT, HEDGE = "NIFTY26OCT22400CE", "NIFTY26OCT23200CE"


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def r05(x):
    return round(round(x / 0.05) * 0.05, 2)


class Clock:
    def __init__(self, broker=None):
        self.t = 1_000_000.0
        self.broker = broker

    def __call__(self):
        return self.t

    def sleep(self, dt):
        self.t += dt
        if self.broker:
            self.broker.tick()


class FakeBroker:
    """Kite-shaped fake. Prices come from px_fn(symbol, elapsed_s)."""

    def __init__(self, clock, px_fn):
        self.clock, self.px_fn = clock, px_fn
        self.t0 = clock.t
        self.orders, self.net, self.n = [], {}, 0
        self.modify_ok = self.cancel_ok = True
        self.gtts = []
        # The LTP the executor priced the 09:29:01 buy-back off (180.95) was
        # already behind a market trading ~209 — that is why 182.75 rested.
        self.stale_ltp = {SHORT: 180.95}

    # market
    def px(self, sym):
        return self.px_fn(sym, self.clock.t - self.t0)

    def tick(self):
        for o in self.orders:
            if o["status"] != "OPEN":
                continue
            p = self.px(o["tradingsymbol"])
            buy = o["transaction_type"] == "BUY"
            if (buy and p <= o["price"]) or (not buy and p >= o["price"]):
                o.update(status="COMPLETE", filled_quantity=o["quantity"],
                         pending_quantity=0, average_price=p)
                self.net[o["tradingsymbol"]] = self.net.get(o["tradingsymbol"], 0) \
                    + (o["quantity"] if buy else -o["quantity"])

    def _place(self, sym, txn, qty, price, manual=False):
        # Selling a long that is the cover for an open short of the SAME
        # option type (CE/PE) leaves that short naked → margin rejection.
        if txn == "SELL" and self.net.get(sym, 0) > 0 and any(
                q < 0 for k, q in self.net.items()
                if k != sym and k[-2:] == sym[-2:]):
            raise RuntimeError('Relay /relay/place_order failed HTTP 500: '
                               '{"error": "Insufficient funds. Margin '
                               'required: 183786.85. Margin available: '
                               '160000.00."}')
        self.n += 1
        oid = f"OID{self.n}"
        self.orders.append(dict(order_id=oid, tradingsymbol=sym,
                                transaction_type=txn, quantity=qty,
                                price=price, status="OPEN", filled_quantity=0,
                                pending_quantity=qty, average_price=0.0,
                                status_message="", manual=manual))
        self.tick()
        return oid

    # executor API (the subset the manager / monitor / driver use)
    def place_buy_exit(self, symbol, qty, reason):
        ltp = self.stale_ltp.get(symbol) or self.px(symbol)
        return self._place(symbol, "BUY", qty, r05(ltp * 1.01))

    def place_market_sell(self, symbol, qty):
        return self._place(symbol, "SELL", qty, r05(self.px(symbol) * 0.99))

    # entries — REAL executor shapes: (order_id, limit, qty) tuples
    reject_sell_entry = False

    def place_buy(self, symbol, token, qty):
        return (self._place(symbol, "BUY", qty, r05(self.px(symbol) * 1.01)),
                0.0, qty)

    def place_sell_entry(self, symbol, token, qty):
        lim = r05(self.px(symbol) * 0.98)
        if self.reject_sell_entry:            # RMS rejects after placement
            self.n += 1
            oid = f"OID{self.n}"
            self.orders.append(dict(order_id=oid, tradingsymbol=symbol,
                                    transaction_type="SELL", quantity=qty,
                                    price=lim, status="REJECTED",
                                    filled_quantity=0, pending_quantity=0,
                                    average_price=0.0, manual=False,
                                    status_message="Insufficient funds"))
            return (oid, lim, qty)
        return (self._place(symbol, "SELL", qty, lim), lim, qty)

    def manual(self, symbol, txn, qty):                  # user in Kite
        p = self.px(symbol)
        return self._place(symbol, txn, qty, p * (1.5 if txn == "BUY" else 0.5),
                           manual=True)

    def get_order_fill(self, oid):
        for o in self.orders:
            if o["order_id"] == oid:
                return {"status": o["status"], "avg_price": o["average_price"],
                        "filled_qty": o["filled_quantity"],
                        "pending_qty": o["pending_quantity"], "found": True,
                        "status_message": o["status_message"]}
        return {"status": None, "found": False}

    def modify_order(self, oid, price, symbol=""):
        if not self.modify_ok:
            return None
        for o in self.orders:
            if o["order_id"] == oid and o["status"] == "OPEN":
                o["price"] = price
                self.tick()
                return oid
        return None

    def cancel_order(self, oid, symbol=""):
        if not self.cancel_ok:
            raise RuntimeError("relay timeout")
        for o in self.orders:
            if o["order_id"] == oid and o["status"] == "OPEN":
                o["status"] = "CANCELLED"

    def fresh_buy_entry_limit(self, symbol):
        p = self.px(symbol)
        return (r05(p * 1.02), p, "ask")

    def fresh_sell_entry_limit(self, symbol):
        p = self.px(symbol)
        return (r05(p * 0.98), p, "bid")

    def get_open_positions_or_none(self):
        return [{"tradingsymbol": s, "quantity": q}
                for s, q in self.net.items() if q]

    def get_orders(self):
        return [dict(o) for o in self.orders]

    def get_gtts(self):
        return self.gtts

    def cancel_gtt_verified(self, gid):
        return True

    def get_gtt_status(self, gid):
        return None

    # helpers
    def open_orders(self):
        return [o for o in self.orders if o["status"] == "OPEN"]


def incident_px(sym, e):
    """Market after the 09:29:01 SL: short ~209, hedge ~5.10."""
    return 209.0 if sym == SHORT else 5.10


ALERTS, CRIT, LOG = [], [], []


def _stub_sinks():
    TM.record_alert = lambda code, msg="", **k: ALERTS.append((code, msg))
    TM.notify_critical = lambda d: CRIT.append(d["message"])
    for fn in ("notify_trade_entry", "notify_tp_exit", "notify_sl_exit",
               "notify_manual_exit"):
        setattr(TM, fn, lambda d: None)
    TM.write_audit_log = lambda m: LOG.append(m)
    GM.write_audit_log = lambda m: LOG.append(m)


def _setup(px_fn=incident_px):
    _stub_sinks()
    ALERTS.clear(); CRIT.clear(); LOG.clear()
    cfg = V["cfg"]({"premium_max": 200, "lots": 1, "sl_pct": 12,
                    "tp_pct": 10, "sl_unit": "PCT", "tp_unit": "ABS"},
                   {"premium_max": 5, "lots": 1})
    cfg.update(trade_mode="INTRADAY", trade_execution_mode="PAPER_LIVE")
    SL.load_strategy_config_ex = lambda sid: (cfg, False)
    db = tempfile.mktemp(suffix=".db")
    m = V["Manager"]({}, V["Repo"](db_path=db))
    clock = Clock()
    br = FakeBroker(clock, px_fn)
    clock.broker = br
    m.executor, m._sleep, m._clock = br, clock.sleep, clock
    snap = m._cfg_snapshot()
    g = m._build_group(
        sig={"ts": 1_793_000_000, "side": "PE", "cond": "E1", "spot": 25000.0},
        snap=snap, mode="LIVE", sell_sym=SHORT, hedge_sym=HEDGE,
        sell_qty=65, buy_qty=65,
        smeta={"token": 1, "strike": 22400, "expiry": "2026-10-27"},
        hmeta={"token": 2, "strike": 23200, "expiry": "2026-10-27"},
        sell_entry=159.85, hedge_entry=4.50, hedge_fb=False)
    m.group = g
    m._persist_entry(g)
    br.net = {SHORT: -65, HEDGE: 65}
    return m, br, clock, db


def _rows(db):
    return list(sqlite3.connect(db).execute(
        f"SELECT direction,status,exit_price FROM {V['table']} ORDER BY id"))


SL_RES = {"exit_ts": 1_793_000_000, "exit_price": 179.03,
          "exit_reason": "SL", "ambiguous": False}


def session_ts(hh, mm):
    return int(datetime(2026, 10, 26, hh, mm, tzinfo=timezone.utc).timestamp()
               - (5 * 3600 + 30 * 60))


def test_repeg_fills_then_hedge():
    m, br, clock, db = _setup()
    m._exit_group(dict(SL_RES), forced=True)
    buys = [o for o in br.orders if o["tradingsymbol"] == SHORT]
    check("A: one buy-back order, re-pegged (not a second order)", len(buys) == 1)
    check("A: buy-back FILLED near market, not at the stale 182.75",
          buys[0]["status"] == "COMPLETE" and buys[0]["average_price"] == 209.0)
    check("A: short flat, hedge flat", br.net.get(SHORT) == 0 and br.net.get(HEDGE) == 0)
    check("A: no orders left working", not br.open_orders())
    rows = _rows(db)
    check("A: both rows CLOSED at BROKER fills (not model 179.03)",
          rows[0][1] == "CLOSED" and rows[0][2] == 209.0 and rows[1][2] == 5.10,
          str(rows))
    check("A: group cleared", m.group is None)


def test_stuck_keeps_hedge_then_manual_flatten():
    m, br, clock, db = _setup()
    br.modify_ok = False                      # re-peg impossible → stuck
    m._exit_group(dict(SL_RES), forced=True)
    check("B: hedge sell NEVER attempted while short is open",
          not [o for o in br.orders if o["tradingsymbol"] == HEDGE])
    check("B: unfilled buy-back was CANCELLED, not abandoned",
          not br.open_orders() and br.orders[0]["status"] == "CANCELLED")
    check("B: nothing booked — rows still OPEN",
          [r[1] for r in _rows(db)] == ["OPEN", "OPEN"], str(_rows(db)))
    check("B: group kept with exit_state", m.group and m.group.get("exit_state"))
    check("B: alert says SHORT STILL OPEN and hedge KEPT",
          any("SHORT IS STILL OPEN" in c and "KEPT" in c for c in CRIT), str(CRIT))
    # 09:30:28 / 09:30:34 — user flattens manually, short first
    br.manual(SHORT, "BUY", 65)
    br.manual(HEDGE, "SELL", 65)
    m.on_minute(session_ts(9, 31), None, None)
    check("B: next minute resumes, sees flat, books manual fills",
          m.group is None and [r[1] for r in _rows(db)] == ["CLOSED", "CLOSED"],
          str(_rows(db)))
    check("B: no app order placed after the manual flatten",
          len([o for o in br.orders if not o["manual"]]) == 1)
    # 09:35:49 — price dips back to 182.75: nothing must fill now
    br.px_fn = lambda s, e: 182.0 if s == SHORT else 5.0
    br.tick()
    check("B: the 09:35 stray fill cannot happen (no working order)",
          br.net.get(SHORT, 0) == 0, str(br.net))


def test_unconfirmed_cancel_never_doubles():
    m, br, clock, db = _setup()
    br.modify_ok = False
    br.cancel_ok = False                      # cancel lost / relay down
    m._exit_group(dict(SL_RES), forced=True)
    check("C: unresolved order remembered", m.group["exit_state"]["working"].get("SELL"))
    check("C: alert tells the human to cancel that order",
          any("CANCEL IT in Kite" in c for c in CRIT), str(CRIT))
    br.cancel_ok, br.modify_ok = True, True
    m.on_minute(session_ts(9, 31), None, None)
    buys = [o for o in br.orders if o["tradingsymbol"] == SHORT]
    check("C: retry DROVE the same order — still exactly one buy-back",
          len(buys) == 1 and buys[0]["status"] == "COMPLETE", str(buys))
    check("C: then the hedge was sold and the group closed",
          br.net.get(HEDGE) == 0 and m.group is None)


def test_gtt_triggered_unfilled_escalates():
    # GTT limit rests at 179.55 (0.3% buffer) while market is at 209
    m, br, clock, db = _setup(lambda s, e: 209.0 if s == SHORT else 5.1)
    m.group["sell"]["gtt_id"] = "G1"
    br.orders.append(dict(order_id="GTTORD", tradingsymbol=SHORT,
                          transaction_type="BUY", quantity=65, price=179.55,
                          status="OPEN", filled_quantity=0, pending_quantity=65,
                          average_price=0.0, status_message=""))
    br.gtts = [{"id": "G1", "status": "triggered",
                "orders": [{"result": {"order_result": {"order_id": "GTTORD"}}}]}]
    mon = V["Monitor"](br, m)
    mon._sweep()
    buys = [o for o in br.orders if o["tradingsymbol"] == SHORT]
    check("D: monitor did NOT hand off as flat; adopted the GTT order",
          len(buys) == 1 and buys[0]["status"] == "COMPLETE", str(buys))
    check("D: hedge sold only after the short filled; group closed",
          br.net.get(SHORT) == 0 and br.net.get(HEDGE) == 0 and m.group is None)


def test_recon_flags_stray_fill():
    m, br, clock, db = _setup()
    m._exit_group(dict(SL_RES), forced=True)
    br.net[SHORT] = 65                        # a stray BUY filled later
    m._recon_tick(session_ts(9, 36))
    first = [a for a in ALERTS if a[0] == "TMA_RECON_MISMATCH"]
    m._recon_tick(session_ts(9, 38))
    second = [a for a in ALERTS if a[0] == "TMA_RECON_MISMATCH"]
    check("E: recon waits one confirming read, then alerts the stray",
          not first and len(second) == 1 and SHORT in second[0][1], str(ALERTS))


def test_router_forwards():
    class Z:   # Zerodha-shaped
        def place_gtt_sl_only_short(self, symbol, qty, sl_price,
                                    last_price=None, limit_buffer=1.003):
            return ("Z", limit_buffer)

        def get_basket_margin(self, basket):
            return {"required": 1, "available": 2}

    class A:   # Angel-shaped: no last_price / limit_buffer
        def place_gtt_sl_only_short(self, symbol, qty, sl_price):
            return ("A", None)

    check("F: router forwards place_gtt_sl_only_short (the AttributeError)",
          ExecutionRouter(Z()).place_gtt_sl_only_short(
              symbol="X", qty=65, sl_price=1, limit_buffer=1.05) == ("Z", 1.05))
    check("F: Angel GTT still placed; unsupported buffer dropped",
          ExecutionRouter(A()).place_gtt_sl_only_short(
              symbol="X", qty=65, sl_price=1, limit_buffer=1.05) == ("A", None))
    check("F: router forwards get_basket_margin (margin guard now runs)",
          ExecutionRouter(Z()).get_basket_margin([])["available"] == 2)
    try:
        ExecutionRouter(A()).get_basket_margin([])
        ok = False
    except RuntimeError:
        ok = True
    check("F: unsupported executor raises (caller fail-open), not AttributeError", ok)


ALL_TESTS = (test_repeg_fills_then_hedge,
             test_stuck_keeps_hedge_then_manual_flatten,
             test_unconfirmed_cancel_never_doubles,
             test_gtt_triggered_unfilled_escalates,
             test_recon_flags_stray_fill, test_router_forwards)


def run_all(label):
    for t in ALL_TESTS:
        t()
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {label} EXIT PATH TESTS PASSED")


if __name__ == "__main__":
    run_all("TMA_V2")
