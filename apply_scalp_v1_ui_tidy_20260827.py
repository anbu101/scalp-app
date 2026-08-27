#!/usr/bin/env python3
# apply_scalp_v1_ui_tidy_20260827.py
#
# UI TIDY FOR CONFIG B — fence: SCALP_V1_UI_TIDY_20260827   (frontend-only)
#
# Decision (Anbu, 27 Aug 2026): Config B = flagship base + ATM skew
# "Sell dearer side" (RAW basis, min 0) + VWAP tolerance band +10
# (min_below_pts = -10) is adopted as the second base config. Accordingly:
#
# 1. REMOVE the parity-adjust controls (Skew basis + Carry pts fields, their
#    state, saveParams/buildConfig wiring, and the carry sweep axis).
#    Falsified idea #13: 2023 negative at every carry {4: -454K, 6.5: -130K,
#    9: -272K}, no plateau. UI-ONLY removal — the backend keys, the loader
#    defaults and the runner logic stay, so any historical run's stored
#    config still executes and still DISPLAYS (chip / queue token /
#    comparison row all keep their parity rendering).
#
# 2. LEGITIMISE the negative VWAP tolerance, which is now a sealed Config B
#    parameter rather than an exploration trick:
#      - the "Min pts below" input loses its min="0" (typed negatives were
#        already accepted; the spinner now agrees)
#      - chip / queue token / comparison row render a negative value
#        honestly as a tolerance band ("within +10") instead of the
#        misleading bare "below" that hid the sign
#
# PREREQ: SCALP_V1_ATM_SKEW_PARITY_20260826 present in Backtest.jsx.
# Idempotent. Run from the repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_UI_TIDY_20260827"
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "frontend" / "src"
BT_JSX = SRC / "pages" / "Backtest.jsx"
QU_JSX = SRC / "pages" / "backtest" / "BacktestQueue.jsx"
RC_JSX = SRC / "pages" / "backtest" / "RunComparison.jsx"
SW_JSX = SRC / "pages" / "backtest" / "SweepBuilder.jsx"


def _die(m):
    print(f"ABORT: {m}")
    sys.exit(1)


def _ro(t, o, n, lab):
    c = t.count(o)
    if c != 1:
        _die(f"anchor '{lab}' matched {c} times (want 1) — NOTHING written")
    return t.replace(o, n, 1)


# ── 1. parity controls out ────────────────────────────────────────────────

B1_OLD = """  // ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── residual vs raw sk, + the carry.
  const [v1AtmSkewParity, setV1AtmSkewParity] = useState(saved.v1AtmSkewParity ?? false);
  const [v1AtmSkewCarry, setV1AtmSkewCarry] = useState(saved.v1AtmSkewCarry ?? 6.5);
"""
B1_NEW = "  // ── SCALP_V1_UI_TIDY_20260827 ── parity-adjust controls removed\n  // (falsified idea #13); backend keys retained for historical runs.\n"

B2_OLD = "      v1AtmSkewInvert,   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ──\n      v1AtmSkewParity, v1AtmSkewCarry });   // ── SCALP_V1_ATM_SKEW_PARITY_20260826 ──"
B2_NEW = "      v1AtmSkewInvert });   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ──"

B3_OLD = "      v1AtmSkewInvert,   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ──\n      v1AtmSkewParity, v1AtmSkewCarry]);   // ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── stale-closure rule: saveParams reads them, so they land here in the SAME commit"
B3_NEW = "      v1AtmSkewInvert]);   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── stale-closure rule: saveParams reads it, so it lands here in the SAME commit"

B4_OLD = "      if (v1AtmSkew) cfg.atm_skew_filter = { enabled: true, min_diff_pts: Number(v1AtmSkewMin) || 0, invert: !!v1AtmSkewInvert, parity_adjust: !!v1AtmSkewParity, carry_pts: Number(v1AtmSkewCarry) };   // ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── parity choice rides along"
B4_NEW = "      if (v1AtmSkew) cfg.atm_skew_filter = { enabled: true, min_diff_pts: Number(v1AtmSkewMin) || 0, invert: !!v1AtmSkewInvert };   // ── SCALP_V1_UI_TIDY_20260827 ── raw basis only; parity keys no longer emitted"

B5_OLD = "      v1AtmSkewInvert,   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ──\n      v1AtmSkewParity, v1AtmSkewCarry]);   // ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"
B5_NEW = "      v1AtmSkewInvert]);   // ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── stale-closure rule: buildConfig reads it, so it lands here in the SAME commit"

B6_OLD = '''              {/* ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── "Raw" compares the two
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
              )}
'''
B6_NEW = ""

W1_OLD = """  // ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── -1 = raw sk; >= 0 = parity-adjusted
  // with that carry. Sweep {-1, 4, 6.5, 9}: the raw control plus the plateau
  // check in ONE grid — if 4/6.5/9 behave alike the constant is robust; if
  // only 6.5 works it is a fit, and it goes in the falsified pile.
  { key: "v1_atm_skew_carry", label: "V1 ATM skew carry (-1=raw)", strategies: [V1],
    hint: "-1, 4, 6.5, 9", parse: _num,
    apply: (c, v) => { c.atm_skew_filter = { ...(c.atm_skew_filter || {}), parity_adjust: v >= 0, carry_pts: (v >= 0 ? v : 6.5) }; }, fmt: (v) => (v >= 0 ? `par${v}` : "raw") },
"""
W1_NEW = ""

# ── 2. VWAP tolerance made first-class ────────────────────────────────────

V1_OLD = '''              {v1Vwap && (
                <Field label="Min pts below"><input type="number" min="0" step="0.5" style={inputStyle} value={v1VwapMinBelow} onChange={(e) => setV1VwapMinBelow(e.target.value)} /></Field>
              )}'''
V1_NEW = '''              {v1Vwap && (
                <Field label="Min pts below"><input type="number" step="0.5" style={inputStyle} value={v1VwapMinBelow} onChange={(e) => setV1VwapMinBelow(e.target.value)} /></Field>
              )}
              {/* ── SCALP_V1_UI_TIDY_20260827 ── negative = tolerance band
                  (Config B uses -10: entries allowed within +10 of the
                  session average). min="0" removed — this is a sealed
                  parameter now, not a typed-input trick. */}'''

V2_OLD = '  if (cfg.vwap_filter?.enabled) add("VWAP", `below${Number(cfg.vwap_filter.min_below_pts) > 0 ? ` ≥${cfg.vwap_filter.min_below_pts}` : ""}`);   // ── SCALP_V1_VWAP_20260825 ──'
V2_NEW = '  if (cfg.vwap_filter?.enabled) { const _mb = Number(cfg.vwap_filter.min_below_pts) || 0; add("VWAP", _mb < 0 ? `within +${-_mb}` : `below${_mb > 0 ? ` ≥${_mb}` : ""}`); }   // ── SCALP_V1_UI_TIDY_20260827 ── tolerance band shown honestly, never a bare "below"'

V3_OLD = '  if (cfg.vwap_filter?.enabled) p.push(`vwap${Number(cfg.vwap_filter.min_below_pts) > 0 ? cfg.vwap_filter.min_below_pts : ""}`);   // ── SCALP_V1_VWAP_20260825 ──'
V3_NEW = '  if (cfg.vwap_filter?.enabled) { const _mb = Number(cfg.vwap_filter.min_below_pts) || 0; p.push(_mb < 0 ? `vwap+${-_mb}tol` : `vwap${_mb > 0 ? _mb : ""}`); }   // ── SCALP_V1_UI_TIDY_20260827 ──'

V4_OLD = '''  { key: "vwap_filter",      label: "VWAP filter",    get: (r) => (r.config?.vwap_filter?.enabled ? `below ≥${r.config.vwap_filter.min_below_pts}` : null) },   // ── SCALP_V1_VWAP_20260825 ──'''
V4_NEW = '''  { key: "vwap_filter",      label: "VWAP filter",    get: (r) => { if (!r.config?.vwap_filter?.enabled) return null; const _mb = Number(r.config.vwap_filter.min_below_pts) || 0; return _mb < 0 ? `within +${-_mb}` : `below ≥${_mb}`; } },   // ── SCALP_V1_UI_TIDY_20260827 ── Config B's band reads "within +10"'''


def main():
    if not BT_JSX.exists():
        _die("run from the scalp-app repo root")
    t = BT_JSX.read_text()
    if FENCE in t:
        _die(f"fence {FENCE} already present in Backtest.jsx — already applied")
    if "SCALP_V1_ATM_SKEW_PARITY_20260826" not in t:
        _die("prerequisite parity fence missing in Backtest.jsx")
    staged = []
    for lab, o, n in [("B1", B1_OLD, B1_NEW), ("B2", B2_OLD, B2_NEW),
                      ("B3", B3_OLD, B3_NEW), ("B4", B4_OLD, B4_NEW),
                      ("B5", B5_OLD, B5_NEW), ("B6", B6_OLD, B6_NEW),
                      ("V1", V1_OLD, V1_NEW), ("V2", V2_OLD, V2_NEW)]:
        t = _ro(t, o, n, f"Backtest:{lab}")
    if "v1AtmSkewParity" in t or "v1AtmSkewCarry" in t:
        _die("parity state still referenced after removal — NOTHING written")
    staged.append((BT_JSX, t))
    for path, lab, o, n in [(QU_JSX, "Queue:V3", V3_OLD, V3_NEW),
                            (RC_JSX, "Comparison:V4", V4_OLD, V4_NEW),
                            (SW_JSX, "Sweep:W1", W1_OLD, W1_NEW)]:
        tt = path.read_text()
        if FENCE in tt and path is not SW_JSX:
            _die(f"fence {FENCE} already present in {path.name}")
        staged.append((path, _ro(tt, o, n, lab)))
    for p, tt in staged:
        p.write_text(tt)
        print(f"PATCHED: {p}")
    print(f"\nDONE — fence {FENCE} applied (frontend only; no rebuild of the")
    print("backend needed, but the desktop frontend tree must be re-synced).")
    print()
    print("CONFIG B on the Backtest page is now exactly these fields:")
    print("  ATM SKEW: Sell dearer side · MIN SKEW PTS: 0")
    print("  VWAP FILTER: On (below) · MIN PTS BELOW: -10   (chip: 'within +10')")
    print("  ...on the sealed flagship base. Parity controls are gone; every")
    print("  historical parity run still renders correctly in queue/comparison.")


if __name__ == "__main__":
    main()
