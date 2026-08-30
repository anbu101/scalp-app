#!/usr/bin/env python3
# apply_cbo_v1_ui_20260829.py
#
# ── CBO_V1_UI_20260829 ── wire CBO_V1 into frontend/src/pages/Backtest.jsx.
#
# Companion to apply_cbo_v1_20260829.py (backend). Run the backend one
# FIRST: without its dispatch arms the chip appears and every run 400s.
#
# NINE INSERTION POINTS, all assert-anchored to exactly-one occurrence, plus
# one deliberate replace-3 for the shared-field hide guards:
#   1  localStorage key + loader
#   2  loadCboParams() call in the component body
#   3  strategyId restore allow-list
#   4  state block + persistence effect
#   5  buildConfig arm
#   6  buildConfig dep array           <- the stale-closure rule
#   7  describeConfig arm
#   8  strategy chip
#   9  the config panel
#  10  hide the shared session/lots fields for CBO (3 sites)
#
# THE STALE-CLOSURE RULE IS THE WHOLE RISK HERE. buildConfig is a
# useCallback; any state it reads MUST land in the dep array in the SAME
# commit or the config silently freezes at its first-render values and every
# sweep runs the same numbers while the form appears to change. Point 6
# exists for that and is asserted, not optional.
#
# Frontend is a SINGLE tree (frontend/src) — no dual-tree step.
#
#     python3 apply_cbo_v1_ui_20260829.py --check
#     python3 apply_cbo_v1_ui_20260829.py

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

FENCE = "CBO_V1_UI_20260829"
TARGET = Path("frontend/src/pages/Backtest.jsx")


# ── 1. LS key + loader ───────────────────────────────────────────────────
A_LSKEY = '// ── VET_V1 END ──\n\n// ── TSG_V1 BEGIN ──'
I_LSKEY = f'''// ── VET_V1 END ──

// ── {FENCE} BEGIN ── previous-candle breakout (CBO_V1). Own LS key: an
// intraday breakout's params share nothing with the swing forms above.
const CBO_LS_KEY = "scalp_backtest_cbo_v1";
function loadCboParams() {{
  try {{ return JSON.parse(localStorage.getItem(CBO_LS_KEY)) || {{}}; }} catch {{ return {{}}; }}
}}
// ── {FENCE} END ──

// ── TSG_V1 BEGIN ──'''


# ── 2. loader call ───────────────────────────────────────────────────────
A_SAVED = '  const vetSaved = loadVetParams();     // ── VET_V1 ──'
I_SAVED = (A_SAVED + f'\n  const cboSaved = loadCboParams();     // ── {FENCE} ──')


# ── 3. strategyId restore allow-list ─────────────────────────────────────
A_RESTORE = '"TMA_V1", "TMA_V2", "VET_V1"].includes(saved.strategyId)'
I_RESTORE = '"TMA_V1", "TMA_V2", "VET_V1", "CBO_V1"].includes(saved.strategyId)'


# ── 4. state block + persistence ─────────────────────────────────────────
A_STATE = '  // ── VET_V1 END ──\n  // "run" = the existing run+config+results view'
I_STATE = f'''  // ── VET_V1 END ──

  // ── {FENCE} BEGIN ── previous-candle breakout on index SPOT.
  // A forming tf bar that touches or crosses the PREVIOUS bar's high (or
  // low) triggers, detected on 1m sub-bars and filled at the next 1m open.
  // SL is a SPOT level — the reference bar's other extreme. TP is an
  // OPTION premium move. Nothing carries overnight.
  const isCBO = strategyId === "CBO_V1";
  const [cboTf, setCboTf] = useState(cboSaved.tf ?? 5);
  const [cboTriggerSrc, setCboTriggerSrc] = useState(cboSaved.triggerSrc ?? "high");
  const [cboBothPolicy, setCboBothPolicy] = useState(cboSaved.bothPolicy ?? "pessimistic");
  const [cboBuffer, setCboBuffer] = useState(cboSaved.buffer ?? 0);
  const [cboMinRefRange, setCboMinRefRange] = useState(cboSaved.minRefRange ?? 0);
  const [cboRequireFullRef, setCboRequireFullRef] = useState(cboSaved.requireFullRef ?? false);
  const [cboDirection, setCboDirection] = useState(cboSaved.direction ?? "BOTH");
  const [cboLegAction, setCboLegAction] = useState(cboSaved.legAction ?? "BUY");
  const [cboPremMin, setCboPremMin] = useState(cboSaved.premMin ?? 100);
  const [cboPremMax, setCboPremMax] = useState(cboSaved.premMax ?? 200);
  const [cboLots, setCboLots] = useState(cboSaved.lots ?? 1);
  const [cboLotSize, setCboLotSize] = useState(cboSaved.lotSize ?? 0);
  const [cboTargetMode, setCboTargetMode] = useState(cboSaved.targetMode ?? "abs");
  const [cboTargetValue, setCboTargetValue] = useState(cboSaved.targetValue ?? 10);
  const [cboSessStart, setCboSessStart] = useState(cboSaved.sessStart ?? "09:20");
  const [cboSessEnd, setCboSessEnd] = useState(cboSaved.sessEnd ?? "15:00");
  const [cboEodTime, setCboEodTime] = useState(cboSaved.eodTime ?? "15:15");
  const [cboMaxTrades, setCboMaxTrades] = useState(cboSaved.maxTrades ?? 0);
  const [cboMtmLoss, setCboMtmLoss] = useState(cboSaved.mtmLoss ?? 0);
  const [cboMtmProfit, setCboMtmProfit] = useState(cboSaved.mtmProfit ?? 0);
  const [cboMtmIncludeOpen, setCboMtmIncludeOpen] = useState(cboSaved.mtmIncludeOpen ?? true);
  const [cboCooldown, setCboCooldown] = useState(cboSaved.cooldown ?? 0);
  const [cboSkipExpiry, setCboSkipExpiry] = useState(cboSaved.skipExpiry ?? false);
  const [cboSkewOn, setCboSkewOn] = useState(cboSaved.skewOn ?? false);
  const [cboSkewMin, setCboSkewMin] = useState(cboSaved.skewMin ?? 0);
  const [cboSkewInvert, setCboSkewInvert] = useState(cboSaved.skewInvert ?? false);
  const [cboSkewParity, setCboSkewParity] = useState(cboSaved.skewParity ?? false);
  const [cboSkewCarry, setCboSkewCarry] = useState(cboSaved.skewCarry ?? 6.5);
  useEffect(() => {{
    try {{ localStorage.setItem(CBO_LS_KEY, JSON.stringify({{ tf: cboTf, triggerSrc: cboTriggerSrc, bothPolicy: cboBothPolicy, buffer: cboBuffer, minRefRange: cboMinRefRange, requireFullRef: cboRequireFullRef, direction: cboDirection, legAction: cboLegAction, premMin: cboPremMin, premMax: cboPremMax, lots: cboLots, lotSize: cboLotSize, targetMode: cboTargetMode, targetValue: cboTargetValue, sessStart: cboSessStart, sessEnd: cboSessEnd, eodTime: cboEodTime, maxTrades: cboMaxTrades, mtmLoss: cboMtmLoss, mtmProfit: cboMtmProfit, mtmIncludeOpen: cboMtmIncludeOpen, cooldown: cboCooldown, skipExpiry: cboSkipExpiry, skewOn: cboSkewOn, skewMin: cboSkewMin, skewInvert: cboSkewInvert, skewParity: cboSkewParity, skewCarry: cboSkewCarry }})); }} catch {{ /* ignore */ }}
  }}, [cboTf, cboTriggerSrc, cboBothPolicy, cboBuffer, cboMinRefRange, cboRequireFullRef, cboDirection, cboLegAction, cboPremMin, cboPremMax, cboLots, cboLotSize, cboTargetMode, cboTargetValue, cboSessStart, cboSessEnd, cboEodTime, cboMaxTrades, cboMtmLoss, cboMtmProfit, cboMtmIncludeOpen, cboCooldown, cboSkipExpiry, cboSkewOn, cboSkewMin, cboSkewInvert, cboSkewParity, cboSkewCarry]);
  // ── {FENCE} END ──
  // "run" = the existing run+config+results view'''


# ── 5. buildConfig arm ───────────────────────────────────────────────────
A_BCARM = '    if (sid === "VET_V1") {'
I_BCARM = f'''    if (sid === "CBO_V1") {{
      // ── {FENCE} ── both_side_policy + breakout_buffer_pts is the
      // describeConfig detection key — disjoint from every other shape on
      // this page (VET is trend_len+range_len, TMA is ema/ema4, PST legs).
      return {{
        timeframe_minutes: Number(cboTf) || 5,
        trigger_source: cboTriggerSrc,
        both_side_policy: cboBothPolicy,
        breakout_buffer_pts: Number(cboBuffer) || 0,
        min_ref_range_pts: Number(cboMinRefRange) || 0,
        require_full_ref: !!cboRequireFullRef,
        direction: cboDirection,
        leg_action: cboLegAction,
        option_premium: {{ min: Number(cboPremMin) || 0, max: Number(cboPremMax) || 0 }},
        lots: Number(cboLots) || 1,
        lot_size: Number(cboLotSize) || 0,
        target_mode: cboTargetMode,
        target_value: Number(cboTargetValue) || 0,
        session_start: cboSessStart,
        session_end: cboSessEnd,
        eod_square_off: cboEodTime,
        max_trades_per_day: Number(cboMaxTrades) || 0,
        mtm_loss_cap: Number(cboMtmLoss) || 0,
        mtm_profit_cap: Number(cboMtmProfit) || 0,
        mtm_include_open: !!cboMtmIncludeOpen,
        cooldown_minutes: Number(cboCooldown) || 0,
        skip_expiry_day: !!cboSkipExpiry,
        atm_skew_filter: {{
          enabled: !!cboSkewOn,
          min_diff_pts: Number(cboSkewMin) || 0,
          invert: !!cboSkewInvert,
          parity_adjust: !!cboSkewParity,
          carry_pts: Number(cboSkewCarry) || 6.5,
        }},
      }};
    }}
    if (sid === "VET_V1") {{'''


# ── 6. buildConfig dep array (the stale-closure rule) ────────────────────
# Anchor is the FULL line including its trailing comment. Anchoring only the
# prefix put the insertion mid-comment and left the comment's tail as bare
# tokens inside the array — esbuild caught it at the staging gate.
A_DEPS = ('    vetScrOn, vetScrEmaFast, vetScrEmaSlow, vetScrSmaTrend, '
          'vetScrVolSma, vetScrMinVolume, vetScrWindow,   // ── VET_V1 ── '
          'stale-closure rule: buildConfig reads them, so they land here in '
          'the SAME commit')
I_DEPS = (A_DEPS + f'''
    // ── {FENCE} ── STALE-CLOSURE RULE: buildConfig reads every one of
    // these, so they land in the dep array in the SAME commit. Omit one and
    // the config silently freezes at its first-render value while the form
    // keeps appearing to change — every sweep cell then runs identical
    // numbers under different labels.
    cboTf, cboTriggerSrc, cboBothPolicy, cboBuffer, cboMinRefRange, cboRequireFullRef, cboDirection, cboLegAction, cboPremMin, cboPremMax, cboLots, cboLotSize, cboTargetMode, cboTargetValue, cboSessStart, cboSessEnd, cboEodTime, cboMaxTrades, cboMtmLoss, cboMtmProfit, cboMtmIncludeOpen, cboCooldown, cboSkipExpiry, cboSkewOn, cboSkewMin, cboSkewInvert, cboSkewParity, cboSkewCarry,''')


# ── 7. describeConfig arm ────────────────────────────────────────────────
A_DESC = '  // ── VET_V1 ── (trend_len + range_len is unique to VET configs)'
I_DESC = f'''  // ── {FENCE} ── (both_side_policy is unique to CBO configs)
  if (cfg.both_side_policy != null && cfg.breakout_buffer_pts != null) {{
    add("Signal", `prev-${{cfg.timeframe_minutes || 5}}m-candle breakout`);
    add("Trigger", cfg.trigger_source === "close" ? "sub-bar CLOSE through" : "touch/cross (intrabar)");
    if (Number(cfg.breakout_buffer_pts) > 0) add("Buffer", `${{cfg.breakout_buffer_pts}}pt`);
    if (Number(cfg.min_ref_range_pts) > 0) add("MinRef", `${{cfg.min_ref_range_pts}}pt`);
    add("Leg", cfg.leg_action === "SELL" ? "option SELLING (opposite side)" : "option buying");
    add("Direction", cfg.direction || "BOTH");
    if (cfg.option_premium) add("Premium", `${{cfg.option_premium.min}}–${{cfg.option_premium.max}}`);
    if (cfg.lots) add("Lots", cfg.lots);
    add("Target", cfg.target_mode === "pct" ? `${{cfg.target_value}}% of entry` : `₹${{cfg.target_value}}`);
    add("SL", "prev-candle spot level");
    add("Ambiguous", cfg.both_side_policy === "pessimistic" ? "forced LOSS" : cfg.both_side_policy);
    if (cfg.session_start && cfg.session_end) add("Sess", `${{cfg.session_start}}–${{cfg.session_end}}`);
    if (cfg.eod_square_off) add("EOD", cfg.eod_square_off);
    if (Number(cfg.max_trades_per_day) > 0) add("MaxDay", cfg.max_trades_per_day);
    if (Number(cfg.mtm_loss_cap) > 0) add("MTM loss", `₹${{cfg.mtm_loss_cap}}`);
    if (Number(cfg.mtm_profit_cap) > 0) add("MTM profit", `₹${{cfg.mtm_profit_cap}}`);
    if (cfg.atm_skew_filter && cfg.atm_skew_filter.enabled) {{
      add("ATM skew", `${{cfg.atm_skew_filter.parity_adjust ? "parity-adj" : "raw"}}${{cfg.atm_skew_filter.invert ? " INVERTED" : ""}} ≥${{cfg.atm_skew_filter.min_diff_pts}}`);
    }}
    if (cfg.skip_expiry_day) add("Expiry", "skipped");
    return out;
  }}
  // ── VET_V1 ── (trend_len + range_len is unique to VET configs)'''


# ── 8. strategy chip ─────────────────────────────────────────────────────
A_CHIP = '          { id: "VET_V1", label: "VET V1", sub: "EMA trend + regime" },   // ── VET_V1 ──'
I_CHIP = (A_CHIP +
          f'\n          {{ id: "CBO_V1", label: "CBO V1", sub: "prev-candle breakout" }},   // ── {FENCE} ──')


# ── 9. config panel ──────────────────────────────────────────────────────
A_PANEL = '          {isVET && ('
I_PANEL = f'''          {{isCBO && (
            /* ── {FENCE} BEGIN ── previous-candle breakout. Shared
               session/lots fields are HIDDEN for CBO — everything the
               runner reads is defined here. */
            <div style={{{{ gridColumn: "1 / -1", marginTop: 8 }}}}>
              <div style={{{{ display: "flex", gap: spacing.md, marginBottom: spacing.md, flexWrap: "wrap" }}}}>
                <Field label="Timeframe">
                  <select style={{inputStyle}} value={{cboTf}} onChange={{(e) => setCboTf(Number(e.target.value))}}
                    title="The candle whose high/low becomes the breakout reference. Detection always runs on 1m sub-bars regardless of this value.">
                    {{[1, 3, 5, 10, 15].map((v) => <option key={{v}} value={{v}}>{{v}}m</option>)}}
                  </select>
                </Field>
                <Field label="Trigger">
                  <select style={{inputStyle}} value={{cboTriggerSrc}} onChange={{(e) => setCboTriggerSrc(e.target.value)}}
                    title="touch/cross = the spec as written: the forming bar's running high reaching the reference fires, wicks included. close = the 1m sub-bar must CLOSE through the level. close is strictly fewer and later signals.">
                    <option value="high">touch / cross (intrabar)</option>
                    <option value="close">sub-bar close through</option>
                  </select>
                </Field>
                <Field label="Direction">
                  <select style={{inputStyle}} value={{cboDirection}} onChange={{(e) => setCboDirection(e.target.value)}}>
                    <option value="BOTH">BOTH</option><option value="UP">UP only</option><option value="DOWN">DOWN only</option>
                  </select>
                </Field>
                <Field label="Leg action">
                  <select style={{inputStyle}} value={{cboLegAction}} onChange={{(e) => setCboLegAction(e.target.value)}}
                    title="BUY = long the side the breakout points at (UP -> CE). SELL = the SAME directional view expressed with the OPPOSITE short contract (UP -> short PE), the VET convention.">
                    <option value="BUY">BUY (long option)</option>
                    <option value="SELL">SELL (short opposite)</option>
                  </select>
                </Field>
                <Field label="Outside bar">
                  <select style={{inputStyle}} value={{cboBothPolicy}} onChange={{(e) => setCboBothPolicy(e.target.value)}}
                    title="What to do when ONE 1m bar breaches the previous high AND low. Order is unknowable at 1m. pessimistic = enter and book an immediate stop-out (the tie-break already agreed for exits). skip = take nothing. up/down force a side and introduce bias.">
                    <option value="pessimistic">pessimistic (forced loss)</option>
                    <option value="skip">skip (take nothing)</option>
                    <option value="up">force UP</option><option value="down">force DOWN</option>
                  </select>
                </Field>
              </div>
              <div style={{{{ display: "flex", gap: spacing.md, marginBottom: spacing.md, flexWrap: "wrap" }}}}>
                <Field label="Premium min"><input type="number" style={{inputStyle}} value={{cboPremMin}} onChange={{(e) => setCboPremMin(Number(e.target.value))}} title="Selection band. NOTE this does more than pick a strike: the selector infers ATM as the MEDIAN of strikes that passed the band, so the band also moves what counts as ATM." /></Field>
                <Field label="Premium max"><input type="number" style={{inputStyle}} value={{cboPremMax}} onChange={{(e) => setCboPremMax(Number(e.target.value))}} /></Field>
                <Field label="Lots"><input type="number" style={{inputStyle}} value={{cboLots}} onChange={{(e) => setCboLots(Number(e.target.value))}} /></Field>
                <Field label="Lot size (0 = auto)"><input type="number" style={{inputStyle}} value={{cboLotSize}} onChange={{(e) => setCboLotSize(Number(e.target.value))}} title="0 = the index constant (NIFTY 65, BANKNIFTY 35)." /></Field>
                <Field label="Target mode">
                  <select style={{inputStyle}} value={{cboTargetMode}} onChange={{(e) => setCboTargetMode(e.target.value)}}
                    title="abs = rupees of OPTION premium. pct = a percentage of the entry premium; on a short, of the premium collected.">
                    <option value="abs">absolute ₹</option><option value="pct">% of entry</option>
                  </select>
                </Field>
                <Field label={{cboTargetMode === "pct" ? "Target %" : "Target ₹"}}><input type="number" style={{inputStyle}} value={{cboTargetValue}} onChange={{(e) => setCboTargetValue(Number(e.target.value))}} /></Field>
              </div>
              <div style={{{{ display: "flex", gap: spacing.md, marginBottom: spacing.md, flexWrap: "wrap" }}}}>
                <Field label="Session start"><input type="text" style={{inputStyle}} value={{cboSessStart}} onChange={{(e) => setCboSessStart(e.target.value)}} /></Field>
                <Field label="Session end"><input type="text" style={{inputStyle}} value={{cboSessEnd}} onChange={{(e) => setCboSessEnd(e.target.value)}} title="Last minute a NEW entry may fire. Exits are not gated by this." /></Field>
                <Field label="EOD square-off"><input type="text" style={{inputStyle}} value={{cboEodTime}} onChange={{(e) => setCboEodTime(e.target.value)}} title="MUST match the live cron exactly. A mismatch here is the SCALP_V5 parity break: its 267 EOD trades carried 100% of net P&L. Must be AFTER session end or the run aborts." /></Field>
                <Field label="Max trades/day"><input type="number" style={{inputStyle}} value={{cboMaxTrades}} onChange={{(e) => setCboMaxTrades(Number(e.target.value))}} title="0 = unlimited." /></Field>
                <Field label="Cooldown (min)"><input type="number" style={{inputStyle}} value={{cboCooldown}} onChange={{(e) => setCboCooldown(Number(e.target.value))}} title="Minutes to wait after an exit before a new entry may fire. 0 = off." /></Field>
              </div>
              <div style={{{{ display: "flex", gap: spacing.md, marginBottom: spacing.md, flexWrap: "wrap", alignItems: "center" }}}}>
                <Field label="MTM loss cap ₹"><input type="number" style={{inputStyle}} value={{cboMtmLoss}} onChange={{(e) => setCboMtmLoss(Number(e.target.value))}} title="0 = off. On breach the open position is flattened immediately and no new entry is taken that day." /></Field>
                <Field label="MTM profit cap ₹"><input type="number" style={{inputStyle}} value={{cboMtmProfit}} onChange={{(e) => setCboMtmProfit(Number(e.target.value))}} title="0 = off. Same flatten-and-halt behaviour." /></Field>
                <label style={{{{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}}} title="ON = the cap watches realised + OPEN MTM. OFF = realised only, so a large unrealised loss will not trigger it.">
                  <input type="checkbox" checked={{cboMtmIncludeOpen}} onChange={{(e) => setCboMtmIncludeOpen(e.target.checked)}} /> include open MTM
                </label>
                <label style={{{{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}}}>
                  <input type="checkbox" checked={{cboSkipExpiry}} onChange={{(e) => setCboSkipExpiry(e.target.checked)}} /> skip expiry day
                </label>
                <label style={{{{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}}} title="Refuse a reference bar built from fewer than tf sub-bars, so a corpus gap cannot produce an artificially narrow high/low.">
                  <input type="checkbox" checked={{cboRequireFullRef}} onChange={{(e) => setCboRequireFullRef(e.target.checked)}} /> require full ref bar
                </label>
              </div>
              <div style={{{{ display: "flex", gap: spacing.md, marginBottom: spacing.md, flexWrap: "wrap", alignItems: "center" }}}}>
                <Field label="Breakout buffer (pt)"><input type="number" style={{inputStyle}} value={{cboBuffer}} onChange={{(e) => setCboBuffer(Number(e.target.value))}} title="0 keeps 'touch' inclusive. Above 0 the level must be exceeded by this much — the lever for testing whether exact-touch fills are noise." /></Field>
                <Field label="Min ref range (pt)"><input type="number" style={{inputStyle}} value={{cboMinRefRange}} onChange={{(e) => setCboMinRefRange(Number(e.target.value))}} title="Ignore reference bars narrower than this. A near-doji reference makes BOTH levels reachable in one minute, which under the pessimistic policy books a forced loss." /></Field>
                <label style={{{{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}}} title="The 'ATM CE > ATM PE' condition. Read the note below before trusting it.">
                  <input type="checkbox" checked={{cboSkewOn}} onChange={{(e) => setCboSkewOn(e.target.checked)}} /> ATM skew gate
                </label>
                {{cboSkewOn && (<>
                  <Field label="Min diff (pt)"><input type="number" style={{inputStyle}} value={{cboSkewMin}} onChange={{(e) => setCboSkewMin(Number(e.target.value))}} /></Field>
                  <label style={{{{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}}} title="Subtract (spot − strike) and the carry so only RESIDUAL richness counts.">
                    <input type="checkbox" checked={{cboSkewParity}} onChange={{(e) => setCboSkewParity(e.target.checked)}} /> parity-adjust
                  </label>
                  {{cboSkewParity && (
                    <Field label="Carry (pt)"><input type="number" step="0.1" style={{inputStyle}} value={{cboSkewCarry}} onChange={{(e) => setCboSkewCarry(Number(e.target.value))}} title="MEASURED on this corpus (SCALP_V1 found 6.57), not fitted per run." /></Field>
                  )}}
                  <label style={{{{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}}}>
                    <input type="checkbox" checked={{cboSkewInvert}} onChange={{(e) => setCboSkewInvert(e.target.checked)}} /> invert
                  </label>
                </>)}}
              </div>
              <div style={{{{ marginTop: 6, fontSize: 11, color: colors.text.tertiary }}}}>
                Signal: while a {{cboTf}}m bar is forming, its running {{cboDirection === "DOWN" ? "low" : "high"}} touching or crossing the PREVIOUS {{cboTf}}m bar's {{cboDirection === "DOWN" ? "low" : "high"}} fires. Detection is at the close of a 1m sub-bar; the fill is the NEXT 1m open, so nothing is ever filled on information from an unfinished bar. Stop is the reference bar's opposite extreme — a SPOT level — while the target is an OPTION premium move; when both are touched inside one minute the STOP wins. The reference resets daily, so there is no warmup-seeding requirement and no cold-start gap. {{cboBothPolicy === "pessimistic" ? "An outside 1m bar breaching both levels is entered and booked as an immediate stop-out." : `Outside-bar policy: ${{cboBothPolicy}}.`}}
                {{cboSkewOn && !cboSkewParity && <><br /><b>ATM skew, raw mode:</b> put-call parity makes “ATM CE dearer than ATM PE” almost exactly “spot &gt; strike”, i.e. spot mod 50 &lt; 25 — where the strike grid happens to fall, not a directional signal. It will veto roughly half of all breakouts at random. Tick parity-adjust to measure residual richness instead, and treat the raw result as a control arm rather than a filter.</>}}
                <br /><b>Before reading the P&amp;L:</b> check ambiguous_pnl_share_pct, eod_pnl_share_pct and mtm_cap_pnl_share_pct in the run diag. If any one of them carries most of net, the run is describing that mechanism rather than the breakout rule.
              </div>
            </div>
            /* ── {FENCE} END ── */
          )}}
          {{isVET && ('''


# ── 10. hide shared session/lots fields for CBO (3 sites) ────────────────
A_HIDE = '!isGC && !isVET && ('
I_HIDE = '!isGC && !isVET && !isCBO && ('
HIDE_N = 3


class Abort(Exception):
    pass


def replace_n(text, old, new, what, n=1):
    got = text.count(old)
    if got != n:
        raise Abort(f"{what}: anchor found {got} times, expected {n}. "
                    f"The file has drifted — patch NOT applied.")
    return text.replace(old, new)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-esbuild", action="store_true",
                    help="skip the JSX parse gate (not recommended)")
    args = ap.parse_args()

    if not TARGET.exists():
        print(f"ABORTED: {TARGET} not found — run from the repo root",
              file=sys.stderr)
        return 1
    orig = TARGET.read_text()
    if FENCE in orig:
        print(f"  already fenced — skipped   {TARGET}")
        print(f"\n{FENCE} no-op.")
        return 0

    try:
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
    except Abort as e:
        print(f"\nABORTED: {e}\nNo files were modified.", file=sys.stderr)
        return 1

    # ── STALE-CLOSURE ASSERT ── every state name read by the CBO
    # buildConfig arm must also appear in the dep array. This is checked
    # mechanically because the failure is SILENT: a missing name freezes
    # that field at its first render and no error is ever raised.
    names = [n.strip() for n in I_DEPS.split("\n")[-1].split(",") if n.strip()]
    arm = I_BCARM
    missing = [n for n in names if n not in arm]
    unlisted = [w for w in set(
        __import__("re").findall(r"\bcbo[A-Z]\w*", arm)) if w not in names]
    if missing or unlisted:
        print(f"\nABORTED: stale-closure check failed.\n"
              f"  in deps but not read by buildConfig: {missing}\n"
              f"  read by buildConfig but not in deps: {unlisted}",
              file=sys.stderr)
        return 1

    # ── JSX PARSE GATE ── esbuild must accept the file before it is written.
    if not args.no_esbuild:
        tmp = TARGET.parent / "_cbo_ui_stage.jsx"
        tmp.write_text(t)
        try:
            r = subprocess.run(
                ["npx", "--yes", "esbuild", str(tmp), "--loader:.jsx=jsx",
                 "--outfile=/dev/null"],
                capture_output=True, text=True, cwd=".")
            if r.returncode != 0:
                print(f"\nABORTED: esbuild rejected the patched file:\n"
                      f"{r.stderr[:2000]}\nNo files were modified.",
                      file=sys.stderr)
                return 1
        except FileNotFoundError:
            print("  WARNING: npx not found — JSX gate SKIPPED", file=sys.stderr)
        finally:
            tmp.unlink(missing_ok=True)

    if args.check:
        print(f"  would patch (clean, esbuild OK)   {TARGET}")
        print(f"\n{FENCE} check complete.")
        return 0

    shutil.copy2(TARGET, TARGET.with_suffix(f".jsx.bak-{FENCE}"))
    TARGET.write_text(t)
    print(f"  patched (backup .bak-{FENCE})   {TARGET}")
    print(f"\n{FENCE} applied.\n\nNext:")
    print("  1. cd frontend && npm start        # dev-server smoke test FIRST")
    print("  2. select CBO V1, change a field, confirm the run config echoes it")
    print("  3. only then rebuild Tauri")
    return 0


if __name__ == "__main__":
    sys.exit(main())
