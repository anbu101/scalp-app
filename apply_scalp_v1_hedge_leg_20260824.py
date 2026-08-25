#!/usr/bin/env python3
# apply_scalp_v1_hedge_leg_20260824.py
#
# D11 — OPTIONAL HEDGE LEG — fence: SCALP_V1_HEDGE_LEG_20260824 (backtest-only)
#
# Config (SCALP_V1): "hedge_leg": {"enabled": false, "max_premium": 8.0}
# On every main-leg SHORT entry, BUY a protective hedge: same option type
# (CE/PE), same expiry, the HIGHEST-premium contract quoting <= max_premium
# at the entry candle (TSG V1 semantics: best protection per rupee), same
# qty. Hedge is sold at its price on the main leg's exit candle (SL/TP/EOD/
# MTM). Hedge P&L and charges FOLD INTO the main trade's pnl/charges/net so
# every existing report/comparison stays directly comparable (D11.2).
# No candidate <= max_premium with data at the entry minute -> trade runs
# UNHEDGED with an audit line (fail-open; the validated main strategy is
# never blocked). Missing exit price -> last known price, else scratch at
# cost (audited). Charges use charges_for_long_trade — the codebase's exact-purpose
# model for LONG hedge legs (STT on the exit side). No approximation.
# NOTE (D11.3): backtests can only measure the hedge's COST — the margin
# benefit is live-side. Judge results as drag-vs-margin-release.
#
# PREREQ: SCALP_V1_MTM_STOP_20260824. Idempotent. Run from repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_HEDGE_LEG_20260824"
PREREQ = "SCALP_V1_MTM_STOP_20260824"
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


R1_OLD = """    except (TypeError, ValueError):
        mtm_limit = 0.0"""
R1_NEW = '''    except (TypeError, ValueError):
        mtm_limit = 0.0

    # ── SCALP_V1_HEDGE_LEG_20260824: config (D11) ──
    _hl = cfg.get("hedge_leg") or {}
    hedge_on = bool(_hl.get("enabled", False))
    try:
        hedge_max_prem = float(_hl.get("max_premium", 8.0) or 8.0)
        if hedge_max_prem <= 0:
            hedge_on = False
    except (TypeError, ValueError):
        hedge_on = False
        hedge_max_prem = 8.0'''

R2_OLD = "        day_mtm_halted = False    #    breach latch: no further entries today"
R2_NEW = '''        day_mtm_halted = False    #    breach latch: no further entries today
        # ── SCALP_V1_HEDGE_LEG_20260824: per-day hedge state + helpers ──
        open_hedges = {}      # main_sym -> (hedge_sym, hedge_entry_px, qty)
        _h_cache = {}         # hedge_sym -> {minute_ts: close}
        _h_universe = None    # lazy: contracts active this day

        def _h_prices(hsym):
            if hsym not in _h_cache:
                _h_cache[hsym] = {c.ts: c.close for c in
                                  src.candles_1m_for_symbol_day(hsym, day_start_epoch)}
            return _h_cache[hsym]

        def _pick_hedge(main_sym, sig_ts, m_qty):
            """Highest-premium same-type/same-expiry contract <= max_premium at
            the signal candle (TSG semantics). None -> run unhedged (audited)."""
            nonlocal _h_universe
            if _h_universe is None:
                _h_universe = src.contracts_active_on_day(underlying, day_start_epoch)
            opt_type = "CE" if main_sym.endswith("CE") else "PE"
            m_meta = next((c for c in _h_universe
                           if c.get("tradingsymbol") == main_sym), None)
            m_exp = m_meta.get("expiry") if m_meta else None
            best = None
            for c in _h_universe:
                hsym = c.get("tradingsymbol")
                if (not hsym or hsym == main_sym
                        or not hsym.endswith(opt_type)
                        or (m_exp and c.get("expiry") != m_exp)):
                    continue
                px = _h_prices(hsym).get(sig_ts)
                if px is None or px > hedge_max_prem:
                    continue
                if best is None or px > best[1]:
                    best = (hsym, px)
            if best is None:
                write_audit_log(
                    f"[BACKTEST][{strategy_id}][HEDGE] no contract <= "
                    f"{hedge_max_prem} at entry for {main_sym} — UNHEDGED")
                return None
            return (best[0], best[1], m_qty)

        def _settle_hedge(ct, sig_ts):
            """Sell the hedge at the exit candle; FOLD pnl+charges into ct."""
            h = open_hedges.pop(ct.symbol, None)
            if h is None:
                return
            hsym, h_in, h_qty = h
            prices = _h_prices(hsym)
            h_out = prices.get(sig_ts)
            if h_out is None:
                past = [t for t in prices if t <= sig_ts]
                if past:
                    h_out = prices[max(past)]
                else:
                    h_out = h_in   # scratch at cost — audited
                    write_audit_log(
                        f"[BACKTEST][{strategy_id}][HEDGE] no exit price for "
                        f"{hsym} — scratched at cost (fail-visible)")
            h_pnl = (h_out - h_in) * h_qty          # LONG leg
            try:
                # exact-purpose model: LONG hedge trade, STT on the EXIT leg
                from app.backtest.charges.charges_model import charges_for_long_trade
                h_chg = float(charges_for_long_trade(
                    entry_price=h_in, exit_price=h_out, qty=h_qty).total_charges)
            except Exception:
                h_chg = 0.0
                write_audit_log(
                    f"[BACKTEST][{strategy_id}][HEDGE] charges model unavailable "
                    f"for {hsym} — hedge charges recorded as 0 (fail-visible)")
            ct.pnl += h_pnl
            ct.charges += h_chg
            ct.net_pnl = ct.pnl - ct.charges
            write_audit_log(
                f"[BACKTEST][{strategy_id}][HEDGE] {ct.symbol} hedged by {hsym} "
                f"in={h_in} out={h_out} pnl={h_pnl:.0f} chg={h_chg:.0f} "
                f"(folded into trade)")'''

R3_OLD = """                                            ambiguous_fill=fr.ambiguous)
                        day_realized += _ct.pnl   # ── SCALP_V1_MTM_STOP_20260824 ──"""
R3_NEW = """                                            ambiguous_fill=fr.ambiguous)
                        _settle_hedge(_ct, ts)   # ── SCALP_V1_HEDGE_LEG_20260824 ──
                        day_realized += _ct.pnl   # ── SCALP_V1_MTM_STOP_20260824 ──"""

R4_OLD = """                                            exit_price=c.open,
                                            exit_reason="EOD",
                                            ambiguous_fill=False)
                        day_realized += _ct.pnl   # ── SCALP_V1_MTM_STOP_20260824 ──"""
R4_NEW = """                                            exit_price=c.open,
                                            exit_reason="EOD",
                                            ambiguous_fill=False)
                        _settle_hedge(_ct, ts)   # ── SCALP_V1_HEDGE_LEG_20260824 ──
                        day_realized += _ct.pnl   # ── SCALP_V1_MTM_STOP_20260824 ──"""

R5_OLD = """                                            exit_price=c.close,
                                            exit_reason="EOD",
                                            ambiguous_fill=False)
                        day_realized += _ct.pnl   # ── SCALP_V1_MTM_STOP_20260824 ──"""
R5_NEW = """                                            exit_price=c.close,
                                            exit_reason="EOD",
                                            ambiguous_fill=False)
                        _settle_hedge(_ct, ts)   # ── SCALP_V1_HEDGE_LEG_20260824 ──
                        day_realized += _ct.pnl   # ── SCALP_V1_MTM_STOP_20260824 ──"""

R6_OLD = """                                                    exit_reason="MTM",
                                                    ambiguous_fill=False)
                                day_realized += _ct.pnl"""
R6_NEW = """                                                    exit_reason="MTM",
                                                    ambiguous_fill=False)
                                _settle_hedge(_ct, ts)   # ── SCALP_V1_HEDGE_LEG_20260824 ──
                                day_realized += _ct.pnl"""

R7_OLD = """                    condition=diag))   # ── SCALP_V1_DIAG_20260823 ──
                day_entries += 1   # SCALP_V1_BT_FILTERS_20260823 (D2)"""
R7_NEW = """                    condition=diag))   # ── SCALP_V1_DIAG_20260823 ──
                # ── SCALP_V1_HEDGE_LEG_20260824: buy protection (fail-open) ──
                if hedge_on:
                    _h = _pick_hedge(sym, ts, _trade_qty)
                    if _h is not None:
                        open_hedges[sym] = _h
                day_entries += 1   # SCALP_V1_BT_FILTERS_20260823 (D2)"""

L1_OLD = '        "daily_max_mtm_loss": 0,'
L1_NEW = '''        "daily_max_mtm_loss": 0,
        # ── SCALP_V1_HEDGE_LEG_20260824 ── optional protective BUY on every
        # main entry: highest-premium same-type/expiry contract <= max_premium.
        # Backtest measures the hedge's COST; the margin benefit is live-side.
        "hedge_leg": {
            "enabled":     False,
            "max_premium": 8.0
        },'''

J1_OLD = "  const [v1MtmLoss, setV1MtmLoss] = useState(saved.v1MtmLoss ?? 0);"
J1_NEW = """  const [v1MtmLoss, setV1MtmLoss] = useState(saved.v1MtmLoss ?? 0);
  // ── SCALP_V1_HEDGE_LEG_20260824 ──
  const [v1Hedge, setV1Hedge] = useState(saved.v1Hedge ?? false);
  const [v1HedgeMaxPrem, setV1HedgeMaxPrem] = useState(saved.v1HedgeMaxPrem ?? 8);"""

J2_OLD = "      v1MtmLoss });   // ── SCALP_V1_MTM_STOP_20260824 ──"
J2_NEW = "      v1MtmLoss,   // ── SCALP_V1_MTM_STOP_20260824 ──\n      v1Hedge, v1HedgeMaxPrem });   // ── SCALP_V1_HEDGE_LEG_20260824 ──"

J3_OLD = "      v1MtmLoss]);   // ── SCALP_V1_MTM_STOP_20260824 ── stale-closure rule: saveParams reads it, so it lands here in the SAME commit"
J3_NEW = "      v1MtmLoss,   // ── SCALP_V1_MTM_STOP_20260824 ──\n      v1Hedge, v1HedgeMaxPrem]);   // ── SCALP_V1_HEDGE_LEG_20260824 ── stale-closure rule: saveParams reads them, so they land here in the SAME commit"

J4_OLD = """      if (Number(v1MtmLoss) > 0) cfg.daily_max_mtm_loss = Number(v1MtmLoss);   // ── SCALP_V1_MTM_STOP_20260824 ──
    }"""
J4_NEW = """      if (Number(v1MtmLoss) > 0) cfg.daily_max_mtm_loss = Number(v1MtmLoss);   // ── SCALP_V1_MTM_STOP_20260824 ──
      if (v1Hedge) cfg.hedge_leg = { enabled: true, max_premium: Number(v1HedgeMaxPrem) || 8 };   // ── SCALP_V1_HEDGE_LEG_20260824 ──
    }"""

J5_OLD = "      v1MtmLoss]);   // ── SCALP_V1_MTM_STOP_20260824 ── stale-closure rule: buildConfig reads it, so it lands here in the SAME commit"
J5_NEW = "      v1MtmLoss,   // ── SCALP_V1_MTM_STOP_20260824 ──\n      v1Hedge, v1HedgeMaxPrem]);   // ── SCALP_V1_HEDGE_LEG_20260824 ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"

J6_OLD = '              <Field label="Daily MTM stop ₹"><input type="number" min="0" step="5000" style={inputStyle} value={v1MtmLoss} onChange={(e) => setV1MtmLoss(e.target.value)} /></Field>'
J6_NEW = '''              <Field label="Daily MTM stop ₹"><input type="number" min="0" step="5000" style={inputStyle} value={v1MtmLoss} onChange={(e) => setV1MtmLoss(e.target.value)} /></Field>
              {/* ── SCALP_V1_HEDGE_LEG_20260824 ── protective buy for margin
                  benefit; backtest shows its COST. */}
              <Field label="Hedge leg">
                <select style={inputStyle} value={v1Hedge ? "1" : "0"} onChange={(e) => setV1Hedge(e.target.value === "1")}>
                  <option value="0">Off</option>
                  <option value="1">On (buy)</option>
                </select>
              </Field>
              {v1Hedge && (
                <Field label="Hedge max ₹"><input type="number" min="1" step="1" style={inputStyle} value={v1HedgeMaxPrem} onChange={(e) => setV1HedgeMaxPrem(e.target.value)} /></Field>
              )}'''

J7_OLD = '  if (Number(cfg.daily_max_mtm_loss) > 0) add("MTM stop", `₹${cfg.daily_max_mtm_loss}/day`);   // ── SCALP_V1_MTM_STOP_20260824 ──'
J7_NEW = '''  if (Number(cfg.daily_max_mtm_loss) > 0) add("MTM stop", `₹${cfg.daily_max_mtm_loss}/day`);   // ── SCALP_V1_MTM_STOP_20260824 ──
  if (cfg.hedge_leg?.enabled) add("Hedge", `buy ≤₹${cfg.hedge_leg.max_premium}`);   // ── SCALP_V1_HEDGE_LEG_20260824 ──'''

Q1_OLD = '  if (Number(cfg.daily_max_mtm_loss) > 0) p.push(`mtm${cfg.daily_max_mtm_loss/1000}k`);   // ── SCALP_V1_MTM_STOP_20260824 ──'
Q1_NEW = '''  if (Number(cfg.daily_max_mtm_loss) > 0) p.push(`mtm${cfg.daily_max_mtm_loss/1000}k`);   // ── SCALP_V1_MTM_STOP_20260824 ──
  if (cfg.hedge_leg?.enabled) p.push(`hdg${cfg.hedge_leg.max_premium}`);   // ── SCALP_V1_HEDGE_LEG_20260824 ──'''

C1_OLD = '''  { key: "mtm_stop",         label: "Daily MTM stop", get: (r) => (Number(r.config?.daily_max_mtm_loss) > 0 ? `₹${r.config.daily_max_mtm_loss}` : null) },   // ── SCALP_V1_MTM_STOP_20260824 ──'''
C1_NEW = '''  { key: "mtm_stop",         label: "Daily MTM stop", get: (r) => (Number(r.config?.daily_max_mtm_loss) > 0 ? `₹${r.config.daily_max_mtm_loss}` : null) },   // ── SCALP_V1_MTM_STOP_20260824 ──
  { key: "hedge_leg",        label: "Hedge leg",      get: (r) => (r.config?.hedge_leg?.enabled ? `buy ≤₹${r.config.hedge_leg.max_premium}` : null) },   // ── SCALP_V1_HEDGE_LEG_20260824 ──'''

W1_OLD = """  { key: "v1_mtm_stop", label: "V1 daily MTM stop ₹ (0=off)", strategies: [V1],
    hint: "0, 50000, 75000, 100000, 125000", parse: _num,
    apply: (c, v) => { if (v > 0) c.daily_max_mtm_loss = v; }, fmt: (v) => (v > 0 ? `mtm${v/1000}k` : "no mtm") },"""
W1_NEW = """  { key: "v1_mtm_stop", label: "V1 daily MTM stop ₹ (0=off)", strategies: [V1],
    hint: "0, 50000, 75000, 100000, 125000", parse: _num,
    apply: (c, v) => { if (v > 0) c.daily_max_mtm_loss = v; }, fmt: (v) => (v > 0 ? `mtm${v/1000}k` : "no mtm") },
  // ── SCALP_V1_HEDGE_LEG_20260824 ── 0 = no hedge; otherwise max premium ₹.
  { key: "v1_hedge", label: "V1 hedge max ₹ (0=off)", strategies: [V1],
    hint: "0, 5, 8, 12", parse: _num,
    apply: (c, v) => { if (v > 0) c.hedge_leg = { enabled: true, max_premium: v }; }, fmt: (v) => (v > 0 ? `hdg${v}` : "no hedge") },"""


def main():
    if not (ROOT / "backend" / RN_REL).exists():
        _die("run from the scalp-app repo root")
    staged = []
    for tree in TREES:
        rn_p, ld_p = tree / RN_REL, tree / LD_REL
        rn, ld = rn_p.read_text(), ld_p.read_text()
        if FENCE in rn or FENCE in ld:
            _die(f"fence {FENCE} already present under {tree}")
        if PREREQ not in rn:
            _die(f"prerequisite fence {PREREQ} MISSING in {rn_p}")
        for lab, o, n in [("R1", R1_OLD, R1_NEW), ("R2", R2_OLD, R2_NEW),
                          ("R3", R3_OLD, R3_NEW), ("R4", R4_OLD, R4_NEW),
                          ("R5", R5_OLD, R5_NEW), ("R6", R6_OLD, R6_NEW),
                          ("R7", R7_OLD, R7_NEW)]:
            rn = _ro(rn, o, n, f"{tree.name}:{lab}")
        ld = _ro(ld, L1_OLD, L1_NEW, f"{tree.name}:L1")
        staged.append((rn_p, rn))
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
    print(f"\nDONE — fence {FENCE} applied. Default OFF; the sealed 7/7 config")
    print("is unchanged until Hedge leg is enabled. First test: the locked")
    print("config with Hedge On @ ₹8 — read the result as ANNUAL HEDGE COST")
    print("per year, judged against live margin release, not as a P&L knob.")


if __name__ == "__main__":
    main()
