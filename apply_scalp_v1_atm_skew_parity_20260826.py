#!/usr/bin/env python3
# apply_scalp_v1_atm_skew_parity_20260826.py
#
# D15c — PARITY-ADJUSTED ATM SKEW — fence: SCALP_V1_ATM_SKEW_PARITY_20260826
# (backtest-only; extends SCALP_V1_ATM_SKEW_FLIP_20260826)
#
# THE ARITHMETIC (no fitting anywhere in it). Put-call parity is an identity:
# for one strike/expiry, CE - PE = F - K. The diagnostics already record
#     sk = ATM PE - ATM CE  =  K - F
#     sd = spot - ATM strike =  S - K
# so                sk + sd  =  S - F  =  -carry
# EXACTLY, for every trade, if parity holds. Therefore
#     residual = sk + sd + carry
# is the part of the price difference that parity CANNOT explain — genuine
# relative richness of one leg — centred at ~0, with the strike-grid geometry
# removed. Raw sk is dominated by that geometry (measured slope of sk on -sd:
# 1.001), which is why it reads the strike grid rather than the market.
#
# WHY IT MATTERS (measured on the filter-OFF control, 8,277 trades):
#   raw sk,   dearer rule: kept +Rs 639/trade vs rejected +Rs 161 — 6/7 years
#   residual, dearer rule: kept +Rs 1,197    vs rejected -Rs 430  — 7/7 years
# The residual fixes 2023, the single year the raw rule inverts (raw: 23 vs
# 736 against; residual: 485 vs 254 for). It is also NOT a days-to-expiry
# effect in disguise: corr(residual, DTE) = -0.357, but the edge survives
# INSIDE every DTE bucket (+Rs 1,195 to +Rs 2,512/trade at DTE 0-4).
#
#   "atm_skew_filter": {
#       "enabled": false, "min_diff_pts": 0, "invert": false,
#       "parity_adjust": false,      <- NEW: use the residual, not raw sk
#       "carry_pts": 6.5             <- NEW: the forward carry constant
#   }
#
# ON carry_pts: it is MEASURED, not fitted — carry = -(sk + sd) on every
# trade, and the mean across the corpus is 6.57. It is also economically
# pinned (~ spot x rate x days-to-expiry), which is why it drifts through the
# expiry week. Sweep {4, 6.5, 9}: if all three behave alike the constant is
# robust and the effect is real; if only 6.5 works it is a fit and should be
# thrown out. A DTE-scaled carry is the obvious refinement — AFTER the fixed
# constant proves itself, not before.
#
# Everything else is untouched: fail-closed on an unmeasurable ATM pair,
# per-candidate evaluation, the invert flip, and the sk/sd diagnostics.
#
# PREREQ: SCALP_V1_ATM_SKEW_FLIP_20260826. Idempotent. Run from the repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_ATM_SKEW_PARITY_20260826"
PREREQ = "SCALP_V1_ATM_SKEW_FLIP_20260826"
ROOT = Path(__file__).resolve().parent
RN_REL = "app/backtest/runner/backtest_runner.py"
LD_REL = "app/config/strategy_loader.py"
SRC = ROOT / "frontend" / "src"
BT_JSX = SRC / "pages" / "Backtest.jsx"
QU_JSX = SRC / "pages" / "backtest" / "BacktestQueue.jsx"
RC_JSX = SRC / "pages" / "backtest" / "RunComparison.jsx"
SW_JSX = SRC / "pages" / "backtest" / "SweepBuilder.jsx"
TREES = [ROOT / "backend"]
_d = ROOT / "desktop" / "src-tauri" / "backend"
if (_d / RN_REL).exists():
    TREES.append(_d)


def _die(m):
    print(f"ABORT: {m}")
    sys.exit(1)


def _ro(t, o, n, lab):
    c = t.count(o)
    if c != 1:
        _die(f"anchor '{lab}' matched {c} times (want 1) — NOTHING written")
    return t.replace(o, n, 1)


# ── R1: config ────────────────────────────────────────────────────────────
R1_OLD = '    skew_invert = bool(_sk.get("invert", False))'
R1_NEW = '''    skew_invert = bool(_sk.get("invert", False))
    # ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── use the parity residual
    # (sk + sd + carry) instead of raw sk. carry_pts is MEASURED, not fitted:
    # carry == -(sk + sd) on every trade; the corpus mean is 6.57.
    skew_parity = bool(_sk.get("parity_adjust", False))
    try:
        _cp = _sk.get("carry_pts", 6.5)
        skew_carry = float(6.5 if _cp is None else _cp)
    except (TypeError, ValueError):
        skew_carry = 6.5'''

# ── R2: the gate ──────────────────────────────────────────────────────────
R2_OLD = '                    _diff = _skv[0] if sym.endswith("CE") else -_skv[0]'
R2_NEW = '''                    # ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── parity-adjusted
                    # value: sk + sd == -carry EXACTLY under put-call parity,
                    # so (sk + sd + carry) is the residual richness with the
                    # strike-grid geometry removed, centred at ~0. OFF keeps
                    # raw sk, so a paired run differs ONLY in this choice.
                    _sv = ((_skv[0] + _skv[1] + skew_carry)
                           if skew_parity else _skv[0])
                    _diff = _sv if sym.endswith("CE") else -_sv'''

# ── loader ────────────────────────────────────────────────────────────────
L1_OLD = '''            # ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── False = sell the cheaper
            # side (as originally specified, falsified on the full corpus);
            # True = sell the dearer side (the inverted hypothesis).
            "invert":       False
        },'''
L1_NEW = '''            # ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── False = sell the cheaper
            # side (as originally specified, falsified on the full corpus);
            # True = sell the dearer side (the inverted hypothesis).
            "invert":       False,
            # ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── compare the parity
            # RESIDUAL (sk + sd + carry) rather than raw sk, so the rule reads
            # genuine richness instead of where spot sits in the strike grid.
            "parity_adjust": False,
            "carry_pts":     6.5
        },'''

# ── UI ────────────────────────────────────────────────────────────────────
J1_OLD = "  const [v1AtmSkewInvert, setV1AtmSkewInvert] = useState(saved.v1AtmSkewInvert ?? false);"
J1_NEW = """  const [v1AtmSkewInvert, setV1AtmSkewInvert] = useState(saved.v1AtmSkewInvert ?? false);
  // ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── residual vs raw sk, + the carry.
  const [v1AtmSkewParity, setV1AtmSkewParity] = useState(saved.v1AtmSkewParity ?? false);
  const [v1AtmSkewCarry, setV1AtmSkewCarry] = useState(saved.v1AtmSkewCarry ?? 6.5);"""

J2_OLD = "      v1AtmSkewInvert });   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ──"
J2_NEW = "      v1AtmSkewInvert,   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ──\n      v1AtmSkewParity, v1AtmSkewCarry });   // ── SCALP_V1_ATM_SKEW_PARITY_20260826 ──"

J3_OLD = "      v1AtmSkewInvert]);   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── stale-closure rule: saveParams reads it, so it lands here in the SAME commit"
J3_NEW = "      v1AtmSkewInvert,   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ──\n      v1AtmSkewParity, v1AtmSkewCarry]);   // ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── stale-closure rule: saveParams reads them, so they land here in the SAME commit"

J4_OLD = "      if (v1AtmSkew) cfg.atm_skew_filter = { enabled: true, min_diff_pts: Number(v1AtmSkewMin) || 0, invert: !!v1AtmSkewInvert };   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── direction rides along"
J4_NEW = "      if (v1AtmSkew) cfg.atm_skew_filter = { enabled: true, min_diff_pts: Number(v1AtmSkewMin) || 0, invert: !!v1AtmSkewInvert, parity_adjust: !!v1AtmSkewParity, carry_pts: Number(v1AtmSkewCarry) };   // ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── parity choice rides along"

J5_OLD = "      v1AtmSkewInvert]);   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── stale-closure rule: buildConfig reads it, so it lands here in the SAME commit"
J5_NEW = "      v1AtmSkewInvert,   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ──\n      v1AtmSkewParity, v1AtmSkewCarry]);   // ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"

J6_OLD = '''              {v1AtmSkew && (
                <Field label="Min skew pts"><input type="number" min="0" step="0.5" style={inputStyle} value={v1AtmSkewMin} onChange={(e) => setV1AtmSkewMin(e.target.value)} /></Field>
              )}'''
J6_NEW = '''              {v1AtmSkew && (
                <Field label="Min skew pts"><input type="number" min="0" step="0.5" style={inputStyle} value={v1AtmSkewMin} onChange={(e) => setV1AtmSkewMin(e.target.value)} /></Field>
              )}
              {/* ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── "Raw" compares the two
                  ATM prices directly (mostly strike-grid geometry); "Parity
                  adj" compares sk + sd + carry, the part parity cannot
                  explain. Carry is measured, not fitted (corpus mean 6.57). */}
              {v1AtmSkew && (
                <Field label="Skew basis">
                  <select style={inputStyle} value={v1AtmSkewParity ? "1" : "0"} onChange={(e) => setV1AtmSkewParity(e.target.value === "1")}>
                    <option value="0">Raw (PE − CE)</option>
                    <option value="1">Parity adj (residual)</option>
                  </select>
                </Field>
              )}
              {v1AtmSkew && v1AtmSkewParity && (
                <Field label="Carry pts"><input type="number" step="0.5" style={inputStyle} value={v1AtmSkewCarry} onChange={(e) => setV1AtmSkewCarry(e.target.value)} /></Field>
              )}'''

J7_OLD = '  if (cfg.atm_skew_filter?.enabled) add("ATM skew", `${cfg.atm_skew_filter.invert ? "dearer" : "cheaper"} ≥${Number(cfg.atm_skew_filter.min_diff_pts) || 0}`);   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── direction is the headline, so it leads the chip'
J7_NEW = '  if (cfg.atm_skew_filter?.enabled) add("ATM skew", `${cfg.atm_skew_filter.invert ? "dearer" : "cheaper"} ${cfg.atm_skew_filter.parity_adjust ? `par${cfg.atm_skew_filter.carry_pts}` : "raw"} ≥${Number(cfg.atm_skew_filter.min_diff_pts) || 0}`);   // ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── basis + carry join the chip: raw and residual runs must never look alike'

Q1_OLD = '  if (cfg.atm_skew_filter?.enabled) p.push(`skew${cfg.atm_skew_filter.invert ? "-inv" : ""}${Number(cfg.atm_skew_filter.min_diff_pts) || 0}`);   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ──'
Q1_NEW = '  if (cfg.atm_skew_filter?.enabled) p.push(`skew${cfg.atm_skew_filter.invert ? "-inv" : ""}${cfg.atm_skew_filter.parity_adjust ? `-par${cfg.atm_skew_filter.carry_pts}` : ""}${Number(cfg.atm_skew_filter.min_diff_pts) || 0}`);   // ── SCALP_V1_ATM_SKEW_PARITY_20260826 ──'

C1_OLD = '''  { key: "atm_skew",         label: "ATM skew",       get: (r) => (r.config?.atm_skew_filter?.enabled ? `${r.config.atm_skew_filter.invert ? "dearer" : "cheaper"} ≥${Number(r.config.atm_skew_filter.min_diff_pts) || 0}` : null) },   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── two runs that differ only in direction must NEVER diff as identical params'''
C1_NEW = '''  { key: "atm_skew",         label: "ATM skew",       get: (r) => (r.config?.atm_skew_filter?.enabled ? `${r.config.atm_skew_filter.invert ? "dearer" : "cheaper"} ${r.config.atm_skew_filter.parity_adjust ? `parity ${r.config.atm_skew_filter.carry_pts}` : "raw"} ≥${Number(r.config.atm_skew_filter.min_diff_pts) || 0}` : null) },   // ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── direction AND basis AND carry, so no two skew runs can diff as identical params'''

W1_OLD = """  { key: "v1_atm_skew_dir", label: "V1 ATM skew dir (0=cheaper,1=dearer)", strategies: [V1],
    hint: "0, 1", parse: _num,
    apply: (c, v) => { c.atm_skew_filter = { ...(c.atm_skew_filter || {}), invert: !!v }; }, fmt: (v) => (v ? "dearer" : "cheaper") },"""
W1_NEW = """  { key: "v1_atm_skew_dir", label: "V1 ATM skew dir (0=cheaper,1=dearer)", strategies: [V1],
    hint: "0, 1", parse: _num,
    apply: (c, v) => { c.atm_skew_filter = { ...(c.atm_skew_filter || {}), invert: !!v }; }, fmt: (v) => (v ? "dearer" : "cheaper") },
  // ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── -1 = raw sk; >= 0 = parity-adjusted
  // with that carry. Sweep {-1, 4, 6.5, 9}: the raw control plus the plateau
  // check in ONE grid — if 4/6.5/9 behave alike the constant is robust; if
  // only 6.5 works it is a fit, and it goes in the falsified pile.
  { key: "v1_atm_skew_carry", label: "V1 ATM skew carry (-1=raw)", strategies: [V1],
    hint: "-1, 4, 6.5, 9", parse: _num,
    apply: (c, v) => { c.atm_skew_filter = { ...(c.atm_skew_filter || {}), parity_adjust: v >= 0, carry_pts: (v >= 0 ? v : 6.5) }; }, fmt: (v) => (v >= 0 ? `par${v}` : "raw") },"""


def main():
    if not (ROOT / "backend" / RN_REL).exists():
        _die("run from the scalp-app repo root")
    staged = []
    for tree in TREES:
        rp, lp = tree / RN_REL, tree / LD_REL
        rt, lt = rp.read_text(), lp.read_text()
        for p, t in ((rp, rt), (lp, lt)):
            if FENCE in t:
                _die(f"fence {FENCE} already present in {p} — already applied")
        if PREREQ not in rt:
            _die(f"prerequisite fence {PREREQ} MISSING in {rp}")
        rt = _ro(rt, R1_OLD, R1_NEW, f"{tree.name}:R1")
        rt = _ro(rt, R2_OLD, R2_NEW, f"{tree.name}:R2")
        lt = _ro(lt, L1_OLD, L1_NEW, f"{tree.name}:L1")
        staged += [(rp, rt), (lp, lt)]
    t = BT_JSX.read_text()
    if FENCE in t:
        _die("fence already present in Backtest.jsx")
    for lab, o, n in [("J1", J1_OLD, J1_NEW), ("J2", J2_OLD, J2_NEW), ("J3", J3_OLD, J3_NEW),
                      ("J4", J4_OLD, J4_NEW), ("J5", J5_OLD, J5_NEW), ("J6", J6_OLD, J6_NEW),
                      ("J7", J7_OLD, J7_NEW)]:
        t = _ro(t, o, n, f"Backtest:{lab}")
    staged.append((BT_JSX, t))
    for path, lab, o, n in [(QU_JSX, "Queue:Q1", Q1_OLD, Q1_NEW),
                            (RC_JSX, "Comparison:C1", C1_OLD, C1_NEW),
                            (SW_JSX, "Sweep:W1", W1_OLD, W1_NEW)]:
        tt = path.read_text()
        if FENCE in tt:
            _die(f"fence already present in {path.name}")
        staged.append((path, _ro(tt, o, n, lab)))
    for p, t in staged:
        if p.suffix == ".py":
            try:
                compile(t, str(p), "exec")
            except SyntaxError as e:
                _die(f"staged content for {p} does not compile: {e}")
    for p, t in staged:
        p.write_text(t)
        print(f"PATCHED: {p}")
    print(f"\nDONE — fence {FENCE} applied. Default is RAW sk; nothing changes")
    print("until 'Skew basis' is switched to Parity adj.")
    print()
    print("THE FOUR RUNS (everything else identical to your 56fdb692 config):")
    print("  1. control      : ATM skew Off                       (98e5367e — have it)")
    print("  2. raw dearer   : dearer + Raw                       (56fdb692 — have it)")
    print("  3. parity dearer: dearer + Parity adj, carry 6.5     <- the candidate")
    print("  4. carry sweep  : axis 'V1 ATM skew carry' = -1, 4, 6.5, 9")
    print()
    print("WHAT DECIDES IT: 2023. Raw sk inverts that year (23 vs 736 per trade)")
    print("and it is the weak year in both dearer runs. If the residual holds")
    print("2023 up AND 4/6.5/9 land close together, the mechanism is confirmed.")
    print("If only 6.5 works, it is a fit — falsified idea #12, flagship stands.")
    print("Bar unchanged otherwise: 7/7, worst-year AND maxDD better than the")
    print("control, plus the hold-out (tune excluding 2022+2025, validate there).")


if __name__ == "__main__":
    main()
