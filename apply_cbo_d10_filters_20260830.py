#!/usr/bin/env python3
# apply_cbo_d10_filters_20260830.py
#
# ── CBO_D10_FILTERS_20260830 ── three pre-registered instruments:
#
# 1) tp_fill_through_pts (D10) — the TP limit books ONLY if the bar trades
#    THROUGH the level by ε (fill price stays the limit). ε=0 keeps today's
#    touch-fill behaviour byte-identical. This bounds how much of the P&L is
#    bar-wick paper: losses already fill pessimistically everywhere, so any
#    correction from ε>0 moves results in exactly one direction.
#
# 2) vwap_filter — SCALP_V1_VWAP_20260825 semantics translated to CBO's
#    signal series: session cumulative typical-price ((H+L+C)/3) mean of the
#    SPOT, IST-day reset, fail-closed when unmeasurable. UP entries need
#    spot close >= VWAP + min_pts at the trigger bar; DOWN mirrored; invert
#    flips the verdict and nothing else (paired-run convention).
#    HISTORY, pre-registered: a VWAP entry gate is on the SCALP_V3 falsified
#    list — its gradient vanished under close-confirmation because it
#    mechanistically encodes the same information. CBO's best trigger IS
#    close-confirmation, so the expected result of this toggle on that
#    trigger is "no effect"; it exists to be falsified, not assumed.
#
# 3) ema_gate — SCALP_V1_EMA_GATE_20260824 shape on SPOT closes: EMA(period)
#    with slope measured over slope_window minutes; UP needs slope >=
#    +min_slope, DOWN needs <= -min_slope; invert flips; warmup/None BLOCKS
#    and is counted separately (gate doctrine: can't measure -> don't trade,
#    but never silently).
#
# Gate order at entry: skew -> vwap -> ema. Each block is counted at the
# FIRST gate that fires, so the ledger (signals_raw == entries + Σ blocked_*)
# still balances exactly.
#
#     python3 apply_cbo_d10_filters_20260830.py --check
#     python3 apply_cbo_d10_filters_20260830.py

from __future__ import annotations

import argparse
import py_compile
import sys
import tempfile
from pathlib import Path

FENCE = "CBO_D10_FILTERS_20260830"

RUNNERS = [Path("backend/app/backtest/cbo/backtest_cbo_runner.py"),
           Path("desktop/src-tauri/backend/app/backtest/cbo/backtest_cbo_runner.py")]

# ── A: config keys ───────────────────────────────────────────────────────
A_OLD = '''    "sl_prem_mode": "off",             # off | abs (premium ₹) | pct (of entry)'''
A_NEW = '''    "sl_prem_mode": "off",             # off | abs (premium ₹) | pct (of entry)
    # ── CBO_D10_FILTERS_20260830 ──
    "tp_fill_through_pts": 0.0,        # ε: TP books only if traded THROUGH
    "vwap_filter": {"enabled": False, "min_pts": 0.0, "invert": False},
    "ema_gate": {"enabled": False, "period": 144, "slope_window": 10,
                 "min_slope": 0.0, "invert": False},'''

# ── B: float coercion ────────────────────────────────────────────────────
B_OLD = '''    for k in ("breakout_buffer_pts", "min_ref_range_pts", "target_value",'''
B_NEW = '''    for k in ("breakout_buffer_pts", "min_ref_range_pts", "target_value",
              "tp_fill_through_pts",                 # CBO_D10_FILTERS_20260830'''

# ── C: resolve_exit — the ε ──────────────────────────────────────────────
C_OLD = '''                 sl_prem_px: Optional[float] = None) -> Optional[Tuple[str, float]]:'''
C_NEW = '''                 sl_prem_px: Optional[float] = None,
                 tp_eps: float = 0.0) -> Optional[Tuple[str, float]]:'''

D_OLD = '''    tp_hit = (opt_bar.low <= tp_px) if is_sell else (opt_bar.high >= tp_px)'''
D_NEW = '''    # ── CBO_D10_FILTERS_20260830 ── ε=0: a touch fills (today's model,
    # best case). ε>0: the bar must trade THROUGH the limit by ε — the
    # microstructure rule that a limit traded through almost certainly
    # filled, while a touch is a queue lottery. Fill price stays tp_px in
    # both cases: a limit never fills better than its price; ε changes
    # WHETHER, never AT WHAT PRICE.
    tp_hit = (opt_bar.low <= tp_px - tp_eps) if is_sell \\
        else (opt_bar.high >= tp_px + tp_eps)'''

# ── E: call site ─────────────────────────────────────────────────────────
E_OLD = '''                        sl_prem_px=pos["sl_prem_px"])'''
E_NEW = '''                        sl_prem_px=pos["sl_prem_px"],
                        tp_eps=cfg["tp_fill_through_pts"])'''

# ── F: diag keys ─────────────────────────────────────────────────────────
F_OLD = '''        "blocked_skew_unmeasurable": 0, "blocked_after_eod": 0,'''
F_NEW = '''        "blocked_skew_unmeasurable": 0, "blocked_after_eod": 0,
        # ── CBO_D10_FILTERS_20260830 ── verdict blocks vs data blocks,
        # separately, per gate — a silent day-killer must be impossible.
        "blocked_vwap": 0, "blocked_vwap_unmeasurable": 0,
        "blocked_ema": 0, "blocked_ema_unmeasurable": 0,'''

# ── G: per-day spot indicators ───────────────────────────────────────────
G_OLD = '''        spot = spot_bars_for(ds)'''
G_NEW = '''        spot = spot_bars_for(ds)
        # ── CBO_D10_FILTERS_20260830 ── per-day SPOT indicator series,
        # computed once, keyed by bar ts (the value KNOWN at that bar's
        # close — a gate at trigger_ts reads its own bar, never a later
        # one). VWAP = SCALP_V1's session cumulative typical-price mean
        # ((H+L+C)/3, equal weight, day-reset). EMA = standard
        # alpha 2/(n+1) on closes; slope over slope_window bars; None
        # until warm (fail-closed at the gate, counted).
        vwap_at: Dict[int, float] = {}
        ema_slope_at: Dict[int, Optional[float]] = {}
        if cfg["vwap_filter"].get("enabled") or cfg["ema_gate"].get("enabled"):
            _pv = _n = 0.0
            _per = max(2, int(cfg["ema_gate"].get("period", 144) or 144))
            _win = max(1, int(cfg["ema_gate"].get("slope_window", 10) or 10))
            _al = 2.0 / (_per + 1.0)
            _ema = None
            _cnt = 0
            _hist: List[float] = []
            for _b in spot:
                if (_b.ts - ds) // 60 < GRID_ANCHOR_MIN:
                    continue                     # pre-open prints: no session
                _pv += (_b.high + _b.low + _b.close) / 3.0
                _n += 1.0
                vwap_at[_b.ts] = _pv / _n
                _ema = _b.close if _ema is None else \\
                    _al * _b.close + (1.0 - _al) * _ema
                _cnt += 1
                _hist.append(_ema)
                if _cnt >= _per + _win:
                    ema_slope_at[_b.ts] = _ema - _hist[-1 - _win]
                else:
                    ema_slope_at[_b.ts] = None   # warming: unmeasurable
        spot_close_at = {b.ts: b.close for b in spot}'''

# ── H: the gates, after skew ─────────────────────────────────────────────
H_OLD = '''                    if not ok:
                        diag["blocked_skew"] += 1
                        continue'''
H_NEW = '''                    if not ok:
                        diag["blocked_skew"] += 1
                        continue

                # ── CBO_D10_FILTERS_20260830 ── VWAP filter (SCALP_V1
                # semantics on the SPOT): UP needs close >= VWAP + min_pts
                # at the trigger bar, DOWN mirrored; invert flips the
                # verdict only. Unmeasurable BLOCKS, counted separately.
                vf = cfg["vwap_filter"]
                if vf.get("enabled"):
                    _vw = vwap_at.get(s.trigger_ts)
                    _cl = spot_close_at.get(s.trigger_ts)
                    if _vw is None or _cl is None:
                        diag["blocked_vwap_unmeasurable"] += 1
                        continue
                    try:
                        _vmin = float(vf.get("min_pts", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        _vmin = 0.0
                    _vok = (_cl - _vw >= _vmin) if s.direction == UP \\
                        else (_vw - _cl >= _vmin)
                    if bool(vf.get("invert", False)):
                        _vok = not _vok
                    if not _vok:
                        diag["blocked_vwap"] += 1
                        continue

                # ── CBO_D10_FILTERS_20260830 ── EMA slope gate (SCALP_V1
                # shape on SPOT closes): UP needs slope >= +min_slope, DOWN
                # <= -min_slope; invert flips; warmup/None BLOCKS, counted.
                eg = cfg["ema_gate"]
                if eg.get("enabled"):
                    _sl = ema_slope_at.get(s.trigger_ts)
                    if _sl is None:
                        diag["blocked_ema_unmeasurable"] += 1
                        continue
                    try:
                        _emin = float(eg.get("min_slope", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        _emin = 0.0
                    _eok = (_sl >= _emin) if s.direction == UP \\
                        else (_sl <= -_emin)
                    if bool(eg.get("invert", False)):
                        _eok = not _eok
                    if not _eok:
                        diag["blocked_ema"] += 1
                        continue'''


class Abort(Exception):
    pass


def replace_once(text, old, new, what):
    n = text.count(old)
    if n != 1:
        raise Abort(f"{what}: anchor found {n}x, expected 1 — drifted; "
                    f"nothing written.")
    return text.replace(old, new, 1)


def stage(path, edits):
    if not path.exists():
        print(f"  SKIPPED (absent)        {path}")
        return None
    text = path.read_text()
    if FENCE in text:
        print(f"  already fenced — skipped   {path}")
        return None
    for old, new, what in edits:
        text = replace_once(text, old, new, f"{path}:{what}")
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(text)
        tmp = fh.name
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        raise Abort(f"{path}: staged compile failed — {e}")
    finally:
        Path(tmp).unlink(missing_ok=True)
    return (path, text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    edits = [(A_OLD, A_NEW, "A config"), (B_OLD, B_NEW, "B coercion"),
             (C_OLD, C_NEW, "C signature"), (D_OLD, D_NEW, "D tp_hit"),
             (E_OLD, E_NEW, "E call site"), (F_OLD, F_NEW, "F diag"),
             (G_OLD, G_NEW, "G indicators"), (H_OLD, H_NEW, "H gates")]
    staged = []
    try:
        for p in RUNNERS:
            staged.append(stage(p, edits))
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
