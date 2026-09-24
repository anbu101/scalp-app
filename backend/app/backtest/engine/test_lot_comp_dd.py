# backend/app/backtest/engine/test_lot_comp_dd.py
#
# ── LOT_COMP_DD_20260924 ── standalone:
#   cd backend && python3 app/backtest/engine/test_lot_comp_dd.py
# Drawdown guard: when equity (base × cpl + realised net) sits more than
# `lot_comp_dd_limit_pct` below its peak, lots drop to BASE (never above it)
# until equity makes a new high. Unit checks on the state machine, then
# runner checks where each trade's qty is recomputed independently from the
# trades closed before its entry day, guard included.
from __future__ import annotations

import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))
os.environ.setdefault("SCALP_LOT_SIZE_OFFLINE", "1")

from app.backtest.engine.lot_compounding import LotCompounder, _trade_net   # noqa: E402

FAILS = []


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if not ok:
        FAILS.append(name)


class T:
    def __init__(self, exit_ts, net_pnl, exit_price=1.0):
        self.exit_ts, self.net_pnl, self.exit_price = exit_ts, net_pnl, exit_price


S = date(2020, 1, 1)
EQ = {"lot_comp_mode": "equity", "lot_comp_capital_per_lot": 100000, "lot_comp_max_lots": 30}

print("── parse ──")
c = LotCompounder({**EQ, "lot_comp_dd_limit_pct": 10}, S, 10)
check("dd_limit parsed as fraction", abs(c.dd_limit - 0.10) < 1e-12)
check("absent → 0", LotCompounder(EQ, S, 10).dd_limit == 0)
check("string accepted", abs(LotCompounder({**EQ, "lot_comp_dd_limit_pct": "12.5"}, S, 10).dd_limit - 0.125) < 1e-12)
check("≥100 → off", LotCompounder({**EQ, "lot_comp_dd_limit_pct": 100}, S, 10).dd_limit == 0)
check("negative → off", LotCompounder({**EQ, "lot_comp_dd_limit_pct": -5}, S, 10).dd_limit == 0)
check("junk → off", LotCompounder({**EQ, "lot_comp_dd_limit_pct": "x"}, S, 10).dd_limit == 0)
check("guard inert without cpl (calendar, no cpl)",
      not LotCompounder({"lot_comp_step_months": 3, "lot_comp_add_lots": 1, "lot_comp_dd_limit_pct": 10}, S, 10).dd_guard_on)
check("guard armed in calendar mode WITH cpl",
      LotCompounder({"lot_comp_step_months": 3, "lot_comp_add_lots": 1, "lot_comp_dd_limit_pct": 10, "lot_comp_capital_per_lot": 100000}, S, 10).dd_guard_on)
check("guard armed in equity mode", c.dd_guard_on)

print("── equity mode state machine (start ₹10L, limit 10%) ──")
D = lambda n: date(2020, 1, n)   # noqa: E731
tr = []
check("day 1: base, guard idle", c.begin_day(D(1), tr) == 10 and not c.dd_active)
tr.append(T(1, +300000))                                    # equity 13L → 13 lots
check("day 2: 13 lots (peak 13L)", c.begin_day(D(2), tr) == 13 and c.dd_peak == 1300000)
tr.append(T(2, -120000))                                    # equity 11.8L, DD 9.2% → still sized 11
check("day 3: DD 9.2% < 10% → 11 lots, guard idle", c.begin_day(D(3), tr) == 11 and not c.dd_active)
tr.append(T(3, -20000))                                     # equity 11.6L, DD 10.8% → breach
check("day 4: DD 10.8% ≥ 10% → BREACH → base 10 (not 11)", c.begin_day(D(4), tr) == 10 and c.dd_active)
check("lots() reports guarded lots", c.lots(D(4)) == 10 and c.mult(D(4)) == 1.0)
tr.append(T(4, +50000))                                     # equity 12.1L, still below 13L peak
check("day 5: partial recovery → still base", c.begin_day(D(5), tr) == 10 and c.dd_active)
tr.append(T(5, -400000))                                    # equity 8.1L → equity rule says 8
check("day 6: deep DD → guard keeps min(rule, base) = 8", c.begin_day(D(6), tr) == 8 and c.dd_active)
tr.append(T(6, +480000))                                    # equity 12.9L, just under peak
check("day 7: under the old peak → still guarded (12 → 10)", c.begin_day(D(7), tr) == 10 and c.dd_active)
tr.append(T(7, +150000))                                    # equity 14.4L → new high
check("day 8: new equity high → guard off → 14 lots", c.begin_day(D(8), tr) == 14 and not c.dd_active and c.dd_peak == 1440000)
dg = c.diag()
check("diag records breach + recovery", dg["dd_guard"]["limit_pct"] == 10 and [e["type"] for e in dg["dd_guard"]["events"]] == ["breach", "recover"])
check("diag guarded-day count = 4 (days 4-7)", dg["dd_guard"]["days"] == 4)
check("ladder shows effective lots", [n for _, n in c._eq_ladder] == [10, 13, 11, 10, 8, 10, 14])
check("describe mentions dd", "dd=10%" in c.describe())

print("── calendar mode guard (10 → +1/1mo, cpl ₹1L, limit 10%) ──")
k = LotCompounder({"lot_comp_step_months": 1, "lot_comp_add_lots": 1, "lot_comp_dd_limit_pct": 10, "lot_comp_capital_per_lot": 100000}, S, 10)
tr = []
check("Jan: ladder 10", k.begin_day(date(2020, 1, 2), tr) == 10)
check("Feb: ladder 11", k.begin_day(date(2020, 2, 3), tr) == 11)
tr.append(T(1, -150000))                                     # equity 8.5L vs peak 10L → DD 15%
check("Mar after −15%: breach → base 10 (ladder would be 12)", k.begin_day(date(2020, 3, 2), tr) == 10 and k.dd_active)
check("tier still advances underneath", k.tier(date(2020, 3, 2)) == 2)
tr.append(T(2, +200000))                                     # equity 10.5L → new high
check("Apr after new high: ladder 13 resumes", k.begin_day(date(2020, 4, 1), tr) == 13 and not k.dd_active)
off = LotCompounder({"lot_comp_step_months": 1, "lot_comp_add_lots": 1}, S, 10)
check("no guard: begin_day is still a no-op for calendar", off.begin_day(date(2020, 4, 1), tr) == 13 and not off.dd_guard_on)

# ── runners ────────────────────────────────────────────────────────────────
print("── runners ──")
try:
    src = open(os.path.join(HERE, "test_lot_comp_runners.py"), encoding="utf-8").read()
    head = src.split("RUNNERS = [")[0].replace("while len(all_days) < 45:", "while len(all_days) < 30:")
    ns = {"__file__": os.path.join(HERE, "test_lot_comp_runners.py"), "__name__": "_lcr_head"}
    exec(compile(head, "lcr_head", "exec"), ns)
    run_one, _g, day_of_ts, RUN_FROM = ns["run_one"], ns["_g"], ns["day_of_ts"], ns["RUN_FROM"]
    LOT = 65

    def verify(name, cfg, want_breach=True):
        r = run_one(name, cfg)
        tr = r["trades"]
        ref = LotCompounder(cfg, RUN_FROM, 10)
        closed = [t for t in tr if _g(t, "exit_ts") is not None and _g(t, "exit_price") is not None]
        # replay the module over EVERY sim day (a no-entry day can still set a
        # new equity peak or breach) on the closed-before-day subsets
        exp = {}
        for d in [x for x in ns["all_days"] if x >= RUN_FROM]:
            before = [t for t in closed if day_of_ts(_g(t, "exit_ts")) < d]
            exp[d] = ref.begin_day(d, before)
        bad = [(day_of_ts(_g(t, "entry_ts")), int(_g(t, "qty")), exp[day_of_ts(_g(t, "entry_ts"))] * LOT)
               for t in tr if int(_g(t, "qty") or 0) != exp[day_of_ts(_g(t, "entry_ts"))] * LOT]
        ev = ref.diag().get("dd_guard", {}).get("events", [])
        breaches = [e for e in ev if e["type"] == "breach"]
        check(f"{name}: every trade sized per the guarded rule ({len(tr)} trades, {len(breaches)} breach(es), {ref.diag().get('dd_guard', {}).get('days', 0)} guarded days)",
              tr and not bad, bad[:3])
        if want_breach:
            check(f"{name}: guard actually fired in this run (not vacuous)", len(breaches) > 0)
        return ref

    EQD = {"lot_comp_mode": "equity", "lot_comp_capital_per_lot": 20000, "lot_comp_max_lots": 30, "lot_comp_dd_limit_pct": 5}
    for name in ["STFC_V1", "TSG_V1", "TMA_V2", "SCALP_V1", "VET_V1", "HA_V1"]:
        try:
            verify(name, EQD)
        except Exception as e:
            check(f"{name}: run executes", False, repr(e))
    CALD = {"lot_comp_step_months": 1, "lot_comp_add_lots": 10, "lot_comp_capital_per_lot": 20000, "lot_comp_dd_limit_pct": 5}
    for name in ["ORB_V1", "IC_V1"]:
        try:
            verify(name, CALD, want_breach=(name != "ORB_V1"))   # ORB only gains on this corpus
        except Exception as e:
            check(f"{name}: calendar+guard run executes", False, repr(e))
    # guard OFF ≡ the equity run of the parent suite (no behaviour change when unset)
    a = run_one("STFC_V1", {"lot_comp_mode": "equity", "lot_comp_capital_per_lot": 20000, "lot_comp_max_lots": 30})
    b = run_one("STFC_V1", {"lot_comp_mode": "equity", "lot_comp_capital_per_lot": 20000, "lot_comp_max_lots": 30, "lot_comp_dd_limit_pct": 0})
    check("STFC: dd_limit 0 ≡ unset", [int(_g(t, "qty")) for t in a["trades"]] == [int(_g(t, "qty")) for t in b["trades"]])
except Exception as e:
    import traceback
    traceback.print_exc()
    check("runner checks execute", False, repr(e))

print()
if FAILS:
    print(f"FAILED {len(FAILS)}:")
    for x in FAILS:
        print("  -", x)
    sys.exit(1)
print("ALL LOT_COMP_DD CHECKS PASSED")
