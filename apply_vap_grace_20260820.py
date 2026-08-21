#!/usr/bin/env python3
# apply_vap_grace_20260820.py
#
# TWO changes, both frontend-only:
#
#  A. SL_GRACE_20260820 — expose sl_grace_min / sl_grace_disaster_pct as UI
#     knobs and sweep axes. The backend half ships as whole-file
#     replacements under backend/app/backtest/vap/ (self-contained module,
#     nothing shared is touched — monitor_position_day is UNMODIFIED
#     because it is the parity reference for the LIVE TMA V1/V2 cores).
#
#  B. VAP_COMPARE_FIX — VAP_V1 was missing from RunComparison's hardcoded
#     STRAT_LABEL map and its hardcoded filter-chip list, so VAP runs could
#     not be isolated on Compare Runs and would have rendered a blank chip
#     even if one existed. Also adds the capital-spec arm so the
#     margin/return-on-capital columns populate for VAP instead of showing
#     blank (VAP nests its cap at v1.main.premium_max, which the generic
#     `c.option_premium?.max ?? c.premium_max` lookup does not reach).
#
#  Also adds the two sweep axes flagged as missing last round:
#  require_arm_first and the entry cutoff.
#
# ASSERT-ANCHORED: nothing is written unless every anchor resolves exactly
# once. Run from the repo root:
#     python3 apply_vap_grace_20260820.py --dry-run
#     python3 apply_vap_grace_20260820.py
#
# No --tree pass needed: these are frontend files, which have one tree.

from __future__ import annotations

import io
import sys
from pathlib import Path

DRY = "--dry-run" in sys.argv
EDITS: list[tuple[str, str, str, str]] = []


def edit(path: str, label: str, old: str, new: str) -> None:
    EDITS.append((path, label, old, new))


BT = "frontend/src/pages/Backtest.jsx"
BQ = "frontend/src/pages/backtest/BacktestQueue.jsx"
RC = "frontend/src/pages/backtest/RunComparison.jsx"
SB = "frontend/src/pages/backtest/SweepBuilder.jsx"

# ══════════════════════════════════════════════════════════════════════
# A. SL GRACE — Backtest.jsx state, buildConfig, deps, UI, describeConfig
# ══════════════════════════════════════════════════════════════════════
edit(BT, "Backtest.jsx grace state",
     '''  const [vapWingMode, setVapWingMode] = useState(vapSaved.wingMode ?? "synthetic");''',
     '''  const [vapWingMode, setVapWingMode] = useState(vapSaved.wingMode ?? "synthetic");
  // ── SL_GRACE_20260820 ── suspend the SL for the first N minutes after
  // entry (TP stays armed, EOD always applies). 0 = off.
  const [vapGrace, setVapGrace] = useState(vapSaved.grace ?? 0);
  const [vapGraceDis, setVapGraceDis] = useState(vapSaved.graceDis ?? 0);''')

edit(BT, "Backtest.jsx grace localStorage persist",
     '''maxDay: vapMaxDay, wingMode: vapWingMode })); } catch { /* ignore */ }
  }, [vapMode, vapSigPrem, vapMinPrem, vapSelTime, vapBothSides, vapArmFirst, vapBuffer, vapSlMode, vapSlPct, vapAtrPeriod, vapAtrMult, vapMaxSl, vapTpMode, vapRr, vapTpPct, vapSessStart, vapSessEnd, vapExitTime, vapMain, vapHedge, vapMaxDay, vapWingMode]);''',
     '''maxDay: vapMaxDay, wingMode: vapWingMode, grace: vapGrace, graceDis: vapGraceDis })); } catch { /* ignore */ }
  }, [vapMode, vapSigPrem, vapMinPrem, vapSelTime, vapBothSides, vapArmFirst, vapBuffer, vapSlMode, vapSlPct, vapAtrPeriod, vapAtrMult, vapMaxSl, vapTpMode, vapRr, vapTpPct, vapSessStart, vapSessEnd, vapExitTime, vapMain, vapHedge, vapMaxDay, vapWingMode, vapGrace, vapGraceDis]);''')

edit(BT, "Backtest.jsx buildConfig grace keys",
     '''        max_sl_pct: Number(vapMaxSl) || 0,
        tp_mode: vapTpMode,''',
     '''        max_sl_pct: Number(vapMaxSl) || 0,
        sl_grace_min: Number(vapGrace) || 0,               // ── SL_GRACE_20260820 ──
        sl_grace_disaster_pct: Number(vapGraceDis) || 0,
        tp_mode: vapTpMode,''')

edit(BT, "Backtest.jsx buildConfig deps + grace",
     '''vapMain, vapHedge, vapMaxDay, vapWingMode]);   // ── VAP_V1 ── stale-closure rule''',
     '''vapMain, vapHedge, vapMaxDay, vapWingMode, vapGrace, vapGraceDis]);   // ── VAP_V1 / SL_GRACE_20260820 ── stale-closure rule''')

edit(BT, "Backtest.jsx grace UI fields",
     '''                <Field label="TP basis">
                  <select style={{ ...inputStyle, width: 200 }} value={vapTpMode} onChange={(e) => setVapTpMode(e.target.value)}>''',
     '''                {/* ── SL_GRACE_20260820 ── */}
                <Field label="SL grace (min, 0=off)">
                  <input type="number" min="0" max="360" style={{ ...inputStyle, width: 90 }} value={vapGrace} onChange={(e) => setVapGrace(Number(e.target.value))}
                    title="Suspend the SL for this many minutes after entry. The TP stays ARMED and the EOD square-off always applies — only the stop is held back. If the premium is already through the SL when the window expires, the exit fills at that candle's OPEN (a market fill), not at the untouched SL level." />
                </Field>
                {Number(vapGrace) > 0 && (
                  <Field label="Disaster SL % during grace (0=off)">
                    <input type="number" style={{ ...inputStyle, width: 110 }} value={vapGraceDis} onChange={(e) => setVapGraceDis(Number(e.target.value))}
                      title="A WIDER stop that stays live through the grace window. Must exceed the normal SL% or the run aborts — a tighter disaster stop would fire first and silently cancel the grace. Leave 0 to run the window with no stop at all." />
                  </Field>
                )}
                <Field label="TP basis">
                  <select style={{ ...inputStyle, width: 200 }} value={vapTpMode} onChange={(e) => setVapTpMode(e.target.value)}>''')

edit(BT, "Backtest.jsx grace footnote",
     '''                VWAP accumulates on 1m bars (typical price × volume)''',
     '''                SL grace: the 6-year run stopped out 74% of trades with a median time-to-stop of 21 minutes and the fastest quartile inside 8 — a grace window tests how much of that is noise rather than the signal failing. Watch the DIAG counters, not just net: grace_breached is how many trades would have been stopped inside the window, and the then_sl / then_tp / then_eod split is whether holding through actually paid. A window with grace_breached near zero is costing nothing and buying nothing.
              </div>
              <div style={{ marginTop: 8, fontSize: 11, color: colors.text.tertiary, lineHeight: 1.55 }}>
                VWAP accumulates on 1m bars (typical price × volume)''')

edit(BT, "Backtest.jsx describeConfig grace chips",
     '''    add("TP", cfg.tp_mode === "RR" ? `RR ${cfg.rr}` : `${cfg.tp_pct}%`);''',
     '''    if (Number(cfg.sl_grace_min) > 0) add("SL grace", `${cfg.sl_grace_min}m${Number(cfg.sl_grace_disaster_pct) > 0 ? ` / dis ${cfg.sl_grace_disaster_pct}%` : ""}`);   // ── SL_GRACE_20260820 ──
    add("TP", cfg.tp_mode === "RR" ? `RR ${cfg.rr}` : `${cfg.tp_pct}%`);''')

edit(BQ, "BacktestQueue paramLine grace chip",
     '''    p.push(`TP${cfg.tp_mode === "RR" ? `RR${cfg.rr}` : `${cfg.tp_pct}%`}`);''',
     '''    if (Number(cfg.sl_grace_min) > 0) p.push(`grace${cfg.sl_grace_min}m${Number(cfg.sl_grace_disaster_pct) > 0 ? `/dis${cfg.sl_grace_disaster_pct}%` : ""}`);   // ── SL_GRACE_20260820 ──
    p.push(`TP${cfg.tp_mode === "RR" ? `RR${cfg.rr}` : `${cfg.tp_pct}%`}`);''')

# ══════════════════════════════════════════════════════════════════════
# B. VAP_COMPARE_FIX — RunComparison label, chip, capital spec, columns
# ══════════════════════════════════════════════════════════════════════
edit(RC, "RunComparison STRAT_LABEL",
     '''TMA_V1: "TMA", TMA_V2: "TMA2", TSG_V1: "TSG", GC_V1: "GC" };''',
     '''TMA_V1: "TMA", TMA_V2: "TMA2", TSG_V1: "TSG", GC_V1: "GC", VAP_V1: "VAP" };''')

edit(RC, "RunComparison filter chip list",
     '''"TMA_V1", "TMA_V2", "TSG_V1", "GC_V1" ].map((sId) => (''',
     '''"TMA_V1", "TMA_V2", "TSG_V1", "GC_V1", "VAP_V1" ].map((sId) => (''')

edit(RC, "RunComparison VAP capital spec",
     '''  // single-leg shorts (SCALP_V1/V2 grouped lots, PST_SELL summed legs)''',
     '''  // ── VAP_COMPARE_FIX ── VAP nests its cap at v1.main.premium_max, which
  // the generic `option_premium.max ?? premium_max` lookup above never
  // reaches — without this arm the margin and return-on-capital columns
  // stay blank for every VAP run. SELL is a two-leg spread (short the
  // opposite side + same-side wing); BUY is premium outlay, local math.
  if (run?.strategy_id === "VAP_V1" || (c.vwap && c.v1)) {
    const mn = c.v1?.main || {}, hd = c.v1?.hedge || {};
    const mLots = Number(mn.lots) || 0;
    if (!mLots) return null;
    if (c.mode === "SELL") {
      if (!Number(mn.premium_max)) return null;
      const legs = [{ side: "PE", action: "SELL", premium_max: Number(mn.premium_max), lots: mLots }];
      if (Number(hd.premium_max) > 0)
        legs.push({ side: "PE", action: "BUY", premium_max: Number(hd.premium_max), lots: Number(hd.lots) || mLots });
      return { kind: "api", legs, sig: JSON.stringify(legs) };
    }
    // BUY mode buys the SIGNAL contract, so its cap is signal_premium_max.
    const bCap = Number(c.signal_premium_max) || 0;
    if (!bCap) return null;
    return { kind: "local", amount: bCap * mLots * lot };
  }
  // single-leg shorts (SCALP_V1/V2 grouped lots, PST_SELL summed legs)''')

edit(RC, "RunComparison grace column",
     '''  { key: "vap_tp", label: "VAP TP",''',
     '''  { key: "vap_grace", label: "VAP SL grace", get: (r) => (r.config?.vwap && r.config?.v1 && Number(r.config.sl_grace_min) > 0) ? `${r.config.sl_grace_min}m${Number(r.config.sl_grace_disaster_pct) > 0 ? ` / dis ${r.config.sl_grace_disaster_pct}%` : ""}` : null },
  { key: "vap_tp", label: "VAP TP",''')

# ══════════════════════════════════════════════════════════════════════
# C. SweepBuilder — grace axes + the two axes flagged missing last round
# ══════════════════════════════════════════════════════════════════════
edit(SB, "SweepBuilder grace + arm + cutoff axes",
     '''  { key: "vap_mode", label: "Execution mode", strategies: [VAP],''',
     '''  { key: "vap_sl_grace", label: "SL grace (min)", strategies: [VAP],
    hint: "0, 10, 15, 30, 45", parse: _num,
    apply: (c, v) => { if (c.vwap) c.sl_grace_min = v; }, fmt: (v) => `grace${v}m` },
  { key: "vap_grace_disaster", label: "Disaster SL % in grace", strategies: [VAP],
    hint: "0, 50, 75", parse: _num,
    apply: (c, v) => { if (c.vwap) c.sl_grace_disaster_pct = v; }, fmt: (v) => `dis${v}%` },
  { key: "vap_arm_first", label: "First entry needs arming", strategies: [VAP],
    hint: "ON, OFF", parse: (tok) => {
      const v = tok.trim().toUpperCase();
      return ["ON", "OFF"].includes(v) ? { v: v === "ON" } : { err: `"${tok}" must be ON or OFF` };
    },
    apply: (c, v) => { if (c.vwap) c.require_arm_first = v; },
    fmt: (v) => (v ? "armFirst" : "noArm") },
  { key: "vap_sess_end", label: "Entry cutoff", strategies: [VAP],
    hint: "11:00, 12:30, 14:45", parse: (tok) => {
      const v = tok.trim();
      return /^\\d{2}:\\d{2}$/.test(v) ? { v } : { err: `"${tok}" must be HH:MM` };
    },
    apply: (c, v) => { c.session_end = v; }, fmt: (v) => `cut${v}` },
  { key: "vap_mode", label: "Execution mode", strategies: [VAP],''')


def main() -> int:
    planned: dict[Path, str] = {}
    failures: list[str] = []
    for rel, label, old, new in EDITS:
        p = Path(rel)
        if not p.exists():
            failures.append(f"MISSING FILE {rel} ({label})")
            continue
        src = planned.get(p)
        if src is None:
            src = io.open(p, encoding="utf-8").read()
        n = src.count(old)
        if n == 0:
            failures.append(f"ANCHOR MISS  {rel} :: {label}")
            continue
        if n > 1:
            failures.append(f"ANCHOR AMBIGUOUS ({n}x) {rel} :: {label}")
            continue
        if new in src:
            failures.append(f"ALREADY APPLIED {rel} :: {label}")
            continue
        planned[p] = src.replace(old, new, 1)
        print(f"  ok  {rel} :: {label}")
    if failures:
        print("\nABORTED — nothing written:")
        for f in failures:
            print(f"  {f}")
        return 1
    if DRY:
        print(f"\nDRY RUN: {len(planned)} file(s) would change. Nothing written.")
        return 0
    for p, text in planned.items():
        io.open(p, "w", encoding="utf-8").write(text)
    print(f"\nApplied {len(EDITS)} edit(s) across {len(planned)} file(s).")
    print("REMINDER: replace backend/app/backtest/vap/*.py with the new "
          "whole-file versions, in BOTH trees.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
