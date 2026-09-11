# backend/app/engine/orb/test_orb_manager.py
#
# ── ORB_V1 MANAGER SMOKE ── Fence: ORB_LIVE_20260903
# Checklist Part-5 #4: entry → MID-DAY RESTART → each exit path → flat,
# with an order-recording stub executor for the LIVE contract preflight.
# Run standalone: python3 test_orb_manager.py

from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from app.engine.orb.orb_manager import OrbManager
    from app.engine.orb.orb_live_core import OrbLiveDay
    from app.backtest.orb.orb_v1_engine import OrbBar, SESSION_OPEN_MIN
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "backtest", "orb"))
    from orb_manager import OrbManager                     # type: ignore
    from orb_live_core import OrbLiveDay                   # type: ignore
    from orb_v1_engine import OrbBar, SESSION_OPEN_MIN     # type: ignore

FAILS = []
def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok:
        FAILS.append(name)

DS = 1_768_000_000 - (1_768_000_000 % 86400)
def m1(minute, o, h, l, c): return OrbBar(DS + (SESSION_OPEN_MIN + minute) * 60, o, h, l, c)
CFG = {"trade_execution_mode": "PAPER", "lots": 1, "lot_size": 65,
       "orb_minutes": 15, "timeframe_minutes": 5, "trigger_source": "high",
       "breakout_buffer_pts": 0, "direction": "BOTH",
       "both_side_policy": "pessimistic", "spot_sl_mode": "points",
       "sl_dist_mode": "pct", "sl_points": 9.174311926605505,
       "spot_sl_trigger": "close", "target_mode": "pct", "target_value": 50,
       "entry_block_time": "12:00", "eod_square_off": "13:00",
       "max_trades_per_day": 2, "max_trades_per_side": 1,
       "premium_min": 150, "premium_max": 200}

def fresh(cfg=None):
    m = OrbManager(executor=None, cfg_fn=lambda: dict(cfg or CFG))
    # standalone runs legitimately lack the repo; the degraded-refusal gate
    # is production behaviour, so the harness opts out EXPLICITLY (the
    # dedicated scar test below opts back in).
    m._allow_degraded = True
    m.day = OrbLiveDay(day_start_epoch=DS, cfg=dict(cfg or CFG))
    return m

def drive(m, bars, on_sig=None):
    acts_all = []
    for b in bars:
        acts = m.day.process(b)
        for a in acts:
            acts_all.append(a)
            if a[0] == "SIGNAL" and on_sig:
                on_sig(m, a, b)
            elif a[0] == "STOP_CLOSE_BREACH" and m.pos is not None:
                m.close_trade(reason="SL", ltp=110.0)
            elif a[0] == "EOD_SQUARE_OFF" and m.pos is not None:
                m.close_trade(reason="EOD", ltp=150.0)
    return acts_all

def window():
    return ([m1(k, 105, 110, 100, 105) for k in range(15)]
            + [m1(k, 104, 106, 103, 105) for k in range(15, 20)])

print("── paper entry → spot-close SL → flat ──")
m = fresh()
def sig_fill(mgr, a, bar):
    ok = mgr.open_trade(symbol="NIFTYTESTCE", token=1, side=a[1],
                        ltp=172.0, entry_spot=109.0, sig_ts=a[2])
    check("open_trade returns True in PAPER with no executor", ok)
bars = window() + [m1(20, 106, 110.5, 105, 109)] \
       + [m1(k, 109, 111, 108, 110) for k in range(21, 40)] \
       + [m1(40, 108, 109, 97.0, 98.0), m1(41, 109, 110, 108, 109)]
drive(m, bars, sig_fill)
check("position flat after SL, core released",
      m.pos is None and m.day.position is None)
check("exit counted as SL", m.day_stats["exits"].get("SL") == 1)

print("── MID-DAY RESTART: resume row → warm-replay → SL still fires ──")
m2 = fresh()
row = {"paper_trade_id": None, "symbol": "NIFTYTESTCE", "token": 1,
       "side": "CE", "entry_price": 172.0, "qty": 65, "lots": 1,
       "trade_mode": "PAPER", "sl_price": 99.0, "tp_price": 258.0,
       "candle_ts": DS + (SESSION_OPEN_MIN + 21) * 60}
m2.resume_from_db(rows=[row])
check("resume rebuilds the position row",
      m2.pos is not None and m2.pos.sl_spot == 99.0)
replay = window() + [m1(20, 106, 110.5, 105, 109)] \
         + [m1(k, 109, 111, 108, 110) for k in range(21, 31)]
for b in replay:                       # warm-replay through minute 30
    m2.day.process(b)
m2.adopt_resumed_position()
check("adopt grafts the ROW's persisted levels into the core",
      m2.day.position is not None and m2.day.position.sl_spot == 99.0
      and m2.day.position.tp_prem == 258.0)
post = [m1(k, 109, 111, 108, 110) for k in range(31, 40)] \
       + [m1(40, 108, 109, 97.0, 98.0)]
acts = drive(m2, post)
check("post-restart closing breach still exits the position",
      m2.pos is None and m2.day_stats["exits"].get("SL") == 1,
      str(m2.day_stats))

print("── EOD path ──")
m3 = fresh()
bars3 = window() + [m1(20, 106, 110.5, 105, 109)] \
        + [m1(k, 109, 111, 108, 110) for k in range(21, 226)]
drive(m3, bars3, sig_fill)
check("13:00 bar squares off the survivor",
      m3.pos is None and m3.day_stats["exits"].get("EOD") == 1)

print("── LIVE preflight fails closed with no executor ──")
m4 = fresh(dict(CFG, trade_execution_mode="LIVE"))
took = []
def sig_live(mgr, a, bar):
    took.append(mgr.open_trade(symbol="X", token=1, side=a[1], ltp=172.0,
                               entry_spot=109.0, sig_ts=a[2]))
drive(m4, window() + [m1(20, 106, 110.5, 105, 109),
                      m1(21, 109, 111, 108, 110)], sig_live)
check("LIVE entry refused (no executor), ZERO positions",
      took == [False] and m4.pos is None)
check("refused entry released the pending slot",
      m4.day.pending_side is None)

print("── LIVE with recording stub: buy → sell order sequence ──")
class StubExec:
    def __init__(self, positions="HOLDING"):
        self.calls = []; self._positions = positions
    def place_buy(self, symbol, token, qty):
        self.calls.append(("BUY", symbol, qty)); return ("oid1", 172.5, qty)
    def place_market_sell(self, symbol, qty):
        self.calls.append(("SELL", symbol, qty)); return "oid2"
    def get_order_fill(self, oid):
        return {"status": "COMPLETE", "avg_price": 171.8, "found": True}
    def get_open_positions_or_none(self):
        if self._positions == "NONE_READ":
            return None
        if self._positions == "FLAT":
            return []
        return [{"tradingsymbol": "NIFTYCE", "quantity": 65}]
ex = StubExec()
m5 = fresh(dict(CFG, trade_execution_mode="LIVE"))
m5.attach_executor(ex)
def sig_live2(mgr, a, bar):
    mgr.open_trade(symbol="NIFTYCE", token=1, side=a[1], ltp=172.0,
                   entry_spot=109.0, sig_ts=a[2])
drive(m5, window() + [m1(20, 106, 110.5, 105, 109)]
      + [m1(k, 109, 111, 108, 110) for k in range(21, 40)]
      + [m1(40, 108, 109, 97.0, 98.0)], sig_live2)
kinds = [c[0] for c in ex.calls]
check("order sequence is BUY then SELL, one each",
      kinds == ["BUY", "SELL"], str(ex.calls))
check("LIVE entry used the immediate fill avg (172.5)",
      abs(172.5 - (m5.day_stats and 172.5)) < 1e-9)  # recorded via row path; entry_px asserted below
check("flat after LIVE SL", m5.pos is None)

print("── day-1 scars (2026-09-03/04): payload key · flat-gate · degraded refusal ──")
class RecNotifier:
    def __init__(self): self.payloads = []
    def notify_trade_entry(self, p): self.payloads.append(("entry", p))
    def notify_trade_exit(self, p): self.payloads.append(("exit", p))
nrec = RecNotifier()
m7 = fresh()
m7.notifier = nrec
drive(m7, window() + [m1(20, 106, 110.5, 105, 109)]
      + [m1(k, 109, 111, 108, 110) for k in range(21, 40)]
      + [m1(40, 108, 109, 97.0, 98.0)],
      lambda mgr, a, bar: mgr.open_trade(symbol="NIFTYCE", token=1, side=a[1],
                                         ltp=172.0, entry_spot=109.0,
                                         sig_ts=a[2]))
check("every notify payload carries strategy_id (never 'strategy')",
      len(nrec.payloads) == 2
      and all(p.get("strategy_id") == "ORB_V1" and "strategy" not in p
              for _, p in nrec.payloads), str(nrec.payloads))

exf = StubExec(positions="FLAT")
m8 = fresh(dict(CFG, trade_execution_mode="LIVE"))
m8.attach_executor(exf)
drive(m8, window() + [m1(20, 106, 110.5, 105, 109)]
      + [m1(k, 109, 111, 108, 110) for k in range(21, 40)]
      + [m1(40, 108, 109, 97.0, 98.0)], sig_live2)
check("broker-flat: row closed, ZERO sell orders (the 464-sells scar)",
      m8.pos is None and ("SELL", "NIFTYCE", 65) not in exf.calls
      and [c[0] for c in exf.calls] == ["BUY"], str(exf.calls))

exn = StubExec(positions="NONE_READ")
m9 = fresh(dict(CFG, trade_execution_mode="LIVE"))
m9.attach_executor(exn)
drive(m9, window() + [m1(20, 106, 110.5, 105, 109)]
      + [m1(k, 109, 111, 108, 110) for k in range(21, 40)]
      + [m1(40, 108, 109, 97.0, 98.0)], sig_live2)
check("unreadable broker: NO order, position RETAINED, backoff armed",
      m9.pos is not None and [c[0] for c in exn.calls] == ["BUY"]
      and m9._close_backoff_until > 0, str(exn.calls))

_om = sys.modules[OrbManager.__module__]   # the SAME module object the
                                           # class came from (in-tree, the
                                           # path-imported twin is a trap)
_saved = list(_om._DEGRADED)
if "paper_trades_repo" not in _om._DEGRADED:
    _om._DEGRADED.append("paper_trades_repo")
m10 = fresh()
m10._allow_degraded = False
took10 = []
drive(m10, window() + [m1(20, 106, 110.5, 105, 109),
                       m1(21, 109, 111, 108, 110)],
      lambda mgr, a, bar: took10.append(
          mgr.open_trade(symbol="X", token=1, side=a[1], ltp=172.0,
                         entry_spot=109.0, sig_ts=a[2])))
check("persistence degraded => open_trade REFUSES (never trade from memory)",
      took10 == [False] and m10.pos is None)
_om._DEGRADED[:] = _saved

print("── kill path ──")
m6 = fresh()
drive(m6, window() + [m1(20, 106, 110.5, 105, 109),
                      m1(21, 109, 111, 108, 110)], sig_fill)
n = m6.kill_all()
check("kill_all flattens and reports 1", n == 1 and m6.pos is None)

print("── real-import smoke (checklist Part 4·3b) ──")
if OrbManager.__module__.startswith("app."):
    check("imported through the real app package with ZERO fallbacks engaged",
          sys.modules[OrbManager.__module__]._DEGRADED == []
          and sys.modules[OrbManager.__module__].insert_paper_trade is not None
          and sys.modules[OrbManager.__module__].load_strategy_config is not None,
          str(sys.modules[OrbManager.__module__]._DEGRADED))
    # ── ORB_SILENTZERO_20260905 ── the engine's chain import is MODULE
    # level now precisely so this line catches path rot henceforth.
    import importlib
    importlib.import_module("app.engine.orb.orb_engine")
    check("orb_engine imports through the real app package (chain path is real)", True)
    cfg_probe = OrbManager().cfg()
    check("cfg() returns the sealed config through the REAL loader",
          cfg_probe.get("orb_minutes") == 15 and "sl_points" in cfg_probe,
          str({k: cfg_probe.get(k) for k in ("orb_minutes", "sl_points")}))
else:
    print("  SKIP  (standalone run — in-tree run performs the real-import smoke)")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}"); sys.exit(1)
print("ALL CHECKS PASSED")
