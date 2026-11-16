# test_exit_repeg_fast.py — EXIT_REPEG_FAST_20261116 (DEF-033)
# 16 Nov 09:16: Icarus's 26350 CE wing sell rested at 1.60; its re-price asked
# Kite for market depth, the call hung 7 s (client timeout), then the modify
# hit an order that had already filled. Here, with the REAL ZerodhaOrderExecutor
# and the REAL exit driver (real time, scaled down: cap 0.2 s, hang 1.0 s):
#   * an exit re-price never waits on depth longer than the cap — it uses a
#     fresh live tick, or keeps the limit when there is none;
#   * a depth answer inside the cap prices EXACTLY like before;
#   * after a slow quote the driver reads the order first and never modifies
#     a filled / dead order;
#   * fast quotes: same re-pegs, same modifies, no extra broker calls;
#   * entries keep their own quote path; the router degrades for other brokers.
#   PYTHONPATH=backend python3 backend/app/execution/test_exit_repeg_fast.py
import os
import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("SCALP_TEST_MODE", "1")

import app.event_bus.audit_logger as AL                               # noqa: E402
AL.write_audit_log = lambda m: None
import app.execution.zerodha_executor as ZE                           # noqa: E402
import app.execution.exit_order_driver as XD                          # noqa: E402
from app.execution.execution_router import ExecutionRouter            # noqa: E402
from app.marketdata.ltp_store import LTPStore                         # noqa: E402

LOG = []
ZE.write_audit_log = lambda m: LOG.append(m)
PASSED, FAILS = [0], []
S = "NIFTY26N1726350CE"
CAP, HANG = 0.5, 2.0       # scaled: production cap 1.5 s, Kite timeout 7 s
SLACK = 0.35                # thread start / scheduler jitter (16 Nov: 0.21 s on a MacBook Air)


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print(f"[ok  ] {name}")
    else:
        FAILS.append(name)
        print(f"[MISS] {name}\n         -> {detail}")


class Kite:
    def __init__(self):
        self.q = {"ltp": 1.6, "bid": 1.55, "ask": 1.65}
        self.hang = 0.0
        self.quote_calls = 0

    def quote(self, k):
        self.quote_calls += 1
        if self.hang:
            time.sleep(self.hang)
            raise RuntimeError("HTTPSConnectionPool(host='api.kite.trade'): Read timed out. (read timeout=7)")
        dep = {"buy": [{"price": self.q["bid"]}], "sell": [{"price": self.q["ask"]}]}
        return {k: {"last_price": self.q["ltp"], "depth": dep}}

    def ltp(self, k):
        if self.hang:
            time.sleep(self.hang)
            raise RuntimeError("Read timed out")
        return {k: {"last_price": self.q["ltp"]}}


def executor():
    k = Kite()
    ZE._load_relay_config = lambda: None
    bm = types.SimpleNamespace(is_trade_ready=lambda: True, refresh=lambda: None,
                               get_trade_kite=lambda: k, get_data_kite=lambda: k)
    ex = ZE.ZerodhaOrderExecutor(bm)
    ex.EXIT_QUOTE_CAP_S = CAP
    return ex, k


def tick(px, age=0.0):
    LTPStore.update(S, px)
    LTPStore._timestamps[S] = time.time() - age


def timed(fn, *a):
    t0 = time.time()
    r = fn(*a)
    return r, time.time() - t0


# ── Q1: depth answers in time → exactly the old pricing ───────────────────
ex, k = executor()
tick(1.6)
for side in ("buy", "sell"):
    new, dt = timed(getattr(ex, f"fresh_{side}_exit_limit"), S)
    old = getattr(ex, f"fresh_{side}_entry_limit")(S)
    check(f"Q1 {side}: depth in time — same limit/ref/source as before ({new})", new == old and dt < CAP + SLACK,
          f"{new} vs {old}, {dt:.2f}s")

# ── Q2: depth hangs, fresh tick → priced from the tick within the cap ─────
k.hang = HANG
tick(1.5)
r, dt = timed(ex.fresh_sell_exit_limit, S)
check(f"Q2 depth hangs {HANG}s: SELL exit re-price back in {dt:.2f}s (cap {CAP}s) from the live tick",
      r is not None and r[1] == 1.5 and r[2] == "ltp" and dt < CAP + SLACK, f"{r} {dt:.2f}s")
check("Q2 the slow depth call is logged", any("EXIT_QUOTE" in m and "live tick 1.5" in m for m in LOG), LOG[-3:])
r, dt = timed(ex.fresh_buy_exit_limit, S)
check("Q2b BUY exit (short buy-back) the same", r is not None and r[1] == 1.5 and dt < CAP + SLACK, f"{r} {dt:.2f}s")

# ── Q3: depth hangs, tick stale → None (keep the limit), still within the cap
tick(1.5, age=5.0)
r, dt = timed(ex.fresh_sell_exit_limit, S)
check("Q3 depth hangs, tick 5 s old: no re-price this round (None), back within the cap",
      r is None and dt < CAP + SLACK, f"{r} {dt:.2f}s")

# ── Q4: the entry quote path is unchanged (still waits on depth) ──────────
r, dt = timed(ex.fresh_sell_entry_limit, S)
check("Q4 entries untouched: entry re-price still uses the full depth call", dt >= HANG - 0.05, f"{dt:.2f}s")
k.hang = 0.0
time.sleep(HANG + 0.1)          # let the abandoned threads finish


# ── Driver tests (real driver, real time, a scripted order) ───────────────
class Ex:
    """Executor seen by the driver: one working order, scripted quotes."""
    def __init__(self, quote_s=0.0, fill_after_s=None, quote=(1.55, 1.55, "bid"), exit_api=True):
        self.t0 = time.time()
        self.quote_s, self.fill_after_s, self.q = quote_s, fill_after_s, quote
        self.modifies, self.reads, self.cancels, self.quotes = [], 0, 0, 0
        self.attempts = []
        if exit_api:
            self.fresh_sell_exit_limit = self._fresh
        self.fresh_sell_entry_limit = self._fresh_entry
        self.entry_quotes = 0

    def _fresh(self, sym):
        self.quotes += 1
        time.sleep(self.quote_s)
        return self.q

    def _fresh_entry(self, sym):
        self.entry_quotes += 1
        return self.q

    def get_order_fill(self, oid):
        self.reads += 1
        if self.fill_after_s is not None and time.time() - self.t0 >= self.fill_after_s:
            return {"status": "COMPLETE", "avg_price": 1.6, "filled_qty": 195}
        return {"status": "OPEN", "filled_qty": 0}

    def modify_order(self, oid, price, symbol=""):
        self.attempts.append(price)
        if self.fill_after_s is not None and time.time() - self.t0 >= self.fill_after_s:
            raise RuntimeError("The order for ID does not exist.")
        self.modifies.append(price)
        return True

    def cancel_order(self, oid, symbol=""):
        self.cancels += 1


def drive(ex, **kw):
    t0 = time.time()
    r = XD.drive_exit_order(ex, "O1", symbol=S, side="SELL", chase_s=kw.get("chase", 3.0),
                            repeg_every_s=0.1, poll_s=0.05, settle_s=0.2, log=LOG.append,
                            start_px=1.6)
    return r, time.time() - t0


# D1: the 16 Nov case — quote slow (longer than SLOW_QUOTE_S), order fills meanwhile
XD.SLOW_QUOTE_S = 0.3
ex = Ex(quote_s=0.5, fill_after_s=0.3)
r, dt = drive(ex)
check("D1 16 Nov replay: order filled during a slow quote → FILLED, NO modify attempted",
      r.filled and r.avg_price == 1.6 and not ex.attempts, f"{r} attempts={ex.attempts}")
check("D1 result right after the slow quote (no extra wait)", dt < 0.5 + 0.3, f"{dt:.2f}s")

# D2: slow quote, order still open → modify still sent (re-price not skipped)
ex = Ex(quote_s=0.4, fill_after_s=None)
r, dt = drive(ex, chase=0.6)
check("D2 slow quote, order still open: re-priced (modify sent)", bool(ex.modifies), f"{ex.modifies}")

# D3: fast quotes → behaviour as before: modifies sent, no extra order reads
ex_new = Ex(quote_s=0.0, fill_after_s=0.55)
r_new, _ = drive(ex_new)
ex_old = Ex(quote_s=0.0, fill_after_s=0.55)
_orig = XD.SLOW_QUOTE_S
XD.SLOW_QUOTE_S = 1e9                       # old behaviour: never re-read after a quote
r_old, _ = drive(ex_old)
XD.SLOW_QUOTE_S = _orig
check("D3 fast quotes: same outcome and same re-peg prices as before",
      r_new.filled and r_old.filled and ex_new.modifies[:3] == ex_old.modifies[:3], f"{ex_new.modifies} vs {ex_old.modifies}")
check("D3 fast quotes: no extra order-status reads per re-peg",
      abs(ex_new.reads - ex_old.reads) <= 1, f"{ex_new.reads} vs {ex_old.reads}")

# D4: driver picks the exit quote; an executor without it falls back to the entry quote
ex = Ex(quote_s=0.0, fill_after_s=0.25)
drive(ex)
check("D4 driver uses the exit quote (fresh_sell_exit_limit)", ex.quotes > 0 and ex.entry_quotes == 0,
      f"exit {ex.quotes} entry {ex.entry_quotes}")
ex = Ex(quote_s=0.0, fill_after_s=0.25, exit_api=False)
drive(ex)
check("D4b executor without it (Angel/Dhan/fakes): entry quote as before", ex.entry_quotes > 0)

# R1: router forwards; degrades to the entry quote
inner = types.SimpleNamespace(fresh_sell_entry_limit=lambda s: ("E", s), fresh_buy_entry_limit=lambda s: ("EB", s))
rt = ExecutionRouter.__new__(ExecutionRouter)
rt._executor = inner
check("R1 router: executor without exit quotes → entry quote", rt.fresh_sell_exit_limit(S) == ("E", S)
      and rt.fresh_buy_exit_limit(S) == ("EB", S))
inner.fresh_sell_exit_limit = lambda s: ("X", s)
check("R1b router: exit quote forwarded when present", rt.fresh_sell_exit_limit(S) == ("X", S))

# T1: end-to-end timing with the REAL executor behind the REAL driver —
#     depth hangs, the wing fills at 1.60 ~0.3 s in. Old: the driver sat in
#     the hung call; new: it returns about one cap after the fill.
ex2, k2 = executor()
k2.hang = HANG
tick(1.6)
t_fill = time.time() + 0.3


class Wrap:
    def __init__(self, z):
        self.z = z

    def fresh_sell_exit_limit(self, s):
        return self.z.fresh_sell_exit_limit(s)

    def fresh_sell_entry_limit(self, s):          # what the old driver used
        return self.z.fresh_sell_entry_limit(s)

    def get_order_fill(self, oid):
        return {"status": "COMPLETE", "avg_price": 1.6, "filled_qty": 195} if time.time() >= t_fill \
            else {"status": "OPEN"}

    def modify_order(self, oid, price, symbol=""):
        if time.time() >= t_fill:
            raise RuntimeError("The order for ID does not exist.")
        return True

    def cancel_order(self, oid, symbol=""):
        pass


XD.SLOW_QUOTE_S = 0.15
r, dt = drive(Wrap(ex2))
check(f"T1 real executor, depth hanging {HANG}s: FILLED in {dt:.2f}s (old path: ≥ {HANG}s)",
      r.filled and dt < HANG - 0.2, f"{r} {dt:.2f}s")
time.sleep(HANG + 0.1)

print(f"\n{PASSED[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL EXIT-REPEG-FAST CHECKS PASSED")
