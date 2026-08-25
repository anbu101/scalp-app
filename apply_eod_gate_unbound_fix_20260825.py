#!/usr/bin/env python3
# apply_eod_gate_unbound_fix_20260825.py
#
# ── EOD_GATE_UNBOUND_FIX_20260825 ── run from repo root:
#     python3 apply_eod_gate_unbound_fix_20260825.py
#
# ROOT CAUSE (confirmed by [APS][ERROR] tracebacks, 2026-08-25):
#   TRADING_DAY_GATE_20260816 added this inside each EOD job:
#
#       if not is_trading_day():
#           from app.event_bus.audit_logger import write_audit_log   # <bug>
#           write_audit_log("... non-trading day — no-op")
#           return
#       write_audit_log("... square-off triggered")   # UnboundLocalError!
#
#   A function-body `from X import name` is an ASSIGNMENT, so Python marks
#   `write_audit_log` as a LOCAL for the ENTIRE function. On a trading day
#   the gate branch is skipped, the local is never bound, and the first
#   log call raises UnboundLocalError — before anything reaches the audit
#   log (APScheduler swallowed it to stderr; the [APS] bridge surfaced it).
#   On holidays the jobs would have worked, which is exactly why the fleet
#   died silently starting the first trading day after 2026-08-16.
#
#   Every affected file already imports write_audit_log at MODULE level, so
#   the fix is to DELETE the twelve shadowing inner-import lines. pyflakes
#   has flagged this since day one ("imported but unused" + "redefinition")
#   — check_undefined_names.py must become a HARD build gate.
#
# Affected (12 lines / 11 files; tsg + gc are latent — their post-gate log
# calls only fire when there is something to close, i.e. TSG's LIVE EOD
# backstop would have crashed the first day it actually had work to do):

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JOBS = ROOT / "backend" / "app" / "jobs"

BAD_LINE = "        from app.event_bus.audit_logger import write_audit_log\n"

# file -> expected count of the shadowing line on unpatched main
EXPECTED = {
    "bb_live_eod.py":       1,
    "bb_live_eod_v2.py":    1,
    "gc_live_eod.py":       1,
    "ha_live_eod.py":       1,
    "ic_live_eod.py":       2,   # ic_live_eod_job + ic_morning_job
    "paper_trade_eod.py":   1,
    "scalp_v3_live_eod.py": 1,
    "scalpv5_live_eod.py":  1,
    "tma_live_eod.py":      1,
    "tma2_live_eod.py":     1,
    "tsg_live_eod.py":      1,
}

MODULE_IMPORT = "from app.event_bus.audit_logger import write_audit_log\n"


def main():
    # Pass 1 — verify every file before writing anything.
    plans = []
    for fname, want in EXPECTED.items():
        p = JOBS / fname
        if not p.exists():
            print(f"ABORT: {p} not found — run from repo root. NO FILES WRITTEN.")
            sys.exit(1)
        src = p.read_text(encoding="utf-8")
        got = src.count(BAD_LINE)
        if got == 0:
            print(f"  [SKIP] {fname}: already fixed (0 shadowing imports)")
            continue
        if got != want:
            print(f"ABORT: {fname} has {got} shadowing import(s), expected "
                  f"{want}. Inspect manually. NO FILES WRITTEN.")
            sys.exit(1)
        if MODULE_IMPORT not in src.split("def ", 1)[0]:
            print(f"ABORT: {fname} lacks the MODULE-LEVEL write_audit_log "
                  f"import above the first def — removing the inner import "
                  f"would break it. NO FILES WRITTEN.")
            sys.exit(1)
        plans.append((p, fname, src, got))

    # Pass 2 — apply.
    for p, fname, src, got in plans:
        p.write_text(src.replace(BAD_LINE, ""), encoding="utf-8")
        print(f"  [FIXED] {fname}: removed {got} shadowing import(s)")

    # Post-verification.
    ok = True
    for fname in EXPECTED:
        src = (JOBS / fname).read_text(encoding="utf-8")
        n = src.count(BAD_LINE)
        if n != 0:
            print(f"  [FAIL] {fname}: {n} shadowing import(s) remain")
            ok = False
    if not ok:
        print("POST-CHECK FAILED.")
        sys.exit(1)
    print("ALL SHADOWING IMPORTS REMOVED. Run py_compile + pyflakes, then "
          "rebuild. First green day = every EOD job shows [APS][OK].")


if __name__ == "__main__":
    main()
