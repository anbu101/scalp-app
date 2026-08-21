#!/usr/bin/env python3
# apply_vap_v1_20260820.py
#
# Wires VAP_V1 (anchored option-VWAP, BUY/SELL, intraday) into the backtest
# surface. NEW ENGINE FILES ARE NOT TOUCHED HERE — they are delivered
# separately as whole files under backend/app/backtest/vap/.
#
# ASSERT-ANCHORED: every edit fails LOUDLY on a missed or ambiguous anchor
# and NOTHING is written unless all of them resolve. A partial application
# across six hand-maintained files is the failure mode this guards against.
#
# Run from the repo root:   python3 apply_vap_v1_20260820.py
# Dry run (report only):    python3 apply_vap_v1_20260820.py --dry-run
#
# Dual-tree: run again with --tree desktop/src-tauri to apply the two BACKEND
# edits to the bundled copy (the frontend has no second tree).

from __future__ import annotations

import io
import sys
from pathlib import Path

DRY = "--dry-run" in sys.argv
TREE = Path(".")
if "--tree" in sys.argv:
    TREE = Path(sys.argv[sys.argv.index("--tree") + 1])
BACKEND_ONLY = TREE != Path(".")

EDITS: list[tuple[str, str, str, str]] = []   # (path, label, old, new)


def edit(path: str, label: str, old: str, new: str) -> None:
    EDITS.append((path, label, old, new))


# ══════════════════════════════════════════════════════════════════════
# 1. backend/app/backtest/queue_worker.py — dispatch arm
# ══════════════════════════════════════════════════════════════════════
QW = "backend/app/backtest/queue_worker.py"
edit(QW, "queue_worker VAP_V1 dispatch arm",
     '''    from app.backtest.runner.backtest_runner import run_backtest
    return run_backtest(''',
     '''    if strategy_id == "VAP_V1":
        # ── VAP_V1 ── anchored VWAP on the OPTION premium (5m signals);
        # BUY the signal side, or SELL the opposite side + same-side hedge.
        # Intraday only. Keep this chain in sync with backtest_routes — two
        # hand-maintained copies.
        from app.backtest.vap.backtest_vap_runner import run_vap_backtest
        vap = run_vap_backtest(db_path=str(db), strategy_id=strategy_id, underlying=underlying,
                               date_from=df, date_to=dt, config_override=(config or {}),
                               progress_cb=progress_cb, cancel_cb=cancel_cb)
        return {"run_id": vap["run_id"], "summary": vap["summary"],
                "config": vap.get("config", (config or {})), "trades": vap["trades"],
                "strategy_id": strategy_id,
                # ── ABORT_REASON_PASSTHROUGH ── same contract as the IC arm
                "aborted": vap.get("aborted"), "reason": vap.get("reason")}

    from app.backtest.runner.backtest_runner import run_backtest
    return run_backtest(''')

# ══════════════════════════════════════════════════════════════════════
# 2. backend/app/api/backtest_routes.py — allowlist + dispatch arm
# ══════════════════════════════════════════════════════════════════════
BR = "backend/app/api/backtest_routes.py"
edit(BR, "backtest_routes allowlist tuple",
     '''"PST_SELL", "PST_HEDGE", "TMA_V1", "TMA_V2", "BB_V1", "BB_V2"):
        raise HTTPException(400, "Supported: SCALP_V1, SCALP_V3, SCALP_V5, HA_V1, HA_SELL, IC_V1, IC_V2, TSG_V1, GC_V1, PST_SELL, PST_HEDGE, TMA_V1, TMA_V2, BB_V1, BB_V2")''',
     '''"PST_SELL", "PST_HEDGE", "TMA_V1", "TMA_V2", "VAP_V1", "BB_V1", "BB_V2"):
        raise HTTPException(400, "Supported: SCALP_V1, SCALP_V3, SCALP_V5, HA_V1, HA_SELL, IC_V1, IC_V2, TSG_V1, GC_V1, PST_SELL, PST_HEDGE, TMA_V1, TMA_V2, VAP_V1, BB_V1, BB_V2")''')

edit(BR, "backtest_routes VAP_V1 dispatch arm",
     '''                    # ── TMA_V2 END ──
                elif req.strategy_id == "TSG_V1":''',
     '''                    # ── TMA_V2 END ──
                elif req.strategy_id == "VAP_V1":
                    # ── VAP_V1 BEGIN ── anchored VWAP on the OPTION
                    # premium (5m signals, contracts fixed at 09:20 so the
                    # anchor never restarts); BUY the signal side or SELL
                    # the opposite side + same-side deeper-OTM hedge.
                    # Intraday only. Keep this chain in sync with
                    # queue_worker._dispatch_run_impl (two hand-maintained
                    # copies — the IC omission happened twice).
                    from app.utils.app_paths import APP_HOME
                    from app.backtest.vap.backtest_vap_runner import run_vap_backtest
                    db = APP_HOME / "backtest" / "backtest.db"
                    vap = run_vap_backtest(
                        db_path=str(db), strategy_id=req.strategy_id,
                        underlying=req.underlying, date_from=df, date_to=dt,
                        config_override=(req.config_override or {}), progress_cb=_cb,
                        cancel_cb=lambda: _JOBS.run.get("cancel", False),
                    )
                    result = {
                        "run_id": vap["run_id"], "summary": vap["summary"],
                        "config": vap.get("config", (req.config_override or {})),
                        "trades": vap["trades"], "strategy_id": req.strategy_id,
                        # ── ABORT_REASON_PASSTHROUGH ── see TMA block above
                        "aborted": vap.get("aborted"), "reason": vap.get("reason"),
                    }
                    # ── VAP_V1 END ──
                elif req.strategy_id == "TSG_V1":''')

# ══════════════════════════════════════════════════════════════════════
# 3. frontend/src/pages/Backtest.jsx
# ══════════════════════════════════════════════════════════════════════
BT = "frontend/src/pages/Backtest.jsx"

edit(BT, "Backtest.jsx describeConfig VAP block",
     '''  // ── IC_V1 ──
  // ── TMA_V2 ── (ema4 + s1 is unique to TMA_V2 configs)''',
     '''  // ── IC_V1 ──
  // ── VAP_V1 ── (vwap + v1 is unique to VAP_V1 configs)
  if (cfg.vwap && cfg.v1) {
    add("Mode", cfg.mode === "SELL" ? "SELL opposite + hedge" : "BUY signal side");
    add("Signal", `VWAP <${cfg.signal_premium_max}${Number(cfg.min_premium) > 0 ? ` ≥${cfg.min_premium}` : ""} @${cfg.selection_time}`);
    if (Number(cfg.vwap_buffer_pct) > 0) add("Buffer", `${cfg.vwap_buffer_pct}%`);
    add("Sides", cfg.allow_both_sides === false ? "One slot" : "CE+PE");
    if (cfg.require_arm_first) add("Arm first", "ON");
    add("SL", cfg.sl_mode === "ATR" ? `ATR${cfg.atr_period}×${cfg.atr_mult}` : `${cfg.sl_pct}%`);
    if (Number(cfg.max_sl_pct) > 0) add("SL cap", `${cfg.max_sl_pct}%`);
    add("TP", cfg.tp_mode === "RR" ? `RR ${cfg.rr}` : `${cfg.tp_pct}%`);
    if (cfg.mode === "SELL") add("Short", `<${(cfg.v1.main || {}).premium_max} ${(cfg.v1.main || {}).lots}L`);
    else add("Buy", `${(cfg.v1.main || {}).lots}L`);
    if (cfg.mode === "SELL") add("Hedge", `<${(cfg.v1.hedge || {}).premium_max} ${(cfg.v1.hedge || {}).lots}L`);
    if (cfg.mode === "SELL" && cfg.wing_mode && cfg.wing_mode !== "synthetic") add("Wing", cfg.wing_mode === "skip" ? "Skip" : "RealFB");
    if (Number(cfg.v1.max_trades_per_day)) add("Cap/leg", cfg.v1.max_trades_per_day);
    if (cfg.session_start && cfg.session_end) add("Entries", `${cfg.session_start}–${cfg.session_end}`);
    if (cfg.exit_time) add("EOD", cfg.exit_time);
    return out;
  }
  // ── TMA_V2 ── (ema4 + s1 is unique to TMA_V2 configs)''')

edit(BT, "Backtest.jsx VAP localStorage template",
     '''function loadTma2Params() {
  try { return JSON.parse(localStorage.getItem(TMA2_LS_KEY)) || {}; } catch { return {}; }
}
// ── TMA_V2 END ──''',
     '''function loadTma2Params() {
  try { return JSON.parse(localStorage.getItem(TMA2_LS_KEY)) || {}; } catch { return {}; }
}
// ── TMA_V2 END ──

// ── VAP_V1 BEGIN ── anchored option-VWAP template + own LS key
const VAP_LS_KEY = "scalp_backtest_vap_v1";
const DEFAULT_VAP_MAIN = { premium_max: 200, lots: 1 };
const DEFAULT_VAP_HEDGE = { premium_max: 3, lots: 1 };
function loadVapParams() {
  try { return JSON.parse(localStorage.getItem(VAP_LS_KEY)) || {}; } catch { return {}; }
}
// ── VAP_V1 END ──''')

edit(BT, "Backtest.jsx vapSaved loader",
     '''  const tma2Saved = loadTma2Params();   // ── TMA_V2 ──''',
     '''  const tma2Saved = loadTma2Params();   // ── TMA_V2 ──
  const vapSaved = loadVapParams();     // ── VAP_V1 ──''')

edit(BT, "Backtest.jsx strategy allowlist",
     '''"TMA_V1", "TMA_V2"].includes(saved.strategyId) ? saved.strategyId : "SCALP_V1"''',
     '''"TMA_V1", "TMA_V2", "VAP_V1"].includes(saved.strategyId) ? saved.strategyId : "SCALP_V1"''')

edit(BT, "Backtest.jsx VAP state block",
     '''  const setTma2Leg = useCallback((leg, key, val) => {
    (leg === "main" ? setTma2Main : setTma2Hedge)((c) => ({ ...c, [key]: val }));
  }, []);
  // ── TMA_V2 END ──''',
     '''  const setTma2Leg = useCallback((leg, key, val) => {
    (leg === "main" ? setTma2Main : setTma2Hedge)((c) => ({ ...c, [key]: val }));
  }, []);
  // ── TMA_V2 END ──

  // ── VAP_V1 BEGIN ── anchored VWAP on the OPTION premium. One CE and one
  // PE contract are picked at selection_time and HELD all day (the VWAP
  // anchor belongs to a specific contract). BUY trades the signal side;
  // SELL trades the OPPOSITE side + a same-side deeper-OTM hedge, so the
  // state machine watches one series while SL/TP sit on another.
  const isVAP = strategyId === "VAP_V1";
  const [vapMode, setVapMode] = useState(vapSaved.mode === "SELL" ? "SELL" : "BUY");
  const [vapSigPrem, setVapSigPrem] = useState(vapSaved.sigPrem ?? 200);
  const [vapMinPrem, setVapMinPrem] = useState(vapSaved.minPrem ?? 60);
  const [vapSelTime, setVapSelTime] = useState(vapSaved.selTime ?? "09:20");
  const [vapBothSides, setVapBothSides] = useState(vapSaved.bothSides ?? true);
  const [vapArmFirst, setVapArmFirst] = useState(vapSaved.armFirst ?? false);
  const [vapBuffer, setVapBuffer] = useState(vapSaved.buffer ?? 0);
  const [vapSlMode, setVapSlMode] = useState(vapSaved.slMode ?? "PCT");
  const [vapSlPct, setVapSlPct] = useState(vapSaved.slPct ?? 25);
  const [vapAtrPeriod, setVapAtrPeriod] = useState(vapSaved.atrPeriod ?? 6);
  const [vapAtrMult, setVapAtrMult] = useState(vapSaved.atrMult ?? 1.5);
  const [vapMaxSl, setVapMaxSl] = useState(vapSaved.maxSl ?? 35);
  const [vapTpMode, setVapTpMode] = useState(vapSaved.tpMode ?? "RR");
  const [vapRr, setVapRr] = useState(vapSaved.rr ?? 1.5);
  const [vapTpPct, setVapTpPct] = useState(vapSaved.tpPct ?? 40);
  const [vapSessStart, setVapSessStart] = useState(vapSaved.sessStart ?? "09:30");
  const [vapSessEnd, setVapSessEnd] = useState(vapSaved.sessEnd ?? "14:45");
  const [vapExitTime, setVapExitTime] = useState(vapSaved.exitTime ?? "15:15");
  const [vapMain, setVapMain] = useState({ ...DEFAULT_VAP_MAIN, ...(vapSaved.main || {}) });
  const [vapHedge, setVapHedge] = useState({ ...DEFAULT_VAP_HEDGE, ...(vapSaved.hedge || {}) });
  const [vapMaxDay, setVapMaxDay] = useState(vapSaved.maxDay ?? 3);
  const [vapWingMode, setVapWingMode] = useState(vapSaved.wingMode ?? "synthetic");
  useEffect(() => {
    try { localStorage.setItem(VAP_LS_KEY, JSON.stringify({ mode: vapMode, sigPrem: vapSigPrem, minPrem: vapMinPrem, selTime: vapSelTime, bothSides: vapBothSides, armFirst: vapArmFirst, buffer: vapBuffer, slMode: vapSlMode, slPct: vapSlPct, atrPeriod: vapAtrPeriod, atrMult: vapAtrMult, maxSl: vapMaxSl, tpMode: vapTpMode, rr: vapRr, tpPct: vapTpPct, sessStart: vapSessStart, sessEnd: vapSessEnd, exitTime: vapExitTime, main: vapMain, hedge: vapHedge, maxDay: vapMaxDay, wingMode: vapWingMode })); } catch { /* ignore */ }
  }, [vapMode, vapSigPrem, vapMinPrem, vapSelTime, vapBothSides, vapArmFirst, vapBuffer, vapSlMode, vapSlPct, vapAtrPeriod, vapAtrMult, vapMaxSl, vapTpMode, vapRr, vapTpPct, vapSessStart, vapSessEnd, vapExitTime, vapMain, vapHedge, vapMaxDay, vapWingMode]);
  const setVapLeg = useCallback((leg, key, val) => {
    (leg === "main" ? setVapMain : setVapHedge)((c) => ({ ...c, [key]: val }));
  }, []);
  // ── VAP_V1 END ──''')

edit(BT, "Backtest.jsx buildConfig VAP arm",
     '''    if (sid === "TMA_V2") {''',
     '''    if (sid === "VAP_V1") {
      // ── VAP_V1 ── vwap + v1 is the describeConfig detection key —
      // disjoint from ema (TMA_V1), ema4 (TMA_V2), legs (PST), signal_tf.
      return {
        tf_minutes: 5,
        vwap: { source: "OPTION", price: "TYPICAL", anchor: "09:15" },
        mode: vapMode === "SELL" ? "SELL" : "BUY",
        signal_premium_max: Number(vapSigPrem) || 0,
        min_premium: Number(vapMinPrem) || 0,
        selection_time: vapSelTime,
        allow_both_sides: !!vapBothSides,
        require_arm_first: !!vapArmFirst,
        vwap_buffer_pct: Number(vapBuffer) || 0,
        sl_mode: vapSlMode,
        sl_pct: Number(vapSlPct) || 0,
        atr_period: Number(vapAtrPeriod) || 6,
        atr_mult: Number(vapAtrMult) || 0,
        max_sl_pct: Number(vapMaxSl) || 0,
        tp_mode: vapTpMode,
        rr: Number(vapRr) || 0,
        tp_pct: Number(vapTpPct) || 0,
        session_start: vapSessStart,
        session_end: vapSessEnd,
        exit_time: vapExitTime,
        wing_mode: vapWingMode,
        v1: {
          main: { premium_max: Number(vapMain.premium_max) || 0, lots: Number(vapMain.lots) || 0 },
          hedge: { premium_max: Number(vapHedge.premium_max) || 0, lots: Number(vapHedge.lots) || 0 },
          max_trades_per_day: Number(vapMaxDay) || 0,
        },
      };
    }
    if (sid === "TMA_V2") {''')

edit(BT, "Backtest.jsx buildConfig dependency array",
     '''tma2Main, tma2Hedge, tma2MaxDay, tma2WingMode, tma2SlUnit, tma2TpUnit]);   // ── TMA_V2 ──''',
     '''tma2Main, tma2Hedge, tma2MaxDay, tma2WingMode, tma2SlUnit, tma2TpUnit,   // ── TMA_V2 ──
      vapMode, vapSigPrem, vapMinPrem, vapSelTime, vapBothSides, vapArmFirst, vapBuffer, vapSlMode, vapSlPct, vapAtrPeriod, vapAtrMult, vapMaxSl, vapTpMode, vapRr, vapTpPct, vapSessStart, vapSessEnd, vapExitTime, vapMain, vapHedge, vapMaxDay, vapWingMode]);   // ── VAP_V1 ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit''')

edit(BT, "Backtest.jsx strategy chip",
     '''          { id: "TMA_V2", label: "TMA V2", sub: "4-EMA stack" },   // ── TMA_V2 ──''',
     '''          { id: "TMA_V2", label: "TMA V2", sub: "4-EMA stack" },   // ── TMA_V2 ──
          { id: "VAP_V1", label: "VAP V1", sub: "option VWAP" },   // ── VAP_V1 ──''')

edit(BT, "Backtest.jsx header summary line",
     '''            { isTMA2
            ? `TMA_V2 ''',
     '''            { isVAP
            ? `VAP_V1 · anchored VWAP on the OPTION premium (5m bars, 09:15 anchor) · CE/PE signal contracts <${vapSigPrem} fixed at ${vapSelTime} and held all day · ${vapMode === "SELL" ? `SELL the OPPOSITE side <${vapMain.premium_max} + deep-OTM hedge <${vapHedge.premium_max} (SL/TP on the SOLD leg)` : "BUY the signal contract itself"} · ${vapBothSides ? "CE and PE independently" : "one slot — a two-sided break takes neither"} · re-entry needs a close back below VWAP · SL ${vapSlMode === "ATR" ? `ATR${vapAtrPeriod}×${vapAtrMult}` : `${vapSlPct}%`} · TP ${vapTpMode === "RR" ? `RR ${vapRr}` : `${vapTpPct}%`} · entries ${vapSessStart}–${vapSessEnd} · EOD ${vapExitTime}`
            : isTMA2
            ? `TMA_V2 ''')

edit(BT, "Backtest.jsx VAP config panel",
     '''            /* ── TMA_V2 END ── */
          )}''',
     '''            /* ── TMA_V2 END ── */
          )}
          {isVAP && (
            /* ── VAP_V1 BEGIN ── anchored VWAP on the OPTION premium. The
               CE and PE contracts whose VWAP is watched are chosen ONCE
               per day and held: VWAP is anchored to 09:15 of a specific
               contract, so re-picking mid-day would restart the anchor and
               reset the arm/disarm machine against a series it never saw.
               BUY trades that same contract; SELL trades the OPPOSITE side
               (a different, separately-capped contract) plus a same-side
               deeper-OTM hedge. Intraday only. */
            <div style={{ gridColumn: "1 / -1", marginTop: 8 }}>

              <div style={tmaSecLabel}>Mode &amp; signal contracts</div>
              <div style={tmaSecRow}>
                <Field label="Execution mode">
                  <select style={{ ...inputStyle, width: 250 }} value={vapMode} onChange={(e) => setVapMode(e.target.value)}>
                    <option value="BUY">BUY the signal side (single leg)</option>
                    <option value="SELL">SELL the opposite side + hedge</option>
                  </select>
                </Field>
                <Field label="Signal premium &lt; (CE and PE)">
                  <input type="number" style={{ ...inputStyle, width: 90 }} value={vapSigPrem} onChange={(e) => setVapSigPrem(Number(e.target.value))}
                    title="Picks the CE and the PE whose VWAP is watched — the HIGHEST premium at or under this cap. In BUY mode this contract is also the one traded." />
                </Field>
                <Field label="Signal premium ≥ (floor)">
                  <input type="number" style={{ ...inputStyle, width: 90 }} value={vapMinPrem} onChange={(e) => setVapMinPrem(Number(e.target.value))}
                    title="Hard floor applied before the cap. Sub-₹60 weeklies have a near-random VWAP (tick granularity swamps the mean) and would dominate the signal count without carrying information." />
                </Field>
                <Field label="Selection time">
                  <input type="text" style={{ ...inputStyle, width: 84 }} value={vapSelTime} onChange={(e) => setVapSelTime(e.target.value)}
                    title="When the two signal contracts are chosen. Held for the rest of the day — VWAP anchors to 09:15 of a SPECIFIC contract." />
                </Field>
                <div style={{ alignSelf: "flex-end", fontSize: 11, color: colors.text.tertiary, paddingBottom: 8, maxWidth: 430, lineHeight: 1.45 }}>
                  Days with no strike in the band on a side are counted in DIAG (days_no_signal_ce / _pe), never silently dropped — a band outside Dhan&apos;s ATM±10 cap is a DATA limit, not a flat strategy.
                </div>
              </div>

              <div style={tmaSecLabel}>Entry rules</div>
              <div style={tmaSecRow}>
                <Field label="Both sides">
                  <select style={{ ...inputStyle, width: 230 }} value={vapBothSides ? "ON" : "OFF"} onChange={(e) => setVapBothSides(e.target.value === "ON")}>
                    <option value="ON">CE and PE independently</option>
                    <option value="OFF">One slot — conflict takes neither</option>
                  </select>
                </Field>
                <Field label="First entry needs arming">
                  <select style={{ ...inputStyle, width: 220 }} value={vapArmFirst ? "ON" : "OFF"} onChange={(e) => setVapArmFirst(e.target.value === "ON")}>
                    <option value="OFF">OFF — first break enters</option>
                    <option value="ON">ON — needs a below-close first</option>
                  </select>
                </Field>
                <Field label="VWAP buffer % (0=off)">
                  <input type="number" step="0.1" style={{ ...inputStyle, width: 90 }} value={vapBuffer} onChange={(e) => setVapBuffer(Number(e.target.value))}
                    title="Close must clear VWAP by this margin to count as a break. A DEVIATION buffer, not a theta model — it damps marginal breaks but does not neutralise the all-day decay drift." />
                </Field>
                <Field label="Max entries/day per leg (0=∞)">
                  <input type="number" style={{ ...inputStyle, width: 90 }} value={vapMaxDay} onChange={(e) => setVapMaxDay(Number(e.target.value))} />
                </Field>
                <div style={{ alignSelf: "flex-end", fontSize: 11, color: colors.text.tertiary, paddingBottom: 8, maxWidth: 430, lineHeight: 1.45 }}>
                  Entry = a completed 5m bar closing above the contract&apos;s own VWAP. After any exit the leg is DISARMED until a bar closes back below VWAP. With one slot, a bar where both legs would enter takes NEITHER and both stay armed.
                </div>
              </div>

              <div style={tmaSecLabel}>Stops &amp; targets</div>
              <div style={tmaSecRow}>
                <Field label="SL basis">
                  <select style={{ ...inputStyle, width: 200 }} value={vapSlMode} onChange={(e) => setVapSlMode(e.target.value)}>
                    <option value="PCT">% of entry premium</option>
                    <option value="ATR">ATR of the traded leg</option>
                  </select>
                </Field>
                {vapSlMode === "PCT" ? (
                  <Field label="SL % of premium">
                    <input type="number" style={{ ...inputStyle, width: 84 }} value={vapSlPct} onChange={(e) => setVapSlPct(Number(e.target.value))} />
                  </Field>
                ) : (
                  <>
                    <Field label="ATR period (5m bars)">
                      <input type="number" min="2" max="60" style={{ ...inputStyle, width: 84 }} value={vapAtrPeriod} onChange={(e) => setVapAtrPeriod(Number(e.target.value))}
                        title="Wilder ATR on the TRADED leg (the stop lives on the traded premium). Period 6 is warm on the 7th 5m bar — about 09:50. Entries before that are DIAG-counted as blocked_atr_warmup, never re-sized with a %-stop." />
                    </Field>
                    <Field label="ATR multiplier">
                      <input type="number" step="0.1" style={{ ...inputStyle, width: 84 }} value={vapAtrMult} onChange={(e) => setVapAtrMult(Number(e.target.value))} />
                    </Field>
                  </>
                )}
                <Field label="Max SL % (cap, 0=off)">
                  <input type="number" style={{ ...inputStyle, width: 90 }} value={vapMaxSl} onChange={(e) => setVapMaxSl(Number(e.target.value))}
                    title="Hard ceiling on the stop DISTANCE as a % of entry premium. An ATR spike on an illiquid strike can otherwise mint a stop wider than the premium itself." />
                </Field>
                <Field label="TP basis">
                  <select style={{ ...inputStyle, width: 200 }} value={vapTpMode} onChange={(e) => setVapTpMode(e.target.value)}>
                    <option value="RR">Risk multiple of the SL</option>
                    <option value="PCT">% of entry premium</option>
                  </select>
                </Field>
                {vapTpMode === "RR" ? (
                  <Field label="Reward : risk">
                    <input type="number" step="0.1" style={{ ...inputStyle, width: 84 }} value={vapRr} onChange={(e) => setVapRr(Number(e.target.value))} />
                  </Field>
                ) : (
                  <Field label="TP % of premium">
                    <input type="number" style={{ ...inputStyle, width: 84 }} value={vapTpPct} onChange={(e) => setVapTpPct(Number(e.target.value))} />
                  </Field>
                )}
              </div>

              <div style={tmaSecLabel}>Session</div>
              <div style={tmaSecRow}>
                <Field label="Entries open"><input type="text" style={{ ...inputStyle, width: 84 }} value={vapSessStart} onChange={(e) => setVapSessStart(e.target.value)} /></Field>
                <Field label="Entry cutoff (no new)"><input type="text" style={{ ...inputStyle, width: 84 }} value={vapSessEnd} onChange={(e) => setVapSessEnd(e.target.value)} /></Field>
                <Field label="EOD square-off"><input type="text" style={{ ...inputStyle, width: 84 }} value={vapExitTime} onChange={(e) => setVapExitTime(e.target.value)} /></Field>
                <div style={{ alignSelf: "flex-end", fontSize: 11, color: colors.text.tertiary, paddingBottom: 8, maxWidth: 420, lineHeight: 1.45 }}>
                  Intraday only — nothing carries overnight. Bars before the entry window still feed VWAP and still arm the state machine; they just cannot enter.
                </div>
              </div>

              {vapMode === "SELL" && (
                <>
                  <div style={tmaSecLabel}>Hedge sourcing</div>
                  <div style={tmaSecRow}>
                    <Field label="When no real strike ≤ cap">
                      <select style={{ ...inputStyle, width: 220 }} value={vapWingMode} onChange={(e) => setVapWingMode(e.target.value)}>
                        <option value="synthetic">Model it (SYN-, IV-anchored)</option>
                        <option value="real_fallback">Cheapest real (flagged)</option>
                        <option value="skip">Skip the signal</option>
                      </select>
                    </Field>
                    <div style={{ alignSelf: "flex-end", fontSize: 11, color: colors.text.tertiary, paddingBottom: 8, maxWidth: 460, lineHeight: 1.45 }}>
                      Same wing machinery as TMA/IC. Backtest only — live never models a hedge.
                    </div>
                  </div>
                </>
              )}

              <div style={tmaSecLabel}>Legs</div>
              <table style={{ borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr>{["Leg", "Premium <", "Lots"].map((h, i) => (
                    <th key={i} style={{ padding: "4px 8px", textAlign: "left", fontSize: 10, color: colors.text.muted, textTransform: "uppercase", letterSpacing: 0.4 }}>{h}</th>))}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td style={{ padding: "3px 8px", fontWeight: 700, color: vapMode === "SELL" ? colors.loss : colors.profit, whiteSpace: "nowrap" }}>{vapMode === "SELL" ? "SELL" : "BUY"} <span style={{ fontSize: 9, color: colors.text.muted, fontWeight: 400 }}>{vapMode === "SELL" ? "opposite side, monitored" : "the signal contract itself"}</span></td>
                    <td style={{ padding: "3px 8px" }}>
                      {vapMode === "SELL"
                        ? <input type="number" style={{ ...inputStyle, width: 76 }} value={vapMain.premium_max} onChange={(e) => setVapLeg("main", "premium_max", Number(e.target.value))} title="Cap for the SHORT leg — a different contract from the signal leg, so it has its own band." />
                        : <span style={{ color: colors.text.muted }} title="In BUY mode the signal contract IS the traded contract, so it is governed by the signal premium cap above. Two caps on one contract would be a footgun.">signal cap</span>}
                    </td>
                    <td style={{ padding: "3px 8px" }}><input type="number" style={{ ...inputStyle, width: 64 }} value={vapMain.lots} onChange={(e) => setVapLeg("main", "lots", Number(e.target.value))} /></td>
                  </tr>
                  {vapMode === "SELL" && (
                    <tr>
                      <td style={{ padding: "3px 8px", fontWeight: 700, color: colors.profit, whiteSpace: "nowrap" }}>BUY <span style={{ fontSize: 9, color: colors.text.muted, fontWeight: 400 }}>deep-OTM hedge, follows</span></td>
                      <td style={{ padding: "3px 8px" }}><input type="number" step="0.5" style={{ ...inputStyle, width: 76 }} value={vapHedge.premium_max} onChange={(e) => setVapLeg("hedge", "premium_max", Number(e.target.value))} title="e.g. 2-3 — the synthetic wing covers strikes the corpus lacks" /></td>
                      <td style={{ padding: "3px 8px" }}><input type="number" style={{ ...inputStyle, width: 64 }} value={vapHedge.lots} onChange={(e) => setVapLeg("hedge", "lots", Number(e.target.value))} /></td>
                    </tr>
                  )}
                </tbody>
              </table>

              <div style={{ marginTop: 10, paddingTop: 8, borderTop: `1px solid ${colors.border.dark}`, fontSize: 11, color: colors.text.tertiary, lineHeight: 1.55 }}>
                VWAP accumulates on 1m bars (typical price × volume) and is read at each completed 5m close. Zero-volume minutes contribute nothing; while cumulative volume is still zero the VWAP is UNDEFINED and no decision is taken — that shows up as blocked_warmup, not as a signal. In SELL mode the state machine watches the SIGNAL contract (CE) while SL/TP and the ATR that sizes them live on the TRADED premium (PE) — the asymmetry is deliberate. Expiry-day rows are bucketed separately in the run summary: option VWAP on expiry day is a different regime and blending it hides where the edge came from.
              </div>
            </div>
            /* ── VAP_V1 END ── */
          )}''')

# ══════════════════════════════════════════════════════════════════════
# 4. frontend/src/pages/backtest/BacktestQueue.jsx — paramLine copy
# ══════════════════════════════════════════════════════════════════════
BQ = "frontend/src/pages/backtest/BacktestQueue.jsx"
edit(BQ, "BacktestQueue paramLine VAP block",
     '''  // ── TMA_V2 ── (ema4 + s1 is unique to TMA_V2 configs)
  if (cfg.ema4 && cfg.s1) {''',
     '''  // ── VAP_V1 ── (vwap + v1 is unique to VAP_V1 configs)
  if (cfg.vwap && cfg.v1) {
    p.push(cfg.mode === "SELL" ? "SELL-opposite+hedge" : "BUY-signal");
    p.push(`VWAP<${cfg.signal_premium_max}${Number(cfg.min_premium) > 0 ? `≥${cfg.min_premium}` : ""}@${cfg.selection_time}`);
    if (Number(cfg.vwap_buffer_pct) > 0) p.push(`buf${cfg.vwap_buffer_pct}%`);
    p.push(cfg.allow_both_sides === false ? "1slot" : "CE+PE");
    if (cfg.require_arm_first) p.push("armFirst");
    p.push(`SL${cfg.sl_mode === "ATR" ? `ATR${cfg.atr_period}x${cfg.atr_mult}` : `${cfg.sl_pct}%`}`);
    if (Number(cfg.max_sl_pct) > 0) p.push(`cap${cfg.max_sl_pct}%`);
    p.push(`TP${cfg.tp_mode === "RR" ? `RR${cfg.rr}` : `${cfg.tp_pct}%`}`);
    { const mn = cfg.v1.main || {};
      p.push(cfg.mode === "SELL" ? `Sell<${mn.premium_max} ${mn.lots}L` : `Buy ${mn.lots}L`); }
    if (cfg.mode === "SELL") p.push(`Hedge<${(cfg.v1.hedge || {}).premium_max} ${(cfg.v1.hedge || {}).lots}L`);
    if (cfg.mode === "SELL" && cfg.wing_mode && cfg.wing_mode !== "synthetic") p.push(cfg.wing_mode === "skip" ? "WingSkip" : "WingRealFB");
    if (Number(cfg.v1.max_trades_per_day)) p.push(`cap${cfg.v1.max_trades_per_day}/leg`);
    if (cfg.session_start && cfg.session_end) p.push(`${cfg.session_start}-${cfg.session_end}`);
    if (cfg.exit_time) p.push(`EOD ${cfg.exit_time}`);
    return p.join(" · ");
  }
  // ── TMA_V2 ── (ema4 + s1 is unique to TMA_V2 configs)
  if (cfg.ema4 && cfg.s1) {''')

# ══════════════════════════════════════════════════════════════════════
# 5. frontend/src/pages/backtest/RunComparison.jsx — comparison columns
# ══════════════════════════════════════════════════════════════════════
RC = "frontend/src/pages/backtest/RunComparison.jsx"
edit(RC, "RunComparison VAP columns",
     '''  // ── TMA_V2 ── (ema4 + s1 is unique to TMA_V2 configs)''',
     '''  // ── VAP_V1 ── (vwap + v1 is unique to VAP_V1 configs)
  { key: "vap_mode", label: "VAP mode", get: (r) => (r.config?.vwap && r.config?.v1) ? (r.config.mode === "SELL" ? "SELL opposite + hedge" : "BUY signal side") : null },
  { key: "vap_signal", label: "VAP signal band", get: (r) => (r.config?.vwap && r.config?.v1) ? `<${r.config.signal_premium_max}${Number(r.config.min_premium) > 0 ? ` ≥${r.config.min_premium}` : ""} @${r.config.selection_time}` : null },
  { key: "vap_buffer", label: "VAP buffer", get: (r) => (r.config?.vwap && r.config?.v1 && Number(r.config.vwap_buffer_pct) > 0) ? `${r.config.vwap_buffer_pct}%` : null },
  { key: "vap_sides", label: "VAP sides", get: (r) => (r.config?.vwap && r.config?.v1) ? (r.config.allow_both_sides === false ? "One slot" : "CE+PE") : null },
  { key: "vap_arm", label: "VAP arm first", get: (r) => (r.config?.vwap && r.config?.v1 && r.config.require_arm_first) ? "ON" : null },
  { key: "vap_sl", label: "VAP SL", get: (r) => (r.config?.vwap && r.config?.v1) ? (r.config.sl_mode === "ATR" ? `ATR${r.config.atr_period}×${r.config.atr_mult}` : `${r.config.sl_pct}%`) : null },
  { key: "vap_slcap", label: "VAP SL cap", get: (r) => (r.config?.vwap && r.config?.v1 && Number(r.config.max_sl_pct) > 0) ? `${r.config.max_sl_pct}%` : null },
  { key: "vap_tp", label: "VAP TP", get: (r) => (r.config?.vwap && r.config?.v1) ? (r.config.tp_mode === "RR" ? `RR ${r.config.rr}` : `${r.config.tp_pct}%`) : null },
  { key: "vap_main", label: "VAP traded leg", get: (r) => { const c = (r.config?.vwap && r.config?.v1) ? r.config.v1.main : null; if (!c) return null; return r.config.mode === "SELL" ? `<${c.premium_max} ${c.lots}L` : `${c.lots}L (signal contract)`; } },
  { key: "vap_hedge", label: "VAP hedge", get: (r) => { const c = (r.config?.vwap && r.config?.v1 && r.config?.mode === "SELL") ? r.config.v1.hedge : null; return c ? `<${c.premium_max} ${c.lots}L${r.config.wing_mode && r.config.wing_mode !== "synthetic" ? ` (${r.config.wing_mode})` : ""}` : null; } },
  { key: "vap_cap", label: "VAP cap/leg", get: (r) => (r.config?.vwap && r.config?.v1 && Number(r.config.v1.max_trades_per_day)) ? r.config.v1.max_trades_per_day : null },
  { key: "vap_sess", label: "VAP entries", get: (r) => (r.config?.vwap && r.config?.v1 && r.config?.session_start) ? `${r.config.session_start}–${r.config.session_end}` : null },
  // ── TMA_V2 ── (ema4 + s1 is unique to TMA_V2 configs)''')

# ══════════════════════════════════════════════════════════════════════
# 6. frontend/src/pages/backtest/SweepBuilder.jsx — const + axes
# ══════════════════════════════════════════════════════════════════════
SB = "frontend/src/pages/backtest/SweepBuilder.jsx"
edit(SB, "SweepBuilder strategy const",
     '''TMA2 = "TMA_V2", TSG = "TSG_V1", GC = "GC_V1";''',
     '''TMA2 = "TMA_V2", TSG = "TSG_V1", GC = "GC_V1", VAP = "VAP_V1";''')

edit(SB, "SweepBuilder VAP axes",
     '''  // ── TMA_V2 ── nested s1.main/s1.hedge config; guards keep a sweep from
  // minting keys on a foreign config shape.''',
     '''  // ── VAP_V1 ── nested v1.main/v1.hedge config; guards keep a sweep from
  // minting keys on a foreign config shape. The signal band and the traded
  // band are SEPARATE axes on purpose — in SELL mode they select different
  // contracts, and sweeping them together would confound the two effects.
  { key: "vap_sig_prem", label: "Signal premium <", strategies: [VAP],
    hint: "150, 200, 250", parse: _num,
    apply: (c, v) => { if (c.vwap) c.signal_premium_max = v; }, fmt: (v) => `SIG<${v}` },
  { key: "vap_min_prem", label: "Signal premium ≥", strategies: [VAP],
    hint: "40, 60, 80", parse: _num,
    apply: (c, v) => { if (c.vwap) c.min_premium = v; }, fmt: (v) => `SIG≥${v}` },
  { key: "vap_main_prem", label: "Short leg premium <", strategies: [VAP],
    hint: "100, 150, 200", parse: _num,
    apply: (c, v) => { if (c.v1?.main) c.v1.main.premium_max = v; }, fmt: (v) => `M<${v}` },
  { key: "vap_hedge_prem", label: "Hedge premium <", strategies: [VAP],
    hint: "2, 3, 5", parse: _num,
    apply: (c, v) => { if (c.v1?.hedge) c.v1.hedge.premium_max = v; }, fmt: (v) => `H<${v}` },
  { key: "vap_sl_pct", label: "SL % (sl_mode=PCT)", strategies: [VAP],
    hint: "20, 25, 30, 40", parse: _num,
    apply: (c, v) => { if (c.vwap) { c.sl_mode = "PCT"; c.sl_pct = v; } }, fmt: (v) => `SL${v}%` },
  { key: "vap_atr_mult", label: "ATR multiplier (sl_mode=ATR)", strategies: [VAP],
    hint: "1.0, 1.5, 2.0", parse: _num,
    apply: (c, v) => { if (c.vwap) { c.sl_mode = "ATR"; c.atr_mult = v; } }, fmt: (v) => `ATRx${v}` },
  { key: "vap_atr_period", label: "ATR period (sl_mode=ATR)", strategies: [VAP],
    hint: "4, 6, 10, 14", parse: _num,
    apply: (c, v) => { if (c.vwap) { c.sl_mode = "ATR"; c.atr_period = v; } }, fmt: (v) => `ATR${v}` },
  { key: "vap_rr", label: "Reward:risk (tp_mode=RR)", strategies: [VAP],
    hint: "1.0, 1.5, 2.0, 3.0", parse: _num,
    apply: (c, v) => { if (c.vwap) { c.tp_mode = "RR"; c.rr = v; } }, fmt: (v) => `RR${v}` },
  { key: "vap_tp_pct", label: "TP % (tp_mode=PCT)", strategies: [VAP],
    hint: "30, 40, 60", parse: _num,
    apply: (c, v) => { if (c.vwap) { c.tp_mode = "PCT"; c.tp_pct = v; } }, fmt: (v) => `TP${v}%` },
  { key: "vap_buffer", label: "VWAP buffer %", strategies: [VAP],
    hint: "0, 0.5, 1, 2", parse: _num,
    apply: (c, v) => { if (c.vwap) c.vwap_buffer_pct = v; }, fmt: (v) => `buf${v}%` },
  { key: "vap_max_day", label: "Max entries/day per leg", strategies: [VAP],
    hint: "1, 2, 3, 0", parse: _num,
    apply: (c, v) => { if (c.v1) c.v1.max_trades_per_day = v; }, fmt: (v) => `cap${v}/leg` },
  { key: "vap_both", label: "Both sides", strategies: [VAP],
    hint: "ON, OFF", parse: (tok) => {
      const v = tok.trim().toUpperCase();
      return ["ON", "OFF"].includes(v) ? { v: v === "ON" } : { err: `"${tok}" must be ON or OFF` };
    },
    apply: (c, v) => { if (c.vwap) c.allow_both_sides = v; },
    fmt: (v) => (v ? "CE+PE" : "1slot") },
  { key: "vap_mode", label: "Execution mode", strategies: [VAP],
    hint: "BUY, SELL", parse: (tok) => {
      const v = tok.trim().toUpperCase();
      return ["BUY", "SELL"].includes(v) ? { v } : { err: `"${tok}" must be BUY or SELL` };
    },
    apply: (c, v) => { c.mode = v; },
    fmt: (v) => (v === "SELL" ? "SELL-opposite" : "BUY-signal") },
  // ── TMA_V2 ── nested s1.main/s1.hedge config; guards keep a sweep from
  // minting keys on a foreign config shape.''')


# ══════════════════════════════════════════════════════════════════════
# APPLY
# ══════════════════════════════════════════════════════════════════════
def main() -> int:
    planned: dict[Path, str] = {}
    failures: list[str] = []

    for rel, label, old, new in EDITS:
        if BACKEND_ONLY and not rel.startswith("backend/"):
            continue
        p = TREE / rel
        if not p.exists():
            failures.append(f"MISSING FILE {p} ({label})")
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
    print(f"\nApplied {len(EDITS if not BACKEND_ONLY else [e for e in EDITS if e[0].startswith('backend/')])} "
          f"edit(s) across {len(planned)} file(s).")
    if not BACKEND_ONLY:
        print("REMINDER: dual-tree — now run\n"
              "  python3 apply_vap_v1_20260820.py --tree desktop/src-tauri\n"
              "and copy backend/app/backtest/vap/ into "
              "desktop/src-tauri/backend/app/backtest/vap/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
