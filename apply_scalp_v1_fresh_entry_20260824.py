#!/usr/bin/env python3
# apply_scalp_v1_fresh_entry_20260824.py
#
# FRESH-ENTRY GATE — fence: SCALP_V1_FRESH_ENTRY_20260824
#
# FINDING: the first minutes after any entry boundary produce a synchronized
# burst of entries whose conditions were ALREADY true and merely waiting for
# the boundary (session open 10:00: 578 trades, -Rs 984/trade, 109% of the
# first hour's loss; same mechanism as the 14:00 blackout-release burst,
# -Rs 915/trade). Confirmed net-negative first hour in 4/4 completed sweep
# runs. Moving the boundary relocates the burst; this gate removes it:
#
#   "require_fresh_entry": false   (SCALP_V1; default OFF)
#
# When ON, an entry requires cond_all TRUE on this candle AND FALSE on the
# previous candle — a fresh flip, not a stale carry. Post-exit re-entries
# stay allowed: cond_all embeds no_open_trade, so it reads false while in a
# trade and flips true after the exit — a legitimate fresh transition.
# State is tracked EVERY candle (before any early return), per symbol, in
# the engine instance — live and backtest identically (parity by
# construction). LIVE-SHARED file (strategy_engine.py): config-gated,
# default OFF, zero behavior change until enabled; non-trading-day rebuild.
#
# PREREQ: SCALP_V1_EMA_GATE_20260824. Idempotent. Run from repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_FRESH_ENTRY_20260824"
PREREQ = "SCALP_V1_EMA_GATE_20260824"
ROOT = Path(__file__).resolve().parent
SE_REL = "app/engine/strategy_engine.py"
LD_REL = "app/config/strategy_loader.py"
SRC = ROOT / "frontend" / "src"
BT_JSX = SRC / "pages" / "Backtest.jsx"
QU_JSX = SRC / "pages" / "backtest" / "BacktestQueue.jsx"
RC_JSX = SRC / "pages" / "backtest" / "RunComparison.jsx"
SW_JSX = SRC / "pages" / "backtest" / "SweepBuilder.jsx"

TREES = [ROOT / "backend"]
_d = ROOT / "desktop" / "src-tauri" / "backend"
if (_d / SE_REL).exists():
    TREES.append(_d)


def _die(m):
    print(f"ABORT: {m}")
    sys.exit(1)


def _ro(t, old, new, label):
    n = t.count(old)
    if n != 1:
        _die(f"anchor '{label}' matched {n} times (want 1) — NOTHING written")
    return t.replace(old, new, 1)


# ═══ strategy_engine.py ════════════════════════════════════════════════════

# state tracked every candle, before ANY early return path
S1_OLD = """        # ── SYNC in_trade WITH RECORDED TRUTH ─────────────────
        self._refresh_in_trade()"""
S1_NEW = '''        # ── SYNC in_trade WITH RECORDED TRUTH ─────────────────
        self._refresh_in_trade()

        # ── SCALP_V1_FRESH_ENTRY_20260824 ── freshness state, tracked EVERY
        # candle regardless of which path returns below: was cond_all already
        # true on the PREVIOUS candle? (cond_all embeds no_open_trade, so an
        # exit makes the next true reading a legitimate fresh flip.)
        _cond_was = getattr(self, "_prev_cond_all", False)
        self._prev_cond_all = bool(conditions.get("cond_all", False))'''

S2_OLD = """        eg_enabled   = False    # ── SCALP_V1_EMA_GATE_20260824 ── fail-safe
        eg_min_slope = 0.0      #    defaults survive a failed config read
        tp_mult      = 1.0      #    (outer except leaves them inert)"""
S2_NEW = """        eg_enabled   = False    # ── SCALP_V1_EMA_GATE_20260824 ── fail-safe
        eg_min_slope = 0.0      #    defaults survive a failed config read
        tp_mult      = 1.0      #    (outer except leaves them inert)
        fresh_req    = False    # ── SCALP_V1_FRESH_ENTRY_20260824 ── fail-safe"""

S3_OLD = """            except (TypeError, ValueError):
                tp_mult = 1.0"""
S3_NEW = """            except (TypeError, ValueError):
                tp_mult = 1.0
            fresh_req = bool(cfg.get("require_fresh_entry", False))   # ── SCALP_V1_FRESH_ENTRY_20260824 ──"""

S4_OLD = """        if eg_enabled:
            _gate_slope = (ind.values or {}).get("gate_ema_slope")
            if _gate_slope is None or _gate_slope > -eg_min_slope:
                return signal"""
S4_NEW = '''        if eg_enabled:
            _gate_slope = (ind.values or {}).get("gate_ema_slope")
            if _gate_slope is None or _gate_slope > -eg_min_slope:
                return signal

        # ── SCALP_V1_FRESH_ENTRY_20260824 ── block entries whose conditions
        # were ALREADY true on the prior candle: kills the synchronized burst
        # at ANY boundary (session open, blackout end) instead of moving it.
        # No per-candle audit log — fires every candle; blocked bursts are
        # visible as the missing first-minutes entries in run counts.
        if fresh_req and _cond_was:
            return signal'''

# ═══ strategy_loader.py ════════════════════════════════════════════════════

L1_OLD = '''        "tp_multiplier": 1.0,
        # ── SCALP_V1_BT_FILTERS_20260823 END ──'''
L1_NEW = '''        "tp_multiplier": 1.0,
        # ── SCALP_V1_FRESH_ENTRY_20260824 ── entries require cond_all to
        # flip true THIS candle (kills boundary bursts). Off = classic.
        "require_fresh_entry": False,
        # ── SCALP_V1_BT_FILTERS_20260823 END ──'''

# ═══ Backtest.jsx ══════════════════════════════════════════════════════════

J1_OLD = "  const [v1TpMult, setV1TpMult] = useState(saved.v1TpMult ?? 1);"
J1_NEW = """  const [v1TpMult, setV1TpMult] = useState(saved.v1TpMult ?? 1);
  // ── SCALP_V1_FRESH_ENTRY_20260824 ──
  const [v1FreshEntry, setV1FreshEntry] = useState(saved.v1FreshEntry ?? false);"""

J2_OLD = "      v1EmaGate, v1EmaPeriod, v1EmaLookback, v1EmaMinSlope, v1TpMult });   // ── SCALP_V1_EMA_GATE_20260824 ──"
J2_NEW = "      v1EmaGate, v1EmaPeriod, v1EmaLookback, v1EmaMinSlope, v1TpMult,   // ── SCALP_V1_EMA_GATE_20260824 ──\n      v1FreshEntry });   // ── SCALP_V1_FRESH_ENTRY_20260824 ──"

J3_OLD = "      v1EmaGate, v1EmaPeriod, v1EmaLookback, v1EmaMinSlope, v1TpMult]);   // ── SCALP_V1_EMA_GATE_20260824 ── stale-closure rule: saveParams reads them, so they land here in the SAME commit"
J3_NEW = "      v1EmaGate, v1EmaPeriod, v1EmaLookback, v1EmaMinSlope, v1TpMult,   // ── SCALP_V1_EMA_GATE_20260824 ──\n      v1FreshEntry]);   // ── SCALP_V1_FRESH_ENTRY_20260824 ── stale-closure rule: saveParams reads it, so it lands here in the SAME commit"

J4_OLD = """      if (Number(v1TpMult) > 0 && Number(v1TpMult) !== 1) cfg.tp_multiplier = Number(v1TpMult);
    }"""
J4_NEW = """      if (Number(v1TpMult) > 0 && Number(v1TpMult) !== 1) cfg.tp_multiplier = Number(v1TpMult);
      if (v1FreshEntry) cfg.require_fresh_entry = true;   // ── SCALP_V1_FRESH_ENTRY_20260824 ── omit-when-off
    }"""

J5_OLD = "      v1EmaGate, v1EmaPeriod, v1EmaLookback, v1EmaMinSlope, v1TpMult]);   // ── SCALP_V1_EMA_GATE_20260824 ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"
J5_NEW = "      v1EmaGate, v1EmaPeriod, v1EmaLookback, v1EmaMinSlope, v1TpMult,   // ── SCALP_V1_EMA_GATE_20260824 ──\n      v1FreshEntry]);   // ── SCALP_V1_FRESH_ENTRY_20260824 ── stale-closure rule: buildConfig reads it, so it lands here in the SAME commit"

J6_OLD = '              <Field label="TP multiplier"><input type="number" min="0.5" step="0.1" style={inputStyle} value={v1TpMult} onChange={(e) => setV1TpMult(e.target.value)} /></Field>'
J6_NEW = '''              <Field label="TP multiplier"><input type="number" min="0.5" step="0.1" style={inputStyle} value={v1TpMult} onChange={(e) => setV1TpMult(e.target.value)} /></Field>
              {/* ── SCALP_V1_FRESH_ENTRY_20260824 ── only enter when the
                  conditions flipped true THIS candle; kills session-open
                  bursts at any boundary. */}
              <Field label="Fresh entry">
                <select style={inputStyle} value={v1FreshEntry ? "1" : "0"} onChange={(e) => setV1FreshEntry(e.target.value === "1")}>
                  <option value="0">Off</option>
                  <option value="1">On</option>
                </select>
              </Field>'''

J7_OLD = '  if (Number(cfg.tp_multiplier) > 0 && Number(cfg.tp_multiplier) !== 1) add("TP mult", `${cfg.tp_multiplier}×`);'
J7_NEW = '''  if (Number(cfg.tp_multiplier) > 0 && Number(cfg.tp_multiplier) !== 1) add("TP mult", `${cfg.tp_multiplier}×`);
  if (cfg.require_fresh_entry) add("Fresh entry", "on");   // ── SCALP_V1_FRESH_ENTRY_20260824 ── RUN_PARAMS_DISPLAY tripwire'''

Q1_OLD = "  if (Number(cfg.tp_multiplier) > 0 && Number(cfg.tp_multiplier) !== 1) p.push(`tpX${cfg.tp_multiplier}`);"
Q1_NEW = """  if (Number(cfg.tp_multiplier) > 0 && Number(cfg.tp_multiplier) !== 1) p.push(`tpX${cfg.tp_multiplier}`);
  if (cfg.require_fresh_entry) p.push("fresh");   // ── SCALP_V1_FRESH_ENTRY_20260824 ──"""

C1_OLD = '''  { key: "tp_mult",          label: "TP multiplier",  get: (r) => (Number(r.config?.tp_multiplier) > 0 && Number(r.config?.tp_multiplier) !== 1 ? `${r.config.tp_multiplier}×` : null) },'''
C1_NEW = '''  { key: "tp_mult",          label: "TP multiplier",  get: (r) => (Number(r.config?.tp_multiplier) > 0 && Number(r.config?.tp_multiplier) !== 1 ? `${r.config.tp_multiplier}×` : null) },
  { key: "fresh_entry",      label: "Fresh entry",    get: (r) => (r.config?.require_fresh_entry ? "on" : null) },   // ── SCALP_V1_FRESH_ENTRY_20260824 ──'''

W1_OLD = """  { key: "v1_tp_mult", label: "V1 TP multiplier", strategies: [V1],
    hint: "1, 1.5, 2, 2.5", parse: _num,
    apply: (c, v) => { if (v > 0 && v !== 1) c.tp_multiplier = v; }, fmt: (v) => (v !== 1 ? `tpX${v}` : "tpX1") },"""
W1_NEW = """  { key: "v1_tp_mult", label: "V1 TP multiplier", strategies: [V1],
    hint: "1, 1.5, 2, 2.5", parse: _num,
    apply: (c, v) => { if (v > 0 && v !== 1) c.tp_multiplier = v; }, fmt: (v) => (v !== 1 ? `tpX${v}` : "tpX1") },
  // ── SCALP_V1_FRESH_ENTRY_20260824 ── 0/1 axis.
  { key: "v1_fresh", label: "V1 fresh entry (0/1)", strategies: [V1],
    hint: "0, 1", parse: _num,
    apply: (c, v) => { if (v) c.require_fresh_entry = true; }, fmt: (v) => (v ? "fresh" : "stale-ok") },"""


def main():
    if not (ROOT / "backend" / SE_REL).exists():
        _die("run from the scalp-app repo root")
    staged = []
    for tree in TREES:
        se_p, ld_p = tree / SE_REL, tree / LD_REL
        se, ld = se_p.read_text(), ld_p.read_text()
        if FENCE in se or FENCE in ld:
            _die(f"fence {FENCE} already present under {tree} — already applied")
        if PREREQ not in se:
            _die(f"prerequisite fence {PREREQ} MISSING in {se_p}")
        se = _ro(se, S1_OLD, S1_NEW, f"{tree.name}:S1")
        se = _ro(se, S2_OLD, S2_NEW, f"{tree.name}:S2")
        se = _ro(se, S3_OLD, S3_NEW, f"{tree.name}:S3")
        se = _ro(se, S4_OLD, S4_NEW, f"{tree.name}:S4")
        ld = _ro(ld, L1_OLD, L1_NEW, f"{tree.name}:L1")
        staged.append((se_p, se))
        staged.append((ld_p, ld))
    for path, edits in [
        (BT_JSX, [("J1", J1_OLD, J1_NEW), ("J2", J2_OLD, J2_NEW), ("J3", J3_OLD, J3_NEW),
                  ("J4", J4_OLD, J4_NEW), ("J5", J5_OLD, J5_NEW), ("J6", J6_OLD, J6_NEW),
                  ("J7", J7_OLD, J7_NEW)]),
        (QU_JSX, [("Q1", Q1_OLD, Q1_NEW)]),
        (RC_JSX, [("C1", C1_OLD, C1_NEW)]),
        (SW_JSX, [("W1", W1_OLD, W1_NEW)]),
    ]:
        t = path.read_text()
        if FENCE in t:
            _die(f"fence {FENCE} already present in {path.name} — already applied")
        for label, old, new in edits:
            t = _ro(t, old, new, f"{path.name}:{label}")
        staged.append((path, t))
    for path, text in staged:
        if path.suffix == ".py":
            try:
                compile(text, str(path), "exec")
            except SyntaxError as e:
                _die(f"staged content for {path} does not compile: {e}")
    for path, text in staged:
        path.write_text(text)
        print(f"PATCHED: {path}")
    print(f"\nDONE — fence {FENCE} applied. Default OFF; baseline unchanged.")
    print("Live-shared file touched (strategy_engine) — non-trading-day rebuild.")
    print("Next: re-run the two sweep leaders with Fresh entry = On; judge on")
    print("worst-year net. The first-hour red bar should collapse toward the")
    print("+Rs 48K the 10:05+ population already shows.")


if __name__ == "__main__":
    main()
