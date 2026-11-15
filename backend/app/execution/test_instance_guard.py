# test_instance_guard.py — INSTANCE_GUARD_20261115 (tracker O7)
# A second Scalp Terminal trading the same Zerodha account is recognised from
# the order tags and new shorts are refused; exits are never blocked.
#   PYTHONPATH=backend python3 backend/app/execution/test_instance_guard.py
import io
import os
import sys
import types
import contextlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + "/app/engine")
sys.path.insert(0, ROOT + "/app/execution")
os.environ["SCALP_TEST_MODE"] = "1"
os.environ.pop("SCALP_INSTANCE_ID", None)

from app.execution import instance_guard as IG                       # noqa: E402

PASSED, FAILS, PAGES = [0], [], []
import app.api.telegram_api as _TG                                     # noqa: E402
_TG.notify_critical = lambda d: PAGES.append(d)


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print(f"[ok  ] {name}")
    else:
        FAILS.append(name)
        print(f"[MISS] {name}\n         -> {detail}")


IST = timezone(timedelta(hours=5, minutes=30))


def stamp(epoch):
    return datetime.fromtimestamp(epoch, IST).strftime("%Y-%m-%d %H:%M:%S")   # naive IST, as Kite sends it


def order(tag, epoch, oid="X1"):
    return {"order_id": oid, "tag": tag, "order_timestamp": stamp(epoch), "status": "COMPLETE"}


# ── the machine id
IG.reset_for_tests()
a = IG.iid()
IG._IID = None
check("the machine id is 3 letters/digits and the SAME after a restart", len(a) == 3 and a.isalnum() and IG.iid() == a,
      (a, IG.iid()))
me, other = IG.iid(), ("ZZZ" if IG.iid() != "ZZZ" else "YYY")
T0 = IG.STARTED + 60
tag_other = "ST" + other + "M0ABCDEF" + "01"
tag_me = "ST" + me + "M0ABCDEF" + "01"

IG.reset_for_tests(); PAGES.clear()
IG.observe([order(tag_me, T0)], now=T0)
check("own orders never count", IG.blocked(now=T0) is None and not PAGES)
IG.observe([order("ST" + "M0ABCDEF01", T0), order("MYMANUAL", T0), order("", T0)], now=T0)
check("untagged, manual and old-format tags never count", IG.blocked(now=T0) is None and not PAGES)
IG.observe([order(tag_other, IG.STARTED - 3600)], now=T0)
check("another machine's orders from BEFORE this app started do not count (old Mac, new Mac)",
      IG.blocked(now=T0) is None and not PAGES)
IG.observe([order(tag_other, T0)], now=T0)
why = IG.blocked(now=T0)
check("another machine's order placed while this app runs: entries refused", why and other in why, why)
check("…one critical page", len(PAGES) == 1 and "Close the app on one machine" in PAGES[0]["message"], PAGES)
IG.observe([order(tag_other, T0 + 30)], now=T0 + 30)
check("…not re-paged every read", len(PAGES) == 1, len(PAGES))
IG.observe([order(tag_other, T0 + 30)], now=T0 + 30 + IG.REPAGE_S)
check("…re-paged after 10 minutes while it lasts", len(PAGES) == 2, len(PAGES))
check("the block lifts 30 minutes after the other instance's last order",
      IG.blocked(now=T0 + 30 + IG.BLOCK_S + 1) is None)
IG.observe(None, now=T0); IG.observe([{"tag": None}], now=T0)
check("a broken read never raises", True)

# ── the real executor
import requests                                                        # noqa: E402,F401
import app.event_bus.audit_logger as AL                                # noqa: E402
AL.write_audit_log = lambda m: None
import app.execution.zerodha_executor as ZE                            # noqa: E402
ZE.write_audit_log = lambda m: None
ZE.time.sleep = lambda s: None


class Kite:
    VARIETY_REGULAR, EXCHANGE_NFO, ORDER_TYPE_LIMIT = "regular", "NFO", "LIMIT"
    TRANSACTION_TYPE_BUY, TRANSACTION_TYPE_SELL, PRODUCT_NRML = "BUY", "SELL", "NRML"
    api_key, access_token = "k", "t"

    def __init__(self):
        self.book, self.reads = [], 0

    def place_order(self, **kw):
        o = dict(kw, order_id=f"O{len(self.book) + 1}", status="OPEN",
                 order_timestamp=stamp(T0))
        self.book.append(o)
        return o["order_id"]

    def orders(self):
        self.reads += 1
        return [dict(o) for o in self.book]


def executor():
    k = Kite()
    ZE._load_relay_config = lambda: None
    bm = types.SimpleNamespace(is_trade_ready=lambda: True, refresh=lambda: None,
                               get_trade_kite=lambda: k, get_data_kite=lambda: k)
    ex = ZE.ZerodhaOrderExecutor(bm)
    ex._ensure_trading_enabled = lambda: None
    ex._get_lot_size = lambda kite, symbol, qty: 65
    ex._resolve_ltp = lambda symbol: 100.0
    return ex, k


IG.reset_for_tests(); PAGES.clear()
ex, k = executor()
ex.place_buy_exit("NIFTY26N1725000CE", 130, "T")
t = k.book[0].get("tag") or ""
check("app orders carry this machine's id in the tag (valid for Kite: <= 20, letters/digits)",
      t.startswith("ST" + IG.iid()) and len(t) == IG.TAG_LEN and t.isalnum(), t)
reads0 = k.reads
ex.place_buy_exit("NIFTY26N1725000CE", 130, "T")
check("a normal order makes NO extra broker read", k.reads == reads0, k.reads - reads0)
oid = ex.place_sell_entry("NIFTY26N1725000CE", 1, 130)
check("no other instance: short entries go out as usual", oid and k.book[-1]["transaction_type"] == "SELL", oid)
k.book.append({"order_id": "F1", "tag": tag_other, "order_timestamp": stamp(T0), "status": "COMPLETE",
               "transaction_type": "SELL", "tradingsymbol": "NIFTY26N1725000CE", "quantity": 130})
IG.STARTED = T0 - 60
ex.get_orders()                                       # any read of the book (fill check, flat proof …)
n = len(k.book)
err = None
try:
    ex.place_sell_entry("NIFTY26N1725000CE", 1, 130)
except Exception as e:
    err = e
check("another instance seen in the book: a short entry is refused, nothing sent",
      err is not None and "INSTANCE_GUARD" in str(err) and len(k.book) == n, f"err={err!r} sent={len(k.book) - n}")
ex.place_buy_exit("NIFTY26N1725000CE", 130, "T")
check("…an EXIT still goes out", len(k.book) == n + 1 and k.book[-1]["transaction_type"] == "BUY")
check("…and the account owner was paged", any("ANOTHER SCALP TERMINAL" in p["message"] for p in PAGES), PAGES)

# ── each live strategy: a refused short leaves nothing behind
import test_fleet_live as F                                            # noqa: E402
from app.execution import broker_truth as BT                            # noqa: E402

real_sell = F.FleetKite.place_sell_entry


def guarded(self, *a, **kw):
    why = IG.blocked()
    if why:
        raise RuntimeError(f"INSTANCE_GUARD: {why}")
    return real_sell(self, *a, **kw)


F.FleetKite.place_sell_entry = guarded
IG._FOREIGN["QQQ"] = __import__("time").time()
for name, enter in (("Icarus", "ic_enter"), ("Timberwolf", "t2_enter"), ("Tigris", "ts_enter"),
                    ("Velvet", "vet_enter")):
    BT.reset_app_orders()
    f = F.Fleet()
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            getattr(f, enter)()
        except Exception:
            pass
    k = f.kite
    sells = [o for o in k.orders if o["transaction_type"] == "SELL" and o.get("src", "APP") == "APP"]
    check(f"{name}: second instance active → no short opened, wings unwound, nothing working",
          k.flat() and not k.open_orders(),
          f"net={ {s[-7:]: q for s, q in k.net.items() if q} } working={len(k.open_orders())} sells={len(sells)}")
F.FleetKite.place_sell_entry = real_sell
IG.reset_for_tests()

print(f"\n{PASSED[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL INSTANCE-GUARD CHECKS PASSED")
