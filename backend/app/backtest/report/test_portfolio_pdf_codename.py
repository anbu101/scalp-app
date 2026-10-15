# backend/app/backtest/report/test_portfolio_pdf_codename.py
#
# ── PF_PDF_CODENAME_20261015 ── a shared (anonymous) portfolio PDF names
# strategies by the codename non-admin users see (Velvet, Tigris…); any
# label that is not a public codename or a "Strategy X" alias is replaced.
# Every drawn string is captured (all text goes through Axes.text).
#
#   cd backend && PYTHONPATH=$PWD python3 app/backtest/report/test_portfolio_pdf_codename.py

import os
import sys
import tempfile

os.environ.setdefault("SCALP_APP_HOME", tempfile.mkdtemp(prefix="pf_code_"))

import matplotlib.axes                                   # noqa: E402
import app.backtest.report.portfolio_pdf as P            # noqa: E402
from app.config.strategy_display import STRATEGY_DISPLAY, codename   # noqa: E402

FAILS, OK = [], [0]


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if cond:
        OK[0] += 1
    else:
        FAILS.append(name)


DRAWN = []
_orig = matplotlib.axes.Axes.text


def _spy(self, x, y, s, *a, **k):
    DRAWN.append(str(s))
    return _orig(self, x, y, s, *a, **k)


matplotlib.axes.Axes.text = _spy


def payload(labels, anonymous=True):
    ts = [1577850000 + k * 86400 for k in range(10)]
    return {
        "v": 1, "anonymous": anonymous, "name": None if anonymous else "My book",
        "period": {"from": "2020-01-01", "to": "2026-08-07"}, "basis": "before",
        "strategies": [{"label": l, "color": "#123456", "net": 1000 * (i + 1), "max_dd": 500, "rdd": 2,
                        "trades": 10, "share": 25, "dd_contrib": -100,
                        "sizing": {"base_lots": 10, "mode": "fixed", "lots_min": 10, "lots_max": 10}}
                       for i, l in enumerate(labels)],
        "kpis": {"combined_net": 4000, "combined_max_dd": 800}, "corr": [[1] * len(labels)] * len(labels),
        "monthly": {"months": [{"label": "Jan 2020", "per": [1] * len(labels), "n": [1] * len(labels), "total": 4}]},
        "equity": [{"label": l, "color": "#123456", "ts": ts, "v": list(range(10))} for l in labels]
                  + [{"label": "Portfolio", "color": "#6d28d9", "thick": True, "ts": ts, "v": list(range(10))}],
    }


def drawn(p):
    DRAWN.clear()
    b = P.render_portfolio_pdf(p)
    return b, DRAWN[:]


print("\n── codename map ──")
check("VET_V1 → Velvet, TSG_V1 → Tigris, IC_V2 → Icarus, TMA_V2 → Timberwolf",
      [codename(x) for x in ("VET_V1", "TSG_V1", "IC_V2", "TMA_V2")] == ["Velvet", "Tigris", "Icarus", "Timberwolf"])
check("STFC_V1 → Sabre (backend mirror now matches displayNames.js)", codename("STFC_V1") == "Sabre")

print("\n── anonymous: codenames pass through ──")
b, d = drawn(payload(["Velvet", "Tigris", "Icarus", "Timberwolf"]))
check("renders", b[:5] == b"%PDF-")
for nm in ("Velvet", "Tigris", "Icarus", "Timberwolf"):
    check(f"'{nm}' shown (tables + legend)", sum(1 for s in d if nm in s) >= 4, sum(1 for s in d if nm in s))
check("no alias needed", not any("Strategy A" == s.strip() for s in d))

print("\n── anonymous: leaked labels are replaced ──")
pl = P.public_labels(payload(["VET", "IC_V2", "Iron Condor V2", "Tigris"]))
check("public_labels: non-codenames → Strategy A/B/C, codename kept",
      [s["label"] for s in pl["strategies"]] == ["Strategy A", "Strategy B", "Strategy C", "Tigris"],
      [s["label"] for s in pl["strategies"]])
check("public_labels: equity legend renamed the same way, Portfolio kept",
      [e["label"] for e in pl["equity"]] == ["Strategy A", "Strategy B", "Strategy C", "Tigris", "Portfolio"])
b, d = drawn(payload(["VET", "IC_V2", "Iron Condor V2", "Time Strangle"]))
txt = "\n".join(d)
for leak in ("VET", "IC_V2", "Iron Condor", "Time Strangle"):
    check(f"'{leak}' never drawn", leak not in txt)
check("aliases drawn instead", "Strategy A" in txt and "Strategy D" in txt)
check("input payload not mutated", payload(["VET"])["strategies"][0]["label"] == "VET"
      and P.public_labels(payload(["VET"])) is not None)

print("\n── named (opt-in) report is left alone ──")
b, d = drawn(payload(["VET", "TSG"], anonymous=False))
txt = "\n".join(d)
check("real labels + portfolio name shown when asked", "VET" in txt and "TSG" in txt and "My book" in txt)

print("\n── nothing truncated with the longest codename and longest rule texts ──")
p = payload(["Timberwolf", "Velvet", "Tigris", "Outrider"])
p["any_taxed"] = True
p["strategies"][0].update({"taxed": True, "tax": 1443055, "pre_tax_net": 66000000, "net": 55000000,
                           "sizing": {"base_lots": 10, "mode": "equity", "max_lots": 40, "capital_per_lot": 150000,
                                      "lots_min": 10, "lots_max": 40, "dd_limit_pct": 15, "dd_mode": "hard",
                                      "dd_release_pct": 50, "tax_pct": 30}})
p["strategies"][1]["sizing"] = {"base_lots": 10, "mode": "calendar", "step_months": 12, "add_lots": 10,
                                "max_lots": 40, "lots_min": 10, "lots_max": 40, "dd_limit_pct": 10, "dd_mode": "proportional"}
b, d = drawn(p)
cut = [s for s in d if s.endswith("…")]
check("no table cell, header or legend is cut off with …", not cut, cut)

print("\n── fail-safe ──")
orig = P._public_codenames
P._public_codenames = lambda: set()
pl = P.public_labels(payload(["Velvet", "Tigris"]))
P._public_codenames = orig
check("codename map unavailable → every label becomes an alias",
      [s["label"] for s in pl["strategies"]] == ["Strategy A", "Strategy B"])

if FAILS:
    print(f"\n{len(FAILS)} FAILURES: {FAILS}")
    sys.exit(1)
print(f"\nALL {OK[0]} PORTFOLIO PDF CODENAME CHECKS PASSED")
