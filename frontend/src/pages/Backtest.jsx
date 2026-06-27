// frontend/src/pages/Backtest.jsx
//
// SCALP V1 (short) / V3 / V4 (hedge) backtest UI.
//
// STATE PERSISTENCE (fixes "everything resets on tab change"):
//   The backend is the source of truth. On mount we REHYDRATE:
//     - run/status     → if a job is running, resume polling; else load last result
//     - backfill/status→ if backfilling, resume polling
//     - runs?limit=1   → load the most recent run's results into the table
//   Form parameters persist to localStorage (real app, not a sandboxed artifact),
//   so inputs survive navigation. The running job keeps going server-side
//   regardless of this component's lifecycle.
//
// SECURITY: backend routes are admin-gated; keep OFF the public Funnel until
// the API auth audit is done.

import React, { useEffect, useState, useCallback, useRef } from "react";
import { getApiBase } from "../api/base";
import { colors, spacing, typography, pnlStyle } from "../tokens";

const LS_KEY = "scalp_backtest_params_v1";

function loadParams() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}
function saveParams(p) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(p)); } catch { /* ignore */ }
}

function Card({ children, style, elevated }) {
  return (
    <div style={{
      background: elevated ? colors.bg.tertiary : colors.bg.secondary,
      border: `1px solid ${colors.border.light}`,
      borderRadius: 8,
      boxShadow: elevated ? "0 4px 6px -1px rgba(0,0,0,0.3)" : "0 1px 3px rgba(0,0,0,0.2)",
      ...style,
    }}>{children}</div>
  );
}

function Field({ label, children }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ ...typography.label, color: colors.text.muted, fontSize: 11 }}>{label}</span>
      {children}
    </label>
  );
}

const inputStyle = {
  padding: "7px 10px", borderRadius: 6,
  border: `1px solid ${colors.border.light}`,
  background: colors.bg.secondary, color: colors.text.primary,
  fontSize: 13, outline: "none", fontFamily: "'Inter', sans-serif",
};

const btn = (variant) => ({
  padding: "9px 18px", borderRadius: 6, border: "none", cursor: "pointer",
  background: variant === "primary" ? colors.primary
    : variant === "danger" ? colors.loss : colors.bg.tertiary,
  color: variant === "primary" || variant === "danger" ? "#fff" : colors.text.primary,
  fontSize: 13, fontWeight: 600,
});

async function apiCall(path, options = {}) {
  const res = await fetch(`${getApiBase()}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!res.ok) throw new Error((await res.text()) || `API ${res.status}`);
  return res.json();
}

function fmtDur(s) {
  if (s == null) return "—";
  s = Math.round(s);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60), sec = s % 60;
  return `${m}m ${sec}s`;
}

function ProgressBar({ pct, label }) {
  return (
    <div style={{ marginTop: spacing.md }}>
      <div style={{ height: 8, background: colors.bg.secondary, borderRadius: 4, overflow: "hidden" }}>
        <div style={{
          height: "100%", width: `${Math.min(100, pct || 0).toFixed(1)}%`,
          background: colors.primary, transition: "width 0.4s ease",
        }} />
      </div>
      {label && (
        <div style={{ marginTop: 6, fontSize: 11, color: colors.text.muted, ...typography.mono }}>
          {label}
        </div>
      )}
    </div>
  );
}

export default function Backtest() {
  const saved = loadParams() || {};

  // ── Strategy ──
  const [strategyId, setStrategyId] = useState(saved.strategyId || "SCALP_V1");
  const isHedge = strategyId === "SCALP_V3" || strategyId === "SCALP_V4";

  // ── Backfill ──
  const [bfRunning, setBfRunning] = useState(false);
  const [bfStatus, setBfStatus] = useState(null);
  const [bfError, setBfError] = useState(null);
  const [bfCancelling, setBfCancelling] = useState(false);
  const bfPoll = useRef(null);

  // ── Dhan backfill (expired-options corpus fill) ──
  const [dhanRunning, setDhanRunning] = useState(false);
  const [dhanStatus, setDhanStatus] = useState(null);
  const [dhanError, setDhanError] = useState(null);
  const [dhanCancelling, setDhanCancelling] = useState(false);
  const [dhanFrom, setDhanFrom] = useState(saved.dhanFrom || "");
  const [dhanTo, setDhanTo] = useState(saved.dhanTo || "");
  const dhanPoll = useRef(null);

  // ── BANKNIFTY FUT backfill ──
  const [futRunning, setFutRunning] = useState(false);
  const [futStatus, setFutStatus] = useState(null);
  const [futError, setFutError] = useState(null);
  const [futCancelling, setFutCancelling] = useState(false);
  const [futFrom, setFutFrom] = useState(saved.futFrom || "");
  const [futTo, setFutTo] = useState(saved.futTo || "");
  const futPoll = useRef(null);

  // ── BANKNIFTY OPTIONS backfill ──
  const [bnfoptRunning, setBnfoptRunning] = useState(false);
  const [bnfoptStatus, setBnfoptStatus] = useState(null);
  const [bnfoptError, setBnfoptError] = useState(null);
  const [bnfoptCancelling, setBnfoptCancelling] = useState(false);
  const [bnfoptFrom, setBnfoptFrom] = useState(saved.bnfoptFrom || "");
  const [bnfoptTo, setBnfoptTo] = useState(saved.bnfoptTo || "");
  const bnfoptPoll = useRef(null);

  // ── Coverage ──
  const [coverage, setCoverage] = useState(null);

  // ── Form (rehydrated from localStorage) ──
  const [dateFrom, setDateFrom] = useState(saved.dateFrom || "");
  const [dateTo, setDateTo] = useState(saved.dateTo || "");
  const [premiumMin, setPremiumMin] = useState(saved.premiumMin ?? 150);
  const [premiumMax, setPremiumMax] = useState(saved.premiumMax ?? 200);
  const [rr, setRr] = useState(saved.rr ?? 1.0);
  const [minSl, setMinSl] = useState(saved.minSl ?? 5);
  const [maxSl, setMaxSl] = useState(saved.maxSl ?? 0);
  const [riskMaxSl, setRiskMaxSl] = useState(saved.riskMaxSl ?? 0);
  const [hedgeSl, setHedgeSl] = useState(saved.hedgeSl ?? 20);
  const [sessStart, setSessStart] = useState(saved.sessStart || "09:30");
  const [sessEnd, setSessEnd] = useState(saved.sessEnd || "15:20");
  const [lots, setLots] = useState(saved.lots ?? 10);

  // ── Run ──
  const [runRunning, setRunRunning] = useState(false);
  const [runStatus, setRunStatus] = useState(null);
  const [runError, setRunError] = useState(null);
  const [runCancelling, setRunCancelling] = useState(false);
  const [runId, setRunId] = useState(null);
  const [summary, setSummary] = useState(null);
  const [trades, setTrades] = useState([]);
  const [resultStrategy, setResultStrategy] = useState(strategyId);
  const runPoll = useRef(null);

  // Persist form params on any change.
  useEffect(() => {
    saveParams({ strategyId, dateFrom, dateTo, premiumMin, premiumMax, rr,
      minSl, maxSl, riskMaxSl, hedgeSl, sessStart, sessEnd, lots,
      dhanFrom, dhanTo, futFrom, futTo });
  }, [strategyId, dateFrom, dateTo, premiumMin, premiumMax, rr, minSl, maxSl,
      riskMaxSl, hedgeSl, sessStart, sessEnd, lots, dhanFrom, dhanTo, futFrom, futTo ]);

  // Load a run's full detail (summary + trades) into the table.
  const loadRunDetail = useCallback(async (rid) => {
    if (!rid) return;
    try {
      const d = await apiCall(`/api/backtest/runs/${rid}`);
      setRunId(rid);
      setSummary(d.summary || null);
      setTrades(d.trades || []);
      if (d.strategy_id) setResultStrategy(d.strategy_id);
    } catch { /* ignore */ }
  }, []);

  // Resume polling a running backtest.
  const startRunPolling = useCallback(() => {
    clearInterval(runPoll.current);
    runPoll.current = setInterval(async () => {
      try {
        const s = await apiCall("/api/backtest/run/status");
        setRunStatus(s);
        setRunRunning(s.running);
        if (!s.running) {
          clearInterval(runPoll.current);
          setRunError(s.error);
          setRunCancelling(false);
          if (s.run_id) await loadRunDetail(s.run_id);
        }
      } catch { /* keep polling */ }
    }, 1200);
  }, [loadRunDetail]);

  const startBackfillPolling = useCallback(() => {
    clearInterval(bfPoll.current);
    bfPoll.current = setInterval(async () => {
      try {
        const s = await apiCall("/api/backtest/backfill/status");
        setBfStatus(s);
        setBfRunning(s.running);
        if (!s.running) {
          clearInterval(bfPoll.current);
          setBfError(s.error);
          setBfCancelling(false);
          apiCall("/api/backtest/coverage?underlying=NIFTY").then(setCoverage).catch(() => {});
        }
      } catch { /* keep polling */ }
    }, 1500);
  }, []);

  // ── REHYDRATE ON MOUNT ──
  useEffect(() => {
    let cancelled = false;
    (async () => {
      // coverage + default date range
      try {
        const c = await apiCall("/api/backtest/coverage?underlying=NIFTY");
        if (!cancelled) {
          setCoverage(c);
          if (c.available && !saved.dateFrom) { setDateFrom(c.date_from); setDateTo(c.date_to); }
        }
      } catch { /* ignore */ }

      // run status — resume or load last result
      try {
        const s = await apiCall("/api/backtest/run/status");
        if (cancelled) return;
        setRunStatus(s);
        if (s.running) {
          setRunRunning(true);
          startRunPolling();
        } else if (s.run_id) {
          await loadRunDetail(s.run_id);
        } else {
          // no in-memory run (backend restarted) → load most recent persisted run
          try {
            const list = await apiCall("/api/backtest/runs?limit=1");
            if (!cancelled && list.runs && list.runs.length) {
              await loadRunDetail(list.runs[0].run_id);
            }
          } catch { /* ignore */ }
        }
      } catch { /* ignore */ }

    // backfill status — resume if running
      try {
        const b = await apiCall("/api/backtest/backfill/status");
        if (cancelled) return;
        setBfStatus(b);
        if (b.running) { setBfRunning(true); startBackfillPolling(); }
      } catch { /* ignore */ }

      // dhan backfill status — resume if running
      try {
        const dh = await apiCall("/api/backtest/dhan/status");
        if (cancelled) return;
        setDhanStatus(dh);
        if (dh.running) { setDhanRunning(true); startDhanPolling(); }
      } catch { /* ignore */ }

      try {
        const f = await apiCall("/api/backtest/dhan/fut/status");
        if (cancelled) return;
        setFutStatus(f);
        if (f.running) { setFutRunning(true); startFutPolling(); }
      } catch { /* ignore */ }

    })();
    return () => {
      cancelled = true;
      clearInterval(runPoll.current);
      clearInterval(bfPoll.current);
      clearInterval(dhanPoll.current);
      clearInterval(futPoll.current);
    };
  }, []);

  // ── Backfill actions ──
  const startBackfill = useCallback(async () => {
    setBfError(null);
    try {
      await apiCall("/api/backtest/backfill/start", {
        method: "POST",
        body: JSON.stringify({ underlyings: ["NIFTY"], lookback_days: 60, forward_buffer_days: 14 }),
      });
      setBfCancelling(false);
      setBfRunning(true);
      startBackfillPolling();
    } catch (e) { setBfError(String(e.message || e)); }
  }, [startBackfillPolling]);

  const cancelBackfill = useCallback(async () => {
    setBfCancelling(true);           // immediate UI ack
    try { await apiCall("/api/backtest/backfill/cancel", { method: "POST" }); } catch { /* ignore */ }
  }, []);

  const startDhanPolling = useCallback(() => {
    clearInterval(dhanPoll.current);
    dhanPoll.current = setInterval(async () => {
      try {
        const s = await apiCall("/api/backtest/dhan/status");
        setDhanStatus(s);
        setDhanRunning(s.running);
        if (!s.running) {
          clearInterval(dhanPoll.current);
          setDhanError(s.error);
          setDhanCancelling(false);
          apiCall("/api/backtest/coverage?underlying=NIFTY").then(setCoverage).catch(() => {});
        }
      } catch { /* keep polling */ }
    }, 1500);
  }, []);

  const startDhanBackfill = useCallback(async () => {
    setDhanError(null);
    if (!dhanFrom || !dhanTo) { setDhanError("Pick a Dhan date range"); return; }
    try {
      await apiCall("/api/backtest/dhan/backfill/start", {
        method: "POST",
        body: JSON.stringify({ underlying: "NIFTY", date_from: dhanFrom, date_to: dhanTo, atm_window: 10 }),
      });
      setDhanCancelling(false);
      setDhanRunning(true);
      startDhanPolling();
    } catch (e) { setDhanError(String(e.message || e)); }
  }, [dhanFrom, dhanTo, startDhanPolling]);

  const cancelDhanBackfill = useCallback(async () => {
    setDhanCancelling(true);
    try { await apiCall("/api/backtest/dhan/backfill/cancel", { method: "POST" }); } catch { /* ignore */ }
  }, []);

  const startFutPolling = useCallback(() => {
    clearInterval(futPoll.current);
    futPoll.current = setInterval(async () => {
      try {
        const s = await apiCall("/api/backtest/dhan/fut/status");
        setFutStatus(s);
        setFutRunning(s.running);
        if (!s.running) {
          clearInterval(futPoll.current);
          setFutError(s.error);
          setFutCancelling(false);
          apiCall("/api/backtest/coverage?underlying=BANKNIFTY").then(() => {}).catch(() => {});
        }
      } catch { /* keep polling */ }
    }, 1500);
  }, []);

  const startFutBackfill = useCallback(async () => {
    setFutError(null);
    if (!futFrom || !futTo) { setFutError("Pick a FUT date range"); return; }
    try {
      await apiCall("/api/backtest/dhan/fut/backfill/start", {
        method: "POST",
        body: JSON.stringify({ underlying: "BANKNIFTY", date_from: futFrom, date_to: futTo }),
      });
      setFutCancelling(false);
      setFutRunning(true);
      startFutPolling();
    } catch (e) { setFutError(String(e.message || e)); }
  }, [futFrom, futTo, startFutPolling]);

  const cancelFutBackfill = useCallback(async () => {
    setFutCancelling(true);
    try { await apiCall("/api/backtest/dhan/fut/backfill/cancel", { method: "POST" }); } catch { /* ignore */ }
  }, []);

  // ── Run actions ──
  const startRun = useCallback(async () => {
    setRunError(null);
    const isBB = strategyId === "BB_V1" || strategyId === "BB_V2";
    let config_override;
    if (isBB) {
      // BB is option-BUYING on BANKNIFTY: max_premium + sl_pct/tp_pct.
      config_override = {
        max_premium: Number(premiumMax),     // reuse "Premium max" as max_premium
        sl_pct: Number(minSl),               // reuse "Min SL pts" field as SL %
        tp_pct: Number(maxSl),               // reuse "Max SL cap" field as TP %
        lots: Number(lots),
        session_start: sessStart,
        session_end: sessEnd,
        max_trades_per_side: 10,
        scan_strikes: 60,
      };
    } else {
      config_override = {
        option_premium: { min: Number(premiumMin), max: Number(premiumMax) },
        risk_reward_ratio: Number(rr),
        min_sl_points: Number(minSl),
        max_sl_points: Number(maxSl),
        risk_max_sl_points: Number(riskMaxSl),
        session: { primary: { start: sessStart, end: sessEnd } },
        quantity: { lots: Number(lots) },
      };
      if (isHedge) config_override.hedge_sl_points = Number(hedgeSl);
    }
    try {
      await apiCall("/api/backtest/run/start", {
        method: "POST",
        body: JSON.stringify({
          strategy_id: strategyId, underlying: "NIFTY",
          date_from: dateFrom, date_to: dateTo, config_override,
        }),
      });
      setResultStrategy(strategyId);
      setSummary(null); setTrades([]); setRunId(null);
      setRunCancelling(false);
      setRunRunning(true);
      startRunPolling();
    } catch (e) { setRunError(String(e.message || e)); }
  }, [strategyId, isHedge, dateFrom, dateTo, premiumMin, premiumMax, rr, minSl,
      maxSl, riskMaxSl, hedgeSl, sessStart, sessEnd, lots, startRunPolling]);

  const cancelRun = useCallback(async () => {
    setRunCancelling(true);          // immediate UI ack
    try { await apiCall("/api/backtest/run/cancel", { method: "POST" }); } catch { /* ignore */ }
  }, []);

  const startBnfoptPolling = useCallback(() => {
    clearInterval(bnfoptPoll.current);
    bnfoptPoll.current = setInterval(async () => {
      try {
        const s = await apiCall("/api/backtest/bnf/opt/status");
        setBnfoptStatus(s);
        setBnfoptRunning(s.running);
        if (!s.running) {
          clearInterval(bnfoptPoll.current);
          setBnfoptError(s.error);
          setBnfoptCancelling(false);
        }
      } catch { /* keep polling */ }
    }, 1500);
  }, []);

  const startBnfoptBackfill = useCallback(async () => {
    setBnfoptError(null);
    if (!bnfoptFrom || !bnfoptTo) { setBnfoptError("Pick a date range"); return; }
    try {
      await apiCall("/api/backtest/bnf/opt/backfill/start", {
        method: "POST",
        body: JSON.stringify({ underlying: "BANKNIFTY", date_from: bnfoptFrom, date_to: bnfoptTo, atm_band: 50 }),
      });
      setBnfoptCancelling(false);
      setBnfoptRunning(true);
      startBnfoptPolling();
    } catch (e) { setBnfoptError(String(e.message || e)); }
  }, [bnfoptFrom, bnfoptTo, startBnfoptPolling]);

  const cancelBnfoptBackfill = useCallback(async () => {
    setBnfoptCancelling(true);
    try { await apiCall("/api/backtest/bnf/opt/backfill/cancel", { method: "POST" }); } catch { /* ignore */ }
  }, []);

  // ── Download CSV (blob method, Tauri-safe — same approach as PaperTrades) ──
  const downloadCsv = useCallback(async () => {
    if (!runId) return;
    try {
      const res = await fetch(`${getApiBase()}/api/backtest/runs/${runId}/csv`);
      if (!res.ok) throw new Error(`CSV ${res.status}`);
      const text = await res.text();
      const blob = new Blob([text], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `backtest_${runId.slice(0, 8)}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { setRunError(String(e.message || e)); }
  }, [runId]);

  const resultIsHedge = resultStrategy === "SCALP_V3" || resultStrategy === "SCALP_V4";
  const s = summary;

  // Sort trades newest-first by entry_ts.
  const sortedTrades = React.useMemo(
    () => [...trades].sort((a, b) => (b.entry_ts || 0) - (a.entry_ts || 0)),
    [trades]
  );

  // Progress labels
  const runProg = runStatus?.progress;
  const runLabel = runCancelling
    ? "cancelling… (stops at the next checkpoint)"
    : runProg
    ? `day ${runProg.day}/${runProg.total_days}` +
      `${runProg.minutes_total ? ` · min ${runProg.minute}/${runProg.minutes_total}` : ""}` +
      ` · ${runProg.date}` +
      `${runStatus.eta_s != null ? ` · ETA ~${fmtDur(runStatus.eta_s)}` : ""}` +
      ` · elapsed ${fmtDur(runStatus.elapsed_s)}`
    : "starting…";
  const bfProg = bfStatus?.progress;
  const bfLabel = bfCancelling
    ? "cancelling… (stops at the next token)"
    : bfProg
    ? `${bfProg.done}/${bfProg.total} tokens · ok ${bfProg.ok} · failed ${bfProg.failed}` +
      `${bfStatus.eta_s != null ? ` · ETA ~${fmtDur(bfStatus.eta_s)}` : ""}` +
      ` · elapsed ${fmtDur(bfStatus.elapsed_s)}`
    : "starting…";
  const dhanProg = dhanStatus?.progress;
  const dhanLabel = dhanCancelling
    ? "cancelling… (stops at the next request)"
    : dhanProg
    ? `${dhanProg.done}/${dhanProg.planned} requests · ${dhanProg.chunk || ""} ${dhanProg.offset || ""} ${dhanProg.side || ""} · rows ${dhanProg.rows?.toLocaleString("en-IN") || 0}` +
      `${dhanStatus.eta_s != null ? ` · ETA ~${fmtDur(dhanStatus.eta_s)}` : ""}` +
      ` · elapsed ${fmtDur(dhanStatus.elapsed_s)}`
    : "starting…";

  const futProg = futStatus?.progress;
  const futLabel = futCancelling
    ? "cancelling…"
    : futProg
    ? `${futProg.done}/${futProg.planned} · ${futProg.contract || ""} ${futProg.window || ""} · rows ${futProg.rows?.toLocaleString("en-IN") || 0}` +
      `${futStatus.eta_s != null ? ` · ETA ~${fmtDur(futStatus.eta_s)}` : ""}` +
      ` · elapsed ${fmtDur(futStatus.elapsed_s)}`
    : "starting…";

  const bnfoptProg = bnfoptStatus?.progress;
  const bnfoptLabel = bnfoptCancelling
    ? "cancelling…"
    : bnfoptProg
    ? `${bnfoptProg.done}/${bnfoptProg.planned} · ${bnfoptProg.expiry || ""} ${bnfoptProg.strike || ""}${bnfoptProg.side || ""} · rows ${bnfoptProg.rows?.toLocaleString("en-IN") || 0}` +
      `${bnfoptStatus.eta_s != null ? ` · ETA ~${fmtDur(bnfoptStatus.eta_s)}` : ""}` +
      ` · elapsed ${fmtDur(bnfoptStatus.elapsed_s)}`
    : "starting…";

  return (
    <div style={{
      padding: spacing.xxl, background: colors.bg.primary, color: colors.text.primary,
      minHeight: "100vh", fontFamily: "'Inter', sans-serif", paddingBottom: 56,
    }}>
      <h1 style={{ margin: 0, fontSize: 26, fontWeight: 700 }}>Backtest</h1>
      <p style={{ margin: "4px 0 16px", fontSize: 12, color: colors.text.muted }}>
        {isHedge
          ? `${strategyId === "SCALP_V4" ? "SCALP V4" : "SCALP V3"} · NIFTY · option-BUYING hedge · signal tracked, opposite-side hedge bought (LONG)`
          : "SCALP V1 · NIFTY · short-selling · 1-minute OHLC · pessimistic fills"}
      </p>

      {/* ── Strategy selector ── */}
      <div style={{ display: "flex", gap: spacing.sm, marginBottom: spacing.lg }}>
        {[
          { id: "SCALP_V1", label: "SCALP V1", sub: "short" },
          { id: "SCALP_V3", label: "SCALP V3", sub: "hedge" },
          { id: "SCALP_V4", label: "SCALP V4", sub: "hedge + veto" },
          { id: "BB_V1", label: "BB V1", sub: "BANKNIFTY buy" },
          { id: "BB_V2", label: "BB V2", sub: "BANKNIFTY buy" },
        ].map((o) => {
          const active = strategyId === o.id;
          return (
            <button key={o.id} onClick={() => setStrategyId(o.id)}
              style={{
                padding: "8px 16px", borderRadius: 7, cursor: "pointer",
                border: `1px solid ${active ? colors.primary : colors.border.light}`,
                background: active ? colors.primaryBg : colors.bg.secondary,
                color: active ? colors.primary : colors.text.secondary,
                fontSize: 13, fontWeight: 600,
                display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 1,
              }}>
              {o.label}
              <span style={{ fontSize: 9, opacity: 0.7, fontWeight: 400 }}>{o.sub}</span>
            </button>
          );
        })}
      </div>

      {/* ── BACKFILL PANEL ── */}
      <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.xl }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: spacing.md }}>
          <div>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: 4 }}>Historical data</div>
            <div style={{ fontSize: 13, color: colors.text.secondary }}>
              {coverage?.available
                ? <>Corpus: <b>{coverage.date_from}</b> → <b>{coverage.date_to}</b> · {coverage.candles?.toLocaleString("en-IN")} candles</>
                : "No data yet — run a backfill to pull the last 60 days from Kite."}
            </div>
          </div>
          <div style={{ display: "flex", gap: spacing.sm }}>
            <button style={btn("default")} disabled={bfRunning} onClick={startBackfill}>
              {bfRunning ? "Backfilling…" : "Run backfill (60d)"}
            </button>
            {bfRunning && (
              <button style={btn("danger")} onClick={cancelBackfill} disabled={bfCancelling}>
                {bfCancelling ? "Cancelling…" : "Cancel"}
              </button>
            )}
          </div>
        </div>
        {bfRunning && <ProgressBar pct={bfStatus?.pct} label={bfLabel} />}
        {!bfRunning && bfStatus?.result && !bfError && (
          <div style={{ marginTop: spacing.md, fontSize: 12, color: colors.profit }}>
            Done · {bfStatus.result.ok} ok / {bfStatus.result.failed} failed · {bfStatus.result.candles_written?.toLocaleString("en-IN")} candles · {fmtDur(bfStatus.result.elapsed_s)}
          </div>
        )}
        {bfError && (
          <div style={{ marginTop: spacing.md, fontSize: 12, color: bfError === "cancelled" ? colors.warning : colors.loss }}>
            {bfError === "cancelled" ? "Backfill cancelled." : bfError}
          </div>
        )}

                {/* ── DHAN BACKFILL (expired-options; fills weeks Kite can't return) ── */}
        <div style={{ marginTop: spacing.lg, paddingTop: spacing.lg, borderTop: `1px solid ${colors.border.dark}` }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: spacing.md }}>
            <div>
              <div style={{ ...typography.label, color: colors.text.muted, marginBottom: 4 }}>Dhan backfill (expired weeklies)</div>
              <div style={{ fontSize: 12, color: colors.text.secondary }}>
                {dhanStatus?.creds_set
                  ? <>Fills the exact per-week contracts Kite can't return. ATM±10, NIFTY. Client <b>{dhanStatus.client_id}</b>.</>
                  : "Add Dhan credentials in Connections to enable expired-options backfill."}
              </div>
            </div>
            <div style={{ display: "flex", gap: spacing.sm, alignItems: "flex-end", flexWrap: "wrap" }}>
              <Field label="Dhan from"><input type="date" style={inputStyle} value={dhanFrom} onChange={(e) => setDhanFrom(e.target.value)} /></Field>
              <Field label="Dhan to"><input type="date" style={inputStyle} value={dhanTo} onChange={(e) => setDhanTo(e.target.value)} /></Field>
              <button style={btn("default")} disabled={dhanRunning || !dhanStatus?.creds_set} onClick={startDhanBackfill}>
                {dhanRunning ? "Backfilling…" : "Backfill (Dhan)"}
              </button>
              {dhanRunning && (
                <button style={btn("danger")} onClick={cancelDhanBackfill} disabled={dhanCancelling}>
                  {dhanCancelling ? "Cancelling…" : "Cancel"}
                </button>
              )}
            </div>
          </div>
          {dhanRunning && <ProgressBar pct={dhanStatus?.pct} label={dhanLabel} />}
          {!dhanRunning && dhanStatus?.result && !dhanError && (
            <div style={{ marginTop: spacing.md, fontSize: 12, color: colors.profit }}>
              Done · {dhanStatus.result.rows_upserted?.toLocaleString("en-IN")} rows · {dhanStatus.result.days_covered} days · {dhanStatus.result.expiries?.length || 0} expiries · {dhanStatus.result.requests} requests
              {dhanStatus.result.errors?.length ? ` · ${dhanStatus.result.errors.length} call errors` : ""}
            </div>
          )}
          {dhanError && (
            <div style={{ marginTop: spacing.md, fontSize: 12, color: dhanError === "cancelled" ? colors.warning : colors.loss }}>
              {dhanError === "cancelled" ? "Dhan backfill cancelled." : dhanError}
            </div>
          )}
        </div>

        <div style={{ marginTop: spacing.lg, paddingTop: spacing.lg, borderTop: `1px solid ${colors.border.dark}` }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: spacing.md }}>
            <div>
              <div style={{ ...typography.label, color: colors.text.muted, marginBottom: 4 }}>BANKNIFTY options (for BB)</div>
              <div style={{ fontSize: 12, color: colors.text.secondary }}>
                {dhanStatus?.creds_set
                  ? <>ATM±50 strikes per monthly expiry, anchored to the BANKNIFTY FUT series. Backfill FUT first.</>
                  : "Add Dhan credentials in Connections to enable."}
              </div>
            </div>
            <div style={{ display: "flex", gap: spacing.sm, alignItems: "flex-end", flexWrap: "wrap" }}>
              <Field label="OPT from"><input type="date" style={inputStyle} value={bnfoptFrom} onChange={(e) => setBnfoptFrom(e.target.value)} /></Field>
              <Field label="OPT to"><input type="date" style={inputStyle} value={bnfoptTo} onChange={(e) => setBnfoptTo(e.target.value)} /></Field>
              <button style={btn("default")} disabled={bnfoptRunning || !dhanStatus?.creds_set} onClick={startBnfoptBackfill}>
                {bnfoptRunning ? "Backfilling…" : "Backfill BANKNIFTY OPT"}
              </button>
              {bnfoptRunning && (
                <button style={btn("danger")} onClick={cancelBnfoptBackfill} disabled={bnfoptCancelling}>
                  {bnfoptCancelling ? "Cancelling…" : "Cancel"}
                </button>
              )}
            </div>
          </div>
          {bnfoptRunning && <ProgressBar pct={bnfoptStatus?.pct} label={bnfoptLabel} />}
          {!bnfoptRunning && bnfoptStatus?.result && !bnfoptError && (
            <div style={{ marginTop: spacing.md, fontSize: 12, color: colors.profit }}>
              Done · {bnfoptStatus.result.rows_upserted?.toLocaleString("en-IN")} rows · {bnfoptStatus.result.expiries?.length || 0} expiries · {bnfoptStatus.result.strikes_with_data} strikes w/ data
            </div>
          )}
          {bnfoptError && (
            <div style={{ marginTop: spacing.md, fontSize: 12, color: colors.loss }}>{bnfoptError}</div>
          )}
        </div>


        {/* ── BANKNIFTY FUT (continuous front-month, for BB backtests) ── */}
        <div style={{ marginTop: spacing.lg, paddingTop: spacing.lg, borderTop: `1px solid ${colors.border.dark}` }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: spacing.md }}>
            <div>
              <div style={{ ...typography.label, color: colors.text.muted, marginBottom: 4 }}>BANKNIFTY futures (for BB)</div>
              <div style={{ fontSize: 12, color: colors.text.secondary }}>
                {dhanStatus?.creds_set
                  ? <>Continuous front-month BANKNIFTY FUT series. Reaches back ~3 months (live contracts only).</>
                  : "Add Dhan credentials in Connections to enable."}
              </div>
            </div>
            <div style={{ display: "flex", gap: spacing.sm, alignItems: "flex-end", flexWrap: "wrap" }}>
              <Field label="FUT from"><input type="date" style={inputStyle} value={futFrom} onChange={(e) => setFutFrom(e.target.value)} /></Field>
              <Field label="FUT to"><input type="date" style={inputStyle} value={futTo} onChange={(e) => setFutTo(e.target.value)} /></Field>
              <button style={btn("default")} disabled={futRunning || !dhanStatus?.creds_set} onClick={startFutBackfill}>
                {futRunning ? "Backfilling…" : "Backfill BANKNIFTY FUT"}
              </button>
              {futRunning && (
                <button style={btn("danger")} onClick={cancelFutBackfill} disabled={futCancelling}>
                  {futCancelling ? "Cancelling…" : "Cancel"}
                </button>
              )}
            </div>
          </div>
          {futRunning && <ProgressBar pct={futStatus?.pct} label={futLabel} />}
          {!futRunning && futStatus?.result && !futError && (
            <div style={{ marginTop: spacing.md, fontSize: 12, color: colors.profit }}>
              Done · {futStatus.result.rows_upserted?.toLocaleString("en-IN")} rows · {futStatus.result.days_covered} days · {futStatus.result.contracts_used?.length || 0} contracts ({(futStatus.result.contracts_used || []).join(", ")})
            </div>
          )}
          {futError && (
            <div style={{ marginTop: spacing.md, fontSize: 12, color: colors.loss }}>{futError}</div>
          )}
        </div>
      </Card>

      {/* ── BACKTEST PANEL ── */}
      <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.xl }}>
        <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>Run parameters</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: spacing.md }}>
          <Field label="Date from"><input type="date" style={inputStyle} value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} /></Field>
          <Field label="Date to"><input type="date" style={inputStyle} value={dateTo} onChange={(e) => setDateTo(e.target.value)} /></Field>
          <Field label="Premium min"><input type="number" style={inputStyle} value={premiumMin} onChange={(e) => setPremiumMin(e.target.value)} /></Field>
          <Field label="Premium max"><input type="number" style={inputStyle} value={premiumMax} onChange={(e) => setPremiumMax(e.target.value)} /></Field>
          <Field label="Risk:Reward"><input type="number" step="0.1" style={inputStyle} value={rr} onChange={(e) => setRr(e.target.value)} /></Field>
          <Field label="Min SL pts"><input type="number" style={inputStyle} value={minSl} onChange={(e) => setMinSl(e.target.value)} /></Field>
          <Field label="Max SL cap"><input type="number" style={inputStyle} value={maxSl} onChange={(e) => setMaxSl(e.target.value)} /></Field>
          <Field label="Risk Max SL"><input type="number" style={inputStyle} value={riskMaxSl} onChange={(e) => setRiskMaxSl(e.target.value)} /></Field>
          {isHedge && (
            <Field label="Hedge SL pts"><input type="number" style={inputStyle} value={hedgeSl} onChange={(e) => setHedgeSl(e.target.value)} /></Field>
          )}
          <Field label="Session start"><input type="text" style={inputStyle} value={sessStart} onChange={(e) => setSessStart(e.target.value)} /></Field>
          <Field label="Session end"><input type="text" style={inputStyle} value={sessEnd} onChange={(e) => setSessEnd(e.target.value)} /></Field>
          <Field label="Lots"><input type="number" style={inputStyle} value={lots} onChange={(e) => setLots(e.target.value)} /></Field>
        </div>
        <div style={{ marginTop: spacing.lg, display: "flex", gap: spacing.md, alignItems: "center" }}>
          <button style={btn("primary")} disabled={runRunning || !dateFrom || !dateTo} onClick={startRun}>
            {runRunning ? "Running…" : "Run backtest"}
          </button>
          {runRunning && (
            <button style={btn("danger")} onClick={cancelRun} disabled={runCancelling}>
              {runCancelling ? "Cancelling…" : "Cancel"}
            </button>
          )}
        </div>
        {runRunning && <ProgressBar pct={runStatus?.pct} label={runLabel} />}
        {runError && (
          <div style={{ marginTop: spacing.md, fontSize: 12, color: runError === "cancelled" ? colors.warning : colors.loss }}>
            {runError === "cancelled" ? "Backtest cancelled." : runError}
          </div>
        )}
      </Card>

      {/* ── RESULTS ── */}
      {s && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: spacing.md, marginBottom: spacing.lg }}>
            <Card elevated style={{ padding: spacing.lg }}>
              <div style={{ ...typography.label, color: colors.text.muted }}>Gross P&L</div>
              <div style={{ fontSize: 22, fontWeight: 700, ...typography.mono, ...pnlStyle(s.gross_pnl) }}>
                {s.gross_pnl >= 0 ? "+" : ""}₹{Math.round(s.gross_pnl).toLocaleString("en-IN")}
              </div>
            </Card>
            <Card elevated style={{ padding: spacing.lg }}>
              <div style={{ ...typography.label, color: colors.text.muted }}>Charges</div>
              <div style={{ fontSize: 22, fontWeight: 700, ...typography.mono, color: colors.loss }}>
                −₹{Math.round(s.total_charges).toLocaleString("en-IN")}
              </div>
            </Card>
            <Card elevated style={{ padding: spacing.lg }}>
              <div style={{ ...typography.label, color: colors.text.muted }}>Net P&L</div>
              <div style={{ fontSize: 22, fontWeight: 700, ...typography.mono, ...pnlStyle(s.net_pnl) }}>
                {s.net_pnl >= 0 ? "+" : ""}₹{Math.round(s.net_pnl).toLocaleString("en-IN")}
              </div>
            </Card>
            <Card elevated style={{ padding: spacing.lg }}>
              <div style={{ ...typography.label, color: colors.text.muted }}>Win rate</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: s.win_rate >= 50 ? colors.profit : colors.loss }}>
                {s.win_rate.toFixed(1)}%
              </div>
              <div style={{ fontSize: 11, color: colors.text.tertiary, marginTop: 3 }}>{s.wins}W / {s.losses}L</div>
            </Card>
            <Card elevated style={{ padding: spacing.lg }}>
              <div style={{ ...typography.label, color: colors.text.muted }}>Trades</div>
              <div style={{ fontSize: 22, fontWeight: 700 }}>{s.total_trades}</div>
              <div style={{ fontSize: 11, color: colors.text.tertiary, marginTop: 3 }}>{s.ambiguous_fills} ambiguous</div>
            </Card>
            <Card elevated style={{ padding: spacing.lg }}>
              <div style={{ ...typography.label, color: colors.text.muted }}>Max DD (net)</div>
              <div style={{ fontSize: 22, fontWeight: 700, ...typography.mono, color: colors.loss }}>
                ₹{Math.round(s.max_drawdown).toLocaleString("en-IN")}
              </div>
            </Card>
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: spacing.sm }}>
            <button style={btn("default")} onClick={downloadCsv}>📄 Download CSV</button>
          </div>

          <Card>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}>
                <thead style={{ background: colors.bg.tertiary }}>
                  <tr>
                    {(resultIsHedge
                      ? ["Signal", "Hedge", "Entry", "Hedge ₹", "Hedge SL", "Exit", "Exit ₹", "Reason", "Gross", "Charges", "Net", "Amb"]
                      : ["Symbol", "Entry", "Entry ₹", "SL", "TP", "Exit", "Exit ₹", "Reason", "Gross", "Charges", "Net", "Amb"]
                    ).map((h) => (
                      <th key={h} style={{ padding: "9px 8px", textAlign: "left", ...typography.label, color: colors.text.muted, borderBottom: `2px solid ${colors.border.light}`, whiteSpace: "nowrap" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sortedTrades.map((t, i) => (
                    <tr key={i} style={{ background: i % 2 ? colors.bg.secondary : colors.bg.primary, borderTop: `1px solid ${colors.border.dark}` }}>
                      {resultIsHedge && (
                        <td style={{ padding: "8px", ...typography.mono, fontSize: 11, color: colors.text.secondary, whiteSpace: "nowrap" }}>
                          {t.signal_symbol}
                          <span style={{ fontSize: 9, color: colors.text.muted, marginLeft: 4 }}>{t.signal_side}</span>
                        </td>
                      )}
                      <td style={{ padding: "8px", ...typography.mono, fontWeight: 600, whiteSpace: "nowrap" }}>{t.tradingsymbol}</td>
                      <td style={{ padding: "8px", ...typography.mono, fontSize: 11, color: colors.text.tertiary, whiteSpace: "nowrap" }}>{fmtTs(t.entry_ts)}</td>
                      <td style={{ padding: "8px", ...typography.mono, textAlign: "right" }}>{t.entry_price?.toFixed(2)}</td>
                      <td style={{ padding: "8px", ...typography.mono, textAlign: "right", color: colors.loss }}>{t.sl?.toFixed(2)}</td>
                      {!resultIsHedge && (
                        <td style={{ padding: "8px", ...typography.mono, textAlign: "right", color: colors.profit }}>{t.tp?.toFixed(2)}</td>
                      )}
                      <td style={{ padding: "8px", ...typography.mono, fontSize: 11, color: colors.text.tertiary, whiteSpace: "nowrap" }}>{fmtTs(t.exit_ts)}</td>
                      <td style={{ padding: "8px", ...typography.mono, textAlign: "right" }}>{t.exit_price?.toFixed(2)}</td>
                      <td style={{ padding: "8px" }}>
                        <span style={{ padding: "2px 6px", borderRadius: 4, fontSize: 11, fontWeight: 600,
                          background: (t.exit_reason === "TP" || t.exit_reason === "SIG_TP") ? colors.successBg : t.exit_reason === "EOD" ? colors.warningBg : colors.lossBg,
                          color: (t.exit_reason === "TP" || t.exit_reason === "SIG_TP") ? colors.success : t.exit_reason === "EOD" ? colors.warning : colors.loss }}>
                          {t.exit_reason}
                        </span>
                      </td>
                      <td style={{ padding: "8px", ...typography.mono, textAlign: "right", ...pnlStyle(t.pnl) }}>{Math.round(t.pnl).toLocaleString("en-IN")}</td>
                      <td style={{ padding: "8px", ...typography.mono, textAlign: "right", color: colors.loss }}>−{Math.round(t.charges).toLocaleString("en-IN")}</td>
                      <td style={{ padding: "8px", ...typography.mono, textAlign: "right", fontWeight: 700, ...pnlStyle(t.net_pnl) }}>{Math.round(t.net_pnl).toLocaleString("en-IN")}</td>
                      <td style={{ padding: "8px", textAlign: "center" }}>{t.ambiguous_fill ? "⚠️" : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

function fmtTs(epoch) {
  if (!epoch) return "—";
  const d = new Date(epoch * 1000);
  return d.toLocaleString("en-IN", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
}