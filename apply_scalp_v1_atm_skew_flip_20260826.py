#!/usr/bin/env python3
# apply_scalp_v1_atm_skew_flip_20260826.py
#
# D15b — ATM SKEW DIRECTION FLIP — fence: SCALP_V1_ATM_SKEW_FLIP_20260826
# (backtest-only; extends SCALP_V1_ATM_SKEW_20260826)
#
# WHY: the filter as originally specified ("sell the side the ATM pair prices
# as CHEAPER") was falsified on the full corpus — it selects the worse half:
# +Rs 153/trade favoured vs +Rs 645/trade rejected, worse in 6 of 7 years, and
# both live configs collapsed when it was switched on (Rs 41.65L -> -Rs 12.18L,
# Rs 32.86L -> -Rs 1.23L). With the put-call-parity component regressed out
# (slope 1.001 — sk IS -sd plus carry) the residual separates the same way:
# -Rs 430/trade vs +Rs 1,197/trade.
#
# This fence adds the INVERTED direction as a first-class option so both can
# be run and shown side by side:
#
#   "atm_skew_filter": {"enabled": false, "min_diff_pts": 0, "invert": false}
#
#   invert = false  "Sell cheaper side"  CE sell needs ATM PE dearer  (original)
#   invert = true   "Sell dearer side"   CE sell needs ATM CE dearer  (inverted)
#
# HONESTY NOTE, and it belongs in whatever you show your friend: the inverted
# rule was derived from THIS dataset after seeing the answer. That makes it a
# hypothesis, not a finding. The pre-registered bar is deliberately stricter
# than for a fresh idea: 7/7 years, worst-year AND maxDD both better than the
# flagship, AND it must survive a hold-out (tune with 2022 and 2025 excluded,
# then validate on those two untouched). A rule mined from the same data it is
# then judged on has failed this project's tests before.
#
# Everything else is unchanged: the gate stays fail-closed on an unmeasurable
# ATM pair, stays per-candidate, and the sk/sd diagnostics keep recording.
#
# PREREQ: SCALP_V1_ATM_SKEW_20260826. Idempotent. Run from the repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_ATM_SKEW_FLIP_20260826"
PREREQ = "SCALP_V1_ATM_SKEW_20260826"
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
R1_OLD = '''    try:
        skew_min = float(_sk.get("min_diff_pts", 0.0) or 0.0)
    except (TypeError, ValueError):
        skew_min = 0.0'''
R1_NEW = '''    try:
        skew_min = float(_sk.get("min_diff_pts", 0.0) or 0.0)
    except (TypeError, ValueError):
        skew_min = 0.0
    # ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── False = original ("sell the side
    # the ATM pair prices CHEAPER"), True = inverted. Default False keeps the
    # as-specified rule; the inverted branch is the post-hoc hypothesis.
    skew_invert = bool(_sk.get("invert", False))'''

# ── R2: the gate ──────────────────────────────────────────────────────────
R2_OLD = '''                    _diff = _skv[0] if sym.endswith("CE") else -_skv[0]
                    if _diff <= skew_min:
                        continue'''
R2_NEW = '''                    _diff = _skv[0] if sym.endswith("CE") else -_skv[0]
                    # ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── one sign flip is the
                    # whole difference between the two rules; the threshold,
                    # the fail-closed path and the diagnostics are shared, so
                    # a paired comparison differs ONLY in direction.
                    if skew_invert:
                        _diff = -_diff
                    if _diff <= skew_min:
                        continue'''

# ── loader ────────────────────────────────────────────────────────────────
L1_OLD = '''        "atm_skew_filter": {
            "enabled":      False,
            "min_diff_pts": 0
        },'''
L1_NEW = '''        "atm_skew_filter": {
            "enabled":      False,
            "min_diff_pts": 0,
            # ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── False = sell the cheaper
            # side (as originally specified, falsified on the full corpus);
            # True = sell the dearer side (the inverted hypothesis).
            "invert":       False
        },'''

# ── UI ────────────────────────────────────────────────────────────────────
J1_OLD = "  const [v1AtmSkewMin, setV1AtmSkewMin] = useState(saved.v1AtmSkewMin ?? 0);"
J1_NEW = """  const [v1AtmSkewMin, setV1AtmSkewMin] = useState(saved.v1AtmSkewMin ?? 0);
  // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── direction of the skew rule.
  const [v1AtmSkewInvert, setV1AtmSkewInvert] = useState(saved.v1AtmSkewInvert ?? false);"""

J2_OLD = "      v1AtmSkew, v1AtmSkewMin });   // ── SCALP_V1_ATM_SKEW_20260826 ──"
J2_NEW = "      v1AtmSkew, v1AtmSkewMin,   // ── SCALP_V1_ATM_SKEW_20260826 ──\n      v1AtmSkewInvert });   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ──"

J3_OLD = "      v1AtmSkew, v1AtmSkewMin]);   // ── SCALP_V1_ATM_SKEW_20260826 ── stale-closure rule: saveParams reads them, so they land here in the SAME commit"
J3_NEW = "      v1AtmSkew, v1AtmSkewMin,   // ── SCALP_V1_ATM_SKEW_20260826 ──\n      v1AtmSkewInvert]);   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── stale-closure rule: saveParams reads it, so it lands here in the SAME commit"

J4_OLD = "      if (v1AtmSkew) cfg.atm_skew_filter = { enabled: true, min_diff_pts: Number(v1AtmSkewMin) || 0 };   // ── SCALP_V1_ATM_SKEW_20260826 ──"
J4_NEW = "      if (v1AtmSkew) cfg.atm_skew_filter = { enabled: true, min_diff_pts: Number(v1AtmSkewMin) || 0, invert: !!v1AtmSkewInvert };   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── direction rides along"

J5_OLD = "      v1AtmSkew, v1AtmSkewMin]);   // ── SCALP_V1_ATM_SKEW_20260826 ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"
J5_NEW = "      v1AtmSkew, v1AtmSkewMin,   // ── SCALP_V1_ATM_SKEW_20260826 ──\n      v1AtmSkewInvert]);   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── stale-closure rule: buildConfig reads it, so it lands here in the SAME commit"

J6_OLD = '''              <Field label="ATM skew">
                <select style={inputStyle} value={v1AtmSkew ? "1" : "0"} onChange={(e) => setV1AtmSkew(e.target.value === "1")}>
                  <option value="0">Off</option>
                  <option value="1">On</option>
                </select>
              </Field>'''
J6_NEW = '''              {/* ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── one control, three
                  states, so a paired demo differs ONLY in direction.
                  "cheaper" = the rule as first specified (CE sell needs ATM
                  PE dearer); "dearer" = the inverted hypothesis. */}
              <Field label="ATM skew">
                <select style={inputStyle}
                  value={!v1AtmSkew ? "0" : (v1AtmSkewInvert ? "2" : "1")}
                  onChange={(e) => { const v = e.target.value; setV1AtmSkew(v !== "0"); setV1AtmSkewInvert(v === "2"); }}>
                  <option value="0">Off</option>
                  <option value="1">Sell cheaper side</option>
                  <option value="2">Sell dearer side (inverted)</option>
                </select>
              </Field>'''

J7_OLD = '  if (cfg.atm_skew_filter?.enabled) add("ATM skew", `≥${Number(cfg.atm_skew_filter.min_diff_pts) || 0}`);   // ── SCALP_V1_ATM_SKEW_20260826 ──'
J7_NEW = '  if (cfg.atm_skew_filter?.enabled) add("ATM skew", `${cfg.atm_skew_filter.invert ? "dearer" : "cheaper"} ≥${Number(cfg.atm_skew_filter.min_diff_pts) || 0}`);   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── direction is the headline, so it leads the chip'

Q1_OLD = '  if (cfg.atm_skew_filter?.enabled) p.push(`skew${Number(cfg.atm_skew_filter.min_diff_pts) || 0}`);   // ── SCALP_V1_ATM_SKEW_20260826 ──'
Q1_NEW = '  if (cfg.atm_skew_filter?.enabled) p.push(`skew${cfg.atm_skew_filter.invert ? "-inv" : ""}${Number(cfg.atm_skew_filter.min_diff_pts) || 0}`);   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ──'

C1_OLD = '''  { key: "atm_skew",         label: "ATM skew",       get: (r) => (r.config?.atm_skew_filter?.enabled ? `≥${Number(r.config.atm_skew_filter.min_diff_pts) || 0}` : null) },   // ── SCALP_V1_ATM_SKEW_20260826 ──'''
C1_NEW = '''  { key: "atm_skew",         label: "ATM skew",       get: (r) => (r.config?.atm_skew_filter?.enabled ? `${r.config.atm_skew_filter.invert ? "dearer" : "cheaper"} ≥${Number(r.config.atm_skew_filter.min_diff_pts) || 0}` : null) },   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── two runs that differ only in direction must NEVER diff as identical params'''

W1_OLD = """  { key: "v1_atm_skew", label: "V1 ATM skew min pts (-1=off)", strategies: [V1],
    hint: "-1, 0, 2, 5", parse: _num,
    apply: (c, v) => { if (v >= 0) c.atm_skew_filter = { enabled: true, min_diff_pts: v }; }, fmt: (v) => (v >= 0 ? `skew${v}` : "no skew") },"""
W1_NEW = """  { key: "v1_atm_skew", label: "V1 ATM skew min pts (-1=off)", strategies: [V1],
    hint: "-1, 0, 2, 5", parse: _num,
    apply: (c, v) => { if (v >= 0) c.atm_skew_filter = { ...(c.atm_skew_filter || {}), enabled: true, min_diff_pts: v }; }, fmt: (v) => (v >= 0 ? `skew${v}` : "no skew") },
  // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── 0 = cheaper side, 1 = dearer side.
  // Cross this axis with the one above to get the full paired grid in ONE
  // sweep — that table is the thing to show, not two separate runs.
  { key: "v1_atm_skew_dir", label: "V1 ATM skew dir (0=cheaper,1=dearer)", strategies: [V1],
    hint: "0, 1", parse: _num,
    apply: (c, v) => { c.atm_skew_filter = { ...(c.atm_skew_filter || {}), invert: !!v }; }, fmt: (v) => (v ? "dearer" : "cheaper") },"""


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
    print(f"\nDONE — fence {FENCE} applied. Default is the ORIGINAL direction;")
    print("nothing changes until the selector is moved.")
    print()
    print("THE DEMO, in one sweep rather than two runs:")
    print("  axis 'V1 ATM skew min pts' = 0")
    print("  axis 'V1 ATM skew dir'     = 0, 1")
    print("  ...plus a filter-OFF run as the control. Three rows, one table,")
    print("  identical in every other parameter — that is the thing to show.")
    print()
    print("Then the strict bar for the inverted rule, because it was derived")
    print("AFTER seeing this data: 7/7 years, worst-year AND maxDD better than")
    print("the flagship, and a hold-out (tune excluding 2022 + 2025, validate")
    print("on them untouched). If it only wins in-sample, it is idea #12.")


if __name__ == "__main__":
    main()
