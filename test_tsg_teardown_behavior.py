#!/usr/bin/env python3
"""
Behavioral simulation for TSG_ENTRY_TEARDOWN_20260825 +
TSG_ROUTER_CONTRACT_20260825. Run from repo root AFTER both apply scripts.

Extracts the patched methods from tsg_manager.py via AST (no app.* imports
needed) and the patched ExecutionRouter, then replays:

  T1  2026-08-25 incident, unpatched-router shape: executor raises
      AttributeError on fresh_buy_entry_limit. PASS = order gets CANCELLED
      anyway (TSG-side hardening alone prevents the orphan).
  T2  Patched router over an executor missing fresh_buy_entry_limit:
      router degrades to None, loop exhausts, order CANCELLED.
  T3  Cancel races a COMPLETE fill: leg is ADOPTED (ok=True, broker avg).
  T4  Cancel + readback cannot reach a terminal state:
      TSG_ENTRY_ORDER_UNRESOLVED alert fires (no more silent filled=0).
  T5  _place_and_confirm: exception AFTER order_id exists ->
      cancel/readback runs (no walk-away).
  T6  Router forwards: cancel_order(symbol=) TypeError fallback,
      modify_order/fresh_* degrade-to-None on a minimal executor.
  T7  Happy path regression: order fills on first slice -> ok, avg, no
      cancel issued (behavior unchanged).
"""
import ast
import sys
import time as _time
import types
from pathlib import Path

MGR = Path("backend/app/engine/tsg/tsg_manager.py")
RTR = Path("backend/app/execution/execution_router.py")

# ---------------------------------------------------------------- harness --
LOGS, ALERTS = [], []


def _log(msg):
    LOGS.append(msg)


def extract_methods(path, names):
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
               and any(isinstance(m, ast.FunctionDef) and m.name in names
                       for m in n.body))
    out = {}
    for m in cls.body:
        if isinstance(m, ast.FunctionDef) and m.name in names:
            mod = ast.Module(body=[m], type_ignores=[])
            ns = {"time": _time, "write_audit_log": _log,
                  "TsgLeg": object, "Optional": object, "threading": None}
            import threading as _th
            ns["threading"] = _th
            exec(compile(ast.fix_missing_locations(mod), str(path), "exec"),
                 ns)
            out[m.name] = ns[m.name]
    missing = set(names) - set(out)
    assert not missing, f"methods not found in {path}: {missing}"
    return out


class Leg:
    def __init__(self, is_short=False):
        self.leg_id, self.symbol = "L4", "NIFTY26AUG24000PE"
        self.qty, self.is_short = 65, is_short
        self.entry_order_id = None


class Mgr:
    """Minimal host for the extracted methods."""
    def __init__(self, executor, methods):
        self.executor = executor
        self._token_by_sym = {"NIFTY26AUG24000PE":
                              {"instrument_token": 12345}}
        for k, f in methods.items():
            setattr(self, k, types.MethodType(f, self))

    def _cfg(self):
        return {"entry_fill_timeout_s": 2, "entry_repeg_max": 1}

    def _alert(self, code, msg, *, severity="warning", mode=None):
        ALERTS.append((code, severity))


class BaseExec:
    """Order sits OPEN forever unless a scenario changes post-cancel state."""
    def __init__(self):
        self.cancelled = []
        self.post_cancel_status = "CANCELLED"

    def place_buy(self, symbol, token, qty):
        return ("OID-1", 0.0, qty)

    def get_order_fill(self, oid):
        st = (self.post_cancel_status if self.cancelled else "OPEN")
        avg = 4.5 if st == "COMPLETE" else 0.0
        return {"status": st,
                "filled_qty": 65 if st == "COMPLETE" else 0,
                "avg_price": avg}

    def cancel_order(self, oid, symbol=""):
        self.cancelled.append(oid)

    def modify_order(self, oid, price, symbol=""):
        return str(oid)


def run(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        sys.exit(1)


print("== extracting patched methods ==")
methods = extract_methods(MGR, ["_place_and_confirm",
                                "_confirm_entry_with_repeg",
                                "_cancel_and_readback"])

# Load patched router with a stubbed base_executor module.
sys.modules.setdefault("app", types.ModuleType("app"))
sys.modules.setdefault("app.execution", types.ModuleType("app.execution"))
be = types.ModuleType("app.execution.base_executor")
be.BaseOrderExecutor = object
sys.modules["app.execution.base_executor"] = be
rtr_ns = {}
exec(compile(RTR.read_text(encoding="utf-8"), str(RTR), "exec"),
     rtr_ns.setdefault("__builtins__", __builtins__) and rtr_ns or rtr_ns)
ExecutionRouter = rtr_ns["ExecutionRouter"]

_time.sleep, _real_sleep = (lambda s: None), _time.sleep  # fast-forward

# -------------------------------------------------------------------- T1 --
print("\nT1: incident replay — executor RAISES AttributeError on fresh_buy")
ex = BaseExec()


def _boom(symbol):
    raise AttributeError(
        "'ExecutionRouter' object has no attribute 'fresh_buy_entry_limit'")


ex.fresh_buy_entry_limit = _boom
ex.fresh_sell_entry_limit = _boom
m = Mgr(ex, methods)
res = m._place_and_confirm(Leg())
run("order was CANCELLED (orphan prevented)", ex.cancelled == ["OID-1"])
run("re-peg failure degraded, loop did not escape",
    any("re-peg step failed" in l for l in LOGS))
run("returned fail cleanly", res == {"ok": False, "avg": None,
                                     "filled_qty": 0})

# -------------------------------------------------------------------- T2 --
print("\nT2: patched router over executor with NO fresh_* at all")
LOGS.clear()


class BareExec(BaseExec):
    pass  # no fresh_*, no modify — router must degrade, never raise


ex = BareExec()
router = ExecutionRouter(ex)
m = Mgr(router, methods)
res = m._place_and_confirm(Leg())
run("router degraded fresh_* to None (no-fresh-quote log)",
    any("no fresh quote" in l for l in LOGS))
run("order was CANCELLED through router", ex.cancelled == ["OID-1"])

# -------------------------------------------------------------------- T3 --
print("\nT3: cancel races a COMPLETE fill — adopt the leg")
ex = BaseExec()
ex.fresh_buy_entry_limit = lambda s: None
ex.fresh_sell_entry_limit = lambda s: None
ex.post_cancel_status = "COMPLETE"
m = Mgr(ex, methods)
leg = Leg()
res = m._place_and_confirm(leg)
run("adopted: ok=True with broker avg",
    res["ok"] is True and res["avg"] == 4.5 and res["filled_qty"] == 65)
run("entry_order_id recorded on adoption", leg.entry_order_id == "OID-1")

# -------------------------------------------------------------------- T4 --
print("\nT4: no terminal state after cancel — LOUD alert, not silence")
ALERTS.clear()


class StuckExec(BaseExec):
    def get_order_fill(self, oid):
        return {"status": "OPEN", "filled_qty": 0, "avg_price": 0.0}


ex = StuckExec()
ex.fresh_buy_entry_limit = lambda s: None
ex.fresh_sell_entry_limit = lambda s: None
m = Mgr(ex, methods)
res = m._place_and_confirm(Leg())
run("TSG_ENTRY_ORDER_UNRESOLVED fired",
    ("TSG_ENTRY_ORDER_UNRESOLVED", "error") in ALERTS)
run("cancel was still attempted", ex.cancelled == ["OID-1"])

# -------------------------------------------------------------------- T5 --
print("\nT5: exception AFTER order_id exists in _place_and_confirm")
ex = BaseExec()
m = Mgr(ex, methods)


def _raise_confirm(self, leg, oid, limit_px):
    raise TypeError("simulated post-placement failure")


m._confirm_entry_with_repeg = types.MethodType(_raise_confirm, m)
res = m._place_and_confirm(Leg())
run("teardown ran (order CANCELLED, no walk-away)",
    ex.cancelled == ["OID-1"])

# -------------------------------------------------------------------- T6 --
print("\nT6: router forward semantics")


class LegacyCancelExec:
    def cancel_order(self, order_id):        # no symbol kwarg
        self.last = order_id


lex = LegacyCancelExec()
r = ExecutionRouter(lex)
r.cancel_order("X1", symbol="NIFTY")
run("cancel_order TypeError fallback reaches legacy executor",
    lex.last == "X1")
r2 = ExecutionRouter(object())
run("modify_order degrades to None", r2.modify_order("X", price=1.0) is None)
run("fresh_buy degrades to None", r2.fresh_buy_entry_limit("S") is None)
run("fresh_sell degrades to None", r2.fresh_sell_entry_limit("S") is None)
full = ExecutionRouter(BaseExec())
run("modify_order forwards when present",
    full.modify_order("X9", price=2.0, symbol="S") == "X9")

# -------------------------------------------------------------------- T7 --
print("\nT7: happy-path regression — instant fill")


class InstantExec(BaseExec):
    def get_order_fill(self, oid):
        return {"status": "COMPLETE", "filled_qty": 65, "avg_price": 7.6}


ex = InstantExec()
m = Mgr(ex, methods)
leg = Leg()
res = m._place_and_confirm(leg)
run("ok=True avg=7.6, no cancel issued",
    res == {"ok": True, "avg": 7.6, "filled_qty": 65}
    and ex.cancelled == [] and leg.entry_order_id == "OID-1")

_time.sleep = _real_sleep
print("\nALL BEHAVIORAL TESTS PASSED")
