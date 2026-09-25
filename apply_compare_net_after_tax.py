#!/usr/bin/env python3
# apply_compare_net_after_tax.py — fence COMPARE_NET_AFTER_TAX_20260924
# (requires LOT_COMP_TAX_20260924 in RunComparison.jsx)
#
# Compare-runs TABLE: a sortable, filterable "Net after tax" column right of
# Net = net_pnl − tax withdrawn − tax due, from the persist_run stamps
# (lot_comp_tax_paid / _accrued). Runs without tax show "—" and sort last.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_compare_net_after_tax.py [--allow-dirty]
from __future__ import annotations
import os, shutil, subprocess, sys

FENCE = "COMPARE_NET_AFTER_TAX_20260924"
ROOT = os.path.dirname(os.path.abspath(__file__))
REL = "frontend/src/pages/backtest/RunComparison.jsx"
MIRROR = "desktop/src-tauri/frontend/src/pages/backtest/RunComparison.jsx"

EDITS = [
    # sort
    ('        case "net":         return r.summary?.net_pnl;\n',
     '        case "net":         return r.summary?.net_pnl;\n'
     '        case "netAfterTax": return netAfterTaxOf(r.summary);   // ── COMPARE_NET_AFTER_TAX_20260924 ──\n'),
    # filter
    ("      net: (r) => r.summary?.net_pnl,\n",
     "      net: (r) => r.summary?.net_pnl,\n"
     "      netAfterTax: (r) => netAfterTaxOf(r.summary),   // ── COMPARE_NET_AFTER_TAX_20260924 ──\n"),
    # header
    ('            {th("net", "Net", "right")}\n            {th("winRate", "Win%", "right")}\n',
     '            {th("net", "Net", "right")}\n'
     '            {th("netAfterTax", "Net after tax", "right")}   {/* ── COMPARE_NET_AFTER_TAX_20260924 ── */}\n'
     '            {th("winRate", "Win%", "right")}\n'),
    # filter row
    ('            {filterCell("net", "e.g. >30L")}\n            {filterCell("winRate", "e.g. >30")}\n',
     '            {filterCell("net", "e.g. >30L")}\n'
     '            {filterCell("netAfterTax", "e.g. >25L")}\n'
     '            {filterCell("winRate", "e.g. >30")}\n'),
    # cell
    ('                <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, fontWeight: 700, ...pnlStyle(s.net_pnl) }}>{money(s.net_pnl)}</td>\n',
     '                <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, fontWeight: 700, ...pnlStyle(s.net_pnl) }}>{money(s.net_pnl)}</td>\n'
     "                {/* ── COMPARE_NET_AFTER_TAX_20260924 ── net − FY tax withdrawn − tax due; — when the run had no tax */}\n"
     "                {(() => {\n"
     "                  const v = netAfterTaxOf(s);\n"
     '                  if (v == null) return <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, color: c.text.muted }}>—</td>;\n'
     '                  return (<td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, fontWeight: 600, ...pnlStyle(v) }}\n'
     '                    title={`tax withdrawn ${fmtInr(Number(s.lot_comp_tax_paid) || 0)}${Number(s.lot_comp_tax_accrued) > 0 ? ` · due ${fmtInr(Number(s.lot_comp_tax_accrued))}` : ""}`}>{money(v)}</td>);\n'
     "                })()}\n"),
    # helper (module scope, next to paramSummary)
    ("// ── LOT_COMP_20260924 ── compounding tag leads the summary for every strategy\nfunction paramSummary(run) {\n",
     "// ── COMPARE_NET_AFTER_TAX_20260924 ── null when the run carried no tax stamp\n"
     "function netAfterTaxOf(s) {\n"
     "  if (!s || s.lot_comp_tax_paid == null || s.net_pnl == null) return null;\n"
     "  return Number(s.net_pnl) - (Number(s.lot_comp_tax_paid) || 0) - (Number(s.lot_comp_tax_accrued) || 0);\n"
     "}\n\n"
     "// ── LOT_COMP_20260924 ── compounding tag leads the summary for every strategy\nfunction paramSummary(run) {\n"),
]


def die(m):
    print("\nABORT:", m); sys.exit(1)


p = os.path.join(ROOT, REL)
if not os.path.exists(p):
    die(f"{REL} not found — run from the repo root")
t = open(p, encoding="utf-8").read()
if FENCE in t:
    die(f"{FENCE} already applied")
if "LOT_COMP_TAX_20260924" not in t:
    die("prerequisite LOT_COMP_TAX_20260924 missing — apply apply_lot_comp_tax.py first")
st = subprocess.run(["git", "status", "--porcelain", "--", REL], cwd=ROOT, capture_output=True, text=True).stdout.strip()
if st and not st.startswith("??") and "--allow-dirty" not in sys.argv:
    die(f"{REL} has uncommitted changes ({st}); commit or re-run with --allow-dirty")
for old, new in EDITS:
    n = t.count(old)
    if n != 1:
        die(f"anchor found {n}× (need 1): {old.splitlines()[0][:80]!r}")
    t = t.replace(old, new)
shutil.copy2(p, p + f".bak-{FENCE}")
open(p, "w", encoding="utf-8").write(t)
m = os.path.join(ROOT, MIRROR)
if os.path.isdir(os.path.dirname(m)):
    open(m, "w", encoding="utf-8").write(t)
open(os.path.join(ROOT, f".{FENCE}.done"), "w").write("applied\n")
esb = os.path.join(ROOT, "frontend", "node_modules", ".bin", "esbuild")
esb = os.environ.get("ESBUILD") or (esb if os.path.exists(esb) else shutil.which("esbuild"))
if esb:
    r = subprocess.run([esb, REL, "--loader:.jsx=jsx", "--log-level=error", "--outfile=/dev/null"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        shutil.move(p + f".bak-{FENCE}", p); os.remove(os.path.join(ROOT, f".{FENCE}.done"))
        die("esbuild parse failed; restored:\n" + r.stderr[-1500:])
    print("   esbuild ok")
print(f"── {FENCE} applied (6 edits). Next: ./desktop/build-scalp.sh both ──")
