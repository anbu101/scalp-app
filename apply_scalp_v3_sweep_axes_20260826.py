#!/usr/bin/env python3
# apply_scalp_v3_sweep_axes_20260826.py
#
# V3-D3c — make the new V3 knobs SWEEPABLE through SweepBuilder.
# PREREQ: TPMULT + EMA_GATE patches applied (the knobs must exist in
# buildConfig, since SweepBuilder's base for every combo IS buildConfig).
#
# AUDIT FINDING (why this patch exists): SweepBuilder is a launcher — every
# axis enqueues real runs — and its SWEEP_AXES table gates what can be swept
# per strategy. The good news from the audit: there is NO diverging config
# duplicate — every combo's baseline is buildConfig(strategyId), so
# parallel_workers / tp_multiplier / ema_gate from the rebuilt form already
# flow into sweep runs' base configs. The gap: the tp-multiplier and
# ema-gate-period AXES were declared strategies:[V1] only, so V3 could not
# vary them per-run.
#
# FIX (SweepBuilder.jsx, all copies): extend the two existing axes to
# [V1, V3] and drop the now-wrong "V1 " label prefixes. No new axes — the
# apply() functions are config-key-generic and, for the gate axis, merge
# over the base config so a sweep of PERIOD keeps the form's lookback /
# min-slope (set the form to On 89/30/1 and sweep period; or leave Off and
# the axis supplies enabled:true with 30/0 fallbacks). Keys ("v1_...") are
# internal identifiers and deliberately unchanged — renaming them would
# orphan any saved sweep drafts.
#
# NOT added, on purpose: a Workers axis (cannot change results — the same
# reason it is excluded from RunComparison PARAM_KEYS), and hedge_sl already
# has its [V3] axis.
#
# ACCEPTANCE: SweepBuilder with SCALP_V3 selected now lists "TP multiplier"
# and "EMA gate period (0=off)"; an OAT sweep over "1.5, 2, 2.5, 3, 3.5"
# enqueues five runs whose labels read SWEEP:<name> · tpX<v>, all carrying
# Workers=6 from the form's base config.

import glob
import os
import sys

REPO = os.getcwd()
FENCE = "SCALP_V3_SWEEP_AXES_20260826"


def fail(msg):
    print(f"\n[ABORT] {msg}\nNothing was written.")
    sys.exit(1)


JSX_EDITS = [
    (
        "  { key: \"v1_ema_gate_period\", label: \"V1 EMA gate period (0=off)\", strategies: [V1],\n",
        "  // \u2500\u2500 SCALP_V3_SWEEP_AXES_20260826 \u2500\u2500 axes extended to V3: apply() is\n"
        "  // config-key-generic and the V3 runner/engine now read these keys.\n"
        "  { key: \"v1_ema_gate_period\", label: \"EMA gate period (0=off)\", strategies: [V1, V3],\n",
        1,
    ),
    (
        "  { key: \"v1_tp_mult\", label: \"V1 TP multiplier\", strategies: [V1],\n",
        "  { key: \"v1_tp_mult\", label: \"TP multiplier\", strategies: [V1, V3],   // \u2500\u2500 SCALP_V3_SWEEP_AXES_20260826 \u2500\u2500\n",
        1,
    ),
]


def apply_edits(path):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if FENCE in text:
        print(f"[SKIP] fence already present: {os.path.relpath(path, REPO)}")
        return None
    for i, (old, new, want) in enumerate(JSX_EDITS, 1):
        n = text.count(old)
        if n != want:
            fail(f"anchor #{i} matched {n}x (need exactly {want}) in "
                 f"{os.path.relpath(path, REPO)}")
        text = text.replace(old, new)
    return text


def main():
    paths = [os.path.join(REPO, "frontend", "src", "pages", "backtest", "SweepBuilder.jsx")]
    paths += sorted(set(
        glob.glob(os.path.join(REPO, "desktop", "**", "SweepBuilder.jsx"),
                  recursive=True)) - set(paths))
    found = [p for p in paths if os.path.isfile(p)]
    if not found:
        fail("SweepBuilder.jsx not found — run from the scalp-app repo root")
    if len(found) == 1:
        print("[WARN] only ONE SweepBuilder.jsx found — if the desktop tree "
              "keeps a frontend mirror, rsync/diff it before building.")

    staged = []
    for path in found:
        text = apply_edits(path)
        if text is not None:
            staged.append((path, text))
            print(f"[OK] staged {os.path.relpath(path, REPO)}")

    if not staged:
        print("\n[DONE] nothing to do — fence already present.")
        return

    for path, text in staged:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[WROTE] {os.path.relpath(path, REPO)}")

    for path, _ in staged:
        with open(path, "r", encoding="utf-8") as f:
            t = f.read()
        assert "label: \"EMA gate period (0=off)\", strategies: [V1, V3]" in t
        assert "label: \"TP multiplier\", strategies: [V1, V3]" in t
        # note: other axes (premium band, RR) legitimately carry [V1, V3]
        # already — assert the TWO SPECIFIC axis lines, not a global count.
        # keys unchanged (saved sweep drafts must not orphan)
        assert "key: \"v1_ema_gate_period\"" in t and "key: \"v1_tp_mult\"" in t
    print("\n[PASS] all structural asserts hold.")
    print("Syntax check + rebuild:")
    print("  npx --no-install esbuild frontend/src/pages/backtest/SweepBuilder.jsx --loader:.jsx=jsx --outfile=/dev/null")
    print("Then: SweepBuilder \u2192 SCALP_V3 \u2192 OAT axis 'TP multiplier' \u2192")
    print("values 1.5, 2, 2.5, 3, 3.5 \u2192 stage. Labels: SWEEP:<name> \u00b7 tpX<v>.")


if __name__ == "__main__":
    main()
