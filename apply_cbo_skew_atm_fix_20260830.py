#!/usr/bin/env python3
# apply_cbo_skew_atm_fix_20260830.py
#
# ── CBO_SKEW_ATM_FIX_20260830 ── three defects found by the 2023-05-10 /
# 2026-04-16 trace (trace_cbo_day_20260830.py):
#
# BUG 1 (the day-killer): the ATM skew gate compared CE vs PE at the PICKED
#   contract's strike and looked both legs up in the BAND-FILTERED snapshot.
#   When ATM premium sits below the band min, the band's CE strikes are ITM
#   (below spot) and its PE strikes are ITM the other way (above spot) — the
#   two sides' strike sets are DISJOINT, the opposite leg's lookup returns
#   None, fail-closed fires, and every signal all day dies as blocked_skew.
#   That single mechanism produced the vol-regime clustering (dead exactly
#   when ATM premium < band min), the Fri 76% -> Thu 9% weekday decay (ATM
#   premium decays through the week and the strike sets separate mid-week),
#   zero expiry-day trades ever, and 2023 dead except the Adani week.
#
# FIX 1: measure the rule AS WRITTEN — ATM CE vs ATM PE:
#   * strike = nearest grid strike to SPOT (true ATM), not the pick's strike
#   * symbols come from the FULL expected-expiry universe for the day
#     (contracts_active_on_day scoped to the expiry), NOT the band snapshot —
#     the ATM strike's contracts need not be in the band at all
#   * still fail-closed, but data-blocks are counted SEPARATELY
#     (blocked_skew_unmeasurable) from verdict-blocks (blocked_skew), so a
#     silent day-killer of this shape can never hide again
#
# BUG 2 (uncounted path): signals triggering at/after the EOD square-off
#   minute hit `continue` before any counter — the trace's ledger showed
#   UNACCOUNTED 3 on both days. Benign (they could never trade) but silent.
# FIX 2: count them as blocked_after_eod.
#
# BUG 3 (same class): a signal whose fill minute has no SPOT bar is never
#   iterated at all (sig_by_ts is keyed by fill_ts and the loop walks spot
#   bars). Dense corpora make it rare; rare is not never.
# FIX 3: after the day loop, sweep unvisited signals into blocked_no_spot_bar.
#
# Fleet standard: assert-anchored replace-once, dated fence, staged
# py_compile, dual-tree, idempotent.
#     python3 apply_cbo_skew_atm_fix_20260830.py --check
#     python3 apply_cbo_skew_atm_fix_20260830.py

from __future__ import annotations

import argparse
import py_compile
import sys
import tempfile
from pathlib import Path

FENCE = "CBO_SKEW_ATM_FIX_20260830"

TARGETS = [
    Path("backend/app/backtest/cbo/backtest_cbo_runner.py"),
    Path("desktop/src-tauri/backend/app/backtest/cbo/backtest_cbo_runner.py"),
]

# ── edit A: strike-step constants next to INDEX_LOTS ─────────────────────
A_OLD = 'INDEX_LOTS = {"NIFTY": 65, "BANKNIFTY": 35}'
A_NEW = '''INDEX_LOTS = {"NIFTY": 65, "BANKNIFTY": 35}

# ── CBO_SKEW_ATM_FIX_20260830 ── strike grid per index, for locating the
# TRUE ATM strike (nearest to spot). The skew rule is measured there.
STRIKE_STEPS = {"NIFTY": 50, "BANKNIFTY": 100}'''

# ── edit B: per-day ATM symbol map, built from the FULL expiry universe ──
B_OLD = '''        opt_cache: Dict[str, Dict[int, object]] = {}'''
B_NEW = '''        # ── CBO_SKEW_ATM_FIX_20260830 ── (strike, side) -> tradingsymbol
        # for the WHOLE expected-expiry chain, band-independent. The skew
        # gate measures at the true ATM strike, whose contracts may be
        # nowhere near the premium band — so the band snapshot is the wrong
        # universe for this lookup, and using it was the bug: below-band ATM
        # premium made the band's CE and PE strike sets disjoint, the
        # opposite-leg lookup returned None, and fail-closed killed every
        # signal of every such day as blocked_skew.
        atm_sym: Dict[tuple, str] = {}
        if cfg["atm_skew_filter"].get("enabled"):
            _exp = timeline.get("expected_expiry")
            for _c in src.contracts_active_on_day(underlying, ds, expiry=_exp):
                atm_sym[(float(_c["strike"]), _c["instrument_type"])] = \\
                    _c["tradingsymbol"]
        _step = STRIKE_STEPS.get(underlying.upper(), 50)

        opt_cache: Dict[str, Dict[int, object]] = {}'''

# ── edit C: the skew gate itself ─────────────────────────────────────────
C_OLD = '''                sk = cfg["atm_skew_filter"]
                if sk.get("enabled"):
                    strike = float(pick["strike"])
                    ce = next((src.option_premium_at(o["tradingsymbol"],
                                                     s.trigger_ts)
                               for o in snap if o["type"] == "CE"
                               and float(o["strike"]) == strike), None)
                    pe = next((src.option_premium_at(o["tradingsymbol"],
                                                     s.trigger_ts)
                               for o in snap if o["type"] == "PE"
                               and float(o["strike"]) == strike), None)
                    ok, _ = skew_ok(ce=ce, pe=pe,
                                    spot=src.spot_at(underlying,
                                                     s.trigger_ts + 60),
                                    strike=strike, direction=s.direction,
                                    cfg_skew=sk)
                    if not ok:
                        diag["blocked_skew"] += 1
                        continue'''
C_NEW = '''                sk = cfg["atm_skew_filter"]
                if sk.get("enabled"):
                    # ── CBO_SKEW_ATM_FIX_20260830 ── the rule as written:
                    # ATM CE vs ATM PE, both at the strike nearest SPOT,
                    # symbols from the full expiry chain. Verdict blocks and
                    # data blocks are counted separately so an unmeasurable
                    # gate is visible in the diag, never a silent day-kill.
                    _spot = src.spot_at(underlying, s.trigger_ts + 60)
                    if _spot is None:
                        diag["blocked_skew_unmeasurable"] += 1
                        continue
                    _k = round(_spot / _step) * _step
                    ce = pe = None
                    _cs = atm_sym.get((float(_k), "CE"))
                    _ps = atm_sym.get((float(_k), "PE"))
                    if _cs:
                        ce = src.option_premium_at(_cs, s.trigger_ts)
                    if _ps:
                        pe = src.option_premium_at(_ps, s.trigger_ts)
                    if ce is None or pe is None:
                        diag["blocked_skew_unmeasurable"] += 1
                        continue
                    ok, _ = skew_ok(ce=ce, pe=pe, spot=_spot, strike=_k,
                                    direction=s.direction, cfg_skew=sk)
                    if not ok:
                        diag["blocked_skew"] += 1
                        continue'''

# ── edit D: diag keys ────────────────────────────────────────────────────
D_OLD = '''        "blocked_no_selection": 0, "blocked_no_fill": 0, "blocked_skew": 0,'''
D_NEW = '''        "blocked_no_selection": 0, "blocked_no_fill": 0, "blocked_skew": 0,
        # ── CBO_SKEW_ATM_FIX_20260830 ── every kill path gets a counter:
        # signals_raw must equal entries + Σ blocked_* on every run.
        "blocked_skew_unmeasurable": 0, "blocked_after_eod": 0,
        "blocked_no_spot_bar": 0,'''

# ── edit E: count post-EOD signals (BUG 2) ───────────────────────────────
E_OLD = '''            if minute >= eod_min:
                continue'''
E_NEW = '''            if minute >= eod_min:
                # ── CBO_SKEW_ATM_FIX_20260830 ── these signals can never
                # trade, but silence is not an option: the trace ledger
                # (signals_raw vs entries+blocked) must balance.
                diag["blocked_after_eod"] += len(sig_by_ts.get(bar.ts, []))
                continue'''

# ── edit F: sweep signals whose fill minute had no spot bar (BUG 3) ──────
F_OLD = '''        if pos is not None:
            # Ran out of prints before the EOD minute (a truncated session).'''
F_NEW = '''        # ── CBO_SKEW_ATM_FIX_20260830 ── a signal whose fill_ts minute
        # has no SPOT bar is never reached by the bar loop above (sig_by_ts
        # is keyed by fill_ts). Dense corpora make this rare; count it so it
        # can never be silent.
        _seen = {b.ts for b in spot}
        for _fts, _ss in sig_by_ts.items():
            if _fts not in _seen:
                diag["blocked_no_spot_bar"] += len(_ss)

        if pos is not None:
            # Ran out of prints before the EOD minute (a truncated session).'''


class Abort(Exception):
    pass


def replace_once(text, old, new, what):
    n = text.count(old)
    if n != 1:
        raise Abort(f"{what}: anchor found {n}x, expected 1 — file drifted; "
                    f"nothing written.")
    return text.replace(old, new, 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    present = [t for t in TARGETS if t.exists()]
    if not present:
        print("ABORTED: runner not found — run from the repo root.",
              file=sys.stderr)
        return 1

    for t in TARGETS:
        if not t.exists():
            print(f"  SKIPPED (tree absent)   {t}")
            continue
        text = t.read_text()
        if FENCE in text:
            print(f"  already fenced — skipped   {t}")
            continue
        try:
            for old, new, what in ((A_OLD, A_NEW, "A strike steps"),
                                   (B_OLD, B_NEW, "B atm symbol map"),
                                   (C_OLD, C_NEW, "C skew gate"),
                                   (D_OLD, D_NEW, "D diag keys"),
                                   (E_OLD, E_NEW, "E post-eod counter"),
                                   (F_OLD, F_NEW, "F no-spot-bar sweep")):
                text = replace_once(text, old, new, f"{t}:{what}")
        except Abort as e:
            print(f"\nABORTED: {e}", file=sys.stderr)
            return 1
        with tempfile.NamedTemporaryFile("w", suffix=".py",
                                         delete=False) as fh:
            fh.write(text)
            tmp = fh.name
        try:
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as e:
            print(f"ABORTED: staged compile failed for {t}: {e}",
                  file=sys.stderr)
            return 1
        finally:
            Path(tmp).unlink(missing_ok=True)
        if args.check:
            print(f"  would patch (clean)     {t}")
        else:
            t.write_text(text)
            print(f"  patched                 {t}")

    print(f"\n{FENCE} {'check complete' if args.check else 'applied'}.")
    if not args.check:
        print("\nVerify:")
        print("  1. python3 backend/app/backtest/cbo/test_cbo_runner_sim.py .")
        print("  2. python3 trace_cbo_day_20260830.py --day 2023-05-10 "
              "--min 150 --max 200   # ledger must now say BALANCED")
        print("  3. re-run one sweep cell WITH skew enabled — 2023 must "
              "now trade; read blocked_skew vs blocked_skew_unmeasurable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
