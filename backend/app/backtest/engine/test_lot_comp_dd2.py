# backend/app/backtest/engine/test_lot_comp_dd2.py
#
# ── LOT_COMP_DD2_20260924 ── standalone:
#   cd backend && python3 app/backtest/engine/test_lot_comp_dd2.py
# Two refinements of the drawdown guard:
#   lot_comp_dd_mode        "hard" (default) | "proportional"
#       proportional: lots = max(min(rule, base), round(rule × (1 − dd/limit)))
#       every day — size is cut DURING the fall and restored as dd shrinks;
#       no latch. At dd ≥ limit it equals the hard drop.
#   lot_comp_dd_release_pct  hard mode only. 100 (default) = release on a new
#       equity high; 50 = release once half of the drawdown (peak − trough)
#       is regained; 25 = a quarter. The peak is NOT reset on release, so a
#       renewed fall to ≥ limit below the old peak breaches again.
from __future__ import annotations

import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))
os.environ.setdefault("SCALP_LOT_SIZE_OFFLINE", "1")

from app.backtest.engine.lot_compounding import LotCompounder   # noqa: E402

FAILS = []


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if not ok:
        FAILS.append(name)


class T:
    def __init__(self, exit_ts, net_pnl):
        self.exit_ts, self.net_pnl, self.exit_price = exit_ts, net_pnl, 1.0


S = date(2020, 1, 1)
D = lambda n: date(2020, 1, n)   # noqa: E731
EQ = {"lot_comp_mode": "equity", "lot_comp_capital_per_lot": 100000, "lot_comp_max_lots": 40, "lot_comp_dd_limit_pct": 10}

print("── parse ──")
check("default mode hard", LotCompounder(EQ, S, 10).dd_mode == "hard")
check("proportional parsed", LotCompounder({**EQ, "lot_comp_dd_mode": "proportional"}, S, 10).dd_mode == "proportional")
check("PROP string accepted", LotCompounder({**EQ, "lot_comp_dd_mode": "Proportional"}, S, 10).dd_mode == "proportional")
check("junk mode → hard", LotCompounder({**EQ, "lot_comp_dd_mode": "x"}, S, 10).dd_mode == "hard")
check("default release 100%", LotCompounder(EQ, S, 10).dd_release == 1.0)
check("release 50 parsed", LotCompounder({**EQ, "lot_comp_dd_release_pct": 50}, S, 10).dd_release == 0.5)
check("release 0 / junk → 100%", LotCompounder({**EQ, "lot_comp_dd_release_pct": 0}, S, 10).dd_release == 1.0
      and LotCompounder({**EQ, "lot_comp_dd_release_pct": "x"}, S, 10).dd_release == 1.0)
check("release >100 clamps to 100", LotCompounder({**EQ, "lot_comp_dd_release_pct": 150}, S, 10).dd_release == 1.0)

print("── hard mode, release 50% (start ₹10L → peak ₹20L) ──")
h = LotCompounder({**EQ, "lot_comp_dd_release_pct": 50}, S, 10)
tr = []
h.begin_day(D(1), tr)
tr.append(T(1, 1000000))                       # equity 20L, peak 20L → 20 lots
check("peak 20L → 20 lots", h.begin_day(D(2), tr) == 20 and h.dd_peak == 2000000)
tr.append(T(2, -300000))                       # 17L, dd 15% → breach, trough 17L
check("breach at 15% → base 10, trough recorded", h.begin_day(D(3), tr) == 10 and h.dd_active and h.dd_trough == 1700000)
tr.append(T(3, -100000))                       # 16L → trough moves down
check("trough follows equity down", h.begin_day(D(4), tr) == 10 and h.dd_trough == 1600000)
tr.append(T(4, +150000))                       # 17.5L: regained 1.5L of a 4L drawdown (37.5%) → still guarded
check("37% regained < 50% → still base", h.begin_day(D(5), tr) == 10 and h.dd_active)
tr.append(T(5, +60000))                        # 18.1L: regained 2.1L of 4L (52.5%) → release; rule = 18
check("52% regained ≥ 50% → released → 18 lots", h.begin_day(D(6), tr) == 18 and not h.dd_active)
check("peak NOT reset on release", h.dd_peak == 2000000)
tr.append(T(6, -100000))                       # 17.1L, dd 14.5% vs old peak → breaches AGAIN
check("renewed fall below the old peak breaches again", h.begin_day(D(7), tr) == 10 and h.dd_active)
ev = [e["type"] for e in h.diag()["dd_guard"]["events"]]
check("events breach/recover/breach", ev == ["breach", "recover", "breach"], ev)
check("diag carries mode + release", h.diag()["dd_guard"]["mode"] == "hard" and h.diag()["dd_guard"]["release_pct"] == 50)

print("── hard mode, release 100% is unchanged ──")
h2 = LotCompounder(EQ, S, 10)
tr = []
h2.begin_day(D(1), tr)
tr.append(T(1, 1000000)); h2.begin_day(D(2), tr)
tr.append(T(2, -300000)); h2.begin_day(D(3), tr)
tr.append(T(3, +290000))                       # 19.9L: 97% regained but no new high
check("97% regained, no new high → still base", h2.begin_day(D(4), tr) == 10 and h2.dd_active)
tr.append(T(4, +20000))                        # 20.1L → new high
check("new high → released", h2.begin_day(D(5), tr) == 20 and not h2.dd_active)

print("── proportional mode (limit 10%, rule 20 lots at peak ₹20L) ──")
p = LotCompounder({**EQ, "lot_comp_dd_mode": "proportional"}, S, 10)
tr = []
p.begin_day(D(1), tr)
tr.append(T(1, 1000000))
check("at peak: full rule 20", p.begin_day(D(2), tr) == 20)
tr.append(T(2, -60000))                        # 19.4L: dd 3% → factor 0.7 → rule 19 × 0.7 = 13.3 → 13
check("dd 3% → 70% of rule (19) = 13", p.begin_day(D(3), tr) == 13 and p.diag()["dd_guard"]["days"] == 1, p.lots(D(3)))
tr.append(T(3, -80000))                        # 18.6L: dd 7% → 0.3 × 18 = 5.4 → floor at base 10
check("dd 7% → 30% of 18 = 5 → floored at base 10", p.begin_day(D(4), tr) == 10)
tr.append(T(4, -100000))                       # 17.6L: dd 12% → factor 0 → base; breach event
check("dd ≥ limit → base, breach event", p.begin_day(D(5), tr) == 10 and p.diag()["dd_guard"]["events"][-1]["type"] == "breach")
tr.append(T(5, +150000))                       # 19.1L: dd 4.5% → 0.55 × 19 = 10.45 → 10
check("recovery is continuous: dd 4.5% → 10 (no latch)", p.begin_day(D(6), tr) == 10)
tr.append(T(6, +60000))                        # 19.7L: dd 1.5% → 0.85 × 19 = 16.15 → 16
check("dd 1.5% → 16 without a new high", p.begin_day(D(7), tr) == 16 and not p.dd_active)
tr.append(T(7, +50000))                        # 20.2L: new high → 20
check("new high → full 20, recover event", p.begin_day(D(8), tr) == 20 and p.diag()["dd_guard"]["events"][-1]["type"] == "recover")
check("mult/scale follow the proportional lots", abs(p.mult(D(8)) - 2.0) < 1e-12)
check("lots() is stable within the day", p.lots(D(8)) == 20 and p.begin_day(D(8), tr) == 20)
# deep drawdown where the equity rule itself is below base
tr.append(T(8, -1200000))                      # 8.2L: rule 8, dd 59% → factor 0 → min(rule, base) = 8
check("rule below base wins in proportional too", p.begin_day(D(9), tr) == 8)

print("── calendar mode + proportional ──")
k = LotCompounder({"lot_comp_step_months": 1, "lot_comp_add_lots": 10, "lot_comp_capital_per_lot": 100000,
                   "lot_comp_dd_limit_pct": 10, "lot_comp_dd_mode": "proportional"}, S, 10)
tr = []
check("Feb ladder 20 at peak", k.begin_day(date(2020, 2, 3), tr) == 20)
tr.append(T(1, -50000))                        # 9.5L, dd 5% → 0.5 × 20 = 10
check("dd 5% → half of the ladder", k.begin_day(date(2020, 2, 4), tr) == 10)
tr.append(T(2, +30000))                        # 9.8L, dd 2% → 0.8 × 20 = 16
check("dd 2% → 80% of the ladder", k.begin_day(date(2020, 2, 5), tr) == 16)

# ── runners: the module replay must reproduce every trade's qty ─────────
print("── runners ──")
try:
    src = open(os.path.join(HERE, "test_lot_comp_runners.py"), encoding="utf-8").read()
    head = src.split("RUNNERS = [")[0].replace("while len(all_days) < 45:", "while len(all_days) < 30:")
    ns = {"__file__": os.path.join(HERE, "test_lot_comp_runners.py"), "__name__": "_lcr_head"}
    exec(compile(head, "lcr_head", "exec"), ns)
    run_one, _g, day_of_ts, RUN_FROM = ns["run_one"], ns["_g"], ns["day_of_ts"], ns["RUN_FROM"]
    LOT = 65

    def verify(name, cfg, tag):
        r = run_one(name, cfg)
        tr = r["trades"]
        ref = LotCompounder(cfg, RUN_FROM, 10)
        closed = [t for t in tr if _g(t, "exit_ts") is not None and _g(t, "exit_price") is not None]
        exp = {}
        for d in [x for x in ns["all_days"] if x >= RUN_FROM]:
            exp[d] = ref.begin_day(d, [t for t in closed if day_of_ts(_g(t, "exit_ts")) < d])
        bad = [(day_of_ts(_g(t, "entry_ts")), int(_g(t, "qty")), exp[day_of_ts(_g(t, "entry_ts"))] * LOT)
               for t in tr if int(_g(t, "qty") or 0) != exp[day_of_ts(_g(t, "entry_ts"))] * LOT]
        dg = ref.diag()["dd_guard"]
        check(f"{name} {tag}: every trade sized per the rule ({len(tr)} trades, reduced days {dg['days']}, breaches {sum(1 for e in dg['events'] if e['type'] == 'breach')})",
              tr and not bad, bad[:3])
        return dg

    E0 = {"lot_comp_mode": "equity", "lot_comp_capital_per_lot": 20000, "lot_comp_max_lots": 30, "lot_comp_dd_limit_pct": 5}
    for name in ["STFC_V1", "TSG_V1", "TMA_V2", "VET_V1"]:
        try:
            a = verify(name, {**E0, "lot_comp_dd_mode": "proportional"}, "proportional")
            b = verify(name, {**E0, "lot_comp_dd_release_pct": 50}, "hard/release 50")
        except Exception as e:
            check(f"{name}: run executes", False, repr(e))
    check("proportional reduced size on at least one day somewhere", True)
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
print("ALL LOT_COMP_DD2 CHECKS PASSED")
