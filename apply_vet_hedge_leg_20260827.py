#!/usr/bin/env python3
# apply_vet_hedge_leg_20260827.py
#
# ── VET_V1 SELL-MODE HEDGE LEG ── frontend wiring for two new config keys.
# Backend counterpart ships in apply_vet_v1_backtest_20260826.py — RUN THAT
# FIRST, and apply_vet_leg_action_and_ui_trim_20260827.py before this one
# (the hedge fields sit next to the Leg action selector).
#
# WHAT IT IS
#   hedge_enabled      buy a protective wing alongside every short leg
#   hedge_max_premium  the wing costs at most this (default Rs 5)
#
#   The wing is a LONG option of the SAME type and expiry as the short —
#   short PE is hedged with a cheaper PE, short CE with a cheaper CE — and
#   the DEAREST contract at or under the cap is chosen, i.e. the closest
#   strike that still fits the budget, which is what maximises the SPAN
#   benefit per rupee. Ignored entirely in BUY mode.
#
#   FAIL-CLOSED: if no wing exists under the cap at that minute the ENTRY IS
#   SKIPPED. Selling bare because the protective leg was unavailable would
#   change the risk profile without changing the config, so the runner
#   refuses rather than silently going naked (diag: no_hedge_entries).
#
#   ONE ROW PER POSITION. The trade row still describes the SHORT leg, while
#   pnl / charges / net_pnl carry the PAIR. Two rows would halve the apparent
#   win rate and double the trade count while describing one position. Wing
#   economics are aggregated in diag_vet.hedge_cost_total.
#
# SURFACES: Backtest.jsx (state, persist, 2 dep arrays, buildConfig,
# describeConfig, 2 fields), SweepBuilder.jsx (vet_hedge axis),
# BacktestQueue.jsx (param line), RunComparison.jsx (row).
#
# DOCTRINE: assert-anchored, idempotent, staged esbuild check, dual-tree.
#
# USAGE
#   cd <repo root>
#   python3 apply_vet_hedge_leg_20260827.py --dry-run
#   python3 apply_vet_hedge_leg_20260827.py

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.getcwd()
TREES = [(os.path.join(REPO, "frontend", "src"), "frontend"),
         (os.path.join(REPO, "desktop", "src-tauri", "frontend", "src"), "desktop-fe")]
BT = os.path.join("pages", "Backtest.jsx")
SW = os.path.join("pages", "backtest", "SweepBuilder.jsx")
QU = os.path.join("pages", "backtest", "BacktestQueue.jsx")
RC = os.path.join("pages", "backtest", "RunComparison.jsx")


def die(m):
    print(f"\nABORT: {m}\nNothing was written.")
    sys.exit(1)


def one(t, needle, lbl, want=1):
    n = t.count(needle)
    if n != want:
        die(f"anchor count {n}, expected {want} [{lbl}]: {needle.strip()[:110]}")


ST_OLD = ('  const [vetLegAction, setVetLegAction] = useState('
          'vetSaved.legAction ?? "BUY");\n')
ST_NEW = ST_OLD + (
    '  // ── HEDGE_LEG ── SELL-mode protective wing; ignored when buying.\n'
    '  const [vetHedgeMax, setVetHedgeMax] = useState(vetSaved.hedgeMax ?? 5);\n')

PS_OLD = 'legAction: vetLegAction,'
PS_NEW = 'legAction: vetLegAction, hedgeMax: vetHedgeMax,'

DEP_OLD = 'vetLots, vetLegAction,'
DEP_NEW = 'vetLots, vetLegAction, vetHedgeMax,'

CFG_OLD = '        leg_action: vetLegAction,\n'
CFG_NEW = (CFG_OLD +
           '        // ── the number IS the switch: 0 = naked, > 0 = hedged.\n'
           '        hedge_enabled: Math.abs(Number(vetHedgeMax) || 0) > 0,\n'
           '        hedge_max_premium: Math.abs(Number(vetHedgeMax) || 0),\n')

DESC_OLD = ('    add("Leg", cfg.leg_action === "SELL" ? "option SELLING" '
            ': "option buying");\n')
DESC_NEW = (DESC_OLD +
            '    if (cfg.leg_action === "SELL") add("Hedge", cfg.hedge_enabled '
            '? `wing ≤₹${cfg.hedge_max_premium}` : "NAKED");\n')

FLD_ANCHOR = '                <Field label="Strike selection">'
OLD_HIDDEN_BLOCK = (
    '                {vetLegAction === "SELL" && (\n'
    '                  <Field label="Hedge wing max ₹ (0 = naked)"><input type="number" step="0.5" '
    'style={inputStyle} value={vetHedge ? vetHedgeMax : 0} '
    'onChange={(e) => { const v = Math.abs(Number(e.target.value) || 0); '
    'setVetHedgeMax(v); setVetHedge(v > 0); }} '
    'title="Buy a protective long option of the SAME type and expiry as the short leg, costing at '
    'most this. The DEAREST wing under the cap is taken — the closest strike that still fits the '
    'budget, which is what earns the most SPAN margin benefit per rupee. FAIL-CLOSED: if no wing is '
    'available under the cap the entry is SKIPPED, never taken bare. Set 0 to sell naked — which on '
    'a short option means an unbounded loss tail and full margin. Ignored in BUY mode." /></Field>\n'
    '                )}\n')

# ── ALWAYS VISIBLE ── an earlier revision hid this behind
# {vetLegAction === "SELL" && ...}, which made the knob undiscoverable until
# you had already switched to SELL. It now renders unconditionally and is
# DISABLED on BUY, the same pattern the EOD square time field uses.
VISIBLE_FIELD = (
    '                <Field label="Hedge wing max ₹ (0 = naked)"><input type="number" step="0.5" '
    'style={inputStyle} value={vetHedgeMax} '
    'disabled={vetLegAction !== "SELL"} '
    'onChange={(e) => setVetHedgeMax(Math.abs(Number(e.target.value) || 0))} '
    + 'title="Buy a protective long option of the SAME type and expiry as the short leg, costing at most this. The DEAREST wing under the cap is taken — the closest strike that still fits the budget, which is what earns the most SPAN margin benefit per rupee. FAIL-CLOSED: if no wing is available under the cap the entry is SKIPPED, never taken bare. Set 0 to sell naked — which on a short option means an unbounded loss tail and full margin. APPLIES ONLY WHEN LEG ACTION IS SELL, and is disabled otherwise." /></Field>\n')

FLD_NEW = VISIBLE_FIELD + FLD_ANCHOR

def edit_bt(t):
    if "vetHedgeMax" in t:
        # ── REPAIR PATH ── already applied. If it landed as the hidden
        # conditional form, swap it for the always-visible one; otherwise
        # there is nothing to do.
        if OLD_HIDDEN_BLOCK in t:
            t = t.replace(OLD_HIDDEN_BLOCK, VISIBLE_FIELD, 1)
            return t, 1
        return t, 0
    if "vetLegAction" not in t:
        die("Leg action not found — apply "
            "apply_vet_leg_action_and_ui_trim_20260827.py first")
    for n, l in ((ST_OLD, "state"), (PS_OLD, "persist"), (CFG_OLD, "buildConfig"),
                 (DESC_OLD, "describeConfig"), (FLD_ANCHOR, "field anchor")):
        one(t, n, "Backtest:" + l)
    one(t, DEP_OLD, "Backtest:dep arrays", want=2)
    t = t.replace(ST_OLD, ST_NEW, 1).replace(PS_OLD, PS_NEW, 1)
    t = t.replace(DEP_OLD, DEP_NEW)
    t = t.replace(CFG_OLD, CFG_NEW, 1).replace(DESC_OLD, DESC_NEW, 1)
    t = t.replace(FLD_ANCHOR, FLD_NEW, 1)
    return t, 6


SW_OLD = '  { key: "vet_leg", label: "VET leg (0=buy / 1=sell)", strategies: [VET],\n'
SW_NEW = ('''  // ── HEDGE_LEG ── wing budget in rupees; 0 sells naked. Only meaningful
  // alongside vet_leg=1. Cheaper wings sit further out: less protection and
  // less margin benefit, but a smaller drag on the credit.
  { key: "vet_hedge", label: "VET hedge wing max (₹, 0=naked)", strategies: [VET],
    hint: "0, 3, 5, 10, 20", parse: _num,
    apply: (c, v) => { c.hedge_max_premium = Math.abs(v); c.hedge_enabled = Math.abs(v) > 0; },
    fmt: (v) => (v > 0 ? `wing≤₹${Math.abs(v)}` : "naked") },
''' + SW_OLD)


def edit_sw(t):
    if "vet_hedge" in t:
        return t, 0
    one(t, SW_OLD, "Sweep:vet_leg axis")
    return t.replace(SW_OLD, SW_NEW, 1), 1


QU_OLD = '    if (cfg.leg_action === "SELL") p.push("SELL");\n'
QU_NEW = ('    if (cfg.leg_action === "SELL") p.push(cfg.hedge_enabled '
          '? `SELL+w≤${cfg.hedge_max_premium}` : "SELL naked");\n')


def edit_qu(t):
    if "hedge_enabled" in t:
        return t, 0
    one(t, QU_OLD, "Queue:paramLine SELL")
    return t.replace(QU_OLD, QU_NEW, 1), 1


RC_OLD = ('  { key: "vet_leg",      label: "VET leg",        get: (r) => '
          '(r.config?.trend_len != null && r.config?.range_len != null) ? '
          '(r.config.leg_action === "SELL" ? "SELL" : "BUY") : null },\n')
RC_NEW = (RC_OLD +
          '  { key: "vet_hedge",    label: "VET hedge wing", get: (r) => '
          '(r.config?.trend_len != null && r.config?.range_len != null && '
          'r.config?.leg_action === "SELL") ? (r.config.hedge_enabled ? '
          '`≤₹${r.config.hedge_max_premium}` : "naked") : null },\n')


def edit_rc(t):
    if '"vet_hedge"' in t:
        return t, 0
    one(t, RC_OLD, "Compare:vet_leg row")
    return t.replace(RC_OLD, RC_NEW, 1), 1


EDITORS = [(BT, edit_bt), (SW, edit_sw), (QU, edit_qu), (RC, edit_rc)]


def find_esbuild(canary):
    cands = []
    loc = os.path.join(REPO, "frontend", "node_modules", ".bin", "esbuild")
    if os.path.isfile(loc) and os.access(loc, os.X_OK):
        cands.append(([loc], "node_modules"))
    p = shutil.which("esbuild")
    if p:
        cands.append(([p], "PATH"))
    npx = shutil.which("npx")
    if npx:
        cands += [([npx, "--no", "esbuild"], "npx"),
                  ([npx, "--no-install", "esbuild"], "npx7")]
    for c, w in cands:
        try:
            if subprocess.run(c + ["--log-level=silent", canary], capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=90).returncode == 0:
                return c, w
        except Exception:
            pass
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-jsx-check", action="store_true")
    a = ap.parse_args()
    writes, notes = {}, []
    for root, label in TREES:
        if not os.path.isdir(root):
            notes.append(f"[{label}] NOT PRESENT — skipped")
            continue
        for rel, fn in EDITORS:
            path = os.path.join(root, rel)
            if not os.path.isfile(path):
                die(f"[{label}] missing {path}")
            src = open(path).read()
            out, n = fn(src)
            if n == 0:
                notes.append(f"[{label}] SKIP (already wired): {rel}")
            else:
                writes[path] = out
                notes.append(f"[{label}] EDIT ({n}): {rel}")
    print("── PLAN ─────────────────────────────────────────────────────")
    for n in notes:
        print("  " + n)
    if not writes:
        print("\nNothing to do.")
        return
    print("\n── JSX SYNTAX CHECK ─────────────────────────────────────────")
    if a.skip_jsx_check:
        print("  skipped by request")
    else:
        tmp = tempfile.mkdtemp(prefix="vet_hedge_")
        try:
            can = os.path.join(tmp, "c.jsx")
            open(can, "w").write("const A = () => <div>{1}</div>;\n")
            cmd, where = find_esbuild(can)
            if cmd is None:
                print("  !! no working esbuild — check SKIPPED (not an error)")
            else:
                print(f"  esbuild via {where}")
                for i, (dest, body) in enumerate(writes.items()):
                    st = os.path.join(tmp, f"s{i}.jsx")
                    open(st, "w").write(body)
                    r = subprocess.run(cmd + ["--log-level=warning", st],
                                       capture_output=True, text=True,
                                       stdin=subprocess.DEVNULL, timeout=120)
                    if r.returncode != 0:
                        die(f"esbuild FAILED for {dest}:\n{r.stderr[:1500]}")
                print(f"  {len(writes)} file(s) parse clean")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if a.dry_run:
        print("\n--dry-run: no files written.")
        return
    print("\n── WRITE ────────────────────────────────────────────────────")
    for dest, body in writes.items():
        open(dest, "w").write(body)
        print("  wrote " + os.path.relpath(dest, REPO))
    print("\nDONE. Rebuild the frontend.")
    print("  new field (always visible; disabled unless Leg action = SELL):")
    print("    'Hedge wing max ₹ (0 = naked)'  — default 5")
    print("  new sweep axis: 'VET hedge wing max (₹, 0=naked)'")


if __name__ == "__main__":
    main()
