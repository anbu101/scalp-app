#!/usr/bin/env python3
# apply_pst_sell_confirm_20260828.py
#
# ── PST_SELL_CONFIRM_20260828 ── N-minute delayed entry with SL-touch abort
# (D6, locked 2026-08-28). Applies ON TOP of PST_SELL_ENTRY_FILTERS_20260828
# (anchors reference that fence's content; aborts if it is absent).
#
# WHY THIS SHAPE (and not classic "close holds the level" confirmation):
# PST_SELL FADES the crossed level — spot falling back through the level is
# the WINNER path (premium collapse → TP), so momentum confirmation would
# keep losers and drop winners. The fast burns (53.8% of SPOT_SLs die
# ≤10min; median SL duration 9min vs TP 46min) are runaway continuations.
# Because the seller SL is a SPOT level anchored to the SIGNAL bar close —
# independent of the option fill — "would this trade have died in its first
# N minutes" is exactly observable before entering:
#
#   * wait N minutes after the signal;
#   * if spot touches ANY active leg's would-be SL level during the wait
#     (CE: high >= spot+tg, PE: low <= spot−tg; tightest leg dominates)
#     → ABORT the entry (diag signals_skipped_confirm); slot frees at the
#     touch minute;
#   * else enter at the close of the candle N minutes later (selection
#     re-priced at that shifted minute, same runner select_option), with
#     SL levels still SIGNAL-ANCHORED (unchanged) and premium TP computed
#     off the actual later entry, exactly as today.
#
# confirm_minutes = 0 (default) → byte-identical to the current engine.
# Clamped to 0..30 (a wait beyond 30min is not this strategy).

import os
import py_compile
import sys
import tempfile

FENCE = "PST_SELL_CONFIRM_20260828"
PREV = "PST_SELL_ENTRY_FILTERS_20260828"

REPO = os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app")
TREES = [os.path.join(REPO, "backend"),
         os.path.join(REPO, "desktop", "src-tauri", "backend")]
ENGINE_REL = os.path.join("app", "backtest", "pst", "pst_sell_engine.py")
RUNNER_REL = os.path.join("app", "backtest", "pst", "backtest_pst_sell_runner.py")


def _ro(src, old, new, tag):
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"ABORT [{tag}]: anchor found {n}x (need exactly 1). "
                         f"No files written.")
    return src.replace(old, new, 1)


def patch_engine(src):
    if FENCE in src:
        print("  engine: fence present — skipping (idempotent)")
        return src
    if PREV not in src:
        raise SystemExit(f"ABORT: engine missing prerequisite fence {PREV}.")

    # C1 — signature gains confirm_minutes
    old = """                  risk: Optional[dict] = None,
                  allowed_levels: Optional[frozenset] = None) -> Dict:"""
    new = """                  risk: Optional[dict] = None,
                  allowed_levels: Optional[frozenset] = None,
                  confirm_minutes: int = 0) -> Dict:"""
    src = _ro(src, old, new, "C1 signature")

    # C2 — diag key
    old = """            "signals_skipped_level": 0,   # ── PST_SELL_ENTRY_FILTERS_20260828 ──
            "ambiguous": 0}"""
    new = """            "signals_skipped_level": 0,   # ── PST_SELL_ENTRY_FILTERS_20260828 ──
            "signals_skipped_confirm": 0,  # ── """ + FENCE + """ ──
            "ambiguous": 0}"""
    src = _ro(src, old, new, "C2 diag")

    # C3 — per-day precompute after busy_until init (spot lookup + tightest tg)
    old = "    busy_until = -1\n    for sig in signals:"
    new = """    busy_until = -1
    # ── """ + FENCE + """ ── wait-window scan needs 1m spot by ts and the
    # TIGHTEST active leg tg (that leg dies first; whole entry is atomic).
    _cfm = max(0, int(confirm_minutes or 0))
    _spot_by = {int(c["ts"]): c for c in spot_1m} if _cfm else {}
    _tgs = [float(l["spot_tg_points"]) for l in legs
            if float(l.get("spot_tg_points") or 0) > 0]
    _tg_min = min(_tgs) if _tgs else None
    for sig in signals:"""
    src = _ro(src, old, new, "C3 precompute")

    # C4 — the wait/abort + shifted entry, replacing the select/enter block
    old = """        if sig["ts"] >= eod_ts:
            continue
        sel = select_option(sig["side"], sig["ts"])
        if sel is None:
            diag["signals_skipped_select"] += 1
            continue
        pos = simulate_position_short(legs, sig["side"], sig["ts"],
                                      float(sel["entry_price"]), float(sig["spot"]),
                                      sel["candles"], spot_1m, eod_ts,
                                      risk=risk)"""
    new = """        if sig["ts"] >= eod_ts:
            continue
        # ── """ + FENCE + """ ── N-minute wait with SL-touch abort. SL
        # levels are SIGNAL-anchored (sig["spot"] ± tg) and the spot path is
        # fill-independent, so the scan sees exactly what the position's
        # first N monitored minutes would have seen. Spot falling back
        # through the crossed level is NOT an abort — that is the TP path.
        _ets = sig["ts"] + _cfm * 60
        if _cfm and _tg_min is not None:
            _is_ce = sig["side"] == "CE"
            _sl_lvl = (float(sig["spot"]) + _tg_min) if _is_ce \\
                else (float(sig["spot"]) - _tg_min)
            _touch = None
            for _m in range(1, _cfm + 1):
                _sc = _spot_by.get(sig["ts"] + _m * 60)
                if _sc is None:
                    continue
                if (_is_ce and float(_sc["high"]) >= _sl_lvl) or \\
                        ((not _is_ce) and float(_sc["low"]) <= _sl_lvl):
                    _touch = sig["ts"] + _m * 60
                    break
            if _touch is not None:
                diag["signals_skipped_confirm"] += 1
                busy_until = _touch + 60   # we were committed until it died
                continue
        if _ets >= eod_ts:
            diag["signals_skipped_confirm"] += 1
            continue
        sel = select_option(sig["side"], _ets)
        if sel is None:
            diag["signals_skipped_select"] += 1
            continue
        pos = simulate_position_short(legs, sig["side"], _ets,
                                      float(sel["entry_price"]), float(sig["spot"]),
                                      sel["candles"], spot_1m, eod_ts,
                                      risk=risk)"""
    src = _ro(src, old, new, "C4 wait/abort")
    return src


def patch_runner(src):
    if FENCE in src:
        print("  runner: fence present — skipping (idempotent)")
        return src
    if PREV not in src:
        raise SystemExit(f"ABORT: runner missing prerequisite fence {PREV}.")

    # R1 — cfg parse, appended to the entry-filters fence block
    old = """    allowed_levels = frozenset(_raw_levels) or None   # empty = filter OFF
    skip_expiry_day = bool(cfg.get("skip_expiry_day"))
    # ── PST_SELL_ENTRY_FILTERS_20260828 END ──"""
    new = """    allowed_levels = frozenset(_raw_levels) or None   # empty = filter OFF
    skip_expiry_day = bool(cfg.get("skip_expiry_day"))
    # ── PST_SELL_ENTRY_FILTERS_20260828 END ──
    # ── """ + FENCE + """ ── 0 = off; clamped 0..30 (a >30min wait is not
    # this strategy — clamp, don't abort: the value is a tuning knob, not a
    # category like level names)
    confirm_minutes = min(30, max(0, int(cfg.get("confirm_minutes") or 0)))"""
    src = _ro(src, old, new, "R1 cfg")

    # R2 — diag init
    old = """            "days_skipped_expiry": 0,     # ── PST_SELL_ENTRY_FILTERS_20260828 ──
            "blocked_warmup": 0, "blocked_gate": 0, "ambiguous": 0}"""
    new = """            "days_skipped_expiry": 0,     # ── PST_SELL_ENTRY_FILTERS_20260828 ──
            "signals_skipped_confirm": 0,  # ── """ + FENCE + """ ──
            "blocked_warmup": 0, "blocked_gate": 0, "ambiguous": 0}"""
    src = _ro(src, old, new, "R2 diag")

    # R3 — pass through
    old = """                            risk=risk,
                            allowed_levels=allowed_levels)   # ── PST_SELL_ENTRY_FILTERS_20260828 ──"""
    new = """                            risk=risk,
                            allowed_levels=allowed_levels,   # ── PST_SELL_ENTRY_FILTERS_20260828 ──
                            confirm_minutes=confirm_minutes)   # ── """ + FENCE + """ ──"""
    src = _ro(src, old, new, "R3 call")

    # R4 — accumulate
    old = """                  "signals_skipped_level",   # ── PST_SELL_ENTRY_FILTERS_20260828 ──
                  "ambiguous"):"""
    new = """                  "signals_skipped_level",   # ── PST_SELL_ENTRY_FILTERS_20260828 ──
                  "signals_skipped_confirm",  # ── """ + FENCE + """ ──
                  "ambiguous"):"""
    src = _ro(src, old, new, "R4 accumulate")

    # R5 — audit line token
    old = """        + (f", expDaysSkipped {diag['days_skipped_expiry']}"
           if skip_expiry_day else ""))   # ── PST_SELL_ENTRY_FILTERS_20260828 ──"""
    new = """        + (f", expDaysSkipped {diag['days_skipped_expiry']}"
           if skip_expiry_day else "")
        + (f", cfm{confirm_minutes}m cfmBlk {diag['signals_skipped_confirm']}"
           if confirm_minutes else ""))   # ── PST_SELL_ENTRY_FILTERS_20260828 / """ + FENCE + """ ──"""
    src = _ro(src, old, new, "R5 audit")
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
        eng_p = os.path.join(tree, ENGINE_REL)
        run_p = os.path.join(tree, RUNNER_REL)
        if not os.path.isfile(eng_p) or not os.path.isfile(run_p):
            print(f"[skip] tree not present: {tree}")
            if "src-tauri" in tree:
                print("       (desktop tree absent — re-run there before the "
                      "next PyInstaller build)")
            continue
        print(f"[tree] {tree}")
        eng_src, run_src = open(eng_p).read(), open(run_p).read()
        eng_new, run_new = patch_engine(eng_src), patch_runner(run_src)
        _stage_compile(eng_p, eng_new)
        _stage_compile(run_p, run_new)
        if eng_new != eng_src:
            open(eng_p, "w").write(eng_new)
            print(f"  wrote {eng_p}")
        if run_new != run_src:
            open(run_p, "w").write(run_new)
            print(f"  wrote {run_p}")
        patched_any = True
    if not patched_any:
        raise SystemExit("ABORT: no tree found. Set SCALP_REPO.")
    print("DONE —", FENCE)


if __name__ == "__main__":
    main()
