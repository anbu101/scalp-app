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

import React, { useEffect, useState, useCallback, useRef } from "react";

const STRAT_LABEL = { SCALP_V1: "V1", SCALP_V3: "V3", SCALP_V4: "V4", SCALP_V5: "V5" };
const STATUS_STYLE = (c, st) => ({
  pending:   { bg: c.bg.tertiary, fg: c.text.muted },
  running:   { bg: c.primaryBg,   fg: c.primary },
  done:      { bg: c.successBg,   fg: c.profit },
  error:     { bg: c.lossBg,      fg: c.loss },
  cancelled: { bg: c.warningBg,   fg: c.warning },
}[st] || { bg: c.bg.tertiary, fg: c.text.muted });

function paramLine(cfg) {
  if (!cfg) return "—";
  const p = [];
  if (cfg.option_premium) p.push(`prem ${cfg.option_premium.min}-${cfg.option_premium.max}`);
  if (cfg.sl_points) p.push(`SL ${cfg.sl_points}`);
  if (cfg.tp_points) p.push(`TP ${cfg.tp_points}`);
  if (cfg.risk_reward_ratio != null) p.push(`RR ${cfg.risk_reward_ratio}`);
  if (cfg.min_sl_points) p.push(`minSL ${cfg.min_sl_points}`);
  if (cfg.max_sl_points) p.push(`maxSL ${cfg.max_sl_points}`);
  if (cfg.hedge_sl_points) p.push(`hSL ${cfg.hedge_sl_points}`);
  if (cfg.trade_side_mode && cfg.trade_side_mode !== "BOTH") p.push(cfg.trade_side_mode);
  if (cfg.max_loss) p.push(`ML ${cfg.max_loss}`);
  if (cfg.max_profit) p.push(`MP ${cfg.max_profit}`);
  if (cfg.session?.primary) p.push(`${cfg.session.primary.start}-${cfg.session.primary.end}`);
  if (cfg.quantity?.lots != null) p.push(`${cfg.quantity.lots}L`);
  return p.join(" · ");
}

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

  const cancelJob = useCallback(async (jobId) => {
    try { await apiCall(`/api/backtest/queue/${jobId}`, { method: "DELETE" }); await refresh(); }
    catch (e) { setErr(String(e.message || e)); }
  }, [apiCall, refresh]);

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
          Tip: in the <b>Run</b> tab, set up a combination, come here and “Add current params”, then change the Run params and add again — repeat to stage several, then Start.
        </div>
      </Card>

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
                return (
                  <tr key={j.job_id} style={{ background: isRunning ? c.primaryBg : i % 2 ? c.bg.secondary : c.bg.primary,
                    borderTop: `1px solid ${c.border.dark}` }}>
                    <td style={{ padding: "8px 10px", ...typography.mono, color: c.text.tertiary }}>{j.position}</td>
                    <td style={{ padding: "8px 10px", fontWeight: 700 }}>{STRAT_LABEL[j.strategy_id] || j.strategy_id}</td>
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
                      {j.status === "pending" && (
                        <button onClick={() => cancelJob(j.job_id)} title="Remove from queue"
                          style={{ border: "none", background: "transparent", cursor: "pointer", color: c.loss, fontSize: 13 }}>✕</button>
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