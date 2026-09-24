# backend/app/backtest/engine/test_lot_compounding.py
#
# ── LOT_COMP_20260924 ── standalone:
#   cd backend && python3 app/backtest/engine/test_lot_compounding.py
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))

from app.backtest.engine.lot_compounding import (   # noqa: E402
    LotCompounder, months_elapsed, parse_lot_comp)

FAILS = []


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok:
        FAILS.append(name)


print("── parse ──")
check("absent → OFF", parse_lot_comp({}) == (0, 0))
check("None cfg → OFF", parse_lot_comp(None) == (0, 0))
check("step only → OFF", parse_lot_comp({"lot_comp_step_months": 3}) == (0, 0))
check("add only → OFF", parse_lot_comp({"lot_comp_add_lots": 1}) == (0, 0))
check("both → ON", parse_lot_comp({"lot_comp_step_months": 3, "lot_comp_add_lots": 1}) == (3, 1))
check("strings accepted", parse_lot_comp({"lot_comp_step_months": "3", "lot_comp_add_lots": "2"}) == (3, 2))
check("junk → OFF", parse_lot_comp({"lot_comp_step_months": "x", "lot_comp_add_lots": 1}) == (0, 0))
check("negative → OFF", parse_lot_comp({"lot_comp_step_months": -3, "lot_comp_add_lots": 1}) == (0, 0))

print("── months_elapsed (anniversary) ──")
s = date(2020, 1, 15)
check("same day = 0", months_elapsed(s, s) == 0)
check("day before anniversary = 2", months_elapsed(s, date(2020, 4, 14)) == 2)
check("anniversary = 3", months_elapsed(s, date(2020, 4, 15)) == 3)
check("year later = 12", months_elapsed(s, date(2021, 1, 15)) == 12)
check("before start = 0", months_elapsed(s, date(2019, 12, 31)) == 0)
s31 = date(2020, 1, 31)
check("31st start: Feb 29 = 0", months_elapsed(s31, date(2020, 2, 29)) == 0)
check("31st start: Mar 1 = 1", months_elapsed(s31, date(2020, 3, 1)) == 1)
check("31st start: Mar 31 = 2", months_elapsed(s31, date(2020, 3, 31)) == 2)
s1 = date(2020, 1, 1)
check("1st start: Mar 31 = 2 (calendar months)", months_elapsed(s1, date(2020, 3, 31)) == 2)
check("1st start: Apr 1 = 3", months_elapsed(s1, date(2020, 4, 1)) == 3)

print("── OFF is the identity ──")
off = LotCompounder({}, date(2020, 1, 1), 10)
check("off flag", not off.on)
check("tier 0", off.tier(date(2025, 1, 1)) == 0)
check("lots = base", off.lots(date(2025, 1, 1)) == 10)
check("mult 1", off.mult(date(2025, 1, 1)) == 1.0)
check("scale_lots identity", off.scale_lots(7, date(2025, 1, 1)) == 7)
check("scale_qty identity", off.scale_qty(650, 65, date(2025, 1, 1)) == 650)
check("scale_rs identity", off.scale_rs(35000, date(2025, 1, 1)) == 35000)
legs = [{"id": "L1", "lots": 3}]
check("scale_legs returns same object", off.scale_legs(legs, date(2025, 1, 1)) is legs)
check("peak = base", off.peak_lots(date(2026, 1, 1)) == 10)
check("diag off", off.diag() == {"on": False})
check("base 0 → OFF even with keys",
      not LotCompounder({"lot_comp_step_months": 3, "lot_comp_add_lots": 1}, date(2020, 1, 1), 0).on)
check("lot_comp_anchor wins over date_from (parallel shards)",
      LotCompounder({"lot_comp_step_months": 3, "lot_comp_add_lots": 1, "lot_comp_anchor": "2020-01-01"},
                    date(2021, 6, 1), 10).lots(date(2021, 6, 1)) == 10 + 5)
check("no date_from → OFF",
      not LotCompounder({"lot_comp_step_months": 3, "lot_comp_add_lots": 1}, None, 10).on)

print("── ON: 10 lots, +1 every 3 months from 2020-01-01 ──")
cfg = {"lot_comp_step_months": 3, "lot_comp_add_lots": 1}
c = LotCompounder(cfg, date(2020, 1, 1), 10)
check("on flag", c.on)
check("Jan–Mar tier 0", [c.tier(date(2020, m, 15)) for m in (1, 2, 3)] == [0, 0, 0])
check("Apr 1 tier 1", c.tier(date(2020, 4, 1)) == 1 and c.lots(date(2020, 4, 1)) == 11)
check("Mar 31 still tier 0", c.tier(date(2020, 3, 31)) == 0)
check("Jul tier 2 = 12 lots", c.lots(date(2020, 7, 1)) == 12)
check("6 years → 24 steps → 34 lots", c.lots(date(2026, 1, 1)) == 34)
check("peak_lots at 2025-12-31 = 33", c.peak_lots(date(2025, 12, 31)) == 33)
check("peak_lots accepts iso string", c.peak_lots("2026-01-01") == 34)
check("tier accepts iso string", c.tier("2020-04-01") == 1)
check("mult Apr = 1.1", abs(c.mult(date(2020, 4, 1)) - 1.1) < 1e-12)

print("── ratio scaling of legs / rupee knobs ──")
d1 = date(2020, 4, 1)   # mult 1.1
check("scale_lots 10 → 11", c.scale_lots(10, d1) == 11)
check("scale_lots 1 → 1 (round 1.1)", c.scale_lots(1, d1) == 1)
check("scale_lots 5 → 6 (round 5.5 → banker's? no: python round(5.5)=6)", c.scale_lots(5, d1) == 6)
check("scale_lots 0 stays 0 (dte skip)", c.scale_lots(0, d1) == 0)
check("scale_lots never < 1 for positive", c.scale_lots(1, date(2020, 1, 2)) == 1)
check("scale_qty 650 → 715", c.scale_qty(650, 65, d1) == 715)
check("scale_qty 0 → 0", c.scale_qty(0, 65, d1) == 0)
check("scale_rs 35000 → 38500", abs(c.scale_rs(35000, d1) - 38500) < 1e-9)
check("scale_rs 0 stays 0 (off knob)", c.scale_rs(0, d1) == 0)
check("scale_rs negative keeps sign", abs(c.scale_rs(-2500, d1) + 2750) < 1e-9)
check("scale_rs junk passthrough", c.scale_rs("x", d1) == "x")
sl = c.scale_legs([{"id": "L1", "lots": 10}, {"id": "L2", "lots": 10}, {"id": "H", "lots": 4}], d1)
check("scale_legs 10/10/4 → 11/11/4", [l["lots"] for l in sl] == [11, 11, 4])
check("scale_legs copies (input untouched)", sl[0] is not None and sl is not legs)
c2 = LotCompounder({"lot_comp_step_months": 3, "lot_comp_add_lots": 10}, date(2020, 1, 1), 10)
check("big step: 10 → 20 at tier 1", c2.scale_lots(10, d1) == 20)
check("ratio keeps hedge ratio: 4 → 8", c2.scale_lots(4, d1) == 8)

print("── real-clock tier walk over 2020–2026 (doctrine: real dates) ──")
walk = LotCompounder(cfg, date(2020, 1, 1), 10)
d, last, steps = date(2020, 1, 1), -1, []
while d <= date(2026, 8, 31):
    t = walk.tier(d)
    if t != last:
        steps.append((d, t))
        last = t
    d += timedelta(days=1)
check("first tier at start", steps[0] == (date(2020, 1, 1), 0))
check("every step lands on the 1st of a quarter month",
      all(s.day == 1 and s.month in (1, 4, 7, 10) for s, _ in steps[1:]))
check("tiers are consecutive", [t for _, t in steps] == list(range(len(steps))))
check("27 tiers by Aug 2026 (0..26)", steps[-1][1] == 26)
dg = walk.diag()
check("diag tiers count", dg["on"] and len(dg["tiers"]) == 27)
check("diag first tier range", dg["tiers"][0]["from"] == "2020-01-01" and dg["tiers"][0]["to"] == "2020-03-31")
check("diag lots ladder", [t["lots"] for t in dg["tiers"][:4]] == [10, 11, 12, 13])
check("describe", "step=3mo" in walk.describe())

print()
if FAILS:
    print(f"FAILED {len(FAILS)}:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print(f"ALL {sum(1 for _ in open(__file__) if 'check(' in _) - 1} LOT_COMP CHECKS PASSED")
