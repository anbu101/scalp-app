#!/usr/bin/env python3
# apply_cbo_month_breaker_20260830.py
#
# ── CBO_MONTH_BREAKER_20260830 ── monthly circuit breaker (Anbu's profile
# objective: bound the worst month and the losing streak BY CONSTRUCTION).
#
#   monthly_loss_breaker  ₹, 0=off. When calendar-month P&L (realised
#                         month-to-date + open MTM when mtm_include_open)
#                         reaches -X: flatten immediately (MONTH_CAP) and
#                         take no new entries until the month changes.
#   monthly_profit_lock   ₹, 0=off. Mirror on the upside: lock a green
#                         month once it reaches +X and stand down.
#
# Why this shape: the profile scorecard's worst-month floor was measured to
# be daily_cap × red-days (~₹10k × 18-20 ≈ the observed −1.8..−2.0L). Entry
# filters barely move it because they barely change WHICH days go red. A
# monthly breaker attacks the metric directly: worst month ≈ −X + one
# flatten's slippage, and a max losing streak becomes bounded by months
# that stop early. It is powered by the same measured persistence that
# justified the daily cap.
#
# Attribution doctrine: MONTH_CAP exits carry their own counters and P&L
# share (month_cap_pnl_share_pct); halted-month entry blocks are counted as
# blocked_month_halt so the signals ledger still balances exactly; and
# months_loss_breaker_hit / months_profit_lock_hit count events per run.
#
#     python3 apply_cbo_month_breaker_20260830.py --check
#     python3 apply_cbo_month_breaker_20260830.py

from __future__ import annotations

import argparse
import py_compile
import sys
import tempfile
from pathlib import Path

FENCE = "CBO_MONTH_BREAKER_20260830"

RUNNERS = [Path("backend/app/backtest/cbo/backtest_cbo_runner.py"),
           Path("desktop/src-tauri/backend/app/backtest/cbo/backtest_cbo_runner.py")]

# ── A: config keys ───────────────────────────────────────────────────────
A_OLD = '''    "mtm_include_open": True,'''
A_NEW = '''    "mtm_include_open": True,
    # ── CBO_MONTH_BREAKER_20260830 ── calendar-month circuit breakers.
    "monthly_loss_breaker": 0.0,       # ₹, 0=off: stand down for the month
    "monthly_profit_lock": 0.0,        # ₹, 0=off: lock a green month'''

# ── B: coercion ──────────────────────────────────────────────────────────
B_OLD = '''              "sl_prem_value", "mtm_loss_cap", "mtm_profit_cap"):'''
B_NEW = '''              "sl_prem_value", "mtm_loss_cap", "mtm_profit_cap",
              "monthly_loss_breaker", "monthly_profit_lock"):   # CBO_MONTH_BREAKER_20260830'''

# ── C: diag keys ─────────────────────────────────────────────────────────
C_OLD = '''        "mtm_loss_cap_days": 0, "mtm_profit_cap_days": 0,'''
C_NEW = '''        "mtm_loss_cap_days": 0, "mtm_profit_cap_days": 0,
        # ── CBO_MONTH_BREAKER_20260830 ──
        "months_loss_breaker_hit": 0, "months_profit_lock_hit": 0,
        "month_cap_exits": 0, "month_cap_pnl_gross": 0.0,
        "blocked_month_halt": 0,'''

# ── D: month state before the day loop ───────────────────────────────────
D_OLD = '''    for i, day in enumerate(days):'''
D_NEW = '''    # ── CBO_MONTH_BREAKER_20260830 ── calendar-month accumulators. These
    # OUTLIVE the day loop on purpose: a halted month stays halted until the
    # month key changes, unlike the daily `halted` which resets every day.
    month_key = None
    month_realised = 0.0
    month_halted = False

    for i, day in enumerate(days):'''

# ── E: month rollover at day start ───────────────────────────────────────
E_OLD = '''        ds = _day_start_epoch(day)'''
E_NEW = '''        ds = _day_start_epoch(day)
        # ── CBO_MONTH_BREAKER_20260830 ── month rollover resets the
        # accumulator and re-arms the breaker.
        _mk = (day.year, day.month)
        if _mk != month_key:
            month_key = _mk
            month_realised = 0.0
            month_halted = False'''

# ── F: close_pos accumulates into the month ──────────────────────────────
F_OLD = '''            nonlocal pos, realised'''
F_NEW = '''            nonlocal pos, realised, month_realised   # CBO_MONTH_BREAKER_20260830'''

F2_OLD = '''            realised += net'''
F2_NEW = '''            realised += net
            month_realised += net   # CBO_MONTH_BREAKER_20260830'''

# ── G: MONTH_CAP in the attribution maps ─────────────────────────────────
G_OLD = '''                   "EOD": "eod_pnl_gross", "MTM_CAP": "mtm_cap_pnl_gross",
                   "AMBIGUOUS": "ambiguous_pnl_gross"}.get(reason)'''
G_NEW = '''                   "EOD": "eod_pnl_gross", "MTM_CAP": "mtm_cap_pnl_gross",
                   "MONTH_CAP": "month_cap_pnl_gross",   # CBO_MONTH_BREAKER_20260830
                   "AMBIGUOUS": "ambiguous_pnl_gross"}.get(reason)'''

G2_OLD = '''            diag[{"SL_SPOT": "sl_spot_exits", "SL_PREM": "sl_prem_exits",
                  "TP": "tp_exits", "EOD": "eod_exits",
                  "MTM_CAP": "mtm_cap_exits",
                  "AMBIGUOUS": "ambiguous_exits"}.get(reason, "eod_exits")] += 1'''
G2_NEW = '''            diag[{"SL_SPOT": "sl_spot_exits", "SL_PREM": "sl_prem_exits",
                  "TP": "tp_exits", "EOD": "eod_exits",
                  "MTM_CAP": "mtm_cap_exits",
                  "MONTH_CAP": "month_cap_exits",   # CBO_MONTH_BREAKER_20260830
                  "AMBIGUOUS": "ambiguous_exits"}.get(reason, "eod_exits")] += 1'''

# ── H: the breaker check, right after the daily caps ─────────────────────
H_OLD = '''                    if pos is not None:
                        ob = opt_bars(pos["symbol"]).get(bar.ts)
                        close_pos(bar.ts,
                                  float(ob.close) if ob else pos["last_mark"],
                                  "MTM_CAP")

            # ── 4. entries ──'''
H_NEW = '''                    if pos is not None:
                        ob = opt_bars(pos["symbol"]).get(bar.ts)
                        close_pos(bar.ts,
                                  float(ob.close) if ob else pos["last_mark"],
                                  "MTM_CAP")

            # ── 3b. CBO_MONTH_BREAKER_20260830 ── calendar-month breaker on
            # month-to-date realised + open MTM (same include_open rule as
            # the daily caps). A breach flattens NOW and stands the strategy
            # down until the month rolls — the worst month is bounded at
            # roughly −X plus one flatten's slippage, by construction.
            if not month_halted and (cfg["monthly_loss_breaker"] > 0
                                     or cfg["monthly_profit_lock"] > 0):
                _mlive = month_realised + (
                    mtm_of_open(pos, pos["last_mark"])
                    if (pos is not None and cfg["mtm_include_open"]) else 0.0)
                _mhl = (cfg["monthly_loss_breaker"] > 0
                        and _mlive <= -cfg["monthly_loss_breaker"])
                _mhp = (cfg["monthly_profit_lock"] > 0
                        and _mlive >= cfg["monthly_profit_lock"])
                if _mhl or _mhp:
                    month_halted = True
                    diag["months_loss_breaker_hit" if _mhl
                         else "months_profit_lock_hit"] += 1
                    if pos is not None:
                        ob = opt_bars(pos["symbol"]).get(bar.ts)
                        close_pos(bar.ts,
                                  float(ob.close) if ob else pos["last_mark"],
                                  "MONTH_CAP")

            # ── 4. entries ──'''

# ── I: month halt blocks entries (counted -> ledger balances) ────────────
I_OLD = '''            for s in sig_by_ts.get(bar.ts, []):
                if halted:'''
I_NEW = '''            for s in sig_by_ts.get(bar.ts, []):
                if month_halted:   # CBO_MONTH_BREAKER_20260830
                    diag["blocked_month_halt"] += 1
                    continue
                if halted:'''

# ── J: P&L share for the new reason ──────────────────────────────────────
J_OLD = '''        for k in ("ambiguous", "eod", "mtm_cap", "tp", "sl",'''
J_NEW = '''        for k in ("ambiguous", "eod", "mtm_cap", "month_cap", "tp", "sl",   # CBO_MONTH_BREAKER_20260830'''


class Abort(Exception):
    pass


def replace_once(text, old, new, what):
    n = text.count(old)
    if n != 1:
        raise Abort(f"{what}: anchor found {n}x, expected 1 — drifted; "
                    f"nothing written.")
    return text.replace(old, new, 1)


def stage(path, edits):
    if not path.exists():
        print(f"  SKIPPED (absent)        {path}")
        return None
    text = path.read_text()
    if FENCE in text:
        print(f"  already fenced — skipped   {path}")
        return None
    for old, new, what in edits:
        text = replace_once(text, old, new, f"{path}:{what}")
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(text)
        tmp = fh.name
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        raise Abort(f"{path}: staged compile failed — {e}")
    finally:
        Path(tmp).unlink(missing_ok=True)
    return (path, text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    edits = [(A_OLD, A_NEW, "A config"), (B_OLD, B_NEW, "B coercion"),
             (C_OLD, C_NEW, "C diag"), (D_OLD, D_NEW, "D month state"),
             (E_OLD, E_NEW, "E rollover"), (F_OLD, F_NEW, "F nonlocal"),
             (F2_OLD, F2_NEW, "F2 accumulate"), (G_OLD, G_NEW, "G pnl map"),
             (G2_OLD, G2_NEW, "G2 exits map"), (H_OLD, H_NEW, "H breaker"),
             (I_OLD, I_NEW, "I entry halt"), (J_OLD, J_NEW, "J shares")]
    staged = []
    try:
        for p in RUNNERS:
            staged.append(stage(p, edits))
    except Abort as e:
        print(f"\nABORTED: {e}\nNothing written (all-or-nothing staging).",
              file=sys.stderr)
        return 1
    for item in staged:
        if item is None:
            continue
        path, text = item
        if args.check:
            print(f"  would patch (clean)     {path}")
        else:
            path.write_text(text)
            print(f"  patched                 {path}")
    print(f"\n{FENCE} {'check complete' if args.check else 'applied'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
