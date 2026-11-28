# backend/app/execution/test_execution_router_forwarding.py
# ── ROUTER_FORWARDING_20261128 ── (TD-076)
#
# Every strategy places its live orders through ExecutionRouter, which forwards
# EXPLICITLY to the broker executor. test_execution_router_contract.py checks
# that each method strategies call EXISTS on the router; this suite checks what
# each forward DOES: the exact call it makes (positional or keyword, defaults),
# that the answer comes back untouched, which orders are noted as "placed by
# this app" (broker_truth), and how each forward degrades on an executor that
# lacks the method (Angel / Dhan / older builds / test fakes).
#
#   TC-EXEC-0293  every plain forward: exact executor call, answer passed back, noted or not
#   TC-EXEC-0294  place_gtt_oco: defaults, limit_buffer, executors that refuse limit_buffer
#   TC-EXEC-0295  single-leg GTTs: optional args sent only when set and accepted; missing → clear error
#   TC-EXEC-0296  degrade paths: verify-cancel, strict reads, modify, cancel(symbol), wing buy, re-peg quotes
#   TC-EXEC-0297  noting an order never breaks the order (broker_truth unavailable)
#
# Run: python3 test_execution_router_forwarding.py
import os
import sys
import types
from pathlib import Path

import pytest

os.environ["SCALP_TEST_MODE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.execution as _pkg                                           # noqa: E402
import app.event_bus.audit_logger as AL                                # noqa: E402
from app.execution.execution_router import ExecutionRouter             # noqa: E402

NOTED, AUDIT = [], []
S = "NIFTY26D0225000CE"


class Ret:
    """A distinct answer per call, so 'passed back untouched' is checkable."""
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"Ret({self.name})"


class Rec:
    """An executor with every method: records (name, args, kwargs), answers Ret(name)."""
    def __init__(self, only=None, without=()):
        self.calls = []
        self._only, self._without = only, set(without)

    def __getattr__(self, name):
        if name.startswith("__") or name in self._without or (self._only is not None and name not in self._only):
            raise AttributeError(name)

        def fn(*a, **k):
            self.calls.append((name, a, k))
            return Ret(name)
        return fn


@pytest.fixture(autouse=True)
def env(monkeypatch):
    NOTED.clear(); AUDIT.clear()
    bt = types.ModuleType("app.execution.broker_truth")
    bt.note_app_order = NOTED.append
    monkeypatch.setitem(sys.modules, "app.execution.broker_truth", bt)
    monkeypatch.setattr(_pkg, "broker_truth", bt, raising=False)
    monkeypatch.setattr(AL, "write_audit_log", AUDIT.append)


def route(ex=None, **kw):
    ex = ex or Rec(**kw)
    return ExecutionRouter(ex), ex


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0293 — plain forwards
# ════════════════════════════════════════════════════════════════════════════
PLAIN = [   # (router method, args, kwargs, executor method, expected args, expected kwargs, noted)
    ("place_buy", (S, 111, 65), {}, "place_buy", (S, 111, 65), {}, True),
    ("place_limit_buy", (S, 65, 12.5), {}, "place_limit_buy", (S, 65, 12.5), {"product": "MIS", "tag": ""}, True),
    ("place_limit_buy", (S, 65, 12.5), {"product": "NRML", "tag": "T1"}, "place_limit_buy", (S, 65, 12.5),
     {"product": "NRML", "tag": "T1"}, True),
    ("get_last_avg_price", ("O1",), {}, "get_last_avg_price", ("O1",), {}, False),
    ("cancel_gtt", ("901",), {}, "cancel_gtt", ("901",), {}, False),
    ("get_gtts", (), {}, "get_gtts", (), {}, False),
    ("get_basket_margin", ([{"symbol": S}],), {}, "get_basket_margin", ([{"symbol": S}],), {}, False),
    ("place_sl", (S, 65, 80.0), {}, "place_sl", (S, 65, 80.0), {}, False),
    ("place_exit", (S, 65, "SL"), {}, "place_exit", (), {"symbol": S, "qty": 65, "reason": "SL"}, True),
    ("place_market_sell", (S, 65), {}, "place_market_sell", (), {"symbol": S, "qty": 65}, True),
    ("place_sell_entry", (S, 111, 65), {}, "place_sell_entry", (), {"symbol": S, "token": 111, "qty": 65}, True),
    ("place_buy_exit", (S, 65, "TP"), {}, "place_buy_exit", (), {"symbol": S, "qty": 65, "reason": "TP"}, True),
    ("get_open_positions", (), {}, "get_open_positions", (), {}, False),
    ("get_order_fill", ("O1",), {}, "get_order_fill", ("O1",), {}, False),
    ("get_orders", (), {}, "get_orders", (), {}, False),
    ("resolve_symbol", (S,), {}, "resolve_symbol", (S,), {}, False),
    ("resolve_ltp", (S,), {}, "_resolve_ltp", (S,), {}, False),
    ("get_gtts_or_none", (), {}, "get_gtts_or_none", (), {}, False),
    ("gtt_last_state", ("901",), {}, "gtt_last_state", ("901",), {}, False),
    ("get_gtt_status", ("901",), {}, "get_gtt_status", ("901",), {}, False),
    ("get_open_positions_or_none", (), {}, "get_open_positions_or_none", (), {}, False),
    ("cancel_gtt_verified", ("901",), {}, "cancel_gtt_verified", ("901",), {"retries": 2}, False),
    ("cancel_gtt_verified", ("901",), {"retries": 5}, "cancel_gtt_verified", ("901",), {"retries": 5}, False),
    ("cancel_order", ("O1",), {"symbol": S}, "cancel_order", ("O1",), {"symbol": S}, False),
    ("cancel_order", ("O1",), {}, "cancel_order", ("O1",), {"symbol": ""}, False),
    ("modify_order", ("O1", 12.5), {"symbol": S}, "modify_order", ("O1",), {"price": 12.5, "symbol": S}, False),
    ("modify_order", ("O1", 12.5), {}, "modify_order", ("O1",), {"price": 12.5, "symbol": ""}, False),
    ("place_wing_buy", (S, 111, 65), {}, "place_wing_buy", (), {"symbol": S, "token": 111, "qty": 65}, True),
    ("fresh_wing_buy_limit", (S,), {}, "fresh_wing_buy_limit", (S,), {}, False),
    ("fresh_sell_entry_limit", (S,), {}, "fresh_sell_entry_limit", (S,), {}, False),
    ("fresh_buy_entry_limit", (S,), {}, "fresh_buy_entry_limit", (S,), {}, False),
    ("fresh_sell_exit_limit", (S,), {}, "fresh_sell_exit_limit", (S,), {}, False),
    ("fresh_buy_exit_limit", (S,), {}, "fresh_buy_exit_limit", (S,), {}, False),
]


@pytest.mark.parametrize("meth,args,kw,xname,xargs,xkw,noted", PLAIN,
                         ids=[f"{p[0]}-{i}" for i, p in enumerate(PLAIN)])
def test_0293_forward(meth, args, kw, xname, xargs, xkw, noted):
    r, ex = route()
    out = getattr(r, meth)(*args, **kw)
    assert ex.calls == [(xname, xargs, xkw)]
    assert isinstance(out, Ret) and out.name == xname            # the executor's own answer
    assert NOTED == ([out] if noted else [])


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0294 — place_gtt_oco
# ════════════════════════════════════════════════════════════════════════════
def _oco(**extra):
    return dict({"symbol": S, "qty": 65, "sl_price": 80.0, "tp_price": 130.0, "last_price": None,
                 "direction": "LONG"}, **extra)


def test_0294_oco_defaults_and_values():
    r, ex = route()
    out = r.place_gtt_oco(S, 65, 80.0, 130.0)
    assert ex.calls == [("place_gtt_oco", (), _oco())] and out.name == "place_gtt_oco"
    ex.calls.clear()
    r.place_gtt_oco(S, 65, 80.0, None, last_price=100.0, direction="SHORT")
    assert ex.calls == [("place_gtt_oco", (), _oco(tp_price=None, last_price=100.0, direction="SHORT"))]
    assert NOTED == []


def test_0294_oco_limit_buffer():
    r, ex = route()
    out = r.place_gtt_oco(S, 65, 80.0, 130.0, limit_buffer=1.05)
    assert ex.calls == [("place_gtt_oco", (), _oco(limit_buffer=1.05))] and out.name == "place_gtt_oco"
    ex.calls.clear()
    r.place_gtt_oco(S, 65, 80.0, 130.0, limit_buffer=0)          # 0 / None: never sent
    assert ex.calls == [("place_gtt_oco", (), _oco())]


class OldOco:
    """An executor whose place_gtt_oco has no limit_buffer (Angel, older builds)."""
    def __init__(self, err=None):
        self.calls, self.err = [], err

    def place_gtt_oco(self, symbol, qty, sl_price, tp_price, last_price=None, direction="LONG", **k):
        self.calls.append(dict(symbol=symbol, qty=qty, sl_price=sl_price, tp_price=tp_price,
                               last_price=last_price, direction=direction, **k))
        if k and self.err:
            raise self.err
        return "G1"


def test_0294_oco_buffer_refused_falls_back():
    ex = OldOco(TypeError("place_gtt_oco() got an unexpected keyword argument 'limit_buffer'"))
    assert ExecutionRouter(ex).place_gtt_oco(S, 65, 80.0, 130.0, limit_buffer=1.05) == "G1"
    assert ex.calls == [_oco(limit_buffer=1.05), _oco()]          # tried with it, then the stock call


def test_0294_oco_other_type_error_raises():
    ex = OldOco(TypeError("unsupported operand type(s)"))
    with pytest.raises(TypeError, match="unsupported operand"):
        ExecutionRouter(ex).place_gtt_oco(S, 65, 80.0, 130.0, limit_buffer=1.05)
    assert len(ex.calls) == 1                                    # never silently resent


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0295 — single-leg GTTs through _call_supported
# ════════════════════════════════════════════════════════════════════════════
GTT1 = [("place_gtt_sl_only_short", "sl_price", ("last_price", "limit_buffer")),
        ("place_gtt_sl_only_long", "sl_price", ("last_price", "limit_buffer")),
        ("place_gtt_tp_only_long", "tp_price", ("last_price",))]


@pytest.mark.parametrize("meth,price,opts", GTT1, ids=[g[0] for g in GTT1])
def test_0295_optional_args_only_when_set(meth, price, opts):
    r, ex = route()
    out = getattr(r, meth)(S, 65, 80.0)
    assert ex.calls == [(meth, (), {"symbol": S, "qty": 65, price: 80.0})] and out.name == meth
    ex.calls.clear()
    vals = {"last_price": 100.0, "limit_buffer": 1.05}
    getattr(r, meth)(S, 65, 80.0, **{o: vals[o] for o in opts})
    assert ex.calls == [(meth, (), dict({"symbol": S, "qty": 65, price: 80.0}, **{o: vals[o] for o in opts}))]
    assert NOTED == [] and AUDIT == []


class Angel:
    def place_gtt_sl_only_short(self, symbol, qty, sl_price):
        return ("short", symbol, qty, sl_price)

    def place_gtt_sl_only_long(self, symbol, qty, sl_price, last_price=None):
        return ("long", symbol, qty, sl_price, last_price)

    def place_gtt_tp_only_long(self, symbol, qty, tp_price):
        return ("tp", symbol, qty, tp_price)


def test_0295_unsupported_optional_args_dropped_and_logged():
    r = ExecutionRouter(Angel())
    assert r.place_gtt_sl_only_short(S, 65, 80.0, last_price=100.0, limit_buffer=1.05) == ("short", S, 65, 80.0)
    assert AUDIT == ["[ROUTER] place_gtt_sl_only_short: Angel does not accept ['last_price', 'limit_buffer'] "
                     "— sent without them"]
    AUDIT.clear()
    assert r.place_gtt_sl_only_long(S, 65, 80.0, last_price=100.0, limit_buffer=1.05) == ("long", S, 65, 80.0, 100.0)
    assert AUDIT == ["[ROUTER] place_gtt_sl_only_long: Angel does not accept ['limit_buffer'] — sent without them"]
    AUDIT.clear()
    assert r.place_gtt_tp_only_long(S, 65, 130.0) == ("tp", S, 65, 130.0) and AUDIT == []   # nothing to drop


def test_0295_signature_unreadable_sends_everything(monkeypatch):
    seen = []

    class Odd:
        def place_gtt_sl_only_short(self, **k):
            seen.append(k)
            return "ok"
    ex = Odd()
    import inspect
    real = inspect.signature

    def boom(fn, *a, **k):
        if getattr(fn, "__name__", "") == "place_gtt_sl_only_short":
            raise ValueError("no signature")
        return real(fn, *a, **k)
    monkeypatch.setattr(inspect, "signature", boom)
    assert ExecutionRouter(ex).place_gtt_sl_only_short(S, 65, 80.0, last_price=100.0) == "ok"
    assert seen == [{"symbol": S, "qty": 65, "sl_price": 80.0, "last_price": 100.0}]


def test_0295_audit_failure_never_drops_the_gtt(monkeypatch):
    def boom(m):
        raise OSError("log disk full")
    monkeypatch.setattr(AL, "write_audit_log", boom)
    assert ExecutionRouter(Angel()).place_gtt_sl_only_short(S, 65, 80.0, limit_buffer=1.05) == ("short", S, 65, 80.0)


@pytest.mark.parametrize("meth", [g[0] for g in GTT1] + ["get_basket_margin"])
def test_0295_missing_method_is_a_clear_error(meth):
    class Bare:
        pass
    r = ExecutionRouter(Bare())
    with pytest.raises(RuntimeError) as e:
        getattr(r, meth)([]) if meth == "get_basket_margin" else getattr(r, meth)(S, 65, 80.0)
    assert str(e.value) == f"{meth} not supported by Bare"
    r2 = ExecutionRouter(types.SimpleNamespace(**{meth: "not callable"}))
    with pytest.raises(RuntimeError, match=f"{meth} not supported by SimpleNamespace"):
        getattr(r2, meth)([]) if meth == "get_basket_margin" else getattr(r2, meth)(S, 65, 80.0)


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0296 — degrade paths on an executor without the method
# ════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("meth,args", [("get_gtts_or_none", ()), ("gtt_last_state", ("901",)),
                                       ("get_gtt_status", ("901",)), ("get_open_positions_or_none", ()),
                                       ("modify_order", ("O1", 12.5)), ("fresh_sell_entry_limit", (S,)),
                                       ("fresh_buy_entry_limit", (S,))])
def test_0296_missing_reads_none(meth, args):
    r, ex = route(only=set())
    assert getattr(r, meth)(*args) is None and ex.calls == []
    r2 = ExecutionRouter(types.SimpleNamespace(**{meth: 42}))     # present but not callable
    assert getattr(r2, meth)(*args) is None


def test_0296_verify_cancel_degrades_to_plain_cancel():
    r, ex = route(without={"cancel_gtt_verified"})
    assert r.cancel_gtt_verified("901", retries=4) is True
    assert ex.calls == [("cancel_gtt", ("901",), {})]

    class Boom:
        def cancel_gtt(self, g):
            raise RuntimeError("relay down")
    assert ExecutionRouter(Boom()).cancel_gtt_verified("901") is True   # not confirmed armed


def test_0296_cancel_order_without_symbol_param():
    calls = []

    class Old:
        def cancel_order(self, order_id):
            calls.append(order_id)
            return "C"
    assert ExecutionRouter(Old()).cancel_order("O1", symbol=S) == "C" and calls == ["O1"]


def test_0296_wing_buy_falls_back_to_place_buy():
    r, ex = route(without={"place_wing_buy"})
    out = r.place_wing_buy(S, 111, 65)
    assert ex.calls == [("place_buy", (), {"symbol": S, "token": 111, "qty": 65})]
    assert out.name == "place_buy" and NOTED == [out]


@pytest.mark.parametrize("meth,primary,fallback", [
    ("fresh_wing_buy_limit", "fresh_wing_buy_limit", "fresh_buy_entry_limit"),
    ("fresh_sell_exit_limit", "fresh_sell_exit_limit", "fresh_sell_entry_limit"),
    ("fresh_buy_exit_limit", "fresh_buy_exit_limit", "fresh_buy_entry_limit"),
])
def test_0296_quote_fallbacks(meth, primary, fallback):
    r, ex = route(without={primary})
    out = getattr(r, meth)(S)
    assert ex.calls == [(fallback, (S,), {})] and out.name == fallback
    r2, ex2 = route(only=set())
    assert getattr(r2, meth)(S) is None and ex2.calls == []


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0297 — noting an order never breaks the order
# ════════════════════════════════════════════════════════════════════════════
def test_0297_note_failure_is_swallowed(monkeypatch):
    bt = types.ModuleType("app.execution.broker_truth")

    def boom(ret):
        raise RuntimeError("state dir read-only")
    bt.note_app_order = boom
    monkeypatch.setitem(sys.modules, "app.execution.broker_truth", bt)
    monkeypatch.setattr(_pkg, "broker_truth", bt, raising=False)
    r, ex = route()
    out = r.place_buy(S, 111, 65)
    assert out.name == "place_buy" and ex.calls == [("place_buy", (S, 111, 65), {})]


if __name__ == "__main__":
    import subprocess
    _here = os.path.dirname(os.path.abspath(__file__))
    _env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [str(Path(_here).parents[1]), _here] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]))
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                              os.path.basename(__file__)], cwd=_here, env=_env))
