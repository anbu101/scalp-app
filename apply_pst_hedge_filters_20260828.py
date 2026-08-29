#!/usr/bin/env python3
# apply_pst_hedge_filters_20260828.py
#
# ── PST_HEDGE_ENTRY_FILTERS_20260828 + PST_HEDGE_CONFIRM_20260828 ──
# Ports the two ACCEPTED PST_SELL rounds to PST_HEDGE (H1–H5, locked
# 2026-08-28). The falsified EMA gate is NOT ported.
#
# WHY PORTABLE: PST_HEDGE reproduces PST_SELL's event stream trade-for-trade
# by construction (signals are PST_V1's, verbatim; SPOT_SL is the same
# signal-anchored spot level; SIG_TP lives on the signal contract). Both
# filters key off the SIGNAL, not the held contract, so the mechanics carry
# over unchanged — only the P&L differs, which is the point of the build.
#
# WHY SEPARATE CONFIG KEYS (H5): the two strategies demonstrably want
# DIFFERENT level sets. S1 is +Rs288k for the seller but -Rs94k for the
# hedge (it holds the OPPOSITE contract, so a level that mean-reverts for
# the short need not pay the long). Sharing a key would be a footgun. Keys
# here are the same NAMES on a different strategy's config object, which the
# UI builds separately per strategy id.
#
#   allowed_levels   — allowlist on the NEAREST-CROSSED level of the SIGNAL
#                      (CE signal = lowest crossed, PE = highest). Empty =
#                      OFF. Fail-closed on unknown names.
#   skip_expiry_day  — full-day skip on weekly expiry (H2; -Rs784k over 740
#                      trades, hurts in 5/7 years).
#   confirm_minutes  — wait N min, abort if spot touches the would-be SL
#                      level during the wait (H3). Clamped 0..30.
#
# All three default OFF → existing configs reproduce byte-identically.
#
# TOUCHES: backtest/pst/pst_hedge_engine.py, backtest_pst_hedge_runner.py.
# pst_sell_engine / pst_v1_engine are NOT modified — the pivot helpers are
# IMPORTED from pst_sell_engine so the two strategies can never drift on
# what "nearest crossed level" means.

import os
import py_compile
import tempfile

F_LVL = "PST_HEDGE_ENTRY_FILTERS_20260828"
F_CFM = "PST_HEDGE_CONFIRM_20260828"

REPO = os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app")
TREES = [os.path.join(REPO, "backend"),
         os.path.join(REPO, "desktop", "src-tauri", "backend")]
ENGINE_REL = os.path.join("app", "backtest", "pst", "pst_hedge_engine.py")
RUNNER_REL = os.path.join("app", "backtest", "pst", "backtest_pst_hedge_runner.py")


def _ro(src, old, new, tag):
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"ABORT [{tag}]: anchor found {n}x (need exactly 1). "
                         f"No files written.")
    return src.replace(old, new, 1)


# ────────────────────────────────────────────────────────────────────
def patch_engine(src):
    if F_LVL in src:
        print("  engine: fence present — skipping (idempotent)")
        return src

    # E1 — import the pivot helpers from the sell engine (single source of
    # truth for nearest-crossed semantics; no duplicate definition)
    old = """# Signal generation is PST_V1's, verbatim (re-export for the runner).
try:
    from app.backtest.pst.pst_v1_engine import build_signals  # noqa: F401
except ImportError:  # standalone tests
    from pst_v1_engine import build_signals  # type: ignore  # noqa: F401"""
    new = old + f"""

# ── {F_LVL} ── pivot-level helpers are IMPORTED from pst_sell_engine, not
# redefined: PST_HEDGE trades PST_SELL's event stream, so "nearest crossed
# level" must mean exactly the same thing in both or the two strategies
# would silently diverge on identical signals.
# NOTE: only nearest_crossed_level is imported here. PIVOT_RANK is NOT —
# the engine has no use for it and pyflakes (which does not honour noqa) is
# a hard build gate, so an unused re-export would fail the gate. The runner
# imports PIVOT_RANK straight from pst_sell_engine where it needs it.
try:
    from app.backtest.pst.pst_sell_engine import nearest_crossed_level
except ImportError:  # standalone tests
    from pst_sell_engine import nearest_crossed_level  # type: ignore"""
    src = _ro(src, old, new, "E1 import")

    # E2 — run_day_hedge signature
    old = """                  *, side_mode: str = "BOTH", max_trades_per_day: int = 0,
                  risk: Optional[dict] = None) -> Dict:"""
    new = """                  *, side_mode: str = "BOTH", max_trades_per_day: int = 0,
                  risk: Optional[dict] = None,
                  allowed_levels: Optional[frozenset] = None,
                  confirm_minutes: int = 0) -> Dict:"""
    src = _ro(src, old, new, "E2 signature")

    # E3 — diag keys
    old = """    diag = {"signals_taken": 0, "signals_skipped_busy": 0,
            "signals_skipped_side": 0, "signals_skipped_select": 0,
            "signals_skipped_cap": 0, "signals_skipped_risk": 0,
            "ambiguous": 0}"""
    new = """    diag = {"signals_taken": 0, "signals_skipped_busy": 0,
            "signals_skipped_side": 0, "signals_skipped_select": 0,
            "signals_skipped_cap": 0, "signals_skipped_risk": 0,
            "signals_skipped_level": 0,    # ── """ + F_LVL + """ ──
            "signals_skipped_confirm": 0,  # ── """ + F_CFM + """ ──
            "ambiguous": 0}"""
    src = _ro(src, old, new, "E3 diag")

    # E4 — precompute for the wait scan
    old = "    busy_until = -1\n    for sig in signals:"
    new = """    busy_until = -1
    # ── """ + F_CFM + """ ── wait-window scan needs 1m spot by ts and the
    # TIGHTEST active leg tg (that leg dies first; the entry is atomic).
    _cfm = max(0, int(confirm_minutes or 0))
    _spot_by = {int(c["ts"]): c for c in spot_1m} if _cfm else {}
    _tgs = [float(l["spot_tg_points"]) for l in legs
            if float(l.get("spot_tg_points") or 0) > 0]
    _tg_min = min(_tgs) if _tgs else None
    for sig in signals:"""
    src = _ro(src, old, new, "E4 precompute")

    # E5 — level gate: after side_mode, before busy (a level-blocked signal
    # never occupied the slot, so it must not be counted busy either)
    old = """        if side_mode != "BOTH" and sig["side"] != side_mode:
            diag["signals_skipped_side"] += 1
            continue
        if sig["ts"] < busy_until:"""
    new = """        if side_mode != "BOTH" and sig["side"] != side_mode:
            diag["signals_skipped_side"] += 1
            continue
        # ── """ + F_LVL + """ ── allowlist on the SIGNAL's nearest-crossed
        # level (None/empty = OFF)
        if allowed_levels:
            _lvl = nearest_crossed_level(sig["side"], sig.get("levels_crossed"))
            if _lvl is None or _lvl not in allowed_levels:
                diag["signals_skipped_level"] += 1
                continue
        if sig["ts"] < busy_until:"""
    src = _ro(src, old, new, "E5 level gate")

    # E6 — wait/abort + shifted PAIR selection
    old = """        if sig["ts"] >= eod_ts:
            continue
        sel = select_pair(sig["side"], sig["ts"])
        if sel is None:
            diag["signals_skipped_select"] += 1
            continue
        pos = simulate_position_hedge(
            legs, sig["side"], sig["ts"],
            float(sel["held_entry"]), float(sel["sig_entry"]),
            float(sig["spot"]),
            sel["held_candles"], sel["sig_candles"], spot_1m, eod_ts,
            risk=risk)"""
    new = """        if sig["ts"] >= eod_ts:
            continue
        # ── """ + F_CFM + """ ── N-minute wait with SL-touch abort. The
        # SPOT_SL level is SIGNAL-anchored (sig["spot"] ± tg) and the spot
        # path is fill-independent, so the scan sees exactly what the
        # position's first N monitored minutes would have seen. Spot
        # reverting through the crossed level is NOT an abort — for the
        # hedge that is the SIG_TP path (the signal contract's premium
        # collapsing), just as it is the TP path for PST_SELL.
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
        # BOTH contracts are re-selected at the shifted minute — the pair
        # must stay time-consistent (sig_entry drives the SIG_TP level).
        sel = select_pair(sig["side"], _ets)
        if sel is None:
            diag["signals_skipped_select"] += 1
            continue
        pos = simulate_position_hedge(
            legs, sig["side"], _ets,
            float(sel["held_entry"]), float(sel["sig_entry"]),
            float(sig["spot"]),
            sel["held_candles"], sel["sig_candles"], spot_1m, eod_ts,
            risk=risk)"""
    src = _ro(src, old, new, "E6 wait/abort")
    return src


# ────────────────────────────────────────────────────────────────────
def patch_runner(src):
    if F_LVL in src:
        print("  runner: fence present — skipping (idempotent)")
        return src

    # R1 — config parse + fail-closed level validation
    old = """    sma_cfg = cfg.get("sma") or {}
    st_cfg = cfg.get("supertrend") or {}"""
    new = old + """
    # ── """ + F_LVL + """ / """ + F_CFM + """ ── entry filters, all default OFF
    from app.backtest.pst.pst_sell_engine import PIVOT_RANK
    _raw_levels = [str(x).strip().upper()
                   for x in (cfg.get("allowed_levels") or []) if str(x).strip()]
    _bad_levels = sorted(set(_raw_levels) - set(PIVOT_RANK))
    if _bad_levels:
        # fail-closed: a typo silently widening/narrowing the allowlist is
        # exactly the class of silent-wrong this strategy cannot afford
        return {"run_id": None, "aborted": True,
                "reason": f"PST_HEDGE allowed_levels has unknown level(s): "
                          f"{', '.join(_bad_levels)} "
                          f"(valid: {', '.join(sorted(PIVOT_RANK))})",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}
    allowed_levels = frozenset(_raw_levels) or None   # empty = filter OFF
    skip_expiry_day = bool(cfg.get("skip_expiry_day"))
    confirm_minutes = min(30, max(0, int(cfg.get("confirm_minutes") or 0)))"""
    src = _ro(src, old, new, "R1 cfg")

    # R2 — diag keys
    old = """            "signals_skipped_risk": 0,   # ── PST_RISK_LIMITS ──
            "blocked_warmup": 0, "blocked_gate": 0, "ambiguous": 0}"""
    new = """            "signals_skipped_risk": 0,   # ── PST_RISK_LIMITS ──
            "signals_skipped_level": 0,    # ── """ + F_LVL + """ ──
            "days_skipped_expiry": 0,      # ── """ + F_LVL + """ ──
            "signals_skipped_confirm": 0,  # ── """ + F_CFM + """ ──
            "blocked_warmup": 0, "blocked_gate": 0, "ambiguous": 0}"""
    src = _ro(src, old, new, "R2 diag")

    # R3 — full-day expiry skip, after want_expiry and after the
    # prev_hlc/prev_spot rotation (so tomorrow's pivots + warmup still see
    # this session)
    old = """        want_expiry = expected_expiry_for_day(d).isoformat()
        week = [c for c in universe if c.get("expiry") == want_expiry]"""
    new = """        want_expiry = expected_expiry_for_day(d).isoformat()
        # ── """ + F_LVL + """ ── H2: full-day skip on the weekly expiry
        # date (intraday strategy — full skip is entry skip). The rotation
        # of prev_hlc/prev_spot already happened above, so tomorrow's pivots
        # and cross-day warmup still see this session.
        if skip_expiry_day and want_expiry == d.isoformat():
            diag["days_skipped_expiry"] += 1
            continue
        week = [c for c in universe if c.get("expiry") == want_expiry]"""
    src = _ro(src, old, new, "R3 expiry skip")

    # R4 — pass through
    old = """        day = run_day_hedge(sig_res["signals"], legs, select_pair, spot, eod_ts,
                            side_mode=side_mode, max_trades_per_day=max_tpd,
                            risk=risk)"""
    new = """        day = run_day_hedge(sig_res["signals"], legs, select_pair, spot, eod_ts,
                            side_mode=side_mode, max_trades_per_day=max_tpd,
                            risk=risk,
                            allowed_levels=allowed_levels,     # ── """ + F_LVL + """ ──
                            confirm_minutes=confirm_minutes)   # ── """ + F_CFM + """ ──"""
    src = _ro(src, old, new, "R4 call")

    # R5 — accumulate
    old = """        for k in ("signals_taken", "signals_skipped_busy",
                  "signals_skipped_select", "signals_skipped_side",
                  "signals_skipped_cap", "signals_skipped_risk", "ambiguous"):"""
    new = """        for k in ("signals_taken", "signals_skipped_busy",
                  "signals_skipped_select", "signals_skipped_side",
                  "signals_skipped_cap", "signals_skipped_risk",
                  "signals_skipped_level",    # ── """ + F_LVL + """ ──
                  "signals_skipped_confirm",  # ── """ + F_CFM + """ ──
                  "ambiguous"):"""
    src = _ro(src, old, new, "R5 accumulate")

    # R6 — audit line
    old = """        f"{len(trades)} leg-trades, net {summary['net_pnl']}, "
        f"warmupBlk {diag['blocked_warmup']} gateBlk {diag['blocked_gate']}")"""
    new = """        f"{len(trades)} leg-trades, net {summary['net_pnl']}, "
        f"warmupBlk {diag['blocked_warmup']} gateBlk {diag['blocked_gate']}"
        + (f", lvls={'+'.join(sorted(allowed_levels, key=PIVOT_RANK.get))}"
           f" lvlBlk {diag['signals_skipped_level']}" if allowed_levels else "")
        + (f", expDaysSkipped {diag['days_skipped_expiry']}"
           if skip_expiry_day else "")
        + (f", cfm{confirm_minutes}m cfmBlk {diag['signals_skipped_confirm']}"
           if confirm_minutes else ""))   # ── """ + F_LVL + """ / """ + F_CFM + """ ──"""
    src = _ro(src, old, new, "R6 audit")
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
        # prerequisite: the sell engine must already expose the helpers
        sell_p = os.path.join(tree, "app", "backtest", "pst", "pst_sell_engine.py")
        if os.path.isfile(sell_p) and "PST_SELL_ENTRY_FILTERS_20260828" not in open(sell_p).read():
            raise SystemExit("ABORT: pst_sell_engine.py lacks "
                             "PST_SELL_ENTRY_FILTERS_20260828 — apply the "
                             "PST_SELL filters first (this patch imports "
                             "PIVOT_RANK / nearest_crossed_level from it).")
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
    print("DONE —", F_LVL, "+", F_CFM)


if __name__ == "__main__":
    main()
