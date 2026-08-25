#!/usr/bin/env python3
# apply_scalp_v1_vwap_20260825.py
#
# VWAP FILTER — fence: SCALP_V1_VWAP_20260825
#
# Config (SCALP_V1): "vwap_filter": {"enabled": false, "min_below_pts": 0}
# Entry allowed only when the signal candle CLOSES BELOW the session VWAP of
# the option's own premium by at least min_below_pts (0 = any amount below).
# Premium below its volume-weighted average confirms supply-side control —
# what a short seller wants (VAP V1 fleet precedent). VWAP unavailable
# (zero cumulative volume, or still warming) -> BLOCK, per gate doctrine:
# no decision, no entry.
#
# VWAP is SESSION-ANCHORED: accumulation skips warmup candles (prior-day
# history) and resets on IST day rollover. In the backtest, per-day context
# rebuilds guarantee the reset regardless; the rollover logic covers
# long-lived live indicator instances. Exposed as values["vwap"], EXCLUDED
# from the ready latch (like the gate EMA). Diag snapshot gains "vw"
# (close - vwap at entry) so the losers-separation claim can be verified on
# data before trusting any filtered run.
#
# LIVE-SHARED files (indicator engine + strategy_engine): config-gated,
# default OFF, zero behavior change until enabled; non-trading-day rebuild.
# PREREQS: SCALP_V1_HEDGE_LEG_20260824 chain. Idempotent. Run from repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_VWAP_20260825"
ROOT = Path(__file__).resolve().parent
IND_REL = "app/engine/indicator_engine_pine_v1_9.py"
SE_REL = "app/engine/strategy_engine.py"
RN_REL = "app/backtest/runner/backtest_runner.py"
LD_REL = "app/config/strategy_loader.py"
SRC = ROOT / "frontend" / "src"
BT_JSX = SRC / "pages" / "Backtest.jsx"
QU_JSX = SRC / "pages" / "backtest" / "BacktestQueue.jsx"
RC_JSX = SRC / "pages" / "backtest" / "RunComparison.jsx"
SW_JSX = SRC / "pages" / "backtest" / "SweepBuilder.jsx"
TREES = [ROOT / "backend"]
_d = ROOT / "desktop" / "src-tauri" / "backend"
if (_d / IND_REL).exists():
    TREES.append(_d)


def _die(m):
    print(f"ABORT: {m}")
    sys.exit(1)


def _ro(t, o, n, lab):
    c = t.count(o)
    if c != 1:
        _die(f"anchor '{lab}' matched {c} times (want 1) — NOTHING written")
    return t.replace(o, n, 1)


# ═══ indicator engine ═══════════════════════════════════════════════════════

I1_OLD = "        self._gate_hist: deque = deque(maxlen=self._gate_lookback + 1)"
I1_NEW = '''        self._gate_hist: deque = deque(maxlen=self._gate_lookback + 1)
        # ── SCALP_V1_VWAP_20260825 ── session VWAP of the premium. Two
        # accumulators; warmup candles excluded; reset on IST day rollover.
        self._vwap_pv = 0.0
        self._vwap_v = 0.0
        self._vwap_day = None'''

I2_OLD = "        gate_val = gate_slope = None"
I2_NEW = '''        # ── SCALP_V1_VWAP_20260825 ── session-anchored VWAP accumulation.
        # No warmup flag needed: warmup candles are PRIOR days, so the IST
        # day-rollover reset below wipes them the moment today's first candle
        # arrives — session purity is guaranteed by the reset itself, in both
        # the backtest (per-day contexts) and long-lived live instances.
        # Typical price = (H+L+C)/3, volume-weighted; zero cum volume -> None.
        _cts = getattr(candle, "start_ts", None) or getattr(candle, "ts", None) \
               or getattr(candle, "end_ts", None)
        if _cts is not None:
            _cday = int((_cts + 19800) // 86400)   # IST day index
            if self._vwap_day != _cday:
                self._vwap_day = _cday
                self._vwap_pv = 0.0
                self._vwap_v = 0.0
        _vol = float(getattr(candle, "volume", 0) or 0)
        if _vol > 0:
            self._vwap_pv += ((h + l + c) / 3.0) * _vol
            self._vwap_v += _vol
        vwap_val = (self._vwap_pv / self._vwap_v) if self._vwap_v > 0 else None

        gate_val = gate_slope = None'''

I3_OLD = '''            "gate_ema_slope": gate_slope,
        }'''
I3_NEW = '''            "gate_ema_slope": gate_slope,
            # ── SCALP_V1_VWAP_20260825 ── excluded from the ready latch
            "vwap": vwap_val,
        }'''

# ═══ strategy_engine ════════════════════════════════════════════════════════

S1_OLD = "        fresh_req    = False    # ── SCALP_V1_FRESH_ENTRY_20260824 ── fail-safe"
S1_NEW = """        fresh_req    = False    # ── SCALP_V1_FRESH_ENTRY_20260824 ── fail-safe
        vw_enabled   = False    # ── SCALP_V1_VWAP_20260825 ── fail-safe defaults
        vw_min_below = 0.0"""

S2_OLD = '            fresh_req = bool(cfg.get("require_fresh_entry", False))   # ── SCALP_V1_FRESH_ENTRY_20260824 ──'
S2_NEW = '''            fresh_req = bool(cfg.get("require_fresh_entry", False))   # ── SCALP_V1_FRESH_ENTRY_20260824 ──
            # ── SCALP_V1_VWAP_20260825 ──
            _vw = cfg.get("vwap_filter") or {}
            vw_enabled = bool(_vw.get("enabled", False))
            try:
                vw_min_below = float(_vw.get("min_below_pts", 0.0) or 0.0)
            except (TypeError, ValueError):
                vw_min_below = 0.0'''

S3_OLD = """        if fresh_req and _cond_was:
            return signal"""
S3_NEW = '''        if fresh_req and _cond_was:
            return signal

        # ── SCALP_V1_VWAP_20260825 ── sell only when the premium closes BELOW
        # its session VWAP by >= min_below_pts. VWAP None (warming / zero
        # volume) -> BLOCK: no decision, no entry (gate doctrine).
        if vw_enabled:
            _vwap = (ind.values or {}).get("vwap")
            if _vwap is None or candle.close > _vwap - vw_min_below:
                return signal'''

# ═══ runner diag ════════════════════════════════════════════════════════════

R1_OLD = '''                    "gs": (_r2(ind_vals.get("gate_ema_slope"))
                           if ind_vals.get("gate_ema_slope") is not None else None),'''
R1_NEW = '''                    "gs": (_r2(ind_vals.get("gate_ema_slope"))
                           if ind_vals.get("gate_ema_slope") is not None else None),
                    # ── SCALP_V1_VWAP_20260825 ── close-minus-VWAP at entry
                    "vw": (_r2(c.close - ind_vals.get("vwap"))
                           if ind_vals.get("vwap") is not None else None),'''

# ═══ loader ═════════════════════════════════════════════════════════════════

L1_OLD = '''            "max_premium": 8.0
        },'''
L1_NEW = '''            "max_premium": 8.0
        },
        # ── SCALP_V1_VWAP_20260825 ── entry requires premium close BELOW its
        # session VWAP by >= min_below_pts (0 = any amount). Off = classic.
        "vwap_filter": {
            "enabled":       False,
            "min_below_pts": 0
        },'''

# ═══ UI ═════════════════════════════════════════════════════════════════════

J1_OLD = "  const [v1HedgeMaxPrem, setV1HedgeMaxPrem] = useState(saved.v1HedgeMaxPrem ?? 8);"
J1_NEW = """  const [v1HedgeMaxPrem, setV1HedgeMaxPrem] = useState(saved.v1HedgeMaxPrem ?? 8);
  // ── SCALP_V1_VWAP_20260825 ──
  const [v1Vwap, setV1Vwap] = useState(saved.v1Vwap ?? false);
  const [v1VwapMinBelow, setV1VwapMinBelow] = useState(saved.v1VwapMinBelow ?? 0);"""

J2_OLD = "      v1Hedge, v1HedgeMaxPrem });   // ── SCALP_V1_HEDGE_LEG_20260824 ──"
J2_NEW = "      v1Hedge, v1HedgeMaxPrem,   // ── SCALP_V1_HEDGE_LEG_20260824 ──\n      v1Vwap, v1VwapMinBelow });   // ── SCALP_V1_VWAP_20260825 ──"

J3_OLD = "      v1Hedge, v1HedgeMaxPrem]);   // ── SCALP_V1_HEDGE_LEG_20260824 ── stale-closure rule: saveParams reads them, so they land here in the SAME commit"
J3_NEW = "      v1Hedge, v1HedgeMaxPrem,   // ── SCALP_V1_HEDGE_LEG_20260824 ──\n      v1Vwap, v1VwapMinBelow]);   // ── SCALP_V1_VWAP_20260825 ── stale-closure rule: saveParams reads them, so they land here in the SAME commit"

J4_OLD = """      if (v1Hedge) cfg.hedge_leg = { enabled: true, max_premium: Number(v1HedgeMaxPrem) || 8 };   // ── SCALP_V1_HEDGE_LEG_20260824 ──
    }"""
J4_NEW = """      if (v1Hedge) cfg.hedge_leg = { enabled: true, max_premium: Number(v1HedgeMaxPrem) || 8 };   // ── SCALP_V1_HEDGE_LEG_20260824 ──
      if (v1Vwap) cfg.vwap_filter = { enabled: true, min_below_pts: Number(v1VwapMinBelow) || 0 };   // ── SCALP_V1_VWAP_20260825 ──
    }"""

J5_OLD = "      v1Hedge, v1HedgeMaxPrem]);   // ── SCALP_V1_HEDGE_LEG_20260824 ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"
J5_NEW = "      v1Hedge, v1HedgeMaxPrem,   // ── SCALP_V1_HEDGE_LEG_20260824 ──\n      v1Vwap, v1VwapMinBelow]);   // ── SCALP_V1_VWAP_20260825 ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"

J6_OLD = """              {v1Hedge && (
                <Field label="Hedge max ₹"><input type="number" min="1" step="1" style={inputStyle} value={v1HedgeMaxPrem} onChange={(e) => setV1HedgeMaxPrem(e.target.value)} /></Field>
              )}"""
J6_NEW = """              {v1Hedge && (
                <Field label="Hedge max ₹"><input type="number" min="1" step="1" style={inputStyle} value={v1HedgeMaxPrem} onChange={(e) => setV1HedgeMaxPrem(e.target.value)} /></Field>
              )}
              {/* ── SCALP_V1_VWAP_20260825 ── sell only below session VWAP of
                  the premium; min pts below (0 = any). */}
              <Field label="VWAP filter">
                <select style={inputStyle} value={v1Vwap ? "1" : "0"} onChange={(e) => setV1Vwap(e.target.value === "1")}>
                  <option value="0">Off</option>
                  <option value="1">On (below)</option>
                </select>
              </Field>
              {v1Vwap && (
                <Field label="Min pts below"><input type="number" min="0" step="0.5" style={inputStyle} value={v1VwapMinBelow} onChange={(e) => setV1VwapMinBelow(e.target.value)} /></Field>
              )}"""

J7_OLD = '  if (cfg.hedge_leg?.enabled) add("Hedge", `buy ≤₹${cfg.hedge_leg.max_premium}`);   // ── SCALP_V1_HEDGE_LEG_20260824 ──'
J7_NEW = '''  if (cfg.hedge_leg?.enabled) add("Hedge", `buy ≤₹${cfg.hedge_leg.max_premium}`);   // ── SCALP_V1_HEDGE_LEG_20260824 ──
  if (cfg.vwap_filter?.enabled) add("VWAP", `below${Number(cfg.vwap_filter.min_below_pts) > 0 ? ` ≥${cfg.vwap_filter.min_below_pts}` : ""}`);   // ── SCALP_V1_VWAP_20260825 ──'''

Q1_OLD = '  if (cfg.hedge_leg?.enabled) p.push(`hdg${cfg.hedge_leg.max_premium}`);   // ── SCALP_V1_HEDGE_LEG_20260824 ──'
Q1_NEW = '''  if (cfg.hedge_leg?.enabled) p.push(`hdg${cfg.hedge_leg.max_premium}`);   // ── SCALP_V1_HEDGE_LEG_20260824 ──
  if (cfg.vwap_filter?.enabled) p.push(`vwap${Number(cfg.vwap_filter.min_below_pts) > 0 ? cfg.vwap_filter.min_below_pts : ""}`);   // ── SCALP_V1_VWAP_20260825 ──'''

C1_OLD = '''  { key: "hedge_leg",        label: "Hedge leg",      get: (r) => (r.config?.hedge_leg?.enabled ? `buy ≤₹${r.config.hedge_leg.max_premium}` : null) },   // ── SCALP_V1_HEDGE_LEG_20260824 ──'''
C1_NEW = '''  { key: "hedge_leg",        label: "Hedge leg",      get: (r) => (r.config?.hedge_leg?.enabled ? `buy ≤₹${r.config.hedge_leg.max_premium}` : null) },   // ── SCALP_V1_HEDGE_LEG_20260824 ──
  { key: "vwap_filter",      label: "VWAP filter",    get: (r) => (r.config?.vwap_filter?.enabled ? `below ≥${r.config.vwap_filter.min_below_pts}` : null) },   // ── SCALP_V1_VWAP_20260825 ──'''

W1_OLD = """  { key: "v1_hedge", label: "V1 hedge max ₹ (0=off)", strategies: [V1],
    hint: "0, 5, 8, 12", parse: _num,
    apply: (c, v) => { if (v > 0) c.hedge_leg = { enabled: true, max_premium: v }; }, fmt: (v) => (v > 0 ? `hdg${v}` : "no hedge") },"""
W1_NEW = """  { key: "v1_hedge", label: "V1 hedge max ₹ (0=off)", strategies: [V1],
    hint: "0, 5, 8, 12", parse: _num,
    apply: (c, v) => { if (v > 0) c.hedge_leg = { enabled: true, max_premium: v }; }, fmt: (v) => (v > 0 ? `hdg${v}` : "no hedge") },
  // ── SCALP_V1_VWAP_20260825 ── -1 = off; 0 = below by any amount; >0 = min pts below.
  { key: "v1_vwap", label: "V1 VWAP min below (-1=off)", strategies: [V1],
    hint: "-1, 0, 2, 5", parse: _num,
    apply: (c, v) => { if (v >= 0) c.vwap_filter = { enabled: true, min_below_pts: v }; }, fmt: (v) => (v >= 0 ? `vwap≥${v}` : "no vwap") },"""


def main():
    if not (ROOT / "backend" / RN_REL).exists():
        _die("run from the scalp-app repo root")
    staged = []
    for tree in TREES:
        paths = {r: tree / r for r in (IND_REL, SE_REL, RN_REL, LD_REL)}
        tx = {r: p.read_text() for r, p in paths.items()}
        for r, t in tx.items():
            if FENCE in t:
                _die(f"fence {FENCE} already present in {paths[r]}")
        if "SCALP_V1_HEDGE_LEG_20260824" not in tx[RN_REL]:
            _die("prerequisite fence chain missing in runner")
        tx[IND_REL] = _ro(tx[IND_REL], I1_OLD, I1_NEW, f"{tree.name}:I1")
        tx[IND_REL] = _ro(tx[IND_REL], I2_OLD, I2_NEW, f"{tree.name}:I2")
        tx[IND_REL] = _ro(tx[IND_REL], I3_OLD, I3_NEW, f"{tree.name}:I3")
        tx[SE_REL] = _ro(tx[SE_REL], S1_OLD, S1_NEW, f"{tree.name}:S1")
        tx[SE_REL] = _ro(tx[SE_REL], S2_OLD, S2_NEW, f"{tree.name}:S2")
        tx[SE_REL] = _ro(tx[SE_REL], S3_OLD, S3_NEW, f"{tree.name}:S3")
        tx[RN_REL] = _ro(tx[RN_REL], R1_OLD, R1_NEW, f"{tree.name}:R1")
        tx[LD_REL] = _ro(tx[LD_REL], L1_OLD, L1_NEW, f"{tree.name}:L1")
        for r, t in tx.items():
            staged.append((paths[r], t))
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
            _die(f"fence {FENCE} already present in {path.name}")
        for lab, o, n in edits:
            t = _ro(t, o, n, f"{path.name}:{lab}")
        staged.append((path, t))
    for p, t in staged:
        if p.suffix == ".py":
            try:
                compile(t, str(p), "exec")
            except SyntaxError as e:
                _die(f"staged content for {p} does not compile: {e}")
    for p, t in staged:
        p.write_text(t)
        print(f"PATCHED: {p}")
    print(f"\nDONE — fence {FENCE} applied. Default OFF; sealed config unchanged.")
    print("First run: sealed config + VWAP On @ 0 and a diag-only baseline —")
    print("the 'vw' column proves (or disproves) loser separation before any")
    print("threshold gets tuned. Bar unchanged: 7/7 + better worst-year + DD.")


if __name__ == "__main__":
    main()
