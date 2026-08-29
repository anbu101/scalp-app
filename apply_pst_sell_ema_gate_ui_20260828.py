#!/usr/bin/env python3
# apply_pst_sell_ema_gate_ui_20260828.py
#
# ── PST_SELL_EMA_GATE_20260828 ── frontend half. Backend half:
# apply_pst_sell_ema_gate_20260828.py. Applies ON TOP of the
# PST_SELL_CONFIRM_20260828 UI patch (anchors reference it).
#
# Adds to the PST_SELL filter row: "EMA gate" enable checkbox + period /
# slope-lookback / min-slope inputs → config key
# ema_gate: {enabled, period, slope_lookback, min_slope}.
# Gap sweep: describeConfig chip (Portfolio via prop), RunComparison
# PARAM_KEYS, BacktestQueue token, SweepBuilder axes (period & min_slope —
# sweeping either sets enabled=true, since a swept gate is an active gate;
# lookback stays a UI-set constant per sweep to keep axes independent).
# Stale-closure rule: buildConfig reads the four new states → LS deps + the
# buildConfig dep array gain them in this SAME change.

import os

FENCE = "PST_SELL_EMA_GATE_20260828"
PREV = "PST_SELL_CONFIRM_20260828"
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


def patch_backtest(src):
    if FENCE in src:
        print("  Backtest.jsx: fence present — skipping (idempotent)")
        return src
    if PREV not in src:
        raise SystemExit(f"ABORT: Backtest.jsx missing prerequisite {PREV}.")

    # B1 — state
    old = """  const [pstConfirmMin, setPstConfirmMin] = useState(Number(pstSaved.confirmMin) || 0);"""
    new = old + f"""
  // ── {FENCE} ── EMA slope regime gate (veto fades against strong trends)
  const [pstEmaGateOn, setPstEmaGateOn] = useState(!!pstSaved.emaGateOn);
  const [pstEmaPeriod, setPstEmaPeriod] = useState(Number(pstSaved.emaPeriod) || 20);
  const [pstEmaLookback, setPstEmaLookback] = useState(Number(pstSaved.emaLookback) || 6);
  const [pstEmaMinSlope, setPstEmaMinSlope] = useState(Number(pstSaved.emaMinSlope) || 15);"""
    src = _ro(src, old, new, "B1 state")

    # B2 — LS payload
    old = "confirmMin: pstConfirmMin })"
    new = ("confirmMin: pstConfirmMin, emaGateOn: pstEmaGateOn, "
           "emaPeriod: pstEmaPeriod, emaLookback: pstEmaLookback, "
           "emaMinSlope: pstEmaMinSlope })")
    src = _ro(src, old, new, "B2 LS payload")

    # B3 — LS deps (stale-closure rule)
    old = "pstAllowedLevels, pstSkipExpiry, pstConfirmMin]);"
    new = ("pstAllowedLevels, pstSkipExpiry, pstConfirmMin, pstEmaGateOn, "
           "pstEmaPeriod, pstEmaLookback, pstEmaMinSlope]);")
    src = _ro(src, old, new, "B3 LS deps")

    # B4 — buildConfig spread gains ema_gate (PST_SELL only)
    old = """        ...(sid === "PST_SELL" ? { allowed_levels: pstAllowedLevels, skip_expiry_day: !!pstSkipExpiry, confirm_minutes: Math.min(30, Math.max(0, Number(pstConfirmMin) || 0)) } : {}),   // ── PST_SELL_CONFIRM_20260828 ──"""
    new = """        ...(sid === "PST_SELL" ? {
          allowed_levels: pstAllowedLevels,
          skip_expiry_day: !!pstSkipExpiry,
          confirm_minutes: Math.min(30, Math.max(0, Number(pstConfirmMin) || 0)),
          // ── """ + FENCE + """ ── always emitted so sweep axes have a
          // guarded object to mutate (VAP guard pattern)
          ema_gate: { enabled: !!pstEmaGateOn, period: Math.max(2, Number(pstEmaPeriod) || 20), slope_lookback: Math.max(1, Number(pstEmaLookback) || 6), min_slope: Number(pstEmaMinSlope) || 15 },
        } : {}),   // ── PST_SELL_CONFIRM_20260828 / """ + FENCE + """ ──"""
    src = _ro(src, old, new, "B4 buildConfig")

    # B5 — buildConfig dep array (stale-closure rule)
    old = """      pstConfirmMin,   // ── PST_SELL_CONFIRM_20260828 ── stale-closure rule: buildConfig reads it, so it lands here in the SAME commit"""
    new = old + f"""
      pstEmaGateOn, pstEmaPeriod, pstEmaLookback, pstEmaMinSlope,   // ── {FENCE} ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"""
    src = _ro(src, old, new, "B5 buildConfig deps")

    # B6 — describeConfig chip
    old = """    if (Number(cfg.confirm_minutes) > 0) add("Confirm", `${cfg.confirm_minutes}m wait`);   // ── PST_SELL_CONFIRM_20260828 ──"""
    new = old + f"""
    if (cfg.ema_gate?.enabled) add("EMA gate", `${{cfg.ema_gate.period}}/${{cfg.ema_gate.slope_lookback}} ≥${{cfg.ema_gate.min_slope}}p`);   // ── {FENCE} ──"""
    src = _ro(src, old, new, "B6 chip")

    # B7 — UI fields after the Confirm field
    old = """                  <Field label="Confirm (min, 0=off)">
                    <input
                      type="number" min="0" max="30" step="1"
                      style={{ ...inputStyle, width: 80 }}
                      value={pstConfirmMin}
                      onChange={(e) => setPstConfirmMin(Math.min(30, Math.max(0, Number(e.target.value) || 0)))}
                      title="wait N minutes after the signal; abort if spot touches the would-be SL level during the wait"
                    />
                  </Field>"""
    new = old + """
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
    src = _ro(src, old, new, "B7 UI")
    return src


def patch_runcomparison(src):
    if FENCE in src:
        print("  RunComparison.jsx: fence present — skipping (idempotent)")
        return src
    old = """  { key: "pst_confirm", label: "Confirm",    get: (r) => (r.config?.signal_tf && Number(r.config?.confirm_minutes) > 0 ? `${r.config.confirm_minutes}m` : null) },   // ── PST_SELL_CONFIRM_20260828 ──"""
    new = old + f"""
  {{ key: "pst_emagate", label: "EMA gate",   get: (r) => (r.config?.signal_tf && r.config?.ema_gate?.enabled ? `${{r.config.ema_gate.period}}/${{r.config.ema_gate.slope_lookback}}≥${{r.config.ema_gate.min_slope}}p` : null) }},   // ── {FENCE} ──"""
    return _ro(src, old, new, "RC PARAM_KEYS")


def patch_queue(src):
    if FENCE in src:
        print("  BacktestQueue.jsx: fence present — skipping (idempotent)")
        return src
    old = """  if (Number(cfg.confirm_minutes) > 0) p.push(`cfm${cfg.confirm_minutes}m`);   // ── PST_SELL_CONFIRM_20260828 ── sweep rows over N must be tellable apart"""
    new = old + f"""
  if (cfg.ema_gate?.enabled) p.push(`eG${{cfg.ema_gate.period}}/${{cfg.ema_gate.slope_lookback}}≥${{cfg.ema_gate.min_slope}}`);   // ── {FENCE} ── sweep rows must be tellable apart"""
    return _ro(src, old, new, "BQ paramLine")


def patch_sweepbuilder(src):
    if FENCE in src:
        print("  SweepBuilder.jsx: fence present — skipping (idempotent)")
        return src
    old = """  { key: "pst_confirm", label: "Confirm wait (min)", strategies: [PSTS],
    hint: "1, 2, 3, 5", parse: _num,
    apply: (c, v) => { c.confirm_minutes = Math.min(30, Math.max(0, v)); }, fmt: (v) => `cfm${v}m` },   // ── PST_SELL_CONFIRM_20260828 ──"""
    new = old + f"""
  {{ key: "pst_ema_period", label: "EMA gate period", strategies: [PSTS],
    hint: "20, 50", parse: _num,
    apply: (c, v) => {{ if (c.ema_gate) {{ c.ema_gate.enabled = true; c.ema_gate.period = Math.max(2, v); }} }}, fmt: (v) => `eGp${{v}}` }},   // ── {FENCE} ── sweeping the gate implies gate ON
  {{ key: "pst_ema_slope", label: "EMA gate min slope (pts)", strategies: [PSTS],
    hint: "15, 25, 40", parse: _num,
    apply: (c, v) => {{ if (c.ema_gate) {{ c.ema_gate.enabled = true; c.ema_gate.min_slope = v; }} }}, fmt: (v) => `eGs${{v}}` }},   // ── {FENCE} ──"""
    return _ro(src, old, new, "SB axes")


def main():
    for p in (BT, RC, BQ, SB):
        if not os.path.isfile(p):
            raise SystemExit(f"ABORT: missing {p} — set SCALP_REPO/SCALP_FRONTEND.")
    bt, rc, bq, sb = open(BT).read(), open(RC).read(), open(BQ).read(), open(SB).read()
    bt2, rc2, bq2, sb2 = (patch_backtest(bt), patch_runcomparison(rc),
                          patch_queue(bq), patch_sweepbuilder(sb))
    for path, cur, new in ((BT, bt, bt2), (RC, rc, rc2), (BQ, bq, bq2), (SB, sb, sb2)):
        if new != cur:
            open(path, "w").write(new)
            print(f"  wrote {path}")
    print("DONE —", FENCE, "(frontend)")


if __name__ == "__main__":
    main()
