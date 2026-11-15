# test_order_once.py — ORDER_ONCE_20261115
# The REAL ZerodhaOrderExecutor with a fake Kite and fake relays: when the
# answer to an order is lost, is the order sent twice?
#   PYTHONPATH=backend python3 backend/app/execution/test_order_once.py
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests                                                       # noqa: E402
import app.event_bus.audit_logger as AL                               # noqa: E402
AL.write_audit_log = lambda m: None
import app.execution.zerodha_executor as ZE                           # noqa: E402
LOG = []
ZE.write_audit_log = lambda m: LOG.append(m)

PASSED, FAILS, PAGES = [0], [], []


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print(f"[ok  ] {name}")
    else:
        FAILS.append(name)
        print(f"[MISS] {name}\n         -> {detail}")


class Clock:
    t = 0.0


ZE.time.sleep = lambda s: setattr(Clock, "t", Clock.t + s)
if hasattr(ZE, "_start_bg"):
    ZE._start_bg = lambda fn, name: fn()                 # the background watch runs inline
sys.modules.setdefault("app.api.telegram_api", types.ModuleType("app.api.telegram_api"))
sys.modules["app.api.telegram_api"].notify_critical = lambda d: PAGES.append(d)


class NetworkException(Exception):
    pass


class Kite:
    VARIETY_REGULAR, EXCHANGE_NFO, ORDER_TYPE_LIMIT = "regular", "NFO", "LIMIT"
    TRANSACTION_TYPE_BUY, TRANSACTION_TYPE_SELL, PRODUCT_NRML = "BUY", "SELL", "NRML"
    GTT_TYPE_SINGLE = "single"
    api_key, access_token = "k", "t"

    def __init__(self):
        self.book, self.gtts, self.pending = [], [], []     # pending: (visible_at, order)
        self.reads = 0
        self.direct_fail_after_send = False
        self.cancelled = []

    def _add(self, kw, visible_at=0.0, status="OPEN"):
        o = dict(kw, order_id=f"O{len(self.book) + len(self.pending) + 1}", status=status,
                 filled_quantity=kw["quantity"] if status == "COMPLETE" else 0, average_price=100.0)
        (self.book if visible_at <= Clock.t else self.pending).append(o if visible_at <= Clock.t else (visible_at, o))
        return o["order_id"]

    def place_order(self, **kw):
        oid = self._add(kw)
        if self.direct_fail_after_send:
            raise NetworkException("Read timed out")
        return oid

    def orders(self):
        self.reads += 1
        for item in list(self.pending):
            if item[0] <= Clock.t:
                self.pending.remove(item)
                self.book.append(item[1])
        return [dict(o) for o in self.book]

    def cancel_order(self, variety=None, order_id=None, **k):
        self.cancelled.append(order_id)
        for o in self.book:
            if o["order_id"] == order_id:
                o["status"] = "CANCELLED"

    def place_gtt(self, **kw):
        g = {"id": 500 + len(self.gtts), "status": "active",
             "condition": {"tradingsymbol": kw["tradingsymbol"], "trigger_values": kw["trigger_values"]},
             "orders": [dict(o) for o in kw["orders"]]}
        self.gtts.append(g)
        return {"trigger_id": g["id"]}

    def get_gtts(self):
        return [dict(g) for g in self.gtts if g["status"] == "active"]

    def delete_gtt(self, gid):
        for g in self.gtts:
            if str(g["id"]) == str(gid):
                g["status"] = "deleted"


MODE = {}        # relay url -> behaviour


class Relay:
    """A relay as the app's RelayClient sees it."""

    def __init__(self, url, secret, api_key, access_token):
        self.url = url

    def _do(self, send):
        m = MODE.get(self.url, "ok")
        if m == "refused":
            raise requests.exceptions.ConnectionError(
                "HTTPConnectionPool: Max retries exceeded (Caused by NewConnectionError("
                "'Failed to establish a new connection: [Errno 61] Connection refused'))")
        if m == "reject":
            raise ZE.RelayBrokerRejection("InputException", "Insufficient funds")
        if m == "http401":
            raise RuntimeError("Relay /relay/place_order failed HTTP 401: bad secret")
        if m == "lost":                                   # timed out, the order never reached Kite
            raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=10)")
        out = send(0.0)
        if m == "timeout_after_send":
            raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=10)")
        if m == "reset_after_send":
            raise requests.exceptions.ConnectionError("('Connection aborted.', RemoteDisconnected(...))")
        if m == "http500_after_send":
            raise RuntimeError("Relay /relay/place_order failed HTTP 500: upstream timeout")
        return out

    def place_order(self, **kw):
        m = MODE.get(self.url, "ok")
        if m.startswith("late"):                          # reaches Kite, shows up 20 s later
            K._add(kw, visible_at=Clock.t + 20.0, status="COMPLETE" if m == "late_filled" else "OPEN")
            raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=10)")
        return self._do(lambda _t: K._add(kw))

    def place_gtt(self, **kw):
        return self._do(lambda _t: K.place_gtt(**kw))

    def cancel_order(self, variety, order_id):
        K.cancel_order(order_id=order_id)

    def cancel_gtt(self, trigger_id):
        K.delete_gtt(trigger_id)


ZE.RelayClient = Relay
K = None


def setup(relays=("A",), **modes):
    global K
    K = Kite()
    Clock.t = 0.0
    MODE.clear()
    MODE.update({f"http://{k}": v for k, v in modes.items()})
    PAGES.clear()
    LOG.clear()
    cfg = {"relays": [{"url": f"http://{r}", "host": r, "secret": "s", "is_primary": i == 0}
                      for i, r in enumerate(relays)]} if relays else None
    ZE._load_relay_config = lambda: cfg
    bm = types.SimpleNamespace(is_trade_ready=lambda: True, refresh=lambda: None,
                               get_trade_kite=lambda: K, get_data_kite=lambda: K)
    ex = ZE.ZerodhaOrderExecutor(bm)
    ex._ensure_trading_enabled = lambda: None
    ex._get_lot_size = lambda kite, symbol, qty: 65
    ex._resolve_ltp = lambda symbol: 100.0
    return ex


S = "NIFTY26N1725000CE"


def buy_back(ex):
    try:
        return ex.place_buy_exit(S, 130, "TEST"), None
    except Exception as e:
        return None, e


def live(k=None):
    k = k or K
    k.orders()
    return [o for o in k.book if o["status"] != "CANCELLED"]


def t_normal_order_costs_nothing():
    ex = setup()
    oid, err = buy_back(ex)
    tag = K.book[0].get("tag") or ""
    check("a normal order: one order, tagged, and NOT ONE extra broker call or wait",
          oid == "O1" and len(K.book) == 1 and K.reads == 0 and Clock.t == 0.0,
          f"oid={oid} err={err!r} orders={len(K.book)} reads={K.reads} waited={Clock.t}s")
    check("…the tag is valid for Kite (letters and digits, at most 20) and differs per order",
          tag.isalnum() and len(tag) <= 20 and buy_back(ex)[0] == "O2" and K.book[1]["tag"] != tag,
          f"tags={[o.get('tag') for o in K.book]}")


def t_answer_lost_single_relay():
    for mode in ("timeout_after_send", "reset_after_send", "http500_after_send"):
        ex = setup(A=mode)
        oid, err = buy_back(ex)
        check(f"one relay, answer lost ({mode}): the order that IS at the broker is returned, not an error",
              oid == "O1" and len(live()) == 1,
              f"returned={oid} error={type(err).__name__ if err else None} orders at broker={len(live())} "
              f"(an error here makes the strategy place the buy-back AGAIN)")


def t_answer_lost_two_relays():
    ex = setup(("A", "B"), A="timeout_after_send")
    oid, err = buy_back(ex)
    check("two relays, the first one's answer is lost: ONE buy-back at the broker, not two",
          len(live()) == 1 and oid == "O1", f"returned={oid} orders at broker={len(live())} (2 = bought back twice)")


def t_clear_failures_unchanged():
    ex = setup(("A", "B"), A="refused")
    oid, err = buy_back(ex)
    check("relay down (connection refused): straight to the next relay, no lookup, no wait",
          oid == "O1" and len(live()) == 1 and Clock.t == 0.0, f"oid={oid} waited={Clock.t}s")
    ex = setup(("A", "B"), A="http401")
    oid, err = buy_back(ex)
    check("relay refused the request (4xx): next relay, no wait", oid == "O1" and Clock.t == 0.0, f"oid={oid} waited={Clock.t}s")
    ex = setup(("A", "B"), A="reject")
    oid, err = buy_back(ex)
    check("the broker rejected it: raised at once, nothing resent, no lookup",
          oid is None and isinstance(err, ZE.RelayBrokerRejection) and not K.book and Clock.t == 0.0,
          f"oid={oid} err={err!r}")


def t_really_lost():
    ex = setup(("A", "B"), A="lost")
    oid, err = buy_back(ex)
    check("timed out and the order never reached the broker: looked up ~2 s, then sent ONCE via the next relay",
          oid == "O1" and len(live()) == 1 and 1.9 <= Clock.t and not PAGES,
          f"oid={oid} orders={len(live())} pages={len(PAGES)}")
    ex = setup(A="lost")
    oid, err = buy_back(ex)
    check("…with one relay: raised as before (the strategy retries), nothing at the broker",
          oid is None and isinstance(err, ZE.RelayUnavailableError) and not live(), f"oid={oid} err={err!r}")


def t_late_arrival():
    ex = setup(("A", "B"), A="late")
    oid, err = buy_back(ex)
    Clock.t += 0
    check("first order shows up 20 s late, after a second was sent: the late one is CANCELLED, one page",
          len(live()) == 1 and len(K.cancelled) == 1 and len(PAGES) == 1 and "CANCELLED" in PAGES[0]["message"],
          f"orders live={len(live())} cancelled={K.cancelled} pages={[p['message'][:80] for p in PAGES]}")
    ex = setup(("A", "B"), A="late_filled")
    oid, err = buy_back(ex)
    check("…if the late one has already FILLED: not cancelled, paged as an error to check Kite",
          not K.cancelled and len(PAGES) == 1 and PAGES[0]["severity"] == "error", f"pages={PAGES}")
    ex = setup(A="late")
    oid, err = buy_back(ex)
    check("one relay, the 'failed' order shows up late: cancelled (the app is not tracking it) and paged",
          oid is None and len(live()) == 0 and len(K.cancelled) == 1 and len(PAGES) == 1,
          f"oid={oid} live={len(live())} cancelled={K.cancelled} pages={len(PAGES)}")


def t_gtt():
    ex = setup(("A", "B"), A="timeout_after_send")
    gid = ex.place_gtt_sl_only_short(S, 130, 140.0, last_price=100.0)
    check("stop GTT, first relay's answer lost: ONE GTT at the broker, and its id is returned",
          len(K.get_gtts()) == 1 and str(gid) == str(K.gtts[0]["id"]),
          f"GTTs armed={len(K.get_gtts())} returned={gid} (2 = two stops armed for one short)")


def t_direct_mode():
    ex = setup(relays=())
    K.direct_fail_after_send = True
    oid, err = buy_back(ex)
    check("no relay (direct to Kite), answer lost: the order at the broker is returned",
          oid == "O1" and len(live()) == 1, f"returned={oid} err={type(err).__name__ if err else None}")


def t_callers_tag():
    ex = setup(A="timeout_after_send")
    K._add(dict(tradingsymbol=S, quantity=65, transaction_type="BUY", tag="LVB"))    # an older order, same tag
    try:
        oid = ex.place_limit_buy(S, 65, 100.0, tag="LVB")
    except Exception as e:
        oid = e
    check("an order with the caller's own (not unique) tag is never matched to an older order",
          oid != "O1", f"returned={oid!r}")


if __name__ == "__main__":
    for t in (t_normal_order_costs_nothing, t_answer_lost_single_relay, t_answer_lost_two_relays,
              t_clear_failures_unchanged, t_really_lost, t_late_arrival, t_gtt, t_direct_mode, t_callers_tag):
        try:
            t()
        except Exception as e:
            import traceback
            FAILS.append(t.__name__)
            print(f"[ERROR] {t.__name__}: {e!r}\n{traceback.format_exc()[-500:]}")
    print(f"\n{PASSED[0]} passed, {len(FAILS)} failed")
    if FAILS:
        sys.exit(1)
    print("ALL ORDER-ONCE CHECKS PASSED")
