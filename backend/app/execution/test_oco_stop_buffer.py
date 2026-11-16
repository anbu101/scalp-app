# test_oco_stop_buffer.py — OCO_STOP_BUFFER_20261116 (DEF-035)
# A stop+target GTT (OCO) put its STOP leg's limit only 0.5% past the trigger,
# ignoring gtt_limit_buffer_pct. Icarus now passes its configured buffer (5%)
# for both its shorts (BUY limit above) and its adjustment longs (SELL limit
# below). The target leg and every other caller are unchanged.
#   PYTHONPATH=backend python3 backend/app/execution/test_oco_stop_buffer.py
import contextlib
import io
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SCALP_TEST_MODE", "1")

import app.event_bus.audit_logger as AL                               # noqa: E402
AL.write_audit_log = lambda m: None
import app.execution.zerodha_executor as ZE                           # noqa: E402
from app.execution.execution_router import ExecutionRouter            # noqa: E402
from app.marketdata.ltp_store import LTPStore                         # noqa: E402
ZE.write_audit_log = lambda m: None

PASSED, FAILS = [0], []
S = "NIFTY26N1725350CE"


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print(f"[ok  ] {name}")
    else:
        FAILS.append(name)
        print(f"[MISS] {name}\n         -> {detail}")


class Kite:
    VARIETY_REGULAR, EXCHANGE_NFO, ORDER_TYPE_LIMIT = "regular", "NFO", "LIMIT"
    TRANSACTION_TYPE_BUY, TRANSACTION_TYPE_SELL, PRODUCT_NRML = "BUY", "SELL", "NRML"
    GTT_TYPE_SINGLE, GTT_TYPE_OCO = "single", "two-leg"
    api_key, access_token = "k", "t"

    def __init__(self):
        self.gtts = []

    def place_gtt(self, **kw):
        self.gtts.append(kw)
        return {"trigger_id": 900 + len(self.gtts)}

    def get_gtts(self):
        return []


def executor():
    k = Kite()
    ZE._load_relay_config = lambda: None
    bm = types.SimpleNamespace(is_trade_ready=lambda: True, refresh=lambda: None,
                               get_trade_kite=lambda: k, get_data_kite=lambda: k)
    ex = ZE.ZerodhaOrderExecutor(bm)
    ex._ensure_trading_enabled = lambda: None
    ex._get_lot_size = lambda kite, symbol, qty: 65
    return ex, k


def legs(g):
    """(stop leg, target leg) prices of a placed GTT, by trigger order."""
    o = [x["price"] for x in g["orders"]]
    return o


def r05(x):
    return round(round(x / 0.05) * 0.05, 2)


# ── 16 Nov L1A: long 25350 CE @100.30, SL 70.21, TP 200.60 ────────────────
ex, k = executor()
ex.place_gtt_oco(symbol=S, qty=65, sl_price=70.21, tp_price=200.60, last_price=100.3)
old = legs(k.gtts[-1])
ex.place_gtt_oco(symbol=S, qty=65, sl_price=70.21, tp_price=200.60, last_price=100.3,
                 limit_buffer=1.05)
new = legs(k.gtts[-1])
check(f"O1 long OCO, no buffer: stop sells at 0.5% below the trigger as before ({old[0]})",
      old[0] == r05(70.21 * 0.995), str(old))
check(f"O2 long OCO, 5%: stop sells down to {new[0]} (= trigger 70.20 / 1.05)",
      new[0] == r05(70.2 / 1.05) and new[0] < old[0], str(new))
check("O2 the target leg is unchanged", new[1] == old[1], f"{new} vs {old}")
check("O2 triggers unchanged", k.gtts[-1]["trigger_values"] == k.gtts[-2]["trigger_values"])

# short OCO (Icarus short with a target): BUY limit above the stop
ex.place_gtt_oco(symbol=S, qty=65, sl_price=124.11, tp_price=40.0, last_price=87.4, direction="SHORT")
so = [x["price"] for x in k.gtts[-1]["orders"]]
ex.place_gtt_oco(symbol=S, qty=65, sl_price=124.11, tp_price=40.0, last_price=87.4, direction="SHORT",
                 limit_buffer=1.05)
sn = [x["price"] for x in k.gtts[-1]["orders"]]
check(f"O3 short OCO, no buffer: stop buys up to +0.5% as before ({so[1]})", so[1] == r05(r05(124.11) * 1.005), str(so))
check(f"O4 short OCO, 5%: stop buys up to {sn[1]} (= trigger x 1.05); target unchanged",
      sn[1] == r05(r05(124.11) * 1.05) and sn[0] == so[0], f"{sn} vs {so}")

# SL-only long via the OCO entry point (tp None) also honours the buffer
ex.place_gtt_oco(symbol=S, qty=65, sl_price=70.21, tp_price=None, last_price=100.3, limit_buffer=1.05)
check("O5 long stop-only through place_gtt_oco: 5% too", legs(k.gtts[-1])[0] == r05(70.2 / 1.05))

# ── router ────────────────────────────────────────────────────────────────
rt = ExecutionRouter.__new__(ExecutionRouter)
rt._executor = ex
rt.place_gtt_oco(S, 65, 70.21, 200.60, last_price=100.3, limit_buffer=1.05)
check("R1 router forwards the buffer to Zerodha", legs(k.gtts[-1])[0] == r05(70.2 / 1.05))
calls = []


class OldBroker:                       # Angel/Dhan-style: no limit_buffer parameter
    def place_gtt_oco(self, symbol, qty, sl_price, tp_price, last_price=None, direction="LONG"):
        calls.append((symbol, sl_price, tp_price, direction))
        return "G1"


rt._executor = OldBroker()
check("R2 other brokers: placed exactly as before (buffer dropped)",
      rt.place_gtt_oco(S, 65, 70.21, 200.60, limit_buffer=1.05) == "G1" and len(calls) == 1)
check("R3 no buffer given: the same call as before", rt.place_gtt_oco(S, 65, 70.21, 200.60) == "G1"
      and len(calls) == 2)

# ── Icarus end to end (fleet harness): every OCO it places carries 5% ─────
sys.path.insert(0, str(ROOT / "app/engine"))
with contextlib.redirect_stdout(io.StringIO()):
    import test_fleet_live as F                                    # noqa: E402
SEEN = []
_orig = F.FleetKite.place_gtt_oco


def _rec(self, symbol, qty, sl_price, tp_price, last_price=None, direction="LONG", limit_buffer=None):
    SEEN.append((symbol, direction, limit_buffer))
    return _orig(self, symbol, qty, sl_price, tp_price, last_price, direction)


F.FleetKite.place_gtt_oco = _rec
with contextlib.redirect_stdout(io.StringIO()):
    F.t_icarus_sl_with_tigris_on_same_strike()
check(f"I1 Icarus placed stop+target GTTs ({len(SEEN)}) and every one carries the configured 5%",
      len(SEEN) > 0 and all(b == 1.05 for _, _, b in SEEN), str(SEEN))
check("I1 including the adjustment long (SELL-side stop)", any(d == "LONG" for _, d, _ in SEEN), str(SEEN))

print(f"\n{PASSED[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL OCO-STOP-BUFFER CHECKS PASSED")
