#!/usr/bin/env python3
# apply_scalp_v3_tpmult_20260826.py
#
# V3-D3a — expose TP MULTIPLIER for SCALP_V3 backtests.
#
# Evidence (canonical baseline 95e70e7e): the loss is front-loaded — trades
# dead within 0-2 minutes carry -126.6L of the -173L; the surviving (3m+)
# population is TP-skewed (13,431 SIG_TP vs 10,175 SIG_SL) and gross-flat.
# Widening the signal TP lets that healthy half win bigger per trade, and
# (single-slot) longer holds mechanically suppress churn + charges. V1's
# decisive knob, pointed at V3's healthy half. Hypothesis, not answer —
# the sweep decides.
#
# WHY THIS IS ONE FILE: the shared StrategyEngine already computes
#   tp_price = entry_price - (risk_distance * tp_mult)
# from cfg.get("tp_multiplier") of WHATEVER strategy_id is running, and every
# display surface is key-generic on the config:
#   Backtest.jsx run-detail chip  ("TP mult", line ~533)      — generic
#   BacktestQueue.jsx job label   (`tpX{n}`, line ~241)       — generic
#   RunComparison.jsx PARAM_DEFS  ("TP multiplier", ~107)     — generic
# paramFormat.js (x4) is the SETTINGS formatter — untouched (backtest-only key).
# NOTE (engine read, sighted): require_fresh_entry is hardcoded ON fleet-wide
# since SCALP_V1_LIVE_SETTINGS_20260825 — V3's baseline already ran with it;
# no fresh-entry wiring exists in this patch because there is nothing to wire.
#
# Edits (Backtest.jsx, fence SCALP_V3_TPMULT_20260826):
#   1. v3TpMult state (default 1 = neutral, persisted)
#   2. buildConfig hedge branch: OMIT-WHEN-1 — a TP Mult of 1/blank emits NO
#      key, so the canonical baseline config stays byte-identical and
#      RunComparison diffs stay clean (SCALP_V1_ENTRY_SIZING discipline)
#   3. TP Mult field in the V3 row
#   4. v3TpMult in all THREE mirrored lists (saveParams object, saveParams
#      deps, buildConfig deps) in the SAME commit — stale-closure rule
#
# ACCEPTANCE:
#   • TP Mult = 1 (or blank), baseline config → CSV diff vs 95e70e7e must be
#     byte-identical (omit-when-1 makes the config literally the same).
#   • Sweep: 1.5 / 2.0 / 2.5 / 3.0 / 3.5, enqueued from the REBUILT UI with
#     Workers=6 in the form (queue snapshots config at enqueue).
#
# Frontend-only — safe to apply today; needs esbuild check + rebuild.

import glob
import os
import sys

REPO = os.getcwd()
FENCE = "SCALP_V3_TPMULT_20260826"


def fail(msg):
    print(f"\n[ABORT] {msg}\nNothing was written.")
    sys.exit(1)


JSX_EDITS = [
    (
        "  const [v3Workers, setV3Workers] = useState(saved.v3Workers ?? 4);   // \u2500\u2500 SCALP_V3_PARALLEL_20260826 \u2500\u2500\n",
        "  const [v3Workers, setV3Workers] = useState(saved.v3Workers ?? 4);   // \u2500\u2500 SCALP_V3_PARALLEL_20260826 \u2500\u2500\n"
        "  const [v3TpMult, setV3TpMult] = useState(saved.v3TpMult ?? 1);   // \u2500\u2500 SCALP_V3_TPMULT_20260826 \u2500\u2500\n",
        1,
    ),
    (
        "      cfg.parallel_workers = Number(v3Workers) || 1;\n",
        "      cfg.parallel_workers = Number(v3Workers) || 1;\n"
        "      // \u2500\u2500 SCALP_V3_TPMULT_20260826 \u2500\u2500 omit-when-1: baseline configs stay\n"
        "      // byte-identical to 95e70e7e and RunComparison diffs stay clean. The\n"
        "      // shared engine reads this key generically (tp = entry - risk\u00d7mult).\n"
        "      if (Number(v3TpMult) > 0 && Number(v3TpMult) !== 1) cfg.tp_multiplier = Number(v3TpMult);\n",
        1,
    ),
    (
        "              <Field label=\"Workers\"><input type=\"number\" min=\"1\" max=\"16\" style={inputStyle} value={v3Workers} onChange={(e) => setV3Workers(e.target.value)} /></Field>\n",
        "              <Field label=\"Workers\"><input type=\"number\" min=\"1\" max=\"16\" style={inputStyle} value={v3Workers} onChange={(e) => setV3Workers(e.target.value)} /></Field>\n"
        "              {/* \u2500\u2500 SCALP_V3_TPMULT_20260826 \u2500\u2500 signal TP = entry \u2212 risk\u00d7mult\n"
        "                  (shared-engine math). 1 = neutral (key omitted). */}\n"
        "              <Field label=\"TP Mult\"><input type=\"number\" min=\"0\" step=\"0.5\" style={inputStyle} value={v3TpMult} onChange={(e) => setV3TpMult(e.target.value)} /></Field>\n",
        1,
    ),
    (
        "      v3Workers,   // \u2500\u2500 SCALP_V3_PARALLEL_20260826 \u2500\u2500\n",
        "      v3Workers,   // \u2500\u2500 SCALP_V3_PARALLEL_20260826 \u2500\u2500\n"
        "      v3TpMult,   // \u2500\u2500 SCALP_V3_TPMULT_20260826 \u2500\u2500\n",
        3,
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
                 f"{os.path.relpath(path, REPO)} — is the "
                 f"SCALP_V3_PARALLEL_20260826 patch applied first?")
        text = text.replace(old, new)
    return text


def main():
    jsx_paths = [os.path.join(REPO, "frontend", "src", "pages", "Backtest.jsx")]
    jsx_paths += sorted(set(
        glob.glob(os.path.join(REPO, "desktop", "**", "Backtest.jsx"),
                  recursive=True)) - set(jsx_paths))
    found = [p for p in jsx_paths if os.path.isfile(p)]
    if not found:
        fail("Backtest.jsx not found — run from the scalp-app repo root")
    if len(found) == 1:
        print("[WARN] only ONE Backtest.jsx found — if the desktop tree keeps "
              "a frontend mirror, rsync/diff it before building.")

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
        assert t.count("v3TpMult,   // \u2500\u2500 SCALP_V3_TPMULT_20260826 \u2500\u2500") == 3
        assert "if (Number(v3TpMult) > 0 && Number(v3TpMult) !== 1) cfg.tp_multiplier = Number(v3TpMult);" in t
        assert t.count("value={v3TpMult}") == 1
        # omit-when-1 must live INSIDE the hedge branch, after workers
        assert t.index("cfg.parallel_workers = Number(v3Workers)") \
            < t.index("cfg.tp_multiplier = Number(v3TpMult)")
    print("\n[PASS] all structural asserts hold.")
    print("Syntax check + rebuild:")
    print("  npx --no-install esbuild frontend/src/pages/Backtest.jsx --loader:.jsx=jsx --outfile=/dev/null")
    print("ACCEPTANCE: TP Mult=1 baseline diff vs 95e70e7e byte-identical;")
    print("then sweep 1.5/2.0/2.5/3.0/3.5 enqueued from the rebuilt UI, Workers=6.")


if __name__ == "__main__":
    main()
