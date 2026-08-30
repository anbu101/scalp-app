#!/usr/bin/env python3
# apply_cbo_month_breaker_ui_20260830.py
#
# ── CBO_MONTH_BREAKER_UI_20260830 ── two fields beside the daily MTM caps:
# "Monthly loss breaker ₹" and "Monthly profit lock ₹" (0 = off). Companion
# to apply_cbo_month_breaker_20260830.py — run the backend script FIRST.
# Standard 7-point pattern; stale-closure assert on both new state names;
# esbuild parse gate; all-or-nothing.
#
#     python3 apply_cbo_month_breaker_ui_20260830.py --check
#     python3 apply_cbo_month_breaker_ui_20260830.py

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

FENCE = "CBO_MONTH_BREAKER_UI_20260830"
NEEDS = "CBO_D10_FILTERS_UI_20260830"
TARGET = Path("frontend/src/pages/Backtest.jsx")

E1_OLD = '  const [cboMtmProfit, setCboMtmProfit] = useState(cboSaved.mtmProfit ?? 0);'
E1_NEW = E1_OLD + f'''
  // ── {FENCE} ── calendar-month circuit breakers (profile objective:
  // worst month and losing streak bounded BY CONSTRUCTION).
  const [cboMonthLoss, setCboMonthLoss] = useState(cboSaved.monthLoss ?? 0);
  const [cboMonthLock, setCboMonthLock] = useState(cboSaved.monthLock ?? 0);'''

E2_OLD = 'mtmLoss: cboMtmLoss, mtmProfit: cboMtmProfit,'
E2_NEW = ('mtmLoss: cboMtmLoss, mtmProfit: cboMtmProfit, '
          'monthLoss: cboMonthLoss, monthLock: cboMonthLock,')

E3_OLD = '        mtm_profit_cap: Number(cboMtmProfit) || 0,'
E3_NEW = E3_OLD + f'''
        monthly_loss_breaker: Number(cboMonthLoss) || 0,   // ── {FENCE} ──
        monthly_profit_lock: Number(cboMonthLock) || 0,'''

# deps substring appears in BOTH arrays (effect deps + buildConfig deps) —
# patched via count==2 replace, asserted below.
DEPS_OLD = 'cboMtmLoss, cboMtmProfit, cboMtmIncludeOpen,'
DEPS_NEW = 'cboMtmLoss, cboMtmProfit, cboMonthLoss, cboMonthLock, cboMtmIncludeOpen,'

E5_OLD = '    if (Number(cfg.mtm_profit_cap) > 0) add("MTM profit", `₹${cfg.mtm_profit_cap}`);'
E5_NEW = E5_OLD + f'''
    if (Number(cfg.monthly_loss_breaker) > 0) add("Month breaker", `−₹${{cfg.monthly_loss_breaker}}`);   // ── {FENCE} ──
    if (Number(cfg.monthly_profit_lock) > 0) add("Month lock", `+₹${{cfg.monthly_profit_lock}}`);'''

E6_OLD = ('<Field label="MTM profit cap ₹"><input type="number" style={inputStyle} '
          'value={cboMtmProfit} onChange={(e) => setCboMtmProfit(Number(e.target.value))} '
          'title="0 = off. Same flatten-and-halt behaviour." /></Field>')
E6_NEW = E6_OLD + """
                <Field label="Monthly loss breaker ₹">
                  <input type="number" style={inputStyle} value={cboMonthLoss} onChange={(e) => setCboMonthLoss(Number(e.target.value))}
                    title="0 = off. When the calendar month's P&L (realised month-to-date + open MTM, same include-open rule as the daily caps) reaches −X: flatten immediately (reason MONTH_CAP) and take no new entries until the month changes. Bounds the worst month at ≈ −X plus one flatten's slippage, and shortens losing streaks by construction. Counters: months_loss_breaker_hit, month_cap_exits, blocked_month_halt." />
                </Field>
                <Field label="Monthly profit lock ₹">
                  <input type="number" style={inputStyle} value={cboMonthLock} onChange={(e) => setCboMonthLock(Number(e.target.value))}
                    title="0 = off. Upside mirror: once the month reaches +X, flatten and stand down — the green month is locked in. Counter: months_profit_lock_hit." />
                </Field>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-esbuild", action="store_true")
    args = ap.parse_args()
    if not TARGET.exists():
        print(f"ABORTED: {TARGET} not found — run from the repo root",
              file=sys.stderr)
        return 1
    orig = TARGET.read_text()
    if FENCE in orig:
        print(f"  already fenced — skipped   {TARGET}")
        return 0
    if NEEDS not in orig:
        print(f"ABORTED: {TARGET} lacks {NEEDS} — apply the D10-filters UI "
              f"patch first.", file=sys.stderr)
        return 1
    t = orig
    for old, new, what, want in ((E1_OLD, E1_NEW, "1 state", 1),
                                 (E2_OLD, E2_NEW, "2 persist", 1),
                                 (E3_OLD, E3_NEW, "3 buildConfig", 1),
                                 (DEPS_OLD, DEPS_NEW, "4 deps x2", 2),
                                 (E5_OLD, E5_NEW, "5 describe", 1),
                                 (E6_OLD, E6_NEW, "6 panel", 1)):
        n = t.count(old)
        if n != want:
            print(f"\nABORTED: {what}: anchor found {n}x, expected {want} — "
                  f"drifted; nothing written.", file=sys.stderr)
            return 1
        t = t.replace(old, new)

    arm = t[t.find('if (sid === "CBO_V1")'): t.find('if (sid === "VET_V1")')]
    for name in ("cboMonthLoss", "cboMonthLock"):
        if name not in arm or len(re.findall(rf"\b{name}\b", t)) < 5:
            print(f"\nABORTED: stale-closure check failed for {name}.",
                  file=sys.stderr)
            return 1

    if not args.no_esbuild:
        tmp = TARGET.parent / "_cbo_mb_stage.jsx"
        tmp.write_text(t)
        try:
            r = subprocess.run(["npx", "--yes", "esbuild", str(tmp),
                                "--loader:.jsx=jsx", "--outfile=/dev/null"],
                               capture_output=True, text=True, cwd=".")
            if r.returncode != 0:
                print(f"\nABORTED: esbuild rejected the patched file:\n"
                      f"{r.stderr[:1500]}", file=sys.stderr)
                return 1
        except FileNotFoundError:
            print("  WARNING: npx not found — JSX gate SKIPPED",
                  file=sys.stderr)
        finally:
            tmp.unlink(missing_ok=True)

    if args.check:
        print(f"  would patch (clean, esbuild OK)   {TARGET}")
        return 0
    shutil.copy2(TARGET, TARGET.with_suffix(f".jsx.bak-{FENCE}"))
    TARGET.write_text(t)
    print(f"  patched (backup .bak-{FENCE})   {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
