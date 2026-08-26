#!/usr/bin/env python3
# apply_scalp_v3_ema_gate_20260826.py
#
# V3-D3b — port SCALP_V1's EMA gate (D10.1) to SCALP_V3 backtests.
# PREREQ: apply_scalp_v3_tpmult_20260826.py must be applied first (anchors).
#
# TWO HALVES, BOTH REQUIRED (verified against the engine source):
#
#   BACKEND (backtest_hedge_runner.py — backtest-only, dual-tree, safe today)
#   The shared engine's gate is FAIL-CLOSED by doctrine: when ema_gate.enabled
#   and gate_ema_slope is None, every entry is BLOCKED. The slope only exists
#   if IndicatorEnginePineV19 is constructed with gate params — V1's _Ctx
#   does this; V3's _Ctx builds it bare. Frontend-only wiring would therefore
#   produce silent ZERO-TRADE runs the moment the gate is enabled. This
#   patch mirrors V1's construction exactly (the BT override is installed
#   before ctxs are built, so load_strategy_config returns this run's cfg).
#   Side effect: the diag "gs" field (null in every V3 export so far) starts
#   carrying gate_ema_slope at entry — regime slicing unlocked.
#
#   FRONTEND (Backtest.jsx, both copies)
#   v3EmaGate on/off + period / slope-lookback / min-slope fields in the V3
#   row, prefilled with V1's sealed values (89 / 30 / 1) AS HYPOTHESES —
#   gate OFF by default, OMIT-WHEN-OFF so baseline configs stay byte-identical
#   to 95e70e7e. All three mirrored lists updated in the same commit.
#
#   Display surfaces are already key-generic (verified): RunComparison
#   PARAM_DEFS "EMA gate" row, queue `eGate p/lb` token, run-detail chip.
#   paramFormat.js x4 untouched (Settings formatter; backtest-only keys).
#
# GATE SEMANTICS (for reading results): entry requires the SIGNAL contract's
# gate-EMA slope <= -min_slope_pts over the lookback — i.e. the tracked
# premium's EMA must be FALLING. Slope None during warmup blocks (fail-closed),
# identical to V1.
#
# ACCEPTANCE:
#   • Gate OFF, baseline config → byte-identical to 95e70e7e (omit-when-off).
#   • Isolation runs: gate 89/30/1 at TP Mult=1, and gate 89/30/1 at the
#     TP-sweep winner. One variable at a time.

import glob
import os
import py_compile
import sys
import tempfile

REPO = os.getcwd()
BACKEND_TREES = ["backend", os.path.join("desktop", "src-tauri", "backend")]
RUNNER = os.path.join("app", "backtest", "runner", "backtest_hedge_runner.py")
V1_RUNNER = os.path.join("app", "backtest", "runner", "backtest_runner.py")

FENCE = "SCALP_V3_EMA_GATE_20260826"


def fail(msg):
    print(f"\n[ABORT] {msg}\nNothing was written.")
    sys.exit(1)


RUNNER_EDITS = [
    (
        "        self.indicator = IndicatorEnginePineV19()\n"
        "        self.conditions_engine = ConditionEngineV19()\n",
        "        # \u2500\u2500 SCALP_V3_EMA_GATE_20260826 \u2500\u2500 gate params from the run's merged\n"
        "        # cfg (the BT_CONFIG_OVERRIDE token is installed before ctxs are\n"
        "        # built, so load_strategy_config returns this run's overrides).\n"
        "        # MANDATORY when ema_gate.enabled: the engine's gate is FAIL-CLOSED\n"
        "        # \u2014 a bare indicator never computes gate_ema_slope, and slope=None\n"
        "        # blocks EVERY entry (silent zero-trade run). Mirrors V1's _Ctx.\n"
        "        from app.config.strategy_loader import load_strategy_config as _lsc\n"
        "        _eg = (_lsc(strategy_id) or {}).get(\"ema_gate\") or {}\n"
        "        self.indicator = IndicatorEnginePineV19(\n"
        "            gate_ema_period=(int(_eg.get(\"period\", 144) or 144)\n"
        "                             if _eg.get(\"enabled\") else None),\n"
        "            gate_slope_lookback=int(_eg.get(\"slope_lookback\", 30) or 30))\n"
        "        self.conditions_engine = ConditionEngineV19()\n",
        1,
    ),
]

JSX_EDITS = [
    (
        "  const [v3TpMult, setV3TpMult] = useState(saved.v3TpMult ?? 1);   // \u2500\u2500 SCALP_V3_TPMULT_20260826 \u2500\u2500\n",
        "  const [v3TpMult, setV3TpMult] = useState(saved.v3TpMult ?? 1);   // \u2500\u2500 SCALP_V3_TPMULT_20260826 \u2500\u2500\n"
        "  // \u2500\u2500 SCALP_V3_EMA_GATE_20260826 \u2500\u2500 defaults are V1's sealed 89/30/1 as\n"
        "  // HYPOTHESES for V3 (buy-side economics may want different numbers).\n"
        "  const [v3EmaGate, setV3EmaGate] = useState(saved.v3EmaGate ?? false);\n"
        "  const [v3EmaPeriod, setV3EmaPeriod] = useState(saved.v3EmaPeriod ?? 89);\n"
        "  const [v3EmaLookback, setV3EmaLookback] = useState(saved.v3EmaLookback ?? 30);\n"
        "  const [v3EmaMinSlope, setV3EmaMinSlope] = useState(saved.v3EmaMinSlope ?? 1);\n",
        1,
    ),
    (
        "      if (Number(v3TpMult) > 0 && Number(v3TpMult) !== 1) cfg.tp_multiplier = Number(v3TpMult);\n",
        "      if (Number(v3TpMult) > 0 && Number(v3TpMult) !== 1) cfg.tp_multiplier = Number(v3TpMult);\n"
        "      // \u2500\u2500 SCALP_V3_EMA_GATE_20260826 \u2500\u2500 omit-when-off: baseline configs\n"
        "      // stay byte-identical. The runner constructs the gate indicator from\n"
        "      // this key; the shared engine applies the fail-closed slope gate.\n"
        "      if (v3EmaGate) cfg.ema_gate = { enabled: true, period: Number(v3EmaPeriod) || 89, slope_lookback: Number(v3EmaLookback) || 30, min_slope_pts: Number(v3EmaMinSlope) || 0 };\n",
        1,
    ),
    (
        "              <Field label=\"TP Mult\"><input type=\"number\" min=\"0\" step=\"0.5\" style={inputStyle} value={v3TpMult} onChange={(e) => setV3TpMult(e.target.value)} /></Field>\n",
        "              <Field label=\"TP Mult\"><input type=\"number\" min=\"0\" step=\"0.5\" style={inputStyle} value={v3TpMult} onChange={(e) => setV3TpMult(e.target.value)} /></Field>\n"
        "              {/* \u2500\u2500 SCALP_V3_EMA_GATE_20260826 \u2500\u2500 signal-premium EMA must be\n"
        "                  FALLING >= min slope over the lookback; warmup blocks\n"
        "                  (fail-closed, V1 doctrine). Off = keys omitted. */}\n"
        "              <Field label=\"EMA Gate\">\n"
        "                <select style={inputStyle} value={v3EmaGate ? \"1\" : \"0\"} onChange={(e) => setV3EmaGate(e.target.value === \"1\")}>\n"
        "                  <option value=\"0\">Off</option>\n"
        "                  <option value=\"1\">On</option>\n"
        "                </select>\n"
        "              </Field>\n"
        "              {v3EmaGate && (\n"
        "                <>\n"
        "                  <Field label=\"Gate EMA Period\"><input type=\"number\" min=\"2\" style={inputStyle} value={v3EmaPeriod} onChange={(e) => setV3EmaPeriod(e.target.value)} /></Field>\n"
        "                  <Field label=\"Slope Lookback\"><input type=\"number\" min=\"1\" style={inputStyle} value={v3EmaLookback} onChange={(e) => setV3EmaLookback(e.target.value)} /></Field>\n"
        "                  <Field label=\"Min Slope Pts\"><input type=\"number\" min=\"0\" step=\"0.5\" style={inputStyle} value={v3EmaMinSlope} onChange={(e) => setV3EmaMinSlope(e.target.value)} /></Field>\n"
        "                </>\n"
        "              )}\n",
        1,
    ),
    (
        "      v3TpMult,   // \u2500\u2500 SCALP_V3_TPMULT_20260826 \u2500\u2500\n",
        "      v3TpMult,   // \u2500\u2500 SCALP_V3_TPMULT_20260826 \u2500\u2500\n"
        "      v3EmaGate, v3EmaPeriod, v3EmaLookback, v3EmaMinSlope,   // \u2500\u2500 SCALP_V3_EMA_GATE_20260826 \u2500\u2500\n",
        3,
    ),
]


def apply_edits(path, edits):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if FENCE in text:
        print(f"[SKIP] fence already present: {os.path.relpath(path, REPO)}")
        return None
    for i, (old, new, want) in enumerate(edits, 1):
        n = text.count(old)
        if n != want:
            fail(f"anchor #{i} matched {n}x (need exactly {want}) in "
                 f"{os.path.relpath(path, REPO)} — are the PARALLEL and "
                 f"TPMULT patches applied first?")
        text = text.replace(old, new)
    return text


def main():
    staged = []

    # backend, dual-tree
    trees = [t for t in BACKEND_TREES if os.path.isdir(os.path.join(REPO, t, "app"))]
    if not trees:
        fail("no backend tree found — run from the scalp-app repo root")
    for tree in trees:
        path = os.path.join(REPO, tree, RUNNER)
        if not os.path.isfile(path):
            fail(f"missing file: {path}")
        text = apply_edits(path, RUNNER_EDITS)
        if text is None:
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as tf:
            tf.write(text); tmp = tf.name
        try:
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as e:
            fail(f"staged compile failed for {tree}/{RUNNER}:\n{e}")
        finally:
            os.unlink(tmp)
        staged.append((path, text))
        print(f"[OK] staged {tree}/{RUNNER} (compiles)")

    # frontend, all copies
    jsx_paths = [os.path.join(REPO, "frontend", "src", "pages", "Backtest.jsx")]
    jsx_paths += sorted(set(
        glob.glob(os.path.join(REPO, "desktop", "**", "Backtest.jsx"),
                  recursive=True)) - set(jsx_paths))
    found = [p for p in jsx_paths if os.path.isfile(p)]
    if not found:
        fail("Backtest.jsx not found")
    for path in found:
        text = apply_edits(path, JSX_EDITS)
        if text is not None:
            staged.append((path, text))
            print(f"[OK] staged {os.path.relpath(path, REPO)}")

    if not staged:
        print("\n[DONE] nothing to do — all fences already present.")
        return

    for path, text in staged:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[WROTE] {os.path.relpath(path, REPO)}")

    # post-write structural asserts
    for tree in trees:
        rp = os.path.join(REPO, tree, RUNNER)
        with open(rp, "r", encoding="utf-8") as f:
            t = f.read()
        assert "gate_ema_period=" in t and "gate_slope_lookback=" in t
        # pattern-parity with V1's runner: same constructor keywords
        v1p = os.path.join(REPO, tree, V1_RUNNER)
        if os.path.isfile(v1p):
            with open(v1p, "r", encoding="utf-8") as f:
                v1t = f.read()
            assert "gate_ema_period=" in v1t and "gate_slope_lookback=" in v1t, \
                "V1 runner lacks the gate construction this port mirrors"
    for path, _ in staged:
        if path.endswith(".jsx"):
            with open(path, "r", encoding="utf-8") as f:
                t = f.read()
            assert t.count("v3EmaGate, v3EmaPeriod, v3EmaLookback, v3EmaMinSlope,   // \u2500\u2500 SCALP_V3_EMA_GATE_20260826 \u2500\u2500") == 3
            assert "if (v3EmaGate) cfg.ema_gate = { enabled: true," in t
            assert t.count("value={v3EmaPeriod}") == 1
    print("\n[PASS] all structural asserts hold.")
    print("Syntax check + rebuild:")
    print("  npx --no-install esbuild frontend/src/pages/Backtest.jsx --loader:.jsx=jsx --outfile=/dev/null")
    print("ACCEPTANCE: gate OFF baseline diff vs 95e70e7e byte-identical;")
    print("then isolation runs \u2014 gate 89/30/1 at TP Mult=1, and at the TP winner.")


if __name__ == "__main__":
    main()
