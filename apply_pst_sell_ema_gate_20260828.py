#!/usr/bin/env python3
# apply_pst_sell_ema_gate_20260828.py
#
# ── PST_SELL_EMA_GATE_20260828 ── EMA slope regime gate (D7, locked
# 2026-08-28). Applies ON TOP of PST_SELL_ENTRY_FILTERS_20260828 and
# PST_SELL_CONFIRM_20260828 (anchors reference their content).
#
# WHAT: veto FADE entries against too-strong trends — the residual 2024
# damage (Jan/Oct trend months) after the level/expiry/confirm rounds.
#   CE sell blocked when EMA(period on 5m spot) rose  >= min_slope points
#   over slope_lookback bars; PE sell blocked when it fell <= −min_slope.
#
# WHERE: the RUNNER, filtering sig_res["signals"] at SIGNAL time before
# run_day_short — pst_v1_engine stays zero-diff (its seal) and the engine's
# confirm/level logic is untouched. The 5m stream is built EXACTLY like
# build_signals builds bars5 (warmup sessions + today, completed bars), via
# the same _warmup_bars/aggregate helpers, so gate and signal share one
# clock. No-lookahead: the gate reads the last 5m bar whose END <= signal ts
# (same convention as build_signals' sma_at).
#
# UNREADY POLICY: fail-OPEN + diag ema_gate_unready. The gate is a VETO, not
# a precondition — an unready veto must not kill an otherwise-valid signal.
# With 1 warmup session (~75 completed 5m bars) it is ready from day 2 for
# any sane period+lookback, so the counter should stay ~0.
#
# EMA: SMA-seeded (indices < period−1 undefined), alpha = 2/(period+1).
# config: ema_gate: {enabled, period, slope_lookback, min_slope}; default
# ABSENT/disabled → byte-identical results.

import os
import py_compile
import tempfile

FENCE = "PST_SELL_EMA_GATE_20260828"
PREV1 = "PST_SELL_ENTRY_FILTERS_20260828"
PREV2 = "PST_SELL_CONFIRM_20260828"

REPO = os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app")
TREES = [os.path.join(REPO, "backend"),
         os.path.join(REPO, "desktop", "src-tauri", "backend")]
RUNNER_REL = os.path.join("app", "backtest", "pst", "backtest_pst_sell_runner.py")


def _ro(src, old, new, tag):
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"ABORT [{tag}]: anchor found {n}x (need exactly 1). "
                         f"No files written.")
    return src.replace(old, new, 1)


def patch_runner(src):
    if FENCE in src:
        print("  runner: fence present — skipping (idempotent)")
        return src
    for prev in (PREV1, PREV2):
        if prev not in src:
            raise SystemExit(f"ABORT: runner missing prerequisite fence {prev}.")

    # G1 — pure, unit-testable helpers at module level (after DEFAULT_LEGS)
    old = """DEFAULT_LEGS = [
    {"id": "L1", "lots": 2, "sl_pct": 15, "spot_tg_points": 20},
    {"id": "L2", "lots": 1, "sl_pct": 15, "spot_tg_points": 50},
]"""
    new = old + """


# ── """ + FENCE + """ BEGIN ── pure helpers (module-level so they are
# unit-testable without the DB-coupled _impl).
def _ema_series(closes, period):
    \"\"\"SMA-seeded EMA; None for indices < period−1.\"\"\"
    n = len(closes)
    out = [None] * n
    if period <= 0 or n < period:
        return out
    seed = sum(closes[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    for i in range(period, n):
        out[i] = closes[i] * k + out[i - 1] * (1 - k)
    return out


def _ema_gate_blocks(side, slope, min_slope):
    \"\"\"Veto direction: CE fades an up-move → blocked when the trend is
    STRONGLY up (slope >= +min_slope); PE mirrored. Everything else passes —
    including strong counter-trend slopes, which are the fade's friend.\"\"\"
    if side == "CE":
        return slope >= min_slope
    return slope <= -min_slope
# ── """ + FENCE + """ END ──"""
    src = _ro(src, old, new, "G1 helpers")

    # G2 — config parse, appended after the confirm_minutes fence line
    old = """    confirm_minutes = min(30, max(0, int(cfg.get("confirm_minutes") or 0)))"""
    new = old + """
    # ── """ + FENCE + """ ── ema_gate: {enabled, period, slope_lookback,
    # min_slope}; absent/disabled = OFF (byte-identical results)
    _eg = cfg.get("ema_gate") or {}
    ema_gate_enabled = bool(_eg.get("enabled"))
    ema_gate_period = max(2, int(_eg.get("period") or 20))
    ema_gate_lookback = max(1, int(_eg.get("slope_lookback") or 6))
    ema_gate_min_slope = float(_eg.get("min_slope") or 15)"""
    src = _ro(src, old, new, "G2 cfg")

    # G3 — diag init
    old = """            "signals_skipped_confirm": 0,  # ── PST_SELL_CONFIRM_20260828 ──
            "blocked_warmup": 0, "blocked_gate": 0, "ambiguous": 0}"""
    new = """            "signals_skipped_confirm": 0,  # ── PST_SELL_CONFIRM_20260828 ──
            "signals_skipped_ema": 0,      # ── """ + FENCE + """ ──
            "ema_gate_unready": 0,         # ── """ + FENCE + """ ──
            "blocked_warmup": 0, "blocked_gate": 0, "ambiguous": 0}"""
    src = _ro(src, old, new, "G3 diag")

    # G4 — the gate itself: filter sig_res["signals"] at signal time.
    # Anchored on the block between signal accounting and the empty-check.
    old = """        diag["signals_total"] += sig_res["diag"]["signals"]
        diag["blocked_warmup"] += sig_res["diag"]["blocked_warmup"]
        diag["blocked_gate"] += sig_res["diag"]["blocked_gate"]
        if not sig_res["signals"]:
            continue"""
    new = """        diag["signals_total"] += sig_res["diag"]["signals"]
        diag["blocked_warmup"] += sig_res["diag"]["blocked_warmup"]
        diag["blocked_gate"] += sig_res["diag"]["blocked_gate"]
        # ── """ + FENCE + """ ── signal-time regime veto. Same 5m stream
        # construction as build_signals (warmup + today, completed bars);
        # gate index = last bar whose END <= signal ts (no lookahead).
        # Unready → fail-open + counter (a veto, not a precondition).
        if ema_gate_enabled and sig_res["signals"]:
            from app.backtest.pst.pst_v1_engine import _warmup_bars
            from app.backtest.pst.pst_indicators import aggregate as _agg5
            _b5 = _warmup_bars(warmup_sessions, 5) + \\
                [b for b in _agg5(spot, 5, day_start) if b["complete"]]
            _closes5 = [b["close"] for b in _b5]
            _ends5 = [b["ts"] + 300 for b in _b5]
            _ema5 = _ema_series(_closes5, ema_gate_period)
            _kept = []
            for _sig in sig_res["signals"]:
                _gi = None
                for _j in range(len(_ends5) - 1, -1, -1):
                    if _ends5[_j] <= _sig["ts"]:
                        _gi = _j
                        break
                if (_gi is None or _gi < ema_gate_lookback
                        or _ema5[_gi] is None
                        or _ema5[_gi - ema_gate_lookback] is None):
                    diag["ema_gate_unready"] += 1
                    _kept.append(_sig)
                    continue
                _slope = _ema5[_gi] - _ema5[_gi - ema_gate_lookback]
                if _ema_gate_blocks(_sig["side"], _slope, ema_gate_min_slope):
                    diag["signals_skipped_ema"] += 1
                else:
                    _kept.append(_sig)
            sig_res["signals"] = _kept
        if not sig_res["signals"]:
            continue"""
    src = _ro(src, old, new, "G4 gate")

    # G5 — audit line token
    old = """        + (f", cfm{confirm_minutes}m cfmBlk {diag['signals_skipped_confirm']}"
           if confirm_minutes else ""))   # ── PST_SELL_ENTRY_FILTERS_20260828 / PST_SELL_CONFIRM_20260828 ──"""
    new = """        + (f", cfm{confirm_minutes}m cfmBlk {diag['signals_skipped_confirm']}"
           if confirm_minutes else "")
        + (f", eGate {ema_gate_period}/{ema_gate_lookback}>={ema_gate_min_slope}"
           f" emaBlk {diag['signals_skipped_ema']}"
           f" emaUnready {diag['ema_gate_unready']}"
           if ema_gate_enabled else ""))   # ── PST_SELL_ENTRY_FILTERS_20260828 / PST_SELL_CONFIRM_20260828 / """ + FENCE + """ ──"""
    src = _ro(src, old, new, "G5 audit")
    return src


def _stage_compile(label, content):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as t:
        t.write(content)
        tmp = t.name
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        raise SystemExit(f"ABORT: staged compile failed for {label}: {e}")
    finally:
        os.unlink(tmp)


def main():
    patched_any = False
    for tree in TREES:
        run_p = os.path.join(tree, RUNNER_REL)
        if not os.path.isfile(run_p):
            print(f"[skip] tree not present: {tree}")
            if "src-tauri" in tree:
                print("       (desktop tree absent — re-run there before the "
                      "next PyInstaller build)")
            continue
        print(f"[tree] {tree}")
        run_src = open(run_p).read()
        run_new = patch_runner(run_src)
        _stage_compile(run_p, run_new)
        if run_new != run_src:
            open(run_p, "w").write(run_new)
            print(f"  wrote {run_p}")
        patched_any = True
    if not patched_any:
        raise SystemExit("ABORT: no tree found. Set SCALP_REPO.")
    print("DONE —", FENCE)


if __name__ == "__main__":
    main()
