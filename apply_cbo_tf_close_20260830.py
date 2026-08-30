#!/usr/bin/env python3
# apply_cbo_tf_close_20260830.py
#
# ── CBO_TF_CLOSE_20260830 ── third trigger mode: the COMPLETED tf candle
# must CLOSE through the previous tf candle's level (Anbu's expectation,
# 2026-08-30 — the classic close-confirmed breakout). The strictness ladder:
#
#   "high"      touch/cross intrabar (spec-literal; wick fires)     [D1]
#   "close"     the 1m SUB-BAR must close through the level
#   "tf_close"  the 5m (tf) candle itself must close through  [this patch]
#
# MECHANICS (no lookahead): with a 1m grid the tf bucket's last sub-bar is
# known by CLOCK (ts == bucket_start + tf - 1m), and that sub-bar's close IS
# the tf close. Detection happens at that sub-bar's close; fill is the next
# 1m open — the first minute of the next bucket. If the last minute of a
# bucket is missing from the corpus, that bucket simply cannot confirm
# (deterministic, counted by the ledger as fewer signals — never a guess).
#
# A pleasing structural property, asserted in the tests: tf_close CANNOT
# produce an ambiguous signal. One close is one price; it cannot be above
# the reference high AND below the reference low at once, so D8's forced-
# loss path is unreachable in this mode.
#
# Patches (dual-tree): cbo_v1_engine.py (mode + detection), runner
# _merge_cfg validation, and the UI dropdown's third option.
#
#     python3 apply_cbo_tf_close_20260830.py --check
#     python3 apply_cbo_tf_close_20260830.py

from __future__ import annotations

import argparse
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = "CBO_TF_CLOSE_20260830"

ENGINES = [Path("backend/app/backtest/cbo/cbo_v1_engine.py"),
           Path("desktop/src-tauri/backend/app/backtest/cbo/cbo_v1_engine.py")]
RUNNERS = [Path("backend/app/backtest/cbo/backtest_cbo_runner.py"),
           Path("desktop/src-tauri/backend/app/backtest/cbo/backtest_cbo_runner.py")]
UI = Path("frontend/src/pages/Backtest.jsx")

# ── engine edit 1: accept the mode ───────────────────────────────────────
E1_OLD = '''    if trigger_source not in ("high", "close"):
        raise ValueError(f"trigger_source must be high|close, got {trigger_source!r}")'''
E1_NEW = '''    if trigger_source not in ("high", "close", "tf_close"):
        raise ValueError(
            f"trigger_source must be high|close|tf_close, got {trigger_source!r}")'''

# ── engine edit 2: the detection branch ──────────────────────────────────
E2_OLD = '''        if ref is None:
            continue

        up_probe = run_hi if trigger_source == "high" else bar.close
        dn_probe = run_lo if trigger_source == "high" else bar.close'''
E2_NEW = '''        if ref is None:
            continue

        # ── CBO_TF_CLOSE_20260830 ── "tf_close": only the LAST sub-bar of
        # a bucket (known by clock) may fire, and only on ITS close — which
        # is the tf candle's close. Everything downstream (levels, stop,
        # fill_ts = next 1m open) is shared with the other modes.
        if trigger_source == "tf_close" and \\
                bar.ts != cur_bucket + tf_sec - 60:
            continue

        up_probe = run_hi if trigger_source == "high" else bar.close
        dn_probe = run_lo if trigger_source == "high" else bar.close'''

# ── engine edit 3: docstring ladder ──────────────────────────────────────
E3_OLD = '''    trigger_source        "high": the tf bar's running high/low breaches the
                          reference (spec-literal, intrabar).
                          "close": the sub-bar CLOSE must breach — strictly
                          fewer and later signals.'''
E3_NEW = '''    trigger_source        "high": the tf bar's running high/low breaches the
                          reference (spec-literal, intrabar).
                          "close": the sub-bar CLOSE must breach — strictly
                          fewer and later signals.
                          "tf_close": the COMPLETED tf candle must CLOSE
                          through the level; at most one signal per bucket
                          by construction, and never ambiguous (one close
                          is one price).   ── CBO_TF_CLOSE_20260830 ──'''

# ── runner edit: validation list ─────────────────────────────────────────
R1_OLD = '''    _tsrc = str(cfg["trigger_source"]).lower()
    cfg["trigger_source"] = _tsrc if _tsrc in ("high", "close") else "high"'''
R1_NEW = '''    _tsrc = str(cfg["trigger_source"]).lower()
    cfg["trigger_source"] = _tsrc if _tsrc in (
        "high", "close", "tf_close") else "high"   # ── CBO_TF_CLOSE_20260830 ──'''

# ── UI edit: third dropdown option ───────────────────────────────────────
U1_OLD = '''                    <option value="high">touch / cross (intrabar)</option>
                    <option value="close">sub-bar close through</option>'''
U1_NEW = '''                    <option value="high">touch / cross (intrabar)</option>
                    <option value="close">sub-bar close through</option>
                    <option value="tf_close">5m candle close through</option>'''

U2_OLD = '''    add("Trigger", cfg.trigger_source === "close" ? "sub-bar CLOSE through" : "touch/cross (intrabar)");'''
U2_NEW = '''    add("Trigger", cfg.trigger_source === "tf_close" ? "tf-candle CLOSE through" : cfg.trigger_source === "close" ? "sub-bar CLOSE through" : "touch/cross (intrabar)");   // ── CBO_TF_CLOSE_20260830 ──'''


class Abort(Exception):
    pass


def replace_once(text, old, new, what):
    n = text.count(old)
    if n != 1:
        raise Abort(f"{what}: anchor found {n}x, expected 1 — drifted; "
                    f"nothing written.")
    return text.replace(old, new, 1)


def stage(path, edits, gate=None):
    """Validate and return (path, new_text) WITHOUT writing. All-or-nothing:
    main() writes only after every file has staged clean — the first version
    of this script patched the engine, then aborted on the UI anchor while
    printing 'Nothing written', leaving the trees half-patched. An apply
    script that can stop mid-sequence must not touch disk mid-sequence."""
    if not path.exists():
        print(f"  SKIPPED (absent)        {path}")
        return None
    text = path.read_text()
    if FENCE in text:
        print(f"  already fenced — skipped   {path}")
        return None
    for old, new, what in edits:
        text = replace_once(text, old, new, f"{path}:{what}")
    if gate:
        gate(path, text)
    return (path, text)


def py_gate(path, text):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(text)
        tmp = fh.name
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        raise Abort(f"{path}: staged compile failed — {e}")
    finally:
        Path(tmp).unlink(missing_ok=True)


def jsx_gate(path, text):
    tmp = path.parent / "_cbo_tfc_stage.jsx"
    tmp.write_text(text)
    try:
        r = subprocess.run(["npx", "--yes", "esbuild", str(tmp),
                            "--loader:.jsx=jsx", "--outfile=/dev/null"],
                           capture_output=True, text=True, cwd=".")
        if r.returncode != 0:
            raise Abort(f"esbuild rejected the patched UI:\n{r.stderr[:1200]}")
    except FileNotFoundError:
        print("  WARNING: npx not found — JSX gate SKIPPED", file=sys.stderr)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    staged = []
    try:
        for p in ENGINES:
            staged.append(stage(p, [(E1_OLD, E1_NEW, "mode validation"),
                                    (E2_OLD, E2_NEW, "detection branch"),
                                    (E3_OLD, E3_NEW, "docstring")], py_gate))
        for p in RUNNERS:
            staged.append(stage(p, [(R1_OLD, R1_NEW, "cfg validation")],
                                py_gate))
        staged.append(stage(UI, [(U1_OLD, U1_NEW, "dropdown"),
                                 (U2_OLD, U2_NEW, "describeConfig")],
                            jsx_gate))
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
