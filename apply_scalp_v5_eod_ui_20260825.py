#!/usr/bin/env python3
# apply_scalp_v5_eod_ui_20260825.py
#
# SCALP_V5 — EOD square-off UI field — fence: SCALP_V5_EOD_UI_20260825
#
# Completes apply_scalp_v5_parity_perf_20260825: that fence added the
# backend `eod_squareoff_time` key but NO way to set it from the Backtest
# page, so the knob was unreachable. This adds the field, its state, the
# saveParams/buildConfig wiring and dep-array entries, the run-params chip,
# the queue token, the RunComparison row and a sweep axis.
#
# FORMAT: "" = LEGACY (day's last candle, byte-identical to run 798a88c0).
# "HH:MM" = PARITY (day stops at the boundary; leftover position closes on
# the last 1m bar closing at or before it).
#
# HOUSE RULE: <input type="time"> is unreliable under Tauri/WebKit, so this
# is a type="text" input canonicalised on blur — "1515", "15.15", "15:5"
# and " 15:15 " all normalise to "15:15"; anything unparseable clears to ""
# (legacy) rather than silently running an unintended boundary.
#
# PREREQ: SCALP_V5_PARITY_PERF_20260825 in the runner. Idempotent.
# Run from the repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V5_EOD_UI_20260825"
PREREQ = "SCALP_V5_PARITY_PERF_20260825"
ROOT = Path(__file__).resolve().parent
RN_REL = "app/backtest/scalpv5/backtest_scalpv5_runner.py"
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


# ── J1: state + canonicaliser ─────────────────────────────────────────────
J1_OLD = "  const [v5Tf, setV5Tf] = useState(saved.v5Tf ?? 3);   // ── V5_TIMEFRAME ──"
J1_NEW = '''  const [v5Tf, setV5Tf] = useState(saved.v5Tf ?? 3);   // ── V5_TIMEFRAME ──
  // ── SCALP_V5_EOD_UI_20260825 ── "" = legacy (day's last candle, ~15:30);
  // "HH:MM" = live-parity square-off. type="text" per the Tauri/WebKit rule.
  const [v5EodTime, setV5EodTime] = useState(saved.v5EodTime ?? "");'''

# ── J2: canonicaliser helper, next to the component ───────────────────────
J2_OLD = "  const isV5 = strategyId === \"SCALP_V5\";"
J2_NEW = '''  const isV5 = strategyId === "SCALP_V5";
  // ── SCALP_V5_EOD_UI_20260825 ── accept 1515 / 15.15 / 15:5 / " 15:15 ";
  // anything unparseable clears to "" (legacy) — never a silent wrong time.
  const canonHm = (raw) => {
    const s = String(raw ?? "").trim();
    if (!s) return "";
    const m = s.match(/^(\\d{1,2})\\s*[:.]?\\s*(\\d{2})$/);
    if (!m) return "";
    const h = Number(m[1]), mi = Number(m[2]);
    if (!(h >= 0 && h <= 23 && mi >= 0 && mi <= 59)) return "";
    return `${String(h).padStart(2, "0")}:${String(mi).padStart(2, "0")}`;
  };'''

# ── J3/J4: saveParams object + its dep array (same literal, 2 sites) ──────
J3_OLD = "      slPoints, tpPoints, maxLoss, maxProfit, sideMode, v5Tf,"
J3_NEW = "      slPoints, tpPoints, maxLoss, maxProfit, sideMode, v5Tf,\n      v5EodTime,   // ── SCALP_V5_EOD_UI_20260825 ──"

# ── J5: config emission ───────────────────────────────────────────────────
# NOTE: the session/quantity/side triple appears in BOTH the V3 and V5
# branches, so the anchor carries the V5-only timeframe line to stay unique.
J5_OLD = """    if (v5) {
      return {
        timeframe_minutes: Number(v5Tf),   // ── V5_TIMEFRAME ──
        option_premium: { min: Number(premiumMin), max: Number(premiumMax) },
        sl_points: Number(slPoints),
        tp_points: Number(tpPoints),
        session: { primary: { start: sessStart, end: sessEnd } },
        quantity: { lots: Number(lots) },
        trade_side_mode: sideMode,"""
J5_NEW = """    if (v5) {
      return {
        timeframe_minutes: Number(v5Tf),   // ── V5_TIMEFRAME ──
        option_premium: { min: Number(premiumMin), max: Number(premiumMax) },
        sl_points: Number(slPoints),
        tp_points: Number(tpPoints),
        session: { primary: { start: sessStart, end: sessEnd } },
        quantity: { lots: Number(lots) },
        trade_side_mode: sideMode,
        // ── SCALP_V5_EOD_UI_20260825 ── omit-when-empty: a legacy run's
        // config stays byte-identical to the pre-fence runs it is compared to.
        ...(canonHm(v5EodTime) ? { eod_squareoff_time: canonHm(v5EodTime) } : {}),"""

# ── J6: buildConfig dep array ─────────────────────────────────────────────
J6_OLD = "      maxLoss, maxProfit, rr, minSl, maxSl, riskMaxSl, hedgeSl, v5Tf,"
J6_NEW = "      maxLoss, maxProfit, rr, minSl, maxSl, riskMaxSl, hedgeSl, v5Tf,\n      v5EodTime,   // ── SCALP_V5_EOD_UI_20260825 ── stale-closure rule: buildConfig reads it, so it lands here in the SAME commit"

# ── J7: the field itself ──────────────────────────────────────────────────
J7_OLD = '''              <Field label="SL pts"><input type="number" style={inputStyle} value={slPoints} onChange={(e) => setSlPoints(e.target.value)} /></Field>'''
J7_NEW = '''              {/* ── SCALP_V5_EOD_UI_20260825 ── blank = legacy (day's last
                  candle, stamps ~15:30). Set to the LIVE cron time for a
                  parity run — the day then stops at that boundary. */}
              <Field label="EOD square-off">
                <input type="text" placeholder="blank = 15:30" style={inputStyle}
                  value={v5EodTime}
                  onChange={(e) => setV5EodTime(e.target.value)}
                  onBlur={(e) => setV5EodTime(canonHm(e.target.value))} />
              </Field>
              <Field label="SL pts"><input type="number" style={inputStyle} value={slPoints} onChange={(e) => setSlPoints(e.target.value)} /></Field>'''

# ── J8: run-params chip ───────────────────────────────────────────────────
J8_OLD = '  if (cfg.vwap_filter?.enabled) add("VWAP", `below${Number(cfg.vwap_filter.min_below_pts) > 0 ? ` ≥${cfg.vwap_filter.min_below_pts}` : ""}`);   // ── SCALP_V1_VWAP_20260825 ──'
J8_NEW = '''  if (cfg.vwap_filter?.enabled) add("VWAP", `below${Number(cfg.vwap_filter.min_below_pts) > 0 ? ` ≥${cfg.vwap_filter.min_below_pts}` : ""}`);   // ── SCALP_V1_VWAP_20260825 ──
  // ── SCALP_V5_EOD_UI_20260825 ── a parity run and a legacy run must never
  // read as the same parameters: the EOD boundary carries all of V5's P&L.
  if (cfg.eod_squareoff_time) add("EOD", String(cfg.eod_squareoff_time));'''

Q1_OLD = '  if (cfg.hedge_leg?.enabled) p.push(`hdg${cfg.hedge_leg.max_premium}`);   // ── SCALP_V1_HEDGE_LEG_20260824 ──'
Q1_NEW = '''  if (cfg.hedge_leg?.enabled) p.push(`hdg${cfg.hedge_leg.max_premium}`);   // ── SCALP_V1_HEDGE_LEG_20260824 ──
  if (cfg.eod_squareoff_time) p.push(`eod${cfg.eod_squareoff_time}`);   // ── SCALP_V5_EOD_UI_20260825 ──'''

C1_OLD = '''  { key: "vwap_filter",      label: "VWAP filter",    get: (r) => (r.config?.vwap_filter?.enabled ? `below ≥${r.config.vwap_filter.min_below_pts}` : null) },   // ── SCALP_V1_VWAP_20260825 ──'''
C1_NEW = '''  { key: "vwap_filter",      label: "VWAP filter",    get: (r) => (r.config?.vwap_filter?.enabled ? `below ≥${r.config.vwap_filter.min_below_pts}` : null) },   // ── SCALP_V1_VWAP_20260825 ──
  { key: "eod_squareoff",    label: "EOD square-off", get: (r) => (r.config?.eod_squareoff_time ? String(r.config.eod_squareoff_time) : "day end (legacy)") },   // ── SCALP_V5_EOD_UI_20260825 ── always shown: legacy vs parity is the headline difference'''

W1_OLD = """  // ── SCALP_V1_VWAP_20260825 ── -1 = off; 0 = below by any amount; >0 = min pts below."""
W1_NEW = """  // ── SCALP_V5_EOD_UI_20260825 ── blank = legacy day-end; else "HH:MM".
  { key: "v5_eod", label: "V5 EOD square-off (blank=day end)", strategies: [V5],
    hint: "15:15, 15:25", parse: (s) => String(s || "").trim(),
    apply: (c, v) => { if (v) c.eod_squareoff_time = v; }, fmt: (v) => (v ? `eod${v}` : "day end") },
  // ── SCALP_V1_VWAP_20260825 ── -1 = off; 0 = below by any amount; >0 = min pts below."""


def main():
    if not (ROOT / "backend" / RN_REL).exists():
        _die("run from the scalp-app repo root")
    if PREREQ not in (ROOT / "backend" / RN_REL).read_text():
        _die(f"prerequisite fence {PREREQ} MISSING — apply the runner fence first")
    staged = []
    t = BT_JSX.read_text()
    if FENCE in t:
        _die(f"fence {FENCE} already present in Backtest.jsx")
    t = _ro(t, J1_OLD, J1_NEW, "Backtest:J1")
    t = _ro(t, J2_OLD, J2_NEW, "Backtest:J2")
    n = t.count(J3_OLD)
    if n != 2:
        _die(f"anchor 'Backtest:J3/J4' matched {n} times (want 2) — NOTHING written")
    t = t.replace(J3_OLD, J3_NEW)
    t = _ro(t, J5_OLD, J5_NEW, "Backtest:J5")
    t = _ro(t, J6_OLD, J6_NEW, "Backtest:J6")
    t = _ro(t, J7_OLD, J7_NEW, "Backtest:J7")
    t = _ro(t, J8_OLD, J8_NEW, "Backtest:J8")
    staged.append((BT_JSX, t))
    for path, lab, o, n2 in [(QU_JSX, "Queue:Q1", Q1_OLD, Q1_NEW),
                             (RC_JSX, "Comparison:C1", C1_OLD, C1_NEW),
                             (SW_JSX, "Sweep:W1", W1_OLD, W1_NEW)]:
        tt = path.read_text()
        if FENCE in tt:
            _die(f"fence {FENCE} already present in {path.name}")
        staged.append((path, _ro(tt, o, n2, lab)))
    for p, tt in staged:
        p.write_text(tt)
        print(f"PATCHED: {p}")
    print(f"\nDONE — fence {FENCE} applied. Restart the frontend (or rebuild).")
    print()
    print('Select SCALP V5 → the "EOD square-off" field sits above SL pts.')
    print(" * BLANK  → legacy day-end close. Run this FIRST against the same")
    print("   config as 798a88c0: trades/net/DD must match EXACTLY (the perf")
    print("   regression test), and it should finish much faster.")
    print(' * "15:15" or "15:25" → parity run. The chip, the queue token and')
    print("   the RunComparison row all show the boundary, so a parity run can")
    print("   never be mistaken for a legacy one.")


if __name__ == "__main__":
    main()
