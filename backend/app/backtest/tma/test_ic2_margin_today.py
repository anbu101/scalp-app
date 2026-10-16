# backend/app/backtest/tma/test_ic2_margin_today.py — fence IC2_MARGIN_TODAY_20261015
#
# The IC "Margin (today)" button reuses the GENERIC_LEGS path of
# /api/backtest/margin-estimate (estimate_tma_margin). Fake Kite + fake
# instrument dump; never touches the network or a broker session.
#
#   1. condor basket (L1–L4 as the IC form sends them): each leg = highest
#      LTP ≤ its cap on its side (runner's select_strike), wings distinct
#      from the shorts, BUY legs first in the basket, naked = shorts alone
#   2. wing cap below every live strike → cheapest real + note (as the runner)
#   3. ADJ-only basket (two BUYs): total = Σ ltp × qty, no shorts → naked 0
#   4. a 0-lot leg is dropped; all-zero → clean error
#
# Run: cd backend && python3 app/backtest/tma/test_ic2_margin_today.py

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = str(HERE.parents[3])
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

FAILS: list = []
OK = [0]


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if ok:
        OK[0] += 1
    else:
        FAILS.append(name)


def fake_world(spot=25000.0):
    import pandas as pd
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    exp = expected_expiry_for_day(date.today())
    rows, ltp = [], {}
    for k in range(-30, 31):
        K = int(spot) + 50 * k
        for side in ("CE", "PE"):
            sym = f"NIFTYTEST{K}{side}"
            dist = (K - spot) if side == "CE" else (spot - K)
            px = max(0.0, -dist) + 120.0 * (0.97 ** max(0.0, dist / 10.0))   # intrinsic + decaying TV
            rows.append({"name": "NIFTY", "segment": "NFO-OPT", "instrument_type": side,
                         "expiry": exp.isoformat(), "tradingsymbol": sym, "strike": float(K),
                         "lot_size": 65})
            ltp[f"NFO:{sym}"] = {"last_price": round(px, 2)}
    return pd.DataFrame(rows), ltp, exp


class FakeKite:
    def __init__(self, ltp):
        self._ltp = ltp
        self.baskets = []

    def ltp(self, keys):
        return {k: self._ltp[k] for k in keys if k in self._ltp}

    def basket_order_margins(self, basket, consider_positions=False):
        self.baskets.append([dict(o) for o in basket])
        tot, buy_sides = 0.0, set()
        for o in basket:
            if o["transaction_type"] == "BUY":
                tot += self._ltp[f"NFO:{o['tradingsymbol']}"]["last_price"] * o["quantity"]
                buy_sides.add(o["tradingsymbol"][-2:])
        for o in basket:
            if o["transaction_type"] == "SELL":
                per_lot = 50000.0 if o["tradingsymbol"][-2:] in buy_sides else 150000.0
                tot += per_lot * o["quantity"] / 65
        return {"final": {"total": tot}}


def main():
    import app.fetcher.zerodha_instruments as zi
    from app.backtest.tma.margin_estimate import estimate_tma_margin
    from app.backtest.ic.ic_v1_engine import select_strike
    df, ltp, exp = fake_world()
    zi.load_instruments_df = lambda *a, **k: df

    def ladder(side):
        return [(s.split(":")[1], v["last_price"]) for s, v in ltp.items() if s.endswith(side)]

    print("── 1. condor basket ──")
    k = FakeKite(ltp)
    legs = [{"side": "CE", "action": "SELL", "premium_max": 100, "lots": 10},
            {"side": "PE", "action": "SELL", "premium_max": 100, "lots": 10},
            {"side": "CE", "action": "BUY", "premium_max": 4, "lots": 10},
            {"side": "PE", "action": "BUY", "premium_max": 4, "lots": 10}]
    r = estimate_tma_margin(legs=legs, kite=k)
    check("ok", r.get("ok"), r.get("error"))
    allp = r["legs"]["all"]
    want_s_ce = select_strike(ladder("CE"), 100)[0]
    want_w_ce = select_strike([x for x in ladder("CE") if x[0] != want_s_ce], 4)[0]
    got = {(x["action"], x["side"]): x["symbol"] for x in allp}
    check("short CE = highest LTP ≤ 100 (runner rule)", got[("SELL", "CE")] == want_s_ce, (got, want_s_ce))
    check("wing CE = highest LTP ≤ 4, distinct from the short", got[("BUY", "CE")] == want_w_ce and want_w_ce != want_s_ce)
    check("4 legs, qty 650 each", len(allp) == 4 and all(x["qty"] == 650 for x in allp))
    check("basket sends BUY legs first", [o["transaction_type"] for o in k.baskets[0]] == ["BUY", "BUY", "SELL", "SELL"])
    check("naked basket = the two shorts alone", [o["transaction_type"] for o in k.baskets[1]] == ["SELL", "SELL"])
    check("hedged < naked, benefit = difference",
          r["hedged_total"] < r["naked_total"] and abs(r["benefit"] - (r["naked_total"] - r["hedged_total"])) < 0.01)
    check("expiry = this week's expiry", r["expiry"] == exp.isoformat())

    print("── 2. wing cap below every strike ──")
    r2 = estimate_tma_margin(legs=[dict(l, premium_max=0.01) if l["action"] == "BUY" else l for l in legs], kite=FakeKite(ltp))
    check("still ok with cheapest real wings + note", r2.get("ok") and r2.get("note"), r2)

    print("── 3. ADJ-only basket ──")
    k3 = FakeKite(ltp)
    r3 = estimate_tma_margin(legs=[{"side": "CE", "action": "BUY", "premium_max": 100, "lots": 8},
                                   {"side": "PE", "action": "BUY", "premium_max": 100, "lots": 8}], kite=k3)
    a = r3["legs"]["all"]
    prem = sum(x["ltp"] * x["qty"] for x in a)
    check("ok, two BUY legs of 520", r3.get("ok") and len(a) == 2 and all(x["qty"] == 520 for x in a), r3)
    check("total = Σ ltp × qty (premium paid)", abs(r3["hedged_total"] - round(prem, 2)) < 0.01, (r3["hedged_total"], prem))
    check("no shorts → naked 0", r3["naked_total"] == 0)
    check("ADJ strike = highest LTP ≤ 100 (runner rule)",
          {x["side"]: x["symbol"] for x in a}["CE"] == select_strike(ladder("CE"), 100)[0])

    print("── 4. lots ──")
    r4 = estimate_tma_margin(legs=[dict(legs[0], lots=0)] + legs[1:], kite=FakeKite(ltp))
    check("0-lot leg dropped (3 legs priced)", r4.get("ok") and len(r4["legs"]["all"]) == 3)
    r5 = estimate_tma_margin(legs=[dict(l, lots=0) for l in legs], kite=FakeKite(ltp))
    check("all-zero → clean error", r5.get("ok") is False and "lots" in r5.get("error", ""))

    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} IC MARGIN-TODAY CHECKS PASSED")


if __name__ == "__main__":
    main()
