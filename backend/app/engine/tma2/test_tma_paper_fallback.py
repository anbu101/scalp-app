# backend/app/engine/tma2/test_tma_paper_fallback.py
#
# ── PAPER_FALLBACK_20261103 ── TMA_V2 / TMA_V1 in PAPER_LIVE: a LIVE entry
# refused by the margin guard no longer silences the paper book (same defect
# as IC_V2 on 2026-11-03). Run standalone:  python3 test_tma_paper_fallback.py
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_tma2_e2e as E                                            # noqa: E402
from app.engine.tma import tma_trade_manager as TM1                  # noqa: E402
from app.engine.tma.tma_common import TMARepo                        # noqa: E402
from app.execution.execution_router import ExecutionRouter           # noqa: E402
from app.execution.testkit_fake_kite import FakeKite                 # noqa: E402

FAILS = []
TWIN = 10


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def arm(m, allowed=True):
    m._paper_fallback_allowed = lambda snap: allowed and snap.get("exec_mode") == "PAPER_LIVE"
    m._twin_qtys = lambda snap, s, h, sq, bq: (sq * TWIN, bq * TWIN)


def modes(db, table):
    return [r[0] for r in sqlite3.connect(db).execute(f"SELECT mode FROM {table} ORDER BY id")]


# ── TMA_V2 ───────────────────────────────────────────────────────────
print("── TMA_V2")
m, kite, ch, db = E.setup(mode="PAPER_LIVE")
arm(m)
kite.margin_available = 40_000                       # < required × 1.25
E.enter(m, ch)
g = m.group
check("margin-refused LIVE → a PAPER group still opens", g is not None and g["mode"] == "PAPER", g and g["mode"])
check("no broker order, no GTT", not kite.events, kite.events[:3])
check("sized like the twin (10 lots)", g and g["sell"]["qty"] == 65 * TWIN and g["hedge"]["qty"] == 65 * TWIN)
check("booked in the paper book", modes(db, "tma2_trades") and set(modes(db, "tma2_trades")) == {"PAPER"},
      modes(db, "tma2_trades"))
check("warning says the paper book trades on", any(c == "TMA_MARGIN_BLOCK" and "paper book" in msg
                                                    for c, msg in E.ALERTS), E.ALERTS)

m, kite, ch, db = E.setup(mode="LIVE")
arm(m)
kite.margin_available = 40_000
E.enter(m, ch)
check("LIVE-only: still a hard block", m.group is None and not kite.events and not modes(db, "tma2_trades"))

m, kite, ch, db = E.setup(mode="PAPER_LIVE")
arm(m)
E.enter(m, ch)
check("enough margin: LIVE as before", m.group is not None and m.group["mode"] == "LIVE" and kite.events)

m, kite, ch, db = E.setup(mode="PAPER_LIVE")
arm(m)
kite.margin_available = 40_000
m._enter_live_orig = m._enter_live
m._enter_live = lambda *a, **k: False               # a NON-margin failure (e.g. hedge never filled)
m._live_block = None
E.enter(m, ch)
check("a non-margin LIVE failure does NOT fall back to paper", m.group is None)

# ── TMA_V1 (same manager shape, c1.* config) ─────────────────────────
print("── TMA_V1")
cfg1 = {"c1": {"sell": {"premium_max": 170, "lots": 1, "sl_pct": 12, "tp_pct": 10,
                        "sl_unit": "PCT", "tp_unit": "ABS"},
               "buy": {"premium_max": 5, "lots": 1}, "max_trades_per_day": 0},
        "trade_mode": "INTRADAY", "trade_execution_mode": "PAPER_LIVE", "exit_time": "15:25"}
E.setup()                                            # isolate + stubs
E.SL.load_strategy_config_ex = lambda sid: (cfg1, False)
TM1.record_alert = lambda code, msg="", **k: E.ALERTS.append((code, msg))
TM1.notify_critical = lambda d: None
TM1.write_audit_log = lambda msg: None
for fn in ("notify_trade_entry", "notify_tp_exit", "notify_sl_exit", "notify_manual_exit"):
    setattr(TM1, fn, lambda d: None)
TM1._ENTRY_FILL_CAP_S, TM1._ENTRY_FILL_POLL_S = 0.3, 0.05
kite1 = FakeKite({**E.CE_CHAIN, **E.PE_CHAIN})
db1 = tempfile.mktemp(suffix=".db")
m1 = TM1.TMATradeManager({}, TMARepo(db_path=db1), executor=ExecutionRouter(kite1))
m1._sleep, m1._clock = kite1.clock.sleep, kite1.clock
arm(m1)
kite1.margin_available = 40_000
E.ALERTS.clear()
E.enter(m1, E.Chain(kite1))
g1 = m1.group
check("TMA_V1: margin-refused LIVE → PAPER group, twin size, no orders",
      g1 is not None and g1["mode"] == "PAPER" and g1["sell"]["qty"] == 65 * TWIN and not kite1.events,
      (g1 and g1["mode"], kite1.events[:2]))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("ALL TMA PAPER-FALLBACK CHECKS PASSED")
