// frontend/src/pages/CryptoStfcLab.jsx
//
// ── CRYPTO_STFC_20260907 ── STFC (SuperTrend Flip-Confirm) lab on the
// BTCUSD perp corpus. Rendered inside CryptoLab.jsx via the lab tab switch.
//
//   * Corpus panel — pull BTCUSD 1m candles for a date window (IST days),
//     coverage check (missing / thin days), cancel (resumable, day-level).
//   * Config panel — timeframe, SuperTrend len/mult, SL $, spread $, taker
//     fee %/side, size (BTC), sides, date range; optional GRID lists for
//     tf / len / mult / SL (comma separated) → scoreboard.
//   * Results — scoreboard ranked by the house priority (yearly signs →
//     worst month → consec losing months → positive months → DD → net),
//     cell detail with yearly sign strip, monthly grid, equity curve and
//     trades (primary cell only carries trades), client-side CSV.
//
// Backend is the source of truth for jobs/runs (rehydrated on mount).
// No window.confirm/alert (blocked in Tauri webview).

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getApiBase } from "../api/base";
import { colors, spacing, typography } from "../tokens";

const LS_KEY = "crypto_stfc_params_v1";

function isoDaysAgo(n) {
  const d = new Date(Date.now() - n * 86400000);
  return d.toISOString().slice(0, 10);
}

const DEFAULT_PARAMS = {
  tf_min: 3, st_len: 10, st_mult: 2.0, sl_usd: 0, spread_usd: 0,
  fee_pct_side: 0.05, size_btc: 1, trade_long: true, trade_short: true,
  date_from: isoDaysAgo(365), date_to: isoDaysAgo(0),
  grid_on: false, tf_text: "3,5,15", len_text: "7,10,14",
  mult_text: "1.5,2,3", sl_text: "0,100,200",
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
function parseList(txt, isFloat) {
  return String(txt || "").split(/[,\s]+/).map((s) => s.trim()).filter(Boolean)
    .map((s) => (isFloat ? parseFloat(s) : parseInt(s, 10))).filter((v) => !Number.isNaN(v));
}

/* ── tiny local UI kit (mirrors CryptoLab.jsx; not exported there) ── */
function Card({ children, style }) {
  return (
    <div style={{
      background: colors.bg.secondary, border: `1px solid ${colors.border.light}`,
      borderRadius: 8, padding: 16, boxShadow: "0 1px 3px var(--c-shadow)", ...style,
    }}>{children}</div>
  );
}
function Field({ label, children, hint, w }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: w || 110 }}>
      <span style={{ ...typography.label, color: colors.text.muted, fontSize: 11 }}>{label}</span>
      {children}
      {hint ? <span style={{ fontSize: 10, color: colors.text.tertiary }}>{hint}</span> : null}
    </label>
  );
}
const inputStyle = {
  padding: "7px 10px", borderRadius: 6, border: `1px solid ${colors.border.light}`,
  background: colors.bg.secondary, color: colors.text.primary, fontSize: 13,
  outline: "none", fontFamily: "var(--c-font-ui)",
};
function Btn({ children, onClick, disabled, danger, primary, small }) {
  return (
    <button onClick={onClick} disabled={disabled} style={{
      padding: small ? "5px 10px" : "8px 16px", borderRadius: 6,
      fontSize: small ? 12 : 13, fontWeight: 600,
      cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.5 : 1,
      border: `1px solid ${danger ? colors.danger : primary ? colors.primary : colors.border.light}`,
      background: danger ? "rgba(239,68,68,0.12)" : primary ? colors.primaryBg : colors.bg.tertiary,
      color: danger ? colors.danger : primary ? colors.primary : colors.text.primary,
    }}>{children}</button>
  );
}
function StatCard({ label, value, sub, tone }) {
  const col = tone === "good" ? colors.success : tone === "bad" ? colors.danger
    : tone === "warn" ? colors.warning : colors.text.primary;
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
function usd(v, d = 2) {
  if (v === null || v === undefined) return "—";
  const s = v < 0 ? "-" : "";
  return `${s}$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: d })}`;
}
const pnlCol = (v) => (v > 0 ? colors.success : v < 0 ? colors.danger : colors.text.primary);
const pct = (v) => `${(100 * (v || 0)).toFixed(1)}%`;

function EquityCurve({ trades }) {
  const pts = useMemo(() => { let eq = 0; return trades.map((t) => { eq += t.net; return eq; }); }, [trades]);
  if (!pts.length) return null;
  const W = 860, H = 220, PAD = 34;
  const min = Math.min(0, ...pts), max = Math.max(0, ...pts);
  const span = max - min || 1;
  const x = (i) => PAD + (i / Math.max(1, pts.length - 1)) * (W - 2 * PAD);
  const y = (v) => H - PAD - ((v - min) / span) * (H - 2 * PAD);
  const path = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }}>
      <line x1={PAD} y1={y(0)} x2={W - PAD} y2={y(0)} stroke={colors.border.light} strokeDasharray="4 4" />
      <path d={path} fill="none" stroke={pnlCol(last)} strokeWidth="1.8" />
      <text x={PAD} y={14} fill={colors.text.muted} fontSize="10">cumulative net USD · primary cell · {trades.length} trades</text>
      <text x={W - PAD} y={14} fill={pnlCol(last)} fontSize="11" textAnchor="end" fontWeight="700">{usd(last)}</text>
    </svg>
  );
}

/* yearly sign strip */
function YearStrip({ years }) {
  if (!years?.length) return null;
  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
      {years.map((y) => (
        <div key={y.year} style={{
          padding: "6px 10px", borderRadius: 6, minWidth: 84, textAlign: "center",
          background: y.net > 0 ? colors.successBg : colors.dangerBg,
          border: `1px solid ${y.net > 0 ? colors.success : colors.danger}`,
        }}>
          <div style={{ fontSize: 11, color: colors.text.muted }}>{y.year}</div>
          <div style={{ fontSize: 13, fontWeight: 700, color: pnlCol(y.net) }}>{usd(y.net, 0)}</div>
        </div>
      ))}
    </div>
  );
}

/* monthly grid: rows = years, cols = Jan..Dec */
function MonthGrid({ months }) {
  const byYear = useMemo(() => {
    const m = {};
    (months || []).forEach((r) => { const [y, mo] = r.month.split("-"); (m[y] = m[y] || {})[parseInt(mo, 10)] = r.net; });
    return m;
  }, [months]);
  const years = Object.keys(byYear).sort();
  if (!years.length) return null;
  const maxAbs = Math.max(1, ...(months || []).map((r) => Math.abs(r.net)));
  const cell = (v) => {
    if (v === undefined) return { background: "transparent", color: colors.text.tertiary };
    const a = Math.min(0.85, 0.15 + 0.7 * (Math.abs(v) / maxAbs));
    return { background: v >= 0 ? `rgba(34,197,94,${a})` : `rgba(239,68,68,${a})`, color: "#fff" };
  };
  return (
    <table style={{ borderCollapse: "collapse", fontSize: 11, width: "100%" }}>
      <thead><tr>
        <th style={{ textAlign: "left", padding: 4, color: colors.text.muted }}>Year</th>
        {["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"].map((m) =>
          <th key={m} style={{ padding: 4, color: colors.text.muted, fontWeight: 500 }}>{m}</th>)}
        <th style={{ padding: 4, color: colors.text.muted }}>Year</th>
      </tr></thead>
      <tbody>{years.map((y) => {
        const tot = Object.values(byYear[y]).reduce((a, b) => a + b, 0);
        return (
          <tr key={y}>
            <td style={{ padding: 4, fontWeight: 700 }}>{y}</td>
            {Array.from({ length: 12 }, (_, i) => i + 1).map((mo) => {
              const v = byYear[y][mo];
              return <td key={mo} style={{ padding: "5px 4px", textAlign: "center", borderRadius: 3, ...cell(v) }}>
                {v === undefined ? "·" : usd(v, 0)}</td>;
            })}
            <td style={{ padding: 4, textAlign: "right", fontWeight: 700, color: pnlCol(tot) }}>{usd(tot, 0)}</td>
          </tr>
        );
      })}</tbody>
    </table>
  );
}

const cellLabel = (c) => `${c.tf_min}m · ST(${c.st_len},${c.st_mult}) · SL ${c.sl_usd > 0 ? "$" + c.sl_usd : "off"}`;

/* ─────────────────────────────────────────────────────────────────── */
export default function CryptoStfcLab() {
  const api = getApiBase();
  const [params, setParams] = useState(loadParams);
  const [pull, setPull] = useState({ date_from: isoDaysAgo(365), date_to: isoDaysAgo(0), pace_s: 0.35, force: false });
  const [cJob, setCJob] = useState(null);
  const [cStats, setCStats] = useState(null);
  const [cov, setCov] = useState(null);
  const [rJob, setRJob] = useState(null);
  const [run, setRun] = useState(null);          // loaded run detail
  const [selCell, setSelCell] = useState(0);
  const [runs, setRuns] = useState([]);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const pollRef = useRef(null);
  const lastRunId = useRef(null);

  const upd = (k, v) => setParams((p) => { const n = { ...p, [k]: v }; saveParams(n); return n; });

  const refreshRuns = useCallback(async () => {
    try {
      const j = await fetch(`${api}/api/backtest/crypto/runs?limit=30&strategy=STFC`).then((r) => r.json());
      setRuns(j.runs || []);
    } catch { /* ignore */ }
  }, [api]);

  const loadRun = useCallback(async (runId) => {
    try {
      const r = await fetch(`${api}/api/backtest/crypto/runs/${runId}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setRun(j); setSelCell(0); setErr("");
    } catch (e) { setErr(`load run: ${e.message}`); }
  }, [api]);

  const poll = useCallback(async () => {
    try {
      const [c, r] = await Promise.all([
        fetch(`${api}/api/backtest/crypto/corpus/status`).then((x) => x.json()),
        fetch(`${api}/api/backtest/crypto/run/status`).then((x) => x.json()),
      ]);
      setCJob(c.job); setCStats(c.stats); setRJob(r);
      if (!r.running && r.run_id && r.run_id !== lastRunId.current) {
        lastRunId.current = r.run_id;
        if (r.result?.params?.strategy === "STFC") { setRun(r.result); setSelCell(0); refreshRuns(); }
      }
      if (r.error) setErr(`run: ${r.error}`);
      if (c.job?.error) setErr(`corpus: ${c.job.error}`);
    } catch { /* offline — keep last state */ }
  }, [api, refreshRuns]);

  useEffect(() => {
    poll(); refreshRuns();
    pollRef.current = setInterval(poll, 2000);
    return () => clearInterval(pollRef.current);
  }, [poll, refreshRuns]);

  const collecting = !!cJob?.running;
  const running = !!rJob?.running;

  const checkCoverage = async () => {
    try {
      const j = await fetch(`${api}/api/backtest/crypto/perp/coverage?date_from=${pull.date_from}&date_to=${pull.date_to}`).then((r) => r.json());
      setCov(j); setErr("");
    } catch (e) { setErr(`coverage: ${e.message}`); }
  };
  const startPull = async () => {
    setErr(""); setNote("");
    try {
      const r = await fetch(`${api}/api/backtest/crypto/perp/pull/start`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(pull) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
      poll();
    } catch (e) { setErr(`pull: ${e.message}`); }
  };
  const cancelPull = async () => {
    try { await fetch(`${api}/api/backtest/crypto/corpus/backfill/cancel`, { method: "POST" }); poll(); }
    catch (e) { setErr(`cancel: ${e.message}`); }
  };

  const buildBody = () => {
    const b = {
      tf_min: Number(params.tf_min), st_len: Number(params.st_len), st_mult: Number(params.st_mult),
      sl_usd: Number(params.sl_usd), spread_usd: Number(params.spread_usd),
      fee_pct_side: Number(params.fee_pct_side), size_btc: Number(params.size_btc),
      trade_long: !!params.trade_long, trade_short: !!params.trade_short,
      date_from: params.date_from || "", date_to: params.date_to || "",
      tf_list: [], st_len_list: [], st_mult_list: [], sl_list: [],
    };
    if (params.grid_on) {
      b.tf_list = parseList(params.tf_text, false);
      b.st_len_list = parseList(params.len_text, false);
      b.st_mult_list = parseList(params.mult_text, true);
      b.sl_list = parseList(params.sl_text, true);
    }
    return b;
  };
  const gridCells = useMemo(() => {
    if (!params.grid_on) return 1;
    const n = (t, f) => Math.max(1, parseList(t, f).length);
    return n(params.tf_text) * n(params.len_text) * n(params.mult_text, true) * n(params.sl_text, true);
  }, [params]);

  const startRun = async () => {
    setErr(""); setNote("");
    try {
      const r = await fetch(`${api}/api/backtest/crypto/stfc/run/start`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(buildBody()) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
      if (j.corpus_busy) setNote("Corpus pull is running — results are non-authoritative until it finishes.");
      poll();
    } catch (e) { setErr(`run: ${e.message}`); }
  };
  const cancelRun = async () => {
    try { await fetch(`${api}/api/backtest/crypto/run/cancel`, { method: "POST" }); poll(); }
    catch (e) { setErr(`cancel: ${e.message}`); }
  };

  /* client-side CSV (Backtest.jsx convention — blob anchor, no window.open) */
  const downloadCsv = () => {
    if (!run?.trades?.length) return;
    const cols = ["ts_ist", "exit_ts_ist", "dir", "entry", "exit", "high", "low", "sl_price", "sl_hit", "move", "gross", "spread", "fees", "net"];
    const ist = (ts) => new Date(ts * 1000).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour12: false });
    const rows = run.trades.map((t) => ({ ...t, ts_ist: ist(t.ts), exit_ts_ist: ist(t.exit_ts), dir: t.dir === 1 ? "LONG" : "SHORT" }));
    const esc = (v) => (v === null || v === undefined ? "" : `"${String(v).replace(/"/g, '""')}"`);
    const csv = [cols.join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = `stfc_${run.run_id.slice(0, 8)}.csv`;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    setNote(`CSV saved: stfc_${run.run_id.slice(0, 8)}.csv (${rows.length} trades)`);
  };

  const cells = run?.summary?.cells || [];
  const ranked = useMemo(() => [...cells].sort((a, b) => (a.rank || 0) - (b.rank || 0)), [cells]);
  const cell = cells[selCell] || null;
  const isPrimary = selCell === 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {err ? <Card style={{ borderColor: colors.danger, color: colors.danger, fontSize: 13 }}>{err}</Card> : null}
      {note ? <Card style={{ borderColor: colors.warning, color: colors.warning, fontSize: 13 }}>{note}</Card> : null}

      {/* ── Corpus: perp window pull ── */}
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <div style={{ ...typography.h2, fontSize: 15 }}>BTCUSD perp corpus (1m candles)</div>
          <div style={{ fontSize: 11, color: colors.text.tertiary }}>
            {cStats && !cStats.error
              ? `${(cStats.perp_rows || 0).toLocaleString()} rows · ${cStats.perp_from || "—"} → ${cStats.perp_to || "—"} · ${cStats.db_size_mb ?? 0} MB · disk free ${cStats.disk_free_gb ?? "?"} GB`
              : "stats unavailable"}
          </div>
        </div>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
          <Field label="From (IST date)">
            <input type="date" style={inputStyle} value={pull.date_from} onChange={(e) => setPull((f) => ({ ...f, date_from: e.target.value }))} />
          </Field>
          <Field label="To (IST date)">
            <input type="date" style={inputStyle} value={pull.date_to} onChange={(e) => setPull((f) => ({ ...f, date_to: e.target.value }))} />
          </Field>
          <Field label="Pace (s/req)" hint="raise on HTTP 429s">
            <input type="number" step="0.05" min="0.2" max="2" style={inputStyle} value={pull.pace_s} onChange={(e) => setPull((f) => ({ ...f, pace_s: Number(e.target.value) }))} />
          </Field>
          <Field label="Re-fetch full days" hint="normally off: complete days are skipped">
            <select style={inputStyle} value={pull.force ? "1" : "0"} onChange={(e) => setPull((f) => ({ ...f, force: e.target.value === "1" }))}>
              <option value="0">No (resume)</option><option value="1">Yes (force)</option>
            </select>
          </Field>
          <Btn onClick={checkCoverage} disabled={collecting}>Check coverage</Btn>
          {!collecting
            ? <Btn primary onClick={startPull} disabled={running}>Pull window</Btn>
            : <Btn danger onClick={cancelPull}>Cancel (resumable)</Btn>}
          <Btn small onClick={() => { const n = { ...pull, date_from: params.date_from, date_to: params.date_to }; setPull(n); }}>
            ← use backtest range
          </Btn>
        </div>
        {collecting && cJob?.progress ? (
          <div style={{ marginTop: 10 }}>
            <div style={{ height: 8, borderRadius: 4, background: colors.bg.tertiary, overflow: "hidden" }}>
              <div style={{ height: "100%", background: colors.primary, transition: "width 1s linear",
                width: `${Math.min(100, (100 * (cJob.progress.done || 0)) / Math.max(1, cJob.progress.total || 1)).toFixed(1)}%` }} />
            </div>
            <div style={{ fontSize: 11, color: colors.text.tertiary, marginTop: 4 }}>
              {cJob.progress.phase} · day {cJob.progress.done}/{cJob.progress.total} · {cJob.progress.current || ""} · {(cJob.progress.rows || 0).toLocaleString()} rows inserted · {cJob.progress.skipped || 0} skipped · {cJob.progress.failed || 0} failed
            </div>
          </div>
        ) : null}
        {!collecting && cJob?.result?.kind === "perp_pull" ? (
          <div style={{ fontSize: 11, color: colors.text.tertiary, marginTop: 8 }}>
            last pull: {cJob.result.done}/{cJob.result.total} days · {(cJob.result.rows || 0).toLocaleString()} rows · {cJob.result.skipped} skipped · {cJob.result.failed} failed{cJob.result.cancelled ? " · cancelled" : ""}
          </div>
        ) : null}
        {cov ? (
          <div style={{ fontSize: 12, marginTop: 8, color: colors.text.secondary }}>
            <b>Coverage</b> {pull.date_from} → {pull.date_to}: {cov.rows?.toLocaleString()} rows · {cov.days_with_data} days with data ·{" "}
            <span style={{ color: cov.missing_count ? colors.danger : colors.success }}>{cov.missing_count} missing</span> ·{" "}
            <span style={{ color: cov.thin_count ? colors.warning : colors.success }}>{cov.thin_count} thin (&lt;1380 rows)</span>
            {cov.missing_count ? <div style={{ fontSize: 11, color: colors.text.tertiary, marginTop: 2 }}>missing: {cov.missing_days.join(", ")}{cov.missing_count > cov.missing_days.length ? " …" : ""}</div> : null}
          </div>
        ) : null}
      </Card>

      {/* ── Config ── */}
      <Card>
        <div style={{ ...typography.h2, fontSize: 15, marginBottom: 10 }}>STFC config</div>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
          <Field label="Timeframe (min)"><input type="number" min="1" max="240" style={inputStyle} value={params.tf_min} onChange={(e) => upd("tf_min", e.target.value)} /></Field>
          <Field label="ST length"><input type="number" min="2" max="200" style={inputStyle} value={params.st_len} onChange={(e) => upd("st_len", e.target.value)} /></Field>
          <Field label="ST factor"><input type="number" step="0.1" min="0.1" style={inputStyle} value={params.st_mult} onChange={(e) => upd("st_mult", e.target.value)} /></Field>
          <Field label="Stop loss ($)" hint="0 = off · walked on 1m candles"><input type="number" min="0" step="10" style={inputStyle} value={params.sl_usd} onChange={(e) => upd("sl_usd", e.target.value)} /></Field>
          <Field label="Spread ($/trade)"><input type="number" min="0" step="1" style={inputStyle} value={params.spread_usd} onChange={(e) => upd("spread_usd", e.target.value)} /></Field>
          <Field label="Taker fee %/side" hint="Delta ≈ 0.05"><input type="number" min="0" step="0.01" style={inputStyle} value={params.fee_pct_side} onChange={(e) => upd("fee_pct_side", e.target.value)} /></Field>
          <Field label="Size (BTC)" hint="P/L in USD for this size"><input type="number" min="0.001" step="0.001" style={inputStyle} value={params.size_btc} onChange={(e) => upd("size_btc", e.target.value)} /></Field>
          <Field label="Sides">
            <select style={inputStyle} value={params.trade_long && params.trade_short ? "both" : params.trade_long ? "long" : "short"}
              onChange={(e) => { const v = e.target.value; upd("trade_long", v !== "short"); upd("trade_short", v !== "long"); }}>
              <option value="both">Long + Short</option><option value="long">Long only</option><option value="short">Short only</option>
            </select>
          </Field>
          <Field label="From"><input type="date" style={inputStyle} value={params.date_from} onChange={(e) => upd("date_from", e.target.value)} /></Field>
          <Field label="To"><input type="date" style={inputStyle} value={params.date_to} onChange={(e) => upd("date_to", e.target.value)} /></Field>
        </div>

        <div style={{ marginTop: 12, paddingTop: 10, borderTop: `1px dashed ${colors.border.light}` }}>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer" }}>
            <input type="checkbox" checked={!!params.grid_on} onChange={(e) => upd("grid_on", e.target.checked)} />
            <b>Grid sweep</b> <span style={{ color: colors.text.tertiary, fontSize: 11 }}>comma-separated lists; empty list falls back to the single value above · {gridCells} cell{gridCells === 1 ? "" : "s"} (max 400)</span>
          </label>
          {params.grid_on ? (
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 8 }}>
              <Field label="Timeframes" w={150}><input style={inputStyle} value={params.tf_text} onChange={(e) => upd("tf_text", e.target.value)} /></Field>
              <Field label="ST lengths" w={150}><input style={inputStyle} value={params.len_text} onChange={(e) => upd("len_text", e.target.value)} /></Field>
              <Field label="ST factors" w={150}><input style={inputStyle} value={params.mult_text} onChange={(e) => upd("mult_text", e.target.value)} /></Field>
              <Field label="Stop losses ($)" w={150}><input style={inputStyle} value={params.sl_text} onChange={(e) => upd("sl_text", e.target.value)} /></Field>
            </div>
          ) : null}
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 12 }}>
          {!running
            ? <Btn primary onClick={startRun} disabled={collecting}>Run backtest{gridCells > 1 ? ` (${gridCells} cells)` : ""}</Btn>
            : <Btn danger onClick={cancelRun}>Cancel run</Btn>}
          {running && rJob?.progress ? (
            <span style={{ fontSize: 12, color: colors.text.tertiary }}>
              cell {rJob.progress.done}/{rJob.progress.total} · {rJob.progress.cell ? cellLabel(rJob.progress.cell) : ""} · {rJob.progress.trades} trades
            </span>
          ) : null}
          {collecting ? <span style={{ fontSize: 12, color: colors.warning }}>corpus pull running — wait for it before running</span> : null}
        </div>
      </Card>

      {/* ── Scoreboard (grid runs) ── */}
      {run && cells.length > 1 ? (
        <Card>
          <div style={{ ...typography.h2, fontSize: 15, marginBottom: 6 }}>Scoreboard · {cells.length} cells</div>
          <div style={{ fontSize: 11, color: colors.text.tertiary, marginBottom: 8 }}>
            ranked: yearly signs → worst month → consec. losing months → positive months → drawdown → net. Click a row for detail. Only cell #1 (first in grid order) carries trades/equity.
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12 }}>
              <thead><tr style={{ color: colors.text.muted }}>
                {["#", "Cell", "Trades", "Win%", "Net", "Gross", "Costs", "MaxDD", "Net/DD", "Yrs +", "Worst mo", "Neg mo run", "Mo +", "SL hits"].map((h) =>
                  <th key={h} style={{ textAlign: h === "Cell" ? "left" : "right", padding: "4px 6px", borderBottom: `1px solid ${colors.border.light}`, whiteSpace: "nowrap" }}>{h}</th>)}
              </tr></thead>
              <tbody>{ranked.map((c) => {
                const idx = cells.indexOf(c);
                const sel = idx === selCell;
                const td = (v, col, left) => <td style={{ padding: "4px 6px", textAlign: left ? "left" : "right", color: col || colors.text.primary, whiteSpace: "nowrap" }}>{v}</td>;
                return (
                  <tr key={idx} onClick={() => setSelCell(idx)} style={{ cursor: "pointer", background: sel ? colors.primaryBg : "transparent", borderBottom: `1px solid ${colors.border.light}` }}>
                    {td(c.rank)}{td(cellLabel(c.cell) + (idx === 0 ? "  ★" : ""), null, true)}{td(c.trades)}{td(pct(c.win_rate))}
                    {td(usd(c.net, 0), pnlCol(c.net))}{td(usd(c.gross, 0), pnlCol(c.gross))}{td(usd(-(c.spread + c.fees), 0), colors.warning)}
                    {td(usd(-c.max_dd, 0), colors.danger)}{td(c.net_over_dd ?? "—")}
                    {td(`${c.years_positive}/${c.years_total}`, c.years_positive === c.years_total ? colors.success : colors.danger)}
                    {td(usd(c.worst_month, 0), pnlCol(c.worst_month))}{td(c.max_consec_neg_months)}
                    {td(`${c.months_positive}/${c.months_total}`)}{td(c.sl_hits)}
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        </Card>
      ) : null}

      {/* ── Cell detail ── */}
      {run && cell ? (
        <Card>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
            <div>
              <div style={{ ...typography.h2, fontSize: 15 }}>{cellLabel(cell.cell)}{cells.length > 1 ? ` · rank ${cell.rank}/${cells.length}` : ""}</div>
              <div style={{ fontSize: 11, color: colors.text.tertiary }}>
                {run.summary.range_from?.slice(0, 10)} → {run.summary.range_to?.slice(0, 10)} · {cell.trading_days} trading days · spread ${run.params.spread_usd} · fee {run.params.fee_pct_side}%/side · size {run.params.size_btc} BTC · {run.summary.m1_rows?.toLocaleString()} 1m rows · {run.summary.elapsed_s}s
              </div>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              {isPrimary && run.trades?.length ? <Btn small onClick={downloadCsv}>CSV ({run.trades.length})</Btn> : null}
            </div>
          </div>

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <StatCard label="Net" value={usd(cell.net)} sub={`${usd(cell.net_per_day)} / day`} tone={cell.net >= 0 ? "good" : "bad"} />
            <StatCard label="Gross" value={usd(cell.gross)} sub={`spread ${usd(-cell.spread)} · fees ${usd(-cell.fees)}`} tone={cell.gross >= 0 ? "good" : "bad"} />
            <StatCard label="Trades" value={cell.trades} sub={`L ${cell.longs} · S ${cell.shorts}`} />
            <StatCard label="Win rate" value={pct(cell.win_rate)} sub={`L ${pct(cell.long_win_rate)} · S ${pct(cell.short_win_rate)}`} tone={cell.win_rate >= 0.5 ? "good" : "warn"} />
            <StatCard label="Max DD" value={usd(-cell.max_dd)} sub={`net/DD ${cell.net_over_dd ?? "—"} · PF ${cell.profit_factor ?? "—"}`} tone="bad" />
            <StatCard label="Avg win / loss" value={`${usd(cell.avg_win)} / ${usd(-cell.avg_loss)}`} sub={`best ${usd(cell.best)} · worst ${usd(cell.worst)}`} />
            <StatCard label="SL hits" value={cell.sl_hits} sub={cell.trades ? pct(cell.sl_hits / cell.trades) : ""} tone={cell.sl_hits ? "warn" : undefined} />
            <StatCard label="Months +" value={`${cell.months_positive}/${cell.months_total}`} sub={`worst ${usd(cell.worst_month, 0)} · neg run ${cell.max_consec_neg_months}`} tone={cell.months_positive > cell.months_total / 2 ? "good" : "bad"} />
            <StatCard label="Years +" value={`${cell.years_positive}/${cell.years_total}`} sub={`max consec. losses ${cell.max_consec_losses}`} tone={cell.years_positive === cell.years_total ? "good" : "bad"} />
            <StatCard label="Long / Short net" value={`${usd(cell.long_net, 0)} / ${usd(cell.short_net, 0)}`} />
          </div>

          <div style={{ marginTop: 14 }}><YearStrip years={cell.years} /></div>
          <div style={{ marginTop: 12 }}><MonthGrid months={cell.months} /></div>

          {isPrimary && run.trades?.length ? (
            <>
              <div style={{ marginTop: 14 }}><EquityCurve trades={run.trades} /></div>
              <div style={{ marginTop: 10, fontSize: 11, color: colors.text.tertiary }}>last {Math.min(200, run.trades.length)} trades (IST)</div>
              <div style={{ overflowX: "auto", maxHeight: 360, overflowY: "auto" }}>
                <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 11 }}>
                  <thead><tr style={{ color: colors.text.muted }}>
                    {["Time", "Dir", "Entry", "Exit", "Low", "High", "SL", "Move", "Costs", "Net"].map((h) =>
                      <th key={h} style={{ textAlign: "right", padding: "3px 6px", borderBottom: `1px solid ${colors.border.light}` }}>{h}</th>)}
                  </tr></thead>
                  <tbody>{run.trades.slice(-200).reverse().map((t) => (
                    <tr key={t.ts} style={{ borderBottom: `1px solid ${colors.border.light}` }}>
                      <td style={{ padding: "3px 6px", textAlign: "right", whiteSpace: "nowrap" }}>{new Date(t.ts * 1000).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour12: false })}</td>
                      <td style={{ padding: "3px 6px", textAlign: "right", color: t.dir === 1 ? colors.success : colors.danger, fontWeight: 600 }}>{t.dir === 1 ? "L" : "S"}</td>
                      <td style={{ padding: "3px 6px", textAlign: "right" }}>{t.entry.toFixed(1)}</td>
                      <td style={{ padding: "3px 6px", textAlign: "right" }}>{t.exit.toFixed(1)}</td>
                      <td style={{ padding: "3px 6px", textAlign: "right" }}>{t.low.toFixed(1)}</td>
                      <td style={{ padding: "3px 6px", textAlign: "right" }}>{t.high.toFixed(1)}</td>
                      <td style={{ padding: "3px 6px", textAlign: "right", color: t.sl_hit ? colors.warning : colors.text.tertiary }}>{t.sl_hit ? "HIT" : "—"}</td>
                      <td style={{ padding: "3px 6px", textAlign: "right", color: pnlCol(t.move) }}>{t.move.toFixed(1)}</td>
                      <td style={{ padding: "3px 6px", textAlign: "right", color: colors.warning }}>{usd(-(t.spread + t.fees))}</td>
                      <td style={{ padding: "3px 6px", textAlign: "right", color: pnlCol(t.net), fontWeight: 600 }}>{usd(t.net)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </>
          ) : null}
        </Card>
      ) : null}

      {/* ── History ── */}
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <div style={{ ...typography.h2, fontSize: 15 }}>STFC run history</div>
          <Btn small onClick={refreshRuns}>Refresh</Btn>
        </div>
        {!runs.length ? <div style={{ fontSize: 12, color: colors.text.tertiary }}>no runs yet</div> : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {runs.map((r) => {
              const p = r.summary?.primary || {};
              const b = r.summary?.best || {};
              return (
                <div key={r.run_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 12,
                  padding: "6px 10px", borderRadius: 6, background: run?.run_id === r.run_id ? colors.primaryBg : colors.bg.tertiary }}>
                  <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                    <span style={{ color: colors.text.tertiary }}>{new Date(r.created_at * 1000).toLocaleString("en-IN", { hour12: false })}</span>
                    <span>{r.params.cells > 1 ? `grid ${r.params.cells} cells` : cellLabel(p.cell || {})}</span>
                    <span style={{ color: colors.text.tertiary }}>{r.params.date_from || "…"} → {r.params.date_to || "…"}</span>
                    <span style={{ fontWeight: 700, color: pnlCol(p.net) }}>{usd(p.net, 0)}</span>
                    <span style={{ color: colors.text.tertiary }}>{p.trades} trades · win {pct(p.win_rate)} · yrs {p.years_positive}/{p.years_total}</span>
                    {r.params.cells > 1 && b.cell ? <span style={{ color: colors.text.tertiary }}>best: {cellLabel(b.cell)} {usd(b.net, 0)}</span> : null}
                  </div>
                  <Btn small onClick={() => loadRun(r.run_id)}>Load</Btn>
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
