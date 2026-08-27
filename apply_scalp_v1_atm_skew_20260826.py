#!/usr/bin/env python3
# apply_scalp_v1_atm_skew_20260826.py
#
# D15 — ATM SKEW ENTRY FILTER — fence: SCALP_V1_ATM_SKEW_20260826
# (backtest-only, built against the CURRENT GitHub main)
#
# REQUEST: before a CE sell, require the ATM PE to be pricier than the ATM CE;
# before a PE sell, require the ATM CE to be pricier than the ATM PE.
#
#   "atm_skew_filter": {"enabled": false, "min_diff_pts": 0}
#
# WHAT IT ACTUALLY MEASURES — read before interpreting any result. Put-call
# parity says that at strike K, CE - PE = F - K (F = forward ≈ spot plus a few
# points of carry). So "ATM PE pricier than ATM CE" is, to first order,
# "spot is BELOW the ATM strike" — i.e. the CE being sold is OTM rather than
# ITM. That is a legitimate MONEYNESS filter (sell the side that is already
# out of the money), but it is NOT a sentiment or skew read: most of the
# signal is where spot happens to sit inside its 50-point strike bucket. The
# genuine skew content is the residual — the deviation from parity caused by
# real demand/supply — which is worth a few points at most.
# The diagnostics below record BOTH components so the question is settled by
# data rather than argument:
#     "sk" = ATM PE - ATM CE   (what the filter tests)
#     "sd" = spot - ATM strike (the parity/moneyness component)
# If "sk" and "-sd" track each other almost perfectly, the filter is a
# moneyness rule; whatever explanatory power survives after removing "sd" is
# the real skew.
#
# SEMANTICS
#  * ATM strike = the strike NEAREST spot that has BOTH a CE and a PE on the
#    traded contract's own expiry (no hardcoded 50-point step — the grid is
#    read from the day's universe, so it survives any step change).
#  * Prices are sampled at the SIGNAL CANDLE's minute; spot uses
#    src.spot_at(ts + 60), which is the freshest legal close at the decision
#    instant (the helper's own no-lookahead rule: a bar stamped T covers
#    [T, T+60)).
#  * FAIL-CLOSED: missing spot, missing ATM pair, or a missing price BLOCKS
#    the entry — same doctrine as the EMA and VWAP gates.
#  * Applied per CANDIDATE (side is known there), so when both a CE and a PE
#    candidate exist only the side the skew favours can be taken; the
#    condition is mutually exclusive, so it can never admit both.
#
# COST: one spot query per distinct signal minute (cached per day); option
# prices are served from the day preload. Negligible at ~6 trades/day.
#
# LIVE PARITY NOTE: this is backtest-only, like the D8.3 spread gate. Live
# would need the same two LTP samples at the ATM strike; that wiring is a
# separate fence, and only worth building if the filter earns its place.
#
# Idempotent. Run from the repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_ATM_SKEW_20260826"
PREREQ = "SCALP_V1_VWAP_20260825"
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
R1_OLD = "    # ── SCALP_V1_ENTRY_SIZING_20260823 END: config ──"
R1_NEW = '''    # ── SCALP_V1_ENTRY_SIZING_20260823 END: config ──

    # ── SCALP_V1_ATM_SKEW_20260826: config (D15) ──
    _sk = cfg.get("atm_skew_filter") or {}
    skew_on = bool(_sk.get("enabled", False))
    try:
        skew_min = float(_sk.get("min_diff_pts", 0.0) or 0.0)
    except (TypeError, ValueError):
        skew_min = 0.0'''

# ── R2: per-day state + helper ────────────────────────────────────────────
R2_OLD = "        _h_universe = None    # lazy: contracts active this day"
R2_NEW = '''        _h_universe = None    # lazy: contracts active this day
        # ── SCALP_V1_ATM_SKEW_20260826: per-day ATM state (D15) ──
        _sk_grid: Dict[str, tuple] = {}   # expiry -> (sorted strikes, {k: {CE,PE}})
        _sk_cache: Dict[tuple, object] = {}   # (minute_ts, expiry) -> (sk, sd) | None

        def _sk_build(expiry):
            """Strike grid for ONE expiry: only strikes carrying BOTH legs, so
            the ATM pair is always complete. Read from the day's universe —
            no hardcoded strike step."""
            if expiry not in _sk_grid:
                legs: Dict[float, dict] = {}
                for _m in meta_map.values():
                    if _m.get("expiry") != expiry:
                        continue
                    try:
                        _k = float(_m.get("strike") or 0)
                    except (TypeError, ValueError):
                        continue
                    if _k <= 0:
                        continue
                    legs.setdefault(_k, {})[_m.get("instrument_type")] = \\
                        _m.get("tradingsymbol")
                pairs = {k: v for k, v in legs.items() if "CE" in v and "PE" in v}
                _sk_grid[expiry] = (sorted(pairs), pairs)
            return _sk_grid[expiry]

        def _sk_at(sig_ts, expiry):
            """(ATM_PE - ATM_CE, spot - ATM_strike) at this minute, or None when
            it cannot be measured (spot gap, no complete ATM pair, missing
            print). None BLOCKS the entry at the call site — fail-closed."""
            key = (sig_ts, expiry)
            if key in _sk_cache:
                return _sk_cache[key]
            out = None
            # +60: the decision is taken at the candle CLOSE, and spot_at's
            # own no-lookahead rule returns the bar stamped at-or-before
            # (arg - 60) — so this is the freshest LEGAL spot close.
            spot = src.spot_at(underlying, int(sig_ts) + 60)
            if spot is not None:
                strikes, pairs = _sk_build(expiry)
                if strikes:
                    k = min(strikes, key=lambda s: (abs(s - spot), s))
                    ce_px = src.option_premium_at(pairs[k]["CE"], sig_ts)
                    pe_px = src.option_premium_at(pairs[k]["PE"], sig_ts)
                    if ce_px is not None and pe_px is not None:
                        out = (round(float(pe_px) - float(ce_px), 2),
                               round(float(spot) - float(k), 2))
            _sk_cache[key] = out
            return out'''

# ── R3: the gate + diagnostics ────────────────────────────────────────────
R3_OLD = '''                if (max_spread_pts > 0 and _e8 is not None
                        and _e20l is not None
                        and (_e8 - _e20l) > max_spread_pts):
                    continue
                _r2 = lambda v: round(v, 2)'''
R3_NEW = '''                if (max_spread_pts > 0 and _e8 is not None
                        and _e20l is not None
                        and (_e8 - _e20l) > max_spread_pts):
                    continue
                # ── SCALP_V1_ATM_SKEW_20260826: D15 ATM skew gate. Sell the
                # side the ATM pair prices as the cheaper one: a CE sell needs
                # ATM PE dearer than ATM CE (and vice-versa) by >= min_diff.
                # Unmeasurable -> BLOCK (fail-closed, as with the EMA/VWAP
                # gates). Recorded either way as "sk"/"sd" for analysis.
                _skv = _sk_at(ts, (meta_map.get(sym) or {}).get("expiry")) \\
                    if (skew_on or True) else None
                if skew_on:
                    if _skv is None:
                        continue
                    _diff = _skv[0] if sym.endswith("CE") else -_skv[0]
                    if _diff <= skew_min:
                        continue
                _r2 = lambda v: round(v, 2)'''

R4_OLD = '''                    "vw": (_r2(c.close - ind_vals.get("vwap"))
                           if ind_vals.get("vwap") is not None else None),
                }, separators=(",", ":"))'''
R4_NEW = '''                    "vw": (_r2(c.close - ind_vals.get("vwap"))
                           if ind_vals.get("vwap") is not None else None),
                    # ── SCALP_V1_ATM_SKEW_20260826 ── sk = ATM PE - ATM CE
                    # (what the filter tests); sd = spot - ATM strike (the
                    # put-call-parity component). Recorded even when the
                    # filter is OFF so separation can be tested BEFORE tuning.
                    "sk": (_skv[0] if _skv is not None else None),
                    "sd": (_skv[1] if _skv is not None else None),
                }, separators=(",", ":"))'''

# ── loader ────────────────────────────────────────────────────────────────
L1_OLD = '''        "vwap_filter": {'''
L1_NEW = '''        # ── SCALP_V1_ATM_SKEW_20260826 ── D15: sell only the side the ATM
        # pair prices as cheaper (CE sell needs ATM PE dearer, and vice
        # versa). Largely a MONEYNESS rule via put-call parity — see the
        # apply script's header before reading results.
        "atm_skew_filter": {
            "enabled":      False,
            "min_diff_pts": 0
        },
        "vwap_filter": {'''

# ── UI ────────────────────────────────────────────────────────────────────
J1_OLD = "  const [v1VwapMinBelow, setV1VwapMinBelow] = useState(saved.v1VwapMinBelow ?? 0);"
J1_NEW = """  const [v1VwapMinBelow, setV1VwapMinBelow] = useState(saved.v1VwapMinBelow ?? 0);
  // ── SCALP_V1_ATM_SKEW_20260826 ──
  const [v1AtmSkew, setV1AtmSkew] = useState(saved.v1AtmSkew ?? false);
  const [v1AtmSkewMin, setV1AtmSkewMin] = useState(saved.v1AtmSkewMin ?? 0);"""

J2_OLD = "      v1Vwap, v1VwapMinBelow });   // ── SCALP_V1_VWAP_20260825 ──"
J2_NEW = "      v1Vwap, v1VwapMinBelow,   // ── SCALP_V1_VWAP_20260825 ──\n      v1AtmSkew, v1AtmSkewMin });   // ── SCALP_V1_ATM_SKEW_20260826 ──"

J3_OLD = "      v1Vwap, v1VwapMinBelow]);   // ── SCALP_V1_VWAP_20260825 ── stale-closure rule: saveParams reads them, so they land here in the SAME commit"
J3_NEW = "      v1Vwap, v1VwapMinBelow,   // ── SCALP_V1_VWAP_20260825 ──\n      v1AtmSkew, v1AtmSkewMin]);   // ── SCALP_V1_ATM_SKEW_20260826 ── stale-closure rule: saveParams reads them, so they land here in the SAME commit"

J4_OLD = """      if (v1Vwap) cfg.vwap_filter = { enabled: true, min_below_pts: Number(v1VwapMinBelow) || 0 };   // ── SCALP_V1_VWAP_20260825 ──
    }"""
J4_NEW = """      if (v1Vwap) cfg.vwap_filter = { enabled: true, min_below_pts: Number(v1VwapMinBelow) || 0 };   // ── SCALP_V1_VWAP_20260825 ──
      if (v1AtmSkew) cfg.atm_skew_filter = { enabled: true, min_diff_pts: Number(v1AtmSkewMin) || 0 };   // ── SCALP_V1_ATM_SKEW_20260826 ──
    }"""

J5_OLD = "      v1Vwap, v1VwapMinBelow]);   // ── SCALP_V1_VWAP_20260825 ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"
J5_NEW = "      v1Vwap, v1VwapMinBelow,   // ── SCALP_V1_VWAP_20260825 ──\n      v1AtmSkew, v1AtmSkewMin]);   // ── SCALP_V1_ATM_SKEW_20260826 ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"

J6_OLD = '''              {v1Vwap && (
                <Field label="Min pts below"><input type="number" min="0" step="0.5" style={inputStyle} value={v1VwapMinBelow} onChange={(e) => setV1VwapMinBelow(e.target.value)} /></Field>
              )}'''
J6_NEW = '''              {v1Vwap && (
                <Field label="Min pts below"><input type="number" min="0" step="0.5" style={inputStyle} value={v1VwapMinBelow} onChange={(e) => setV1VwapMinBelow(e.target.value)} /></Field>
              )}
              {/* ── SCALP_V1_ATM_SKEW_20260826 ── CE sell needs ATM PE dearer
                  than ATM CE by >= min pts (PE sell mirrored). */}
              <Field label="ATM skew">
                <select style={inputStyle} value={v1AtmSkew ? "1" : "0"} onChange={(e) => setV1AtmSkew(e.target.value === "1")}>
                  <option value="0">Off</option>
                  <option value="1">On</option>
                </select>
              </Field>
              {v1AtmSkew && (
                <Field label="Min skew pts"><input type="number" min="0" step="0.5" style={inputStyle} value={v1AtmSkewMin} onChange={(e) => setV1AtmSkewMin(e.target.value)} /></Field>
              )}'''

J7_OLD = '  if (cfg.vwap_filter?.enabled) add("VWAP", `below${Number(cfg.vwap_filter.min_below_pts) > 0 ? ` ≥${cfg.vwap_filter.min_below_pts}` : ""}`);   // ── SCALP_V1_VWAP_20260825 ──'
J7_NEW = '''  if (cfg.vwap_filter?.enabled) add("VWAP", `below${Number(cfg.vwap_filter.min_below_pts) > 0 ? ` ≥${cfg.vwap_filter.min_below_pts}` : ""}`);   // ── SCALP_V1_VWAP_20260825 ──
  if (cfg.atm_skew_filter?.enabled) add("ATM skew", `≥${Number(cfg.atm_skew_filter.min_diff_pts) || 0}`);   // ── SCALP_V1_ATM_SKEW_20260826 ──'''

Q1_OLD = '  if (cfg.vwap_filter?.enabled) p.push(`vwap${Number(cfg.vwap_filter.min_below_pts) > 0 ? cfg.vwap_filter.min_below_pts : ""}`);   // ── SCALP_V1_VWAP_20260825 ──'
Q1_NEW = '''  if (cfg.vwap_filter?.enabled) p.push(`vwap${Number(cfg.vwap_filter.min_below_pts) > 0 ? cfg.vwap_filter.min_below_pts : ""}`);   // ── SCALP_V1_VWAP_20260825 ──
  if (cfg.atm_skew_filter?.enabled) p.push(`skew${Number(cfg.atm_skew_filter.min_diff_pts) || 0}`);   // ── SCALP_V1_ATM_SKEW_20260826 ──'''

C1_OLD = '''  { key: "vwap_filter",      label: "VWAP filter",    get: (r) => (r.config?.vwap_filter?.enabled ? `below ≥${r.config.vwap_filter.min_below_pts}` : null) },   // ── SCALP_V1_VWAP_20260825 ──'''
C1_NEW = '''  { key: "vwap_filter",      label: "VWAP filter",    get: (r) => (r.config?.vwap_filter?.enabled ? `below ≥${r.config.vwap_filter.min_below_pts}` : null) },   // ── SCALP_V1_VWAP_20260825 ──
  { key: "atm_skew",         label: "ATM skew",       get: (r) => (r.config?.atm_skew_filter?.enabled ? `≥${Number(r.config.atm_skew_filter.min_diff_pts) || 0}` : null) },   // ── SCALP_V1_ATM_SKEW_20260826 ──'''

W1_OLD = """  { key: "v1_vwap", label: "V1 VWAP min below (-1=off)", strategies: [V1],"""
W1_NEW = """  // ── SCALP_V1_ATM_SKEW_20260826 ── -1 = off; >= 0 = min skew points.
  { key: "v1_atm_skew", label: "V1 ATM skew min pts (-1=off)", strategies: [V1],
    hint: "-1, 0, 2, 5", parse: _num,
    apply: (c, v) => { if (v >= 0) c.atm_skew_filter = { enabled: true, min_diff_pts: v }; }, fmt: (v) => (v >= 0 ? `skew${v}` : "no skew") },
  { key: "v1_vwap", label: "V1 VWAP min below (-1=off)", strategies: [V1],"""


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
        for lab, o, n in [("R1", R1_OLD, R1_NEW), ("R2", R2_OLD, R2_NEW),
                          ("R3", R3_OLD, R3_NEW), ("R4", R4_OLD, R4_NEW)]:
            rt = _ro(rt, o, n, f"{tree.name}:{lab}")
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
    print(f"\nDONE — fence {FENCE} applied. Default OFF; sealed config unchanged.")
    print()
    print("PROTOCOL (same as every filter before it):")
    print(" 1. Sealed config, ATM skew OFF, full range. The export now carries")
    print("    sk (ATM PE - ATM CE) and sd (spot - ATM strike) on EVERY trade.")
    print(" 2. Upload it. I test whether sk separates winners from losers")
    print("    per-year, AND how much of sk is just -sd (put-call parity).")
    print(" 3. Only if separation survives that decomposition do we sweep")
    print("    min_diff {0, 2, 5}. Bar unchanged: 7/7 held, worst-year AND")
    print("    maxDD both better than the flagship, else the flagship stands.")


if __name__ == "__main__":
    main()
