# backend/app/backtest/report/test_portfolio_shared_pdf.py
#
# ── PF_SHARED_EQUITY_20261024 ── shared-equity (portfolio compounding) wording in
# the shareable portfolio PDF: the sizing rule, the account section, and that
# reports without a shared account are unchanged. Still no params / ids leak.
#
#   cd backend && PYTHONPATH=$PWD python3 app/backtest/report/test_portfolio_shared_pdf.py

import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="pf_shared_"))
os.environ.setdefault("SCALP_APP_HOME", str(TMP / "home"))

import matplotlib.axes                                         # noqa: E402
import app.backtest.report.portfolio_pdf as P                  # noqa: E402

FAILS, OK = [], [0]


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if cond:
        OK[0] += 1
    else:
        FAILS.append(name)


DRAWN = []
_orig_text = matplotlib.axes.Axes.text


def _spy(self, x, y, s, *a, **k):
    DRAWN.append(str(s))
    return _orig_text(self, x, y, s, *a, **k)


matplotlib.axes.Axes.text = _spy
SECRETS = ["IC_V2", "TMA_V2", "VET_V1", "RUNID", "SECRETPARAM"]


def payload(shared=True):
    labels = ["Icarus", "Timberwolf", "Velvet"]
    starts = [3, 3, 2]
    strats = []
    for i, lab in enumerate(labels):
        sz = ({"base_lots": starts[i], "mode": "portfolio", "max_lots": None, "capital_per_lot": 125000,
               "dd_limit_pct": 15, "dd_mode": "proportional", "lots_min": starts[i], "lots_max": starts[i] * 6,
               "tax_pct": 35} if shared else {"base_lots": starts[i], "mode": "fixed", "lots_min": starts[i],
                                               "lots_max": starts[i]})
        strats.append({"label": lab, "color": "#818cf8", "sid": "IC_V2", "run_id": "RUNID-1",
                       "params": "SECRETPARAM", "sizing": sz,
                       "net": 1e6 * (3 - i), "pre_tax_net": 1.5e6 * (3 - i), "tax": 5e5 * (3 - i), "taxed": shared,
                       "max_dd": 2e5, "rdd": 10.0, "trades": 1000, "share": 33.0, "dd_contrib": -1e5,
                       "worst_day": {"label": "11 Apr 24", "v": -5e4, "portfolio_total": -2e4}})
    ts = [1577850000 + k * 86400 for k in range(40)]
    p = {"v": 1, "anonymous": True, "name": None, "period": {"from": "2020-01-01", "to": "2026-08-07"},
         "generated_label": "24 Oct 2026, 15:00 IST", "basis": "after", "any_taxed": shared,
         "any_tax_available": shared, "combined_tax": 3e6 if shared else 0, "strategies": strats,
         "kpis": {"combined_net": 6e6, "combined_max_dd": 4e5, "dd_reduction": 40.0, "sum_ind_dd": 6e5,
                  "return_to_dd": 15.0, "best_single": 10.0},
         "dd_window": {"from_label": "11 Apr 24", "to_label": "08 May 24"},
         "corr": [[1, .1, .2], [.1, 1, .3], [.2, .3, 1]],
         "days": {"rescued": 4, "cluster": 2, "red": 5, "green": 9, "total": 14},
         "exposure": {"max_concurrent": 3, "max_strats": 3, "n_strats": 3, "peak_notional": 100000, "overlap_pct": 10},
         "monthly": {"months": [{"label": "Jan 2020", "per": [1, 2, 3], "n": [1, 1, 1], "total": 6}], "stats": {}},
         "equity": [{"label": l, "color": "#123456", "thick": False, "ts": ts, "v": list(range(40))} for l in labels]}
    if shared:
        p["portfolio_sizing"] = {"start_capital": 1000000, "total_lots": 8, "capital_per_lot": 125000,
                                 "end_equity": 22219755, "cagr_pct": 59.99, "max_dd_pct": 2.86,
                                 "tax_paid": 9667785, "tax_accrued": 1758237, "max_total": None, "dd_pct": 15,
                                 "tax_pct": 35, "guard_days": 662,
                                 "cagr_from": "2020-08-03", "floor_start": False}   # ── PF_SHARED_EQUITY_FIX1_20261024 ──
    return p


def render(p):
    DRAWN.clear()
    b = P.render_portfolio_pdf(p)
    return b, "\n".join(DRAWN)


print("\n── sizing cells (portfolio mode) ──")
c = P.sizing_cells({"base_lots": 3, "mode": "portfolio", "max_lots": None, "capital_per_lot": 125000,
                    "dd_limit_pct": 15, "dd_mode": "proportional", "lots_min": 3, "lots_max": 18, "tax_pct": 35})
check("portfolio row", c == ["3", "Shared equity (portfolio)", "—", "3–18", "₹1.25 L", "15% proportional", "35%"], c)
c = P.sizing_cells({"base_lots": 2, "mode": "portfolio", "max_lots": 12, "capital_per_lot": 125000,
                    "lots_min": 2, "lots_max": 12})
check("portfolio row with per-strategy cap, no guard / tax", c == ["2", "Shared equity (portfolio)", "12", "2–12", "₹1.25 L", "—", "—"], c)
c = P.sizing_cells({"base_lots": 5, "mode": "equity", "dd_limit_pct": 10, "dd_mode": "proportional", "capital_per_lot": 150000})
check("per-run equity wording unchanged", c[1] == "Equity-based (compounding)" and c[5] == "10% proportional", c)

print("\n── shared report ──")
b, txt = render(payload(shared=True))
check("renders", b[:5] == b"%PDF-")
check("sizing rule drawn", "Shared equity (portfolio)" in txt)
check("shared wording paragraph", "every strategy trades from one account" in txt)
check("account section", "Shared account" in txt and "Starting capital" in txt and "CAGR" in txt
      and "Max drawdown of account" in txt)
check("account figures", "₹10,00,000" in txt and "60.0%" in txt and "2.9%" in txt, [s for s in DRAWN if "%" in s][:8])
check("capital per lot in the account sub", "8 start lots · ₹1.25 L per lot" in txt)
for sec in SECRETS:
    check(f"never drawn: {sec}", sec not in txt)
check("no secret in raw PDF bytes", all(s.encode() not in b for s in SECRETS))

flat = txt.replace("\n", " ")
check("CAGR labelled from the first trade (FIX1)", "annual growth since first trade, 03 Aug 2020" in flat)
check("no floor note when the floor is off (FIX1)", "floor at start" not in flat and "at least 1" in flat)
p = payload(shared=True)
p["portfolio_sizing"]["floor_start"] = True
b, txt = render(p)
flat = txt.replace("\n", " ")
check("floor note in the account and the paragraph (FIX1)", "floor at start" in flat and "never below its start lots" in flat)
p["portfolio_sizing"]["cagr_from"] = None
b, txt = render(p)
check("missing cagr_from falls back to the generic label (FIX1)", "compound annual growth of the account" in txt.replace("\n", " "))

print("\n── report without a shared account is unchanged ──")
b, txt = render(payload(shared=False))
check("renders", b[:5] == b"%PDF-")
check("no account section", "Shared account" not in txt and "Shared equity" not in txt)
check("classic sizing paragraph", "Lots used is the smallest to largest" in txt)
p = payload(shared=True)
p["portfolio_sizing"] = {"start_capital": 0}
b, txt = render(p)
check("empty/zero account block is skipped, no crash", "Shared account" not in txt)

print(f"\n{OK[0]} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
