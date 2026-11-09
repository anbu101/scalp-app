# backend/app/engine/test_entry_fast_confirm.py
# ── ENTRY_FAST_CONFIRM_20261109 ── live entry legs are confirmed every 0.3 s
# (TMA_V2 / IC_V2 were 2 s, TSG_V1 / VET_V1 1 s), and Timberwolf's margin
# pre-check runs at signal time instead of at the boundary.
#   cd backend && PYTHONPATH=$PWD python3 app/engine/test_entry_fast_confirm.py
import os, sys, tempfile, time
from types import SimpleNamespace
os.environ["HOME"] = tempfile.mkdtemp(prefix="fast_confirm_")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
OK, FAILS = [0], []


def check(n, c, d=""):
    if c:
        OK[0] += 1; print(f"  PASS  {n}")
    else:
        FAILS.append(n); print(f"  FAIL  {n}  [{str(d)[:300]}]")


class Exe:
    """An order that turns COMPLETE `after` seconds after placement."""
    def __init__(self, after=0.35, price=153.2):
        self.t0, self.after, self.price, self.polls = time.time(), after, price, 0

    def get_order_fill(self, oid):
        self.polls += 1
        done = time.time() - self.t0 >= self.after
        return {"status": "COMPLETE" if done else "OPEN", "avg_price": self.price, "filled_qty": 65 if done else 0}


from app.engine.tma2 import tma2_trade_manager as TM          # noqa: E402
from app.engine.ic import ic_group_manager as GM              # noqa: E402
from app.engine.tsg import tsg_manager as TS                  # noqa: E402
from app.engine.vet import vet_manager as VM                  # noqa: E402

print("── poll cadence")
check("TMA_V2 polls every 0.3 s (was 2)", TM._ENTRY_FILL_POLL_S == 0.3)
check("IC_V2 polls every 0.3 s (was 2)", GM._ENTRY_FILL_POLL_S == 0.3)
check("TSG_V1 polls every 0.3 s (was 1)", TS._ENTRY_POLL_S == 0.3)
check("VET_V1 drives orders with poll_s 0.3 (driver default 1)", VM.ORDER_POLL_S == 0.3)

e = Exe(0.35); t = time.time()
px = TM.TMA2TradeManager._confirm_fill(SimpleNamespace(executor=e, _sid="TMA_V2"), "o1")
check("TMA_V2: a fill 0.35 s after placement is seen within 0.7 s (was ≥ 2 s)", px == 153.2 and time.time() - t < 0.7,
      time.time() - t)
e = Exe(0.35); t = time.time()
px = GM.ICGroupManager._confirm_fill(SimpleNamespace(executor=e, strategy_id="IC_V2"), "o2") \
    if hasattr(GM, "ICGroupManager") else None
if px is None:
    cls = next(v for v in vars(GM).values() if isinstance(v, type) and hasattr(v, "_confirm_fill"))
    px = cls._confirm_fill(SimpleNamespace(executor=e, strategy_id="IC_V2"), "o2")
check("IC_V2: same, within 0.7 s", px == 153.2 and time.time() - t < 0.7, time.time() - t)

logs = []
TS.write_audit_log = lambda m, *a, **k: logs.append(str(m))
e = Exe(1.6); t = time.time()
leg = SimpleNamespace(leg_id="L1", qty=65, is_short=True, symbol="NIFTY26N1724900CE", action="SELL")
tsg_self = SimpleNamespace(_cfg=lambda: {"entry_fill_timeout_s": 5, "entry_repeg_max": 3}, executor=e)
res = TS.TsgManager._confirm_entry_with_repeg(tsg_self, leg, "o3", 120.0) if hasattr(TS, "TsgManager") else \
    next(v for v in vars(TS).values() if isinstance(v, type) and hasattr(v, "_confirm_entry_with_repeg")) \
    ._confirm_entry_with_repeg(tsg_self, leg, "o3", 120.0)
el = time.time() - t
waits = [l for l in logs if "[TSG][ENTRY_WAIT]" in l]
check("TSG_V1: a fill at 1.6 s is seen within 2.0 s (was up to 2.6 s)", res["ok"] and el < 2.0, el)
check("…the ENTRY_WAIT heartbeat stays at about one line a second (+ the COMPLETE line)", 2 <= len(waits) <= 4, len(waits))
check("…and it polled about every 0.3 s", e.polls >= 5, e.polls)
e = Exe(0.35); t = time.time()
tcls = next(v for v in vars(TS).values() if isinstance(v, type) and hasattr(v, "_confirm_fill") and hasattr(v, "_confirm_entry_with_repeg"))
check("TSG_V1 _confirm_fill also polls fast", tcls._confirm_fill(SimpleNamespace(executor=e), "o4") == 153.2
      and time.time() - t < 0.7)

seen = {}
VM.drive_exit_order = lambda *a, **k: seen.update(k) or SimpleNamespace(outcome="FILLED")
vcls = next(v for v in vars(VM).values() if isinstance(v, type) and hasattr(v, "_drive"))
vcls._drive(SimpleNamespace(executor=None, _sleep=time.sleep, _clock=time.time), "o5", "X", "BUY", chase_s=15, repeg=True)
check("VET_V1 _drive passes poll_s=0.3 to the order driver", seen.get("poll_s") == 0.3, seen)

print("── Timberwolf margin pre-check")
src = open(TM.__file__, encoding="utf-8").read()
i_pend = src.index('"hedge_fallback": hedge_fb}')
check("pre-check sits right after the pending signal is recorded", 0 < src.index('snap["_margin_pre"] = bool(self._margin_ok(', i_pend) - i_pend < 900)
calls = []


class Mgr:
    _sid = "TMA_V2"
    diag = {"skipped_margin": 0}
    _live_block = None

    def _margin_ok(self, *a):
        calls.append(a)
        return True


TM.record_alert = lambda *a, **k: None
TM.notify_critical = lambda *a, **k: None
m = Mgr()
snap = {"margin_guard": True, "_margin_pre": False}
r = TM.TMA2TradeManager._enter_live(m, {}, snap, None, "S", "H", 65, 65, {}, {}, model_sell=1, model_hedge=1, hedge_fb=False)
check("shortfall found at signal time → blocked at the boundary, no second margin call",
      r is False and not calls and m.diag["skipped_margin"] == 1 and m._live_block == "MARGIN")


class Boom:
    def place_buy(self, *a):
        raise RuntimeError("stop here")


m2 = Mgr(); m2.executor = Boom(); calls.clear()
r = TM.TMA2TradeManager._enter_live(m2, {}, {"margin_guard": True, "_margin_pre": True}, None, "S", "H", 65, 65,
                                    {"token": 1}, {"token": 2}, model_sell=1, model_hedge=1, hedge_fb=False)
check("pre-check ok → goes straight to the hedge order, no margin call at the boundary", not calls and r is False)
m3 = Mgr(); m3.executor = Boom(); calls.clear()
TM.TMA2TradeManager._enter_live(m3, {}, {"margin_guard": True}, None, "S", "H", 65, 65, {"token": 1}, {"token": 2},
                                model_sell=1, model_hedge=1, hedge_fb=False)
check("no pre-check verdict (it failed / paper) → checked at the boundary as before", len(calls) == 1)
print(f"\n{OK[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL ENTRY FAST-CONFIRM CHECKS PASSED")
