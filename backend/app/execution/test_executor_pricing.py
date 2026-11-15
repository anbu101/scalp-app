# test_executor_pricing.py — tracker T1 (the rest: pricing, stop orders, timing)
# The REAL ZerodhaOrderExecutor with a fake Kite (orders, GTTs, quotes) and a
# simulated clock. What every live order is priced at, what is refused, what
# a stop GTT carries, and how long an EXIT takes when the price feed fails.
#   PYTHONPATH=backend python3 backend/app/execution/test_executor_pricing.py
import sys
import types
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import os                                                             # noqa: E402
os.environ.setdefault("SCALP_TEST_MODE", "1")

import app.event_bus.audit_logger as AL                               # noqa: E402
AL.write_audit_log = lambda m: None
import app.execution.zerodha_executor as ZE                           # noqa: E402
from app.marketdata.ltp_store import LTPStore                         # noqa: E402
LOG = []
ZE.write_audit_log = lambda m: LOG.append(m)

PASSED, FAILS = [0], []


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print(f"[ok  ] {name}")
    else:
        FAILS.append(name)
        print(f"[MISS] {name}\n         -> {detail}")


class Clock:
    t = 1_000_000.0


ZE.time.sleep = lambda s: setattr(Clock, "t", Clock.t + s)
ZE.time.time = lambda: Clock.t
import app.marketdata.ltp_store as LS                                 # noqa: E402
LS.time.time = lambda: Clock.t


def on_tick(x):
    return abs(round(x / 0.05) * 0.05 - x) < 1e-9


class Kite:
    VARIETY_REGULAR, EXCHANGE_NFO, ORDER_TYPE_LIMIT = "regular", "NFO", "LIMIT"
    TRANSACTION_TYPE_BUY, TRANSACTION_TYPE_SELL, PRODUCT_NRML = "BUY", "SELL", "NRML"
    GTT_TYPE_SINGLE = "single"
    api_key, access_token = "k", "t"

    def __init__(self):
        self.book, self.gtts = [], []
        self.quotes = {}                 # symbol -> dict(ltp, bid, ask)
        self.ltp_fail = 0                # next N REST ltp calls fail
        self.ltp_hang_s = 0.0            # each failing call takes this long (timeout)
        self.ltp_calls = 0

    def place_order(self, **kw):
        self.book.append(dict(kw, order_id=f"O{len(self.book) + 1}", t=Clock.t))
        return self.book[-1]["order_id"]

    def place_gtt(self, **kw):
        self.gtts.append(kw)
        return {"trigger_id": 900 + len(self.gtts)}

    def get_gtts(self):
        return []

    def orders(self):
        return [dict(o) for o in self.book]

    def ltp(self, k):
        self.ltp_calls += 1
        if self.ltp_fail > 0:
            self.ltp_fail -= 1
            Clock.t += self.ltp_hang_s
            raise RuntimeError("ReadTimeout: HTTPSConnectionPool read timed out")
        s = k.split(":", 1)[1]
        return {k: {"last_price": self.quotes[s]["ltp"]}} if s in self.quotes else {}

    def quote(self, k):
        s = k.split(":", 1)[1]
        q = self.quotes.get(s)
        if not q:
            return {}
        dep = {"buy": [{"price": q.get("bid", 0)}], "sell": [{"price": q.get("ask", 0)}]}
        return {k: {"last_price": q["ltp"], "depth": dep}}


def executor():
    k = Kite()
    ZE._load_relay_config = lambda: None
    bm = types.SimpleNamespace(is_trade_ready=lambda: True, refresh=lambda: None,
                               get_trade_kite=lambda: k, get_data_kite=lambda: k)
    ex = ZE.ZerodhaOrderExecutor(bm)
    ex._ensure_trading_enabled = lambda: None
    ex._get_lot_size = lambda kite, symbol, qty: 65
    return ex, k


S = "NIFTY26N1725000CE"
E = ZE.ZerodhaOrderExecutor

# ═══ pricing rules, over a sweep of premiums ═════════════════════════════════
rng = random.Random(7)
prices = [0.05, 0.10, 0.15, 0.95, 1.00, 2.45, 2.50, 4.95, 5.00, 24.95, 25.00, 25.05,
          49.95, 99.95, 150.0, 333.35] + [round(round(rng.uniform(0.05, 600) / 0.05) * 0.05, 2) for _ in range(300)]
bad = []
for p in prices:
    b, s = E._protected_limit_price(p, "BUY"), E._protected_limit_price(p, "SELL")
    if not (on_tick(b) and on_tick(s) and b >= p and s <= p and b <= p * 1.01 + 0.025 + 1e-9
            and s >= p * 0.99 - 0.025 - 1e-9 and s > 0):
        bad.append(("exit", p, b, s))
    eb, es = E._entry_limit_price(p, "BUY"), E._entry_limit_price(p, "SELL")
    buf = 0.02 if p >= 25 else 0.01
    if not (on_tick(eb) and on_tick(es) and eb >= p and es <= p and es > 0
            and eb <= p * (1 + buf) + 0.025 + 1e-9 and es >= p * (1 - buf) - 0.025 - 1e-9):
        bad.append(("entry", p, eb, es))
    w = E._wing_limit_price(p)
    if not (on_tick(w) and w >= p + min(max(1.0, p * 0.2), 3.0) - 1e-9 and w <= p + 3.05 + 1e-9):
        bad.append(("wing", p, w))
check(f"{len(prices)} premiums: every exit / entry / wing limit is on the 0.05 tick, on the "
      f"marketable side of its reference, never 0, and within its buffer", not bad, bad[:5])
check("entry buffer is tiered: ₹24.95 sells at −1%, ₹25 at −2%",
      E._entry_limit_price(24.95, "SELL") == 24.70 and E._entry_limit_price(25.0, "SELL") == 24.5,
      (E._entry_limit_price(24.95, "SELL"), E._entry_limit_price(25.0, "SELL")))

# ═══ short entry: priced off the best bid, LTP when there is no depth ═════════
ex, k = executor()
k.quotes[S] = {"ltp": 100.0, "bid": 98.0, "ask": 100.5}
oid, lim, q = ex.place_sell_entry(S, 1, 130)
o = k.book[-1]
check("short entry: SELL LIMIT off the BEST BID (98 → 96.05), NRML, NFO, qty as asked",
      lim == 96.05 and o["price"] == 96.05 and o["transaction_type"] == "SELL" and o["product"] == "NRML"
      and o["exchange"] == "NFO" and o["order_type"] == "LIMIT" and o["quantity"] == 130, o)
k.quotes[S] = {"ltp": 100.0, "bid": 0, "ask": 0}
_, lim, _ = ex.place_sell_entry(S, 1, 130)
check("short entry with no depth: off the LTP (100 → 98.00)", lim == 98.0, lim)
for qty, why in ((100, "not a lot multiple"), (0, "zero"), (ZE.MAX_QTY_PER_ORDER + 65, "above the per-order cap")):
    n = len(k.book)
    try:
        ex.place_sell_entry(S, 1, qty)
        err = None
    except Exception as e:
        err = e
    check(f"short entry refused, nothing sent: qty {qty} ({why})", err is not None and len(k.book) == n, err)
k.quotes.pop(S)
LTPStore._prices.pop(S, None)
n = len(k.book)
try:
    ex.place_sell_entry(S, 1, 130)
    err = None
except Exception as e:
    err = e
check("short entry with no price at all: refused, nothing sent", err is not None and len(k.book) == n, err)

# ═══ exits: buy-back and long sell ═══════════════════════════════════════════
ex, k = executor()
k.quotes[S] = {"ltp": 200.0, "bid": 199.0, "ask": 201.0}
ex.place_buy_exit(S, 130, "SL")
o = k.book[-1]
check("buy-back: BUY LIMIT at REST LTP + 1% (200 → 202.00), NRML, qty as asked",
      o["price"] == 202.0 and o["transaction_type"] == "BUY" and o["quantity"] == 130 and o["product"] == "NRML", o)
ex.place_market_sell(S, 130)
o = k.book[-1]
check("long sell: SELL LIMIT at REST LTP − 1% (200 → 198.00)", o["price"] == 198.0 and o["transaction_type"] == "SELL", o)
n = len(k.book)
try:
    ex.place_buy_exit(S, 100, "SL")
    err = None
except Exception as e:
    err = e
check("buy-back of a qty that is not a lot multiple: refused, nothing sent", err is not None and len(k.book) == n)
ex.place_buy_exit(S, 1950, "EOD")
check("a buy-back is NOT refused above the per-order cap (an exit must go; slicing is the caller's)",
      k.book[-1]["quantity"] == 1950, k.book[-1]["quantity"])

# ═══ stop GTTs ════════════════════════════════════════════════════════════════
ex, k = executor()
gid = ex.place_gtt_sl_only_short(S, 130, 142.0, last_price=100.0, limit_buffer=1.05)
g = k.gtts[-1]
check("short's stop GTT: BUY, trigger 142.00, limit 149.10 (+5%), last price passed, NRML",
      g["trigger_values"] == [142.0] and g["orders"][0]["price"] == 149.1
      and g["orders"][0]["transaction_type"] == "BUY" and g["last_price"] == 100.0
      and g["orders"][0]["product"] == "NRML" and g["orders"][0]["quantity"] == 130, g)
for sl, last, why in ((100.0, 100.0, "stop AT the price"), (95.0, 100.0, "stop already crossed")):
    n = len(k.gtts)
    try:
        ex.place_gtt_sl_only_short(S, 130, sl, last_price=last)
        err = None
    except Exception as e:
        err = e
    check(f"short's stop GTT refused, nothing placed: {why} (caller exits instead)",
          err is not None and len(k.gtts) == n, err)
ex.place_gtt_sl_only_short(S, 130, 141.98, last_price=100.0, limit_buffer=1.05)
g = k.gtts[-1]
check("odd stop price 141.98 → trigger on the tick (142.00), limit above it",
      g["trigger_values"] == [142.0] and g["orders"][0]["price"] > 142.0, g["trigger_values"])
ex.place_gtt_sl_only_short(S, 130, 5.0, last_price=3.0)
g = k.gtts[-1]
check("default buffer (+0.3%) on a ₹5 stop: the limit EQUALS the trigger — it rests on any gap "
      "(Icarus passes its own wider buffer)", g["orders"][0]["price"] == 5.0, g["orders"][0]["price"])
ex.place_gtt_sl_only_long(S, 130, 20.0, last_price=30.0, limit_buffer=1.05)
g = k.gtts[-1]
check("long's stop GTT: SELL, trigger 20.00, limit BELOW it (19.05)",
      g["trigger_values"] == [20.0] and g["orders"][0]["price"] == 19.05
      and g["orders"][0]["transaction_type"] == "SELL", g)
ex.place_gtt_sl_only_long(S, 130, 0.05, last_price=1.0, limit_buffer=1.5)
check("long's stop at the minimum: limit never below 0.05", k.gtts[-1]["orders"][0]["price"] == 0.05)


# ═══ timing: how long an EXIT waits when the price feed fails ═════════════════
def exit_time(hang_s, fail, store_age=None, store_px=200.0):
    ex, k = executor()
    k.quotes[S] = {"ltp": 200.0, "bid": 199.0, "ask": 201.0}
    k.ltp_fail, k.ltp_hang_s = fail, hang_s
    if store_age is None:
        LTPStore._prices.pop(S, None)
    else:
        LTPStore.update(S, store_px)
        LTPStore._timestamps[S] = Clock.t - store_age
    t0 = Clock.t
    err = None
    try:
        ex.place_buy_exit(S, 130, "SL")
    except Exception as e:
        err = e
    return Clock.t - t0, err, (k.book[-1]["price"] if k.book else None)


dt, err, px = exit_time(0, 0)
check("normal buy-back: sent at once (no wait)", dt == 0 and err is None and px == 202.0, (dt, err, px))
ex, k = executor()
k.quotes[S] = {"ltp": 200.0, "bid": 199.0, "ask": 201.0}
LTPStore.update(S, 201.0)
c0 = k.ltp_calls
ex.place_buy_exit(S, 130, "SL")
check("buy-back with a FRESH live tick: priced off the tick (201 → 203.00), NO REST price call",
      k.book[-1]["price"] == 203.0 and k.ltp_calls == c0, (k.book[-1]["price"], k.ltp_calls - c0))
LTPStore._timestamps[S] = Clock.t - 3.0
ex.place_buy_exit(S, 130, "SL")
check("…a tick 3 s old is not fresh: REST price as before (202.00)",
      k.book[-1]["price"] == 202.0 and k.ltp_calls == c0 + 1, (k.book[-1]["price"], k.ltp_calls - c0))
LTPStore.update(S, 201.0)
c0 = k.ltp_calls
ex.place_market_sell(S, 130)
check("long sell with a fresh tick: off the tick (201 → 199.00), no REST call",
      k.book[-1]["price"] == 199.0 and k.ltp_calls == c0, (k.book[-1]["price"], k.ltp_calls - c0))
LTPStore._prices.pop(S, None)
dt, err, px = exit_time(0.0, 1)
check("REST price fails once (fast error): sent after the retry pause only",
      err is None and dt <= 0.5, f"waited {dt:.1f}s")
EXIT_WAIT = {}
for hang, fail, age in ((7.0, 3, 0.5), (7.0, 1, 0.5), (0.0, 3, 0.5), (7.0, 3, None)):
    dt, err, px = exit_time(hang, fail, age)
    EXIT_WAIT[(hang, fail, age)] = (dt, err, px)
dt, err, px = EXIT_WAIT[(7.0, 3, 0.5)]
check("REST price HANGS (7 s timeouts) while the live tick price is fresh: the buy-back is still sent "
      "within 2 s (an SL exit must not wait on a dead price feed)",
      err is None and dt <= 2.0, f"waited {dt:.1f}s before sending, price={px}")
dt, err, px = EXIT_WAIT[(0.0, 3, 0.5)]
check("REST price errors fast 3× with a fresh tick price: sent within 1 s",
      err is None and dt <= 1.0, f"waited {dt:.1f}s")
dt, err, px = EXIT_WAIT[(7.0, 3, None)]
check("REST hangs and NO tick price at all: refused with a clear error (the caller retries) — not a guess",
      err is not None and "LTP unavailable" in str(err), f"waited {dt:.1f}s err={err!r}")
dt, err, px = exit_time(0.0, 3, store_age=600, store_px=50.0)
check("REST fails and the tick price is 10 minutes old: a stale price is used only as before "
      "(the exit driver re-prices off the live book within seconds)", err is None and px is not None,
      f"waited {dt:.1f}s px={px}")

print("\nexit wait when REST hangs:", {f"{k}": f"{v[0]:.1f}s" for k, v in EXIT_WAIT.items()})
print(f"\n{PASSED[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL EXECUTOR PRICING CHECKS PASSED")
