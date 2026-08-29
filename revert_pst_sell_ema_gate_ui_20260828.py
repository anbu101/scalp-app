#!/usr/bin/env python3
# revert_pst_sell_ema_gate_ui_20260828.py
#
# ── REVERT of PST_SELL_EMA_GATE_20260828, FRONTEND ONLY ──
#
# WHY: the EMA slope regime gate was FALSIFIED by the 2x3 sweep (period
# 20/50 x min_slope 15/25/40) on the sealed base (levels PP+S1+S3+R3,
# expiry skip ON, confirm 4m). Every cell was worse than the base on net,
# max DD and net/DD; each bought a +54k..+156k improvement in 2024 by
# paying -114k..-774k across the other six years, at a near-constant
# -Rs1,950 per vetoed signal — the signature of a filter removing randomly
# selected profitable trades, not one removing a bad regime.
#
# SCOPE — UI ONLY, BY DESIGN:
#   * REVERTED: Backtest.jsx (state, LS payload + deps, buildConfig +
#     deps, describeConfig chip, UI fields), RunComparison.jsx PARAM_KEYS
#     row, BacktestQueue.jsx token, SweepBuilder.jsx axes.
#   * KEPT: the backend fence PST_SELL_EMA_GATE_20260828 in
#     backtest_pst_sell_runner.py. It is inert without the config key —
#     `_eg = cfg.get("ema_gate") or {}` yields enabled=False — so results
#     are byte-identical to the gate never existing, and the knob stays
#     available for a future retest via an explicit config_override.
#
# ARCHIVED RUNS: configs already persisted WITH an ema_gate block keep it;
# those runs simply lose the RunComparison row / queue token / chip that
# displayed it. No stored data is rewritten.
#
# Anchors are the exact strings the UI apply script INSERTED, so a partial
# or hand-edited application aborts unwritten rather than half-reverting.

import os

FENCE = "PST_SELL_EMA_GATE_20260828"
KEEP = "PST_SELL_CONFIRM_20260828"   # must survive the revert
REPO = os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app")
FRONT = os.environ.get("SCALP_FRONTEND", os.path.join(REPO, "frontend"))

BT = os.path.join(FRONT, "src", "pages", "Backtest.jsx")
RC = os.path.join(FRONT, "src", "pages", "backtest", "RunComparison.jsx")
BQ = os.path.join(FRONT, "src", "pages", "backtest", "BacktestQueue.jsx")
SB = os.path.join(FRONT, "src", "pages", "backtest", "SweepBuilder.jsx")


def _ro(src, old, new, tag):
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"ABORT [{tag}]: anchor found {n}x (need exactly 1). "
                         f"No files written.")
    return src.replace(old, new, 1)


def revert_backtest(src):
    if FENCE not in src:
        print("  Backtest.jsx: fence absent — already reverted (idempotent)")
        return src

    # B1 — state block
    old = f"""
  // ── {FENCE} ── EMA slope regime gate (veto fades against strong trends)
  const [pstEmaGateOn, setPstEmaGateOn] = useState(!!pstSaved.emaGateOn);
  const [pstEmaPeriod, setPstEmaPeriod] = useState(Number(pstSaved.emaPeriod) || 20);
  const [pstEmaLookback, setPstEmaLookback] = useState(Number(pstSaved.emaLookback) || 6);
  const [pstEmaMinSlope, setPstEmaMinSlope] = useState(Number(pstSaved.emaMinSlope) || 15);"""
    src = _ro(src, old, "", "B1 state")

    # B2 — LS payload
    old = ("confirmMin: pstConfirmMin, emaGateOn: pstEmaGateOn, "
           "emaPeriod: pstEmaPeriod, emaLookback: pstEmaLookback, "
           "emaMinSlope: pstEmaMinSlope })")
    src = _ro(src, old, "confirmMin: pstConfirmMin })", "B2 LS payload")

    # B3 — LS deps
    old = ("pstAllowedLevels, pstSkipExpiry, pstConfirmMin, pstEmaGateOn, "
           "pstEmaPeriod, pstEmaLookback, pstEmaMinSlope]);")
    src = _ro(src, old, "pstAllowedLevels, pstSkipExpiry, pstConfirmMin]);",
              "B3 LS deps")

    # B4 — buildConfig spread back to the confirm-era one-liner
    old = """        ...(sid === "PST_SELL" ? {
          allowed_levels: pstAllowedLevels,
          skip_expiry_day: !!pstSkipExpiry,
          confirm_minutes: Math.min(30, Math.max(0, Number(pstConfirmMin) || 0)),
          // ── """ + FENCE + """ ── always emitted so sweep axes have a
          // guarded object to mutate (VAP guard pattern)
          ema_gate: { enabled: !!pstEmaGateOn, period: Math.max(2, Number(pstEmaPeriod) || 20), slope_lookback: Math.max(1, Number(pstEmaLookback) || 6), min_slope: Number(pstEmaMinSlope) || 15 },
        } : {}),   // ── PST_SELL_CONFIRM_20260828 / """ + FENCE + """ ──"""
    new = """        ...(sid === "PST_SELL" ? { allowed_levels: pstAllowedLevels, skip_expiry_day: !!pstSkipExpiry, confirm_minutes: Math.min(30, Math.max(0, Number(pstConfirmMin) || 0)) } : {}),   // ── PST_SELL_CONFIRM_20260828 ──"""
    src = _ro(src, old, new, "B4 buildConfig")

    # B5 — buildConfig dep array
    old = f"""
      pstEmaGateOn, pstEmaPeriod, pstEmaLookback, pstEmaMinSlope,   // ── {FENCE} ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"""
    src = _ro(src, old, "", "B5 buildConfig deps")

    # B6 — describeConfig chip
    old = f"""
    if (cfg.ema_gate?.enabled) add("EMA gate", `${{cfg.ema_gate.period}}/${{cfg.ema_gate.slope_lookback}} ≥${{cfg.ema_gate.min_slope}}p`);   // ── {FENCE} ──"""
    src = _ro(src, old, "", "B6 chip")

    # B7 — UI fields
    old = """
                  {/* ── """ + FENCE + """ ── veto fades against strong
                      trends: CE sells blocked when EMA(period on 5m spot)
                      rose ≥ min_slope pts over lookback bars; PE mirrored.
                      Targets the residual 2024 Jan/Oct trend-month damage. */}
                  <Field label="EMA gate">
                    <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                      <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, cursor: "pointer" }}>
                        <input type="checkbox" checked={!!pstEmaGateOn} onChange={(e) => setPstEmaGateOn(e.target.checked)} />
                        on
                      </label>
                      <input type="number" min="2" step="1" style={{ ...inputStyle, width: 62 }} value={pstEmaPeriod}
                        onChange={(e) => setPstEmaPeriod(Math.max(2, Number(e.target.value) || 20))} title="EMA period (5m spot bars)" />
                      <input type="number" min="1" step="1" style={{ ...inputStyle, width: 56 }} value={pstEmaLookback}
                        onChange={(e) => setPstEmaLookback(Math.max(1, Number(e.target.value) || 6))} title="slope lookback (5m bars)" />
                      <input type="number" min="0" step="1" style={{ ...inputStyle, width: 62 }} value={pstEmaMinSlope}
                        onChange={(e) => setPstEmaMinSlope(Number(e.target.value) || 0)} title="min slope (spot points over the lookback) to veto" />
                      <span style={{ fontSize: 10, color: "#64748b" }}>period / lookback / ≥pts</span>
                    </div>
                  </Field>"""
    src = _ro(src, old, "", "B7 UI")
    return src


def revert_runcomparison(src):
    if FENCE not in src:
        print("  RunComparison.jsx: fence absent — already reverted (idempotent)")
        return src
    old = f"""
  {{ key: "pst_emagate", label: "EMA gate",   get: (r) => (r.config?.signal_tf && r.config?.ema_gate?.enabled ? `${{r.config.ema_gate.period}}/${{r.config.ema_gate.slope_lookback}}≥${{r.config.ema_gate.min_slope}}p` : null) }},   // ── {FENCE} ──"""
    return _ro(src, old, "", "RC PARAM_KEYS")


def revert_queue(src):
    if FENCE not in src:
        print("  BacktestQueue.jsx: fence absent — already reverted (idempotent)")
        return src
    old = f"""
  if (cfg.ema_gate?.enabled) p.push(`eG${{cfg.ema_gate.period}}/${{cfg.ema_gate.slope_lookback}}≥${{cfg.ema_gate.min_slope}}`);   // ── {FENCE} ── sweep rows must be tellable apart"""
    return _ro(src, old, "", "BQ paramLine")


def revert_sweepbuilder(src):
    if FENCE not in src:
        print("  SweepBuilder.jsx: fence absent — already reverted (idempotent)")
        return src
    old = f"""
  {{ key: "pst_ema_period", label: "EMA gate period", strategies: [PSTS],
    hint: "20, 50", parse: _num,
    apply: (c, v) => {{ if (c.ema_gate) {{ c.ema_gate.enabled = true; c.ema_gate.period = Math.max(2, v); }} }}, fmt: (v) => `eGp${{v}}` }},   // ── {FENCE} ── sweeping the gate implies gate ON
  {{ key: "pst_ema_slope", label: "EMA gate min slope (pts)", strategies: [PSTS],
    hint: "15, 25, 40", parse: _num,
    apply: (c, v) => {{ if (c.ema_gate) {{ c.ema_gate.enabled = true; c.ema_gate.min_slope = v; }} }}, fmt: (v) => `eGs${{v}}` }},   // ── {FENCE} ──"""
    return _ro(src, old, "", "SB axes")


def main():
    for p in (BT, RC, BQ, SB):
        if not os.path.isfile(p):
            raise SystemExit(f"ABORT: missing {p} — set SCALP_REPO/SCALP_FRONTEND.")
    bt, rc, bq, sb = open(BT).read(), open(RC).read(), open(BQ).read(), open(SB).read()
    bt2, rc2, bq2, sb2 = (revert_backtest(bt), revert_runcomparison(rc),
                          revert_queue(bq), revert_sweepbuilder(sb))
    # post-conditions: EMA UI gone everywhere, confirm layer intact
    for name, txt in (("Backtest.jsx", bt2), ("RunComparison.jsx", rc2),
                      ("BacktestQueue.jsx", bq2), ("SweepBuilder.jsx", sb2)):
        if FENCE in txt:
            raise SystemExit(f"ABORT: {FENCE} still present in {name} after "
                             f"revert. No files written.")
        if KEEP not in txt:
            raise SystemExit(f"ABORT: {KEEP} missing from {name} — the revert "
                             f"would remove the confirm layer. No files written.")
    # Leftover scan uses PST-SPECIFIC identifiers only. NOTE: the bare
    # string "ema_gate" is NOT a valid probe — SCALP_V1 owns its own
    # cfg.ema_gate (fence SCALP_V1_EMA_GATE_20260824) in these same files,
    # and scanning for it produces a false abort.
    for name, txt in (("Backtest.jsx", bt2), ("RunComparison.jsx", rc2),
                      ("BacktestQueue.jsx", bq2), ("SweepBuilder.jsx", sb2)):
        for leftover in ("pstEmaGateOn", "pstEmaPeriod", "pstEmaLookback",
                         "pstEmaMinSlope", "pst_ema_period", "pst_ema_slope",
                         "pst_emagate"):
            if leftover in txt:
                raise SystemExit(f"ABORT: leftover '{leftover}' in {name}. "
                                 f"No files written.")
    # SCALP_V1's gate must be untouched by this revert
    if "SCALP_V1_EMA_GATE_20260824" not in bt2:
        raise SystemExit("ABORT: SCALP_V1_EMA_GATE_20260824 vanished from "
                         "Backtest.jsx — wrong gate reverted. No files written.")
    for path, cur, new in ((BT, bt, bt2), (RC, rc, rc2), (BQ, bq, bq2), (SB, sb, sb2)):
        if new != cur:
            open(path, "w").write(new)
            print(f"  reverted {path}")
    print("DONE — reverted", FENCE, "(frontend only; backend fence kept, inert)")


if __name__ == "__main__":
    main()
