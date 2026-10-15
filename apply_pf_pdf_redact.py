#!/usr/bin/env python3
# apply_pf_pdf_redact.py — fence PF_PDF_REDACT_20261015   (requires PF_SAVED_PDF_20261008)
#
# The Backtest → Portfolio PDF is for sharing with anyone, so it now shows
# RESULTS, SIZING and TAX only — nothing about how the strategies trade.
#
#   Never in the PDF (either mode): strategy parameters, run notes, run ids,
#   strategy ids, intraday times (the combined-DD window prints dates only).
#   The page stops sending them, AND the renderer no longer has any code
#   path that reads them, so even an old client payload can't leak them.
#
#   Default (anonymous): strategies print as "Strategy A, B, C…", the title
#   is "Strategy portfolio", the file is portfolio_report_<from>_to_<to>_….pdf.
#   A "Strategy names in PDF" tickbox (off by default, not remembered) brings
#   back strategy labels + the portfolio name for your own copies.
#
#   New "Sizing and tax" section replaces the params section: base lots,
#   sizing rule (fixed / +N lots every M months / equity-based), max lots,
#   lots actually used (min to max), capital per lot, DD guard, tax rate.
#   Contribution table gains a Pre-tax net column when tax applies.
#
#  EDIT  backend/app/backtest/report/portfolio_pdf.py
#        backend/app/api/backtest_portfolio_routes.py   (anonymous file name)
#        frontend/src/pages/backtest/Portfolio.jsx
#  NEW   backend/app/backtest/report/test_portfolio_pdf_redact.py
#  Mirrored to desktop/src-tauri/{backend,frontend}/ when those trees exist.
#  Backtest-only; no live path, no schema, no dependency change.
#
# Usage: python3 apply_pf_pdf_redact.py [--repo PATH] [--allow-dirty]
#                                       [--skip-tests] [--skip-esbuild]
# Run ONLY this script — it carries every payload itself.

from __future__ import annotations

import argparse
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = "PF_PDF_REDACT_20261015"
PARENT = "PF_SAVED_PDF_20261008"
MIRRORS = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
NEW_FILES = {"backend/app/backtest/report/test_portfolio_pdf_redact.py": '# backend/app/backtest/report/test_portfolio_pdf_redact.py\n#\n# ── PF_PDF_REDACT_20261015 ── the shareable portfolio PDF reveals results,\n# sizing and tax only. Every string the renderer draws is captured (all text\n# goes through matplotlib Axes.text) and searched for leaks; the raw PDF\n# bytes (document info / title) are searched too.\n#\n#   cd backend && PYTHONPATH=$PWD python3 app/backtest/report/test_portfolio_pdf_redact.py\n\nimport asyncio\nimport json\nimport os\nimport sys\nimport tempfile\nfrom pathlib import Path\n\nTMP = Path(tempfile.mkdtemp(prefix="pf_redact_"))\nos.environ.setdefault("SCALP_APP_HOME", str(TMP / "home"))\n\nimport matplotlib.axes                                         # noqa: E402\nimport app.backtest.report.portfolio_pdf as P                  # noqa: E402\n\nFAILS, OK = [], [0]\n\n\ndef check(name, cond, detail=""):\n    print(f"  {\'PASS\' if cond else \'FAIL\'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))\n    if cond:\n        OK[0] += 1\n    else:\n        FAILS.append(name)\n\n\nDRAWN = []\n_orig_text = matplotlib.axes.Axes.text\n\n\ndef _spy(self, x, y, s, *a, **k):\n    DRAWN.append(str(s))\n    return _orig_text(self, x, y, s, *a, **k)\n\n\nmatplotlib.axes.Axes.text = _spy\n\nSECRETS = ["SECRETPARAM", "EMA10/20", "SECRETNOTE", "SECRETRUN", "VET_V1", "09:16", "09:24"]\n\n\ndef payload(anonymous=True, taxed=True):\n    labels = ["Strategy A", "Strategy B", "Strategy C"] if anonymous else ["VET", "TSG", "IC2"]\n    strats = []\n    for i, lab in enumerate(labels):\n        strats.append({\n            "label": lab, "color": ["#f97316", "#d946ef", "#818cf8"][i],\n            # a careless / old client might still send these — the renderer must ignore them\n            "sid": "VET_V1", "run_id": "SECRETRUN123", "params": "SECRETPARAM Signal EMA10/20 · TF 5m",\n            "note": "SECRETNOTE 5L non-expiry",\n            "sizing": [\n                {"base_lots": 5, "mode": "equity", "max_lots": 40, "capital_per_lot": 150000, "lots_min": 5,\n                 "lots_max": 40, "dd_limit_pct": 10, "dd_mode": "proportional", "tax_pct": 30},\n                {"base_lots": 10, "mode": "calendar", "step_months": 6, "add_lots": 2, "max_lots": 30,\n                 "lots_min": 10, "lots_max": 30, "dd_limit_pct": 15, "dd_mode": "hard", "dd_release_pct": 50},\n                {"base_lots": 4, "mode": "fixed", "lots_min": 4, "lots_max": 4},\n            ][i],\n            "net": 4556945 - i * 1e6, "pre_tax_net": 6000000 - i * 1e6, "tax": 1443055, "taxed": taxed and i == 0,\n            "max_dd": 302620, "rdd": 15.06, "trades": 2462, "share": 33.0, "dd_contrib": -234220,\n            "worst_day": {"label": "11 Apr 24", "v": -98000, "portfolio_total": -40000}})\n    ts = [1577850000 + k * 86400 for k in range(40)]\n    return {\n        "v": 1, "anonymous": anonymous, "name": None if anonymous else "VET + TSG + IC2",\n        "period": {"from": "2020-01-01", "to": "2026-08-07"}, "generated_label": "15 Oct 2026, 10:00 IST",\n        "basis": "after", "any_taxed": taxed, "any_tax_available": taxed, "combined_tax": 1443055,\n        "strategies": strats,\n        "kpis": {"combined_net": 13729110, "combined_max_dd": 371107, "dd_reduction": 58.9, "sum_ind_dd": 903256,\n                 "return_to_dd": 37.0, "best_single": 19.27},\n        "dd_window": {"from_label": "11 Apr 24", "to_label": "08 May 24"},\n        "corr": [[1, .1, .2], [.1, 1, .3], [.2, .3, 1]],\n        "days": {"rescued": 4, "cluster": 2, "red": 5, "green": 9, "total": 14},\n        "exposure": {"max_concurrent": 3, "max_strats": 3, "n_strats": 3, "peak_notional": 100000, "overlap_pct": 10},\n        "monthly": {"months": [{"label": "Jan 2020", "per": [1, 2, 3], "n": [1, 1, 1], "total": 6}], "stats": {}},\n        "equity": [{"label": l, "color": "#123456", "thick": False, "ts": ts, "v": list(range(40))} for l in labels],\n    }\n\n\ndef render(p):\n    DRAWN.clear()\n    b = P.render_portfolio_pdf(p)\n    return b, "\\n".join(DRAWN)\n\n\nprint("\\n── sizing cells ──")\nc = P.sizing_cells({"base_lots": 5, "mode": "equity", "max_lots": 40, "capital_per_lot": 150000, "lots_min": 5,\n                    "lots_max": 40, "dd_limit_pct": 10, "dd_mode": "proportional", "tax_pct": 30})\ncheck("equity mode", c == ["5", "Equity-based (compounding)", "40", "5 to 40", "₹1.5 L",\n                           "10%, proportional", "30%"], c)\nc = P.sizing_cells({"base_lots": 10, "mode": "calendar", "step_months": 6, "add_lots": 2, "max_lots": 30,\n                    "lots_min": 10, "lots_max": 30, "dd_limit_pct": 15, "dd_mode": "hard", "dd_release_pct": 50})\ncheck("calendar mode + hard guard with release", c == ["10", "+2 lots every 6 months", "30", "10 to 30", "—",\n                                                       "15%, back to base (release 50%)", "—"], c)\nc = P.sizing_cells({"base_lots": 1, "mode": "calendar", "step_months": 1, "add_lots": 1})\ncheck("singular wording", c[1] == "+1 lot every 1 month", c)\nc = P.sizing_cells({"base_lots": 4, "mode": "fixed", "lots_min": 4, "lots_max": 4, "max_lots": 9})\ncheck("fixed lots: single lots-used value, no max", c[:4] == ["4", "Fixed lots", "—", "4"], c)\ncheck("missing sizing block → dashes, no crash", P.sizing_cells(None) == ["—", "Fixed lots", "—", "—", "—", "—", "—"])\ncheck("capital per lot in lakh", P._lakh(250000) == "₹2.5 L" and P._lakh(100000) == "₹1 L" and P._lakh(None) == "—")\n\nprint("\\n── anonymous report (the default) ──")\nb, txt = render(payload(anonymous=True))\ncheck("renders", b[:5] == b"%PDF-")\nfor sec in SECRETS:\n    check(f"never drawn: {sec}", sec not in txt)\nfor nm in ("VET", "TSG", "IC2"):\n    check(f"strategy name \'{nm}\' absent from every drawn string", all(nm not in s.split() and nm + " " not in s\n                                                                      for s in DRAWN))\ncheck("portfolio name absent (drawn + PDF bytes)", "VET + TSG" not in txt and b"VET + TSG" not in b)\ncheck("no secret in raw PDF bytes (title/metadata)", all(s.encode() not in b for s in SECRETS))\ncheck("title is the generic \'Strategy portfolio\'", "Strategy portfolio" in txt)\ncheck("aliases used throughout", txt.count("Strategy A") >= 4, txt.count("Strategy A"))\ncheck("sizing section present", "Sizing and tax" in txt and "Equity-based (compounding)" in txt\n      and "+2 lots every 6 months" in txt and "5 to 40" in txt and "₹1.5 L" in txt)\ncheck("old params section gone", "Strategies" not in [s.strip() for s in DRAWN] and "with their parameters" not in txt)\ncheck("pre-tax net column when tax applies", "Pre-tax net" in txt and "+₹60,00,000" in txt)\ncheck("DD window shown as dates", "11 Apr 24 to 08 May 24" in txt)\n\nprint("\\n── named report (opt-in) ──")\nb, txt = render(payload(anonymous=False, taxed=False))\ncheck("name + labels shown when asked", "VET + TSG + IC2" in txt and "VET" in txt)\nfor sec in SECRETS:\n    check(f"still never drawn: {sec}", sec not in txt)\ncheck("no pre-tax column without tax", "Pre-tax net" not in txt)\n\nprint("\\n── route file name ──")\nimport app.api.backtest_portfolio_routes as R      # noqa: E402\nimport app.utils.app_paths as AP                   # noqa: E402\nAP.APP_HOME = TMP / "home"\nfrom fastapi import FastAPI                         # noqa: E402\n\n\ndef asgi_post(app, path, body):\n    raw = json.dumps(body).encode()\n    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",\n             "path": path, "raw_path": path.encode(), "query_string": b"",\n             "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())],\n             "client": ("127.0.0.1", 5), "server": ("test", 80), "root_path": ""}\n    out = {"headers": {}, "body": b""}\n    done = [False]\n\n    async def receive():\n        if not done[0]:\n            done[0] = True\n            return {"type": "http.request", "body": raw, "more_body": False}\n        return {"type": "http.disconnect"}\n\n    async def send(m):\n        if m["type"] == "http.response.start":\n            out["status"] = m["status"]\n            out["headers"] = {k.decode().lower(): v.decode() for k, v in m["headers"]}\n        else:\n            out["body"] += m.get("body", b"")\n    asyncio.run(app(scope, receive, send))\n    return out\n\n\napp = FastAPI()\napp.include_router(R.router)\nr = asgi_post(app, "/api/backtest/portfolios/report.pdf", payload(anonymous=True))\nfn = r["headers"].get("x-report-file", "")\ncheck("anonymous → file name \'portfolio_report_…\', no names", r["status"] == 200\n      and fn.startswith("portfolio_report_2020-01-01_to_2026-08-07_"), fn)\nr = asgi_post(app, "/api/backtest/portfolios/report.pdf", payload(anonymous=False))\nfn = r["headers"].get("x-report-file", "")\ncheck("named → file name carries the portfolio name", fn.startswith("portfolio_VET_TSG_IC2_"), fn)\n\nif FAILS:\n    print(f"\\n{len(FAILS)} FAILURES: {FAILS}")\n    sys.exit(1)\nprint(f"\\nALL {OK[0]} PORTFOLIO PDF REDACTION CHECKS PASSED")\n'}
SUITES = ["app/backtest/report/test_portfolio_pdf_redact.py", "app/backtest/report/test_portfolio_saved_pdf.py"]
PREREQS = {
    "frontend/src/pages/backtest/lotCompounding.js": ["export function lotCompOf", "export function baseLotsOf",
                                                      "export function lotSizeOf", "export function taxActiveOf"],
    "backend/app/backtest/report/test_portfolio_saved_pdf.py": [PARENT],
}

# ── edits ──
PDF = "backend/app/backtest/report/portfolio_pdf.py"
ROUTES = "backend/app/api/backtest_portfolio_routes.py"
PF = "frontend/src/pages/backtest/Portfolio.jsx"
EDITS = []

# ───────────────────────── renderer ─────────────────────────
EDITS.append((PDF,
"""# Pages (A4 portrait, white, print-friendly):
#   1  header · headline figures · strategies with params/notes · contribution
""",
"""# ── PF_PDF_REDACT_20261015 ── the PDF is meant to be shared with anyone, so it
# shows RESULTS only. It never prints strategy parameters, run notes, run ids
# or intraday times — not even when a payload carries them (the renderer has
# no code path that reads params/note/run_id). With payload.anonymous (the
# page's default) strategies print as "Strategy A/B/…" and the portfolio name
# is dropped. In place of the old params section: a sizing-and-tax table
# (base lots, compounding rule, max lots, lots used, capital per lot, DD guard,
# tax rate) — how the money was sized, not how the strategy trades.
#
# Pages (A4 portrait, white, print-friendly):
#   1  header · headline figures · sizing and tax · contribution
"""))

# one place decides the display name
EDITS.append((PDF,
"""def _ist_dt(ts):""",
"""def _doc_name(p: dict) -> str:   # ── PF_PDF_REDACT_20261015 ──
    if p.get("anonymous"):
        return "Strategy portfolio"
    return _s(p.get("name")).strip() or "Strategy portfolio"


def _ist_dt(ts):"""))

EDITS.append((PDF,
"""    doc.new_page()
    name = _s(p.get("name")).strip() or "Untitled portfolio"
    per = p.get("period") or {}""",
"""    doc.new_page()
    name = _doc_name(p)   # ── PF_PDF_REDACT_20261015 ──
    per = p.get("period") or {}"""))

EDITS.append((PDF,
"""    total = len(doc.pages)
    name = _s(p.get("name")).strip() or "Untitled portfolio"
""",
"""    total = len(doc.pages)
    name = _doc_name(p)   # ── PF_PDF_REDACT_20261015 ──
"""))

EDITS.append((PDF,
"""        name = _s(payload.get("name")).strip() or "Untitled portfolio"
""",
"""        name = _doc_name(payload)   # ── PF_PDF_REDACT_20261015 ──
"""))

EDITS.append((PDF,
"""        _strategies(doc, payload)
""",
"""        _sizing(doc, payload)   # ── PF_PDF_REDACT_20261015 ── replaces the params section
"""))

# replace the params section with sizing & tax
EDITS.append((PDF,
"""def _strategies(doc: Doc, p: dict):
    strats = p.get("strategies") or []
    doc.section("Strategies", "the runs composed, with their parameters", keep=24)
    for s in strats:
        params = wrap(_s(s.get("params")) or "—", CW - 40, 7.0, max_lines=2, sep=" · ")
        note = _s(s.get("note")).strip()
        h = 5.2 + len(params) * lh(7.0) + (lh(7.0) if note else 0) + 2.2
        doc.need(h)
        y0 = doc.y
        doc.rect(MX, y0 + 0.6, 3.0, 3.0, _s(s.get("color"), INK))
        doc.text(MX + 5, y0, fit(_s(s.get("label")), 30, 9, bold=True), size=9, bold=True)
        doc.text(MX + 5, y0 + lh(9), _s(s.get("run_id"))[:8], size=6.8, color=FAINT, mono=True)
        yy = y0 + 0.3
        for line in params:
            doc.text(MX + 40, yy, line, size=7.0, color=INK)
            yy += lh(7.0)
        if note:
            doc.text(MX + 40, yy, fit("Note: " + note, CW - 40, 7.0), size=7.0, color=NOTE, italic=True)
            yy += lh(7.0)
        doc.y = max(yy, y0 + 2 * lh(9)) + 2.2
        doc.hline(MX, MX + CW, doc.y - 1.1, color=RULE, lw=0.4)
""",
"""def _lakh(v) -> str:
    f = _num(v)
    if f is None or f <= 0:
        return "—"
    return f"₹{f / 1e5:.2f}".rstrip("0").rstrip(".") + " L"


def sizing_cells(z: dict) -> List[str]:
    \"\"\"[base lots, sizing rule, max lots, lots used, capital per lot, DD guard, tax]
    from one strategy's `sizing` block. Pure, so it is unit-tested directly.\"\"\"
    z = z if isinstance(z, dict) else {}
    base = _num(z.get("base_lots"))
    mode = _s(z.get("mode"), "fixed").lower()
    step, add = _num(z.get("step_months")), _num(z.get("add_lots"))
    if mode == "equity":
        rule = "Equity-based (compounding)"
    elif mode == "calendar" and step and add:
        rule = f"+{add:g} lot{'s' if add != 1 else ''} every {step:g} month{'s' if step != 1 else ''}"
    else:
        rule = "Fixed lots"
    mx = _num(z.get("max_lots"))
    lo, hi = _num(z.get("lots_min")), _num(z.get("lots_max"))
    used = "—" if hi is None else (f"{hi:g}" if lo is None or lo == hi else f"{lo:g} to {hi:g}")
    dd = _num(z.get("dd_limit_pct"))
    if dd:
        if _s(z.get("dd_mode")).lower().startswith("prop"):
            guard = f"{dd:g}%, proportional"
        else:
            rel = _num(z.get("dd_release_pct")) or 100
            guard = f"{dd:g}%, back to base" + ("" if rel >= 100 else f" (release {rel:g}%)")
    else:
        guard = "—"
    tax = _num(z.get("tax_pct"))
    return ["—" if base is None else f"{base:g}", rule,
            "—" if not mx or mode == "fixed" else f"{mx:g}", used,
            _lakh(z.get("capital_per_lot")), guard, "—" if not tax else f"{tax:g}%"]


def _sizing(doc: Doc, p: dict):
    \"\"\"How each strategy was sized and taxed — never how it trades.\"\"\"
    strats = p.get("strategies") or []
    doc.section("Sizing and tax", "how each strategy was sized in this backtest", keep=8 + 6 * (len(strats) + 1))
    cols = [{"title": "Strategy", "w": 24}, {"title": "Base lots", "w": 16, "align": "right"},
            {"title": "Sizing rule", "w": 46}, {"title": "Max lots", "w": 15, "align": "right"},
            {"title": "Lots used", "w": 18, "align": "right"}, {"title": "Capital/lot", "w": 19, "align": "right"},
            {"title": "DD guard", "w": 28, "align": "right"}, {"title": "Tax", "w": 12, "align": "right"}]
    rows = []
    for s in strats:
        c = sizing_cells(s.get("sizing"))
        rows.append([{"t": _s(s.get("label")), "bold": True, "swatch": _s(s.get("color"), INK), "mono": False},
                     c[0], {"t": c[1], "mono": False}, c[2], c[3], c[4], c[5], c[6]])
    doc.table(cols, rows)
    doc.y += 2
    doc.para("Lots used is the smallest to largest position size actually traded. With equity-based sizing, lots are "
             "recomputed each day from starting capital (base lots × capital per lot) plus realised profit. A DD guard "
             "cuts size while equity is that far below its peak. Tax is applied per financial year on that year’s "
             "realised net, with losses carried forward.")
"""))

# contribution: pre-tax column when tax applies
EDITS.append((PDF,
"""    cols = [{"title": "Strategy", "w": 24},
            {"title": "Net after tax" if any_taxed else "Net P&L", "w": 27, "align": "right"},
            {"title": "Tax", "w": 21, "align": "right"},
            {"title": "Share", "w": 15, "align": "right"},
            {"title": "Own max DD", "w": 24, "align": "right"},
            {"title": "Return ÷ DD", "w": 20, "align": "right"},
            {"title": "Trades", "w": 15, "align": "right"},
            {"title": "P&L in combined DD", "w": 32, "align": "right"}]
    rows = []
    for s in strats:
        share = _num(s.get("share"))
        rows.append([
            {"t": _s(s.get("label")), "bold": True, "swatch": _s(s.get("color"), INK), "mono": False},
""",
"""    # ── PF_PDF_REDACT_20261015 ── pre-tax net beside the after-tax figure when tax applies
    if any_taxed:
        cols = [{"title": "Strategy", "w": 24}, {"title": "Pre-tax net", "w": 24, "align": "right"},
                {"title": "Tax", "w": 22, "align": "right"}, {"title": "Net after tax", "w": 24, "align": "right"},
                {"title": "Share", "w": 12, "align": "right"}, {"title": "Own max DD", "w": 21, "align": "right"},
                {"title": "Ret. ÷ DD", "w": 15, "align": "right"}, {"title": "Trades", "w": 12, "align": "right"},
                {"title": "P&L in DD", "w": 24, "align": "right"}]
    else:
        cols = [{"title": "Strategy", "w": 24},
                {"title": "Net P&L", "w": 27, "align": "right"},
                {"title": "Tax", "w": 21, "align": "right"},
                {"title": "Share", "w": 15, "align": "right"},
                {"title": "Own max DD", "w": 24, "align": "right"},
                {"title": "Return ÷ DD", "w": 20, "align": "right"},
                {"title": "Trades", "w": 15, "align": "right"},
                {"title": "P&L in combined DD", "w": 32, "align": "right"}]
    rows = []
    for s in strats:
        share = _num(s.get("share"))
        pre = [{"t": money(s.get("pre_tax_net")), "color": pnl_color(s.get("pre_tax_net"))}] if any_taxed else []
        tax_cell = {"t": money(-(_num(s.get("tax")) or 0)) if s.get("taxed") else "—",
                    "color": LOSS if s.get("taxed") else FAINT}
        net_cell = {"t": money(s.get("net")), "color": pnl_color(s.get("net")), "bold": True}
        mid = (pre + [tax_cell, net_cell]) if any_taxed else [net_cell, tax_cell]
        rows.append([
            {"t": _s(s.get("label")), "bold": True, "swatch": _s(s.get("color"), INK), "mono": False},
            *mid,
"""))
EDITS.append((PDF,
"""            *mid,
            {"t": money(s.get("net")), "color": pnl_color(s.get("net")), "bold": True},
            {"t": money(-(_num(s.get("tax")) or 0)) if s.get("taxed") else "—",
             "color": LOSS if s.get("taxed") else FAINT},
            "—" if share is None else f"{share:.0f}%",""",
"""            *mid,
            "—" if share is None else f"{share:.0f}%","""))

# ───────────────────────── route filename ─────────────────────────
EDITS.append((ROUTES,
"""    fname = f"portfolio_{_safe_name(payload.get('name') or 'untitled')}_{_safe_name(per.get('from'))}_to_" \\
            f"{_safe_name(per.get('to'))}_{stamp}.pdf\"""",
"""    # ── PF_PDF_REDACT_20261015 ── an anonymous report's file name carries no names either
    nm = "report" if payload.get("anonymous") else (payload.get("name") or "report")
    fname = f"portfolio_{_safe_name(nm)}_{_safe_name(per.get('from'))}_to_" \\
            f"{_safe_name(per.get('to'))}_{stamp}.pdf\""""))

# ───────────────────────── Portfolio.jsx ─────────────────────────
EDITS.append((PF,
"""import { fyTaxOf } from "./lotCompounding";   // ── PF_NET_AFTER_TAX_20260930 ── fallback when a run has no stamped schedule
""",
"""import { fyTaxOf } from "./lotCompounding";   // ── PF_NET_AFTER_TAX_20260930 ── fallback when a run has no stamped schedule
import { lotCompOf, baseLotsOf, lotSizeOf, taxActiveOf } from "./lotCompounding";   // ── PF_PDF_REDACT_20261015 ──
"""))

EDITS.append((PF,
"""   can never disagree with the screen. Infinity travels as null + *_inf. */
export function buildPortfolioReportPayload(pf, ctx = {}) {
  const fin = (v) => (typeof v === "number" && isFinite(v) ? v : null);
  const lab = (sid) => STRAT_LABEL[sid] || sid;
""",
"""   can never disagree with the screen. Infinity travels as null + *_inf.
   ── PF_PDF_REDACT_20261015 ── the PDF is for sharing: NO params, notes, run
   ids, strategy ids or intraday times leave the page. ctx.anonymous (default
   true) also replaces strategy names with "Strategy A/B/…" and drops the
   portfolio name. Each strategy carries a `sizing` block instead. */
export function reportSizingOf(run, closed) {
  const cfg = run?.config || {};
  const s = run?.summary || {};
  const lc = lotCompOf(cfg, run?.date_from, run?.date_to, s);
  const ls = lotSizeOf(cfg, run?.strategy_id, run?.underlying);
  let qMin = Number(s.qty_min) > 0 ? Number(s.qty_min) : null;
  let qMax = Number(s.qty_max) > 0 ? Number(s.qty_max) : null;
  if (qMax == null) {
    const qs = (closed || []).map((t) => Number(t.qty)).filter((q) => q > 0);
    if (qs.length) { qMin = Math.min(...qs); qMax = Math.max(...qs); }
  }
  const lots = (q) => (q != null && ls ? Math.round(q / ls) : null);
  const n = (v) => (Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : null);
  return {
    base_lots: baseLotsOf(cfg),
    mode: lc ? lc.mode : "fixed",
    step_months: lc && lc.mode === "calendar" ? lc.step : null,
    add_lots: lc && lc.mode === "calendar" ? lc.add : null,
    max_lots: lc && lc.max ? lc.max : null,
    capital_per_lot: lc ? n(lc.cpl) : n(cfg.lot_comp_capital_per_lot),
    dd_limit_pct: lc && lc.ddLimit ? lc.ddLimit : null,
    dd_mode: lc && lc.ddLimit ? lc.ddMode : null,
    dd_release_pct: lc && lc.ddLimit ? lc.ddRelease : null,
    lots_min: lots(qMin), lots_max: lots(qMax),
    tax_pct: taxActiveOf(cfg) ? Number(cfg.lot_comp_tax_pct) : null,
  };
}
export function buildPortfolioReportPayload(pf, ctx = {}) {
  const fin = (v) => (typeof v === "number" && isFinite(v) ? v : null);
  const anonymous = ctx.anonymous !== false;
  const alias = new Map(pf.perStrat.map((s, i) => [s.run.strategy_id, `Strategy ${String.fromCharCode(65 + i)}`]));
  const lab = (sid) => (anonymous ? alias.get(sid) || "Strategy" : STRAT_LABEL[sid] || sid);
  const dateOf = (ts) => fmtDayLabel(istDayKey(ts));   // dates only: intraday times reveal entry timing
"""))

EDITS.append((PF,
"""    const dayRow = wi ? pf.days.find((d) => d.day === wi.k) : null;
    let params = "";
    try {
      params = ctx.describeConfig ? ctx.describeConfig(s.run.config).map(([k, v]) => `${k} ${v}`).join(" · ") : "";
    } catch { params = ""; }
    return {
      sid, label: lab(sid), color: ACCENT[sid] || "#6b6680", run_id: s.run.run_id,
      params, note: s.run.note || null,
      net: fin(s.net),""",
"""    const dayRow = wi ? pf.days.find((d) => d.day === wi.k) : null;
    return {
      label: lab(sid), color: ACCENT[sid] || "#6b6680",
      sizing: reportSizingOf(s.run, s.closed),
      net: fin(s.net),"""))

EDITS.append((PF,
"""    v: 1,
    name: ctx.name || strategies.map((s) => s.label).join(" + "),""",
"""    v: 1,
    anonymous,
    name: anonymous ? null : (ctx.name || strategies.map((s) => s.label).join(" + ")),"""))

EDITS.append((PF,
"""    dd_window: pf.ddPeakTs ? { from_label: fmtTsL(pf.ddPeakTs), to_label: fmtTsL(pf.ddTroughTs) } : null,""",
"""    dd_window: pf.ddPeakTs ? { from_label: dateOf(pf.ddPeakTs), to_label: dateOf(pf.ddTroughTs) } : null,"""))

EDITS.append((PF,
"""  const fmtTsL = ctx.fmtTs || ((x) => String(x));
""",
""""""))

# UI: names toggle (default off)
EDITS.append((PF,
"""  const [pdfBusy, setPdfBusy] = useState(false);
""",
"""  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfNames, setPdfNames] = useState(false);   // ── PF_PDF_REDACT_20261015 ── off = "Strategy A/B/…" (safe to share)
"""))

EDITS.append((PF,
"""              <button style={{ ...smallBtn("default"), marginLeft: "auto" }} disabled={pdfBusy} data-testid="pf-pdf"
                title="Download this analysis as a PDF to share. Uses the basis shown above."
""",
"""              {/* ── PF_PDF_REDACT_20261015 ── names are opt-in; params/notes/ids/times are never in the PDF */}
              <label data-testid="pf-pdf-names" style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 6,
                fontSize: 12, color: c.text.muted, cursor: "pointer" }}
                title="Off: strategies print as Strategy A, B, C and the portfolio name is left out. Parameters, notes, run ids and trade times are never included.">
                <input type="checkbox" checked={pdfNames} onChange={(e) => setPdfNames(e.target.checked)} />
                Strategy names in PDF
              </label>
              <button style={smallBtn("default")} disabled={pdfBusy} data-testid="pf-pdf"
                title="Download this analysis as a PDF to share. Results, sizing and tax only. Uses the basis shown above."
"""))

EDITS.append((PF,
"""                    const body = buildPortfolioReportPayload(pf, {
                      name: activePfName, describeConfig, fmtTs,
""",
"""                    const body = buildPortfolioReportPayload(pf, {
                      name: activePfName, anonymous: !pdfNames,
"""))


def die(msg: str) -> None:
    print(f"\n✗ {msg}")
    sys.exit(1)


def git_dirty(repo: Path, rel: str) -> bool:
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", rel], cwd=repo,
                             capture_output=True, text=True, timeout=30).stdout
        return bool(out.strip())
    except Exception:
        return False


def find_esbuild(repo: Path):
    for c in (repo / "frontend/node_modules/.bin/esbuild", repo / "desktop/node_modules/.bin/esbuild"):
        if c.exists():
            return str(c)
    return shutil.which("esbuild")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app"))
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--skip-esbuild", action="store_true")
    a = ap.parse_args()
    repo = Path(a.repo).expanduser().resolve()
    print(f"[{FENCE}] repo: {repo}")

    targets = sorted({f for f, _, _ in EDITS})
    for rel in targets:
        p = repo / rel
        if not p.exists():
            die(f"missing {rel}")
        txt = p.read_text(encoding="utf-8")
        if FENCE in txt:
            die(f"{rel} already carries {FENCE} — already applied, nothing to do")
        if PARENT not in txt:
            die(f"{rel} lacks {PARENT} — apply apply_pf_saved_pdf.py first")
    for rel in NEW_FILES:
        if (repo / rel).exists():
            die(f"{rel} already exists — already applied (or a stray copy); nothing written")
    for rel, marks in PREREQS.items():
        p = repo / rel
        if not p.exists():
            die(f"prerequisite file missing: {rel}")
        txt = p.read_text(encoding="utf-8")
        for mk in marks:
            if mk not in txt:
                die(f"prerequisite '{mk}' not found in {rel}")
    dirty = [rel for rel in targets if git_dirty(repo, rel)]
    if dirty and not a.allow_dirty:
        die("uncommitted changes in " + ", ".join(dirty) + " — commit/stash first or re-run with --allow-dirty")

    staged = {rel: (repo / rel).read_text(encoding="utf-8") for rel in targets}
    original = dict(staged)
    for rel, old, new in EDITS:
        n = staged[rel].count(old)
        if n != 1:
            die(f"anchor matched {n}× (need 1) in {rel}:\n---\n{old[:300]}\n---")
        staged[rel] = staged[rel].replace(old, new, 1)

    with tempfile.TemporaryDirectory() as td:
        for rel, txt in list(NEW_FILES.items()) + [(r, staged[r]) for r in targets if r.endswith(".py")]:
            tp = Path(td) / Path(rel).name
            tp.write_text(txt, encoding="utf-8")
            try:
                py_compile.compile(str(tp), doraise=True)
            except py_compile.PyCompileError as e:
                die(f"py_compile failed for staged {rel}: {e}")
        print("  ✓ py_compile: renderer, routes, new test")
        if not a.skip_esbuild:
            esb = find_esbuild(repo)
            jsx = "frontend/src/pages/backtest/Portfolio.jsx"
            if esb:
                tp = Path(td) / "Portfolio.jsx"
                tp.write_text(staged[jsx], encoding="utf-8")
                r = subprocess.run([esb, str(tp), "--loader:.jsx=jsx", "--log-level=error"], capture_output=True, text=True)
                if r.returncode != 0:
                    die(f"esbuild could not parse staged {jsx}:\n{r.stderr[:2000]}")
                print("  ✓ esbuild parsed staged Portfolio.jsx")
            else:
                print("  ! esbuild not found — JSX parse gate skipped (npm run build will check)")

    def rollback(reason: str) -> None:
        for rel in targets:
            (repo / rel).write_text(original[rel], encoding="utf-8")
            (repo / rel).with_name(Path(rel).name + f".bak-{FENCE}").unlink(missing_ok=True)
        for rel in NEW_FILES:
            (repo / rel).unlink(missing_ok=True)
        die(reason + " — every file restored to its pre-apply content, new file removed")

    written = []
    try:
        for rel in targets:
            p = repo / rel
            shutil.copy2(p, p.with_name(p.name + f".bak-{FENCE}"))
        for rel in targets:
            (repo / rel).write_text(staged[rel], encoding="utf-8")
            written.append(rel)
        for rel, txt in NEW_FILES.items():
            (repo / rel).write_text(txt, encoding="utf-8")
            written.append(rel)
    except Exception as e:
        rollback(f"write failed ({e!r})")
    for rel in written:
        print(f"  ✓ wrote {rel}")

    if not a.skip_tests:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=str(repo / "backend") + os.pathsep + os.environ.get("PYTHONPATH", ""))
        for script in SUITES:
            r = subprocess.run([sys.executable, script], cwd=repo / "backend", capture_output=True, text=True,
                               env=env, timeout=600)
            last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
            if r.returncode != 0 or "FAIL " in r.stdout:
                print(r.stdout[-5000:])
                print(r.stderr[-3000:])
                rollback(f"suite {script} FAILED")
            print(f"  ✓ {Path(script).name}: {last}")

    for rel in written:
        for src_pre, dst_pre in MIRRORS.items():
            if rel.startswith(src_pre) and (repo / dst_pre).exists():
                dst = repo / (dst_pre + rel[len(src_pre):])
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    shutil.copy2(dst, dst.with_name(dst.name + f".bak-{FENCE}"))
                shutil.copy2(repo / rel, dst)
                print(f"  ✓ mirrored → {dst.relative_to(repo)}")

    print(f"\n✓ {FENCE} applied. Rebuild (backend + frontend). The portfolio PDF now shows results, sizing and tax only;\n"
          f"  tick 'Strategy names in PDF' next to the download only for copies you keep yourself.")


if __name__ == "__main__":
    main()
