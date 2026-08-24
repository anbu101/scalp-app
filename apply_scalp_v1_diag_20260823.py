#!/usr/bin/env python3
# apply_scalp_v1_diag_20260823.py
#
# D6 — SCALP_V1 trade diagnostics — fence: SCALP_V1_DIAG_20260823
# Backtest-only instrumentation. ZERO behavior change: no entry/exit decision
# reads any of this; it only records evidence for the entry/exit redesign.
#
# WHAT IT CAPTURES per trade:
#  * condition column (DB + both CSVs): compact JSON snapshot at ENTRY —
#      b   candle body (close-open)          r    candle range (high-low)
#      e8  close - EMA8                      e20  close - EMA20_low
#      sp  EMA8 - EMA20_low (band spread)    e20h EMA20_high - close (headroom)
#      rk  sl - entry (risk distance, pts)
#    The DB column, repo CSV export, and the frontend "Backtest Export" CSV
#    Condition column ALL already exist and already plumb this field — it was
#    simply never populated for SCALP_V1. One capture point lights them all up.
#  * MAE / MFE / DurMin columns added to the frontend Backtest Export CSV
#    (already tracked by VirtualBook + stored in DB; just not exported there).
#
# FILES:
#  backend/app/backtest/sim/virtual_book.py     condition field carried through
#  backend/app/backtest/runner/backtest_runner.py  snapshot built at entry
#  frontend/src/pages/Backtest.jsx              MAE/MFE/DurMin export columns
#  (+ desktop rsync tree if present locally)
#
# PREREQUISITE: apply_scalp_v1_bt_filters_20260823.py already applied — the
# runner anchors here match the POST-filter file state on purpose.
#
# Idempotent: aborts if fence present. Run from repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_DIAG_20260823"
PREREQ_FENCE = "SCALP_V1_BT_FILTERS_20260823"
ROOT = Path(__file__).resolve().parent

VB_REL = "app/backtest/sim/virtual_book.py"
RN_REL = "app/backtest/runner/backtest_runner.py"
BT_JSX = ROOT / "frontend" / "src" / "pages" / "Backtest.jsx"

TREES = [ROOT / "backend"]
_desktop = ROOT / "desktop" / "src-tauri" / "backend"
if (_desktop / VB_REL).exists():
    TREES.append(_desktop)


def _die(msg):
    print(f"ABORT: {msg}")
    sys.exit(1)


def _replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        _die(f"anchor '{label}' matched {n} times (want 1) — NOTHING written")
    return text.replace(old, new, 1)


# ═══ virtual_book.py ═══════════════════════════════════════════════════════

V1_OLD = """    # running extremes for analytics (premium vs entry)
    max_adverse: float = 0.0      # worst (premium rose most) — bad for short
    max_favorable: float = 0.0    # best  (premium fell most) — good for short"""
V1_NEW = '''    # running extremes for analytics (premium vs entry)
    max_adverse: float = 0.0      # worst (premium rose most) — bad for short
    max_favorable: float = 0.0    # best  (premium fell most) — good for short
    # ── SCALP_V1_DIAG_20260823 ── entry snapshot JSON (diagnostics only;
    # NEVER read by any entry/exit decision). Flows to ClosedTrade.condition
    # -> backtest_trades.condition -> both CSV exports.
    condition: str = ""'''

V2_OLD = """    charges: float = 0.0      # round-trip charges (live zerodha_charges)
    net_pnl: float = 0.0      # pnl - charges"""
V2_NEW = """    charges: float = 0.0      # round-trip charges (live zerodha_charges)
    net_pnl: float = 0.0      # pnl - charges
    condition: str = ""       # ── SCALP_V1_DIAG_20260823 ── entry snapshot JSON"""

V3_OLD = """            max_adverse=pos.max_adverse, max_favorable=pos.max_favorable,
            charges=charges, net_pnl=net_pnl,
        )"""
V3_NEW = """            max_adverse=pos.max_adverse, max_favorable=pos.max_favorable,
            charges=charges, net_pnl=net_pnl,
            condition=pos.condition,   # ── SCALP_V1_DIAG_20260823 ──
        )"""

# ═══ backtest_runner.py (anchors match POST-SCALP_V1_BT_FILTERS state) ═════

R1_OLD = "import uuid\nimport time"
R1_NEW = "import json   # ── SCALP_V1_DIAG_20260823 ── entry snapshot serializer\nimport uuid\nimport time"

R2_OLD = "                entry_candidates.append((signal.entry_price, sym, ctx, c, signal))"
R2_NEW = """                # ── SCALP_V1_DIAG_20260823 BEGIN ── entry snapshot. Built at
                # CANDIDATE time because ind_vals/conds are per-symbol loop
                # locals: by election time they'd hold the LAST iterated
                # symbol's values, not the winner's. Diagnostics only —
                # nothing downstream reads this for any trading decision.
                _e8 = ind_vals.get("ema8")
                _e20l = ind_vals.get("ema20_low")
                _e20h = ind_vals.get("ema20_high")
                _r2 = lambda v: round(v, 2)
                diag = json.dumps({
                    "b": _r2(c.close - c.open),
                    "r": _r2(c.high - c.low),
                    "e8": _r2(c.close - _e8) if _e8 is not None else None,
                    "e20": _r2(c.close - _e20l) if _e20l is not None else None,
                    "sp": _r2(_e8 - _e20l) if (_e8 is not None and _e20l is not None) else None,
                    "e20h": _r2(_e20h - c.close) if _e20h is not None else None,
                    "rk": _r2(signal.sl - signal.entry_price),
                }, separators=(",", ":"))
                entry_candidates.append((signal.entry_price, sym, ctx, c, signal, diag))
                # ── SCALP_V1_DIAG_20260823 END ──"""

R3_OLD = "                ep, sym, ctx, c, signal = entry_candidates[0]"
R3_NEW = "                ep, sym, ctx, c, signal, diag = entry_candidates[0]   # ── SCALP_V1_DIAG_20260823 ──"

R4_OLD = """                    sl=signal.sl, tp=signal.tp, qty=qty))
                day_entries += 1   # SCALP_V1_BT_FILTERS_20260823 (D2)"""
R4_NEW = """                    sl=signal.sl, tp=signal.tp, qty=qty,
                    condition=diag))   # ── SCALP_V1_DIAG_20260823 ──
                day_entries += 1   # SCALP_V1_BT_FILTERS_20260823 (D2)"""

# ═══ Backtest.jsx export columns ═══════════════════════════════════════════

J1_OLD = '  lines.push(["Symbol", "Condition", "Entry Time", "Entry", "SL", "TP", "Exit Time", "Exit", "Reason", "Gross", "Charges", "Net", "Ambiguous"].join(","));'
J1_NEW = '''  // ── SCALP_V1_DIAG_20260823 ── MAE/MFE/DurMin appended (already in the DB
  // rows the results endpoint returns; they were just never exported here).
  lines.push(["Symbol", "Condition", "Entry Time", "Entry", "SL", "TP", "Exit Time", "Exit", "Reason", "Gross", "Charges", "Net", "Ambiguous", "MAE", "MFE", "DurMin"].join(","));'''

J2_OLD = """      t.ambiguous_fill ? "YES" : "",
    ].join(","));"""
J2_NEW = """      t.ambiguous_fill ? "YES" : "",
      t.max_adverse != null ? t.max_adverse.toFixed(2) : "",   // ── SCALP_V1_DIAG_20260823 ──
      t.max_favorable != null ? t.max_favorable.toFixed(2) : "",
      (t.exit_ts != null && t.entry_ts != null) ? Math.round((t.exit_ts - t.entry_ts) / 60) : "",
    ].join(","));"""


def main():
    if not (ROOT / "backend" / RN_REL).exists():
        _die("run from the scalp-app repo root")

    staged = []  # (path, new_text)

    for tree in TREES:
        vb_p, rn_p = tree / VB_REL, tree / RN_REL
        vb, rn = vb_p.read_text(), rn_p.read_text()
        if FENCE in vb or FENCE in rn:
            _die(f"fence {FENCE} already present under {tree} — already applied")
        if PREREQ_FENCE not in rn:
            _die(f"prerequisite fence {PREREQ_FENCE} MISSING in {rn_p} — apply "
                 f"apply_scalp_v1_bt_filters_20260823.py first")
        vb = _replace_once(vb, V1_OLD, V1_NEW, f"{tree.name}/vb:V1")
        vb = _replace_once(vb, V2_OLD, V2_NEW, f"{tree.name}/vb:V2")
        vb = _replace_once(vb, V3_OLD, V3_NEW, f"{tree.name}/vb:V3")
        rn = _replace_once(rn, R1_OLD, R1_NEW, f"{tree.name}/rn:R1")
        rn = _replace_once(rn, R2_OLD, R2_NEW, f"{tree.name}/rn:R2")
        rn = _replace_once(rn, R3_OLD, R3_NEW, f"{tree.name}/rn:R3")
        rn = _replace_once(rn, R4_OLD, R4_NEW, f"{tree.name}/rn:R4")
        staged.append((vb_p, vb))
        staged.append((rn_p, rn))

    jsx = BT_JSX.read_text()
    if FENCE in jsx:
        _die(f"fence {FENCE} already present in {BT_JSX.name} — already applied")
    jsx = _replace_once(jsx, J1_OLD, J1_NEW, "jsx:J1")
    jsx = _replace_once(jsx, J2_OLD, J2_NEW, "jsx:J2")
    staged.append((BT_JSX, jsx))

    # All anchors verified AND all staged Python compiled BEFORE writing ANY —
    # a syntax error in the patch itself must never reach disk.
    for path, text in staged:
        if path.suffix == ".py":
            try:
                compile(text, str(path), "exec")
            except SyntaxError as e:
                _die(f"staged content for {path} does not compile: {e}")
    for path, text in staged:
        path.write_text(text)
        print(f"PATCHED: {path}")
    for path, text in staged:
        if path.suffix == ".py":
            print(f"py_compile OK: {path}")

    print()
    print(f"DONE — fence {FENCE} applied.")
    print()
    print("NOTES:")
    print(" * Diagnostics only — no gate, SL, TP, or fill logic reads any of it.")
    print("   A re-run produces the SAME trades as before this patch; only the")
    print("   condition column and the three new export columns gain content.")
    print(" * Condition JSON keys: b=body r=range e8=close-EMA8 e20=close-EMA20low")
    print("   sp=EMA8-EMA20low e20h=EMA20high-close rk=risk pts. All at entry candle.")
    print(" * Rebuild frontend (or dev-reload) to pick up the new export columns.")
    print(" * Old runs re-exported will show blank Condition/MAE/MFE for rows")
    print("   recorded before this patch — expected; re-run to populate.")
    print(" * Next: run ONE full-range baseline (filters OFF) and upload the")
    print("   export — the entry/exit redesign gets designed from that file.")


if __name__ == "__main__":
    main()
