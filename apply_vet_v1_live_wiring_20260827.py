#!/usr/bin/env python3
# apply_vet_v1_live_wiring_20260827.py
#
# ── VET_V1 LIVE WIRING, PART 1 of N ──────────────────────────────────────
# The three checklist items that, if missed, make a strategy INVISIBLE with
# zero errors anywhere (Part 7 scar tissue, TSG 2026-08-02):
#
#   2.1  strategy_registry.STRATEGIES["VET_V1"]   — gates the runtime launch
#   2.2  strategy_loader default config           — miss it, mode reads OFF
#   2.10 paper_trade_squareoff OVERNIGHT_EXEMPT   — miss it, the generic
#        15:25 sweep force-closes rows and corrupts paper-vs-backtest parity
#
# This script does NOT launch anything: there is no runtime, manager, route
# or panel yet, so applying it is inert beyond making the config readable.
# It is separated deliberately so the registry/loader/squareoff triple can be
# reviewed on its own — they are the items with silent failure modes.
#
# ── ONE STRATEGY, FOUR MODES (2026-08-27 decision) ───────────────────────
# VET_V1 ships every sealed configuration as SETTINGS, not as separate
# strategies: leg_action BUY|SELL, eod_square ON|OFF (intraday|positional),
# and the hedge wing budget. Defaults are the SAFEST sealed config — NIFTY
# Buy B, intraday, unhedged (trend 36, ATM-1, EOD 15:15) — because a default
# is what runs if nobody touches anything.
#
# ── WHY VET_V1 IS OVERNIGHT-EXEMPT IN *BOTH* MODES ───────────────────────
# eod_square is user-switchable at any time, so the strategy owns its exit
# lifecycle either way:
#   * eod_square ON  → own EOD at 15:15, BEFORE the generic 15:25 sweep.
#     The sweep would only ever double-close an already-closed row.
#   * eod_square OFF → carries overnight BY DESIGN (the sell-positional
#     config holds 30% of positions overnight, median ~3h, longest ~7
#     sessions). The sweep would destroy exactly the behaviour being tested.
# Exempting unconditionally is correct for both and cannot go stale if the
# user flips the mode mid-week. Residual accepted, identical to IC_V2: a
# paper row orphaned by a crash before carry-commit stays OPEN until closed
# by hand — cosmetic, paper-only, and far preferable to force-closing
# legitimate carries.
#
# ── LIVE WINGS ARE NEVER SYNTHETIC (carried from TMA_V2 wing_mode) ───────
# The backtest may PRICE a wing it could not buy (ic_synth_wing). Live
# cannot. wing_mode "real_fallback" means: real contract at or under the cap
# or NO TRADE. This is a known, deliberate live/backtest divergence and it
# is material for the sell-positional config — see the divergence ledger in
# the config comment.
#
# Idempotent, assert-anchored, dual-tree.
#
# USAGE
#   cd <repo root>
#   python3 apply_vet_v1_live_wiring_20260827.py --dry-run
#   python3 apply_vet_v1_live_wiring_20260827.py

import argparse
import os
import py_compile
import shutil
import sys
import tempfile

REPO = os.getcwd()
TREES = [(os.path.join(REPO, "backend"), "backend"),
         (os.path.join(REPO, "desktop", "src-tauri", "backend"), "desktop-be")]

REG = os.path.join("app", "strategy", "strategy_registry.py")
LOADER = os.path.join("app", "config", "strategy_loader.py")
SQOFF = os.path.join("app", "db", "paper_trade_squareoff.py")


def die(m):
    print(f"\nABORT: {m}\nNothing was written.")
    sys.exit(1)


def one(t, needle, lbl, want=1):
    n = t.count(needle)
    if n != want:
        die(f"anchor count {n}, expected {want} [{lbl}]: {needle.strip()[:90]}")


# ── 2.1 registry ────────────────────────────────────────────────────────
REG_ANCHOR = "    # ── TSG_V1 BEGIN ──"
REG_BLOCK = '''    # ── VET_V1 BEGIN ──
    # ==================================================
    # VET_V1 — Vivek Equity Tool: dual-EMA(10/20) + SMA(40)±ATR×0.618
    # regime channel on 5m NIFTY SPOT. Transition-only signals; a FLAT
    # (in-channel) bar CARRIES the condition and therefore HOLDS an open
    # position (RANGE-HOLD) rather than closing it. One position at a time.
    #
    # FOUR SEALED CONFIGS, ONE RUNTIME. leg_action (BUY|SELL), eod_square
    # (intraday|positional) and the hedge wing are SETTINGS, not separate
    # strategies. Defaults = NIFTY Buy B intraday unhedged (the safest).
    #
    # NO SL/TP AND NO GTT LAYER. All four sealed configs run sl_pct=0 /
    # tp_pct=0 — exits are FLIP, SIGNAL_EXIT, EXPIRY_EXIT and (intraday)
    # EOD, all decided by the engine at 5m closes. Adding a live stop would
    # be a parity break, so there is deliberately no GTT machinery here;
    # the kill path is correspondingly simple (flatten both legs).
    #
    # Signals are parity-by-construction: the live engine re-runs the
    # BACKTEST's own resample_spot + vet_states over the growing day prefix
    # with a 10-session warmup, guarded for prefix stability (freezes and
    # emits nothing on any drift). Manages ALL state itself in vet_trades
    # (slots=[]). Launched as a STANDALONE async loop in api_server, like
    # TMA_V2/PST, with its own KiteTicker.
    #
    # Defaults ship trade_execution_mode=PAPER. To REMOVE: delete this
    # entry, app/engine/vet/, app/jobs/vet_live_eod.py,
    # app/api/vet_state_routes.py, the VET_V1 default in strategy_loader,
    # the OVERNIGHT_EXEMPT entry, and DROP the vet_trades table.
    # ==================================================
    "VET_V1": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "1m",          # ticks fold to 1m; decisions at 5m
        "timeframe_sec": 60,
        "slots": [],
    },
    # ── VET_V1 END ──
'''

# ── 2.2 loader defaults ─────────────────────────────────────────────────
LOADER_ANCHOR = '    "TMA_V2": {'
LOADER_BLOCK = '''    # ── VET_V1 BEGIN ──
    # Defaults are the SEALED "NIFTY Buy B" configuration, intraday and
    # unhedged — the lowest-drawdown of the four studied configs (net
    # ₹39.0L, MaxDD ₹12.6L, net/DD 3.09, 7/7 years positive over
    # 2020-01-01..2026-08-18 at 10 lots). Every other sealed config is
    # reachable from Settings without a code change:
    #   Buy A            : trend_len 40, atm_offset -2
    #   Sell EOD         : leg_action SELL, hedge_max_premium 3
    #   Sell Positional  : leg_action SELL, hedge_max_premium 3,
    #                      eod_square False   ← carries overnight
    #
    # ── LIVE / BACKTEST DIVERGENCE LEDGER (read before trusting live) ──
    # 1. WINGS ARE REAL-ONLY LIVE. wing_mode="real_fallback": if no listed
    #    contract sits at or under hedge_max_premium, the entry is SKIPPED.
    #    The backtest may synthesise one. In the sell-positional study 57%
    #    of wings (1,713 of 2,981) were synthetic, so live WILL take
    #    materially fewer sell trades than that backtest unless the cap is
    #    raised. This is the largest known divergence in the strategy.
    # 2. FILLS. Backtest fills at the 1m option close; live crosses a
    #    spread and slips.
    # 3. NO STOP. sl_pct/tp_pct are 0 by design (parity). A SELL position
    #    carrying overnight has NO stop — the wing is the only backstop and
    #    the SL-under-SELL sweep has NOT been run.
    "VET_V1": {
        "trade_execution_mode": "PAPER",
        "underlying": "NIFTY",

        # ── signal (frozen study params; changing these leaves the study) ──
        "signal_tf": 5,
        "trend_len": 36,
        "range_len": 0.618,
        "ema_fast1": 10,
        "ema_fast2": 20,
        "warmup_sessions": 10,      # MUST equal the backtest's or signals
                                    # diverge silently at each day's start

        # ── expression: the four modes live here ──
        "leg_action": "BUY",        # BUY | SELL
        "strike_selection": "atm",
        "atm_offset": -1,           # negative = in-the-money
        "hedge_enabled": False,     # SELL only; wing budget below
        "hedge_max_premium": 3.0,
        "wing_mode": "real_fallback",   # real_fallback | skip — NEVER synthetic live

        # ── session / lifecycle ──
        "eod_square": True,         # True = intraday, False = carry overnight
        "session_start": "09:15",
        "entry_cutoff": "15:00",
        "exit_time": "15:15",
        "rollover_enabled": False,

        # ── risk: OFF by design, see divergence ledger note 3 ──
        "sl_pct": 0,
        "tp_pct": 0,
        "daily_mtm_cap": 0,
        "max_trades_per_day": 0,

        # ── selection filters (0 = off) ──
        "premium_min": 0,
        "premium_max": 0,
        "min_entry_dte": 0,
        "max_entry_dte": 0,
        "min_entry_volume": 0,

        "quantity": {"lot_size": 65, "lots": 10},
    },
    # ── VET_V1 END ──
'''

SQ_OLD = 'OVERNIGHT_EXEMPT_STRATEGIES = ("IC_V2", "TSG_V1", "TMA_V2")'
SQ_NEW = ('# ── VET_V1 (2026-08-27): exempt in BOTH modes. eod_square is a user\n'
          '# setting: ON → VET owns its 15:15 EOD, which lands BEFORE this 15:25\n'
          '# sweep (the sweep could only double-close). OFF → VET carries\n'
          '# overnight by design and the sweep would destroy the carry. Neither\n'
          '# mode wants this sweep, and exempting unconditionally cannot go stale\n'
          '# when the mode is flipped mid-week.\n'
          'OVERNIGHT_EXEMPT_STRATEGIES = ("IC_V2", "TSG_V1", "TMA_V2", "VET_V1")')


def edit_reg(t):
    if '"VET_V1"' in t:
        return t, 0
    one(t, REG_ANCHOR, "registry TSG anchor")
    return t.replace(REG_ANCHOR, REG_BLOCK + REG_ANCHOR, 1), 1


def edit_loader(t):
    if '"VET_V1"' in t:
        return t, 0
    one(t, LOADER_ANCHOR, "loader TMA_V2 anchor")
    return t.replace(LOADER_ANCHOR, LOADER_BLOCK + LOADER_ANCHOR, 1), 1


def edit_sqoff(t):
    if "VET_V1" in t:
        return t, 0
    one(t, SQ_OLD, "squareoff exempt tuple")
    return t.replace(SQ_OLD, SQ_NEW, 1), 1


EDITORS = [(REG, edit_reg), (LOADER, edit_loader), (SQOFF, edit_sqoff)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    writes, notes = {}, []
    for root, label in TREES:
        if not os.path.isdir(root):
            notes.append(f"[{label}] NOT PRESENT — skipped (rsync target)")
            continue
        for rel, fn in EDITORS:
            path = os.path.join(root, rel)
            if not os.path.isfile(path):
                die(f"[{label}] missing {path}")
            out, n = fn(open(path).read())
            if n == 0:
                notes.append(f"[{label}] SKIP (already wired): {rel}")
            else:
                writes[path] = out
                notes.append(f"[{label}] EDIT: {rel}")
    print("── PLAN ─────────────────────────────────────────────────────")
    for x in notes:
        print("  " + x)
    if not writes:
        print("\nNothing to do.")
        return
    print("\n── STAGED COMPILE ───────────────────────────────────────────")
    tmp = tempfile.mkdtemp(prefix="vet_wire_")
    try:
        for i, (dest, body) in enumerate(writes.items()):
            stage = os.path.join(tmp, f"s{i}.py")
            open(stage, "w").write(body)
            try:
                py_compile.compile(stage, doraise=True)
            except py_compile.PyCompileError as e:
                die(f"compile FAILED for {dest}:\n{e}")
        print(f"  {len(writes)} file(s) compile clean")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if a.dry_run:
        print("\n--dry-run: no files written.")
        return
    print("\n── WRITE ────────────────────────────────────────────────────")
    for dest, body in writes.items():
        open(dest, "w").write(body)
        print("  wrote " + os.path.relpath(dest, REPO))
    print("\nDONE. Inert until the runtime lands — no launch path exists yet.")


if __name__ == "__main__":
    main()
