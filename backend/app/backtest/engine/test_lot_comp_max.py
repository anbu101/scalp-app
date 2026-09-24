# backend/app/backtest/engine/test_lot_comp_max.py
#
# ── LOT_COMP_MAX_20260924 ── standalone:
#   cd backend && python3 app/backtest/engine/test_lot_comp_max.py
# Unit checks on the cap + ₹-knob switch, then two runner checks on the
# shared synthetic corpus (STFC ladder stops at the cap; TSG with fixed ₹
# knobs keeps its MTM SL while lots double; V5 fixed cap compares raw ₹).
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


S = date(2020, 1, 1)
base = {"lot_comp_step_months": 3, "lot_comp_add_lots": 1}

print("── max lots ──")
c = LotCompounder({**base, "lot_comp_max_lots": 15}, S, 10)
check("cap parsed", c.max_lots == 15)
check("below cap unchanged: 2021-01 = 14", c.lots(date(2021, 1, 1)) == 14)
check("at cap: 2021-04 = 15", c.lots(date(2021, 4, 1)) == 15)
check("above cap held: 2026-01 = 15 (not 34)", c.lots(date(2026, 1, 1)) == 15)
check("mult at cap = 1.5", abs(c.mult(date(2026, 1, 1)) - 1.5) < 1e-12)
check("peak_lots honours cap", c.peak_lots(date(2026, 8, 31)) == 15)
check("scale_lots at cap: 10 → 15", c.scale_lots(10, date(2026, 1, 1)) == 15)
check("scale_rs at cap: 35000 → 52500", abs(c.scale_rs(35000, date(2026, 1, 1)) - 52500) < 1e-9)
d = c.diag()
check("diag carries max_lots", d["max_lots"] == 15 and d["scale_rs"] is True)
c.tier(date(2026, 1, 1))
check("diag tier lots capped", all(t["lots"] <= 15 for t in c.diag()["tiers"]))
check("describe mentions max", "max=15" in c.describe())
c_lo = LotCompounder({**base, "lot_comp_max_lots": 5}, S, 10)
check("cap below base is ignored (uncapped)", c_lo.max_lots == 0 and c_lo.lots(date(2026, 1, 1)) == 34)
c_eq = LotCompounder({**base, "lot_comp_max_lots": 10}, S, 10)
check("cap == base → flat at base forever", c_eq.on and c_eq.lots(date(2026, 1, 1)) == 10)
c_str = LotCompounder({**base, "lot_comp_max_lots": "20"}, S, 10)
check("cap accepts a string", c_str.max_lots == 20)
c_junk = LotCompounder({**base, "lot_comp_max_lots": "x"}, S, 10)
check("junk cap → uncapped", c_junk.max_lots == 0)
c_off = LotCompounder({"lot_comp_max_lots": 15}, S, 10)
check("cap alone does not switch compounding on", not c_off.on and c_off.lots(date(2026, 1, 1)) == 10)

print("── ₹-knob switch ──")
f = LotCompounder({**base, "lot_comp_scale_rs": False}, S, 10)
D = date(2020, 4, 1)   # tier 1, mult 1.1
check("default is scale ON", LotCompounder(base, S, 10).scale_rs_on)
check("false → OFF", not f.scale_rs_on)
check('"false" string → OFF', not LotCompounder({**base, "lot_comp_scale_rs": "false"}, S, 10).scale_rs_on)
check('"0" string → OFF', not LotCompounder({**base, "lot_comp_scale_rs": "0"}, S, 10).scale_rs_on)
check("true string → ON", LotCompounder({**base, "lot_comp_scale_rs": "true"}, S, 10).scale_rs_on)
check("lots still grow with ₹ fixed", f.lots(D) == 11 and f.scale_lots(10, D) == 11)
check("scale_rs is identity with ₹ fixed", f.scale_rs(35000, D) == 35000)
check("rs_mult = 1 with ₹ fixed", f.rs_mult(D) == 1.0)
check("rs_mult = mult with ₹ scaling", abs(LotCompounder(base, S, 10).rs_mult(D) - 1.1) < 1e-12)
check("rs_mult = 1 when OFF", LotCompounder({}, S, 10).rs_mult(D) == 1.0)
check("diag scale_rs False", f.diag()["scale_rs"] is False)
check("describe says rs=fixed", "rs=fixed" in f.describe())

# ── runner checks on the shared synthetic corpus ──────────────────────────
print("── runners ──")
try:
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location("_lcr", os.path.join(HERE, "test_lot_comp_runners.py"))
    # The runner suite builds the corpus at import and runs its own checks; we
    # only want its helpers, so guard: import in a child process would re-run
    # everything. Build a small corpus here instead (same generator, 30 days).
    src = open(os.path.join(HERE, "test_lot_comp_runners.py"), encoding="utf-8").read()
    head = src.split("BASE = 10")[0]                # corpus builder + monkeypatch, no checks
    head = head.replace("while len(all_days) < 45:", "while len(all_days) < 30:")
    ns = {"__file__": os.path.join(HERE, "test_lot_comp_runners.py"), "__name__": "_lcr_head"}
    exec(compile(head, "lcr_head", "exec"), ns)
    dbp, RUN_FROM, RUN_TO, all_days = ns["dbp"], ns["RUN_FROM"], ns["RUN_TO"], ns["all_days"]
    day_of_ts = ns["day_of_ts"]
    boundary = next(dd for dd in all_days if LotCompounder({"lot_comp_step_months": 1, "lot_comp_add_lots": 10}, RUN_FROM, 10).tier(dd) == 1)

    from app.backtest.stfc.backtest_stfc_runner import run_stfc_backtest
    r = run_stfc_backtest(db_path=dbp, strategy_id="STFC_V1", underlying="NIFTY", date_from=RUN_FROM, date_to=RUN_TO,
                          config_override={"lots": 10, "premium_min": 0, "premium_max": 1000,
                                           "lot_comp_step_months": 1, "lot_comp_add_lots": 10, "lot_comp_max_lots": 15})
    q1 = sorted({int(t.qty) for t in r["trades"] if day_of_ts(t.entry_ts) >= boundary})
    q0 = sorted({int(t.qty) for t in r["trades"] if day_of_ts(t.entry_ts) < boundary})
    check("STFC: tier 0 at base lots", bool(q0) and all(q % 10 == 0 for q in q0), q0)
    check("STFC: tier 1 capped at 15 lots (not 20)", bool(q1) and all(q == q0[0] // 10 * 15 for q in q1), q1)

    from app.backtest.tsg.backtest_tsg_runner import run_tsg_backtest
    legs = [{"id": "L1", "action": "SELL", "opt_type": "CE", "lots": 10, "premium_max": 85},
            {"id": "L2", "action": "SELL", "opt_type": "PE", "lots": 10, "premium_max": 85},
            {"id": "L3", "action": "BUY", "opt_type": "CE", "lots": 10, "premium_max": 30},
            {"id": "L4", "action": "BUY", "opt_type": "PE", "lots": 10, "premium_max": 30}]
    tcfg = {"legs": legs, "mtm_sl": 6000, "mtm_target": 0, "lot_comp_step_months": 1, "lot_comp_add_lots": 10}
    flat = run_tsg_backtest(db_path=dbp, strategy_id="TSG_V1", underlying="NIFTY", date_from=RUN_FROM, date_to=RUN_TO,
                            config_override={"legs": legs, "mtm_sl": 6000, "mtm_target": 0})
    scal = run_tsg_backtest(db_path=dbp, strategy_id="TSG_V1", underlying="NIFTY", date_from=RUN_FROM, date_to=RUN_TO, config_override=tcfg)
    fixd = run_tsg_backtest(db_path=dbp, strategy_id="TSG_V1", underlying="NIFTY", date_from=RUN_FROM, date_to=RUN_TO,
                            config_override={**tcfg, "lot_comp_scale_rs": False})

    def sl_days(res):
        return sorted({day_of_ts(t.entry_ts) for t in res["trades"] if t.exit_reason == "MTM_SL"})
    check("TSG: scaled ₹ knobs → same MTM_SL days as flat", sl_days(scal) == sl_days(flat))
    fixed_t1 = [dd for dd in sl_days(fixd) if dd >= boundary]
    flat_t1 = [dd for dd in sl_days(flat) if dd >= boundary]
    check("TSG: fixed ₹ SL at 2× lots trips on at least as many days after the boundary",
          set(flat_t1) <= set(fixed_t1), f"flat {flat_t1} fixed {fixed_t1}")
    check("TSG: fixed ₹ run still doubled its lots", any(int(t.qty) == 1300 for t in fixd["trades"] if day_of_ts(t.entry_ts) >= boundary))
    check("TSG: fixed ₹ run differs from the scaled run (the toggle does something)",
          len(fixed_t1) > len(flat_t1) or [t.exit_ts for t in fixd["trades"]] != [t.exit_ts for t in scal["trades"]])

    from app.backtest.scalpv5.backtest_scalpv5_runner import run_scalpv5_backtest
    v5 = lambda extra: run_scalpv5_backtest(db_path=dbp, strategy_id="SCALP_V5", underlying="NIFTY", date_from=RUN_FROM, date_to=RUN_TO,
                                            config_override={"quantity": {"lots": 10}, "option_premium": {"min": 0, "max": 1000},
                                                             "max_loss": 150000, "lot_comp_step_months": 1, "lot_comp_add_lots": 10, **extra})
    v5_scale, v5_fixed, v5_flat = v5({}), v5({"lot_comp_scale_rs": False}), v5({"lot_comp_add_lots": 0})
    check("V5: scaled cap stops on the flat run's trade", len(v5_scale["trades"]) == len(v5_flat["trades"]))
    check("V5: fixed ₹ cap at 2× lots stops no later than the scaled one",
          len(v5_fixed["trades"]) <= len(v5_scale["trades"]), f"{len(v5_fixed['trades'])} vs {len(v5_scale['trades'])}")
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
print("ALL LOT_COMP_MAX CHECKS PASSED")
