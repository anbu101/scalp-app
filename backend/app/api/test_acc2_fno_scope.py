# backend/app/api/test_acc2_fno_scope.py
# ACC2_FNO_SCOPE_20261106 — Angel rows outside NFO/BFO never enter P&L, and
# API totals include merged Angel rows. Run: python3 test_acc2_fno_scope.py
import importlib.util, sys, types
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _mk(n, **kw):
    m = sys.modules.get(n) or types.ModuleType(n)
    for k, v in kw.items(): setattr(m, k, v)
    sys.modules[n] = m; return m


class _Router:
    def __init__(self, **k): pass
    def get(self, *a, **k): return lambda f: f


AUDIT = []
for n in ("app", "app.brokers", "app.trading", "app.event_bus", "app.config",
          "app.execution"):
    _mk(n)
_mk("fastapi", APIRouter=_Router)
KITE = {"net": []}


class _Kite:
    def positions(self): return KITE
    def ltp(self, syms): return {}


_mk("app.brokers.zerodha_auth", get_kite=lambda: _Kite())
_mk("app.trading.trade_state_manager",
    TradeStateManager=type("T", (), {"_REGISTRY": {}}))
_mk("app.event_bus.audit_logger", write_audit_log=AUDIT.append)
_mk("app.config.angel_credentials_store", load_credentials=lambda: {"k": 1})
ANGEL = []


class _AE:
    def get_open_positions(self): return list(ANGEL)


class _AM:
    def is_trade_ready(self): return True


_mk("app.execution.executor_factory", _get_angel_executor=lambda: _AE(),
    get_angel_manager=lambda: _AM())

spec = importlib.util.spec_from_file_location("positions_routes",
                                              HERE / "positions_routes.py")
PR = importlib.util.module_from_spec(spec); spec.loader.exec_module(PR)

SHANTI = {"tradingsymbol": "SHANTI-EQ", "exchange": "NSE", "producttype": "DELIVERY",
          "netqty": "-100", "netprice": "5.80", "ltp": "5.85"}
OPT = {"tradingsymbol": "NIFTY10NOV2624800CE", "exchange": "NFO",
       "producttype": "CARRYFORWARD", "netqty": "-65", "netprice": "100.00",
       "ltp": "90.00"}


def _run(rows, kite_net=()):
    ANGEL[:] = rows; KITE["net"] = list(kite_net); AUDIT.clear()
    PR._ACC2_SKIPPED.clear()
    return PR.positions_today()


def test_f1_shanti_repro_excluded():
    d = _run([SHANTI])
    assert d["open"] == [] and d["totals"]["total"] == 0.0
    assert any("SHANTI-EQ" in a and "skipped non-F&O" in a for a in AUDIT)


def test_f2_option_row_kept_and_in_totals():
    d = _run([SHANTI, OPT])
    assert [p["tradingsymbol"] for p in d["open"]] == ["NIFTY10NOV2624800CE"]
    assert d["open"][0]["exchange"] == "NFO"
    assert d["open"][0]["product"] == "CARRYFORWARD"
    assert d["totals"]["unrealised"] == 650.0 and d["totals"]["total"] == 650.0


def test_f3_no_exchange_falls_back_on_symbol():
    a = dict(OPT); a.pop("exchange")
    b = dict(SHANTI); b.pop("exchange")
    d = _run([a, b])
    assert [p["tradingsymbol"] for p in d["open"]] == ["NIFTY10NOV2624800CE"]


def test_f4_bfo_kept():
    s = dict(OPT, tradingsymbol="SENSEX26N1381000PE", exchange="BFO")
    assert len(_run([s])["open"]) == 1


def test_f5_skip_logged_once_per_symbol():
    ANGEL[:] = [SHANTI]; KITE["net"] = []; AUDIT.clear(); PR._ACC2_SKIPPED.clear()
    PR.positions_today(); PR.positions_today(); PR.positions_today()
    assert sum("SHANTI-EQ" in a for a in AUDIT) == 1


def test_f6_zerodha_branch_unchanged():
    z = {"tradingsymbol": "NIFTY26N1124800CE", "quantity": 65, "average_price": 50.0,
         "day_buy_quantity": 65, "day_sell_quantity": 0, "unrealised": 130.0,
         "pnl": 130.0, "multiplier": 1}
    d = _run([], kite_net=[z])
    assert d["totals"]["unrealised"] == 130.0
    assert d["open"][0]["account"] == "ZERODHA"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f(); print(f"  ✓ {f.__name__}")
    print(f"ALL {len(fns)} PASSED")
