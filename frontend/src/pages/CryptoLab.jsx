// frontend/src/pages/CryptoLab.jsx
//
// ── CRYPTO_LAB ── Crypto Options Lab (admin-only; gated in App.jsx).
//
// BTC daily-options backtest playground on Delta Exchange (India) data:
//   * Corpus panel — collect/extend the local candle corpus (months, strike
//     span, candle window 25–48h for early-entry experiments, request pace),
//     with live progress, disk-guard surfacing, and cancel (resumable).
//   * Config panel — structure (condor/strangle), free entry timing (0/1/2
//     days before expiry at any HH:MM IST), exit time, short selection by
//     premium-ratio OR OTM %, wing selection by premium-ratio OR OTM gap,
//     MTM SL / TP, size, fee model (+GST toggle), date range, expiry-weekday
//     filter, exclude-dates list.
//   * Results — summary cards, exit/skip breakdown, equity curve (SVG),
//     trades table, client-side CSV. Run history persisted server-side
//     (crypto_backtest.db lab_runs) and reloadable.
//
// STATE: backend is source of truth for jobs/runs (rehydrated on mount);
// form params persist to localStorage. Backend routes are admin-gated
// (/api/backtest/crypto/* inherits the backtest router's gate).
//
// NOTE: window.confirm/alert are blocked in Tauri's webview — destructive
// actions here are limited to job cancels (safe, resumable), so no confirm
// dialogs are used at all.

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getApiBase } from "../api/base";
import { colors, spacing, typography } from "../tokens";

const LS_KEY = "crypto_lab_params_v1";
const WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const DEFAULT_PARAMS = {
  structure: "condor",
  entry_days_before: 1,
  entry_hm: "17:45",
  exit_hm: "17:15",
  short_mode: "premium_ratio",
  short_ratio: 0.25,
  short_otm_pct: 1.5,
  wing_mode: "premium_ratio",
  wing_prem_ratio: 0.25,
  wing_gap_pct: 1.0,
  sl_mult: 1.5,
  tp_ratio: 0.0,
  contracts: 100,
  fee_mult: 1.0,
  gst_pct: 0.0,
  margin_buffer_pct: 10.0,
  margin_shock_pct: 10.0,
  spread_signal: "tma_trend",
  ema_fast: 20,
  ema_mid: 50,
  ema_slow: 200,
  ema_tf_min: 15,
  date_from: "",
  date_to: "",
  weekdays: [0, 1, 2, 3, 4, 5, 6],
  exclude_dates_text: "",
};

function loadParams() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    return raw ? { ...DEFAULT_PARAMS, ...JSON.parse(raw) } : { ...DEFAULT_PARAMS };
  } catch { return { ...DEFAULT_PARAMS }; }
}
function saveParams(p) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(p)); } catch { /* ignore */ }
}

function Card({ children, style }) {
  return (
    <div style={{
      background: colors.bg.secondary,
      border: `1px solid ${colors.border.light}`,
      borderRadius: 8, padding: 16,
      boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
      ...style,
    }}>{children}</div>
  );
}

function Field({ label, children, hint }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 110 }}>
      <span style={{ ...typography.label, color: colors.text.muted, fontSize: 11 }}>{label}</span>
      {children}
      {hint ? <span style={{ fontSize: 10, color: colors.text.tertiary }}>{hint}</span> : null}
    </label>
  );
}

const inputStyle = {
  padding: "7px 10px", borderRadius: 6,
  border: `1px solid ${colors.border.light}`,
  background: colors.bg.secondary, color: colors.text.primary,
  fontSize: 13, outline: "none", fontFamily: "'Inter', sans-serif",
};

function Btn({ children, onClick, disabled, danger, primary, small }) {
  return (
    <button onClick={onClick} disabled={disabled} style={{
      padding: small ? "5px 10px" : "8px 16px",
      borderRadius: 6, fontSize: small ? 12 : 13, fontWeight: 600,
      cursor: disabled ? "not-allowed" : "pointer",
      opacity: disabled ? 0.5 : 1,
      border: `1px solid ${danger ? colors.danger : primary ? colors.primary : colors.border.light}`,
      background: danger ? "rgba(239,68,68,0.12)" : primary ? colors.primaryBg : colors.bg.tertiary,
      color: danger ? colors.danger : primary ? colors.primary : colors.text.primary,
    }}>{children}</button>
  );
}

function StatCard({ label, value, sub, tone }) {
  const col = tone === "good" ? colors.success : tone === "bad" ? colors.danger : colors.text.primary;
  return (
    <div style={{
      background: colors.bg.tertiary, borderRadius: 8, padding: "10px 14px",
      border: `1px solid ${colors.border.light}`, minWidth: 130,
    }}>
      <div style={{ fontSize: 10, color: colors.text.muted, textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color: col, marginTop: 2 }}>{value}</div>
      {sub ? <div style={{ fontSize: 10, color: colors.text.tertiary, marginTop: 2 }}>{sub}</div> : null}
    </div>
  );
}

function usd(v) {
  if (v === null || v === undefined) return "—";
  const s = v < 0 ? "-" : "";
  return `${s}$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

/* ── Equity curve: cumulative net USD, pure SVG, no deps ── */
function EquityCurve({ trades }) {
  const pts = useMemo(() => {
    let eq = 0;
    return trades.map((t) => { eq += t.usd_net; return eq; });
  }, [trades]);
  if (!pts.length) return null;
  const W = 860, H = 220, PAD = 34;
  const min = Math.min(0, ...pts), max = Math.max(0, ...pts);
  const span = max - min || 1;
  const x = (i) => PAD + (i / Math.max(1, pts.length - 1)) * (W - 2 * PAD);
  const y = (v) => H - PAD - ((v - min) / span) * (H - 2 * PAD);
  const path = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const zero = y(0);
  const last = pts[pts.length - 1];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }}>
      <line x1={PAD} y1={zero} x2={W - PAD} y2={zero}
        stroke={colors.border.light} strokeDasharray="4 4" />
      <path d={path} fill="none"
        stroke={last >= 0 ? colors.success : colors.danger} strokeWidth="1.8" />
      <text x={PAD} y={14} fill={colors.text.muted} fontSize="10">
        cumulative net USD (sequential by expiry)
      </text>
      <text x={W - PAD} y={14} fill={last >= 0 ? colors.success : colors.danger}
        fontSize="11" textAnchor="end" fontWeight="700">{usd(last)}</text>
    </svg>
  );
}

export default function CryptoLab() {
  const api = getApiBase();
  const [params, setParams] = useState(loadParams);
  const [corpus, setCorpus] = useState(null);      // {job, stats}
  const [run, setRun] = useState(null);            // run job state
  const [result, setResult] = useState(null);      // {run_id, summary, trades}
  const [runsList, setRunsList] = useState([]);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [csvMsg, setCsvMsg] = useState(null);   // {kind:'ok'|'err'|'info', text}
  const pollRef = useRef(null);

  const set = useCallback((k, v) => {
    setParams((p) => { const n = { ...p, [k]: v }; saveParams(n); return n; });
  }, []);

  /* ── polling: corpus + run status; rehydrates on mount ── */
  const poll = useCallback(async () => {
    try {
      const [cs, rs] = await Promise.all([
        fetch(`${api}/api/backtest/crypto/corpus/status`).then((r) => r.json()),
        fetch(`${api}/api/backtest/crypto/run/status`).then((r) => r.json()),
      ]);
      setCorpus(cs);
      setRun(rs);
      if (rs && rs.result && !rs.running) {
        setResult((prev) => (prev && prev.run_id === rs.result.run_id ? prev : rs.result));
      }
    } catch { /* backend down; status bar shows it */ }
  }, [api]);

  const loadRuns = useCallback(async () => {
    try {
      const j = await fetch(`${api}/api/backtest/crypto/runs?limit=30`).then((r) => r.json());
      setRunsList(j.runs || []);
    } catch { /* ignore */ }
  }, [api]);

  useEffect(() => {
    poll(); loadRuns();
    pollRef.current = setInterval(poll, 2000);
    return () => clearInterval(pollRef.current);
  }, [poll, loadRuns]);   // ── CRYPTO_LAB ── deps: stable callbacks above

  /* ── actions ── */
  const [collectForm, setCollectForm] = useState({ months: 24, span_pct: 4.0, window_h: 25, pace_s: 0.35, include_perp: true });

  const startCollect = useCallback(async () => {
    setErr(""); setNote("");
    try {
      const r = await fetch(`${api}/api/backtest/crypto/corpus/backfill/start`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(collectForm),
      });
      const j = await r.json();
      if (!r.ok) setErr(j.detail || "corpus start failed");
      poll();
    } catch (e) { setErr(String(e)); }
  }, [api, collectForm, poll]);

  const cancelCollect = useCallback(async () => {
    try { await fetch(`${api}/api/backtest/crypto/corpus/backfill/cancel`, { method: "POST" }); poll(); }
    catch (e) { setErr(String(e)); }
  }, [api, poll]);

  const startRun = useCallback(async () => {
    setErr(""); setNote("");
    const body = {
      ...params,
      entry_days_before: Number(params.entry_days_before),
      short_ratio: Number(params.short_ratio),
      short_otm_pct: Number(params.short_otm_pct),
      wing_prem_ratio: Number(params.wing_prem_ratio),
      wing_gap_pct: Number(params.wing_gap_pct),
      sl_mult: Number(params.sl_mult),
      tp_ratio: Number(params.tp_ratio),
      contracts: Number(params.contracts),
      fee_mult: Number(params.fee_mult),
      gst_pct: Number(params.gst_pct),
      margin_buffer_pct: Number(params.margin_buffer_pct),
      margin_shock_pct: Number(params.margin_shock_pct),
      ema_fast: Number(params.ema_fast),
      ema_mid: Number(params.ema_mid),
      ema_slow: Number(params.ema_slow),
      ema_tf_min: Number(params.ema_tf_min),
      exclude_dates: params.exclude_dates_text
        .split(/[\s,]+/).map((s) => s.trim()).filter(Boolean),
    };
    delete body.exclude_dates_text;
    try {
      const r = await fetch(`${api}/api/backtest/crypto/run/start`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const j = await r.json();
      if (!r.ok) { setErr(j.detail || "run start failed"); return; }
      if (j.corpus_busy) setNote("Corpus collection is running — this run is NON-AUTHORITATIVE (corpus is a moving target). Re-run after collection finishes.");
      poll();
    } catch (e) { setErr(String(e)); }
  }, [api, params, poll]);

  const cancelRun = useCallback(async () => {
    try { await fetch(`${api}/api/backtest/crypto/run/cancel`, { method: "POST" }); poll(); }
    catch (e) { setErr(String(e)); }
  }, [api, poll]);

  const loadRun = useCallback(async (runId) => {
    setErr("");
    try {
      const r = await fetch(`${api}/api/backtest/crypto/runs/${runId}`);
      if (!r.ok) { setErr("run not found"); return; }
      setResult(await r.json());
    } catch (e) { setErr(String(e)); }
  }, [api]);

  // ── CRYPTO_LAB ── CSV is built CLIENT-SIDE from loaded trades with a
  // visible download acknowledgement — the proven Backtest.jsx convention
  // (window.open is swallowed by the Tauri webview, and a backend-fetched
  // CSV can fail silently; building locally uses only the /runs/{id} JSON
  // endpoint that the Load button already exercises).
  const CSV_COLS = [
    "expiry", "date", "weekday", "spot",
    "entry_ist", "exit_ist", "entry_ts", "exit_ts", "hold_min",
    "sc", "sc_prem", "sc_xprem", "sp", "sp_prem", "sp_xprem",
    "wc", "wc_prem", "wc_xprem", "wp", "wp_prem", "wp_xprem",
    "credit", "exit_debit", "sl_level", "tp_level", "exit_reason",
    "pnl_unit", "best_unit", "worst_unit",
    "usd_gross", "usd_fees", "usd_net", "margin_unit", "margin_usd",
  ];
  const csvEscape = (v) => {
    if (v === null || v === undefined) return "";
    const str = String(v);
    return /[",\n]/.test(str) ? `"${str.replace(/"/g, '""')}"` : str;
  };
  const buildLabCsv = useCallback((tradeRows) => {
    const lines = [CSV_COLS.join(",")];
    for (const t of tradeRows) {
      lines.push(CSV_COLS.map((c) => csvEscape(t[c])).join(","));
    }
    return lines.join("\n");
  }, []);   // ── CRYPTO_LAB ── CSV_COLS/csvEscape are module-stable per render

  const downloadRunCsv = useCallback(async (runId) => {
    setCsvMsg({ kind: "info", text: "Preparing CSV…" });
    try {
      let tradeRows = (result && result.run_id === runId) ? result.trades : null;
      if (!tradeRows) {
        const r = await fetch(`${api}/api/backtest/crypto/runs/${runId}`);
        if (!r.ok) { setCsvMsg({ kind: "err", text: `Could not load run (${r.status})` }); return; }
        tradeRows = (await r.json()).trades || [];
      }
      if (!tradeRows.length) { setCsvMsg({ kind: "err", text: "Run has no trades to export." }); return; }
      const csv = buildLabCsv(tradeRows);
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const fname = `crypto_lab_${String(runId).replace(/[^0-9A-Za-z_-]/g, "")}.csv`;
      const a = document.createElement("a");
      a.href = url;
      a.download = fname;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setCsvMsg({ kind: "ok", text: `Downloaded ${tradeRows.length} trades → ${fname}` });
      setTimeout(() => setCsvMsg(null), 6000);
    } catch (e) {
      setCsvMsg({ kind: "err", text: `Export failed: ${String(e.message || e)}` });
    }
  }, [api, result, buildLabCsv]);

  const downloadCsv = useCallback(() => {
    if (!result) return;
    downloadRunCsv(result.run_id);
  }, [result, downloadRunCsv]);

  /* ── derived ── */
  const cJob = corpus?.job, cStats = corpus?.stats;
  const cProg = cJob?.progress;
  const collecting = !!cJob?.running;
  const running = !!run?.running;
  const summary = result?.summary;
  const trades = result?.trades || [];
  const isCondor = params.structure === "condor";
  const isSpread = params.structure === "credit_spread";
  const hasWings = isCondor || isSpread;

  const toggleWd = useCallback((wd) => {
    setParams((p) => {
      const s = new Set(p.weekdays);
      if (s.has(wd)) s.delete(wd); else s.add(wd);
      const n = { ...p, weekdays: [...s].sort() };
      saveParams(n); return n;
    });
  }, []);

  return (
    <div style={{ padding: spacing.lg, maxWidth: 1180, margin: "0 auto",
      display: "flex", flexDirection: "column", gap: 16 }}>

      <div>
        <h1 style={{ ...typography.h1, margin: 0 }}>🪙 Crypto Options Lab</h1>
        <div style={{ fontSize: 12, color: colors.text.tertiary, marginTop: 4 }}>
          BTC daily options (Delta Exchange India) · mark-price backtests · research only —
          fee model is an UNVERIFIED assumption; mark-price fills flatter live results.
        </div>
      </div>

      {err ? <Card style={{ borderColor: colors.danger, color: colors.danger, fontSize: 13 }}>{err}</Card> : null}
      {note ? <Card style={{ borderColor: colors.warning, color: colors.warning, fontSize: 13 }}>{note}</Card> : null}

      {/* ── Corpus panel ── */}
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <div style={{ ...typography.h2, fontSize: 15 }}>Corpus (local candle store)</div>
          <div style={{ fontSize: 11, color: colors.text.tertiary }}>
            {cStats && !cStats.error
              ? `${cStats.expiries ?? 0} expiries · perp ${cStats.perp_from || "—"} → ${cStats.perp_to || "—"} · ${cStats.db_size_mb ?? 0} MB · disk free ${cStats.disk_free_gb ?? "?"} GB`
              : "stats unavailable"}
          </div>
        </div>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
          <Field label="Months back">
            <input type="number" min="1" max="36" style={inputStyle} value={collectForm.months}
              onChange={(e) => setCollectForm((f) => ({ ...f, months: Number(e.target.value) }))} />
          </Field>
          <Field label="Strike span ±%" hint="strikes around entry spot">
            <input type="number" step="0.5" min="1" max="10" style={inputStyle} value={collectForm.span_pct}
              onChange={(e) => setCollectForm((f) => ({ ...f, span_pct: Number(e.target.value) }))} />
          </Field>
          <Field label="Candle window (h)" hint="25=trade window · 48=full life (needed for D-2 / early entries; ~2× disk)">
            <input type="number" min="2" max="48" style={inputStyle} value={collectForm.window_h}
              onChange={(e) => setCollectForm((f) => ({ ...f, window_h: Number(e.target.value) }))} />
          </Field>
          <Field label="Pace (s/req)" hint="raise to 0.6 on HTTP 429s">
            <input type="number" step="0.05" min="0.2" max="2" style={inputStyle} value={collectForm.pace_s}
              onChange={(e) => setCollectForm((f) => ({ ...f, pace_s: Number(e.target.value) }))} />
          </Field>
          <Field label="Include perp">
            <select style={inputStyle} value={collectForm.include_perp ? "1" : "0"}
              onChange={(e) => setCollectForm((f) => ({ ...f, include_perp: e.target.value === "1" }))}>
              <option value="1">Yes (BTCUSD 1m first)</option>
              <option value="0">No (options only)</option>
            </select>
          </Field>
          {!collecting
            ? <Btn primary onClick={startCollect} disabled={running}>Start collection</Btn>
            : <Btn danger onClick={cancelCollect}>Cancel (resumable)</Btn>}
        </div>
        {collecting && cProg ? (
          <div style={{ marginTop: 10 }}>
            <div style={{ height: 8, borderRadius: 4, background: colors.bg.tertiary, overflow: "hidden" }}>
              <div style={{
                height: "100%", background: colors.primary, transition: "width 1s linear",
                width: `${Math.min(100, (100 * (cProg.done || 0)) / Math.max(1, cProg.total || 1)).toFixed(1)}%`,
              }} />
            </div>
            <div style={{ fontSize: 11, color: colors.text.tertiary, marginTop: 4 }}>
              {cProg.phase === "perp"
                ? `perp backfill · day ${cProg.done}/${cProg.total} · ${cProg.rows?.toLocaleString?.() || 0} rows`
                : `options · ${cProg.done}/${cProg.total} · ${cProg.current || ""} · usable ${cProg.usable ?? 0} · skipped ${cProg.skipped ?? 0} · disk free ${cProg.disk_free_gb ?? "?"} GB`}
            </div>
          </div>
        ) : null}
        {cJob?.error ? <div style={{ color: colors.danger, fontSize: 12, marginTop: 8 }}>{cJob.error}</div> : null}
        {cJob?.result && !collecting ? (
          <div style={{ color: colors.text.secondary, fontSize: 12, marginTop: 8 }}>
            Last collection: usable {cJob.result.usable} · skipped {cJob.result.skipped}
            {cJob.result.cancelled ? " · cancelled (resumable — just start again)" : ""}
          </div>
        ) : null}
      </Card>

      {/* ── Config panel ── */}
      <Card>
        <div style={{ ...typography.h2, fontSize: 15, marginBottom: 10 }}>Strategy configuration</div>

        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
          <Field label="Structure">
            <select style={inputStyle} value={params.structure} onChange={(e) => set("structure", e.target.value)}>
              <option value="condor">Iron condor (defined risk)</option>
              <option value="strangle">Short strangle (SL is the ONLY cap)</option>
              <option value="credit_spread">Credit spread — TMA directional (defined risk)</option>
            </select>
          </Field>
          <Field label="Entry day" hint="days before expiry">
            <select style={inputStyle} value={params.entry_days_before}
              onChange={(e) => set("entry_days_before", Number(e.target.value))}>
              <option value={0}>Expiry day (intraday)</option>
              <option value={1}>D-1 (evening carry)</option>
              <option value={2}>D-2 (needs 48h corpus window)</option>
            </select>
          </Field>
          <Field label="Entry time IST">
            <input type="time" style={inputStyle} value={params.entry_hm}
              onChange={(e) => set("entry_hm", e.target.value)} />
          </Field>
          <Field label="Exit time IST" hint="≤ 17:30 settlement">
            <input type="time" style={inputStyle} value={params.exit_hm}
              onChange={(e) => set("exit_hm", e.target.value)} />
          </Field>
        </div>

        {isSpread ? (
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end", marginTop: 12 }}>
            <Field label="Spread signal">
              <select style={inputStyle} value={params.spread_signal}
                onChange={(e) => set("spread_signal", e.target.value)}>
                <option value="tma_trend">Triple-EMA trend (TMA)</option>
                <option value="fixed_put">Always PUT spread (control)</option>
                <option value="fixed_call">Always CALL spread (control)</option>
              </select>
            </Field>
            {params.spread_signal === "tma_trend" ? (
              <>
                <Field label="EMA fast">
                  <input type="number" min="2" max="200" style={inputStyle} value={params.ema_fast}
                    onChange={(e) => set("ema_fast", e.target.value)} />
                </Field>
                <Field label="EMA mid">
                  <input type="number" min="3" max="300" style={inputStyle} value={params.ema_mid}
                    onChange={(e) => set("ema_mid", e.target.value)} />
                </Field>
                <Field label="EMA slow">
                  <input type="number" min="4" max="500" style={inputStyle} value={params.ema_slow}
                    onChange={(e) => set("ema_slow", e.target.value)} />
                </Field>
                <Field label="EMA timeframe (min)" hint="on the BTC perp">
                  <input type="number" min="1" max="240" style={inputStyle} value={params.ema_tf_min}
                    onChange={(e) => set("ema_tf_min", e.target.value)} />
                </Field>
              </>
            ) : null}
            <span style={{ fontSize: 10, color: colors.text.tertiary, maxWidth: 300 }}>
              fast&gt;mid&gt;slow → sell PUT spread · inverted → sell CALL spread ·
              no alignment → day skipped (NO_SIGNAL)
            </span>
          </div>
        ) : null}

        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end", marginTop: 12 }}>
          <Field label="Short selection">
            <select style={inputStyle} value={params.short_mode} onChange={(e) => set("short_mode", e.target.value)}>
              <option value="premium_ratio">Premium ratio × ATM straddle</option>
              <option value="otm_pct">Fixed OTM % of spot</option>
            </select>
          </Field>
          {params.short_mode === "premium_ratio" ? (
            <Field label="Short ratio" hint="0.25 = 25% of straddle">
              <input type="number" step="0.01" min="0.02" max="0.95" style={inputStyle}
                value={params.short_ratio} onChange={(e) => set("short_ratio", e.target.value)} />
            </Field>
          ) : (
            <Field label="Short OTM %" hint="distance from spot">
              <input type="number" step="0.1" min="0.1" max="15" style={inputStyle}
                value={params.short_otm_pct} onChange={(e) => set("short_otm_pct", e.target.value)} />
            </Field>
          )}
          {hasWings ? (
            <>
              <Field label="Wing selection">
                <select style={inputStyle} value={params.wing_mode} onChange={(e) => set("wing_mode", e.target.value)}>
                  <option value="premium_ratio">Premium ratio × short leg</option>
                  <option value="otm_gap_pct">OTM gap % beyond short</option>
                </select>
              </Field>
              {params.wing_mode === "premium_ratio" ? (
                <Field label="Wing ratio" hint="wing ≤ ratio × short prem">
                  <input type="number" step="0.05" min="0.05" max="0.9" style={inputStyle}
                    value={params.wing_prem_ratio} onChange={(e) => set("wing_prem_ratio", e.target.value)} />
                </Field>
              ) : (
                <Field label="Wing gap %" hint="of spot, beyond short">
                  <input type="number" step="0.1" min="0.2" max="10" style={inputStyle}
                    value={params.wing_gap_pct} onChange={(e) => set("wing_gap_pct", e.target.value)} />
                </Field>
              )}
            </>
          ) : null}
        </div>

        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end", marginTop: 12 }}>
          <Field label="SL × credit" hint={hasWings ? "0 = off (wings cap risk)" : "required for strangle"}>
            <input type="number" step="0.1" min="0" max="10" style={inputStyle}
              value={params.sl_mult} onChange={(e) => set("sl_mult", e.target.value)} />
          </Field>
          <Field label="TP × credit" hint="0 = hold to exit">
            <input type="number" step="0.05" min="0" max="1" style={inputStyle}
              value={params.tp_ratio} onChange={(e) => set("tp_ratio", e.target.value)} />
          </Field>
          <Field label="Contracts" hint="× 0.001 BTC">
            <input type="number" min="1" max="10000" style={inputStyle}
              value={params.contracts} onChange={(e) => set("contracts", e.target.value)} />
          </Field>
          <Field label="Fee mult" hint="0 = gross">
            <input type="number" step="0.1" min="0" max="5" style={inputStyle}
              value={params.fee_mult} onChange={(e) => set("fee_mult", e.target.value)} />
          </Field>
          <Field label="GST % on fees" hint="verify: usually 18">
            <input type="number" step="1" min="0" max="30" style={inputStyle}
              value={params.gst_pct} onChange={(e) => set("gst_pct", e.target.value)} />
          </Field>
          <Field label="Margin buffer %" hint="on the margin estimate">
            <input type="number" step="1" min="0" max="100" style={inputStyle}
              value={params.margin_buffer_pct} onChange={(e) => set("margin_buffer_pct", e.target.value)} />
          </Field>
          {!hasWings ? (
            <Field label="Margin shock %" hint="strangle scenario move">
              <input type="number" step="1" min="1" max="50" style={inputStyle}
                value={params.margin_shock_pct} onChange={(e) => set("margin_shock_pct", e.target.value)} />
            </Field>
          ) : null}
        </div>

        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end", marginTop: 12 }}>
          <Field label="From (expiry date)">
            <input type="date" style={inputStyle} value={params.date_from}
              onChange={(e) => set("date_from", e.target.value)} />
          </Field>
          <Field label="To (expiry date)">
            <input type="date" style={inputStyle} value={params.date_to}
              onChange={(e) => set("date_to", e.target.value)} />
          </Field>
          <Field label="Expiry weekdays">
            <div style={{ display: "flex", gap: 6 }}>
              {WD.map((w, i) => (
                <button key={w} onClick={() => toggleWd(i)} style={{
                  padding: "6px 8px", borderRadius: 6, fontSize: 11, fontWeight: 600,
                  cursor: "pointer",
                  border: `1px solid ${params.weekdays.includes(i) ? colors.primary : colors.border.light}`,
                  background: params.weekdays.includes(i) ? colors.primaryBg : colors.bg.tertiary,
                  color: params.weekdays.includes(i) ? colors.primary : colors.text.tertiary,
                }}>{w}</button>
              ))}
            </div>
          </Field>
          <Field label="Exclude dates" hint="DDMMYY, comma/space separated (e.g. FOMC days)">
            <input type="text" style={{ ...inputStyle, minWidth: 220 }} placeholder="170925 291025 …"
              value={params.exclude_dates_text} onChange={(e) => set("exclude_dates_text", e.target.value)} />
          </Field>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 16 }}>
          {!running
            ? <Btn primary onClick={startRun}>Run backtest</Btn>
            : <Btn danger onClick={cancelRun}>Cancel run</Btn>}
          {running && run?.progress ? (
            <span style={{ fontSize: 12, color: colors.text.tertiary }}>
              simulating… {run.progress.done}/{run.progress.total} expiries
            </span>
          ) : null}
          {collecting ? (
            <span style={{ fontSize: 11, color: colors.warning }}>
              corpus collection active — runs now are non-authoritative
            </span>
          ) : null}
        </div>
        {run?.error ? <div style={{ color: colors.danger, fontSize: 12, marginTop: 8 }}>{run.error}</div> : null}
      </Card>

      {/* ── Results ── */}
      {summary ? (
        <Card>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <div style={{ ...typography.h2, fontSize: 15 }}>
              Results · <span style={{ color: colors.text.tertiary, fontSize: 12 }}>{result.run_id}</span>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              {csvMsg ? (
                <span style={{ fontSize: 11,
                  color: csvMsg.kind === "ok" ? colors.success
                    : csvMsg.kind === "err" ? colors.danger : colors.text.tertiary }}>
                  {csvMsg.text}
                </span>
              ) : null}
              <Btn small onClick={downloadCsv}>Download CSV</Btn>
            </div>
          </div>

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <StatCard label="Net USD" value={usd(summary.net_usd)}
              tone={summary.net_usd >= 0 ? "good" : "bad"}
              sub={`gross ${usd(summary.gross_usd)} · fees ${usd(summary.fees_usd)}`} />
            <StatCard label="Win rate" value={`${(summary.win_rate * 100).toFixed(1)}%`}
              sub={`${summary.traded} trades · ${summary.skipped} skipped`} />
            <StatCard label="Avg credit" value={summary.avg_credit} sub="per 1 BTC" />
            <StatCard label="Max DD" value={usd(summary.max_dd_usd)} tone="bad" sub="net, sequential" />
            <StatCard label="Worst day"
              value={summary.worst_day ? usd(summary.worst_day.usd_net) : "—"}
              tone="bad"
              sub={summary.worst_day ? `${summary.worst_day.expiry} (${summary.worst_day.exit_reason})` : ""} />
            <StatCard label="Peak margin (est.)"
              value={summary.peak_margin_usd !== undefined ? usd(summary.peak_margin_usd) : "—"}
              sub={summary.avg_margin_usd !== undefined ? `avg ${usd(summary.avg_margin_usd)} · estimate` : "estimate"} />
            <StatCard label="Return on margin"
              value={summary.ret_on_peak_margin_pct !== undefined ? `${summary.ret_on_peak_margin_pct}%` : "—"}
              tone={(summary.ret_on_peak_margin_pct ?? 0) >= 0 ? "good" : "bad"}
              sub="net ÷ peak margin" />
          </div>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10, fontSize: 11 }}>
            {Object.entries(summary.sides || {}).map(([k, v]) => (
              <span key={`side-${k}`} style={{
                padding: "3px 8px", borderRadius: 10, background: colors.bg.tertiary,
                border: `1px solid ${colors.border.light}`, color: colors.text.secondary,
              }}>side {k}: {v}</span>
            ))}
            {Object.entries(summary.exits || {}).map(([k, v]) => (
              <span key={k} style={{
                padding: "3px 8px", borderRadius: 10, background: colors.bg.tertiary,
                border: `1px solid ${colors.border.light}`, color: colors.text.secondary,
              }}>exit {k}: {v}</span>
            ))}
            {Object.entries(summary.skips || {}).map(([k, v]) => (
              <span key={k} style={{
                padding: "3px 8px", borderRadius: 10, background: "rgba(245,158,11,0.10)",
                border: `1px solid rgba(245,158,11,0.4)`, color: colors.warning,
              }}>skip {k}: {v}</span>
            ))}
          </div>

          <div style={{ marginTop: 14 }}>
            <EquityCurve trades={trades} />
          </div>

          <div style={{ marginTop: 14, maxHeight: 460, overflow: "auto",
            border: `1px solid ${colors.border.light}`, borderRadius: 6 }}>
            <table style={{ minWidth: 1280, width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
              <thead>
                <tr style={{ position: "sticky", top: 0, background: colors.bg.tertiary, zIndex: 1 }}>
                  {["Expiry", "Entry (IST)", "Exit (IST)", "Hold", "Spot",
                    "Short Call", "Short Put", "Wing Call", "Wing Put",
                    "Credit", "Exit debit", "SL @", "TP @", "Exit",
                    "MFE", "MAE", "Margin $", "Gross $", "Fees $", "Net $"].map((h) => (
                    <th key={h} style={{ textAlign: "right", padding: "6px 8px",
                      color: colors.text.muted, fontWeight: 600, whiteSpace: "nowrap" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.slice(0, 400).map((t) => {
                  const leg = (k, pIn, pOut) => (t[k] === null || t[k] === undefined)
                    ? <span style={{ color: colors.text.tertiary }}>—</span>
                    : (
                      <span>
                        <span style={{ fontWeight: 600 }}>{t[k]}</span>
                        {t[pIn] !== undefined && t[pIn] !== null ? (
                          <span style={{ color: colors.text.tertiary }}>
                            {" "}@ {t[pIn]}{t[pOut] !== undefined && t[pOut] !== null ? `→${t[pOut]}` : ""}
                          </span>
                        ) : null}
                      </span>
                    );
                  return (
                    <tr key={t.expiry} style={{ borderTop: `1px solid ${colors.border.light}` }}>
                      <td style={{ padding: "4px 8px", textAlign: "right" }}>{t.expiry}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right", whiteSpace: "nowrap",
                        color: colors.text.secondary }}>{t.entry_ist ?? "—"}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right", whiteSpace: "nowrap",
                        color: colors.text.secondary }}>{t.exit_ist ?? "—"}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right" }}>{t.hold_min}m</td>
                      <td style={{ padding: "4px 8px", textAlign: "right" }}>{t.spot?.toLocaleString?.()}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right", whiteSpace: "nowrap" }}>
                        {leg("sc", "sc_prem", "sc_xprem")}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right", whiteSpace: "nowrap" }}>
                        {leg("sp", "sp_prem", "sp_xprem")}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right", whiteSpace: "nowrap" }}>
                        {leg("wc", "wc_prem", "wc_xprem")}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right", whiteSpace: "nowrap" }}>
                        {leg("wp", "wp_prem", "wp_xprem")}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right" }}>{t.credit}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right" }}>{t.exit_debit ?? "—"}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right", color: colors.text.tertiary }}>
                        {t.sl_level ?? "—"}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right", color: colors.text.tertiary }}>
                        {t.tp_level ?? "—"}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right", fontWeight: 600,
                        color: t.exit_reason === "SL" ? colors.danger
                          : t.exit_reason === "TP" ? colors.success : colors.text.secondary }}>
                        {t.exit_reason}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right", color: colors.success }}>
                        {t.best_unit ?? "—"}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right", color: colors.danger }}>
                        {t.worst_unit}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right",
                        color: colors.text.secondary }}>
                        {t.margin_usd !== undefined ? usd(t.margin_usd) : "—"}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right" }}>{usd(t.usd_gross)}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right",
                        color: colors.text.tertiary }}>{usd(t.usd_fees)}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right", fontWeight: 700,
                        color: t.usd_net >= 0 ? colors.success : colors.danger }}>{usd(t.usd_net)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {trades.length > 400 ? (
              <div style={{ padding: 8, fontSize: 11, color: colors.text.tertiary }}>
                showing first 400 of {trades.length} — full set in the CSV
              </div>
            ) : null}
          </div>
          <div style={{ fontSize: 10, color: colors.text.tertiary, marginTop: 6 }}>
            Legs read “strike @ entry→exit premium” (mark price, per 1 BTC). SL/TP @ are the
            trigger levels in credit units. MFE/MAE = best/worst open P&amp;L during the hold.
            Margin $ is an ESTIMATE (condor: max wing width − credit; strangle: ±shock scenario;
            + buffer) — the exchange's number at order time is authoritative.
            Older saved runs show “—” for fields added later.
          </div>
        </Card>
      ) : null}

      {/* ── Run history ── */}
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <div style={{ ...typography.h2, fontSize: 15 }}>Run history</div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {csvMsg ? (
              <span style={{ fontSize: 11,
                color: csvMsg.kind === "ok" ? colors.success
                  : csvMsg.kind === "err" ? colors.danger : colors.text.tertiary }}>
                {csvMsg.text}
              </span>
            ) : null}
            <Btn small onClick={loadRuns}>Refresh</Btn>
          </div>
        </div>
        {runsList.length === 0 ? (
          <div style={{ fontSize: 12, color: colors.text.tertiary }}>No saved runs yet.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {runsList.map((r) => (
              <div key={r.run_id} style={{
                display: "flex", justifyContent: "space-between", alignItems: "center",
                padding: "8px 10px", borderRadius: 6, background: colors.bg.tertiary,
                border: `1px solid ${colors.border.light}`, fontSize: 12,
              }}>
                <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                  <span style={{ color: colors.text.tertiary }}>{r.run_id}</span>
                  <span>{r.params.structure === "credit_spread"
                    ? `spread/${(r.params.spread_signal || "").replace("tma_trend", "TMA")}`
                    : r.params.structure}</span>
                  <span style={{ color: colors.text.tertiary }}>
                    {r.params.short_mode === "otm_pct"
                      ? `OTM ${r.params.short_otm_pct}%`
                      : `ratio ${r.params.short_ratio}`}
                    {" · "}entry D-{r.params.entry_days_before} {r.params.entry_hm}
                    {" · "}SL {r.params.sl_mult}× · TP {r.params.tp_ratio}×
                  </span>
                  <span style={{ fontWeight: 700,
                    color: (r.summary.net_usd ?? 0) >= 0 ? colors.success : colors.danger }}>
                    {usd(r.summary.net_usd)}
                  </span>
                  <span style={{ color: colors.text.tertiary }}>
                    {r.summary.traded} trades · win {(100 * (r.summary.win_rate || 0)).toFixed(0)}%
                  </span>
                </div>
                <div style={{ display: "flex", gap: 6 }}>
                  <Btn small onClick={() => downloadRunCsv(r.run_id)}>CSV</Btn>
                  <Btn small onClick={() => loadRun(r.run_id)}>Load</Btn>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}