#!/usr/bin/env python3
# apply_scalp_v1_determinism_20260823.py
#
# D7 — SCALP_V1 backtest DETERMINISM fix — fence: SCALP_V1_DETERMINISM_20260823
#
# ROOT CAUSE (found by trade-matching two "identical" baselines that differed
# by Rs 21.4L gross): build_selection_timeline returns all_symbols as a Python
# SET; the runner iterates it directly to build per-symbol contexts, and that
# insertion order drives the per-candle processing order. Python randomizes
# string hashing per process (PYTHONHASHSEED), so symbol order differs EVERY
# RUN. On any candle where an exit and a fresh signal coincide — several
# thousand per full run — whether the candidate symbol is evaluated before or
# after the exiting one decides `no_open_trade`, and the single-slot design
# cascades that one coin-flip through the whole rest of the day. Result:
# 1,407 of 1,466 trading days diverged between two runs of the same code,
# same data, same config.
#
# FIX (two parts, both in backtest_runner.py; selector untouched):
#  1. watched = sorted(timeline["all_symbols"]) — deterministic context order.
#  2. TWO-PASS per candle ts: PASS 1 processes ALL exits, PASS 2 evaluates
#     indicators/signals. This is not just determinism — it is more faithful
#     to live: an SL/TP touch happens on a TICK DURING the minute, so by the
#     time the candle CLOSES and signals evaluate, live's slot is already
#     free. Same-candle exit->re-entry is therefore deterministically ALLOWED
#     (matching live), instead of depending on hash order.
#
# CONSEQUENCE: results change ONE more time (like D4) — but after this, a
# re-run of the same config is byte-identical, which the sweep / walk-forward
# program requires to mean anything at all.
#
# ACCEPTANCE TEST (run after applying): execute the SAME full-range baseline
# TWICE; the two exports must be byte-identical in every trade row.
#
# NOTE: other runners (HA family, hedge) may share the set-iteration pattern —
# out of scope here (SCALP_V1 only), flagged for a later sweep.
#
# PREREQ: SCALP_V1_BT_FILTERS_20260823 and SCALP_V1_DIAG_20260823 applied
# (anchors below match that file state). Idempotent. Run from repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_DETERMINISM_20260823"
PREREQS = ["SCALP_V1_BT_FILTERS_20260823", "SCALP_V1_DIAG_20260823"]
ROOT = Path(__file__).resolve().parent
RN_REL = "app/backtest/runner/backtest_runner.py"

TREES = [ROOT / "backend"]
_desktop = ROOT / "desktop" / "src-tauri" / "backend"
if (_desktop / RN_REL).exists():
    TREES.append(_desktop)


def _die(msg):
    print(f"ABORT: {msg}")
    sys.exit(1)


def _replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        _die(f"anchor '{label}' matched {n} times (want 1) — NOTHING written")
    return text.replace(old, new, 1)


# ── A1: deterministic watch order ──────────────────────────────────────────
A1_OLD = '        watched = timeline["all_symbols"]'
A1_NEW = '''        # ── SCALP_V1_DETERMINISM_20260823 ── all_symbols is a SET; raw
        # iteration order is hash-randomized per process and used to drive
        # per-candle processing order. Sort it: identical run -> identical
        # context order -> identical results.
        watched = sorted(timeline["all_symbols"])'''

# ── A2: two-pass per-candle loop (exits first, then signals) ───────────────
A2_OLD = '''            for sym, c in by_ts[ts]:
                ctx = ctxs[sym]
                ctx.clock.advance_to(ts)
                md = _bt_to_md_candle(c)

                # ── EXIT (only the contract holding the slot) ──
                open_pos = book.get_open_for_symbol(sym)
                if open_pos is not None:'''
A2_NEW = '''            # ── SCALP_V1_DETERMINISM_20260823 BEGIN: PASS 1 — EXITS ──
            # All exits for this candle resolve BEFORE any signal evaluates.
            # Live-faithful: an SL/TP touch is a tick DURING the minute, so at
            # candle close (when signals fire) the slot is already free. This
            # also removes the same-candle exit/entry race that made results
            # depend on hash-randomized symbol order.
            for sym, c in by_ts[ts]:
                ctx = ctxs[sym]
                ctx.clock.advance_to(ts)

                # ── EXIT (only the contract holding the slot) ──
                open_pos = book.get_open_for_symbol(sym)
                if open_pos is not None:'''

# ── A3: seam between exit body and signal body -> close pass 1, open pass 2 ─
A3_OLD = '''                    elif ts + 60 >= eod_close_ts:
                        book.close_position(sym, exit_ts=ts + 60,
                                            exit_price=c.close,
                                            exit_reason="EOD",
                                            ambiguous_fill=False)

                # feed indicator (live-mode → tracks _last_red_low)
                ind_vals = ctx.indicator.update(md)'''
A3_NEW = '''                    elif ts + 60 >= eod_close_ts:
                        book.close_position(sym, exit_ts=ts + 60,
                                            exit_price=c.close,
                                            exit_reason="EOD",
                                            ambiguous_fill=False)

            # ── SCALP_V1_DETERMINISM_20260823: PASS 2 — INDICATORS + SIGNALS ──
            # Slot state is now post-exit for every symbol uniformly. NOTE:
            # candles at/after the 15:15 EOD close now also feed the indicator
            # (pass 1's `continue` no longer skips it) — harmless and uniform:
            # the session gate blocks any entry there, and per-day contexts
            # are rebuilt from DB warmup, so no state crosses days.
            for sym, c in by_ts[ts]:
                ctx = ctxs[sym]
                md = _bt_to_md_candle(c)

                # feed indicator (live-mode → tracks _last_red_low)
                ind_vals = ctx.indicator.update(md)'''


def main():
    if not (ROOT / "backend" / RN_REL).exists():
        _die("run from the scalp-app repo root")

    staged = []
    for tree in TREES:
        rn_p = tree / RN_REL
        rn = rn_p.read_text()
        if FENCE in rn:
            _die(f"fence {FENCE} already present under {tree} — already applied")
        for pf in PREREQS:
            if pf not in rn:
                _die(f"prerequisite fence {pf} MISSING in {rn_p} — apply earlier "
                     f"scripts first")
        rn = _replace_once(rn, A1_OLD, A1_NEW, f"{tree.name}:A1")
        rn = _replace_once(rn, A2_OLD, A2_NEW, f"{tree.name}:A2")
        rn = _replace_once(rn, A3_OLD, A3_NEW, f"{tree.name}:A3")
        staged.append((rn_p, rn))

    # anchors verified AND staged content compiled BEFORE any write
    for path, text in staged:
        try:
            compile(text, str(path), "exec")
        except SyntaxError as e:
            _die(f"staged content for {path} does not compile: {e}")
    for path, text in staged:
        path.write_text(text)
        print(f"PATCHED: {path}")
        print(f"py_compile OK: {path}")

    print()
    print(f"DONE — fence {FENCE} applied.")
    print()
    print("ACCEPTANCE TEST (required before trusting any further numbers):")
    print("  1. Run the full-range baseline (filters OFF) TWICE.")
    print("  2. Export both; the trade rows must be byte-identical.")
    print("  3. That run becomes the canonical baseline for every comparison.")
    print()
    print("Then re-run the blackout/cap sweep against the NEW baseline — the")
    print("earlier filter verdict carried seed noise and must be re-measured.")


if __name__ == "__main__":
    main()
