# backend/app/backtest/engine/test_lot_comp_equity.py
#
# ── LOT_COMP_EQ_20260924 ── standalone:
#   cd backend && python3 app/backtest/engine/test_lot_comp_equity.py
# Unit checks on the equity sizing rule, then every runner the shared
# synthetic corpus can drive is run in equity mode and each trade's qty is
# checked against an INDEPENDENT recomputation from the trades closed before
# its entry day — proving the begin_day feedback loop is wired in each runner.
from __future__ import annotations

import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))
os.environ.setdefault("SCALP_LOT_SIZE_OFFLINE", "1")

from app.backtest.engine.lot_compounding import (   # noqa: E402
    LotCompounder, lot_comp_is_equity, _trade_net)

FAILS = []


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if not ok:
        FAILS.append(name)


class T:   # minimal trade stand-in
    def __init__(self, exit_ts, net_pnl=None, pnl=None, charges=0.0, exit_price=1.0, gross=None, net=None):
        self.exit_ts, self.net_pnl, self.pnl, self.charges = exit_ts, net_pnl, pnl, charges
        self.exit_price, self.gross, self.net = exit_price, gross, net


print("── parse / on ──")
S = date(2020, 1, 1)
eq = {"lot_comp_mode": "equity", "lot_comp_capital_per_lot": 150000}
c = LotCompounder(eq, S, 10)
check("equity mode on with base + cpl", c.on and c.mode == "equity" and c.cpl == 150000)
check("lot_comp_is_equity", lot_comp_is_equity(eq) and not lot_comp_is_equity({"lot_comp_mode": "calendar"}) and not lot_comp_is_equity({}))
check("equity mode needs cpl", not LotCompounder({"lot_comp_mode": "equity"}, S, 10).on)
check("equity mode needs base", not LotCompounder(eq, S, 0).on)
check("calendar keys ignored in equity mode", LotCompounder({**eq, "lot_comp_step_months": 3, "lot_comp_add_lots": 1}, S, 10).mode == "equity")
check("EQUITY string accepted", LotCompounder({"lot_comp_mode": "EQUITY", "lot_comp_capital_per_lot": "150000"}, S, 10).on)
check("calendar mode unaffected by cpl", LotCompounder({"lot_comp_step_months": 3, "lot_comp_add_lots": 1, "lot_comp_capital_per_lot": 150000}, S, 10).mode == "calendar")
check("before any begin_day: lots = base", c.lots(date(2021, 1, 1)) == 10 and c.mult(date(2021, 1, 1)) == 1.0)
check("tier is 0 in equity mode", c.tier(date(2025, 1, 1)) == 0)

print("── equity_lots rule ──")
check("no P&L → base", c.equity_lots(0.0) == 10)
check("+1 lot at +cpl", c.equity_lots(150000) == 11)
check("+0 just under cpl", c.equity_lots(149999.99) == 10)
check("−1 lot at −cpl", c.equity_lots(-150000) == 9)
check("floors at 1 lot", c.equity_lots(-10 * 150000) == 1)
check("cap honoured", LotCompounder({**eq, "lot_comp_max_lots": 12}, S, 10).equity_lots(10 * 150000) == 12)
check("cap below base ignored", LotCompounder({**eq, "lot_comp_max_lots": 5}, S, 10).equity_lots(10 * 150000) == 20)

print("── begin_day feedback ──")
D0 = 1577836800 + 5 * 3600 + 30 * 60   # 2020-01-01 09:15 IST-ish epoch
day1, day2, day3 = date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 3)
trades = []
check("day 1: no trades → base", c.begin_day(day1, trades) == 10 and c.lots(day1) == 10)
trades.append(T(exit_ts=D0 + 3600, net_pnl=160000.0))           # closed day 1
check("same day again: unchanged (day guard)", c.begin_day(day1, trades) == 10)
check("day 2 sees day-1 close → 11 lots", c.begin_day(day2, trades) == 11 and c.lots(day2) == 11)
check("mult tracks lots", abs(c.mult(day2) - 1.1) < 1e-12 and c.scale_lots(10, day2) == 11)
check("scale_rs tracks lots", abs(c.scale_rs(35000, day2) - 38500) < 1e-9)
trades.append(T(exit_ts=D0 + 86400 + 3600, pnl=-200000.0, charges=1000.0))   # day 2 loss via pnl-charges
trades.append(T(exit_ts=None, exit_price=None, net_pnl=999999.0))             # OPEN trade must not count
check("day 3: 160000 − 201000 → 9 lots", c.begin_day(day3, trades) == 9)
check("open trade excluded from equity", c._eq_equity == 10 * 150000 + 160000 - 201000)
check("_trade_net prefers net_pnl", _trade_net(T(1, net_pnl=5.0, pnl=99.0)) == 5.0)
check("_trade_net falls back to net", _trade_net(T(1, net=7.0)) == 7.0)
check("_trade_net falls back to gross−charges", _trade_net(T(1, gross=10.0, charges=3.0)) == 7.0)
check("_trade_net dict trades", _trade_net({"exit_price": 1, "net_pnl": 4.0}) == 4.0)
dg = c.diag()
check("diag ladder records changes", dg["on"] and dg["mode"] == "equity" and [x["lots"] for x in dg["ladder"]] == [10, 11, 9])
check("peak_lots = ladder max", c.peak_lots(date(2026, 1, 1)) == 11)
check("describe", "equity" in c.describe() and "cpl=150000" in c.describe())
off = LotCompounder({}, S, 10)
check("begin_day is a no-op when OFF", off.begin_day(day2, trades) == 10 and not off.on)
cal = LotCompounder({"lot_comp_step_months": 3, "lot_comp_add_lots": 1}, S, 10)
check("begin_day is a no-op in calendar mode", cal.begin_day(date(2020, 4, 1), trades) == 11)

# ── runners ────────────────────────────────────────────────────────────────
print("── runners (equity mode, cpl ₹20,000, cap 30) ──")
try:
    src = open(os.path.join(HERE, "test_lot_comp_runners.py"), encoding="utf-8").read()
    head = src.split("RUNNERS = [")[0].replace("while len(all_days) < 45:", "while len(all_days) < 30:")
    ns = {"__file__": os.path.join(HERE, "test_lot_comp_runners.py"), "__name__": "_lcr_head"}
    exec(compile(head, "lcr_head", "exec"), ns)
    run_one, _g, day_of_ts, RUN_FROM = ns["run_one"], ns["_g"], ns["day_of_ts"], ns["RUN_FROM"]
    LOT = 65
    CPL, CAP = 20000, 30
    EQ = {"lot_comp_mode": "equity", "lot_comp_capital_per_lot": CPL, "lot_comp_max_lots": CAP,
          "lot_comp_step_months": 0, "lot_comp_add_lots": 0}
    varied = 0
    for name in ["STFC_V1", "ORB_V1", "BRK_V1", "FVG_V1", "ORV_V1", "CBO_V1", "GC_V1", "VET_V1",
                 "TMA_V2", "TMA_V1", "VAP_V1", "TSG_V1", "IC_V1", "HA_V1", "SCALP_V5", "SCALP_V1", "SCALP_V3"]:
        try:
            r = run_one(name, EQ)
        except Exception as e:
            check(f"{name}: equity run executes", False, repr(e))
            continue
        tr = r["trades"]
        ref = LotCompounder(EQ, RUN_FROM, 10)
        closed = [t for t in tr if _g(t, "exit_ts") is not None and _g(t, "exit_price") is not None]
        bad = 0
        lots_seen = set()
        for t in sorted(tr, key=lambda x: _g(x, "entry_ts") or 0):
            ent = day_of_ts(_g(t, "entry_ts"))
            net_before = sum((_trade_net(x) or 0.0) for x in closed if day_of_ts(_g(x, "exit_ts")) < ent)
            want = ref.equity_lots(net_before) * LOT
            got = int(_g(t, "qty") or 0)
            lots_seen.add(got // LOT)
            if got != want:
                bad += 1
                if bad <= 3:
                    print(f"        {name} {ent}: qty {got} want {want} (net before {net_before:.0f})")
        check(f"{name}: every trade sized from trades closed before its entry day ({len(tr)} trades, lots {sorted(lots_seen)})", tr and bad == 0, f"{bad} mismatches")
        if len(lots_seen) > 1:
            varied += 1
        sm = r.get("summary") or {}
    check("lots actually varied in most runners (not a vacuous pass)", varied >= 10, varied)

    # persist_run stamps the qty envelope on the summary
    from app.backtest.repo.backtest_repo import persist_run   # noqa: F401
    import inspect
    check("persist_run stamps qty_min/qty_max", "qty_max" in inspect.getsource(persist_run))
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
print("ALL LOT_COMP_EQ CHECKS PASSED")
