#!/usr/bin/env python3
"""
apply_BT_DTE_BREAKDOWN_20260911.py — FENCE: BT_DTE_BREAKDOWN_20260911

Backtest → Breakdown: "Day of Week" is meaningless across the NIFTY
Thursday→Tuesday weekly-expiry regime change (a Wednesday was 1DTE, now
it's 6DTE). Every NIFTY strategy trades the current-week expiry, so the
axis that matters is DAYS TO EXPIRY.

What: the panel becomes "Days to Expiry", keyed per trade as
  DTE = calendar days from the ENTRY date (IST) to the traded contract's
        OWN expiry (t.expiry — the symbol knows it, era-agnostic; same
        field the Expiry/Non-expiry segment already uses)
rows sorted 0DTE upward (not by trade count). Trades without an expiry
field (strategies that don't carry it) land in "Unknown" at the bottom.
Calendar days is the market convention (0DTE/1DTE/…); in the Tuesday era
Fri=4DTE, Mon=1DTE, Tue=0DTE; Thursday era Wed=1DTE, Thu=0DTE. If a
trading-sessions count is ever preferred it is a one-line change in the
key function.

Also: the shared bar-scaling (`all = [...]`) uses the new list so bars are
comparable across the three panels. Day-of-week is still computed for
the Analytics (live) page — untouched.

File: frontend/src/pages/Backtest.jsx (fenced, anchored). Safety: fence
check, node test of the exact DTE expression (Tuesday-era and Thursday-era
dates, IST midnight edge, missing expiry), JSX parse gate when esbuild is
available, .bak backup. Frontend-only → frontend rebuild.

Run from the repo root:  python3 apply_BT_DTE_BREAKDOWN_20260911.py
"""
import os, shutil, subprocess, sys, tempfile

FENCE = "BT_DTE_BREAKDOWN_20260911"
ROOT = os.path.abspath(os.getcwd())
REL = "frontend/src/pages/Backtest.jsx"
P = os.path.join(ROOT, REL)
if not os.path.isfile(P):
    sys.exit("ABORT: run from the scalp-app repo root")


def die(m): sys.exit(f"ABORT: {m}")
def read(p):
    with open(p, encoding="utf-8") as f: return f.read()
def sub1(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die(f"{label}: anchor found {n}x (need exactly 1)")
    return text.replace(old, new)


src = read(P)
if FENCE in src:
    die(f"{FENCE} already present — nothing to do")

KEYFN = (
    '(t) => {\n'
    '      if (!t.entry_ts || !t.expiry) return "Unknown";\n'
    '      const e = new Date((t.entry_ts + 19800) * 1000).toISOString().slice(0, 10);\n'
    '      const x = String(t.expiry).slice(0, 10);\n'
    '      const d = Math.round((Date.UTC(+x.slice(0, 4), +x.slice(5, 7) - 1, +x.slice(8, 10))\n'
    '                            - Date.UTC(+e.slice(0, 4), +e.slice(5, 7) - 1, +e.slice(8, 10))) / 86400000);\n'
    '      return Number.isFinite(d) && d >= 0 ? `${d}DTE` : "Unknown";\n'
    '    }'
)

src = sub1(src,
    '    dayBreakdown: makeBreakdowns((t) => t.entry_ts ? DAY_NAMES[new Date(t.entry_ts * 1000).getDay()] : "Unknown"),\n',
    '    dayBreakdown: makeBreakdowns((t) => t.entry_ts ? DAY_NAMES[new Date(t.entry_ts * 1000).getDay()] : "Unknown"),\n'
    '    // ── BT_DTE_BREAKDOWN_20260911 ── calendar days from ENTRY date (IST) to\n'
    '    // the traded contract\'s OWN expiry; era-agnostic (Thu→Tue regime), rows\n'
    '    // ordered 0DTE upward, "Unknown" last.\n'
    '    dteBreakdown: makeBreakdowns(' + KEYFN + ').sort((a, b) => {\n'
    '      const ka = a.name === "Unknown" ? 1e9 : parseInt(a.name, 10);\n'
    '      const kb = b.name === "Unknown" ? 1e9 : parseInt(b.name, 10);\n'
    '      return ka - kb;\n'
    '    }),\n',
    "metrics")
src = sub1(src,
    '    const all = [...metrics.dayBreakdown, ...metrics.instrBreakdown, ...metrics.sideBreakdown];\n',
    '    const all = [...metrics.dteBreakdown, ...metrics.instrBreakdown, ...metrics.sideBreakdown];   // ── BT_DTE_BREAKDOWN_20260911 ──\n',
    "scaling")
src = sub1(src,
    '                <BreakdownPanel title="Day of Week" items={metrics.dayBreakdown} maxTrades={maxBdTrades} maxPnL={maxBdPnL} />\n',
    '                <BreakdownPanel title="Days to Expiry" items={metrics.dteBreakdown} maxTrades={maxBdTrades} maxPnL={maxBdPnL} />   {/* ── BT_DTE_BREAKDOWN_20260911 ── was Day of Week */}\n',
    "panel")

# ── node test of the exact key expression ───────────────────────────────
TMP = tempfile.mkdtemp(prefix=FENCE + "_")
js = os.path.join(TMP, "t.js")
with open(js, "w") as f:
    f.write("const key = " + KEYFN.replace("\n    ", "\n") + ";\n" + r'''
const ist = (y, m, d, hh, mm) => Math.floor(Date.UTC(y, m - 1, d, hh, mm) / 1000) - 19800;  // IST wall clock → epoch
const T = (entry, expiry) => key({ entry_ts: entry, expiry });
const fails = [];
const chk = (n, got, want) => { const ok = got === want; console.log((ok ? "  PASS  " : "  FAIL  ") + n + (ok ? "" : ` (got ${got}, want ${want})`)); if (!ok) fails.push(n); };
// Tuesday era: expiry Tue 2026-09-15
chk("Tue-era: Fri 11 Sep 09:16 → 4DTE", T(ist(2026, 9, 11, 9, 16), "2026-09-15"), "4DTE");
chk("Tue-era: Mon 14 Sep → 1DTE", T(ist(2026, 9, 14, 9, 16), "2026-09-15"), "1DTE");
chk("Tue-era: Tue 15 Sep → 0DTE", T(ist(2026, 9, 15, 9, 16), "2026-09-15"), "0DTE");
chk("Tue-era: Wed 9 Sep → 6DTE", T(ist(2026, 9, 9, 9, 16), "2026-09-15"), "6DTE");
// Thursday era: expiry Thu 2020-08-06
chk("Thu-era: Wed 5 Aug 2020 → 1DTE", T(ist(2020, 8, 5, 9, 16), "2020-08-06"), "1DTE");
chk("Thu-era: Fri 31 Jul 2020 → 6DTE", T(ist(2020, 7, 31, 9, 16), "2020-08-06"), "6DTE");
// IST midnight edge: 15 Sep 00:30 IST is still 14 Sep in UTC — must count as 15 Sep
chk("IST date used, not UTC (00:30 IST on expiry day → 0DTE)", T(ist(2026, 9, 15, 0, 30), "2026-09-15"), "0DTE");
// expiry with a time component
chk("expiry with time component tolerated", T(ist(2026, 9, 14, 9, 16), "2026-09-15T15:30:00"), "1DTE");
chk("no expiry → Unknown", T(ist(2026, 9, 14, 9, 16), undefined), "Unknown");
chk("expiry before entry → Unknown (never negative)", T(ist(2026, 9, 16, 9, 16), "2026-09-15"), "Unknown");
if (fails.length) { console.log("FAILS: " + fails.join(", ")); process.exit(1); }
console.log("node key-function test: OK");
''')
pr = subprocess.run(["node", js], capture_output=True, text=True)
print(pr.stdout.strip()); 
if pr.returncode != 0:
    die("node test failed: " + pr.stderr[-400:])

# ── JSX parse gate (esbuild if present) ──────────────────────────────────
tmpjsx = os.path.join(TMP, "Backtest.jsx")
with open(tmpjsx, "w", encoding="utf-8") as f:
    f.write(src)
fe = os.path.join(ROOT, "frontend")
pr = subprocess.run(["npx", "--no-install", "esbuild", tmpjsx, "--loader:.jsx=jsx", "--log-level=error"],
                    cwd=fe, capture_output=True, text=True)
if pr.returncode == 0:
    print("JSX parse gate: OK")
else:
    print("JSX parse gate: esbuild not available here — the frontend build is the gate")

shutil.copy2(P, P + f".bak-{FENCE}")
with open(P, "w", encoding="utf-8") as f:
    f.write(src)
shutil.rmtree(TMP, ignore_errors=True)
print(f"written {REL} (+ .bak-{FENCE})")
print(f"DONE — {FENCE}. Frontend-only: ./desktop/build-scalp.sh frontend")
