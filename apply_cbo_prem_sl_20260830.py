#!/usr/bin/env python3
# apply_cbo_prem_sl_20260830.py
#
# ── CBO_PREM_SL_20260830 ── wire a PREMIUM stop-loss alongside the
# spot-reference stop (D9, Anbu 2026-08-29). Also retires `sl_premium_pct`,
# a config key that shipped in DEFAULTS on day one but was never read by
# resolve_exit — a dead knob, now replaced by a wired one.
#
# SEMANTICS (D9a-d, locked):
#   sl_prem_mode  off | abs | pct   (pct = % of ENTRY premium; for a short,
#                                    of the premium COLLECTED — same D3b
#                                    convention as the target)
#   sl_prem_value the points / percentage
#   * additive, tighter-wins: the trade exits on whichever of premium-SL /
#     spot-SL / TP triggers first in a minute
#   * tie-breaks, PESSIMISTIC: any SL beats TP (D3). Premium SL fills AT its
#     level (stop-trigger convention, mirror of TP-at-limit); spot SL stays
#     a market fill at the option close; BOTH SLs in one minute -> the WORSE
#     fill is taken.
#   * attribution: exit reasons SL_SPOT / SL_PREM with separate counters and
#     P&L shares. If the new stop carries the result, the decomposition must
#     say so — falsification-counters doctrine.
#
# WHY THIS EXISTS: the 2022-vs-2024 audit showed the breakeven win rate
# ratcheting from 56.8% to 63.9% because the spot-range stop scales with the
# index while the ₹10 target does not. A premium SL bounds the loss in the
# same currency as the target. PRE-REGISTERED CAUTION: a tighter effective
# stop also converts some would-be winners into losses; WR is EXPECTED to
# drop. Whether margin improves is the question the run decides — this
# patch takes no position.
#
#     python3 apply_cbo_prem_sl_20260830.py --check
#     python3 apply_cbo_prem_sl_20260830.py

from __future__ import annotations

import argparse
import py_compile
import sys
import tempfile
from pathlib import Path

FENCE = "CBO_PREM_SL_20260830"

TARGETS = [
    Path("backend/app/backtest/cbo/backtest_cbo_runner.py"),
    Path("desktop/src-tauri/backend/app/backtest/cbo/backtest_cbo_runner.py"),
]

# ── A: config keys ───────────────────────────────────────────────────────
A_OLD = '''    "sl_premium_pct": 0.0,             # 0 = spot-reference stop only'''
A_NEW = '''    # ── CBO_PREM_SL_20260830 ── premium stop, ADDITIVE to the spot stop
    # (tighter-wins). Replaces sl_premium_pct, which shipped unread.
    "sl_prem_mode": "off",             # off | abs (premium ₹) | pct (of entry)
    "sl_prem_value": 0.0,'''

# ── B: normalisation ─────────────────────────────────────────────────────
B_OLD = '''              "sl_premium_pct", "mtm_loss_cap", "mtm_profit_cap"):'''
B_NEW = '''              "sl_prem_value", "mtm_loss_cap", "mtm_profit_cap"):'''

B2_OLD = '''    cfg["target_mode"] = "pct" if str(cfg["target_mode"]).lower() == "pct" else "abs"'''
B2_NEW = '''    cfg["target_mode"] = "pct" if str(cfg["target_mode"]).lower() == "pct" else "abs"
    # ── CBO_PREM_SL_20260830 ── store the lowered value (the "SKIP" lesson)
    _slm = str(cfg.get("sl_prem_mode", "off")).lower()
    cfg["sl_prem_mode"] = _slm if _slm in ("off", "abs", "pct") else "off"'''

# ── C: sl price helper next to target_price ──────────────────────────────
C_OLD = '''def mtm_of_open(pos: dict, mark: Optional[float]) -> float:'''
C_NEW = '''def sl_prem_price(entry_px: float, *, is_sell: bool, mode: str,
                  value: float) -> Optional[float]:
    """── CBO_PREM_SL_20260830 ── the premium level at which the stop
    triggers, or None when the stop is off. Mirrors target_price exactly:
    'pct' is % of ENTRY premium (of the premium COLLECTED on a short, D3b).
    A long stops BELOW entry; a short stops ABOVE (its loss direction).
    Floored at 0.05 on the long side — a stop at a negative premium can
    never trigger and would silently disable itself."""
    if mode == "off" or value <= 0:
        return None
    delta = (entry_px * value / 100.0) if mode == "pct" else value
    return (entry_px + delta) if is_sell else max(0.05, entry_px - delta)


def mtm_of_open(pos: dict, mark: Optional[float]) -> float:'''

# ── D: resolve_exit gains the premium stop ───────────────────────────────
D_OLD = '''    sl_hit = (spot_bar.low <= spot_stop) if direction == UP \\
        else (spot_bar.high >= spot_stop)
    tp_hit = (opt_bar.low <= tp_px) if is_sell else (opt_bar.high >= tp_px)
    if sl_hit:
        return "SL", float(opt_bar.close)
    if tp_hit:
        return "TP", float(tp_px)
    return None'''
D_NEW = '''    sl_hit = (spot_bar.low <= spot_stop) if direction == UP \\
        else (spot_bar.high >= spot_stop)
    tp_hit = (opt_bar.low <= tp_px) if is_sell else (opt_bar.high >= tp_px)
    # ── CBO_PREM_SL_20260830 ── premium stop: triggers when the option
    # trades at-or-through its level; fills AT the level (stop-trigger
    # convention, the mirror of TP-at-limit). Additive to the spot stop.
    prem_hit = False
    if sl_prem_px is not None:
        prem_hit = (opt_bar.high >= sl_prem_px) if is_sell \\
            else (opt_bar.low <= sl_prem_px)
    if sl_hit and prem_hit:
        # both stops in one minute -> the WORSE fill (pessimistic). For a
        # long, worse = lower; for a short, worse = higher.
        spot_fill = float(opt_bar.close)
        worse = max(spot_fill, float(sl_prem_px)) if is_sell \\
            else min(spot_fill, float(sl_prem_px))
        return ("SL_SPOT" if worse == spot_fill else "SL_PREM"), worse
    if sl_hit:
        return "SL_SPOT", float(opt_bar.close)
    if prem_hit:
        return "SL_PREM", float(sl_prem_px)
    if tp_hit:
        return "TP", float(tp_px)
    return None'''

D2_OLD = '''def resolve_exit(*, is_sell: bool, entry_px: float, tp_px: float,
                 spot_stop: float, direction: str,
                 opt_bar, spot_bar) -> Optional[Tuple[str, float]]:'''
D2_NEW = '''def resolve_exit(*, is_sell: bool, entry_px: float, tp_px: float,
                 spot_stop: float, direction: str,
                 opt_bar, spot_bar,
                 sl_prem_px: Optional[float] = None) -> Optional[Tuple[str, float]]:'''

# ── E: runner call site + position state ─────────────────────────────────
E_OLD = '''                    ex = resolve_exit(
                        is_sell=pos["is_sell"], entry_px=pos["entry_px"],
                        tp_px=pos["tp_px"], spot_stop=pos["spot_stop"],
                        direction=pos["dir"], opt_bar=ob, spot_bar=bar)'''
E_NEW = '''                    ex = resolve_exit(
                        is_sell=pos["is_sell"], entry_px=pos["entry_px"],
                        tp_px=pos["tp_px"], spot_stop=pos["spot_stop"],
                        direction=pos["dir"], opt_bar=ob, spot_bar=bar,
                        sl_prem_px=pos["sl_prem_px"])'''

F_OLD = '''                pos = {"symbol": pick["tradingsymbol"], "trade": t,
                       "entry_px": entry_px, "tp_px": tp_px,
                       "spot_stop": s.stop_level, "dir": s.direction,'''
F_NEW = '''                pos = {"symbol": pick["tradingsymbol"], "trade": t,
                       "entry_px": entry_px, "tp_px": tp_px,
                       "sl_prem_px": _slp,
                       "spot_stop": s.stop_level, "dir": s.direction,'''

G_OLD = '''                entry_px = float(fb.open)
                tp_px = target_price(entry_px, is_sell=is_sell,
                                     mode=cfg["target_mode"],
                                     value=cfg["target_value"])'''
G_NEW = '''                entry_px = float(fb.open)
                tp_px = target_price(entry_px, is_sell=is_sell,
                                     mode=cfg["target_mode"],
                                     value=cfg["target_value"])
                # ── CBO_PREM_SL_20260830 ── computed per trade from the
                # actual fill, same as the target.
                _slp = sl_prem_price(entry_px, is_sell=is_sell,
                                     mode=cfg["sl_prem_mode"],
                                     value=cfg["sl_prem_value"])'''

# ── H: bookkeeping — reasons, counters, shares ───────────────────────────
H_OLD = '''            key = {"SL": "sl_pnl_gross", "TP": "tp_pnl_gross",
                   "EOD": "eod_pnl_gross", "MTM_CAP": "mtm_cap_pnl_gross",
                   "AMBIGUOUS": "ambiguous_pnl_gross"}.get(reason)
            if key:
                diag[key] += round(net, 2)
            diag[{"SL": "sl_exits", "TP": "tp_exits", "EOD": "eod_exits",
                  "MTM_CAP": "mtm_cap_exits",
                  "AMBIGUOUS": "ambiguous_exits"}.get(reason, "eod_exits")] += 1'''
H_NEW = '''            # ── CBO_PREM_SL_20260830 ── SL_SPOT / SL_PREM are attributed
            # separately (and also into the legacy sl_* aggregates so every
            # existing report keeps working).
            key = {"SL_SPOT": "sl_spot_pnl_gross", "SL_PREM": "sl_prem_pnl_gross",
                   "TP": "tp_pnl_gross",
                   "EOD": "eod_pnl_gross", "MTM_CAP": "mtm_cap_pnl_gross",
                   "AMBIGUOUS": "ambiguous_pnl_gross"}.get(reason)
            if key:
                diag[key] += round(net, 2)
            if reason in ("SL_SPOT", "SL_PREM"):
                diag["sl_pnl_gross"] += round(net, 2)
                diag["sl_exits"] += 1
            diag[{"SL_SPOT": "sl_spot_exits", "SL_PREM": "sl_prem_exits",
                  "TP": "tp_exits", "EOD": "eod_exits",
                  "MTM_CAP": "mtm_cap_exits",
                  "AMBIGUOUS": "ambiguous_exits"}.get(reason, "eod_exits")] += 1'''

I_OLD = '''        "sl_exits": 0, "tp_exits": 0, "eod_exits": 0,'''
I_NEW = '''        "sl_exits": 0, "tp_exits": 0, "eod_exits": 0,
        "sl_spot_exits": 0, "sl_prem_exits": 0,           # CBO_PREM_SL_20260830
        "sl_spot_pnl_gross": 0.0, "sl_prem_pnl_gross": 0.0,'''

J_OLD = '''        for k in ("ambiguous", "eod", "mtm_cap", "tp", "sl"):'''
J_NEW = '''        for k in ("ambiguous", "eod", "mtm_cap", "tp", "sl",
                  "sl_spot", "sl_prem"):                  # CBO_PREM_SL_20260830'''

# ── K: the D8 forced stop-out keeps its own reason ───────────────────────
K_OLD = '''    exit_reason: Optional[str]         # SL | TP | EOD | MTM_CAP | AMBIGUOUS | SESSION'''
K_NEW = '''    exit_reason: Optional[str]         # SL_SPOT | SL_PREM | TP | EOD | MTM_CAP | AMBIGUOUS'''

EDITS = [(A_OLD, A_NEW, "A config keys"), (B_OLD, B_NEW, "B float coercion"),
         (B2_OLD, B2_NEW, "B2 mode normalisation"), (C_OLD, C_NEW, "C helper"),
         (D2_OLD, D2_NEW, "D2 signature"), (D_OLD, D_NEW, "D exit ladder"),
         (E_OLD, E_NEW, "E call site"), (G_OLD, G_NEW, "G per-trade level"),
         (F_OLD, F_NEW, "F position state"), (H_OLD, H_NEW, "H bookkeeping"),
         (I_OLD, I_NEW, "I diag keys"), (J_OLD, J_NEW, "J shares"),
         (K_OLD, K_NEW, "K reason doc")]


class Abort(Exception):
    pass


def replace_once(text, old, new, what):
    n = text.count(old)
    if n != 1:
        raise Abort(f"{what}: anchor found {n}x, expected 1 — drifted; "
                    f"nothing written.")
    return text.replace(old, new, 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    present = [t for t in TARGETS if t.exists()]
    if not present:
        print("ABORTED: runner not found — run from the repo root.",
              file=sys.stderr)
        return 1
    for t in TARGETS:
        if not t.exists():
            print(f"  SKIPPED (tree absent)   {t}")
            continue
        text = t.read_text()
        if FENCE in text:
            print(f"  already fenced — skipped   {t}")
            continue
        try:
            for old, new, what in EDITS:
                text = replace_once(text, old, new, f"{t}:{what}")
        except Abort as e:
            print(f"\nABORTED: {e}", file=sys.stderr)
            return 1
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write(text)
            tmp = fh.name
        try:
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as e:
            print(f"ABORTED: staged compile failed for {t}: {e}", file=sys.stderr)
            return 1
        finally:
            Path(tmp).unlink(missing_ok=True)
        if args.check:
            print(f"  would patch (clean)     {t}")
        else:
            t.write_text(text)
            print(f"  patched                 {t}")
    print(f"\n{FENCE} {'check complete' if args.check else 'applied'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
