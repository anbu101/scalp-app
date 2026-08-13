// frontend/src/pages/backtest/BacktestQueue.jsx
//
// Scheduled backtest queue — stage several parameter combinations, run them
// one-by-one automatically, watch live status. Cancel the whole queue or a
// single job. Finished jobs persist as normal runs → they appear in Compare
// Runs (pick / compare / export CSV there). No auto-download.
//
// Self-contained: takes design primitives + apiCall from the host page, and a
// `buildConfig(strategyId)` callback that returns the SAME config_override the
// manual Run uses for the currently-entered params (so "Add current params to
// queue" stages exactly what Run would have executed).
//
// QUEUE_PF_BADGE: jobs staged by the Portfolio tab carry label "PF:<name> ·
// <strategy>". Those rows get a colored group badge (same name → same color)
// so the legs of one portfolio are scannable even when other jobs are
// interleaved between them. Plain individual jobs stay unbadged.
//
// QUEUE_REORDER: pending rows get ⤒ ▲ ▼ controls (edge-disabled among the
// PENDING set) backed by POST /api/backtest/queue/{job_id}/move. Running and
// finished rows never move.

import React, { useEffect, useState, useCallback, useRef } from "react";
import SweepBuilder from "./SweepBuilder";   // ── SWEEP_BUILDER ──
import { fmtIcSl } from "./paramFormat";   // ── IC_IV_SL ──

// ── WICK_PST_V1_REMOVAL ── WICK_V1 and PST_V1 are RETIRED (not launchable,
// no runner) but their labels stay: finished jobs from before the removal are
// still in the queue table and this map is display-only.
const STRAT_LABEL = { SCALP_V1: "V1", SCALP_V3: "V3", SCALP_V5: "V5", HA_V1: "HA", HA_SELL: "HAS", WICK_V1: "WICK", IC_V1: "IC", IC_V2: "IC2", PST_V1: "PST", PST_SELL: "PSTS", PST_HEDGE: "PSTH", TMA_V1: "TMA", TSG_V1: "TSG" };
const STATUS_STYLE = (c, st) => ({
  pending:   { bg: c.bg.tertiary, fg: c.text.muted },
  running:   { bg: c.primaryBg,   fg: c.primary },
  done:      { bg: c.successBg,   fg: c.profit },
  error:     { bg: c.lossBg,      fg: c.loss },
  cancelled: { bg: c.warningBg,   fg: c.warning },
}[st] || { bg: c.bg.tertiary, fg: c.text.muted });

// ── QUEUE_GROUP_BADGE BEGIN ── (supersedes QUEUE_PF_BADGE) group detection
// from the job label. Conventions: "PF:<name> · <strategy>" (Portfolio tab)
// and "SWEEP:<name> · <varied values>" (Sweep builder). The badge color is a
// stable hash of kind+name, so every leg of one group shares a color and
// different groups differ (palette of 8; collisions are cosmetic only).
const PF_PALETTE = ["#ec4899", "#06b6d4", "#a855f7", "#f59e0b", "#14b8a6", "#3b82f6", "#f97316", "#a3e635", "#6366f1", "#f43f5e"];
const GROUP_PREFIXES = [["PF:", "PF"], ["SWEEP:", "SW"]];
function groupInfo(label) {
  if (!label) return null;
  for (const [prefix, kind] of GROUP_PREFIXES) {
    if (!label.startsWith(prefix)) continue;
    const name = label.slice(prefix.length).split("·")[0].trim() || kind.toLowerCase();
    const key = kind + name;
    let h = 0;
    for (let i = 0; i < key.length; i++) h = ((h * 31) + key.charCodeAt(i)) >>> 0;
    return { kind, name, color: PF_PALETTE[h % PF_PALETTE.length] };
  }
  return null;
}
// ── QUEUE_GROUP_BADGE END ──

// ── PARAMS_FULL BEGIN ── full-union parameter formatter, matching the Compare
// Runs list (RunComparison.jsx `paramSummary`) so a job's staged params are
// shown COMPLETELY here — entry conditions, per-side cap, fixed target, TP
// hold, etc — not the old V1/V5-only subset. Retired-strategy keys (WICK's
// timeframe/wick/dual-side) are left in the union so archived jobs still read.
// Kept as a LOCAL function (this component is self-contained by contract), but
// deliberately the same output. Only SET params render (0/empty = disabled =
// hidden), so each strategy shows exactly its own knobs.
function _fmtConds(arr) {
  return Array.isArray(arr) && arr.length
    ? arr.map((x) => String(x).replace("COND", "C")).join("+")
    : null;
}

function paramLine(cfg) {
  if (!cfg) return "—";
  const p = [];
  // ── TMA_V1 ── (ema + c1/c2 is unique to TMA configs)
  if (cfg.ema && cfg.c1) {
    if (cfg.trade_mode === "POSITIONAL") p.push("Positional");   // ── POSITIONAL ──
    if (cfg.cut_neg_mtm_eod) p.push("CutLosers@EOD");   // ── NEG_MTM_EOD_CUT ──
    if (cfg.c1.sell) {   // ── SPREAD_V2 ──
      { const s_ = cfg.c1.sell, lg = s_.sl_tp_unit === "PTS" ? "p" : "%";   // ── SLTP_UNITS ──
        const f = (v, x) => { const m = !x ? lg : x === "PTS" ? "p" : x === "ABS" ? "@" : "%"; return m === "@" ? `@${v}` : `${v}${m}`; };
        p.push(`Sell<${s_.premium_max} ${s_.lots}L SL${f(s_.sl_pct, s_.sl_unit)} TP${f(s_.tp_pct, s_.tp_unit)}`); }
      p.push(`Hedge<${(cfg.c1.buy || {}).premium_max} ${(cfg.c1.buy || {}).lots}L`);
      if (cfg.wing_mode && cfg.wing_mode !== "synthetic") p.push(cfg.wing_mode === "skip" ? "WingSkip" : "WingRealFB");
    } else if (cfg.c2) {
      [["C1", cfg.c1], ["C2", cfg.c2]].forEach(([id, c]) => {
        if (c && Number(c.lots) > 0) p.push(`${id}<${c.premium_max} ${c.lots}L`);
      });
    }
    if (cfg.session_start && cfg.session_end) p.push(`${cfg.session_start}-${cfg.session_end}`);
    if (cfg.exit_time) p.push(`EOD ${cfg.exit_time}`);
    return p.join(" · ");
  }
  // ── IC_V1 / IC_V2 ── explicit per-leg condor configs (action + opt_type
  // per leg is unique to IC). Without this branch an IC job's Params column
  // was effectively blank, which made a staged sweep unreadable.
  if (Array.isArray(cfg.legs) && cfg.legs.some((l) => l.action && l.opt_type)) {
    if (cfg.entry_time) p.push(`entry ${cfg.entry_time}`);
    // ── IC_V2 ── carry + adjustment tokens (absent on V1 configs)
    if (cfg.exit_mode === "NEXT_OPEN") {
      p.push(`hold→${cfg.next_open_time || "09:16"}`);
      p.push(`expEOD ${cfg.expiry_exit_time || "15:28"}`);
      if (cfg.adjust_on_sl) {
        const a = cfg.adjust || {};
        const one = (x) => x && (x.enabled !== false) && Number(x.lots) > 0
          ? `<${x.premium_max} ${x.lots}L SL${x.sl_val}${x.sl_mode === "pts" ? "p" : "%"}` : null;
        const s1 = one(a.L1), s2 = one(a.L2);
        p.push(`ADJ+${cfg.adjust_delay_s ?? 60}s ${[s1 && `CE ${s1}`, s2 && `PE ${s2}`].filter(Boolean).join(" / ") || "off"}`);
        if (cfg.adjust_only) p.push("ADJ-ONLY");   // ── ADJ_ONLY ──
      }
    } else if (cfg.exit_time) {
      p.push(`EOD ${cfg.exit_time}`);
    }
    // ── TSG_V1 ── combined-MTM target (unique to TSG configs)
    if (Number(cfg.mtm_target) > 0) p.push(`MTM≥₹${cfg.mtm_target}`);
    if (Number(cfg.mtm_sl) > 0) p.push(`MTMSL₹${cfg.mtm_sl}`);   // ── TSG_MTM_SL ──
    if (cfg.iv_keep_hedge) p.push("IV12KEEP");   // ── TSG_IV12 ──
    if (Number(cfg.min_entry_iv) > 0) p.push(`IVFLOOR${cfg.min_entry_iv}`);   // ── TSG_IV13 ──
    if (Number(cfg.mtm_trail_arm) > 0 && Number(cfg.mtm_trail_giveback) > 0) p.push(`TRAIL${cfg.mtm_trail_arm}/${cfg.mtm_trail_giveback}`);   // ── TSG_TRAIL ──
    if (Number(cfg.iv_sl_delta_pts) > 0) p.push(`IVSL+${cfg.iv_sl_delta_pts}pts`);   // ── TSG_IV_SL_DELTA ──
    else if (Number(cfg.iv_sl_pct) > 0) p.push(`IVSL${cfg.iv_sl_pct}%`);   // ── TSG_IV_SL ──
    cfg.legs.filter((l) => Number(l.lots) > 0).forEach((l) => {
      // ── IC_IV_SL ── shared formatter: this string IS the queued job's
      // label, and a sweep mixing premium and vol stops would otherwise
      // enqueue rows that are impossible to tell apart in the queue table.
      p.push(`${l.id}:${l.action === "SELL" ? "S" : "B"}${l.opt_type}<${l.premium_max}${l.sl_val ? ` ${fmtIcSl(l.sl_val, l.sl_mode)}` : ""}${l.mtc_other_on_sl ? "·MTC" : ""} ${l.lots}L`);
    });
    if (cfg.wing_mode && cfg.wing_mode !== "real_fallback") {
      p.push(cfg.wing_mode === "skip" ? "WingSkip" : `WingSYN×${cfg.skew_mult ?? 1}`);
    }
    return p.join(" · ");
  }
  if (cfg.option_premium) p.push(`prem ${cfg.option_premium.min}-${cfg.option_premium.max}`);
  // WICK_V1 (retired) — kept for archived jobs
  if (cfg.timeframe_minutes) p.push(`tf ${cfg.timeframe_minutes}`);
  if (cfg.top_wick_min) p.push(`wick≥ ${cfg.top_wick_min}`);
  if (cfg.dual_side_mode) p.push("1CE+1PE");
  // V1 / hedge / HA
  if (cfg.risk_reward_ratio != null) p.push(`RR ${cfg.risk_reward_ratio}`);
  if (cfg.min_sl_points) p.push(`minSL ${cfg.min_sl_points}`);
  if (cfg.max_sl_points) p.push(`maxSL ${cfg.max_sl_points}`);
  if (cfg.risk_max_sl_points) p.push(`rMaxSL ${cfg.risk_max_sl_points}`);
  if (cfg.hedge_sl_points) p.push(`hSL ${cfg.hedge_sl_points}`);
  // V5 (and retired WICK) absolute points
  if (cfg.sl_points) p.push(`SL ${cfg.sl_points}`);
  if (cfg.tp_points) p.push(`TP ${cfg.tp_points}`);
  // HA-specific
  if (cfg.target_override?.enabled) p.push(`tgt ${cfg.target_override.points}`);
  { const cc = _fmtConds(cfg.entry_conditions); if (cc) p.push(cc); }
  // ── HA_COND1_RETRACE ── job label token — a sweep over frac/ttl would
  // otherwise enqueue rows impossible to tell apart in the queue table.
  if (cfg.cond1_retrace?.enabled) p.push(`c1rt ${cfg.cond1_retrace.frac ?? 0.5}/${cfg.cond1_retrace.ttl_bars ?? 5}b`);
  if (cfg.cond1_flip_side) p.push("c1flip");   // ── HA_COND1_FLIP ──
  if (cfg.max_trades_per_side) p.push(`cap ${cfg.max_trades_per_side}`);
  if (cfg.tp_hold_extra_candles) p.push(`hold ${cfg.tp_hold_extra_candles}`);
  // shared risk / session / size
  if (cfg.trade_side_mode && cfg.trade_side_mode !== "BOTH") p.push(cfg.trade_side_mode);
  if (cfg.max_loss) p.push(`ML ${cfg.max_loss}`);
  if (cfg.max_profit) p.push(`MP ${cfg.max_profit}`);
  // ── V3_RISK_LIMITS ──
  if (cfg.daily_max_loss) p.push(`dML ${cfg.daily_max_loss}`);
  if (cfg.daily_max_profit) p.push(`dMP ${cfg.daily_max_profit}`);
  if (cfg.monthly_max_loss) p.push(`mML ${cfg.monthly_max_loss}`);
  if (cfg.monthly_max_profit) p.push(`mMP ${cfg.monthly_max_profit}`);
  // ── V3_TRADE_COUNT_LIMITS ──
  if (cfg.max_trades_per_day) p.push(`capD ${cfg.max_trades_per_day}`);
  if (cfg.max_trades_per_side_per_day) p.push(`capS ${cfg.max_trades_per_side_per_day}`);
  if (cfg.session?.primary) p.push(`${cfg.session.primary.start}-${cfg.session.primary.end}`);
  if (cfg.quantity?.lots != null) p.push(`${cfg.quantity.lots}L`);
  return p.join(" · ");
}
// ── PARAMS_FULL END ──

export default function BacktestQueue({
  colors, spacing, typography, Card,
  apiCall,
  strategyId, dateFrom, dateTo,   // current form values (for "add current")
  buildConfig,                    // () => config_override for the current params
  onOpenRun,                      // (run_id) => jump to results view
}) {
  const c = colors;
  const [status, setStatus] = useState(null);   // {active, current_job_id, progress, cancelling, jobs:[]}
  const [err, setErr] = useState(null);
  const poll = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const st = await apiCall("/api/backtest/queue/status");
      setStatus(st);
      setErr(null);
    } catch (e) {
      setErr(String(e.message || e));
    }
  }, [apiCall]);

  useEffect(() => {
    refresh();
    poll.current = setInterval(refresh, 1500);
    return () => clearInterval(poll.current);
  }, [refresh]);

  const jobs = status?.jobs || [];
  const active = !!status?.active;
  const pending = jobs.filter((j) => j.status === "pending");
  const finished = jobs.filter((j) => ["done", "error", "cancelled"].includes(j.status));
  const requeueable = jobs.filter((j) => ["error", "cancelled"].includes(j.status));   // ── QUEUE_REQUEUE ──
  // ── QUEUE_REORDER ── the pending order as the WORKER will consume it (the
  // status endpoint returns jobs position-sorted); drives edge-disabling.
  const pendingIds = pending.map((j) => j.job_id);

  const addCurrent = useCallback(async () => {
    setErr(null);
    if (!dateFrom || !dateTo) { setErr("Set a date range in Run first."); return; }
    try {
      const config_override = buildConfig(strategyId);
      await apiCall("/api/backtest/queue/enqueue", {
        method: "POST",
        body: JSON.stringify({
          strategy_id: strategyId, underlying: "NIFTY",
          date_from: dateFrom, date_to: dateTo,
          config_override,
          label: `${STRAT_LABEL[strategyId] || strategyId} ${paramLine(config_override)}`,
        }),
      });
      await refresh();
    } catch (e) { setErr(String(e.message || e)); }
  }, [apiCall, strategyId, dateFrom, dateTo, buildConfig, refresh]);

  const startQueue = useCallback(async () => {
    setErr(null);
    try { await apiCall("/api/backtest/queue/start", { method: "POST" }); await refresh(); }
    catch (e) { setErr(String(e.message || e)); }
  }, [apiCall, refresh]);

  const cancelQueue = useCallback(async () => {
    // No window.confirm — Tauri's webview can silently block it. Cancel directly.
    try { await apiCall("/api/backtest/queue/cancel", { method: "POST" }); await refresh(); }
    catch (e) { setErr(String(e.message || e)); }
  }, [apiCall, refresh]);

  const cancelCurrent = useCallback(async () => {
    try { await apiCall("/api/backtest/queue/cancel-current", { method: "POST" }); await refresh(); }
    catch (e) { setErr(String(e.message || e)); }
  }, [apiCall, refresh]);

  // ── QUEUE_ROW_DELETE ── one status-aware endpoint: pending → cancelled
  // (tombstone stays visible), done/error/cancelled → row deleted (the saved
  // RUN is untouched — it stays in Compare Runs / Portfolio).
  const removeJob = useCallback(async (jobId) => {
    try { await apiCall(`/api/backtest/queue/${jobId}`, { method: "DELETE" }); await refresh(); }
    catch (e) { setErr(String(e.message || e)); }
  }, [apiCall, refresh]);

  // ── QUEUE_REQUEUE BEGIN ── restart cancelled/errored jobs as pending
  // (config is kept on the row; the job re-enters at the END of the queue —
  // reorder with ▲▼ before Start if needed). Does NOT auto-start the queue.
  const requeueJob = useCallback(async (jobId) => {
    try { await apiCall(`/api/backtest/queue/${jobId}/requeue`, { method: "POST" }); await refresh(); }
    catch (e) { setErr(String(e.message || e)); }
  }, [apiCall, refresh]);
  const requeueCancelled = useCallback(async () => {
    try { await apiCall("/api/backtest/queue/requeue-cancelled", { method: "POST" }); await refresh(); }
    catch (e) { setErr(String(e.message || e)); }
  }, [apiCall, refresh]);
  // ── QUEUE_REQUEUE END ──

  // ── QUEUE_REORDER BEGIN ── move a pending job: "up" | "down" | "top".
  // The backend permutes positions among pending rows only; refresh re-reads
  // the authoritative order (no optimistic reorder — the server is the truth).
  const moveJob = useCallback(async (jobId, direction) => {
    try {
      await apiCall(`/api/backtest/queue/${jobId}/move`, {
        method: "POST",
        body: JSON.stringify({ direction }),
      });
      await refresh();
    } catch (e) { setErr(String(e.message || e)); }
  }, [apiCall, refresh]);
  // ── QUEUE_REORDER END ──

  const clearFinished = useCallback(async () => {
    try { await apiCall("/api/backtest/queue/clear", { method: "POST" }); await refresh(); }
    catch (e) { setErr(String(e.message || e)); }
  }, [apiCall, refresh]);

  const smallBtn = (variant) => ({
    padding: "8px 16px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 13, fontWeight: 600,
    background: variant === "primary" ? c.primary : variant === "danger" ? c.loss : c.bg.tertiary,
    color: variant === "primary" || variant === "danger" ? "#fff" : c.text.primary,
  });

  const prog = status?.progress;
  const progLabel = prog && prog.total_days
    ? `day ${prog.day}/${prog.total_days}${prog.date ? ` · ${prog.date}` : ""}`
    : active ? "running…" : "";

  return (
    <div>
      {/* ── Controls ── */}
      <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.lg }}>
        <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap" }}>
          <div>
            <div style={{ ...typography.label, color: c.text.muted, marginBottom: 4 }}>Scheduled queue</div>
            <div style={{ fontSize: 13, color: c.text.secondary }}>
              Stage parameter combinations and run them one-by-one. Each finished run shows up in <b>Compare Runs</b>.
            </div>
          </div>
          <div style={{ marginLeft: "auto", display: "flex", gap: spacing.sm, flexWrap: "wrap" }}>
            <button style={smallBtn("default")} onClick={addCurrent}>+ Add current params</button>
            {!active ? (
              <button style={smallBtn("primary")} disabled={!pending.length} onClick={startQueue}>
                ▶ Start queue ({pending.length})
              </button>
            ) : (
              <button style={smallBtn("danger")} onClick={cancelQueue}>■ Cancel queue</button>
            )}
            {finished.length > 0 && (
              <button style={smallBtn("default")} onClick={clearFinished}>Clear finished</button>
            )}
            {/* ── QUEUE_REQUEUE ── bulk restore after an accidental cancel */}
            {requeueable.length > 0 && (
              <button style={smallBtn("default")} onClick={requeueCancelled}
                title="Flip all cancelled/errored jobs back to pending (their params are kept); then press Start queue">
                ↻ Requeue cancelled ({requeueable.length})</button>
            )}
          </div>
        </div>
        {active && (
          <div style={{ marginTop: spacing.md, fontSize: 12, color: c.primary, fontWeight: 600 }}>
            ● Queue running — {progLabel}
            {status?.cancelling && <span style={{ color: c.warning, marginLeft: 8 }}>(cancelling…)</span>}
          </div>
        )}
        {err && <div style={{ marginTop: spacing.md, fontSize: 12, color: c.loss }}>{err}</div>}
        <div style={{ marginTop: spacing.sm, fontSize: 11, color: c.text.tertiary }}>
          Tip: in the <b>Run</b> tab, set up a combination, come here and “Add current params”, then change the Run params and add again — repeat to stage several, then Start. Rows staged from the <b>Portfolio</b> tab share a colored PF badge. Use ⤒ ▲ ▼ to reorder pending jobs.
        </div>
      </Card>

      {/* ── SWEEP_BUILDER ── staged parameter sweeps (OAT / focused grid) */}
      <SweepBuilder
        colors={colors} spacing={spacing} typography={typography} Card={Card}
        apiCall={apiCall}
        strategyId={strategyId} dateFrom={dateFrom} dateTo={dateTo}
        buildConfig={buildConfig}
        queueActive={active}
        onStaged={refresh}
      />

      {/* ── Job list ── */}
      {jobs.length === 0 ? (
        <Card elevated style={{ padding: "40px 0", textAlign: "center", color: c.text.muted, fontSize: 13 }}>
          Queue is empty. Stage a combination with “Add current params”.
        </Card>
      ) : (
        <Card style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}>
            <thead style={{ background: c.bg.tertiary }}>
              <tr>
                {["#", "Strategy", "Period", "Params", "Status", "Result", ""].map((h, i) => (
                  <th key={i} style={{ padding: "9px 10px", textAlign: i >= 5 ? "right" : "left",
                    ...typography.label, color: c.text.muted, borderBottom: `2px solid ${c.border.light}`, whiteSpace: "nowrap" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {jobs.map((j, i) => {
                const ss = STATUS_STYLE(c, j.status);
                const isRunning = j.status === "running";
                // ── QUEUE_GROUP_BADGE ── colored badge for PF / SWEEP groups
                const grp = groupInfo(j.label);
                // ── QUEUE_REORDER ── position of this job within the PENDING
                // set (-1 for non-pending rows → no reorder controls)
                const pi = j.status === "pending" ? pendingIds.indexOf(j.job_id) : -1;
                const atTop = pi <= 0;
                const atBottom = pi === pendingIds.length - 1;
                const mvBtn = (disabled, title) => ({
                  border: "none", background: "transparent",
                  cursor: disabled ? "default" : "pointer",
                  color: disabled ? c.text.muted : c.text.secondary,
                  opacity: disabled ? 0.3 : 1,
                  fontSize: 13, fontWeight: 700, padding: "0 4px",
                });
                return (
                  <tr key={j.job_id} style={{ background: isRunning ? c.primaryBg : i % 2 ? c.bg.secondary : c.bg.primary,
                    borderTop: `1px solid ${c.border.dark}` }}>
                    <td style={{ padding: "8px 10px", ...typography.mono, color: c.text.tertiary }}>{j.position}</td>
                    <td style={{ padding: "8px 10px", fontWeight: 700, whiteSpace: "nowrap" }}>
                      {STRAT_LABEL[j.strategy_id] || j.strategy_id}
                      {grp && (
                        <span title={grp.kind === "PF"
                            ? `Portfolio "${grp.name}" — compose its finished runs in the Portfolio tab`
                            : `Sweep "${grp.name}" — analyse its finished runs in Compare Runs`}
                          style={{ marginLeft: 8, padding: "1px 8px", borderRadius: 4, fontSize: 10, fontWeight: 800,
                            background: `${grp.color}22`, border: `1px solid ${grp.color}55`, color: grp.color,
                            whiteSpace: "nowrap", verticalAlign: "middle" }}>
                          {grp.kind} · {grp.name}
                        </span>
                      )}
                    </td>
                    <td style={{ padding: "8px 10px", ...typography.mono, fontSize: 11, color: c.text.tertiary, whiteSpace: "nowrap" }}>{j.date_from} → {j.date_to}</td>
                    <td style={{ padding: "8px 10px", fontSize: 11, color: c.text.secondary }}>{paramLine(j.config)}</td>
                    <td style={{ padding: "8px 10px" }}>
                      <span style={{ padding: "2px 8px", borderRadius: 4, fontSize: 11, fontWeight: 700, background: ss.bg, color: ss.fg }}>
                        {j.status}{isRunning && prog && prog.total_days ? ` ${prog.day}/${prog.total_days}` : ""}
                      </span>
                      {j.status === "error" && j.error_text && (
                        <div style={{ fontSize: 10, color: c.loss, marginTop: 2, maxWidth: 240 }}>{j.error_text}</div>
                      )}
                    </td>
                    <td style={{ padding: "8px 10px", textAlign: "right" }}>
                      {j.run_id && j.status === "done" ? (
                        <button onClick={() => onOpenRun?.(j.run_id)}
                          style={{ border: "none", background: "transparent", cursor: "pointer", color: c.primary, fontSize: 12, fontWeight: 600 }}>
                          Open
                        </button>
                      ) : <span style={{ color: c.text.muted, fontSize: 11 }}>—</span>}
                    </td>
                    <td style={{ padding: "8px 10px", textAlign: "right", whiteSpace: "nowrap" }}>
                      {/* ── QUEUE_REORDER BEGIN ── ⤒ ▲ ▼ on pending rows, edge-disabled */}
                      {j.status === "pending" && (
                        <>
                          <button disabled={atTop} style={mvBtn(atTop)} title="Move to top (runs next)"
                            onClick={() => moveJob(j.job_id, "top")}>⤒</button>
                          <button disabled={atTop} style={mvBtn(atTop)} title="Move up"
                            onClick={() => moveJob(j.job_id, "up")}>▲</button>
                          <button disabled={atBottom} style={mvBtn(atBottom)} title="Move down"
                            onClick={() => moveJob(j.job_id, "down")}>▼</button>
                        </>
                      )}
                      {/* ── QUEUE_REORDER END ── */}
                      {j.status === "pending" && (
                        <button onClick={() => removeJob(j.job_id)} title="Cancel staged job (leaves a cancelled row)"
                          style={{ border: "none", background: "transparent", cursor: "pointer", color: c.loss, fontSize: 13, marginLeft: 6 }}>✕</button>
                      )}
                      {/* ── QUEUE_REQUEUE ── per-row restart on cancelled/errored rows */}
                      {["error", "cancelled"].includes(j.status) && (
                        <button onClick={() => requeueJob(j.job_id)}
                          title="Requeue this job as pending (params kept; joins the end of the queue)"
                          style={{ border: "none", background: "transparent", cursor: "pointer", color: c.accent, fontSize: 13, marginLeft: 6 }}>↻</button>
                      )}
                      {/* ── QUEUE_ROW_DELETE ── per-row delete on finished rows */}
                      {["done", "error", "cancelled"].includes(j.status) && (
                        <button onClick={() => removeJob(j.job_id)}
                          title="Delete this row from the queue — the saved backtest run is NOT deleted"
                          style={{ border: "none", background: "transparent", cursor: "pointer", color: c.text.muted, fontSize: 13, marginLeft: 6 }}>🗑</button>
                      )}
                      {isRunning && (
                        <button onClick={cancelCurrent} title="Cancel this run"
                          style={{ border: "none", background: "transparent", cursor: "pointer", color: c.warning, fontSize: 12, fontWeight: 600 }}>Stop</button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}