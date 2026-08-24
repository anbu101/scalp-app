#!/usr/bin/env python3
# apply_scalp_v1_entry_sizing_20260823.py
#
# D8.2 + D8.3 — SCALP_V1 strategy improvements (backtest-only)
# fence: SCALP_V1_ENTRY_SIZING_20260823
#
# D8.2 — RISK-NORMALIZED SIZING. Root finding: the 20-pt Max SL Cap binds on
#   54% of entries and clamps stops INSIDE the setup's own structure (win
#   rate 48% vs 63% unclamped, -Rs 81L cumulative). Instead of mutilating the
#   stop, hold rupee risk constant through SIZE:
#     lots_dyn = clamp( floor(rupee_risk / (risk_pts * lot_size)), 1, lots )
#   where risk_pts = signal.sl - signal.entry_price (the ACTUAL stop distance
#   under whatever clamp config the run uses). Config:
#     "risk_sizing": {"enabled": false, "rupee_risk": 13000}
#   Default rupee_risk 13000 = 20 pts x 650 qty -> a trade at exactly the cap
#   sizes to today's 10 lots; wider structure sizes DOWN, tighter sizes UP
#   (never above configured lots, never below 1 lot). Intended pairing: run
#   with max_sl raised/off so stops honor structure while rupee risk stays
#   flat. Invalid config -> sizing disabled + audit line (fail-safe: fixed
#   lots, today's behavior).
#
# D8.3 — OVEREXTENSION ENTRY GATE. The one entry feature negative in BOTH the
#   full period and 2025-26: extreme band spread (EMA8 - EMA20_low). Config:
#     "entry_max_spread_points": 0        (0 = off)
#   Entry skipped when spread > threshold. Computed from the same indicator
#   values the diag snapshot already reads; warmup-None values -> gate
#   inactive for that candle (fail-open: can't measure -> don't block).
#
# Both keys: omit-when-off in the UI, chips + queue tokens + RunComparison
# rows added (they CHANGE results, unlike parallel_workers), sweep axes added.
# Frontend export gains a Qty column (sized runs need it for analysis).
#
# PREREQS: all four prior fences. Idempotent. Run from repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_ENTRY_SIZING_20260823"
PREREQS_RN = ["SCALP_V1_BT_FILTERS_20260823", "SCALP_V1_DIAG_20260823",
              "SCALP_V1_DETERMINISM_20260823", "SCALP_V1_PARALLEL_20260823"]
ROOT = Path(__file__).resolve().parent
RN_REL = "app/backtest/runner/backtest_runner.py"
LD_REL = "app/config/strategy_loader.py"
SRC = ROOT / "frontend" / "src"
BT_JSX = SRC / "pages" / "Backtest.jsx"
QU_JSX = SRC / "pages" / "backtest" / "BacktestQueue.jsx"
RC_JSX = SRC / "pages" / "backtest" / "RunComparison.jsx"
SW_JSX = SRC / "pages" / "backtest" / "SweepBuilder.jsx"

TREES = [ROOT / "backend"]
_desktop = ROOT / "desktop" / "src-tauri" / "backend"
if (_desktop / RN_REL).exists():
    TREES.append(_desktop)


def _die(msg):
    print(f"ABORT: {msg}")
    sys.exit(1)


def _replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        _die(f"anchor '{label}' matched {n} times (want 1) — NOTHING written")
    return text.replace(old, new, 1)


# ═══ strategy_loader.py ════════════════════════════════════════════════════

L1_OLD = '''        "parallel_workers": 1,
        # ── SCALP_V1_BT_FILTERS_20260823 END ──'''
L1_NEW = '''        "parallel_workers": 1,
        # ── SCALP_V1_ENTRY_SIZING_20260823 ── D8.2 risk-normalized sizing
        # (constant rupee risk via lots) + D8.3 overextension entry gate
        # (skip when EMA8-EMA20_low spread exceeds threshold; 0 = off).
        "risk_sizing": {
            "enabled":    False,
            "rupee_risk": 13000
        },
        "entry_max_spread_points": 0,
        # ── SCALP_V1_BT_FILTERS_20260823 END ──'''

# ═══ backtest_runner.py ════════════════════════════════════════════════════

# ── R1: parse the new config keys ──────────────────────────────────────────
R1_OLD = "    # ── SCALP_V1_BT_FILTERS_20260823 END: config ──"
R1_NEW = '''    # ── SCALP_V1_BT_FILTERS_20260823 END: config ──

    # ── SCALP_V1_ENTRY_SIZING_20260823 BEGIN: config (D8.2, D8.3) ──
    _rs = cfg.get("risk_sizing") or {}
    rs_enabled = bool(_rs.get("enabled", False))
    try:
        rs_rupee = float(_rs.get("rupee_risk", 13000) or 13000)
        if rs_rupee <= 0:
            raise ValueError(rs_rupee)
    except (TypeError, ValueError):
        if rs_enabled:
            write_audit_log(
                f"[BACKTEST][{strategy_id}] risk_sizing.rupee_risk UNPARSEABLE "
                f"(value={_rs.get('rupee_risk')!r}) -> sizing DISABLED, "
                f"fixed lots={lots} (fail-safe)")
        rs_enabled = False
        rs_rupee = 0.0
    try:
        max_spread_pts = float(cfg.get("entry_max_spread_points", 0) or 0)
    except (TypeError, ValueError):
        max_spread_pts = 0.0
    # ── SCALP_V1_ENTRY_SIZING_20260823 END: config ──'''

# ── R2: D8.3 gate at candidate time (indicator values already in hand) ─────
R2_OLD = '''                _e20h = ind_vals.get("ema20_high")
                _r2 = lambda v: round(v, 2)'''
R2_NEW = '''                _e20h = ind_vals.get("ema20_high")
                # ── SCALP_V1_ENTRY_SIZING_20260823: D8.3 overextension gate.
                # Skip entries where the band spread (EMA8 - EMA20_low) shows
                # an overextended move — the one entry feature negative in
                # both 2020-24 and 2025-26. Warmup-None values -> gate
                # inactive for this candle (can't measure -> don't block).
                if (max_spread_pts > 0 and _e8 is not None
                        and _e20l is not None
                        and (_e8 - _e20l) > max_spread_pts):
                    continue
                _r2 = lambda v: round(v, 2)'''

# ── R3: D8.2 per-trade qty at election ─────────────────────────────────────
R3_OLD = "                ep, sym, ctx, c, signal, diag = entry_candidates[0]   # ── SCALP_V1_DIAG_20260823 ──"
R3_NEW = '''                ep, sym, ctx, c, signal, diag = entry_candidates[0]   # ── SCALP_V1_DIAG_20260823 ──
                # ── SCALP_V1_ENTRY_SIZING_20260823: D8.2 risk-normalized
                # sizing. Constant rupee risk per trade: wider stop -> fewer
                # lots, tighter stop -> more (never above configured lots,
                # never below 1). risk_pts is the ACTUAL final stop distance,
                # so this composes correctly with any min/max SL clamp config.
                _trade_qty = qty
                if rs_enabled:
                    _risk_pts = float(signal.sl) - float(signal.entry_price)
                    if _risk_pts > 0:
                        _lots_dyn = int(rs_rupee // (_risk_pts * lot_size))
                        _lots_dyn = max(1, min(lots, _lots_dyn))
                        _trade_qty = _lots_dyn * lot_size'''

R4_OLD = "                    sl=signal.sl, tp=signal.tp, qty=qty,"
R4_NEW = "                    sl=signal.sl, tp=signal.tp, qty=_trade_qty,   # ── SCALP_V1_ENTRY_SIZING_20260823 ──"

# ═══ Backtest.jsx ══════════════════════════════════════════════════════════

J1_OLD = "  const [v1Workers, setV1Workers] = useState(saved.v1Workers ?? 4);"
J1_NEW = """  const [v1Workers, setV1Workers] = useState(saved.v1Workers ?? 4);
  // ── SCALP_V1_ENTRY_SIZING_20260823 ── D8.2 sizing + D8.3 spread gate.
  const [v1RiskSizing, setV1RiskSizing] = useState(saved.v1RiskSizing ?? false);
  const [v1RupeeRisk, setV1RupeeRisk] = useState(saved.v1RupeeRisk ?? 13000);
  const [v1MaxSpread, setV1MaxSpread] = useState(saved.v1MaxSpread ?? 0);"""

J2_OLD = "      v1Workers });   // ── SCALP_V1_PARALLEL_20260823 ──"
J2_NEW = "      v1Workers,   // ── SCALP_V1_PARALLEL_20260823 ──\n      v1RiskSizing, v1RupeeRisk, v1MaxSpread });   // ── SCALP_V1_ENTRY_SIZING_20260823 ──"

J3_OLD = "      v1Workers]);   // ── SCALP_V1_PARALLEL_20260823 ── stale-closure rule: saveParams reads it, so it lands here in the SAME commit"
J3_NEW = "      v1Workers,   // ── SCALP_V1_PARALLEL_20260823 ──\n      v1RiskSizing, v1RupeeRisk, v1MaxSpread]);   // ── SCALP_V1_ENTRY_SIZING_20260823 ── stale-closure rule: saveParams reads them, so they land here in the SAME commit"

J4_OLD = "      cfg.parallel_workers = Number(v1Workers) || 1;\n    }"
J4_NEW = """      cfg.parallel_workers = Number(v1Workers) || 1;
      // ── SCALP_V1_ENTRY_SIZING_20260823 ── omit-when-off: baseline configs
      // stay byte-identical, and RunComparison diffs stay clean.
      if (v1RiskSizing) cfg.risk_sizing = { enabled: true, rupee_risk: Number(v1RupeeRisk) || 13000 };
      if (Number(v1MaxSpread) > 0) cfg.entry_max_spread_points = Number(v1MaxSpread);
    }"""

J5_OLD = "      v1Workers]);   // ── SCALP_V1_PARALLEL_20260823 ── stale-closure rule: buildConfig reads it, so it lands here in the SAME commit"
J5_NEW = "      v1Workers,   // ── SCALP_V1_PARALLEL_20260823 ──\n      v1RiskSizing, v1RupeeRisk, v1MaxSpread]);   // ── SCALP_V1_ENTRY_SIZING_20260823 ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"

J6_OLD = '              <Field label="Workers"><input type="number" min="1" max="16" style={inputStyle} value={v1Workers} onChange={(e) => setV1Workers(e.target.value)} /></Field>'
J6_NEW = '''              <Field label="Workers"><input type="number" min="1" max="16" style={inputStyle} value={v1Workers} onChange={(e) => setV1Workers(e.target.value)} /></Field>
              {/* ── SCALP_V1_ENTRY_SIZING_20260823 ── D8.2: constant rupee
                  risk via lots (pair with a raised/removed Max SL cap).
                  D8.3: skip overextended entries; 0 = off. */}
              <Field label="Risk sizing">
                <select style={inputStyle} value={v1RiskSizing ? "1" : "0"} onChange={(e) => setV1RiskSizing(e.target.value === "1")}>
                  <option value="0">Off (fixed lots)</option>
                  <option value="1">On (₹ risk/trade)</option>
                </select>
              </Field>
              {v1RiskSizing && (
                <Field label="₹ risk/trade"><input type="number" min="1000" step="500" style={inputStyle} value={v1RupeeRisk} onChange={(e) => setV1RupeeRisk(e.target.value)} /></Field>
              )}
              <Field label="Max spread pts"><input type="number" min="0" style={inputStyle} value={v1MaxSpread} onChange={(e) => setV1MaxSpread(e.target.value)} /></Field>'''

J7_OLD = '  if (cfg.entry_blackout?.enabled) add("Blackout", `${cfg.entry_blackout.start}–${cfg.entry_blackout.end}`);'
J7_NEW = '''  if (cfg.entry_blackout?.enabled) add("Blackout", `${cfg.entry_blackout.start}–${cfg.entry_blackout.end}`);
  // ── SCALP_V1_ENTRY_SIZING_20260823 ── RUN_PARAMS_DISPLAY tripwires.
  if (cfg.risk_sizing?.enabled) add("Risk sizing", `₹${cfg.risk_sizing.rupee_risk}/trade`);
  if (Number(cfg.entry_max_spread_points) > 0) add("Max spread", `${cfg.entry_max_spread_points} pts`);'''

# frontend export: Qty column (sized runs vary qty per trade)
J8_OLD = '  lines.push(["Symbol", "Condition", "Entry Time", "Entry", "SL", "TP", "Exit Time", "Exit", "Reason", "Gross", "Charges", "Net", "Ambiguous", "MAE", "MFE", "DurMin"].join(","));'
J8_NEW = '  lines.push(["Symbol", "Condition", "Entry Time", "Entry", "SL", "TP", "Exit Time", "Exit", "Reason", "Gross", "Charges", "Net", "Ambiguous", "MAE", "MFE", "DurMin", "Qty"].join(","));   // ── SCALP_V1_ENTRY_SIZING_20260823 ── Qty appended'

J9_OLD = """      (t.exit_ts != null && t.entry_ts != null) ? Math.round((t.exit_ts - t.entry_ts) / 60) : "",
    ].join(","));"""
J9_NEW = """      (t.exit_ts != null && t.entry_ts != null) ? Math.round((t.exit_ts - t.entry_ts) / 60) : "",
      t.qty != null ? t.qty : "",   // ── SCALP_V1_ENTRY_SIZING_20260823 ──
    ].join(","));"""

# ═══ BacktestQueue.jsx ═════════════════════════════════════════════════════

Q1_OLD = "  if (cfg.entry_blackout?.enabled) p.push(`bo ${cfg.entry_blackout.start}-${cfg.entry_blackout.end}`);"
Q1_NEW = """  if (cfg.entry_blackout?.enabled) p.push(`bo ${cfg.entry_blackout.start}-${cfg.entry_blackout.end}`);
  // ── SCALP_V1_ENTRY_SIZING_20260823 ── sweep rows must be tellable apart.
  if (cfg.risk_sizing?.enabled) p.push(`rsz ${cfg.risk_sizing.rupee_risk}`);
  if (Number(cfg.entry_max_spread_points) > 0) p.push(`sprd<${cfg.entry_max_spread_points}`);"""

# ═══ RunComparison.jsx ═════════════════════════════════════════════════════

C1_OLD = '  { key: "entry_blackout",   label: "Entry blackout", get: (r) => (r.config?.entry_blackout?.enabled ? `${r.config.entry_blackout.start}–${r.config.entry_blackout.end}` : null) },'
C1_NEW = '''  { key: "entry_blackout",   label: "Entry blackout", get: (r) => (r.config?.entry_blackout?.enabled ? `${r.config.entry_blackout.start}–${r.config.entry_blackout.end}` : null) },
  // ── SCALP_V1_ENTRY_SIZING_20260823 ── a sized run and a fixed-lots run
  // must never diff as identical params; likewise spread-gated vs open.
  { key: "risk_sizing",      label: "Risk sizing",    get: (r) => (r.config?.risk_sizing?.enabled ? `₹${r.config.risk_sizing.rupee_risk}/trade` : null) },
  { key: "max_spread",       label: "Max spread pts", get: (r) => (Number(r.config?.entry_max_spread_points) > 0 ? String(r.config.entry_max_spread_points) : null) },'''

# ═══ SweepBuilder.jsx ══════════════════════════════════════════════════════

S1_OLD = """  { key: "v1_cap", label: "V1 max trades/day", strategies: [V1],
    hint: "0, 10, 12, 15, 20", parse: _num,
    apply: (c, v) => { if (v > 0) c.max_trades_per_day = v; }, fmt: (v) => (v > 0 ? `cap ${v}` : "no cap") },"""
S1_NEW = """  { key: "v1_cap", label: "V1 max trades/day", strategies: [V1],
    hint: "0, 10, 12, 15, 20", parse: _num,
    apply: (c, v) => { if (v > 0) c.max_trades_per_day = v; }, fmt: (v) => (v > 0 ? `cap ${v}` : "no cap") },
  // ── SCALP_V1_ENTRY_SIZING_20260823 ── D8.2/D8.3 axes. rupee_risk 0 = off
  // (fixed lots); pair a sizing sweep with max_sl {0, 25, 30} for the full
  // skip-vs-widen-vs-size picture. Spread 0 = gate off.
  { key: "v1_rupee_risk", label: "V1 ₹ risk/trade (0=fixed lots)", strategies: [V1],
    hint: "0, 10000, 13000, 16000", parse: _num,
    apply: (c, v) => { if (v > 0) c.risk_sizing = { enabled: true, rupee_risk: v }; }, fmt: (v) => (v > 0 ? `rsz ${v}` : "fixed lots") },
  { key: "v1_max_spread", label: "V1 max spread pts (0=off)", strategies: [V1],
    hint: "0, 12, 17, 22", parse: _num,
    apply: (c, v) => { if (v > 0) c.entry_max_spread_points = v; }, fmt: (v) => (v > 0 ? `sprd<${v}` : "no sprd gate") },"""


def main():
    if not (ROOT / "backend" / RN_REL).exists():
        _die("run from the scalp-app repo root")

    staged = []
    for tree in TREES:
        rn_p, ld_p = tree / RN_REL, tree / LD_REL
        rn, ld = rn_p.read_text(), ld_p.read_text()
        if FENCE in rn or FENCE in ld:
            _die(f"fence {FENCE} already present under {tree} — already applied")
        for pf in PREREQS_RN:
            if pf not in rn:
                _die(f"prerequisite fence {pf} MISSING in {rn_p}")
        rn = _replace_once(rn, R1_OLD, R1_NEW, f"{tree.name}:R1")
        rn = _replace_once(rn, R2_OLD, R2_NEW, f"{tree.name}:R2")
        rn = _replace_once(rn, R3_OLD, R3_NEW, f"{tree.name}:R3")
        rn = _replace_once(rn, R4_OLD, R4_NEW, f"{tree.name}:R4")
        ld = _replace_once(ld, L1_OLD, L1_NEW, f"{tree.name}:L1")
        staged.append((rn_p, rn))
        staged.append((ld_p, ld))

    jsx_edits = [
        (BT_JSX, [("J1", J1_OLD, J1_NEW), ("J2", J2_OLD, J2_NEW),
                  ("J3", J3_OLD, J3_NEW), ("J4", J4_OLD, J4_NEW),
                  ("J5", J5_OLD, J5_NEW), ("J6", J6_OLD, J6_NEW),
                  ("J7", J7_OLD, J7_NEW), ("J8", J8_OLD, J8_NEW),
                  ("J9", J9_OLD, J9_NEW)]),
        (QU_JSX, [("Q1", Q1_OLD, Q1_NEW)]),
        (RC_JSX, [("C1", C1_OLD, C1_NEW)]),
        (SW_JSX, [("S1", S1_OLD, S1_NEW)]),
    ]
    for path, edits in jsx_edits:
        t = path.read_text()
        if FENCE in t:
            _die(f"fence {FENCE} already present in {path.name} — already applied")
        for label, old, new in edits:
            t = _replace_once(t, old, new, f"{path.name}:{label}")
        staged.append((path, t))

    # anchors verified AND staged Python compiled BEFORE any write
    for path, text in staged:
        if path.suffix == ".py":
            try:
                compile(text, str(path), "exec")
            except SyntaxError as e:
                _die(f"staged content for {path} does not compile: {e}")
    for path, text in staged:
        path.write_text(text)
        print(f"PATCHED: {path}")

    print()
    print(f"DONE — fence {FENCE} applied.")
    print()
    print("USAGE:")
    print(" * D8.2: Risk sizing = On, ₹13,000/trade, AND raise Max SL cap to")
    print("   25/30 or 0 (off) so stops honor structure. ₹13,000 at 65 lot")
    print("   size = exactly today's 10 lots for a 20-pt stop; wider sizes")
    print("   down, tighter sizes up (never above Lots, never below 1 lot).")
    print(" * D8.3: Max spread pts ~17 (75th pctile) to skip overextended")
    print("   entries; sweep {0, 12, 17, 22}.")
    print(" * Both default OFF — baseline runs unchanged.")
    print(" * Sweep axes added: 'V1 ₹ risk/trade' and 'V1 max spread pts';")
    print("   combine with the existing max_sl axis for skip-vs-widen-vs-size.")
    print(" * Frontend export now includes Qty (needed to analyze sized runs).")
    print(" * Walk-forward discipline applies: tune on 2020-2024, validate")
    print("   untouched on 2025-26.")


if __name__ == "__main__":
    main()
