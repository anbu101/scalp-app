#!/usr/bin/env python3
# apply_brk_v1_ui_20260830.py
#
# ── BRK_V1_UI_20260830 ── wire BRK_V1 (09:25 premium breakout scalp) into
# the frontend. Companion to apply_brk_v1_20260830.py (backend) — run the
# backend one FIRST or the chip appears and every run 400s.
#
# FIVE FILES, all assert-anchored (exactly-one occurrence unless stated):
#
#   frontend/src/pages/Backtest.jsx            (eleven sites)
#     1  localStorage key + loader
#     2  loadBrkParams() call in the component body
#     3  strategyId restore allow-list
#     4  state block + persistence effect
#     5  buildConfig arm
#     6  buildConfig dep array               <- the stale-closure rule
#     7  describeConfig arm
#     8  strategy chip
#     9  the config panel
#    10  hide the shared premium/RR/session/lots fields for BRK (replace-3)
#    11  CSV export passes diag_brk
#   frontend/src/pages/backtest/paramFormat.js  brkParamSummary (shared)
#   frontend/src/pages/backtest/BacktestQueue.jsx   import + paramLine branch
#   frontend/src/pages/backtest/RunComparison.jsx   import + paramSummary
#                                                   branch + STRAT_LABEL +
#                                                   EXIT_REASON_KEYS (TRAIL)
#   frontend/src/pages/backtest/SweepBuilder.jsx    BRK const + axes
#
# The four-formatter-copies rule: every file that renders a config line
# for a run gets the BRK branch in the SAME commit — paramFormat (source of
# truth), BacktestQueue, RunComparison, and Backtest.jsx's describeConfig.
#
# Gates before any write: stale-closure assert (every brk* state read by
# buildConfig is in the dep array and vice versa), then esbuild must parse
# every patched .jsx/.js. Backups: .bak-FENCE. Frontend is a SINGLE tree.
#
#     python3 apply_brk_v1_ui_20260830.py --check
#     python3 apply_brk_v1_ui_20260830.py

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

FENCE = "BRK_V1_UI_20260830"
BT = Path("frontend/src/pages/Backtest.jsx")
PF = Path("frontend/src/pages/backtest/paramFormat.js")
BQ = Path("frontend/src/pages/backtest/BacktestQueue.jsx")
RC = Path("frontend/src/pages/backtest/RunComparison.jsx")
SB = Path("frontend/src/pages/backtest/SweepBuilder.jsx")


# ═════════════════════════════════════════════════════════════════════════
#  Backtest.jsx
# ═════════════════════════════════════════════════════════════════════════

# ── 1. LS key + loader ───────────────────────────────────────────────────
A_LSKEY = '// ── CBO_V1_UI_20260829 END ──\n\n// ── TSG_V1 BEGIN ──'
I_LSKEY = f'''// ── CBO_V1_UI_20260829 END ──

// ── {FENCE} BEGIN ── 09:25 premium breakout scalp (BRK_V1). Own LS key.
const BRK_LS_KEY = "scalp_backtest_brk_v1";
function loadBrkParams() {{
  try {{ return JSON.parse(localStorage.getItem(BRK_LS_KEY)) || {{}}; }} catch {{ return {{}}; }}
}}
// ── {FENCE} END ──

// ── TSG_V1 BEGIN ──'''

# ── 2. loader call ───────────────────────────────────────────────────────
A_SAVED = '  const cboSaved = loadCboParams();     // ── CBO_V1_UI_20260829 ──\n'
I_SAVED = A_SAVED + f'  const brkSaved = loadBrkParams();     // ── {FENCE} ──\n'

# ── 3. restore allow-list ────────────────────────────────────────────────
A_RESTORE = '"VET_V1", "CBO_V1"].includes(saved.strategyId)'
I_RESTORE = '"VET_V1", "CBO_V1", "BRK_V1"].includes(saved.strategyId)'

# ── 4. state block ───────────────────────────────────────────────────────
A_STATE = ('  // ── CBO_V1_UI_20260829 END ──\n'
           '  // "run" = the existing run+config+results view;')
I_STATE = f'''  // ── CBO_V1_UI_20260829 END ──
  // ── {FENCE} BEGIN ── 09:25 premium breakout scalp. At select_time pick
  // the CE and PE nearest-BELOW select_below; from entry_first to
  // entry_last, the first side whose 1m CLOSE holds >= break_above fills
  // at that minute's open. Premium SL/TP, optional trail, EOD square-off.
  const isBRK = strategyId === "BRK_V1";
  const [brkSelTime, setBrkSelTime] = useState(brkSaved.selTime ?? "09:25");
  const [brkSelBelow, setBrkSelBelow] = useState(brkSaved.selBelow ?? 180);
  const [brkSelMin, setBrkSelMin] = useState(brkSaved.selMin ?? 0);
  const [brkBreak, setBrkBreak] = useState(brkSaved.brk ?? 180);
  const [brkSustain, setBrkSustain] = useState(brkSaved.sustain ?? 1);
  const [brkFirst, setBrkFirst] = useState(brkSaved.first ?? "09:30");
  const [brkLast, setBrkLast] = useState(brkSaved.last ?? "09:35");
  const [brkBoth, setBrkBoth] = useState(brkSaved.both ?? "first");
  const [brkSl, setBrkSl] = useState(brkSaved.sl ?? 20);
  const [brkTp, setBrkTp] = useState(brkSaved.tp ?? 40);
  const [brkTrailTrig, setBrkTrailTrig] = useState(brkSaved.trailTrig ?? 0);
  const [brkTrailLock, setBrkTrailLock] = useState(brkSaved.trailLock ?? 0);
  const [brkEod, setBrkEod] = useState(brkSaved.eod ?? "15:15");
  const [brkLots, setBrkLots] = useState(brkSaved.lots ?? 1);
  const [brkLotSize, setBrkLotSize] = useState(brkSaved.lotSize ?? 0);
  const [brkSkipExpiry, setBrkSkipExpiry] = useState(brkSaved.skipExpiry ?? false);
  useEffect(() => {{
    try {{ localStorage.setItem(BRK_LS_KEY, JSON.stringify({{ selTime: brkSelTime, selBelow: brkSelBelow, selMin: brkSelMin, brk: brkBreak, sustain: brkSustain, first: brkFirst, last: brkLast, both: brkBoth, sl: brkSl, tp: brkTp, trailTrig: brkTrailTrig, trailLock: brkTrailLock, eod: brkEod, lots: brkLots, lotSize: brkLotSize, skipExpiry: brkSkipExpiry }})); }} catch {{ /* ignore */ }}
  }}, [brkSelTime, brkSelBelow, brkSelMin, brkBreak, brkSustain, brkFirst, brkLast, brkBoth, brkSl, brkTp, brkTrailTrig, brkTrailLock, brkEod, brkLots, brkLotSize, brkSkipExpiry]);
  // ── {FENCE} END ──
  // "run" = the existing run+config+results view;'''

# ── 5. buildConfig arm ───────────────────────────────────────────────────
A_BCARM = ('    if (sid === "CBO_V1") {\n'
           '      // ── CBO_V1_UI_20260829 ── both_side_policy + breakout_buffer_pts is the')
I_BCARM = f'''    if (sid === "BRK_V1") {{
      // ── {FENCE} ── select_below + break_above is the describeConfig
      // detection key — disjoint from every other shape on this page.
      return {{
        select_time: brkSelTime,
        select_below: Number(brkSelBelow) || 0,
        select_min: Number(brkSelMin) || 0,
        break_above: Number(brkBreak) || 0,
        sustain_candles: Number(brkSustain) || 1,
        entry_first: brkFirst,
        entry_last: brkLast,
        both_policy: brkBoth,
        sl_pts: Number(brkSl) || 0,
        tp_pts: Number(brkTp) || 0,
        trail_trigger_pts: Number(brkTrailTrig) || 0,
        trail_lock_pts: Number(brkTrailLock) || 0,
        eod_square_off: brkEod,
        lots: Number(brkLots) || 1,
        lot_size: Number(brkLotSize) || 0,
        skip_expiry_day: !!brkSkipExpiry,
      }};
    }}
''' + A_BCARM

# ── 6. buildConfig dep array ─────────────────────────────────────────────
A_DEPS = ('cboSkewParity, cboSkewCarry,   // ── CBO_PREM_SL_UI_20260830 ── stale-closure '
          'rule: buildConfig reads the two new fields, so they land here in the SAME commit\n')
I_DEPS = A_DEPS + f'''    // ── {FENCE} ── STALE-CLOSURE RULE: buildConfig reads every one of these.
    brkSelTime, brkSelBelow, brkSelMin, brkBreak, brkSustain, brkFirst, brkLast, brkBoth, brkSl, brkTp, brkTrailTrig, brkTrailLock, brkEod, brkLots, brkLotSize, brkSkipExpiry,
'''

# ── 7. describeConfig arm ────────────────────────────────────────────────
A_DESC = '  // ── CBO_V1_UI_20260829 ── (both_side_policy is unique to CBO configs)'
I_DESC = f'''  // ── {FENCE} ── (select_below + break_above is unique to BRK configs)
  if (cfg.select_below != null && cfg.break_above != null) {{
    add("Select", `nearest-below ₹${{cfg.select_below}} @${{cfg.select_time || "09:25"}}${{Number(cfg.select_min) > 0 ? ` (≥₹${{cfg.select_min}})` : ""}}`);
    add("Break", `close ≥ ₹${{cfg.break_above}}${{Number(cfg.sustain_candles) > 1 ? ` ×${{cfg.sustain_candles}}` : ""}}`);
    add("Window", `${{cfg.entry_first || "09:30"}}–${{cfg.entry_last || "09:35"}}`);
    if (cfg.both_policy && cfg.both_policy !== "first") add("Both", cfg.both_policy);
    add("SL / TP", `−₹${{cfg.sl_pts}} / +₹${{cfg.tp_pts}}`);
    if (Number(cfg.trail_trigger_pts) > 0) add("Trail", `@+₹${{cfg.trail_trigger_pts}} → entry${{Number(cfg.trail_lock_pts) > 0 ? `+₹${{cfg.trail_lock_pts}}` : ""}}`);
    if (cfg.eod_square_off) add("EOD", cfg.eod_square_off);
    if (cfg.lots) add("Lots", cfg.lots);
    if (cfg.skip_expiry_day) add("Expiry", "skipped");
    return out;
  }}
''' + A_DESC

# ── 8. strategy chip ─────────────────────────────────────────────────────
A_CHIP = '          { id: "CBO_V1", label: "CBO V1", sub: "prev-candle breakout" },   // ── CBO_V1_UI_20260829 ──\n'
I_CHIP = A_CHIP + f'          {{ id: "BRK_V1", label: "BRK V1", sub: "09:25 ₹180 breakout" }},   // ── {FENCE} ──\n'

# ── 9. config panel ──────────────────────────────────────────────────────
A_PANEL = ('          {isCBO && (\n'
           '            /* ── CBO_V1_UI_20260829 BEGIN ──')
I_PANEL = f'''          {{isBRK && (
            /* ── {FENCE} BEGIN ── 09:25 premium breakout scalp. Shared
               premium/RR/session/lots fields are HIDDEN for BRK —
               everything the runner reads is defined here. */
            <div style={{{{ gridColumn: "1 / -1", marginTop: 8 }}}}>
              <div style={{{{ display: "flex", gap: spacing.md, marginBottom: spacing.md, flexWrap: "wrap" }}}}>
                <Field label="Select time"><input type="text" style={{inputStyle}} value={{brkSelTime}} onChange={{(e) => setBrkSelTime(e.target.value)}} title="The instant the CE and PE are chosen. Reads the CLOSE of the 1m bar that ends here (09:25 = the 09:24 bar) — the LTP a live engine sees at 09:25:00." /></Field>
                <Field label="Select below ₹"><input type="number" style={{inputStyle}} value={{brkSelBelow}} onChange={{(e) => setBrkSelBelow(Number(e.target.value))}} title="Per side, the contract with the HIGHEST premium strictly below this at select time (nearest-to-level from below)." /></Field>
                <Field label="Select floor ₹ (0=off)"><input type="number" style={{inputStyle}} value={{brkSelMin}} onChange={{(e) => setBrkSelMin(Number(e.target.value))}} title="Optional: ignore contracts printing below this at select time." /></Field>
                <Field label="Break above ₹"><input type="number" style={{inputStyle}} value={{brkBreak}} onChange={{(e) => setBrkBreak(Number(e.target.value))}} title="The level a 1m CLOSE must reach. Must be >= select-below." /></Field>
                <Field label="Sustain (closes)"><input type="number" style={{inputStyle}} value={{brkSustain}} onChange={{(e) => setBrkSustain(Number(e.target.value))}} title="Consecutive 1m closes at-or-above the level required before the decision minute. 1 = the last close. A wick through the level never counts." /></Field>
                <Field label="Entry first"><input type="text" style={{inputStyle}} value={{brkFirst}} onChange={{(e) => setBrkFirst(e.target.value)}} title="First decision minute. A side confirmed here fills at THIS minute's 1m open. Nothing fills earlier even if the level broke at 09:26." /></Field>
                <Field label="Entry last"><input type="text" style={{inputStyle}} value={{brkLast}} onChange={{(e) => setBrkLast(e.target.value)}} title="Last decision minute (inclusive). One check per minute; the first confirmed minute fills at its open. No break by here = no trade today." /></Field>
                <Field label="Both break">
                  <select style={{inputStyle}} value={{brkBoth}} onChange={{(e) => setBrkBoth(e.target.value)}}
                    title="Both sides confirmed at the same decision minute. first = the side whose close crossed the level earliest since selection (same minute -> the dearer). higher = the dearer. skip = no trade.">
                    <option value="first">first to break (tie → dearer)</option>
                    <option value="higher">higher premium</option>
                    <option value="skip">skip the day</option>
                  </select>
                </Field>
              </div>
              <div style={{{{ display: "flex", gap: spacing.md, marginBottom: spacing.md, flexWrap: "wrap" }}}}>
                <Field label="SL ₹ (premium pts)"><input type="number" style={{inputStyle}} value={{brkSl}} onChange={{(e) => setBrkSl(Number(e.target.value))}} title="Stop = entry − this. Triggers on the bar LOW touching it; fills AT the level." /></Field>
                <Field label="Target ₹ (premium pts)"><input type="number" style={{inputStyle}} value={{brkTp}} onChange={{(e) => setBrkTp(Number(e.target.value))}} title="Target = entry + this. Triggers on the bar HIGH touching it; fills AT the level. SL and TP in one bar → SL." /></Field>
                <Field label="Trail trigger ₹ (0=off)"><input type="number" style={{inputStyle}} value={{brkTrailTrig}} onChange={{(e) => setBrkTrailTrig(Number(e.target.value))}} title="Once a bar's high reaches entry + this, the stop is raised (from the NEXT bar) to entry + lock. Exit on the raised stop is booked as TRAIL." /></Field>
                <Field label="Trail lock ₹"><input type="number" style={{inputStyle}} value={{brkTrailLock}} onChange={{(e) => setBrkTrailLock(Number(e.target.value))}} title="0 = breakeven. 10 = lock +10." /></Field>
                <Field label="EOD square-off"><input type="text" style={{inputStyle}} value={{brkEod}} onChange={{(e) => setBrkEod(e.target.value)}} title="Open position is closed at this minute's 1m close. Same clock as the live cron would use." /></Field>
                <Field label="Lots"><input type="number" style={{inputStyle}} value={{brkLots}} onChange={{(e) => setBrkLots(Number(e.target.value))}} /></Field>
                <Field label="Lot size (0 = auto)"><input type="number" style={{inputStyle}} value={{brkLotSize}} onChange={{(e) => setBrkLotSize(Number(e.target.value))}} title="0 = the index constant (NIFTY 65, BANKNIFTY 35)." /></Field>
                <label style={{{{ fontSize: 12, display: "flex", alignItems: "center", gap: 6, alignSelf: "flex-end", paddingBottom: 6 }}}}>
                  <input type="checkbox" checked={{brkSkipExpiry}} onChange={{(e) => setBrkSkipExpiry(e.target.checked)}} /> skip expiry day
                </label>
              </div>
              <div style={{{{ marginTop: 6, fontSize: 11, color: colors.text.tertiary }}}}>
                At {{brkSelTime}} the CE and PE printing nearest-below ₹{{brkSelBelow}} on the expected weekly are chosen. From {{brkFirst}} to {{brkLast}}, one check per minute: the first side whose last {{Number(brkSustain) > 1 ? `${{brkSustain}} closes are` : "1m close is"}} at-or-above ₹{{brkBreak}} is BOUGHT at that minute's open. Stop −₹{{brkSl}}, target +₹{{brkTp}} on the bought premium; both inside one minute → the STOP wins. One trade a day, no re-entry; EOD square-off at {{brkEod}}.
                <br /><b>Before reading the P&amp;L:</b> check entry_minute_hist (09:30 vs the 09:31–09:35 wait), days_no_break vs days_traded, and eod_pnl_share_pct in the run diag. If EOD carries most of net, the run describes the square-off, not the breakout.
              </div>
            </div>
            /* ── {FENCE} END ── */
          )}}
''' + A_PANEL

# ── 10. hide shared fields (replace-3) ───────────────────────────────────
A_HIDE = '!isVET && !isCBO && ('
I_HIDE = '!isVET && !isCBO && !isBRK && ('
HIDE_N = 3

# ── 11. CSV export diag ──────────────────────────────────────────────────
A_CSV = 'summary?.diag_cbo);   // ── CBO_PARAMS_EXPORT_20260830 ──'
I_CSV = f'summary?.diag_cbo || summary?.diag_brk);   // ── CBO_PARAMS_EXPORT_20260830 ── ── {FENCE} ──'


# ═════════════════════════════════════════════════════════════════════════
#  paramFormat.js
# ═════════════════════════════════════════════════════════════════════════
A_PF = 'export default { fmtIcSl, cboParamSummary };'
I_PF = f'''// ── {FENCE} ── compact, DISTINGUISHING one-liner for a BRK_V1 config.
// Defaults are suppressed so a sweep's cells read by what differs. Shared
// so RunComparison, BacktestQueue and Backtest.jsx render the same string.
export function brkParamSummary(cfg) {{
  if (!cfg) return "—";
  const p = [];
  p.push(`sel<${{cfg.select_below}}@${{cfg.select_time || "09:25"}}`);
  if (Number(cfg.select_min) > 0) p.push(`floor${{cfg.select_min}}`);
  p.push(`brk≥${{cfg.break_above}}${{Number(cfg.sustain_candles) > 1 ? `×${{cfg.sustain_candles}}` : ""}}`);
  p.push(`${{cfg.entry_first || "09:30"}}-${{cfg.entry_last || "09:35"}}`);
  if (cfg.both_policy && cfg.both_policy !== "first") p.push(`both:${{cfg.both_policy}}`);
  p.push(`SL${{cfg.sl_pts}} TP${{cfg.tp_pts}}`);
  if (Number(cfg.trail_trigger_pts) > 0) p.push(`trail${{cfg.trail_trigger_pts}}/${{Number(cfg.trail_lock_pts) || 0}}`);
  if (cfg.eod_square_off) p.push(`eod ${{cfg.eod_square_off}}`);
  if (cfg.skip_expiry_day) p.push("noExp");
  if (Number(cfg.lots)) p.push(`${{cfg.lots}}L`);
  return p.join(" · ");
}}

export default {{ fmtIcSl, cboParamSummary, brkParamSummary }};'''


# ═════════════════════════════════════════════════════════════════════════
#  BacktestQueue.jsx
# ═════════════════════════════════════════════════════════════════════════
A_BQ_IMP = 'import { fmtIcSl, cboParamSummary } from "./paramFormat";'
I_BQ_IMP = 'import { fmtIcSl, cboParamSummary, brkParamSummary } from "./paramFormat";'
A_BQ_BR = ('  if (cfg.both_side_policy != null && cfg.breakout_buffer_pts != null) {\n'
           '    return cboParamSummary(cfg);\n'
           '  }\n'
           '  const p = [];\n'
           '  // ── VAP_V1 ── (vwap + v1 is unique to VAP_V1 configs)')
I_BQ_BR = ('  if (cfg.both_side_policy != null && cfg.breakout_buffer_pts != null) {\n'
           '    return cboParamSummary(cfg);\n'
           '  }\n'
           f'  // ── {FENCE} ── same shared formatter as RunComparison.\n'
           '  if (cfg.select_below != null && cfg.break_above != null) {\n'
           '    return brkParamSummary(cfg);\n'
           '  }\n'
           '  const p = [];\n'
           '  // ── VAP_V1 ── (vwap + v1 is unique to VAP_V1 configs)')


# ═════════════════════════════════════════════════════════════════════════
#  RunComparison.jsx
# ═════════════════════════════════════════════════════════════════════════
A_RC_IMP = 'import { fmtIcSl, cboParamSummary } from "./paramFormat";'
I_RC_IMP = 'import { fmtIcSl, cboParamSummary, brkParamSummary } from "./paramFormat";'
A_RC_BR = ('  if (cfg.both_side_policy != null && cfg.breakout_buffer_pts != null) {\n'
           '    return cboParamSummary(cfg);\n'
           '  }\n'
           '  const parts = [];')
I_RC_BR = ('  if (cfg.both_side_policy != null && cfg.breakout_buffer_pts != null) {\n'
           '    return cboParamSummary(cfg);\n'
           '  }\n'
           f'  // ── {FENCE} ── BRK configs use their own field names.\n'
           '  if (cfg.select_below != null && cfg.break_above != null) {\n'
           '    return brkParamSummary(cfg);\n'
           '  }\n'
           '  const parts = [];')
A_RC_LBL = 'VAP_V1: "VAP", VET_V1: "VET" };'
I_RC_LBL = f'VAP_V1: "VAP", VET_V1: "VET", CBO_V1: "CBO", BRK_V1: "BRK" }};   // ── {FENCE} ──'
A_RC_EXIT = '  "MTM_TARGET", "MTM_SL", "MTM_TRAIL", "IV_SL", "IV_SL_HEDGE"];'
I_RC_EXIT = ('  "MTM_TARGET", "MTM_SL", "MTM_TRAIL", "IV_SL", "IV_SL_HEDGE",\n'
             f'  // ── {FENCE} ── BRK_V1 raised-stop exit\n'
             '  "TRAIL"];')


# ═════════════════════════════════════════════════════════════════════════
#  SweepBuilder.jsx
# ═════════════════════════════════════════════════════════════════════════
A_SB_CONST = 'VAP = "VAP_V1", VET = "VET_V1";'
I_SB_CONST = f'VAP = "VAP_V1", VET = "VET_V1", BRK = "BRK_V1";   // ── {FENCE} ──'
A_SB_AXES = '  { key: "vet_tf", label: "VET timeframe (min)", strategies: [VET],'
I_SB_AXES = f'''  // ── {FENCE} ── BRK_V1: the level, the wait window and the exits ARE
  // the strategy. Sweep the level and SL/TP first; the trail only after a
  // naked baseline exists to compare against.
  {{ key: "brk_level", label: "BRK level ₹ (select-below = break-above)", strategies: [BRK],
    hint: "150, 180, 200, 250", parse: _num,
    apply: (c, v) => {{ c.select_below = Math.abs(v); c.break_above = Math.abs(v); }}, fmt: (v) => `₹${{Math.abs(v)}}` }},
  {{ key: "brk_sl", label: "BRK SL ₹", strategies: [BRK],
    hint: "10, 15, 20, 30", parse: _num,
    apply: (c, v) => {{ c.sl_pts = Math.abs(v); }}, fmt: (v) => `SL${{Math.abs(v)}}` }},
  {{ key: "brk_tp", label: "BRK target ₹", strategies: [BRK],
    hint: "30, 40, 60, 80", parse: _num,
    apply: (c, v) => {{ c.tp_pts = Math.abs(v); }}, fmt: (v) => `TP${{Math.abs(v)}}` }},
  {{ key: "brk_last", label: "BRK entry last (HH:MM)", strategies: [BRK],
    hint: "09:30, 09:35, 09:40, 09:45", parse: _hm,
    apply: (c, v) => {{ c.entry_last = v; }}, fmt: (v) => `≤${{v}}` }},
  {{ key: "brk_sustain", label: "BRK sustain closes", strategies: [BRK],
    hint: "1, 2, 3", parse: _num,
    apply: (c, v) => {{ c.sustain_candles = Math.max(1, Math.round(v)); }}, fmt: (v) => `×${{Math.max(1, Math.round(v))}}` }},
  {{ key: "brk_trail", label: "BRK trail trigger ₹ (0=off)", strategies: [BRK],
    hint: "0, 15, 20, 30", parse: _num,
    apply: (c, v) => {{ c.trail_trigger_pts = Math.abs(v); }}, fmt: (v) => (v > 0 ? `trail${{Math.abs(v)}}` : "no trail") }},
  {{ key: "brk_eod", label: "BRK EOD square-off", strategies: [BRK],
    hint: "14:30, 15:00, 15:15, 15:25", parse: _hm,
    apply: (c, v) => {{ c.eod_square_off = v; }}, fmt: (v) => `eod ${{v}}` }},
''' + A_SB_AXES


class Abort(Exception):
    pass


def replace_n(text, old, new, what, n=1):
    got = text.count(old)
    if got != n:
        raise Abort(f"{what}: anchor found {got} times, expected {n}. "
                    f"The file has drifted — patch NOT applied.")
    return text.replace(old, new)


def esbuild_ok(path: Path, text: str, loader: str) -> None:
    tmp = path.parent / f"_brk_ui_stage{path.suffix}"
    tmp.write_text(text)
    try:
        r = subprocess.run(
            ["npx", "--yes", "esbuild", str(tmp), f"--loader:{path.suffix}={loader}",
             "--outfile=/dev/null"], capture_output=True, text=True, cwd=".")
        if r.returncode != 0:
            raise Abort(f"esbuild rejected patched {path}:\n{r.stderr[:2000]}")
    except FileNotFoundError:
        print("  WARNING: npx not found — JSX gate SKIPPED", file=sys.stderr)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-esbuild", action="store_true")
    args = ap.parse_args()

    for p in (BT, PF, BQ, RC, SB):
        if not p.exists():
            print(f"ABORTED: {p} not found — run from the repo root", file=sys.stderr)
            return 1

    staged = {}   # path -> new text
    try:
        # ── Backtest.jsx ──
        orig = BT.read_text()
        if FENCE in orig:
            print(f"  already fenced — skipped   {BT}")
        else:
            t = orig
            t = replace_n(t, A_LSKEY, I_LSKEY, "1 LS key")
            t = replace_n(t, A_SAVED, I_SAVED, "2 loader call")
            t = replace_n(t, A_RESTORE, I_RESTORE, "3 restore allow-list")
            t = replace_n(t, A_STATE, I_STATE, "4 state block")
            t = replace_n(t, A_BCARM, I_BCARM, "5 buildConfig arm")
            t = replace_n(t, A_DEPS, I_DEPS, "6 buildConfig deps")
            t = replace_n(t, A_DESC, I_DESC, "7 describeConfig")
            t = replace_n(t, A_CHIP, I_CHIP, "8 strategy chip")
            t = replace_n(t, A_PANEL, I_PANEL, "9 config panel")
            t = replace_n(t, A_HIDE, I_HIDE, "10 hide shared fields", HIDE_N)
            t = replace_n(t, A_CSV, I_CSV, "11 csv diag")
            # stale-closure assert: deps ⊆ arm reads and arm reads ⊆ deps
            names = [n.strip() for n in I_DEPS.rstrip("\n").split("\n")[-1].split(",") if n.strip()]
            arm_reads = set(re.findall(r"\bbrk[A-Z]\w*", I_BCARM))
            missing = [n for n in names if n not in arm_reads]
            unlisted = sorted(w for w in arm_reads if w not in names)
            if missing or unlisted:
                raise Abort(f"stale-closure check failed.\n"
                            f"  in deps but not read by buildConfig: {missing}\n"
                            f"  read by buildConfig but not in deps: {unlisted}")
            # TDZ audit: every brk* state used anywhere must be declared
            declared = set(re.findall(r"const \[(brk\w+), set", I_STATE))
            used = set(re.findall(r"\bbrk[A-Z]\w*", t)) - {"brkSaved"}
            used = {u for u in used if not u.startswith("brkParam")}
            undeclared = sorted(u for u in used if u not in declared)
            if undeclared:
                raise Abort(f"TDZ audit: used but never declared: {undeclared}")
            if "isBRK" not in I_STATE:
                raise Abort("isBRK not declared in the state block")
            staged[BT] = t

        # ── paramFormat.js ──
        orig = PF.read_text()
        if FENCE in orig:
            print(f"  already fenced — skipped   {PF}")
        else:
            staged[PF] = replace_n(orig, A_PF, I_PF, "paramFormat export")

        # ── BacktestQueue.jsx ──
        orig = BQ.read_text()
        if FENCE in orig:
            print(f"  already fenced — skipped   {BQ}")
        else:
            t = replace_n(orig, A_BQ_IMP, I_BQ_IMP, "BacktestQueue import")
            t = replace_n(t, A_BQ_BR, I_BQ_BR, "BacktestQueue paramLine branch")
            staged[BQ] = t

        # ── RunComparison.jsx ──
        orig = RC.read_text()
        if FENCE in orig:
            print(f"  already fenced — skipped   {RC}")
        else:
            t = replace_n(orig, A_RC_IMP, I_RC_IMP, "RunComparison import")
            t = replace_n(t, A_RC_BR, I_RC_BR, "RunComparison paramSummary branch")
            t = replace_n(t, A_RC_LBL, I_RC_LBL, "RunComparison STRAT_LABEL")
            t = replace_n(t, A_RC_EXIT, I_RC_EXIT, "RunComparison EXIT_REASON_KEYS")
            staged[RC] = t

        # ── SweepBuilder.jsx ──
        orig = SB.read_text()
        if FENCE in orig:
            print(f"  already fenced — skipped   {SB}")
        else:
            t = replace_n(orig, A_SB_CONST, I_SB_CONST, "SweepBuilder const")
            t = replace_n(t, A_SB_AXES, I_SB_AXES, "SweepBuilder axes")
            staged[SB] = t

        # ── parse gate on every staged file ──
        if not args.no_esbuild:
            for p, t in staged.items():
                esbuild_ok(p, t, "jsx" if p.suffix == ".jsx" else "js")
    except Abort as e:
        print(f"\nABORTED: {e}\nNo files were modified.", file=sys.stderr)
        return 1

    if not staged:
        print(f"\n{FENCE} no-op.")
        return 0
    for p, t in staged.items():
        if args.check:
            print(f"  would patch (clean, esbuild OK)   {p}")
        else:
            shutil.copy2(p, p.with_name(p.name + f".bak-{FENCE}"))
            p.write_text(t)
            print(f"  patched (backup .bak-{FENCE})   {p}")
    print(f"\n{FENCE} {'check complete' if args.check else 'applied'}.")
    if not args.check:
        print("\nNext:")
        print("  1. cd frontend && npm start        # dev-server smoke test FIRST")
        print("  2. select BRK V1, change SL to 25, confirm the run-config chips echo it")
        print("  3. run 2026-01-01..2026-07-20 NIFTY, read diag_brk before the P&L")
        print("  4. only then rebuild Tauri")
    return 0


if __name__ == "__main__":
    sys.exit(main())
