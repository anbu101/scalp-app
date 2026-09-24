# backend/app/backtest/engine/test_lot_comp_tax.py
#
# ── LOT_COMP_TAX_20260924 ── standalone:
#   cd backend && python3 app/backtest/engine/test_lot_comp_tax.py
# Optional tax on realised profits, settled once per tax year (Indian FY,
# 1 April – 31 March, start month configurable): on the first sim day on or
# after the FY start the previous FY's net is taxed at lot_comp_tax_pct,
# losses carry forward against later FYs, and the tax is withdrawn from the
# equity that sizes the next day's lots. The DD guard's peak/trough are
# shifted by the same amount so a withdrawal never reads as a drawdown. The
# open FY at the end of a run is accrued (reported), not withdrawn.
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))
os.environ.setdefault("SCALP_LOT_SIZE_OFFLINE", "1")

from app.backtest.engine.lot_compounding import (   # noqa: E402
    LotCompounder, fy_of, fy_tax_schedule)

FAILS = []
IST = 5 * 3600 + 30 * 60


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if not ok:
        FAILS.append(name)


def ts(d: date, h=15) -> int:   # IST-afternoon epoch for a date
    return int((datetime(d.year, d.month, d.day, h) - datetime(1970, 1, 1)).total_seconds()) - IST


class T:
    def __init__(self, exit_day, net_pnl):
        self.exit_ts, self.net_pnl, self.exit_price = ts(exit_day), net_pnl, 1.0


print("── fy_of ──")
check("Apr 1 starts the FY", fy_of(date(2021, 4, 1)) == date(2021, 4, 1))
check("Mar 31 belongs to the previous FY", fy_of(date(2022, 3, 31)) == date(2021, 4, 1))
check("Jan is in the FY that started last April", fy_of(date(2022, 1, 15)) == date(2021, 4, 1))
check("custom start month 3", fy_of(date(2026, 3, 2), 3) == date(2026, 3, 1) and fy_of(date(2026, 2, 27), 3) == date(2025, 3, 1))

print("── fy_tax_schedule (pure) ──")
trades = [T(date(2020, 6, 1), 100000), T(date(2021, 2, 1), -160000),          # FY20-21: −60k → carry
          T(date(2021, 8, 1), 200000),                                         # FY21-22: 200k − 60k carry = 140k → 42k tax
          T(date(2022, 5, 1), 50000)]                                          # FY22-23: open at run end → accrued 15k
sch = fy_tax_schedule(trades, 30, run_to=date(2022, 8, 7))
check("three FYs", [f["fy"] for f in sch["fys"]] == ["2020-21", "2021-22", "2022-23"], [f["fy"] for f in sch["fys"]])
f0, f1, f2 = sch["fys"]
check("FY20-21 loss → no tax, carry −60k", f0["net"] == -60000 and f0["tax"] == 0 and f0["carry_out"] == -60000)
check("FY21-22 taxed on 200k − 60k", f1["taxable"] == 140000 and f1["tax"] == 42000 and f1["carry_out"] == 0)
check("FY22-23 open → accrued, not paid", f2["settled"] is False and f2["tax"] == 15000)
check("totals: paid 42k, accrued 15k", sch["paid"] == 42000 and sch["accrued"] == 15000)
check("rate 0 → empty schedule", fy_tax_schedule(trades, 0) == {"on": False})
check("open trades ignored", fy_tax_schedule(trades + [type("O", (), {"exit_ts": None, "exit_price": None, "net_pnl": 9e9})()], 30, run_to=date(2022, 8, 7))["paid"] == 42000)

print("── module: equity mode, ₹1L/lot, base 10, tax 30% ──")
cfg = {"lot_comp_mode": "equity", "lot_comp_capital_per_lot": 100000, "lot_comp_tax_pct": 30}
c = LotCompounder(cfg, date(2021, 1, 4), 10)
check("tax on", c.tax_on and c.tax_rate == 0.30 and c.tax_fy_month == 4)
tr = []
check("Jan 4 2021: 10 lots", c.begin_day(date(2021, 1, 4), tr) == 10)
tr.append(T(date(2021, 2, 1), 500000))                     # FY20-21 profit 5L → equity 15L
check("Feb: 15 lots", c.begin_day(date(2021, 2, 2), tr) == 15)
check("Mar 31: still 15 (FY not settled yet)", c.begin_day(date(2021, 3, 31), tr) == 15)
check("Apr 1: FY20-21 settled → tax 1.5L → equity 13.5L → 13 lots", c.begin_day(date(2021, 4, 1), tr) == 13 and c.tax_paid == 150000)
check("Apr 2: no double settlement", c.begin_day(date(2021, 4, 2), tr) == 13 and c.tax_paid == 150000)
tr.append(T(date(2021, 9, 1), -400000))                    # FY21-22 loss 4L → equity 9.5L
check("Sep: 9 lots", c.begin_day(date(2021, 9, 2), tr) == 9)
check("Apr 2022: loss FY → no tax, carry −4L", c.begin_day(date(2022, 4, 1), tr) == 9 and c.tax_paid == 150000 and c.tax_carry == -400000)
tr.append(T(date(2022, 6, 1), 600000))                     # FY22-23 profit 6L: taxable 2L
check("Jun 2022: 15 lots (equity 15.5L)", c.begin_day(date(2022, 6, 2), tr) == 15)
check("Apr 2023: tax 30% of (6L − 4L) = 60k → equity 14.9L → 14 lots", c.begin_day(date(2023, 4, 3), tr) == 14 and c.tax_paid == 210000 and c.tax_carry == 0)
tr.append(T(date(2023, 5, 1), 100000))                     # open FY23-24: +1L → accrued 30k
c.begin_day(date(2023, 5, 2), tr)
dg = c.diag()["tax"]
check("diag: paid 2.1L, accrued 30k for the open FY", dg["paid"] == 210000 and dg["accrued"] == 30000 and dg["open_fy"] == "2023-24")
check("diag: 3 settled FYs", [f["fy"] for f in dg["fys"]] == ["2020-21", "2021-22", "2022-23"])
check("describe mentions tax", "tax=30%" in c.describe())

print("── skipped sessions: a gap over 1 April still settles once ──")
g = LotCompounder(cfg, date(2021, 1, 4), 10)
tr = [T(date(2021, 3, 1), 500000)]
g.begin_day(date(2021, 3, 2), tr)
check("first session after the gap settles", g.begin_day(date(2021, 4, 15), tr) == 13 and g.tax_paid == 150000)
g2 = LotCompounder(cfg, date(2021, 1, 4), 10)
tr2 = [T(date(2021, 3, 1), 500000)]
g2.begin_day(date(2021, 3, 2), tr2)
check("two FY boundaries in one gap → both settled (second with no trades)", g2.begin_day(date(2022, 4, 15), tr2) == 13 and g2.tax_paid == 150000 and len(g2.diag()["tax"]["fys"]) == 2)

print("── DD guard is not fooled by the withdrawal ──")
d = LotCompounder({**cfg, "lot_comp_dd_limit_pct": 10}, date(2021, 1, 4), 10)
tr = [T(date(2021, 2, 1), 500000)]
d.begin_day(date(2021, 2, 2), tr)                          # equity 15L = peak
check("peak 15L", d.dd_peak == 1500000)
d.begin_day(date(2021, 4, 1), tr)                          # tax 1.5L withdrawn (10% of equity!)
check("Apr 1: peak shifted with the withdrawal, no breach", d.dd_peak == 1350000 and not d.dd_active and d.lots(date(2021, 4, 1)) == 13)
tr.append(T(date(2021, 4, 5), -100000))                    # 12.5L: dd vs 13.5L peak = 7.4% → no breach
check("a later 7% dip is measured from the shifted peak", d.begin_day(date(2021, 4, 6), tr) == 12 and not d.dd_active)

print("── tax off / inert cases ──")
check("tax 0 → off", not LotCompounder({**cfg, "lot_comp_tax_pct": 0}, date(2021, 1, 4), 10).tax_on)
check("tax without an equity basis (calendar, no cpl, no guard) → inert",
      not LotCompounder({"lot_comp_step_months": 3, "lot_comp_add_lots": 1, "lot_comp_tax_pct": 30}, date(2021, 1, 4), 10).tax_on)
k = LotCompounder({"lot_comp_step_months": 3, "lot_comp_add_lots": 1, "lot_comp_capital_per_lot": 100000,
                   "lot_comp_dd_limit_pct": 10, "lot_comp_tax_pct": 30}, date(2021, 1, 4), 10)
check("calendar + guard + tax → tax on (guard equity basis)", k.tax_on)
tr = [T(date(2021, 2, 1), 500000)]
k.begin_day(date(2021, 2, 2), tr); k.begin_day(date(2021, 4, 5), tr)
check("calendar mode: withdrawal shifts guard equity, ladder unaffected (tier 1 = 11)", k.tax_paid == 150000 and k.lots(date(2021, 4, 5)) == 11)
off = LotCompounder({"lot_comp_mode": "equity", "lot_comp_capital_per_lot": 100000}, date(2021, 1, 4), 10)
tr = [T(date(2021, 2, 1), 500000)]
off.begin_day(date(2021, 2, 2), tr)
check("no tax key: Apr 1 changes nothing", off.begin_day(date(2021, 4, 1), tr) == 15 and off.diag().get("tax") == {"on": False})

# ── runners: FY start month 3 forces a settlement inside the synthetic corpus ──
print("── runners ──")
try:
    src = open(os.path.join(HERE, "test_lot_comp_runners.py"), encoding="utf-8").read()
    head = src.split("RUNNERS = [")[0].replace("while len(all_days) < 45:", "while len(all_days) < 30:")
    ns = {"__file__": os.path.join(HERE, "test_lot_comp_runners.py"), "__name__": "_lcr_head"}
    exec(compile(head, "lcr_head", "exec"), ns)
    run_one, _g, day_of_ts, RUN_FROM = ns["run_one"], ns["_g"], ns["day_of_ts"], ns["RUN_FROM"]
    LOT = 65
    TX = {"lot_comp_mode": "equity", "lot_comp_capital_per_lot": 20000, "lot_comp_max_lots": 30,
          "lot_comp_tax_pct": 30, "lot_comp_tax_fy_start_month": 3}
    for name in ["STFC_V1", "TSG_V1", "TMA_V2", "HA_V1"]:
        try:
            r = run_one(name, TX)
        except Exception as e:
            check(f"{name}: run executes", False, repr(e))
            continue
        tr = r["trades"]
        ref = LotCompounder(TX, RUN_FROM, 10)
        closed = [t for t in tr if _g(t, "exit_ts") is not None and _g(t, "exit_price") is not None]
        exp = {}
        for dd_ in [x for x in ns["all_days"] if x >= RUN_FROM]:
            exp[dd_] = ref.begin_day(dd_, [t for t in closed if day_of_ts(_g(t, "exit_ts")) < dd_])
        bad = [(day_of_ts(_g(t, "entry_ts")), int(_g(t, "qty")), exp[day_of_ts(_g(t, "entry_ts"))] * LOT)
               for t in tr if int(_g(t, "qty") or 0) != exp[day_of_ts(_g(t, "entry_ts"))] * LOT]
        tx = ref.diag()["tax"]
        pre = fy_tax_schedule(closed, 30, fy_start_month=3)["fys"][0]["net"]
        check(f"{name}: every trade sized per the taxed rule ({len(tr)} trades; FY settled tax ₹{tx['paid']:,.0f} on ₹{pre:,.0f})",
              tr and not bad, bad[:3])
        check(f"{name}: settlement happened (one FY closed, first session ≥ Mar 1)",
              len(tx["fys"]) == 1 and tx["fys"][0]["settled"] and tx["fys"][0]["settled_on"] >= "2026-03-01")
        check(f"{name}: tax = 30% of the FY net when positive, 0 when not",
              (tx["fys"][0]["net"] <= 0 and tx["paid"] == 0) or abs(tx["paid"] - 0.30 * tx["fys"][0]["net"]) < 1e-6)
        # the no-tax run must differ only after settlement (sizing), never before
        r0 = run_one(name, {k: v for k, v in TX.items() if not k.startswith("lot_comp_tax")})
        before = lambda rr: [int(_g(t, "qty")) for t in rr["trades"] if day_of_ts(_g(t, "entry_ts")) < date(2026, 3, 1)]   # noqa: E731
        check(f"{name}: identical sizing before the FY boundary", before(r) == before(r0))
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
print("ALL LOT_COMP_TAX CHECKS PASSED")
