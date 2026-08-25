#!/usr/bin/env python3
# apply_scalp_v1_ema_gate_20260824.py
#
# D10.1 + D10.2 — SCALP_V1 SIGNAL-LEVEL rework — fence: SCALP_V1_EMA_GATE_20260824
#
# D10.1 — CONFIGURABLE EMA SLOPE GATE (generic, default EMA144 per Anbu).
#   Rationale: the ceiling test (369 slices, 0 all-years-positive) proved no
#   filter over EXISTING features reaches the all-years bar; TMA_V2 cleared
#   that bar with an EMA144 slope regime gate. Ported here with TMA doctrine:
#   slope over a LOOKBACK window (single-bar deltas are noise) and an
#   unwarmed slope BLOCKS — no decision, no entry (fail closed).
#   Short-premium semantics: sell only when the gate EMA of the option
#   premium is FALLING by at least min_slope_pts over the lookback.
#   Config (SCALP_V1):
#     "ema_gate": {"enabled": false, "period": 144,
#                  "slope_lookback": 30, "min_slope_pts": 0.0}
#
# D10.2 — TP MULTIPLIER: "tp_multiplier": 1.0. TP = entry − risk_distance ×
#   mult (1.0 = exactly prev_red_low = today's behavior, byte-identical).
#   Raises per-trade capture so a gated, lower-frequency strategy can clear
#   the annual charge bill.
#
# LIVE-SHARED FILES TOUCHED (per D10 scope sign-off; config-gated, default
# OFF, zero behavior change until enabled; deploy class: non-trading day):
#   engine/indicator_engine_pine_v1_9.py  optional gate EMA + slope; READY
#                                         latch pinned to the six CORE keys
#                                         so a slow gate EMA can never delay
#                                         readiness for any strategy
#   engine/strategy_engine.py             gate check + TP multiplier
#   marketdata/zerodha_tick_engine.py     SCALP_V1 live indicator gets gate
#                                         params from config (inert while
#                                         enabled=false)
#   (scalp_v3/scalpv5/trade_engine indicator sites deliberately untouched —
#    no gate object is created when period is None: V1-only by design)
# BACKTEST/UI:
#   backtest/runner/backtest_runner.py    gate params from run cfg; diag
#                                         snapshot gains "gs" (gate slope)
#   config/strategy_loader.py             defaults
#   Backtest.jsx / Queue / Comparison / SweepBuilder  fields, chips, tokens,
#                                         rows, axes
#
# WARMUP HONESTY: gate EMA(P) needs ~P bars for its SMA seed plus the slope
# lookback before it emits a slope; the runner's warmup feed must exceed
# P + lookback or the gate blocks every entry (loudly visible as ~0 trades).
# With the current warmup depth, keep period ≤ 300.
#
# PREREQS: all six earlier fences in the runner. Idempotent. Run from root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_EMA_GATE_20260824"
PREREQS_RN = ["SCALP_V1_BT_FILTERS_20260823", "SCALP_V1_DIAG_20260823",
              "SCALP_V1_DETERMINISM_20260823", "SCALP_V1_PARALLEL_20260823",
              "SCALP_V1_ENTRY_SIZING_20260823", "SCALP_V1_SIZING_FLOATFIX_20260824"]
ROOT = Path(__file__).resolve().parent
IND_REL = "app/engine/indicator_engine_pine_v1_9.py"
SE_REL = "app/engine/strategy_engine.py"
TK_REL = "app/marketdata/zerodha_tick_engine.py"
RN_REL = "app/backtest/runner/backtest_runner.py"
LD_REL = "app/config/strategy_loader.py"
SRC = ROOT / "frontend" / "src"
BT_JSX = SRC / "pages" / "Backtest.jsx"
QU_JSX = SRC / "pages" / "backtest" / "BacktestQueue.jsx"
RC_JSX = SRC / "pages" / "backtest" / "RunComparison.jsx"
SW_JSX = SRC / "pages" / "backtest" / "SweepBuilder.jsx"

TREES = [ROOT / "backend"]
_desktop = ROOT / "desktop" / "src-tauri" / "backend"
if (_desktop / IND_REL).exists():
    TREES.append(_desktop)


def _die(msg):
    print(f"ABORT: {msg}")
    sys.exit(1)


def _ro(text, old, new, label):
    n = text.count(old)
    if n != 1:
        _die(f"anchor '{label}' matched {n} times (want 1) — NOTHING written")
    return text.replace(old, new, 1)


# ═══ indicator_engine_pine_v1_9.py ═════════════════════════════════════════

I1_OLD = "from typing import Optional, List"
I1_NEW = ("from collections import deque   # ── SCALP_V1_EMA_GATE_20260824 ──\n"
          "from typing import Optional, List")

I2_OLD = "    def __init__(self):"
I2_NEW = '''    def __init__(self, gate_ema_period: Optional[int] = None,
                 gate_slope_lookback: int = 30):
        # ── SCALP_V1_EMA_GATE_20260824 ── optional configurable-period gate
        # EMA (D10.1). period=None (every existing call site) creates NOTHING:
        # zero overhead, zero behavior change for V3/V5/trade_engine users.
        self._gate_period = int(gate_ema_period) if gate_ema_period else None
        self._gate_lookback = max(1, int(gate_slope_lookback or 30))
        self.gate_ema = EMA(self._gate_period) if self._gate_period else None
        self._gate_hist: deque = deque(maxlen=self._gate_lookback + 1)'''

I3_OLD = """        # Store latest values
        self.values = {"""
I3_NEW = '''        # ── SCALP_V1_EMA_GATE_20260824 ── gate EMA + slope over lookback.
        # TMA_V2 doctrine: single-bar deltas are noise; slope is the delta
        # across the full lookback window, and is None until the window fills.
        gate_val = gate_slope = None
        if self.gate_ema is not None:
            gate_val = self.gate_ema.update(c)
            if gate_val is not None:
                self._gate_hist.append(gate_val)
                if len(self._gate_hist) == self._gate_hist.maxlen:
                    gate_slope = gate_val - self._gate_hist[0]

        # Store latest values
        self.values = {'''

I4_OLD = '''            "rsi_rising": rsi_rising,
        }'''
I4_NEW = '''            "rsi_rising": rsi_rising,
            # ── SCALP_V1_EMA_GATE_20260824 ── EXCLUDED from the ready latch
            "gate_ema": gate_val,
            "gate_ema_slope": gate_slope,
        }'''

I5_OLD = """        if not self.ready:
            self.ready = all(v is not None for v in self.values.values())"""
I5_NEW = '''        if not self.ready:
            # ── SCALP_V1_EMA_GATE_20260824 ── latch over the CORE keys only:
            # a slow gate EMA (e.g. 144) must never delay readiness for ANY
            # strategy sharing this engine. The gate itself fails closed on a
            # None slope at the signal site instead.
            _core = ("ema8", "ema20_low", "ema20_high",
                     "rsi_raw", "rsi_smoothed", "rsi_rising")
            self.ready = all(self.values.get(k) is not None for k in _core)'''

# ═══ strategy_engine.py ════════════════════════════════════════════════════

S1_OLD = """        risk_min_sl = self.RISK_MIN_SL
        risk_max_sl = self.RISK_MAX_SL
        rr          = self.MIN_RR
        max_sl_cap  = None"""
S1_NEW = """        risk_min_sl = self.RISK_MIN_SL
        risk_max_sl = self.RISK_MAX_SL
        rr          = self.MIN_RR
        max_sl_cap  = None
        eg_enabled   = False    # ── SCALP_V1_EMA_GATE_20260824 ── fail-safe
        eg_min_slope = 0.0      #    defaults survive a failed config read
        tp_mult      = 1.0      #    (outer except leaves them inert)"""

S2_OLD = '            max_sl_cap  = cfg.get("max_sl_points")'
S2_NEW = '''            max_sl_cap  = cfg.get("max_sl_points")
            # ── SCALP_V1_EMA_GATE_20260824 ── D10.1 gate + D10.2 TP mult
            _eg          = cfg.get("ema_gate") or {}
            eg_enabled   = bool(_eg.get("enabled", False))
            try:
                eg_min_slope = float(_eg.get("min_slope_pts", 0.0) or 0.0)
            except (TypeError, ValueError):
                eg_min_slope = 0.0
            try:
                tp_mult = float(cfg.get("tp_multiplier", 1.0) or 1.0)
                if tp_mult <= 0:
                    tp_mult = 1.0
            except (TypeError, ValueError):
                tp_mult = 1.0'''

S3_OLD = """        # ── Compute SL and TP for the SHORT trade ─────────────
        tp_price = prev_red_low
        sl_price = entry_price + (risk_distance * rr)"""
S3_NEW = '''        # ── SCALP_V1_EMA_GATE_20260824: D10.1 configurable EMA slope gate ──
        # Short-premium semantics: sell only when the gate EMA of the premium
        # is FALLING by ≥ min_slope_pts over the lookback. TMA_V2 warmup
        # doctrine: slope is None while the gate EMA/lookback warms → BLOCK
        # (no decision, no entry — fail closed). No per-candle audit log:
        # this fires every candle; blocked entries are visible in run counts.
        if eg_enabled:
            _gate_slope = (ind.values or {}).get("gate_ema_slope")
            if _gate_slope is None or _gate_slope > -eg_min_slope:
                return signal

        # ── Compute SL and TP for the SHORT trade ─────────────
        # SCALP_V1_EMA_GATE_20260824: D10.2 — TP may target beyond the
        # red-low structure. mult 1.0 ⇒ tp = entry − risk_distance =
        # prev_red_low EXACTLY (byte-identical to prior behavior).
        tp_price = entry_price - (risk_distance * tp_mult)
        sl_price = entry_price + (risk_distance * rr)'''

S4_OLD = 'f"  tp={tp_price:.2f}  (prev red low, {risk_distance:.2f} pts below)\\n"'
S4_NEW = 'f"  tp={tp_price:.2f}  ({risk_distance:.2f} pts × tpMult={tp_mult} below entry)\\n"'

# ═══ zerodha_tick_engine.py (SCALP_V1 live indicator site) ═════════════════

T1_OLD = "            indicator = IndicatorEnginePineV19()"
T1_NEW = '''            # ── SCALP_V1_EMA_GATE_20260824 ── gate params from SCALP_V1
            # config; enabled=false (the shipped default) constructs NO gate
            # object — live behavior byte-identical until enabled in Settings.
            _eg = {}
            try:
                from app.config.strategy_loader import load_strategy_config as _lsc
                _eg = (_lsc("SCALP_V1") or {}).get("ema_gate") or {}
            except Exception:
                _eg = {}
            indicator = IndicatorEnginePineV19(
                gate_ema_period=(int(_eg.get("period", 144) or 144)
                                 if _eg.get("enabled") else None),
                gate_slope_lookback=int(_eg.get("slope_lookback", 30) or 30))'''

# ═══ backtest_runner.py ════════════════════════════════════════════════════

R1_OLD = "        self.indicator = IndicatorEnginePineV19()"
R1_NEW = '''        # ── SCALP_V1_EMA_GATE_20260824 ── gate params from the run's
        # merged cfg (the BT_CONFIG_OVERRIDE token is installed before ctxs
        # are built, so load_strategy_config returns this run's overrides).
        from app.config.strategy_loader import load_strategy_config as _lsc
        _eg = (_lsc(self.engine.strategy_id) or {}).get("ema_gate") or {}
        self.indicator = IndicatorEnginePineV19(
            gate_ema_period=(int(_eg.get("period", 144) or 144)
                             if _eg.get("enabled") else None),
            gate_slope_lookback=int(_eg.get("slope_lookback", 30) or 30))'''

R2_OLD = '                    "rk": _r2(signal.sl - signal.entry_price),'
R2_NEW = '''                    "rk": _r2(signal.sl - signal.entry_price),
                    # ── SCALP_V1_EMA_GATE_20260824 ── gate slope at entry so
                    # the next ceiling analysis can slice on regime state.
                    "gs": (_r2(ind_vals.get("gate_ema_slope"))
                           if ind_vals.get("gate_ema_slope") is not None else None),'''

# ═══ strategy_loader.py ════════════════════════════════════════════════════

L1_OLD = '''        "entry_max_spread_points": 0,
        # ── SCALP_V1_BT_FILTERS_20260823 END ──'''
L1_NEW = '''        "entry_max_spread_points": 0,
        # ── SCALP_V1_EMA_GATE_20260824 ── D10.1 configurable-EMA slope gate
        # (default 144 per decision; any period the user wants) + D10.2 TP
        # multiplier. Both inert at these defaults.
        "ema_gate": {
            "enabled":        False,
            "period":         144,
            "slope_lookback": 30,
            "min_slope_pts":  0.0
        },
        "tp_multiplier": 1.0,
        # ── SCALP_V1_BT_FILTERS_20260823 END ──'''

# ═══ Backtest.jsx ══════════════════════════════════════════════════════════

J1_OLD = "  const [v1MaxSpread, setV1MaxSpread] = useState(saved.v1MaxSpread ?? 0);"
J1_NEW = """  const [v1MaxSpread, setV1MaxSpread] = useState(saved.v1MaxSpread ?? 0);
  // ── SCALP_V1_EMA_GATE_20260824 ── D10.1 gate + D10.2 TP multiplier.
  const [v1EmaGate, setV1EmaGate] = useState(saved.v1EmaGate ?? false);
  const [v1EmaPeriod, setV1EmaPeriod] = useState(saved.v1EmaPeriod ?? 144);
  const [v1EmaLookback, setV1EmaLookback] = useState(saved.v1EmaLookback ?? 30);
  const [v1EmaMinSlope, setV1EmaMinSlope] = useState(saved.v1EmaMinSlope ?? 0);
  const [v1TpMult, setV1TpMult] = useState(saved.v1TpMult ?? 1);"""

J2_OLD = "      v1RiskSizing, v1RupeeRisk, v1MaxSpread });   // ── SCALP_V1_ENTRY_SIZING_20260823 ──"
J2_NEW = "      v1RiskSizing, v1RupeeRisk, v1MaxSpread,   // ── SCALP_V1_ENTRY_SIZING_20260823 ──\n      v1EmaGate, v1EmaPeriod, v1EmaLookback, v1EmaMinSlope, v1TpMult });   // ── SCALP_V1_EMA_GATE_20260824 ──"

J3_OLD = "      v1RiskSizing, v1RupeeRisk, v1MaxSpread]);   // ── SCALP_V1_ENTRY_SIZING_20260823 ── stale-closure rule: saveParams reads them, so they land here in the SAME commit"
J3_NEW = "      v1RiskSizing, v1RupeeRisk, v1MaxSpread,   // ── SCALP_V1_ENTRY_SIZING_20260823 ──\n      v1EmaGate, v1EmaPeriod, v1EmaLookback, v1EmaMinSlope, v1TpMult]);   // ── SCALP_V1_EMA_GATE_20260824 ── stale-closure rule: saveParams reads them, so they land here in the SAME commit"

J4_OLD = """      if (Number(v1MaxSpread) > 0) cfg.entry_max_spread_points = Number(v1MaxSpread);
    }"""
J4_NEW = """      if (Number(v1MaxSpread) > 0) cfg.entry_max_spread_points = Number(v1MaxSpread);
      // ── SCALP_V1_EMA_GATE_20260824 ── omit-when-off / omit-when-1.
      if (v1EmaGate) cfg.ema_gate = { enabled: true, period: Number(v1EmaPeriod) || 144, slope_lookback: Number(v1EmaLookback) || 30, min_slope_pts: Number(v1EmaMinSlope) || 0 };
      if (Number(v1TpMult) > 0 && Number(v1TpMult) !== 1) cfg.tp_multiplier = Number(v1TpMult);
    }"""

J5_OLD = "      v1RiskSizing, v1RupeeRisk, v1MaxSpread]);   // ── SCALP_V1_ENTRY_SIZING_20260823 ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"
J5_NEW = "      v1RiskSizing, v1RupeeRisk, v1MaxSpread,   // ── SCALP_V1_ENTRY_SIZING_20260823 ──\n      v1EmaGate, v1EmaPeriod, v1EmaLookback, v1EmaMinSlope, v1TpMult]);   // ── SCALP_V1_EMA_GATE_20260824 ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"

J6_OLD = '              <Field label="Max spread pts"><input type="number" min="0" style={inputStyle} value={v1MaxSpread} onChange={(e) => setV1MaxSpread(e.target.value)} /></Field>'
J6_NEW = '''              <Field label="Max spread pts"><input type="number" min="0" style={inputStyle} value={v1MaxSpread} onChange={(e) => setV1MaxSpread(e.target.value)} /></Field>
              {/* ── SCALP_V1_EMA_GATE_20260824 ── D10.1: sell only when the
                  gate EMA of the premium falls ≥ min slope over the lookback;
                  unwarmed slope blocks (fail closed). D10.2: TP = risk × mult
                  below entry; 1 = classic prev-red-low target. */}
              <Field label="EMA gate">
                <select style={inputStyle} value={v1EmaGate ? "1" : "0"} onChange={(e) => setV1EmaGate(e.target.value === "1")}>
                  <option value="0">Off</option>
                  <option value="1">On</option>
                </select>
              </Field>
              {v1EmaGate && (
                <>
                  <Field label="Gate EMA period"><input type="number" min="10" max="300" style={inputStyle} value={v1EmaPeriod} onChange={(e) => setV1EmaPeriod(e.target.value)} /></Field>
                  <Field label="Slope lookback"><input type="number" min="1" style={inputStyle} value={v1EmaLookback} onChange={(e) => setV1EmaLookback(e.target.value)} /></Field>
                  <Field label="Min slope pts"><input type="number" min="0" step="0.1" style={inputStyle} value={v1EmaMinSlope} onChange={(e) => setV1EmaMinSlope(e.target.value)} /></Field>
                </>
              )}
              <Field label="TP multiplier"><input type="number" min="0.5" step="0.1" style={inputStyle} value={v1TpMult} onChange={(e) => setV1TpMult(e.target.value)} /></Field>'''

J7_OLD = '  if (Number(cfg.entry_max_spread_points) > 0) add("Max spread", `${cfg.entry_max_spread_points} pts`);'
J7_NEW = '''  if (Number(cfg.entry_max_spread_points) > 0) add("Max spread", `${cfg.entry_max_spread_points} pts`);
  // ── SCALP_V1_EMA_GATE_20260824 ── RUN_PARAMS_DISPLAY tripwires.
  if (cfg.ema_gate?.enabled) add("EMA gate", `${cfg.ema_gate.period}/${cfg.ema_gate.slope_lookback}b ≥${cfg.ema_gate.min_slope_pts}`);
  if (Number(cfg.tp_multiplier) > 0 && Number(cfg.tp_multiplier) !== 1) add("TP mult", `${cfg.tp_multiplier}×`);'''

# ═══ BacktestQueue.jsx ═════════════════════════════════════════════════════

Q1_OLD = "  if (Number(cfg.entry_max_spread_points) > 0) p.push(`sprd<${cfg.entry_max_spread_points}`);"
Q1_NEW = """  if (Number(cfg.entry_max_spread_points) > 0) p.push(`sprd<${cfg.entry_max_spread_points}`);
  // ── SCALP_V1_EMA_GATE_20260824 ──
  if (cfg.ema_gate?.enabled) p.push(`eGate ${cfg.ema_gate.period}/${cfg.ema_gate.slope_lookback}`);
  if (Number(cfg.tp_multiplier) > 0 && Number(cfg.tp_multiplier) !== 1) p.push(`tpX${cfg.tp_multiplier}`);"""

# ═══ RunComparison.jsx ═════════════════════════════════════════════════════

C1_OLD = '  { key: "max_spread",       label: "Max spread pts", get: (r) => (Number(r.config?.entry_max_spread_points) > 0 ? String(r.config.entry_max_spread_points) : null) },'
C1_NEW = '''  { key: "max_spread",       label: "Max spread pts", get: (r) => (Number(r.config?.entry_max_spread_points) > 0 ? String(r.config.entry_max_spread_points) : null) },
  // ── SCALP_V1_EMA_GATE_20260824 ──
  { key: "ema_gate",         label: "EMA gate",       get: (r) => (r.config?.ema_gate?.enabled ? `${r.config.ema_gate.period}/${r.config.ema_gate.slope_lookback}b ≥${r.config.ema_gate.min_slope_pts}` : null) },
  { key: "tp_mult",          label: "TP multiplier",  get: (r) => (Number(r.config?.tp_multiplier) > 0 && Number(r.config?.tp_multiplier) !== 1 ? `${r.config.tp_multiplier}×` : null) },'''

# ═══ SweepBuilder.jsx ══════════════════════════════════════════════════════

W1_OLD = """  { key: "v1_max_spread", label: "V1 max spread pts (0=off)", strategies: [V1],
    hint: "0, 12, 17, 22", parse: _num,
    apply: (c, v) => { if (v > 0) c.entry_max_spread_points = v; }, fmt: (v) => (v > 0 ? `sprd<${v}` : "no sprd gate") },"""
W1_NEW = """  { key: "v1_max_spread", label: "V1 max spread pts (0=off)", strategies: [V1],
    hint: "0, 12, 17, 22", parse: _num,
    apply: (c, v) => { if (v > 0) c.entry_max_spread_points = v; }, fmt: (v) => (v > 0 ? `sprd<${v}` : "no sprd gate") },
  // ── SCALP_V1_EMA_GATE_20260824 ── D10 axes. Gate period 0 = gate off;
  // lookback/min-slope ride the Backtest-page values via base config.
  { key: "v1_ema_gate_period", label: "V1 EMA gate period (0=off)", strategies: [V1],
    hint: "0, 89, 144, 200", parse: _num,
    apply: (c, v) => { if (v > 0) c.ema_gate = { ...(c.ema_gate || {}), enabled: true, period: v, slope_lookback: (c.ema_gate?.slope_lookback ?? 30), min_slope_pts: (c.ema_gate?.min_slope_pts ?? 0) }; }, fmt: (v) => (v > 0 ? `eGate${v}` : "no eGate") },
  { key: "v1_tp_mult", label: "V1 TP multiplier", strategies: [V1],
    hint: "1, 1.5, 2, 2.5", parse: _num,
    apply: (c, v) => { if (v > 0 && v !== 1) c.tp_multiplier = v; }, fmt: (v) => (v !== 1 ? `tpX${v}` : "tpX1") },"""


def main():
    if not (ROOT / "backend" / RN_REL).exists():
        _die("run from the scalp-app repo root")

    staged = []
    for tree in TREES:
        paths = {rel: tree / rel for rel in (IND_REL, SE_REL, TK_REL, RN_REL, LD_REL)}
        texts = {rel: p.read_text() for rel, p in paths.items()}
        for rel, t in texts.items():
            if FENCE in t:
                _die(f"fence {FENCE} already present in {paths[rel]} — already applied")
        for pf in PREREQS_RN:
            if pf not in texts[RN_REL]:
                _die(f"prerequisite fence {pf} MISSING in {paths[RN_REL]}")
        texts[IND_REL] = _ro(texts[IND_REL], I1_OLD, I1_NEW, f"{tree.name}:I1")
        texts[IND_REL] = _ro(texts[IND_REL], I2_OLD, I2_NEW, f"{tree.name}:I2")
        texts[IND_REL] = _ro(texts[IND_REL], I3_OLD, I3_NEW, f"{tree.name}:I3")
        texts[IND_REL] = _ro(texts[IND_REL], I4_OLD, I4_NEW, f"{tree.name}:I4")
        texts[IND_REL] = _ro(texts[IND_REL], I5_OLD, I5_NEW, f"{tree.name}:I5")
        texts[SE_REL] = _ro(texts[SE_REL], S1_OLD, S1_NEW, f"{tree.name}:S1")
        texts[SE_REL] = _ro(texts[SE_REL], S2_OLD, S2_NEW, f"{tree.name}:S2")
        texts[SE_REL] = _ro(texts[SE_REL], S3_OLD, S3_NEW, f"{tree.name}:S3")
        texts[SE_REL] = _ro(texts[SE_REL], S4_OLD, S4_NEW, f"{tree.name}:S4")
        texts[TK_REL] = _ro(texts[TK_REL], T1_OLD, T1_NEW, f"{tree.name}:T1")
        texts[RN_REL] = _ro(texts[RN_REL], R1_OLD, R1_NEW, f"{tree.name}:R1")
        texts[RN_REL] = _ro(texts[RN_REL], R2_OLD, R2_NEW, f"{tree.name}:R2")
        texts[LD_REL] = _ro(texts[LD_REL], L1_OLD, L1_NEW, f"{tree.name}:L1")
        for rel, t in texts.items():
            staged.append((paths[rel], t))

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
    print("DEPLOYMENT CLASS: shared live-path files touched (indicator engine,")
    print("strategy_engine, zerodha_tick_engine) — config-gated, default OFF,")
    print("zero behavior change until enabled. App REBUILD ships on a")
    print("non-trading day only. Local backtest testing is unaffected.")
    print()
    print("FIRST RUN (instrumentation before tuning): the champion config")
    print("(RR 1, maxSL 20) with EMA gate ON, period 144, lookback 30, min")
    print("slope 0 — plus one run gate OFF to confirm byte-identical baseline.")
    print("Every trade's diag now carries gs (gate slope); upload the gate-OFF")
    print("run's export and the ceiling analysis reruns WITH regime state —")
    print("wait: gs only records when the gate EMA is constructed, so use the")
    print("gate-ON run for the slice analysis and the gate-OFF run for the")
    print("baseline-identity check.")
    print()
    print("Then the D10 grid: eGate period {89, 144, 200} × min slope")
    print("{0, 0.5, 1} × tpX {1, 1.5, 2, 2.5} — objective: WORST-YEAR net.")


if __name__ == "__main__":
    main()
