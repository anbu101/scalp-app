// frontend/src/pages/backtest/RunComparison.jsx
//
// Backtest Run Comparison — an analytics tool (not just a viewer) for the
// Backtest page. Lists every persisted run with the PARAMETERS used and an
// EXHAUSTIVE KPI set, and lets you filter, sort, multi-select to compare
// side-by-side (with deltas + overlaid equity curves), inspect one run, and
// delete unwanted runs.
//
// DATA SOURCES (existing API, plus two small backend additions):
//   GET    /api/backtest/runs?limit=N      → [{run_id, strategy_id, underlying,
//            date_from, date_to, status, created_at, finished_at, summary, config}]
//          (config now included — see backend additions doc)
//   GET    /api/backtest/runs/{run_id}      → full run incl. trades (lazy-loaded
//            only when a run is opened/compared, so KPIs match Backtest.jsx exactly)
//   DELETE /api/backtest/runs/{run_id}      → delete a run (backend addition)
//
// KPI PARITY: this file imports `computeMetrics` from Backtest.jsx so every KPI
// (profit factor, expectancy, return/DD, hold times, streaks, exit-reason
// splits, day/instrument/side breakdowns, equity curve) is byte-for-byte what
// the run's own Summary/Advanced tabs show. No KPI is re-implemented here.
//
// This component is self-contained and takes the shared design primitives +
// helpers as props from the host page, so it never duplicates tokens or math.

import React, { useEffect, useState, useMemo, useCallback, useRef } from "react";
import AiPanel from "./AiPanel";   // ── AI_PANEL ──
import ReportView, { buildReportHtml } from "./ReportView";   // ── REPORT_VIEW ──

// Persisted column (metric) selection — survives navigation + app restart.
// Bump the version suffix if the default set changes meaningfully.
const COLS_LS_KEY = "scalp_compare_metric_cols_v1";

function loadSelectedKeys(allDefs) {
  try {
    const raw = localStorage.getItem(COLS_LS_KEY);
    if (raw) {
      const arr = JSON.parse(raw);
      if (Array.isArray(arr)) {
        // keep only keys that still exist (defs can change across versions)
        const valid = new Set(allDefs.map((d) => d.key));
        const filtered = arr.filter((k) => valid.has(k));
        if (filtered.length) return new Set(filtered);
      }
    }
  } catch { /* ignore */ }
  return new Set(allDefs.filter((d) => d.def).map((d) => d.key));  // defaults
}

function saveSelectedKeys(set) {
  try { localStorage.setItem(COLS_LS_KEY, JSON.stringify([...set])); } catch { /* ignore */ }
}

/* ============================================================================
   ── PARAMS_FULL BEGIN ──
   Parameter model — the FULL union of config keys across every strategy the
   backtest supports (SCALP_V1/V3/V4/V5, HA_V1, HA_SELL, WICK_V1), each with a
   short human label and a getter. Drives the params columns + compare rows.
   Rows where NO selected run sets the param are hidden automatically, so each
   comparison only shows the knobs that actually apply.
   ========================================================================== */
const _fmtConds = (arr) =>
  Array.isArray(arr) && arr.length
    ? arr.map((c) => String(c).replace("COND", "C")).join("+")
    : null;

const PARAM_DEFS = [
  { key: "date_from",        label: "From",           get: (r) => r.date_from },
  { key: "date_to",          label: "To",             get: (r) => r.date_to },
  { key: "premium_min",      label: "Prem min",       get: (r) => r.config?.option_premium?.min },
  { key: "premium_max",      label: "Prem max",       get: (r) => r.config?.option_premium?.max },
  // WICK_V1
  { key: "timeframe",        label: "Timeframe (m)",  get: (r) => r.config?.timeframe_minutes },
  { key: "top_wick_min",     label: "Top wick min",   get: (r) => r.config?.top_wick_min },
  { key: "dual_side",        label: "1 CE + 1 PE",    get: (r) => (r.config?.dual_side_mode ? "ON" : null) },
  // V1 / hedge / HA
  { key: "rr",               label: "R:R",            get: (r) => r.config?.risk_reward_ratio },
  { key: "min_sl",           label: "Min SL",         get: (r) => r.config?.min_sl_points },
  { key: "max_sl",           label: "Max SL cap",     get: (r) => r.config?.max_sl_points },
  { key: "risk_max_sl",      label: "Risk Max SL",    get: (r) => r.config?.risk_max_sl_points },
  { key: "hedge_sl",         label: "Hedge SL",       get: (r) => r.config?.hedge_sl_points },
  // V5 / WICK absolute points
  { key: "sl_points",        label: "SL pts",         get: (r) => r.config?.sl_points },
  { key: "tp_points",        label: "TP pts",         get: (r) => r.config?.tp_points },
  // HA-specific
  { key: "fixed_target",     label: "Fixed target",   get: (r) => (r.config?.target_override?.enabled ? `${r.config.target_override.points} pts` : null) },
  { key: "entry_conds",      label: "Entry conds",    get: (r) => _fmtConds(r.config?.entry_conditions) },
  { key: "max_trades_side",  label: "Max trades/side",get: (r) => r.config?.max_trades_per_side },
  { key: "tp_hold",          label: "TP hold candles",get: (r) => r.config?.tp_hold_extra_candles || null },
  // IC_V1
  { key: "ic_entry",         label: "Entry time",     get: (r) => r.config?.entry_time },
  { key: "ic_exit",          label: "EOD time",       get: (r) => r.config?.exit_time },
  { key: "ic_legs",          label: "Legs",           get: (r) => Array.isArray(r.config?.legs) ? r.config.legs.filter((l) => Number(l.lots) > 0).map((l) => `${l.id}:${l.action === "SELL" ? "S" : "B"}${l.opt_type}<${l.premium_max}${l.sl_val ? ` SL${l.sl_val}${l.sl_mode === "pts" ? "p" : "%"}` : ""}${l.mtc_other_on_sl ? "·MTC" : ""}`).join(" ") : null },
  // shared risk / session / size
  { key: "max_loss",         label: "Max Loss ₹",     get: (r) => r.config?.max_loss },
  { key: "max_profit",       label: "Max Profit ₹",   get: (r) => r.config?.max_profit },
  { key: "side",             label: "Side",           get: (r) => r.config?.trade_side_mode },
  { key: "sess_start",       label: "Sess start",     get: (r) => r.config?.session?.primary?.start },
  { key: "sess_end",         label: "Sess end",       get: (r) => r.config?.session?.primary?.end },
  { key: "lots",             label: "Lots",           get: (r) => r.config?.quantity?.lots },
  // PST_V1
  { key: "pst_prem",  label: "Premium <",  get: (r) => r.config?.signal_tf ? r.config?.premium_max : null },
  { key: "pst_legs",  label: "PST legs",   get: (r) => r.config?.signal_tf && Array.isArray(r.config?.legs) ? r.config.legs.filter((l) => Number(l.lots) > 0).map((l) => `${l.id}:${l.lots}L SL${l.sl_pct}% TG${l.spot_tg_points}p`).join(" ") : null },
  { key: "pst_side",  label: "Side",       get: (r) => r.config?.signal_tf ? r.config?.side_mode : null },
];
/* ── PARAMS_FULL END ── */

/* ============================================================================
   KPI model — the exhaustive list. Each KPI knows how to read its value from a
   computeMetrics() result (m) and the run summary (s), how to format it, and
   which direction is "good" (for delta coloring in compare view).
   dir:  +1 → higher is better, -1 → lower is better, 0 → neutral
   ========================================================================== */
// Derived period stats from computeMetrics' daily/weekly/monthly/yearly
// aggregates. Each aggregate row is {key,label,pnl,trades,wins}; a "win
// period" is pnl>0, a "loss period" is pnl<0.
function periodStats(m) {
  const count = (arr, pred) => (arr || []).reduce((n, r) => n + (pred(r) ? 1 : 0), 0);
  return {
    winDays:    count(m?.daily,   (r) => r.pnl > 0),
    lossDays:   count(m?.daily,   (r) => r.pnl < 0),
    winWeeks:   count(m?.weekly,  (r) => r.pnl > 0),
    lossWeeks:  count(m?.weekly,  (r) => r.pnl < 0),
    winMonths:  count(m?.monthly, (r) => r.pnl > 0),
    lossMonths: count(m?.monthly, (r) => r.pnl < 0),
    // ── YEARLY ── (m.yearly exists once Backtest.jsx ships the yearly aggregate)
    winYears:   count(m?.yearly,  (r) => r.pnl > 0),
    lossYears:  count(m?.yearly,  (r) => r.pnl < 0),
  };
}

// Exit-reason COUNT for a given reason from computeMetrics' exitReasons.
function exitCount(m, reason) {
  const er = (m?.exitReasons || []).find((x) => x.reason === reason);
  return er ? er.trades : 0;
}

// The exit reasons we surface as toggleable count rows (union of the common
// ones across strategies). Unknown reasons still appear in the Exit-reason
// matrix section below; these are just the quick-add count columns.
const EXIT_REASON_KEYS = ["TP", "SL", "SL_AFTER_TP", "EOD", "EMA_EXIT", "SIG_TP", "SIG_SL", "MAX_LOSS", "MAX_PROFIT"];

function makeKpiDefs(fmtInr) {
  const money = (v) => (v == null ? "—" : `${v >= 0 ? "" : "-"}${fmtInr(Math.abs(v))}`);
  const num2 = (v) => (v == null ? "—" : v === Infinity ? "∞" : Number(v).toFixed(2));
  const pct = (v) => (v == null ? "—" : `${Number(v).toFixed(1)}%`);
  const int = (v) => (v == null ? "—" : String(v));
  const dur = (s) => {
    if (!s) return "—";
    s = Math.round(s);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60), rs = s % 60;
    if (m < 60) return rs ? `${m}m ${rs}s` : `${m}m`;
    const h = Math.floor(m / 60), rm = m % 60;
    return `${h}h ${rm}m`;
  };
  // `def` flag = shown by default; others are off until the user adds them.
  const base = [
    { key: "net",         group: "Headline", label: "Net P&L",        dir: +1, def: true,  fmt: money, get: (m, s) => s?.net_pnl ?? m?.totalPnL },
    { key: "gross",       group: "Headline", label: "Gross P&L",      dir: +1, def: true,  fmt: money, get: (m, s) => s?.gross_pnl },
    { key: "charges",     group: "Headline", label: "Charges",        dir: -1, def: true,  fmt: money, get: (m, s) => s?.total_charges == null ? null : -Math.abs(s.total_charges) },
    { key: "trades",      group: "Headline", label: "Trades",         dir: 0,  def: true,  fmt: int,   get: (m, s) => s?.total_trades ?? m?.totalTrades },
    { key: "winRate",     group: "Headline", label: "Win rate",       dir: +1, def: true,  fmt: pct,   get: (m, s) => s?.win_rate ?? m?.winRate },
    { key: "maxDD",       group: "Risk",     label: "Max drawdown",   dir: -1, def: true,  fmt: money, get: (m, s) => (s?.max_drawdown != null ? -Math.abs(s.max_drawdown) : (m ? -Math.abs(m.maxDrawdown) : null)) },
    { key: "returnToDD",  group: "Risk",     label: "Return ÷ Max DD",dir: +1, def: true,  fmt: num2,  get: (m) => m?.returnToDD },
    { key: "profitFactor",group: "Edge",     label: "Profit factor",  dir: +1, def: true,  fmt: num2,  get: (m) => m?.profitFactor },
    { key: "expectancy",  group: "Edge",     label: "Expectancy/trade",dir: +1, def: true,  fmt: money, get: (m) => m?.expectancy },
    { key: "winLoss",     group: "Edge",     label: "Win/Loss size",  dir: +1, def: false, fmt: num2,  get: (m) => m?.winLossRatio },
    { key: "avgWin",      group: "Edge",     label: "Avg win",        dir: +1, def: false, fmt: money, get: (m) => m?.avgWinX },
    { key: "avgLoss",     group: "Edge",     label: "Avg loss",       dir: +1, def: false, fmt: money, get: (m) => m?.avgLossX },
    { key: "wins",        group: "Counts",   label: "Wins",           dir: +1, def: true,  fmt: int,   get: (m, s) => s?.wins ?? m?.wins },
    { key: "losses",      group: "Counts",   label: "Losses",         dir: -1, def: true,  fmt: int,   get: (m, s) => s?.losses ?? m?.losses },
    { key: "bestWinStk",  group: "Streaks",  label: "Win streak (max)", dir: +1, def: true,  fmt: int,  get: (m) => m?.bestWinStreak },
    { key: "bestLossStk", group: "Streaks",  label: "Loss streak (max)",dir: -1, def: true,  fmt: int,  get: (m) => m?.bestLossStreak },
    { key: "largestWin",  group: "Tails",    label: "Largest win",    dir: +1, def: true,  fmt: money, get: (m) => m?.largestWin },
    { key: "largestLoss", group: "Tails",    label: "Largest loss",   dir: +1, def: true,  fmt: money, get: (m) => m?.largestLoss },
    // ── Period win/loss counts (derived) ──
    { key: "winDays",     group: "Periods",  label: "Profitable days",dir: +1, def: false, fmt: int,   get: (m) => (m ? periodStats(m).winDays : null) },
    { key: "lossDays",    group: "Periods",  label: "Loss days",      dir: -1, def: false, fmt: int,   get: (m) => (m ? periodStats(m).lossDays : null) },
    { key: "winWeeks",    group: "Periods",  label: "Win weeks",      dir: +1, def: false, fmt: int,   get: (m) => (m ? periodStats(m).winWeeks : null) },
    { key: "lossWeeks",   group: "Periods",  label: "Loss weeks",     dir: -1, def: false, fmt: int,   get: (m) => (m ? periodStats(m).lossWeeks : null) },
    { key: "winMonths",   group: "Periods",  label: "Win months",     dir: +1, def: false, fmt: int,   get: (m) => (m ? periodStats(m).winMonths : null) },
    { key: "lossMonths",  group: "Periods",  label: "Loss months",    dir: -1, def: false, fmt: int,   get: (m) => (m ? periodStats(m).lossMonths : null) },
    // ── YEARLY ──
    { key: "winYears",    group: "Periods",  label: "Win years",      dir: +1, def: false, fmt: int,   get: (m) => (m ? periodStats(m).winYears : null) },
    { key: "lossYears",   group: "Periods",  label: "Loss years",     dir: -1, def: false, fmt: int,   get: (m) => (m ? periodStats(m).lossYears : null) },
    { key: "avgHold",     group: "Holding",  label: "Avg hold",       dir: 0,  def: false, fmt: dur,   get: (m) => m?.avgHold },
    { key: "medHold",     group: "Holding",  label: "Median hold",    dir: 0,  def: false, fmt: dur,   get: (m) => m?.medHold },
    { key: "avgHoldWin",  group: "Holding",  label: "Avg hold (wins)",dir: 0,  def: false, fmt: dur,   get: (m) => m?.avgHoldWin },
    { key: "avgHoldLoss", group: "Holding",  label: "Avg hold (losses)",dir: 0,def: false, fmt: dur,   get: (m) => m?.avgHoldLoss },
    { key: "ambiguous",   group: "Quality",  label: "Ambiguous fills",dir: -1, def: false, fmt: int,   get: (m, s) => s?.ambiguous_fills },
  ];
  // ── Exit-reason COUNT rows (one per reason, derived) ──
  const exitDefs = EXIT_REASON_KEYS.map((reason) => ({
    key: `exit_${reason}`,
    group: "Exit counts",
    label: `${reason} count`,
    dir: 0,
    def: false,
    fmt: int,
    get: (m) => (m ? exitCount(m, reason) : null),
  }));
  return [...base, ...exitDefs];
}

const STRAT_LABEL = { SCALP_V1: "V1", SCALP_V3: "V3", SCALP_V4: "V4", SCALP_V5: "V5", HA_V1: "HA", HA_SELL: "HAS", WICK_V1: "WICK", IC_V1: "IC", PST_V1: "PST" };
const STATUS_COLOR = (c, status) =>
  status === "done" ? c.profit : status === "error" ? c.loss : status === "cancelled" ? c.warning : c.text.muted;

/* Sort comparator for the runs table. */
// ── PARAMS_FULL ── Compact, distinguishing parameter summary for a run (used
// in the runs-table Key params column, equity legend, compare headers, CSV).
// Built FROM PARAM_DEFS so it can never drift out of sync with the matrix —
// every param the matrix knows about appears here when set. From/To are
// skipped (they have their own Period column).
const SUMMARY_SKIP = new Set(["date_from", "date_to"]);
const SUMMARY_SHORT = {
  premium_min: "prem≥", premium_max: "prem≤", timeframe: "tf", top_wick_min: "wick≥",
  dual_side: "", rr: "RR", min_sl: "minSL", max_sl: "maxSL", risk_max_sl: "rMaxSL",
  hedge_sl: "hSL", sl_points: "SL", tp_points: "TP", fixed_target: "tgt",
  entry_conds: "", max_trades_side: "cap", tp_hold: "hold", max_loss: "ML",
  max_profit: "MP", side: "", sess_start: "", sess_end: "", lots: "",
};
function paramSummary(run) {
  const cfg = run.config || {};
  const parts = [];
  // premium range as one token
  if (cfg.option_premium) parts.push(`prem ${cfg.option_premium.min}-${cfg.option_premium.max}`);
  for (const p of PARAM_DEFS) {
    if (SUMMARY_SKIP.has(p.key) || p.key === "premium_min" || p.key === "premium_max") continue;
    if (p.key === "sess_start" || p.key === "sess_end") continue;   // merged below
    if (p.key === "side" && cfg.trade_side_mode === "BOTH") continue; // BOTH = default, skip in compact view
    const v = p.get(run);
    if (v == null || v === "" || v === 0) continue;
    const pre = SUMMARY_SHORT[p.key];
    parts.push(pre ? `${pre}${typeof v === "string" && pre.endsWith("≥") ? "" : " "}${v}`.trim() : String(v));
  }
  if (cfg.session?.primary) parts.push(`${cfg.session.primary.start}-${cfg.session.primary.end}`);
  if (cfg.quantity?.lots != null) parts.push(`${cfg.quantity.lots}L`);
  return parts.join(" · ");
}

function cmp(a, b) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b));
}

export default function RunComparison({
  // shared design + helpers injected from Backtest.jsx (single source of truth)
  colors, spacing, typography, pnlStyle,
  Card, KpiTile,
  apiCall, fmtInr, fmtTs, computeMetrics, EquityCurve,
  onOpenRun,                 // (run_id) => void : jump to the main results view
}) {
  const KPI_DEFS = useMemo(() => makeKpiDefs(fmtInr), [fmtInr]);

  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [limit, setLimit] = useState(300);

  // selection for compare (set of run_id)
  const [selected, setSelected] = useState(() => new Set());
  // lazily-loaded full detail (trades + computed metrics) per run_id
  const [detail, setDetail] = useState({});   // run_id -> { trades, metrics }
  const [detailLoading, setDetailLoading] = useState({}); // run_id -> bool

  // filters
  const [fStrategy, setFStrategy] = useState("ALL");
  const [fStatus, setFStatus] = useState("ALL");
  const [fSearch, setFSearch] = useState("");
  const [fProfitableOnly, setFProfitableOnly] = useState(false);

  // sorting
  const [sortKey, setSortKey] = useState("created_at");
  const [sortDir, setSortDir] = useState("desc");

  // view mode
  const [mode, setMode] = useState("table");   // "table" | "compare"

  // inline status (replaces window.alert which Tauri's webview blocks)
  const [msg, setMsg] = useState(null);        // {kind:"ok"|"err"|"info", text}

  // ── REPORT_ENGINE BEGIN ── deterministic report over the selected runs
  const [report, setReport] = useState(null);       // {markdown, file}
  const [reportBusy, setReportBusy] = useState(false);
  const [reportRaw, setReportRaw] = useState(false);   // ── REPORT_VIEW ── raw .md toggle
  const generateReport = useCallback(async () => {
    if (selected.size < 2) return;
    setReportBusy(true);
    setMsg({ kind: "info", text: "Generating report…" });
    try {
      const d = await apiCall("/api/backtest/report", {
        method: "POST",
        body: JSON.stringify({ run_ids: [...selected], title: `report-${selected.size}runs` }),
      });
      setReport({ markdown: d.markdown, file: d.file });
      setMsg({ kind: "ok", text: `Report saved: ${String(d.file).split("/").pop()}` });
    } catch (e) {
      setMsg({ kind: "err", text: `Report failed: ${String(e.message || e)}` });
    } finally { setReportBusy(false); }
  }, [apiCall, selected]);
  // ── REPORT_ENGINE END ──

  // ── REPORT_LIBRARY BEGIN ── saved reports browser
  const [libOpen, setLibOpen] = useState(false);
  const [lib, setLib] = useState([]);            // [{name, size, modified}]
  const [libBusy, setLibBusy] = useState(false);
  const loadLibrary = useCallback(async () => {
    setLibBusy(true);
    try { const d = await apiCall("/api/backtest/reports"); setLib(d.reports || []); }
    catch (e) { setMsg({ kind: "err", text: `Couldn't list reports: ${String(e.message || e)}` }); }
    finally { setLibBusy(false); }
  }, [apiCall]);
  // open → refresh; also refresh after a new report is generated while open
  useEffect(() => { if (libOpen) loadLibrary(); }, [libOpen, loadLibrary, report]);
  const openSavedReport = useCallback(async (name) => {
    try {
      const d = await apiCall(`/api/backtest/reports/${encodeURIComponent(name)}`);
      setReport({ markdown: d.markdown, file: name });
    } catch (e) { setMsg({ kind: "err", text: `Couldn't open ${name}: ${String(e.message || e)}` }); }
  }, [apiCall]);
  const deleteSavedReport = useCallback(async (name) => {
    try {
      await apiCall(`/api/backtest/reports/${encodeURIComponent(name)}`, { method: "DELETE" });
      setLib((l) => l.filter((r) => r.name !== name));
      setReport((r) => (r && String(r.file).endsWith(name) ? null : r));
    } catch (e) { setMsg({ kind: "err", text: `Delete failed: ${String(e.message || e)}` }); }
  }, [apiCall]);
  // ── REPORT_LIBRARY END ──

  // ── AI_PANEL BEGIN ──
  const [aiOpen, setAiOpen] = useState(false);
  const [narrateBusy, setNarrateBusy] = useState(false);
  const addNarrative = useCallback(async () => {
    if (!report) return;
    const name = String(report.file).split("/").pop();
    setNarrateBusy(true);
    setMsg({ kind: "info", text: "Writing narrative locally… (up to a couple of minutes on Intel/CPU)" });
    try {
      const d = await apiCall("/api/backtest/ai/narrate", {
        method: "POST", body: JSON.stringify({ report: name }),
      });
      setReport({ markdown: d.markdown, file: name });
      setMsg({ kind: "ok", text: `Narrative added by ${d.model} in ${d.seconds}s.` });
    } catch (e) {
      setMsg({ kind: "err", text: String(e.message || e) });
    } finally { setNarrateBusy(false); }
  }, [apiCall, report]);
  // ── AI_PANEL END ──

  useEffect(() => {
    if (msg && msg.kind === "ok") {
      const t = setTimeout(() => setMsg(null), 4000);
      return () => clearTimeout(t);
    }
  }, [msg]);

  const reload = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const d = await apiCall(`/api/backtest/runs?limit=${limit}`);
      setRuns(d.runs || []);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }, [apiCall, limit]);

  useEffect(() => { reload(); }, [reload]);

  // ── lazy-load a run's trades + metrics (for compare / inspect) ──
  const ensureDetail = useCallback(async (runId) => {
    if (detail[runId] || detailLoading[runId]) return;
    setDetailLoading((s) => ({ ...s, [runId]: true }));
    try {
      const d = await apiCall(`/api/backtest/runs/${runId}`);
      const trades = d.trades || [];
      const metrics = computeMetrics(trades);
      setDetail((s) => ({ ...s, [runId]: { trades, metrics, summary: d.summary, config: d.config } }));
    } catch {
      setDetail((s) => ({ ...s, [runId]: { trades: [], metrics: null } }));
    } finally {
      setDetailLoading((s) => ({ ...s, [runId]: false }));
    }
  }, [apiCall, computeMetrics, detail, detailLoading]);

  // when entering compare mode, fetch detail for all selected runs
  useEffect(() => {
    if (mode !== "compare") return;
    selected.forEach((rid) => ensureDetail(rid));
  }, [mode, selected, ensureDetail]);

  const toggleSelect = useCallback((rid) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(rid) ? next.delete(rid) : next.add(rid);
      return next;
    });
  }, []);

  const del = useCallback(async (rid) => {
    // No window.confirm — Tauri's webview doesn't reliably support it (it can
    // silently return false, making the button appear dead). Backtest runs are
    // regenerable, so we delete directly and report status inline.
    setMsg({ kind: "info", text: "Deleting…" });
    try {
      await apiCall(`/api/backtest/runs/${rid}`, { method: "DELETE" });
      setRuns((rs) => rs.filter((r) => r.run_id !== rid));
      setSelected((prev) => { const n = new Set(prev); n.delete(rid); return n; });
      setDetail((d) => { const n = { ...d }; delete n[rid]; return n; });
      setMsg({ kind: "ok", text: "Run deleted." });
    } catch (e) {
      setMsg({ kind: "err", text: `Delete failed: ${String(e.message || e)}` });
    }
  }, [apiCall]);

  const delSelected = useCallback(async () => {
    if (!selected.size) return;
    setMsg({ kind: "info", text: `Deleting ${selected.size} run(s)…` });
    const ids = [...selected];
    const deleted = [];
    const failed = [];
    for (const rid of ids) {
      try {
        await apiCall(`/api/backtest/runs/${rid}`, { method: "DELETE" });
        deleted.push(rid);
      } catch (e) {
        failed.push({ rid, msg: String(e.message || e) });
      }
    }
    if (deleted.length) {
      const delSet = new Set(deleted);
      setRuns((rs) => rs.filter((r) => !delSet.has(r.run_id)));
      setSelected((prev) => {
        const n = new Set(prev);
        deleted.forEach((rid) => n.delete(rid));
        return n;
      });
    }
    if (failed.length) {
      setMsg({ kind: "err", text: `Deleted ${deleted.length}, failed ${failed.length}. First error: ${failed[0].msg}` });
    } else {
      setMsg({ kind: "ok", text: `Deleted ${deleted.length} run(s).` });
    }
  }, [apiCall, selected]);

  // ── derived: filtered + sorted rows ──
  const filtered = useMemo(() => {
    let rows = runs.slice();
    if (fStrategy !== "ALL") rows = rows.filter((r) => r.strategy_id === fStrategy);
    if (fStatus !== "ALL") rows = rows.filter((r) => (r.status || "") === fStatus);
    if (fProfitableOnly) rows = rows.filter((r) => (r.summary?.net_pnl ?? 0) > 0);
    if (fSearch.trim()) {
      const q = fSearch.trim().toLowerCase();
      rows = rows.filter((r) =>
        (r.run_id || "").toLowerCase().includes(q) ||
        (r.strategy_id || "").toLowerCase().includes(q) ||
        (r.date_from || "").includes(q) ||
        (r.date_to || "").includes(q)
      );
    }
    const getSort = (r) => {
      switch (sortKey) {
        case "strategy_id": return r.strategy_id;
        case "created_at":  return r.created_at;
        case "net":         return r.summary?.net_pnl;
        case "gross":       return r.summary?.gross_pnl;
        case "charges":     return r.summary?.total_charges;
        case "winRate":     return r.summary?.win_rate;
        case "trades":      return r.summary?.total_trades;
        case "maxDD":       return r.summary?.max_drawdown;
        case "date_from":   return r.date_from;
        default:            return r.created_at;
      }
    };
    rows.sort((a, b) => {
      const r = cmp(getSort(a), getSort(b));
      return sortDir === "asc" ? r : -r;
    });
    return rows;
  }, [runs, fStrategy, fStatus, fProfitableOnly, fSearch, sortKey, sortDir]);

  const setSort = (key) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("desc"); }
  };

  // ── SELECT_ALL BEGIN ── select/clear every VISIBLE (filtered) row at once.
  // Operates on the filtered set, not all runs, so it composes with the
  // strategy/status/search filters (e.g. filter to HA → select all → compare).
  const allFilteredSelected = useMemo(
    () => filtered.length > 0 && filtered.every((r) => selected.has(r.run_id)),
    [filtered, selected]
  );
  const toggleSelectAll = useCallback(() => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (filtered.length && filtered.every((r) => next.has(r.run_id))) {
        filtered.forEach((r) => next.delete(r.run_id));   // all on → clear visible
      } else {
        filtered.forEach((r) => next.add(r.run_id));      // otherwise → select visible
      }
      return next;
    });
  }, [filtered]);
  // ── SELECT_ALL END ──

  const selectedRuns = useMemo(
    () => runs.filter((r) => selected.has(r.run_id)),
    [runs, selected]
  );

  // ── styles ──
  const c = colors;
  const th = (key, label, align = "left") => (
    <th
      onClick={key ? () => setSort(key) : undefined}
      style={{
        padding: "9px 10px", textAlign: align, ...typography.label, color: c.text.muted,
        borderBottom: `2px solid ${c.border.light}`, whiteSpace: "nowrap",
        cursor: key ? "pointer" : "default", userSelect: "none",
      }}
    >
      {label}{sortKey === key ? (sortDir === "asc" ? " ▲" : " ▼") : ""}
    </th>
  );
  const chip = (active) => ({
    padding: "5px 12px", borderRadius: 6, cursor: "pointer", fontSize: 12, fontWeight: 600,
    border: `1px solid ${active ? c.primary : c.border.light}`,
    background: active ? c.primaryBg : c.bg.secondary,
    color: active ? c.primary : c.text.secondary,
  });
  const smallBtn = (variant) => ({
    padding: "6px 12px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 12, fontWeight: 600,
    background: variant === "primary" ? c.primary : variant === "danger" ? c.loss : c.bg.tertiary,
    color: variant === "primary" || variant === "danger" ? "#fff" : c.text.primary,
  });

  /* ── TOOLBAR ── */
  const toolbar = (
    <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.lg }}>
      <div style={{ display: "flex", gap: spacing.md, alignItems: "center", flexWrap: "wrap" }}>
        <input
          placeholder="Search run id / date…"
          value={fSearch} onChange={(e) => setFSearch(e.target.value)}
          style={{
            padding: "7px 10px", borderRadius: 6, border: `1px solid ${c.border.light}`,
            background: c.bg.secondary, color: c.text.primary, fontSize: 13, outline: "none", minWidth: 200,
          }}
        />
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {/* ── PARAMS_FULL ── HA_SELL + WICK_V1 added to the strategy filter */}
          {["ALL", "SCALP_V1", "SCALP_V3", "SCALP_V4", "SCALP_V5", "HA_V1", "HA_SELL", "WICK_V1", "IC_V1", "PST_V1" ].map((sId) => (
            <button key={sId} style={chip(fStrategy === sId)} onClick={() => setFStrategy(sId)}>
              {sId === "ALL" ? "All" : STRAT_LABEL[sId]}
            </button>
          ))}
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          {["ALL", "done", "error", "cancelled"].map((st) => (
            <button key={st} style={chip(fStatus === st)} onClick={() => setFStatus(st)}>
              {st === "ALL" ? "Any status" : st}
            </button>
          ))}
        </div>
        <button style={chip(fProfitableOnly)} onClick={() => setFProfitableOnly((v) => !v)}>
          Profitable only
        </button>
        {/* ── SELECT_ALL ── toolbar counterpart of the header checkbox */}
        <button style={chip(allFilteredSelected)} onClick={toggleSelectAll}>
          {allFilteredSelected ? "Clear all" : "Select all"}
        </button>

        <div style={{ marginLeft: "auto", display: "flex", gap: spacing.sm, alignItems: "center" }}>
          {msg && (
            <span style={{ fontSize: 12, fontWeight: 600,
              color: msg.kind === "ok" ? c.profit : msg.kind === "err" ? c.loss : c.text.muted }}>
              {msg.text}
            </span>
          )}
          <span style={{ fontSize: 12, color: c.text.muted }}>
            {filtered.length} run{filtered.length === 1 ? "" : "s"}
            {selected.size ? ` · ${selected.size} selected` : ""}
          </span>
          {/* ── REPORT_ENGINE ── */}
          <button style={smallBtn("default")} disabled={selected.size < 2 || reportBusy}
            onClick={generateReport}
            title="Deterministic report: leaderboard, sensitivity, year slices, robust ranking (worst-year first)">
            {reportBusy ? "Generating…" : `📄 Report (${selected.size})`}
          </button>
          {/* ── REPORT_LIBRARY ── */}
          <button style={smallBtn("default")} onClick={() => setLibOpen((v) => !v)}
            title="Browse saved reports">
            {libOpen ? "▾ Saved" : "▸ Saved"}
          </button>
          {/* ── AI_PANEL ── */}
          <button style={smallBtn("default")} onClick={() => setAiOpen((v) => !v)} title="Local AI setup — models & narratives">
            {aiOpen ? "▾ AI" : "▸ AI"}
          </button>
          {selected.size > 0 && (
            <button style={smallBtn("danger")} onClick={delSelected}>Delete selected</button>
          )}
          <button style={smallBtn("default")} onClick={reload}>↻ Refresh</button>
        </div>
      </div>
    </Card>
  );

  if (loading) {
    return <div style={{ padding: 40, textAlign: "center", color: c.text.muted, fontSize: 13 }}>Loading runs…</div>;
  }
  if (err) {
    return (
      <Card elevated style={{ padding: spacing.lg }}>
        <div style={{ color: c.loss, fontSize: 13 }}>Couldn't load runs: {err}</div>
        <button style={{ ...smallBtn("default"), marginTop: spacing.md }} onClick={reload}>Try again</button>
      </Card>
    );
  }
  if (!runs.length) {
    return (
      <Card elevated style={{ padding: "48px 0", textAlign: "center" }}>
        <div style={{ fontSize: 14, color: c.text.secondary, marginBottom: 6 }}>No saved runs yet</div>
        <div style={{ fontSize: 12, color: c.text.muted }}>Run a backtest and it'll show up here to filter, compare, and analyse.</div>
      </Card>
    );
  }

  return (
    <div>
      {toolbar}
      {/* ── REPORT_LIBRARY BEGIN ── */}
      {libOpen && (
        <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.lg }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: spacing.md }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: c.text.primary }}>Saved reports</span>
            <span style={{ fontSize: 11, ...typography.mono, color: c.text.muted }}>~/.scalp-app/backtest/reports</span>
            <button style={{ ...smallBtn("default"), marginLeft: "auto" }} onClick={loadLibrary}>↻</button>
          </div>
          {libBusy ? (
            <div style={{ color: c.text.muted, fontSize: 12 }}>Loading…</div>
          ) : !lib.length ? (
            <div style={{ color: c.text.muted, fontSize: 12 }}>No saved reports yet — select 2+ runs and press 📄 Report.</div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}>
              <tbody>
                {lib.map((r) => (
                  <tr key={r.name} style={{ borderTop: `1px solid ${c.border.dark}` }}>
                    <td style={{ padding: "8px 10px" }}>
                      <button onClick={() => openSavedReport(r.name)}
                        style={{ border: "none", background: "transparent", cursor: "pointer",
                          color: c.primary, fontSize: 12, fontWeight: 600, fontFamily: "monospace", padding: 0 }}>
                        {r.name}
                      </button>
                    </td>
                    <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, fontSize: 11, color: c.text.tertiary, whiteSpace: "nowrap" }}>
                      {new Date(r.modified * 1000).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })}
                    </td>
                    <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, fontSize: 11, color: c.text.muted }}>
                      {(r.size / 1024).toFixed(1)} KB
                    </td>
                    <td style={{ padding: "8px 10px", textAlign: "right", width: 40 }}>
                      <button onClick={() => deleteSavedReport(r.name)} title="Delete report file"
                        style={{ border: "none", background: "transparent", cursor: "pointer", color: c.loss, fontSize: 13 }}>🗑</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
      {/* ── REPORT_LIBRARY END ── */}
      {/* ── AI_PANEL ── */}
      {aiOpen && <AiPanel colors={c} spacing={spacing} typography={typography} Card={Card} apiCall={apiCall} />}
      {/* ── REPORT_ENGINE BEGIN ── */}
      {report && (
        <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.lg }}>
          <div style={{ display: "flex", alignItems: "center", gap: spacing.md, marginBottom: spacing.md }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: c.text.primary }}>Report</span>
            <span style={{ fontSize: 11, ...typography.mono, color: c.text.muted }}>{report.file}</span>
            <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              <button style={smallBtn("default")} disabled={narrateBusy} onClick={addNarrative}
                title="Fill section 7 with observations written by the local model — every number stays computed">
                {narrateBusy ? "Writing…" : "✨ Narrative"}
              </button>
              {/* ── REPORT_VIEW ── */}
              <button style={smallBtn("default")} onClick={() => setReportRaw((v) => !v)}
                title="Toggle between the rendered view and the raw markdown">
                {reportRaw ? "Rendered" : "Raw .md"}
              </button>
              <button style={smallBtn("default")} title="Self-contained dark HTML — opens in any browser, share it anywhere"
                onClick={() => {
                  const base = (String(report.file).split("/").pop() || "report.md").replace(/\.md$/, "");
                  const blob = new Blob([buildReportHtml(report.markdown, base)], { type: "text/html;charset=utf-8;" });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url; a.download = `${base}.html`;
                  document.body.appendChild(a); a.click(); document.body.removeChild(a);
                  setTimeout(() => URL.revokeObjectURL(url), 1000);
                }}>↓ .html</button>
              <button style={smallBtn("default")} onClick={() => {
                const blob = new Blob([report.markdown], { type: "text/markdown;charset=utf-8;" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url; a.download = String(report.file).split("/").pop() || "report.md";
                document.body.appendChild(a); a.click(); document.body.removeChild(a);
                setTimeout(() => URL.revokeObjectURL(url), 1000);
              }}>↓ .md</button>
              <button style={smallBtn("default")} onClick={() => setReport(null)}>Close</button>
            </div>
          </div>
          {/* ── REPORT_VIEW ── rendered by default; Raw toggle for the source */}
          {reportRaw ? (
            <pre style={{ margin: 0, maxHeight: "65vh", overflow: "auto", fontSize: 12,
              lineHeight: 1.55, fontFamily: "'JetBrains Mono','Fira Code',monospace",
              color: c.text.secondary, whiteSpace: "pre" }}>{report.markdown}</pre>
          ) : (
            <ReportView markdown={report.markdown} colors={c} typography={typography} />
          )}
        </Card>
      )}
      {/* ── REPORT_ENGINE END ── */}
      {mode === "table" ? (
        <RunsTable
          rows={filtered} c={c} spacing={spacing} typography={typography} pnlStyle={pnlStyle}
          Card={Card} fmtInr={fmtInr} th={th}
          selected={selected} toggleSelect={toggleSelect}
          allSelected={allFilteredSelected} toggleSelectAll={toggleSelectAll}
          onDelete={del} onOpenRun={onOpenRun}
          STRAT_LABEL={STRAT_LABEL} STATUS_COLOR={STATUS_COLOR}
        />
      ) : (
        <CompareView
          runs={selectedRuns} detail={detail} detailLoading={detailLoading}
          c={c} spacing={spacing} typography={typography} pnlStyle={pnlStyle}
          Card={Card} fmtInr={fmtInr} EquityCurve={EquityCurve}
          KPI_DEFS={KPI_DEFS} PARAM_DEFS={PARAM_DEFS} STRAT_LABEL={STRAT_LABEL}
          onOpenRun={onOpenRun}
        />
      )}
    </div>
  );
}

/* ============================================================================
   RUNS TABLE — sortable, selectable, with inline params + headline KPIs.
   ========================================================================== */
function RunsTable({
  rows, c, spacing, typography, pnlStyle, Card, fmtInr, th,
  selected, toggleSelect, allSelected, toggleSelectAll,
  onDelete, onOpenRun, STRAT_LABEL, STATUS_COLOR,
}) {
  const tsLabel = (epoch) => {
    if (!epoch) return "—";
    const d = new Date(epoch * 1000);
    return d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
  };
  const money = (v, signed = true) =>
    v == null ? "—" : `${signed && v >= 0 ? "+" : v < 0 ? "-" : ""}${fmtInr(Math.abs(v))}`;

  return (
    <Card style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}>
        <thead style={{ background: c.bg.tertiary }}>
          <tr>
            {/* ── SELECT_ALL ── header checkbox: toggles every visible row */}
            <th style={{ padding: "9px 10px", width: 32, borderBottom: `2px solid ${c.border.light}`, textAlign: "center" }}>
              <input type="checkbox" checked={!!allSelected} onChange={toggleSelectAll}
                title={allSelected ? "Clear all visible" : "Select all visible"} />
            </th>
            {th("strategy_id", "Strat")}
            {th("created_at", "When")}
            {th("date_from", "Period")}
            <th style={{ padding: "9px 10px", textAlign: "left", ...typography.label, color: c.text.muted, borderBottom: `2px solid ${c.border.light}`, whiteSpace: "nowrap" }}>Key params</th>
            {/* ── GROSS_CHARGES ── gross + charges alongside net so a run's cost
                drag is visible in the list (option-buying at high trade counts
                is charge-heavy; net alone hides edge-vs-cost). */}
            {th("gross", "Gross", "right")}
            {th("charges", "Charges", "right")}
            {th("net", "Net", "right")}
            {th("winRate", "Win%", "right")}
            {th("trades", "Trades", "right")}
            {th("maxDD", "Max DD", "right")}
            <th style={{ padding: "9px 10px", textAlign: "right", ...typography.label, color: c.text.muted, borderBottom: `2px solid ${c.border.light}` }}>Status</th>
            <th style={{ padding: "9px 10px", width: 90, borderBottom: `2px solid ${c.border.light}` }} />
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const s = r.summary || {};
            const isSel = selected.has(r.run_id);
            // ── PARAMS_FULL ── the Key params column now uses the SAME
            // paramSummary as the compare header/legend/CSV, so every set knob
            // (entry conds, caps, fixed target, TP hold, timeframe, wick, …)
            // is visible in the list — not just the old V1/V5 subset.
            const keyParams = paramSummary(r);

            return (
              <tr key={r.run_id}
                style={{ background: isSel ? c.primaryBg : i % 2 ? c.bg.secondary : c.bg.primary,
                  borderTop: `1px solid ${c.border.dark}` }}>
                <td style={{ padding: "8px 10px", textAlign: "center" }}>
                  <input type="checkbox" checked={isSel} onChange={() => toggleSelect(r.run_id)} />
                </td>
                <td style={{ padding: "8px 10px", fontWeight: 700 }}>{STRAT_LABEL[r.strategy_id] || r.strategy_id}</td>
                <td style={{ padding: "8px 10px", ...typography.mono, fontSize: 11, color: c.text.tertiary, whiteSpace: "nowrap" }}>{tsLabel(r.created_at)}</td>
                <td style={{ padding: "8px 10px", ...typography.mono, fontSize: 11, color: c.text.tertiary, whiteSpace: "nowrap" }}>{r.date_from} → {r.date_to}</td>
                <td style={{ padding: "8px 10px", fontSize: 11, color: c.text.secondary, maxWidth: 320 }}>{keyParams || "—"}</td>
                {/* ── GROSS_CHARGES ── gross (signed) + charges (always debit) + net */}
                <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, ...pnlStyle(s.gross_pnl) }}>{money(s.gross_pnl)}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, color: c.loss }}>{s.total_charges != null ? `−${fmtInr(Math.abs(s.total_charges))}` : "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, fontWeight: 700, ...pnlStyle(s.net_pnl) }}>{money(s.net_pnl)}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, color: (s.win_rate ?? 0) >= 50 ? c.profit : c.loss }}>{s.win_rate != null ? `${s.win_rate.toFixed(0)}%` : "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono }}>{s.total_trades ?? "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, color: c.loss }}>{s.max_drawdown != null ? fmtInr(s.max_drawdown) : "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", fontSize: 11, fontWeight: 700, color: STATUS_COLOR(c, r.status) }}>{r.status || "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", whiteSpace: "nowrap" }}>
                  <button title="Open in results"
                    onClick={() => onOpenRun?.(r.run_id)}
                    style={{ border: "none", background: "transparent", cursor: "pointer", color: c.primary, fontSize: 12, fontWeight: 600, marginRight: 8 }}>
                    Open
                  </button>
                  <button title="Delete run"
                    onClick={() => onDelete(r.run_id)}
                    style={{ border: "none", background: "transparent", cursor: "pointer", color: c.loss, fontSize: 13 }}>
                    🗑
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}

/* ============================================================================
   COMPARE VIEW — side-by-side params + exhaustive KPIs with best/worst
   highlighting and delta-vs-baseline, plus overlaid equity curves.
   ========================================================================== */
function CompareView({
  runs, detail, detailLoading, c, spacing, typography, pnlStyle,
  Card, fmtInr, EquityCurve, KPI_DEFS, PARAM_DEFS, STRAT_LABEL, onOpenRun,
}) {
  const [baselineIdx, setBaselineIdx] = useState(0);
  const wrapRef = useRef(null);
  const [w, setW] = useState(760);
  useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(320, e.contentRect.width - 32)));
    ro.observe(wrapRef.current);
    setW(Math.max(320, wrapRef.current.offsetWidth - 32));
    return () => ro.disconnect();
  }, []);

  if (runs.length < 2) {
    return <Card elevated style={{ padding: 32, textAlign: "center", color: c.text.muted, fontSize: 13 }}>Select at least two runs to compare.</Card>;
  }

  const cols = runs.map((r) => ({
    run: r,
    d: detail[r.run_id],
    loading: detailLoading[r.run_id],
  }));

  // Per-KPI: compute each run's raw value, find best (by dir), format + delta.
  const valueFor = (def, col) => def.get(col.d?.metrics, col.run.summary);

  const colHead = (col, idx) => {
    const r = col.run;
    const isBase = idx === baselineIdx;
    return (
      <th key={r.run_id} style={{ padding: "10px 12px", textAlign: "right", minWidth: 150, verticalAlign: "bottom",
        borderBottom: `2px solid ${c.border.light}`, background: isBase ? c.primaryBg : "transparent" }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: c.text.primary }}>{STRAT_LABEL[r.strategy_id] || r.strategy_id}</span>
          <span style={{ fontSize: 10, ...typography.mono, color: c.text.muted }}>{r.run_id.slice(0, 8)}</span>
          <span style={{ fontSize: 10, color: c.text.tertiary }}>{r.date_from} → {r.date_to}</span>
          <span style={{ fontSize: 9, color: c.text.muted, fontFamily: "monospace", maxWidth: 150, textAlign: "right", lineHeight: 1.3 }}>{paramSummary(r) || "—"}</span>
          <div style={{ display: "flex", gap: 6, marginTop: 2 }}>
            <button onClick={() => setBaselineIdx(idx)}
              style={{ border: "none", background: "transparent", cursor: "pointer",
                fontSize: 10, fontWeight: 700, color: isBase ? c.primary : c.text.muted }}>
              {isBase ? "● baseline" : "set baseline"}
            </button>
            <button onClick={() => onOpenRun?.(r.run_id)}
              style={{ border: "none", background: "transparent", cursor: "pointer", fontSize: 10, fontWeight: 700, color: c.primary }}>open</button>
          </div>
        </div>
      </th>
    );
  };

  const anyLoading = cols.some((col) => col.loading || !col.d);

  // ── Column (metric) picker state — persisted in localStorage ──
  const [selectedKeys, setSelectedKeys] = useState(() => loadSelectedKeys(KPI_DEFS));
  const [pickerOpen, setPickerOpen] = useState(false);
  useEffect(() => { saveSelectedKeys(selectedKeys); }, [selectedKeys]);

  const toggleKey = (key) =>
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  const setAll = (on) =>
    setSelectedKeys(on ? new Set(KPI_DEFS.map((d) => d.key)) : new Set());
  const resetDefaults = () =>
    setSelectedKeys(new Set(KPI_DEFS.filter((d) => d.def).map((d) => d.key)));

  // group KPIs by their `group` for sectioned rows (all defs, for the picker)
  const groups = [];
  const seen = {};
  KPI_DEFS.forEach((def) => {
    if (!seen[def.group]) { seen[def.group] = []; groups.push([def.group, seen[def.group]]); }
    seen[def.group].push(def);
  });

  // Only the groups/defs the user has enabled (drives the rendered matrix).
  const visibleGroups = groups
    .map(([group, defs]) => [group, defs.filter((d) => selectedKeys.has(d.key))])
    .filter(([, defs]) => defs.length > 0);

  // ── Download the comparison as CSV (params + visible KPIs, one col per run) ──
  const downloadComparisonCsv = () => {
    const esc = (v) => {
      if (v == null) return "";
      const s = String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = [];
    // header row: Metric, then one column per run (strategy + short id)
    lines.push(["Metric", ...cols.map((col) => `${STRAT_LABEL[col.run.strategy_id] || col.run.strategy_id} ${col.run.run_id.slice(0, 8)}`)].map(esc).join(","));
    lines.push(["run_id", ...cols.map((col) => col.run.run_id)].map(esc).join(","));
    lines.push(["period", ...cols.map((col) => `${col.run.date_from} to ${col.run.date_to}`)].map(esc).join(","));
    lines.push(["params", ...cols.map((col) => paramSummary(col.run))].map(esc).join(","));
    lines.push("");
    // params section (raw values)
    lines.push("PARAMETERS");
    PARAM_DEFS.forEach((p) => {
      const vals = cols.map((col) => p.get(col.run));
      if (vals.every((v) => v == null || v === "")) return;
      lines.push([p.label, ...vals.map((v) => (v == null ? "" : v))].map(esc).join(","));
    });
    lines.push("");
    // visible KPI sections (raw numeric values, not formatted, for spreadsheet use)
    visibleGroups.forEach(([group, defs]) => {
      lines.push(group.toUpperCase());
      defs.forEach((def) => {
        const vals = cols.map((col) => def.get(col.d?.metrics, col.run.summary));
        lines.push([def.label, ...vals.map((v) => (v == null || v === Infinity ? "" : v))].map(esc).join(","));
      });
      lines.push("");
    });
    const csv = lines.join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    const a = document.createElement("a");
    a.href = url;
    a.download = `backtest_comparison_${cols.length}runs_${stamp}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const fmtDelta = (def, v, base) => {
    if (v == null || base == null || def.dir === 0) return null;
    const diff = v - base;
    if (!isFinite(diff) || diff === 0) return null;
    const better = def.dir > 0 ? diff > 0 : diff < 0;
    const arrow = diff > 0 ? "▲" : "▼";
    return { text: `${arrow} ${def.fmt(Math.abs(diff))}`, color: better ? c.profit : c.loss };
  };

  return (
    <div ref={wrapRef}>
      {/* ── COLUMN (metric) PICKER ── */}
      <Card elevated style={{ padding: spacing.md, marginBottom: spacing.lg }}>
        <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap" }}>
          <button
            onClick={() => setPickerOpen((v) => !v)}
            style={{ padding: "6px 12px", borderRadius: 6, border: `1px solid ${c.border.light}`,
              background: c.bg.secondary, color: c.text.primary, cursor: "pointer", fontSize: 12, fontWeight: 600 }}>
            {pickerOpen ? "▾ Metrics" : "▸ Metrics"} ({selectedKeys.size})
          </button>
          <span style={{ fontSize: 11, color: c.text.muted }}>
            Pick which rows to show. Choice is remembered.
          </span>
          <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
            <button onClick={downloadComparisonCsv} title="Download the comparison matrix as CSV"
              style={{ padding: "5px 10px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 11, fontWeight: 600, background: c.primary, color: "#fff" }}>↓ CSV</button>
            <button onClick={resetDefaults} style={{ padding: "5px 10px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 11, fontWeight: 600, background: c.bg.tertiary, color: c.text.primary }}>Defaults</button>
            <button onClick={() => setAll(true)} style={{ padding: "5px 10px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 11, fontWeight: 600, background: c.bg.tertiary, color: c.text.primary }}>All</button>
            <button onClick={() => setAll(false)} style={{ padding: "5px 10px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 11, fontWeight: 600, background: c.bg.tertiary, color: c.text.primary }}>None</button>
          </div>
        </div>
        {pickerOpen && (
          <div style={{ marginTop: spacing.md, display: "flex", flexDirection: "column", gap: spacing.md }}>
            {groups.map(([group, defs]) => (
              <div key={group}>
                <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: 0.5, textTransform: "uppercase", color: c.text.muted, marginBottom: 6 }}>{group}</div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {defs.map((d) => {
                    const on = selectedKeys.has(d.key);
                    return (
                      <button key={d.key} onClick={() => toggleKey(d.key)}
                        style={{ padding: "4px 10px", borderRadius: 14, cursor: "pointer", fontSize: 11, fontWeight: 600,
                          border: `1px solid ${on ? c.primary : c.border.light}`,
                          background: on ? c.primaryBg : c.bg.secondary,
                          color: on ? c.primary : c.text.muted }}>
                        {on ? "✓ " : ""}{d.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* equity overlay */}
      <Card elevated style={{ padding: 16, marginBottom: spacing.lg }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <span style={{ fontSize: 14, fontWeight: 600 }}>Equity curves (overlay)</span>
          <span style={{ fontSize: 11, color: c.text.muted }}>net cumulative P&L per run</span>
        </div>
        {anyLoading ? (
          <div style={{ padding: "48px 0", textAlign: "center", color: c.text.muted, fontSize: 13 }}>Loading run trades…</div>
        ) : (
          <EquityOverlay cols={cols} width={w} height={260} c={c} fmtInr={fmtInr} STRAT_LABEL={STRAT_LABEL} />
        )}
      </Card>

      {/* params + KPI matrix (scrolls vertically; header stays put) */}
      <Card style={{ overflow: "auto", maxHeight: "70vh" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}>
          <thead style={{ background: c.bg.tertiary, position: "sticky", top: 0, zIndex: 1 }}>
            <tr>
              <th style={{ padding: "10px 12px", textAlign: "left", ...typography.label, color: c.text.muted, borderBottom: `2px solid ${c.border.light}`, background: c.bg.tertiary }}>Metric</th>
              {cols.map((col, idx) => colHead(col, idx))}
            </tr>
          </thead>
          <tbody>
            {/* PARAMS section */}
            <SectionRow label="Parameters" span={cols.length + 1} c={c} />
            {PARAM_DEFS.map((p) => {
              const vals = cols.map((col) => p.get(col.run));
              if (vals.every((v) => v == null || v === "")) return null;  // hide params none of them set
              return (
                <tr key={p.key} style={{ borderTop: `1px solid ${c.border.dark}` }}>
                  <td style={{ padding: "7px 12px", color: c.text.secondary, fontWeight: 600 }}>{p.label}</td>
                  {vals.map((v, idx) => {
                    const base = vals[baselineIdx];
                    const diff = idx !== baselineIdx && v !== base;
                    return (
                      <td key={idx} style={{ padding: "7px 12px", textAlign: "right", ...typography.mono,
                        color: diff ? c.text.primary : c.text.muted,
                        fontWeight: diff ? 700 : 400,
                        background: idx === baselineIdx ? c.primaryBg : "transparent" }}>
                        {v == null || v === "" ? "—" : String(v)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}

            {/* KPI sections — only the user-enabled metrics */}
            {visibleGroups.map(([group, defs]) => (
              <React.Fragment key={group}>
                <SectionRow label={group} span={cols.length + 1} c={c} />
                {defs.map((def) => {
                  const raw = cols.map((col) => valueFor(def, col));
                  const nums = raw.filter((v) => typeof v === "number" && isFinite(v));
                  let bestVal = null;
                  if (def.dir !== 0 && nums.length) bestVal = def.dir > 0 ? Math.max(...nums) : Math.min(...nums);
                  const base = raw[baselineIdx];
                  return (
                    <tr key={def.key} style={{ borderTop: `1px solid ${c.border.dark}` }}>
                      <td style={{ padding: "7px 12px", color: c.text.secondary, fontWeight: 600 }}>{def.label}</td>
                      {raw.map((v, idx) => {
                        const isBest = def.dir !== 0 && v != null && v === bestVal && nums.length > 1;
                        const delta = idx !== baselineIdx ? fmtDelta(def, v, base) : null;
                        return (
                          <td key={idx} style={{ padding: "7px 12px", textAlign: "right", ...typography.mono,
                            background: idx === baselineIdx ? c.primaryBg : isBest ? c.successBg : "transparent" }}>
                            <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
                              <span style={{ fontWeight: isBest ? 800 : 600,
                                color: isBest ? c.profit : c.text.primary }}>{def.fmt(v)}</span>
                              {delta && <span style={{ fontSize: 9, color: delta.color }}>{delta.text}</span>}
                            </div>
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </React.Fragment>
            ))}

            {/* Exit-reason mini-matrix (net per reason, if metrics present) */}
            <ExitReasonRows cols={cols} c={c} typography={typography} fmtInr={fmtInr} baselineIdx={baselineIdx} />

            {/* ── COND_MATRIX ── Entry-condition mini-matrix (HA runs). Same
                shape as exit reasons: net · trades · win% per condition per
                run — the direct read-out for the condition-isolation workflow
                (queue C1/C2/C3/all → compare here). */}
            <EntryConditionRows cols={cols} c={c} typography={typography} fmtInr={fmtInr} baselineIdx={baselineIdx} />
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function SectionRow({ label, span, c }) {
  return (
    <tr>
      <td colSpan={span} style={{ padding: "8px 12px", background: c.bg.secondary,
        fontSize: 11, fontWeight: 800, letterSpacing: 0.5, textTransform: "uppercase", color: c.text.muted,
        borderTop: `2px solid ${c.border.light}` }}>{label}</td>
    </tr>
  );
}

function ExitReasonRows({ cols, c, typography, fmtInr, baselineIdx }) {
  // union of exit reasons across runs
  const reasons = new Set();
  cols.forEach((col) => (col.d?.metrics?.exitReasons || []).forEach((er) => reasons.add(er.reason)));
  if (!reasons.size) return null;
  const reasonList = [...reasons].sort();
  const lookup = (col, reason) => (col.d?.metrics?.exitReasons || []).find((er) => er.reason === reason);
  return (
    <>
      <SectionRow label="Exit reasons (net · trades · win%)" span={cols.length + 1} c={c} />
      {reasonList.map((reason) => (
        <tr key={reason} style={{ borderTop: `1px solid ${c.border.dark}` }}>
          <td style={{ padding: "7px 12px", color: c.text.secondary, fontWeight: 600 }}>{reason}</td>
          {cols.map((col, idx) => {
            const er = lookup(col, reason);
            return (
              <td key={idx} style={{ padding: "7px 12px", textAlign: "right", ...typography.mono,
                background: idx === baselineIdx ? c.primaryBg : "transparent" }}>
                {er ? (
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
                    <span style={{ color: er.pnl >= 0 ? c.profit : c.loss, fontWeight: 700 }}>
                      {er.pnl >= 0 ? "+" : "-"}{fmtInr(Math.abs(er.pnl))}
                    </span>
                    <span style={{ fontSize: 9, color: c.text.muted }}>
                      {er.trades}t · {er.trades ? Math.round((er.wins / er.trades) * 100) : 0}%
                    </span>
                  </div>
                ) : <span style={{ color: c.text.muted }}>—</span>}
              </td>
            );
          })}
        </tr>
      ))}
    </>
  );
}

/* ── COND_MATRIX BEGIN ── per-entry-condition rows for HA_V1 / HA_SELL runs.
   Reads metrics.entryConditions (computed in Backtest.jsx's computeMetrics
   from each trade's `condition` field). Renders nothing for non-HA runs. */
function EntryConditionRows({ cols, c, typography, fmtInr, baselineIdx }) {
  const conds = new Set();
  cols.forEach((col) => (col.d?.metrics?.entryConditions || []).forEach((ec) => conds.add(ec.reason)));
  if (!conds.size) return null;
  const condList = [...conds].sort();
  const lookup = (col, cond) => (col.d?.metrics?.entryConditions || []).find((ec) => ec.reason === cond);
  return (
    <>
      <SectionRow label="Entry conditions (net · trades · win%)" span={cols.length + 1} c={c} />
      {condList.map((cond) => (
        <tr key={cond} style={{ borderTop: `1px solid ${c.border.dark}` }}>
          <td style={{ padding: "7px 12px", color: c.text.secondary, fontWeight: 600 }}>{cond}</td>
          {cols.map((col, idx) => {
            const ec = lookup(col, cond);
            return (
              <td key={idx} style={{ padding: "7px 12px", textAlign: "right", ...typography.mono,
                background: idx === baselineIdx ? c.primaryBg : "transparent" }}>
                {ec ? (
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
                    <span style={{ color: ec.pnl >= 0 ? c.profit : c.loss, fontWeight: 700 }}>
                      {ec.pnl >= 0 ? "+" : "-"}{fmtInr(Math.abs(ec.pnl))}
                    </span>
                    <span style={{ fontSize: 9, color: c.text.muted }}>
                      {ec.trades}t · {ec.trades ? Math.round((ec.wins / ec.trades) * 100) : 0}%
                    </span>
                  </div>
                ) : <span style={{ color: c.text.muted }}>—</span>}
              </td>
            );
          })}
        </tr>
      ))}
    </>
  );
}
/* ── COND_MATRIX END ── */

/* ── overlaid equity curves (multi-series, net cumulative) ── */
function EquityOverlay({ cols, width, height, c, fmtInr, STRAT_LABEL }) {
  const series = cols
    .map((col, i) => {
      const eq = col.d?.metrics?.equityCurve;
      if (!eq || eq.length < 2) return null;
      return { idx: i, run: col.run, points: eq.map((p) => p.value) };
    })
    .filter(Boolean);

  if (!series.length) {
    return <div style={{ padding: "40px 0", textAlign: "center", color: c.text.muted, fontSize: 13 }}>No closed trades to chart</div>;
  }

  const PAL = [c.primary, c.profit, c.warning, c.loss, "#a855f7", "#14b8a6", "#f97316"];
  const P = { top: 16, right: 16, bottom: 24, left: 76 };
  const W = width - P.left - P.right;
  const H = height - P.top - P.bottom;
  const maxLen = Math.max(...series.map((s) => s.points.length));
  const allVals = series.flatMap((s) => s.points).concat([0]);
  const minV = Math.min(...allVals), maxV = Math.max(...allVals);
  const range = (maxV - minV) || 1;
  const px = (i, len) => P.left + (len <= 1 ? 0 : (i / (len - 1)) * W);
  const py = (v) => P.top + H - ((v - minV) / range) * H;
  const y0 = py(0);
  const ticks = Array.from({ length: 5 }, (_, i) => minV + (range / 4) * i);

  return (
    <div>
      <svg width={width} height={height} style={{ display: "block", overflow: "visible" }}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={P.left} y1={py(t)} x2={P.left + W} y2={py(t)} stroke={c.border.dark} strokeWidth={0.5} />
            <text x={P.left - 6} y={py(t) + 4} textAnchor="end" fontSize={9} fill={c.text.muted} fontFamily="monospace">
              {t < 0 ? "-" : ""}{fmtInr(Math.abs(t))}
            </text>
          </g>
        ))}
        {minV < 0 && maxV > 0 && (
          <line x1={P.left} y1={y0} x2={P.left + W} y2={y0} stroke={c.text.muted} strokeWidth={1} strokeDasharray="4 3" opacity={0.5} />
        )}
        {series.map((s) => {
          const color = PAL[s.idx % PAL.length];
          const d = s.points.map((v, i) => `${i === 0 ? "M" : "L"} ${px(i, s.points.length).toFixed(1)} ${py(v).toFixed(1)}`).join(" ");
          return <path key={s.run.run_id} d={d} fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" opacity={0.9} />;
        })}
      </svg>
      {/* legend — strategy, short id, params, end P&L */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 8 }}>
        {series.map((s) => {
          const color = PAL[s.idx % PAL.length];
          const end = s.points[s.points.length - 1];
          const summary = paramSummary(s.run);
          return (
            <div key={s.run.run_id} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11 }}>
              <span style={{ width: 14, height: 3, background: color, borderRadius: 2, flexShrink: 0 }} />
              <span style={{ color: c.text.secondary, fontWeight: 700, whiteSpace: "nowrap" }}>
                {STRAT_LABEL[s.run.strategy_id] || s.run.strategy_id} {s.run.run_id.slice(0, 6)}
              </span>
              <span style={{ color: c.text.muted, fontFamily: "monospace", fontSize: 10, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {summary || "—"}
              </span>
              <span style={{ fontFamily: "monospace", color: end >= 0 ? c.profit : c.loss, marginLeft: "auto", flexShrink: 0, fontWeight: 700 }}>
                {end >= 0 ? "+" : "-"}{fmtInr(Math.abs(end))}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}