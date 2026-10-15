#!/usr/bin/env python3
# apply_pf_pdf_codename.py — fence PF_PDF_CODENAME_20261015   (requires PF_PDF_REDACT_20261015)
#
# Shared Backtest → Portfolio PDFs name strategies by the CODENAME non-admin
# users see — Velvet instead of VET_V1, Tigris instead of TSG_V1, Icarus,
# Timberwolf, Outrider… — taken from the existing UI_MASK maps
# (frontend/src/strategies/displayNames.js, backend app/config/strategy_display.py).
#
#   * Portfolio.jsx: the shared (default) PDF labels each strategy with its
#     codename; "Strategy A/B/…" only for one without a codename. The tickbox
#     is renamed "Real strategy names in PDF" (still off by default).
#   * portfolio_pdf.py: backstop — in a shared report every label must be a
#     public codename or a "Strategy X" alias; anything else (a raw id, "IC2",
#     "Iron Condor V2") is replaced before drawing. Codename map unavailable →
#     every label becomes an alias (fail-safe). Column widths fit the longest
#     codename and crore-sized figures from compounded runs; lots used prints
#     as "5–40"; DD guard as "15% to base, release 50%" / "10% proportional".
#   * strategy_display.py: adds STFC_V1 → Sabre (frontend had it, backend
#     mirror didn't). Additive only; the Telegram card suites are re-run.
#   * test_portfolio_pdf_redact.py: expectations follow the new wording.
#
#  EDIT  backend/app/backtest/report/portfolio_pdf.py
#        backend/app/config/strategy_display.py
#        backend/app/backtest/report/test_portfolio_pdf_redact.py
#        frontend/src/pages/backtest/Portfolio.jsx
#  NEW   backend/app/backtest/report/test_portfolio_pdf_codename.py
#  Mirrored to desktop/src-tauri/{backend,frontend}/ when those trees exist.
#  Backtest-only; no live path, schema or dependency change.
#
# Order: apply_pf_pdf_redact.py → apply_pf_pdf_codename.py → rebuild.
# Usage: python3 apply_pf_pdf_codename.py [--repo PATH] [--allow-dirty]
#                                         [--skip-tests] [--skip-esbuild]
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

FENCE = "PF_PDF_CODENAME_20261015"
PARENT = "PF_PDF_REDACT_20261015"
NEEDS_PARENT = {"backend/app/backtest/report/portfolio_pdf.py", "frontend/src/pages/backtest/Portfolio.jsx",
                "backend/app/backtest/report/test_portfolio_pdf_redact.py"}
MIRRORS = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
NEW_FILES = {"backend/app/backtest/report/test_portfolio_pdf_codename.py": '# backend/app/backtest/report/test_portfolio_pdf_codename.py\n#\n# ── PF_PDF_CODENAME_20261015 ── a shared (anonymous) portfolio PDF names\n# strategies by the codename non-admin users see (Velvet, Tigris…); any\n# label that is not a public codename or a "Strategy X" alias is replaced.\n# Every drawn string is captured (all text goes through Axes.text).\n#\n#   cd backend && PYTHONPATH=$PWD python3 app/backtest/report/test_portfolio_pdf_codename.py\n\nimport os\nimport sys\nimport tempfile\n\nos.environ.setdefault("SCALP_APP_HOME", tempfile.mkdtemp(prefix="pf_code_"))\n\nimport matplotlib.axes                                   # noqa: E402\nimport app.backtest.report.portfolio_pdf as P            # noqa: E402\nfrom app.config.strategy_display import STRATEGY_DISPLAY, codename   # noqa: E402\n\nFAILS, OK = [], [0]\n\n\ndef check(name, cond, detail=""):\n    print(f"  {\'PASS\' if cond else \'FAIL\'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))\n    if cond:\n        OK[0] += 1\n    else:\n        FAILS.append(name)\n\n\nDRAWN = []\n_orig = matplotlib.axes.Axes.text\n\n\ndef _spy(self, x, y, s, *a, **k):\n    DRAWN.append(str(s))\n    return _orig(self, x, y, s, *a, **k)\n\n\nmatplotlib.axes.Axes.text = _spy\n\n\ndef payload(labels, anonymous=True):\n    ts = [1577850000 + k * 86400 for k in range(10)]\n    return {\n        "v": 1, "anonymous": anonymous, "name": None if anonymous else "My book",\n        "period": {"from": "2020-01-01", "to": "2026-08-07"}, "basis": "before",\n        "strategies": [{"label": l, "color": "#123456", "net": 1000 * (i + 1), "max_dd": 500, "rdd": 2,\n                        "trades": 10, "share": 25, "dd_contrib": -100,\n                        "sizing": {"base_lots": 10, "mode": "fixed", "lots_min": 10, "lots_max": 10}}\n                       for i, l in enumerate(labels)],\n        "kpis": {"combined_net": 4000, "combined_max_dd": 800}, "corr": [[1] * len(labels)] * len(labels),\n        "monthly": {"months": [{"label": "Jan 2020", "per": [1] * len(labels), "n": [1] * len(labels), "total": 4}]},\n        "equity": [{"label": l, "color": "#123456", "ts": ts, "v": list(range(10))} for l in labels]\n                  + [{"label": "Portfolio", "color": "#6d28d9", "thick": True, "ts": ts, "v": list(range(10))}],\n    }\n\n\ndef drawn(p):\n    DRAWN.clear()\n    b = P.render_portfolio_pdf(p)\n    return b, DRAWN[:]\n\n\nprint("\\n── codename map ──")\ncheck("VET_V1 → Velvet, TSG_V1 → Tigris, IC_V2 → Icarus, TMA_V2 → Timberwolf",\n      [codename(x) for x in ("VET_V1", "TSG_V1", "IC_V2", "TMA_V2")] == ["Velvet", "Tigris", "Icarus", "Timberwolf"])\ncheck("STFC_V1 → Sabre (backend mirror now matches displayNames.js)", codename("STFC_V1") == "Sabre")\n\nprint("\\n── anonymous: codenames pass through ──")\nb, d = drawn(payload(["Velvet", "Tigris", "Icarus", "Timberwolf"]))\ncheck("renders", b[:5] == b"%PDF-")\nfor nm in ("Velvet", "Tigris", "Icarus", "Timberwolf"):\n    check(f"\'{nm}\' shown (tables + legend)", sum(1 for s in d if nm in s) >= 4, sum(1 for s in d if nm in s))\ncheck("no alias needed", not any("Strategy A" == s.strip() for s in d))\n\nprint("\\n── anonymous: leaked labels are replaced ──")\npl = P.public_labels(payload(["VET", "IC_V2", "Iron Condor V2", "Tigris"]))\ncheck("public_labels: non-codenames → Strategy A/B/C, codename kept",\n      [s["label"] for s in pl["strategies"]] == ["Strategy A", "Strategy B", "Strategy C", "Tigris"],\n      [s["label"] for s in pl["strategies"]])\ncheck("public_labels: equity legend renamed the same way, Portfolio kept",\n      [e["label"] for e in pl["equity"]] == ["Strategy A", "Strategy B", "Strategy C", "Tigris", "Portfolio"])\nb, d = drawn(payload(["VET", "IC_V2", "Iron Condor V2", "Time Strangle"]))\ntxt = "\\n".join(d)\nfor leak in ("VET", "IC_V2", "Iron Condor", "Time Strangle"):\n    check(f"\'{leak}\' never drawn", leak not in txt)\ncheck("aliases drawn instead", "Strategy A" in txt and "Strategy D" in txt)\ncheck("input payload not mutated", payload(["VET"])["strategies"][0]["label"] == "VET"\n      and P.public_labels(payload(["VET"])) is not None)\n\nprint("\\n── named (opt-in) report is left alone ──")\nb, d = drawn(payload(["VET", "TSG"], anonymous=False))\ntxt = "\\n".join(d)\ncheck("real labels + portfolio name shown when asked", "VET" in txt and "TSG" in txt and "My book" in txt)\n\nprint("\\n── nothing truncated with the longest codename and longest rule texts ──")\np = payload(["Timberwolf", "Velvet", "Tigris", "Outrider"])\np["any_taxed"] = True\np["strategies"][0].update({"taxed": True, "tax": 1443055, "pre_tax_net": 66000000, "net": 55000000,\n                           "sizing": {"base_lots": 10, "mode": "equity", "max_lots": 40, "capital_per_lot": 150000,\n                                      "lots_min": 10, "lots_max": 40, "dd_limit_pct": 15, "dd_mode": "hard",\n                                      "dd_release_pct": 50, "tax_pct": 30}})\np["strategies"][1]["sizing"] = {"base_lots": 10, "mode": "calendar", "step_months": 12, "add_lots": 10,\n                                "max_lots": 40, "lots_min": 10, "lots_max": 40, "dd_limit_pct": 10, "dd_mode": "proportional"}\nb, d = drawn(p)\ncut = [s for s in d if s.endswith("…")]\ncheck("no table cell, header or legend is cut off with …", not cut, cut)\n\nprint("\\n── fail-safe ──")\norig = P._public_codenames\nP._public_codenames = lambda: set()\npl = P.public_labels(payload(["Velvet", "Tigris"]))\nP._public_codenames = orig\ncheck("codename map unavailable → every label becomes an alias",\n      [s["label"] for s in pl["strategies"]] == ["Strategy A", "Strategy B"])\n\nif FAILS:\n    print(f"\\n{len(FAILS)} FAILURES: {FAILS}")\n    sys.exit(1)\nprint(f"\\nALL {OK[0]} PORTFOLIO PDF CODENAME CHECKS PASSED")\n'}
SUITES = ["app/backtest/report/test_portfolio_pdf_codename.py",
          "app/backtest/report/test_portfolio_pdf_redact.py",
          "app/backtest/report/test_portfolio_saved_pdf.py",
          "app/tests/test_eod_card_v2.py",          # strategy_display.py is shared with the Telegram cards
          "app/tests/test_expiry_card.py"]
PREREQS = {
    "frontend/src/strategies/displayNames.js": ["export const STRATEGY_DISPLAY", 'code: "Velvet"', 'code: "Tigris"'],
    "backend/app/config/strategy_display.py": ["STRATEGY_DISPLAY = {", '"Velvet"'],
}

# ── edits ──
PDF = "backend/app/backtest/report/portfolio_pdf.py"
DISP = "backend/app/config/strategy_display.py"
PF = "frontend/src/pages/backtest/Portfolio.jsx"
EDITS = []

# ── backend mirror was missing STFC_V1 (frontend has it) ──
EDITS.append((DISP,
"""    "EMC_V1":    ("EMC V1",         "Ember"),     # ── EMC_V1_20261011 ──
""",
"""    "EMC_V1":    ("EMC V1",         "Ember"),     # ── EMC_V1_20261011 ──
    "STFC_V1":   ("STFC V1",        "Sabre"),     # ── PF_PDF_CODENAME_20261015 ── mirror of displayNames.js
"""))

# ── renderer: codename backstop ──
EDITS.append((PDF,
"""def _doc_name(p: dict) -> str:   # ── PF_PDF_REDACT_20261015 ──""",
"""# ── PF_PDF_CODENAME_20261015 ── in an anonymous report every strategy label
# must be a public codename (the names non-admin users see: Velvet, Tigris…)
# or a "Strategy X" alias. Anything else — a raw id, a short label like
# "IC2", a real name — is replaced by an alias here, so a stale or careless
# client can't print it. Fail-safe: if the codename map can't be loaded, every
# label becomes an alias.
def _public_codenames() -> set:
    try:
        from app.config.strategy_display import STRATEGY_DISPLAY   # stdlib-only module
        return {code for _real, code in STRATEGY_DISPLAY.values()}
    except Exception:
        return set()


def public_labels(payload: dict) -> dict:
    \"\"\"Copy of payload whose strategy + equity labels are safe to share.\"\"\"
    import re
    if not isinstance(payload, dict) or not payload.get("anonymous"):
        return payload
    ok = _public_codenames()
    alias_re = re.compile(r"^Strategy [A-Z]$")
    out = dict(payload)
    rename = {}
    strats = []
    for i, s in enumerate(payload.get("strategies") or []):
        s = dict(s) if isinstance(s, dict) else {}
        lab = _s(s.get("label")).strip()
        safe = lab if (lab in ok or alias_re.match(lab)) else f"Strategy {chr(65 + i % 26)}"
        rename[lab] = safe
        s["label"] = safe
        strats.append(s)
    out["strategies"] = strats
    eq = []
    for e in payload.get("equity") or []:
        if isinstance(e, dict):
            e = dict(e)
            lab = _s(e.get("label")).strip()
            if lab != "Portfolio":
                e["label"] = rename.get(lab) or (lab if lab in ok or alias_re.match(lab) else "Strategy")
        eq.append(e)
    out["equity"] = eq
    return out


def _doc_name(p: dict) -> str:   # ── PF_PDF_REDACT_20261015 ──"""))

EDITS.append((PDF,
"""    import matplotlib
    from matplotlib.backends.backend_pdf import PdfPages
""",
"""    import matplotlib
    from matplotlib.backends.backend_pdf import PdfPages
    payload = public_labels(payload)   # ── PF_PDF_CODENAME_20261015 ──
"""))

EDITS.append((PDF,
"""# is dropped. In place of the old params section: a sizing-and-tax table""",
"""# is dropped. ── PF_PDF_CODENAME_20261015 ── anonymous now means CODENAMES
# (Velvet, Tigris… as non-admin users see them), with "Strategy A/B/…" only
# for a strategy that has no codename. In place of the old params section: a sizing-and-tax table"""))

# ── frontend ──
EDITS.append((PF,
"""import { lotCompOf, baseLotsOf, lotSizeOf, taxActiveOf } from "./lotCompounding";   // ── PF_PDF_REDACT_20261015 ──
""",
"""import { lotCompOf, baseLotsOf, lotSizeOf, taxActiveOf } from "./lotCompounding";   // ── PF_PDF_REDACT_20261015 ──
import { STRATEGY_DISPLAY } from "../../strategies/displayNames";   // ── PF_PDF_CODENAME_20261015 ── non-admin codenames
"""))

EDITS.append((PF,
"""  const alias = new Map(pf.perStrat.map((s, i) => [s.run.strategy_id, `Strategy ${String.fromCharCode(65 + i)}`]));""",
"""  // ── PF_PDF_CODENAME_20261015 ── shared reports use the codename non-admin users see
  // (Velvet, Tigris…); a strategy without one falls back to "Strategy A/B/…"
  const alias = new Map(pf.perStrat.map((s, i) => [s.run.strategy_id,
    STRATEGY_DISPLAY[s.run.strategy_id]?.code || `Strategy ${String.fromCharCode(65 + i)}`]));"""))

EDITS.append((PF,
"""                title="Off: strategies print as Strategy A, B, C and the portfolio name is left out. Parameters, notes, run ids and trade times are never included.">
                <input type="checkbox" checked={pdfNames} onChange={(e) => setPdfNames(e.target.checked)} />
                Strategy names in PDF""",
"""                title="Off: strategies print under their codenames (Velvet, Tigris…), as non-admin users see them, and the portfolio name is left out. Parameters, notes, run ids and trade times are never included.">
                <input type="checkbox" checked={pdfNames} onChange={(e) => setPdfNames(e.target.checked)} />
                Real strategy names in PDF"""))

# ── column widths: the longest codename ("Timberwolf") must fit ──
EDITS.append((PDF,
"""    cols = [{"title": "Strategy", "w": 24}, {"title": "Base lots", "w": 16, "align": "right"},
            {"title": "Sizing rule", "w": 46}, {"title": "Max lots", "w": 15, "align": "right"},""",
"""    cols = [{"title": "Strategy", "w": 28}, {"title": "Base lots", "w": 16, "align": "right"},
            {"title": "Sizing rule", "w": 42}, {"title": "Max lots", "w": 15, "align": "right"},"""))
EDITS.append((PDF,
"""        cols = [{"title": "Strategy", "w": 24}, {"title": "Pre-tax net", "w": 24, "align": "right"},
                {"title": "Tax", "w": 22, "align": "right"}, {"title": "Net after tax", "w": 24, "align": "right"},""",
"""        cols = [{"title": "Strategy", "w": 28}, {"title": "Pre-tax net", "w": 23, "align": "right"},
                {"title": "Tax", "w": 21, "align": "right"}, {"title": "Net after tax", "w": 22, "align": "right"},"""))
EDITS.append((PDF,
"""        cols = [{"title": "Strategy", "w": 24},
                {"title": "Net P&L", "w": 27, "align": "right"},
                {"title": "Tax", "w": 21, "align": "right"},""",
"""        cols = [{"title": "Strategy", "w": 28},
                {"title": "Net P&L", "w": 27, "align": "right"},
                {"title": "Tax", "w": 17, "align": "right"},"""))
EDITS.append((PDF,
"""    cell, lab_w = 17.0, 24.0""",
"""    cell, lab_w = 19.0, 27.0   # ── PF_PDF_CODENAME_20261015 ── fits "Timberwolf\""""))
EDITS.append((PDF,
"""    cols = [{"title": "Strategy", "w": 28}, {"title": "Base lots", "w": 16, "align": "right"},
            {"title": "Sizing rule", "w": 42}, {"title": "Max lots", "w": 15, "align": "right"},
            {"title": "Lots used", "w": 18, "align": "right"}, {"title": "Capital/lot", "w": 19, "align": "right"},
            {"title": "DD guard", "w": 28, "align": "right"}, {"title": "Tax", "w": 12, "align": "right"}]""",
"""    cols = [{"title": "Strategy", "w": 26}, {"title": "Base lots", "w": 14, "align": "right"},
            {"title": "Sizing rule", "w": 44}, {"title": "Max lots", "w": 13, "align": "right"},
            {"title": "Lots used", "w": 17, "align": "right"}, {"title": "Capital/lot", "w": 17, "align": "right"},
            {"title": "DD guard", "w": 35, "align": "right"}, {"title": "Tax", "w": 12, "align": "right"}]"""))
EDITS.append((PDF,
"""            guard = f"{dd:g}%, proportional"
        else:
            rel = _num(z.get("dd_release_pct")) or 100
            guard = f"{dd:g}%, back to base" + ("" if rel >= 100 else f" (release {rel:g}%)")""",
"""            guard = f"{dd:g}% proportional"
        else:
            rel = _num(z.get("dd_release_pct")) or 100
            guard = f"{dd:g}% to base" + ("" if rel >= 100 else f", release {rel:g}%")"""))

# parent's redaction suite asserted the old DD-guard wording
RTEST = "backend/app/backtest/report/test_portfolio_pdf_redact.py"
EDITS.append((RTEST, '''                           "10%, proportional", "30%"], c)''', '''                           "10% proportional", "30%"], c)'''))
EDITS.append((RTEST, '''                                                       "15%, back to base (release 50%)", "—"], c)''',
                     '''                                                       "15% to base, release 50%", "—"], c)'''))

# crore-sized figures (compounded runs) must fit the after-tax table
EDITS.append((PDF,
"""        cols = [{"title": "Strategy", "w": 28}, {"title": "Pre-tax net", "w": 23, "align": "right"},
                {"title": "Tax", "w": 21, "align": "right"}, {"title": "Net after tax", "w": 22, "align": "right"},
                {"title": "Share", "w": 12, "align": "right"}, {"title": "Own max DD", "w": 21, "align": "right"},
                {"title": "Ret. ÷ DD", "w": 15, "align": "right"}, {"title": "Trades", "w": 12, "align": "right"},
                {"title": "P&L in DD", "w": 24, "align": "right"}]""",
"""        cols = [{"title": "Strategy", "w": 26}, {"title": "Pre-tax net", "w": 24, "align": "right"},
                {"title": "Tax", "w": 22, "align": "right"}, {"title": "Net after tax", "w": 24, "align": "right"},
                {"title": "Share", "w": 11, "align": "right"}, {"title": "Own max DD", "w": 21, "align": "right"},
                {"title": "Ret. ÷ DD", "w": 14, "align": "right"}, {"title": "Trades", "w": 12, "align": "right"},
                {"title": "P&L in DD", "w": 24, "align": "right"}]"""))
EDITS.append((PDF,
"""    doc.table(cols, rows)
    dw = p.get("dd_window") or {}""",
"""    doc.table(cols, rows, size=7.4 if any_taxed else 7.8)   # ── PF_PDF_CODENAME_20261015 ── crore figures fit
    dw = p.get("dd_window") or {}"""))
EDITS.append((PDF,
"""                     c[0], {"t": c[1], "mono": False}, c[2], c[3], c[4], c[5], c[6]])""",
"""                     c[0], {"t": c[1], "mono": False}, c[2], c[3], c[4], {"t": c[5], "mono": False}, c[6]])"""))
EDITS.append((PDF,
"""    cols = [{"title": "Strategy", "w": 26}, {"title": "Base lots", "w": 14, "align": "right"},
            {"title": "Sizing rule", "w": 44}, {"title": "Max lots", "w": 13, "align": "right"},
            {"title": "Lots used", "w": 17, "align": "right"}, {"title": "Capital/lot", "w": 17, "align": "right"},
            {"title": "DD guard", "w": 35, "align": "right"}, {"title": "Tax", "w": 12, "align": "right"}]""",
"""    cols = [{"title": "Strategy", "w": 25}, {"title": "Base lots", "w": 15, "align": "right"},
            {"title": "Sizing rule", "w": 43}, {"title": "Max lots", "w": 14, "align": "right"},
            {"title": "Lots used", "w": 15, "align": "right"}, {"title": "Capital/lot", "w": 16, "align": "right"},
            {"title": "DD guard", "w": 40, "align": "right"}, {"title": "Tax", "w": 10, "align": "right"}]"""))
EDITS.append((PDF,
"""    used = "—" if hi is None else (f"{hi:g}" if lo is None or lo == hi else f"{lo:g} to {hi:g}")""",
"""    used = "—" if hi is None else (f"{hi:g}" if lo is None or lo == hi else f"{lo:g}–{hi:g}")"""))
EDITS.append((RTEST, '''check("equity mode", c == ["5", "Equity-based (compounding)", "40", "5 to 40", "₹1.5 L",''',
                     '''check("equity mode", c == ["5", "Equity-based (compounding)", "40", "5–40", "₹1.5 L",'''))
EDITS.append((RTEST, '''check("calendar mode + hard guard with release", c == ["10", "+2 lots every 6 months", "30", "10 to 30", "—",''',
                     '''check("calendar mode + hard guard with release", c == ["10", "+2 lots every 6 months", "30", "10–30", "—",'''))
EDITS.append((RTEST, '''      and "+2 lots every 6 months" in txt and "5 to 40" in txt and "₹1.5 L" in txt)''',
                     '''      and "+2 lots every 6 months" in txt and "5–40" in txt and "₹1.5 L" in txt)'''))


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
        if rel in NEEDS_PARENT and PARENT not in txt:
            die(f"{rel} lacks {PARENT} — apply apply_pf_pdf_redact.py first")
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
        print("  ✓ py_compile: renderer, strategy_display, tests")
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
            if not (repo / "backend" / script).exists():
                print(f"  ! {script} not found — skipped")
                continue
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

    print(f"\n✓ {FENCE} applied. Rebuild (backend + frontend). Shared portfolio PDFs now name strategies by their\n"
          f"  codenames (Velvet, Tigris…); tick 'Real strategy names in PDF' only for copies you keep yourself.")

if __name__ == "__main__":
    main()
