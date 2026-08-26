#!/usr/bin/env python3
# apply_scalp_v3_hdiag_20260826.py
#
# V3-D6 — instrument the BOUGHT leg. The measurement gap: every diagnostic
# field scanned across eleven falsified levers describes the SIGNAL contract;
# the instrument V3 actually holds — the opposite-side hedge — has never had
# its own state recorded at entry. This closes that gap. Diagnostics ONLY:
# nothing downstream reads these for any trading decision, so results are
# byte-identical in P&L and differ only in the `condition` JSON payload.
#
# PREREQ: CONFIRM patch applied (anchors on its fence).
#
# NEW FIELDS appended to the diag JSON at ELECTION time (the hedge does not
# exist at candidate time), taken from the hedge symbol's own ctx — its
# indicator was already updated for this minute by the candidate scan:
#   hb    hedge candle body (close - open)
#   he8   hedge close - hedge's own EMA8
#   he20  hedge close - hedge's own EMA20_low
#   he20h hedge's own EMA20_high - hedge close
#   hgs   hedge's own gate-EMA slope (present when the gate indicator is on)
#   hvw   hedge close - hedge's own VWAP
# All getattr/get-safe -> null when an input is unavailable. Placed BEFORE
# the D4 pending branch so confirmation runs carry the same augmented diag.
#
# PRE-REGISTERED PROTOCOL (agreed before any scan runs — this is the guard
# against desperation-fitting):
#   1) Two instrumented runs: gate 89/30/1 + TP 3.5x, confirm OFF and ON.
#   2) A candidate slice must be all-years-net-positive with >=100 trades/yr
#      on BOTH cells, and must hold on the 2020-23 / 2024-26 walk-forward
#      split of each. Anything weaker is reported as absence of edge.
#   3) Zero hits on both cells -> V3 optimization closes (D5) with a
#      complete file.
#
# Backtest-only, dual-tree, safe today. No UI/surface changes (the CSV
# `condition` column carries the new keys automatically).

import os
import py_compile
import sys
import tempfile

REPO = os.getcwd()
BACKEND_TREES = ["backend", os.path.join("desktop", "src-tauri", "backend")]
RUNNER = os.path.join("app", "backtest", "runner", "backtest_hedge_runner.py")
FENCE = "SCALP_V3_HDIAG_20260826"


def fail(msg):
    print(f"\n[ABORT] {msg}\nNothing was written.")
    sys.exit(1)


RUNNER_EDITS = [
    (
        "                if hedge is None:\n"
        "                    continue  # no hedge available \u2192 skip (per spec)\n"
        "\n"
        "                # \u2500\u2500 SCALP_V3_CONFIRM_20260826 BEGIN: D4.1 \u2014 pending, not order.\n",
        "                if hedge is None:\n"
        "                    continue  # no hedge available \u2192 skip (per spec)\n"
        "\n"
        "                # \u2500\u2500 SCALP_V3_HDIAG_20260826 BEGIN \u2014 augment the entry snapshot\n"
        "                # with the BOUGHT leg's own state. Election-time by necessity\n"
        "                # (no hedge exists at candidate time); the hedge ctx's\n"
        "                # indicator was already updated for this minute by the scan.\n"
        "                # Diagnostics ONLY \u2014 no trading decision reads these. Placed\n"
        "                # before the D4 branch so pendings carry the augmented diag.\n"
        "                _hctx_d = ctxs.get(hedge[\"symbol\"])\n"
        "                _hv_d = ((_hctx_d.indicator.values or {})\n"
        "                         if _hctx_d is not None else {})\n"
        "                _hc_d = _hctx_d.by_ts.get(ts) if _hctx_d is not None else None\n"
        "                _hcl_d = float(hedge[\"close\"])\n"
        "                _r2h = lambda v: round(v, 2)\n"
        "                _he8 = _hv_d.get(\"ema8\")\n"
        "                _he20 = _hv_d.get(\"ema20_low\")\n"
        "                _he20h = _hv_d.get(\"ema20_high\")\n"
        "                try:\n"
        "                    _dd = json.loads(diag)\n"
        "                except Exception:\n"
        "                    _dd = {}\n"
        "                _dd.update({\n"
        "                    \"hb\": (_r2h(_hc_d.close - _hc_d.open)\n"
        "                           if _hc_d is not None else None),\n"
        "                    \"he8\": _r2h(_hcl_d - _he8) if _he8 is not None else None,\n"
        "                    \"he20\": (_r2h(_hcl_d - _he20)\n"
        "                             if _he20 is not None else None),\n"
        "                    \"he20h\": (_r2h(_he20h - _hcl_d)\n"
        "                              if _he20h is not None else None),\n"
        "                    \"hgs\": (_r2h(_hv_d.get(\"gate_ema_slope\"))\n"
        "                            if _hv_d.get(\"gate_ema_slope\") is not None else None),\n"
        "                    \"hvw\": (_r2h(_hcl_d - _hv_d.get(\"vwap\"))\n"
        "                            if _hv_d.get(\"vwap\") is not None else None),\n"
        "                })\n"
        "                diag = json.dumps(_dd, separators=(\",\", \":\"))\n"
        "                # \u2500\u2500 SCALP_V3_HDIAG_20260826 END \u2500\u2500\n"
        "\n"
        "                # \u2500\u2500 SCALP_V3_CONFIRM_20260826 BEGIN: D4.1 \u2014 pending, not order.\n",
        1,
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
                 f"{os.path.relpath(path, REPO)} — is the CONFIRM patch applied?")
        text = text.replace(old, new)
    return text


def sim_augment():
    """Replica of the augment logic: field math, null-safety, JSON merge."""
    import json as _json

    def augment(diag, hedge_close, hv, hc):
        r2 = lambda v: round(v, 2)
        he8, he20, he20h = hv.get("ema8"), hv.get("ema20_low"), hv.get("ema20_high")
        try:
            dd = _json.loads(diag)
        except Exception:
            dd = {}
        dd.update({
            "hb": r2(hc["close"] - hc["open"]) if hc is not None else None,
            "he8": r2(hedge_close - he8) if he8 is not None else None,
            "he20": r2(hedge_close - he20) if he20 is not None else None,
            "he20h": r2(he20h - hedge_close) if he20h is not None else None,
            "hgs": (r2(hv.get("gate_ema_slope"))
                    if hv.get("gate_ema_slope") is not None else None),
            "hvw": (r2(hedge_close - hv.get("vwap"))
                    if hv.get("vwap") is not None else None),
        })
        return _json.dumps(dd, separators=(",", ":"))

    base = _json.dumps({"b": 1.5, "rk": 8.0, "gs": -2.1}, separators=(",", ":"))
    # full inputs
    out = _json.loads(augment(base, 165.30,
        {"ema8": 163.1, "ema20_low": 160.4, "ema20_high": 166.9,
         "gate_ema_slope": -0.8, "vwap": 168.2},
        {"open": 164.9, "close": 165.30}))
    assert out["b"] == 1.5 and out["rk"] == 8.0 and out["gs"] == -2.1  # originals kept
    assert out["he8"] == 2.2 and out["he20"] == 4.9 and out["he20h"] == 1.6
    assert out["hgs"] == -0.8 and out["hvw"] == -2.9 and out["hb"] == 0.4
    # warmup nulls + missing candle
    out = _json.loads(augment(base, 165.30, {}, None))
    assert all(out[k] is None for k in ("hb", "he8", "he20", "he20h", "hgs", "hvw"))
    # corrupt diag -> fields still land
    out = _json.loads(augment("not json", 100.0, {"ema8": 99.0}, None))
    assert out["he8"] == 1.0 and "b" not in out
    print("[SIM] hedge-leg diag augment: math/null-safety/merge 3/3 OK")


def main():
    sim_augment()

    trees = [t for t in BACKEND_TREES if os.path.isdir(os.path.join(REPO, t, "app"))]
    if not trees:
        fail("no backend tree found — run from the scalp-app repo root")

    staged = []
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

    if not staged:
        print("\n[DONE] nothing to do — fence already present.")
        return

    for path, text in staged:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[WROTE] {os.path.relpath(path, REPO)}")

    for tree in trees:
        with open(os.path.join(REPO, tree, RUNNER), "r", encoding="utf-8") as f:
            t = f.read()
        assert t.count(FENCE) == 2
        # HDIAG must sit BEFORE the D4 pending branch so pendings carry it
        assert t.index("SCALP_V3_HDIAG_20260826 BEGIN") \
            < t.index("SCALP_V3_CONFIRM_20260826 BEGIN: D4.1")
    print("\n[PASS] all structural asserts hold.")
    print("Runs (both at gate 89/30/1 + TP 3.5x, Workers 6):")
    print("  1) Entry Confirm OFF   2) Entry Confirm ON")
    print("P&L must match c5504442 / 9ab2240c exactly — only the condition")
    print("JSON gains hb/he8/he20/he20h/hgs/hvw. Upload both CSVs for the scan.")


if __name__ == "__main__":
    main()
