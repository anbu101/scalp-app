// frontend/src/pages/backtest/Portfolio.jsx
//
// ── PORTFOLIO_VIEW ── Multi-strategy portfolio composition (2–3 runs over the
// SAME date range).
//
// COMPOSITION, NOT CO-SIMULATION: strategies are fully isolated in live and in
// backtest (no shared slot, no shared MTM caps, no cross-strategy state), so
// running e.g. SCALP_V3 + SCALP_V5 "together" is mathematically identical to
// running each alone and summing their time-aligned realized-P&L streams.
// This view therefore merges the PERSISTED trades of the selected runs and
// computes combined analytics client-side. No runner is touched; no backend
// change; no live-money path involved.
//
// NOT modeled (v2 / needs a real combined runner): a shared portfolio-level
// max-loss/kill-switch across strategies. Each run keeps its OWN caps.
//
// CORRECTNESS — exit-realized equity: composed curves book each trade's net
// P&L at exit_ts (when it actually realizes), NOT entry_ts. Interleaving two
// strategies' trades by entry time would book P&L before it exists and
// distort the combined drawdown — the one number this page exists for. As a
// consequence, the per-strategy Max DD shown HERE can differ slightly from
// the same run's own Advanced tab (which steps its curve at entry_ts).
//
// Selection rules: 2–3 runs · one run per strategy · identical
// date_from/date_to · status "done". Composition is blocked (with the exact
// reason) otherwise. No silent range intersection — that would quietly change
// what the numbers mean.
//
// DATA SOURCES (all existing endpoints — nothing new server-side):
//   GET  /api/backtest/runs?limit=N     → list incl. summary + config
//   GET  /api/backtest/runs/{run_id}    → full detail incl. trades (lazy)
//   GET  /api/backtest/queue/status     → used to group "PF:" labeled jobs
//   POST /api/backtest/queue/enqueue    → stage a portfolio (label convention)
//   POST /api/backtest/queue/start      → optional auto-start after staging

import React, { useEffect, useState, useMemo, useCallback, useRef } from "react";

// ── PF_MAX (2026-08-04) ── raised 3 → 5. Single source of truth for the
// portfolio size cap: selection validation, auto-detected PF grouping, the
// strategy staging picker and its labels all read this. Everything
// downstream is data-driven (auto-fit grids, mapped NxN correlation matrix),
// so the cap is the only thing that needed to move. Colours are safe at 5:
// ACCENT is keyed by strategy_id, and PF_VALIDATE already enforces ONE run
// per strategy — so 5 runs are always 5 distinct strategies, and ACCENT
// defines 14. The correlation matrix grows 3x3 → 5x5 (10 distinct pairs
// instead of 3), which is the main visual change to sanity-check.
const MAX_PF = 5;
const PF_LABEL_PREFIX = "PF:";

// Strategy accents — matches the app-wide accent map.
const ACCENT = {
  SCALP_V1: "#f59e0b", SCALP_V3: "#ec4899",
  SCALP_V5: "#06b6d4", HA_V1: "#14b8a6", HA_SELL: "#2dd4bf",
  WICK_V1: "#a3e635", IC_V1: "#6366f1", IC_V2: "#818cf8", PST_V1: "#f43f5e", PST_SELL: "#fb7185", PST_HEDGE: "#be123c", BB_V1: "#3b82f6", BB_V2: "#60a5fa",
  TMA_V1: "#8b5cf6",   // ── TMA_V1 ── violet-500 (distinct from every accent above)
  TMA_V2: "#c084fc",   // ── TMA_V2 ── purple-400 (distinct from every accent above)
  TSG_V1: "#d946ef",   // ── TSG_V1 ── fuchsia-500 (distinct from every accent above)
  GC_V1: "#38bdf8",    // ── GC_V1 ── sky-400
};
const STRAT_LABEL = {
  SCALP_V1: "V1", SCALP_V3: "V3", SCALP_V5: "V5",
  HA_V1: "HA", HA_SELL: "HAS", WICK_V1: "WICK", IC_V1: "IC", IC_V2: "IC2", PST_V1: "PST", PST_SELL: "PSTS", PST_HEDGE: "PSTH", BB_V1: "BB1", BB_V2: "BB2",
  TMA_V1: "TMA",   // ── TMA_V1 ──
  TMA_V2: "TMA2",  // ── TMA_V2 ──
  TSG_V1: "TSG",   // ── TSG_V1 ──
  GC_V1: "GC",     // ── GC_V1 ──
};
// Strategies the launch panel can stage (SCALP page scope — buildConfig
// supports exactly these).
// ── WICK_PST_V1_REMOVAL ── WICK_V1 / PST_V1 dropped from the launcher (their
// runners are gone). The colour + short-label maps above deliberately keep
// their entries so archived runs still plot and label correctly.
const LAUNCHABLE = ["SCALP_V1", "SCALP_V3", "SCALP_V5", "HA_V1", "HA_SELL", "IC_V1", "IC_V2", "PST_SELL", "PST_HEDGE", "TMA_V1", "TMA_V2", "TSG_V1", "GC_V1"];

// Lot sizes for the premium-notional exposure estimate (qty = lots × lot size).
const LOT_SIZE = { NIFTY: 65, BANKNIFTY: 30 };

/* ── PF_HELPERS BEGIN ── local copies of the tiny trade-math helpers.
   (netOf/safeNum are module-private in Backtest.jsx; duplicated here verbatim
   so this file stays drop-in with zero extra exports.) */
const safeNum = (v) => (typeof v === "number" && isFinite(v) ? v : 0);
const netOf = (t) => (t.net_pnl != null ? safeNum(t.net_pnl) : safeNum(t.pnl) - safeNum(t.charges));

// IST calendar-day key of an epoch (fixed +5:30, same convention as istHM).
function istDayKey(epoch) {
  const d = new Date((epoch + 5.5 * 3600) * 1000);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
}

function fmtDayLabel(key) {
  const [y, m, d] = key.split("-");
  return new Date(Number(y), Number(m) - 1, Number(d))
    .toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "2-digit" });
}
function fmtMonthLabel(key) {
  const [y, m] = key.split("-");
  return `${new Date(Number(y), Number(m) - 1, 1).toLocaleString("en-IN", { month: "short" })} ${y}`;
}
function fmtDurS(s) {
  if (!s) return "0m";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

// Pearson correlation. null when undefined (n<2 or zero variance).
function pearson(xs, ys) {
  const n = xs.length;
  if (n < 2) return null;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let sxy = 0, sxx = 0, syy = 0;
  for (let i = 0; i < n; i++) {
    const dx = xs[i] - mx, dy = ys[i] - my;
    sxy += dx * dy; sxx += dx * dx; syy += dy * dy;
  }
  if (sxx === 0 || syy === 0) return null;
  return sxy / Math.sqrt(sxx * syy);
}
/* ── PF_HELPERS END ── */

/* ── PF_EXPOSURE BEGIN ── interval sweep over all open trades across the
   selected strategies. Yields the honest-capital picture composition assumes:
   max concurrent open trades, max strategies simultaneously in-trade, peak
   combined premium NOTIONAL (entry_price × lots × lot size — capital outlay
   for LONG legs; for SHORT strategies actual requirement is exchange margin,
   which is larger — flagged in the UI), and the share of in-trade time where
   ≥2 strategies hold positions at once. */
function exposureSweep(perStrat) {
  const evs = [];
  perStrat.forEach((s, si) => {
    const lots = Number(s.run.config?.quantity?.lots ?? 0);
    const lot = LOT_SIZE[String(s.run.underlying || "NIFTY").toUpperCase()] ?? LOT_SIZE.NIFTY;
    const qty = lots * lot;
    for (const t of s.closed) {
      if (!t.entry_ts || !t.exit_ts || t.exit_ts <= t.entry_ts) continue;
      const notional = safeNum(t.entry_price) * qty;
      evs.push({ ts: t.entry_ts, si, dCnt: +1, dNot: +notional });
      evs.push({ ts: t.exit_ts, si, dCnt: -1, dNot: -notional });
    }
  });
  // entries before exits on timestamp ties → conservative (higher) peak
  evs.sort((a, b) => a.ts - b.ts || b.dCnt - a.dCnt);
  const perCnt = perStrat.map(() => 0);
  let cnt = 0, notional = 0, maxCnt = 0, peakNotional = 0, maxStrats = 0;
  let lastTs = null, tAny = 0, tMulti = 0;
  for (const e of evs) {
    if (lastTs != null && e.ts > lastTs) {
      const dt = e.ts - lastTs;
      const strats = perCnt.reduce((n, x) => n + (x > 0 ? 1 : 0), 0);
      if (strats >= 1) tAny += dt;
      if (strats >= 2) tMulti += dt;
    }
    cnt += e.dCnt; perCnt[e.si] += e.dCnt; notional += e.dNot;
    if (cnt > maxCnt) maxCnt = cnt;
    if (notional > peakNotional) peakNotional = notional;
    const strats = perCnt.reduce((n, x) => n + (x > 0 ? 1 : 0), 0);
    if (strats > maxStrats) maxStrats = strats;
    lastTs = e.ts;
  }
  return {
    maxConcurrent: maxCnt, maxStrats, peakNotional,
    overlapPct: tAny > 0 ? (tMulti / tAny) * 100 : 0,
    tAny, tMulti,
  };
}
/* ── PF_EXPOSURE END ── */

/* ── PF_COMPOSE BEGIN ── the portfolio math. Input: [{run, trades}] with
   trades loaded for every entry. Everything downstream renders from this. */
export function composePortfolio(entries) {
  const perStrat = entries.map(({ run, trades }) => {
    const closed = (trades || []).filter((t) => t.exit_ts && t.exit_price != null);
    // realization events at exit_ts (see header note)
    const events = closed
      .map((t) => ({ ts: t.exit_ts, net: netOf(t) }))
      .sort((a, b) => a.ts - b.ts);
    let eq = 0, peak = 0, dd = 0;
    const curve = events.map((e) => {
      eq += e.net;
      if (eq > peak) peak = eq;
      if (peak - eq > dd) dd = peak - eq;
      return { ts: e.ts, value: eq };
    });
    const daily = new Map();
    for (const e of events) {
      const k = istDayKey(e.ts);
      daily.set(k, (daily.get(k) || 0) + e.net);
    }
    return { run, closed, events, curve, net: eq, maxDD: dd, tradeCount: closed.length, daily };
  });

  // merged combined curve + the combined-DD window (peak → trough)
  const merged = perStrat
    .flatMap((s, si) => s.events.map((e) => ({ ts: e.ts, net: e.net, si })))
    .sort((a, b) => a.ts - b.ts);
  let eq = 0, peak = 0, dd = 0, peakTs = null, ddPeakTs = null, ddTroughTs = null;
  const combined = merged.map((e) => {
    eq += e.net;
    if (eq > peak) { peak = eq; peakTs = e.ts; }
    if (peak - eq > dd) { dd = peak - eq; ddPeakTs = peakTs; ddTroughTs = e.ts; }
    return { ts: e.ts, value: eq };
  });

  // which strategy dug (or filled) the hole during the combined-DD window
  const ddContrib = perStrat.map((s) =>
    ddPeakTs == null ? 0 :
      s.events.filter((e) => e.ts > ddPeakTs && e.ts <= ddTroughTs)
        .reduce((a, b) => a + b.net, 0));

  // day table over the union of realization days
  const dayKeys = [...new Set(perStrat.flatMap((s) => [...s.daily.keys()]))].sort();
  const days = dayKeys.map((k) => {
    const per = perStrat.map((s) => s.daily.get(k) || 0);
    return { day: k, per, total: per.reduce((a, b) => a + b, 0) };
  });

  // pairwise Pearson correlation of daily nets (union of the pair's trading
  // days; a day one side didn't trade counts as 0 for that side — days where
  // NEITHER traded are excluded so quiet stretches don't inflate r)
  const n = perStrat.length;
  const corr = Array.from({ length: n }, () => Array(n).fill(null));
  for (let i = 0; i < n; i++) {
    corr[i][i] = 1;
    for (let j = i + 1; j < n; j++) {
      const keys = [...new Set([...perStrat[i].daily.keys(), ...perStrat[j].daily.keys()])];
      const xs = keys.map((k) => perStrat[i].daily.get(k) || 0);
      const ys = keys.map((k) => perStrat[j].daily.get(k) || 0);
      const r = pearson(xs, ys);
      corr[i][j] = r; corr[j][i] = r;
    }
  }

  // diversification day-stats
  const rescuedDays = days.filter((d) => d.total > 0 && d.per.some((p) => p < 0)).length;
  const clusterDays = days.filter((d) => d.per.filter((p) => p < 0).length >= 2).length;
  const redDays = days.filter((d) => d.total < 0).length;
  const greenDays = days.filter((d) => d.total > 0).length;
  const worstCombinedDay = days.length
    ? days.reduce((w, d) => (d.total < w.total ? d : w)) : null;
  const worstIndividual = perStrat.map((s) => {
    let worst = null;
    for (const [k, v] of s.daily) if (worst == null || v < worst.v) worst = { k, v };
    return worst;
  });

  // monthly rollup (per strategy + combined)
  const monthMap = new Map();
  for (const d of days) {
    const m = d.day.slice(0, 7);
    if (!monthMap.has(m)) monthMap.set(m, { month: m, per: perStrat.map(() => 0), total: 0 });
    const row = monthMap.get(m);
    d.per.forEach((p, i) => { row.per[i] += p; });
    row.total += d.total;
  }
  const monthly = [...monthMap.values()].sort((a, b) => a.month.localeCompare(b.month));

  const sumIndDD = perStrat.reduce((a, s) => a + s.maxDD, 0);
  const combinedNet = perStrat.reduce((a, s) => a + s.net, 0);

  return {
    perStrat, combined,
    combinedNet, combinedMaxDD: dd, ddPeakTs, ddTroughTs, ddContrib,
    sumIndDD,
    ddReduction: sumIndDD > 0 ? (1 - dd / sumIndDD) * 100 : null,
    returnToDD: dd > 0 ? combinedNet / dd : (combinedNet > 0 ? Infinity : 0),
    days, corr,
    rescuedDays, clusterDays, redDays, greenDays,
    worstCombinedDay, worstIndividual,
    monthly,
    exposure: exposureSweep(perStrat),
  };
}
/* ── PF_COMPOSE END ── */

/* ── PF_EQUITY_CHART BEGIN ── time-axis multi-series equity chart. Unlike the
   Compare overlay (index-based x), the x-axis here is REAL TIME, so the
   strategies and the combined curve interleave exactly as they did on the
   calendar — required for the combined-DD story to read correctly. Curves are
   downsampled for render only (math above always uses every event). */
function PortfolioEquityChart({ pf, width, height = 300, c, fmtInr }) {
  const series = [
    ...pf.perStrat.map((s) => ({
      key: s.run.run_id,
      label: `${STRAT_LABEL[s.run.strategy_id] || s.run.strategy_id}`,
      color: ACCENT[s.run.strategy_id] || c.text.secondary,
      points: s.curve, thick: false, end: s.net,
    })),
    { key: "__pf__", label: "Portfolio", color: c.primary, points: pf.combined, thick: true, end: pf.combinedNet },
  ].filter((s) => s.points.length >= 2);
  if (!series.length) {
    return <div style={{ padding: "40px 0", textAlign: "center", color: c.text.muted, fontSize: 13 }}>No closed trades to chart</div>;
  }

  const t0 = Math.min(...series.map((s) => s.points[0].ts));
  const t1 = Math.max(...series.map((s) => s.points[s.points.length - 1].ts));
  const span = Math.max(1, t1 - t0);
  const allVals = series.flatMap((s) => s.points.map((p) => p.value)).concat([0]);
  const minV = Math.min(...allVals), maxV = Math.max(...allVals);
  const range = (maxV - minV) || 1;

  const P = { top: 16, right: 16, bottom: 28, left: 76 };
  const W = width - P.left - P.right;
  const H = height - P.top - P.bottom;
  const px = (ts) => P.left + ((ts - t0) / span) * W;
  const py = (v) => P.top + H - ((v - minV) / range) * H;
  const y0 = py(0);
  const ticks = Array.from({ length: 5 }, (_, i) => minV + (range / 4) * i);
  const xTicks = Array.from({ length: 6 }, (_, i) => t0 + (span / 5) * i);

  const pathOf = (pts) => {
    // render-only downsample: keep first/last, stride the middle
    const MAXP = 1500;
    const step = pts.length > MAXP ? Math.ceil(pts.length / MAXP) : 1;
    const kept = pts.filter((_, i) => i % step === 0 || i === pts.length - 1);
    let d = `M ${px(pts[0].ts).toFixed(1)} ${y0.toFixed(1)}`;   // start at 0
    for (const p of kept) d += ` L ${px(p.ts).toFixed(1)} ${py(p.value).toFixed(1)}`;
    return d;
  };

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
        {series.map((s) => (
          <path key={s.key} d={pathOf(s.points)} fill="none" stroke={s.color}
            strokeWidth={s.thick ? 2.6 : 1.4} opacity={s.thick ? 1 : 0.75}
            strokeLinecap="round" strokeLinejoin="round" />
        ))}
        {xTicks.map((ts, i) => (
          <text key={i} x={px(ts).toFixed(1)} y={P.top + H + 18} textAnchor="middle"
            fontSize={9} fill={c.text.muted} fontFamily="monospace">
            {new Date(ts * 1000).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "2-digit" })}
          </text>
        ))}
      </svg>
      <div style={{ display: "flex", gap: 18, flexWrap: "wrap", marginTop: 10 }}>
        {series.map((s) => (
          <div key={s.key} style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 11 }}>
            <span style={{ width: 16, height: s.thick ? 4 : 3, background: s.color, borderRadius: 2 }} />
            <span style={{ color: c.text.secondary, fontWeight: s.thick ? 800 : 700 }}>{s.label}</span>
            <span style={{ fontFamily: "monospace", fontWeight: 700, color: s.end >= 0 ? c.profit : c.loss }}>
              {s.end >= 0 ? "+" : "-"}{fmtInr(Math.abs(s.end))}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
/* ── PF_EQUITY_CHART END ── */

/* ── small shared bits ── */
function StratChip({ sid, c }) {
  const col = ACCENT[sid] || c.text.secondary;
  return (
    <span style={{
      padding: "2px 8px", borderRadius: 4, fontSize: 11, fontWeight: 800,
      background: `${col}22`, border: `1px solid ${col}55`, color: col, whiteSpace: "nowrap",
    }}>
      {STRAT_LABEL[sid] || sid}
    </span>
  );
}

export default function Portfolio({
  colors, spacing, typography, pnlStyle,
  Card, KpiTile,
  apiCall, fmtInr, fmtTs, describeConfig,
  buildConfig,                 // (strategyId) => config_override (from Backtest.jsx)
  defaultFrom, defaultTo,      // form date range, used as launch defaults
  onOpenRun,
}) {
  const c = colors;

  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [detail, setDetail] = useState({});          // run_id -> trades[]
  const [detailLoading, setDetailLoading] = useState({});
  // ── PF_SWITCH_FIX ── failed trade fetches are recorded here (never cached
  // as empty trades — composing an empty leg silently produces garbage KPIs
  // and, worse, looks like "the old portfolio"). A run with an error is NOT
  // loaded; a visible banner offers Retry.
  const [detailErr, setDetailErr] = useState({});    // run_id -> error string
  const [activePfName, setActivePfName] = useState(null); // set by chip select
  const [msg, setMsg] = useState(null);
  const [tab, setTab] = useState("overview");
  const [pfGroups, setPfGroups] = useState([]);      // from PF: queue labels
  // ── PF_RESULTS_FOCUS BEGIN ── the results render BELOW the (long) run
  // picker; without this, composing a portfolio looks like nothing happened.
  // pickerOpen=false collapses the picker to a slim strip (set by the
  // Finished-portfolio chips); the effect scrolls to the results the moment
  // composition completes, whichever path selected the runs.
  const [pickerOpen, setPickerOpen] = useState(true);
  const resultsRef = useRef(null);
  const hadPf = useRef(false);
  // ── PF_RESULTS_FOCUS END ──

  // launch panel
  const [launchOpen, setLaunchOpen] = useState(false);
  const [pfName, setPfName] = useState("");
  const [pfFrom, setPfFrom] = useState(defaultFrom || "");
  const [pfTo, setPfTo] = useState(defaultTo || "");
  const [pfStrats, setPfStrats] = useState(() => new Set(["SCALP_V3", "SCALP_V5"]));
  const [enqueueing, setEnqueueing] = useState(false);

  const wrapRef = useRef(null);
  const [w, setW] = useState(800);
  useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(320, e.contentRect.width - 32)));
    ro.observe(wrapRef.current);
    setW(Math.max(320, wrapRef.current.offsetWidth - 32));
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (msg && msg.kind === "ok") {
      const t = setTimeout(() => setMsg(null), 5000);
      return () => clearTimeout(t);
    }
  }, [msg]);

  const reload = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const d = await apiCall(`/api/backtest/runs?limit=200`);
      setRuns(d.runs || []);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setLoading(false);
    }
    // ── PF_GROUPS ── group finished queue jobs whose label starts "PF:" so a
    // staged portfolio is one click to select once its runs land. Best-effort:
    // the queue list is prunable ("Clear finished"), so this is a convenience,
    // never the source of truth.
    try {
      const st = await apiCall(`/api/backtest/queue/status`);
      const groups = new Map();
      for (const j of st.jobs || []) {
        const label = j.label || "";
        if (!label.startsWith(PF_LABEL_PREFIX) || j.status !== "done" || !j.run_id) continue;
        const name = label.slice(PF_LABEL_PREFIX.length).split("·")[0].trim() || "portfolio";
        if (!groups.has(name)) groups.set(name, []);
        groups.get(name).push({ runId: j.run_id, jobId: j.job_id });
      }
      // ── PF_DELETE ── jobIds kept so "delete portfolio" can remove exactly
      // the group's queue rows (the RUNS are never touched from here).
      setPfGroups([...groups.entries()]
        .map(([name, legs]) => ({
          name,
          ids: [...new Set(legs.map((l) => l.runId))],
          jobIds: [...new Set(legs.map((l) => l.jobId))],
        }))
        .filter((g) => g.ids.length >= 2 && g.ids.length <= MAX_PF));
    } catch { setPfGroups([]); }
  }, [apiCall]);

  useEffect(() => { reload(); }, [reload]);

  const doneRuns = useMemo(() => runs.filter((r) => (r.status || "done") === "done"), [runs]);
  const selRuns = useMemo(() => doneRuns.filter((r) => selected.has(r.run_id)), [doneRuns, selected]);

  /* ── PF_VALIDATE BEGIN ── hard selection rules with exact reasons. */
  const selErrors = useMemo(() => {
    const errs = [];
    if (selRuns.length > MAX_PF) errs.push(`Portfolio supports at most ${MAX_PF} runs — deselect ${selRuns.length - MAX_PF}.`);
    const byStrat = {};
    selRuns.forEach((r) => { (byStrat[r.strategy_id] ||= []).push(r); });
    Object.entries(byStrat).forEach(([sid, rs]) => {
      if (rs.length > 1) errs.push(`One run per strategy — ${sid} is selected ${rs.length} times (two ${STRAT_LABEL[sid] || sid} runs is double sizing, not a portfolio).`);
    });
    if (selRuns.length >= 2) {
      const f = selRuns[0].date_from, t = selRuns[0].date_to;
      const mism = selRuns.filter((r) => r.date_from !== f || r.date_to !== t);
      if (mism.length) {
        errs.push(`Date ranges must match exactly: ${selRuns.map((r) =>
          `${STRAT_LABEL[r.strategy_id] || r.strategy_id} ${r.date_from}→${r.date_to}`).join(" · ")}`);
      }
    }
    return errs;
  }, [selRuns]);
  const composable = selRuns.length >= 2 && selErrors.length === 0;
  /* ── PF_VALIDATE END ── */

  // lazy-load trades for the selected, valid set.
  // ── PF_SWITCH_FIX ── a failed fetch lands in detailErr (NOT in detail as
  // []), so allLoaded stays false, no partial/garbage composition can render,
  // and the error banner below offers Retry.
  useEffect(() => {
    if (!composable) return;
    selRuns.forEach((r) => {
      const rid = r.run_id;
      if (detail[rid] || detailLoading[rid] || detailErr[rid]) return;
      setDetailLoading((s) => ({ ...s, [rid]: true }));
      apiCall(`/api/backtest/runs/${rid}`)
        .then((d) => setDetail((s) => ({ ...s, [rid]: d.trades || [] })))
        .catch((e) => setDetailErr((s) => ({ ...s, [rid]: String(e.message || e) })))
        .finally(() => setDetailLoading((s) => ({ ...s, [rid]: false })));
    });
  }, [composable, selRuns, detail, detailLoading, detailErr, apiCall]);

  const selDetailErrs = useMemo(
    () => selRuns.filter((r) => detailErr[r.run_id])
      .map((r) => ({ run: r, err: detailErr[r.run_id] })),
    [selRuns, detailErr]
  );
  const retryDetails = useCallback(() => {
    // clearing the error entries makes the loader effect refetch them
    setDetailErr((s) => {
      const n = { ...s };
      selRuns.forEach((r) => { delete n[r.run_id]; });
      return n;
    });
  }, [selRuns]);

  // ── PF_SWITCH_FIX ── stable signature of the composed selection; keys the
  // whole results block so SWITCHING portfolios fully remounts it — no state
  // (charts, refs, anything) can leak from the previous composition.
  const selKey = useMemo(
    () => selRuns.map((r) => r.run_id).sort().join("|"),
    [selRuns]
  );

  const allLoaded = composable && selRuns.every((r) => Array.isArray(detail[r.run_id]));
  const pf = useMemo(() => {
    if (!allLoaded) return null;
    return composePortfolio(selRuns.map((r) => ({ run: r, trades: detail[r.run_id] })));
  }, [allLoaded, selRuns, detail]);

  // ── PF_RESULTS_FOCUS ── bring the results into view on (re)composition.
  // scrollIntoView is fine in the Tauri webview (only confirm/alert/open are
  // blocked); the tiny delay lets the results block mount first.
  useEffect(() => {
    const has = !!pf;
    if (has && !hadPf.current) {
      setTimeout(() => resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
    }
    hadPf.current = has;
  }, [pf]);

  const toggleSelect = useCallback((rid) => {
    setActivePfName(null);   // manual change → no longer "the" named portfolio
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(rid) ? next.delete(rid) : next.add(rid);
      return next;
    });
  }, []);

  /* ── PF_SWITCH_FIX BEGIN ── chip selection, done properly. The old handler
     selected against whatever runs list was fetched AT MOUNT — a portfolio
     that finished afterwards (or whose run fell past the fetch limit / was
     deleted in Compare Runs) resolved partially or not at all, and the view
     kept showing the previous composition with no explanation. Now: re-fetch
     the runs list FIRST, resolve every leg against the fresh list, and either
     compose fully or refuse loudly with the exact gap. Never a silent subset. */
  const selectPfGroup = useCallback(async (g) => {
    setMsg({ kind: "info", text: `Loading "${g.name}"…` });
    let fresh = runs;
    try {
      const d = await apiCall(`/api/backtest/runs?limit=200`);
      fresh = d.runs || [];
      setRuns(fresh);
    } catch { /* offline blip — fall back to the in-memory list */ }
    const have = new Set(fresh.filter((r) => (r.status || "done") === "done").map((r) => r.run_id));
    const missing = g.ids.filter((id) => !have.has(id));
    if (missing.length) {
      setMsg({ kind: "err", text: `"${g.name}": ${missing.length} of ${g.ids.length} runs can't be found — deleted in Compare Runs, or older than the latest 200 runs. Composing a partial portfolio would be misleading; re-stage the missing leg.` });
      return;
    }
    setMsg(null);
    setSelected(new Set(g.ids));
    setActivePfName(g.name);
    setPickerOpen(false);
    setTab("overview");
  }, [apiCall, runs]);
  /* ── PF_SWITCH_FIX END ── */

  /* ── PF_DELETE BEGIN ── delete a named portfolio = delete its PF-labeled
     QUEUE ROWS (the grouping source; the chip disappears). Uses the status-
     aware DELETE /queue/{job_id} (QUEUE_ROW_DELETE backend) — done rows are
     hard-deleted. The backtest RUNS are deliberately untouched: they remain
     in Compare Runs / the picker table, and any current composition on screen
     keeps working (it holds the trades already). No window.confirm (blocked
     in the Tauri webview); queue rows are disposable metadata, same direct-
     delete philosophy as run deletion in Compare Runs. */
  const deletePfGroup = useCallback(async (g) => {
    setMsg({ kind: "info", text: `Deleting portfolio "${g.name}"…` });
    let ok = 0, fail = 0;
    for (const jid of g.jobIds) {
      try { await apiCall(`/api/backtest/queue/${jid}`, { method: "DELETE" }); ok++; }
      catch { fail++; }
    }
    if (activePfName === g.name) setActivePfName(null);
    if (fail) setMsg({ kind: "err", text: `"${g.name}": deleted ${ok}, failed ${fail} queue row(s).` });
    else setMsg({ kind: "ok", text: `Portfolio "${g.name}" removed (${ok} queue rows). The backtest runs are kept — delete those in Compare Runs if you want them gone too.` });
    await reload();
  }, [apiCall, reload, activePfName]);
  /* ── PF_DELETE END ── */

  /* ── PF_LAUNCH BEGIN ── stage a portfolio through the EXISTING queue: one
     job per strategy, shared dates, per-strategy config from buildConfig(sid)
     (the same builder the Run/Queue paths use, so a staged portfolio run is
     byte-identical to a manual run of that strategy). Label convention
     "PF:<name> · <strategy>" — the queue repo's label field, no schema change. */
  const toggleLaunchStrat = useCallback((sid) => {
    setPfStrats((prev) => {
      const next = new Set(prev);
      if (next.has(sid)) next.delete(sid);
      else if (next.size < MAX_PF) next.add(sid);
      return next;
    });
  }, []);

  const enqueuePortfolio = useCallback(async (alsoStart) => {
    if (pfStrats.size < 2) { setMsg({ kind: "err", text: "Pick at least 2 strategies for the portfolio." }); return; }
    if (!pfFrom || !pfTo) { setMsg({ kind: "err", text: "Pick the shared date range first." }); return; }
    setEnqueueing(true);
    setMsg({ kind: "info", text: "Staging portfolio runs…" });
    const name = (pfName || "portfolio").trim();
    const staged = [], failed = [];
    for (const sid of LAUNCHABLE.filter((s) => pfStrats.has(s))) {
      try {
        await apiCall("/api/backtest/queue/enqueue", {
          method: "POST",
          body: JSON.stringify({
            strategy_id: sid, underlying: "NIFTY",
            date_from: pfFrom, date_to: pfTo,
            config_override: buildConfig(sid),
            label: `${PF_LABEL_PREFIX}${name} · ${sid}`,
          }),
        });
        staged.push(sid);
      } catch (e) {
        failed.push(`${sid}: ${String(e.message || e)}`);
      }
    }
    let started = false;
    if (alsoStart && staged.length && !failed.length) {
      try { await apiCall("/api/backtest/queue/start", { method: "POST" }); started = true; }
      catch { /* queue may already be running — the Queue tab shows state */ }
    }
    setEnqueueing(false);
    if (failed.length) setMsg({ kind: "err", text: `Staged ${staged.length}, failed ${failed.length}. First error: ${failed[0]}` });
    else setMsg({ kind: "ok", text: `Staged ${staged.length} run(s) as "${name}"${started ? " and started the queue" : " — start them from the Queue tab"}. They'll appear here when done.` });
  }, [apiCall, buildConfig, pfStrats, pfFrom, pfTo, pfName]);
  /* ── PF_LAUNCH END ── */

  const money = (v) => (v == null ? "—" : `${v >= 0 ? "+" : "-"}${fmtInr(Math.abs(v))}`);
  const chipBtn = (active, disabled) => ({
    padding: "6px 12px", borderRadius: 6, cursor: disabled ? "not-allowed" : "pointer",
    fontSize: 12, fontWeight: 700, opacity: disabled ? 0.5 : 1,
    border: `1px solid ${active ? c.primary : c.border.light}`,
    background: active ? c.primaryBg : c.bg.secondary,
    color: active ? c.primary : c.text.secondary,
  });
  const smallBtn = (variant) => ({
    padding: "7px 14px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 12, fontWeight: 600,
    background: variant === "primary" ? c.primary : c.bg.tertiary,
    color: variant === "primary" ? "#fff" : c.text.primary,
  });
  const inputStyle = {
    padding: "7px 10px", borderRadius: 6, border: `1px solid ${c.border.light}`,
    background: c.bg.secondary, color: c.text.primary, fontSize: 13, outline: "none",
    fontFamily: "'Inter', sans-serif",
  };
  const tabBtn = (k) => ({
    padding: "7px 16px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 13, fontWeight: 600,
    background: tab === k ? c.primary : "transparent",
    color: tab === k ? "#fff" : c.text.muted,
  });

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

  return (
    <div ref={wrapRef}>
      {/* ── LAUNCH PANEL ── */}
      <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.lg }}>
        <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap" }}>
          <button style={smallBtn("default")} onClick={() => setLaunchOpen((v) => !v)}>
            {launchOpen ? "▾" : "▸"} Stage a portfolio
          </button>
          <span style={{ fontSize: 12, color: c.text.muted }}>
            Enqueue 2–3 strategies over one shared date range (each with its own current form params), then compose the finished runs below.
          </span>
          <div style={{ marginLeft: "auto", display: "flex", gap: spacing.sm, alignItems: "center" }}>
            {msg && (
              <span style={{ fontSize: 12, fontWeight: 600,
                color: msg.kind === "ok" ? c.profit : msg.kind === "err" ? c.loss : c.text.muted }}>
                {msg.text}
              </span>
            )}
            <button style={smallBtn("default")} onClick={reload}>↻ Refresh</button>
          </div>
        </div>
        {launchOpen && (
          <div style={{ marginTop: spacing.md, display: "flex", gap: spacing.md, alignItems: "flex-end", flexWrap: "wrap" }}>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ ...typography.label, color: c.text.muted, fontSize: 11 }}>Portfolio name</span>
              <input style={{ ...inputStyle, minWidth: 160 }} placeholder="e.g. v3v5-blend"
                value={pfName} onChange={(e) => setPfName(e.target.value)} />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ ...typography.label, color: c.text.muted, fontSize: 11 }}>Date from</span>
              <input type="date" style={inputStyle} value={pfFrom} onChange={(e) => setPfFrom(e.target.value)} />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ ...typography.label, color: c.text.muted, fontSize: 11 }}>Date to</span>
              <input type="date" style={inputStyle} value={pfTo} onChange={(e) => setPfTo(e.target.value)} />
            </label>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ ...typography.label, color: c.text.muted, fontSize: 11 }}>Strategies (2–{MAX_PF})</span>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {LAUNCHABLE.map((sid) => {
                  const on = pfStrats.has(sid);
                  const full = !on && pfStrats.size >= MAX_PF;
                  return (
                    <button key={sid} style={chipBtn(on, full)} disabled={full}
                      onClick={() => toggleLaunchStrat(sid)}
                      title={full ? `Max ${MAX_PF} strategies` : sid}>
                      {STRAT_LABEL[sid]}
                    </button>
                  );
                })}
              </div>
            </div>
            <button style={smallBtn("default")} disabled={enqueueing} onClick={() => enqueuePortfolio(false)}>
              Enqueue
            </button>
            <button style={smallBtn("primary")} disabled={enqueueing} onClick={() => enqueuePortfolio(true)}>
              {enqueueing ? "Staging…" : "Enqueue & start"}
            </button>
          </div>
        )}
        {pfGroups.length > 0 && (
          <div style={{ marginTop: spacing.md, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 11, color: c.text.muted }}>Finished portfolios:</span>
            {pfGroups.map((g) => (
              <span key={g.name} style={{ display: "inline-flex", alignItems: "stretch" }}>
                <button
                  style={{ ...chipBtn(activePfName === g.name, false),
                    borderTopRightRadius: 0, borderBottomRightRadius: 0 }}
                  onClick={() => selectPfGroup(g)}
                  title={`Compose the ${g.ids.length} runs staged as "${g.name}"`}>
                  {g.name} ({g.ids.length})
                </button>
                {/* ── PF_DELETE ── removes the queue rows only; runs are kept */}
                <button
                  style={{ ...chipBtn(false, false), borderTopLeftRadius: 0,
                    borderBottomLeftRadius: 0, borderLeft: "none", color: c.loss, padding: "6px 9px" }}
                  onClick={() => deletePfGroup(g)}
                  title={`Delete portfolio "${g.name}" — removes its queue rows; the backtest runs are NOT deleted`}>
                  ✕
                </button>
              </span>
            ))}
          </div>
        )}
      </Card>

      {/* ── PF_RESULTS_FOCUS ── collapsed picker strip (results-first mode) */}
      {!pickerOpen && (
        <Card elevated style={{ padding: spacing.md, marginBottom: spacing.lg }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <span style={{ ...typography.label, color: c.text.muted }}>
              Composing{activePfName ? <> "<b style={{ color: c.text.secondary }}>{activePfName}</b>"</> : ""}:
            </span>
            {selRuns.map((r) => (
              <span key={r.run_id} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <StratChip sid={r.strategy_id} c={c} />
                <span style={{ fontSize: 10, ...typography.mono, color: c.text.muted }}>
                  {r.date_from} → {r.date_to}
                </span>
              </span>
            ))}
            <button style={{ ...smallBtn("default"), marginLeft: "auto" }} onClick={() => setPickerOpen(true)}>
              Change runs
            </button>
          </div>
        </Card>
      )}

      {/* ── RUN PICKER ── */}
      {pickerOpen && (
      <Card style={{ overflowX: "auto", marginBottom: spacing.lg }}>
        <table style={{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}>
          <thead style={{ background: c.bg.tertiary }}>
            <tr>
              {["", "Strat", "Period", "Params", "Net", "Max DD", "Trades", ""].map((h, i) => (
                <th key={i} style={{ padding: "9px 10px", textAlign: i >= 4 && i <= 6 ? "right" : "left",
                  ...typography.label, color: c.text.muted, borderBottom: `2px solid ${c.border.light}`, whiteSpace: "nowrap" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {doneRuns.map((r, i) => {
              const s = r.summary || {};
              const isSel = selected.has(r.run_id);
              const params = describeConfig(r.config).map(([k, v]) => `${k} ${v}`).join(" · ");
              return (
                <tr key={r.run_id}
                  style={{ background: isSel ? c.primaryBg : i % 2 ? c.bg.secondary : c.bg.primary,
                    borderTop: `1px solid ${c.border.dark}`, cursor: "pointer" }}
                  onClick={() => toggleSelect(r.run_id)}>
                  <td style={{ padding: "8px 10px", textAlign: "center", width: 32 }}>
                    <input type="checkbox" checked={isSel} readOnly />
                  </td>
                  <td style={{ padding: "8px 10px" }}><StratChip sid={r.strategy_id} c={c} /></td>
                  <td style={{ padding: "8px 10px", ...typography.mono, fontSize: 11, color: c.text.tertiary, whiteSpace: "nowrap" }}>
                    {r.date_from} → {r.date_to}
                  </td>
                  <td style={{ padding: "8px 10px", fontSize: 11, color: c.text.secondary, maxWidth: 380,
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={params}>
                    {params || "—"}
                  </td>
                  <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, fontWeight: 700, ...pnlStyle(s.net_pnl) }}>
                    {money(s.net_pnl)}
                  </td>
                  <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, color: c.loss }}>
                    {s.max_drawdown != null ? fmtInr(s.max_drawdown) : "—"}
                  </td>
                  <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono }}>{s.total_trades ?? "—"}</td>
                  <td style={{ padding: "8px 10px", textAlign: "right" }}>
                    <button onClick={(e) => { e.stopPropagation(); onOpenRun?.(r.run_id); }}
                      style={{ border: "none", background: "transparent", cursor: "pointer", color: c.primary, fontSize: 12, fontWeight: 600 }}>
                      Open
                    </button>
                  </td>
                </tr>
              );
            })}
            {!doneRuns.length && (
              <tr><td colSpan={8} style={{ padding: "32px 0", textAlign: "center", color: c.text.muted, fontSize: 13 }}>
                No finished runs yet — stage a portfolio above or run backtests from the Run tab.
              </td></tr>
            )}
          </tbody>
        </table>
      </Card>
      )}

      {/* ── VALIDATION / STATE BANNERS ── */}
      {selErrors.length > 0 && (
        <Card elevated style={{ padding: spacing.md, marginBottom: spacing.lg, borderColor: c.loss }}>
          {selErrors.map((e, i) => (
            <div key={i} style={{ fontSize: 12, color: c.loss, fontWeight: 600, marginBottom: i < selErrors.length - 1 ? 6 : 0 }}>{e}</div>
          ))}
        </Card>
      )}
      {selRuns.length === 1 && (
        <Card elevated style={{ padding: spacing.md, marginBottom: spacing.lg }}>
          <div style={{ fontSize: 12, color: c.text.muted }}>Select one or two more runs (different strategies, same date range) to compose a portfolio.</div>
        </Card>
      )}
      {composable && !allLoaded && selDetailErrs.length === 0 && (
        <Card elevated style={{ padding: "32px 0", marginBottom: spacing.lg, textAlign: "center", color: c.text.muted, fontSize: 13 }}>
          Loading trades for {selRuns.length} runs…
        </Card>
      )}
      {/* ── PF_SWITCH_FIX ── failed trade fetches surface here instead of
          silently composing an empty leg or leaving the previous portfolio
          on screen with no explanation. */}
      {composable && selDetailErrs.length > 0 && (
        <Card elevated style={{ padding: spacing.md, marginBottom: spacing.lg, borderColor: c.loss }}>
          {selDetailErrs.map(({ run, err: e2 }) => (
            <div key={run.run_id} style={{ fontSize: 12, color: c.loss, fontWeight: 600, marginBottom: 6 }}>
              Couldn't load trades for {STRAT_LABEL[run.strategy_id] || run.strategy_id} {run.run_id.slice(0, 8)}: {e2}
            </div>
          ))}
          <button style={smallBtn("default")} onClick={retryDetails}>Retry</button>
        </Card>
      )}

      {/* ── PORTFOLIO RESULTS ── keyed by the selection signature so switching
          portfolios REMOUNTS everything (PF_SWITCH_FIX) */}
      {pf && (
        <div key={selKey} ref={resultsRef} style={{ scrollMarginTop: 12 }}>
          {/* header: what's composed */}
          <Card elevated style={{ padding: spacing.md, marginBottom: spacing.lg }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <span style={{ ...typography.label, color: c.text.muted }}>
                Portfolio{activePfName ? <> · <b style={{ color: c.text.secondary }}>{activePfName}</b></> : null} · {selRuns[0].date_from} → {selRuns[0].date_to}
              </span>
              {pf.perStrat.map((s) => (
                <span key={s.run.run_id} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <StratChip sid={s.run.strategy_id} c={c} />
                  <span style={{ fontSize: 10, ...typography.mono, color: c.text.muted }}>{s.run.run_id.slice(0, 8)}</span>
                </span>
              ))}
            </div>
          </Card>

          <div style={{ display: "flex", gap: 4, marginBottom: spacing.lg, background: c.bg.secondary,
            padding: 4, borderRadius: 8, border: `1px solid ${c.border.light}`, width: "fit-content", flexWrap: "wrap" }}>
            {[["overview", "Overview"], ["equity", "Equity"], ["correlation", "Correlation"],
              ["exposure", "Exposure"], ["monthly", "Monthly"]].map(([k, label]) => (
              <button key={k} style={tabBtn(k)} onClick={() => setTab(k)}>{label}</button>
            ))}
          </div>

          {/* ── OVERVIEW ── */}
          {tab === "overview" && (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: spacing.md, marginBottom: spacing.lg }}>
                <KpiTile label="Combined Net P&L"
                  value={money(pf.combinedNet)}
                  good={pf.combinedNet > 0} bad={pf.combinedNet < 0}
                  sub="sum of all strategies (net)" />
                <KpiTile label="Combined Max DD"
                  value={fmtInr(pf.combinedMaxDD)}
                  bad
                  sub="on the merged exit-realized curve" />
                <KpiTile label="DD reduction"
                  value={pf.ddReduction == null ? "—" : `${pf.ddReduction.toFixed(1)}%`}
                  good={pf.ddReduction > 0} bad={pf.ddReduction != null && pf.ddReduction <= 0}
                  sub={`vs Σ individual DDs (${fmtInr(pf.sumIndDD)})`} />
                <KpiTile label="Return ÷ Max DD"
                  value={pf.returnToDD === Infinity ? "∞" : pf.returnToDD.toFixed(2)}
                  good={pf.returnToDD >= 2} bad={pf.returnToDD < 1}
                  sub={`best single: ${(() => {
                    const best = Math.max(...pf.perStrat.map((s) => (s.maxDD > 0 ? s.net / s.maxDD : (s.net > 0 ? Infinity : 0))));
                    return best === Infinity ? "∞" : best.toFixed(2);
                  })()}`} />
              </div>

              <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.lg }}>
                <div style={{ ...typography.label, color: c.text.muted, marginBottom: spacing.md }}>Per-strategy contribution</div>
                <table style={{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}>
                  <thead style={{ background: c.bg.tertiary }}>
                    <tr>
                      {["Strategy", "Net P&L", "Share of net", "Own Max DD", "Return ÷ DD", "Trades", "P&L during combined DD"].map((h, i) => (
                        <th key={h} style={{ padding: "9px 10px", textAlign: i === 0 ? "left" : "right",
                          ...typography.label, color: c.text.muted, borderBottom: `2px solid ${c.border.light}`, whiteSpace: "nowrap" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {pf.perStrat.map((s, i) => {
                      const rdd = s.maxDD > 0 ? s.net / s.maxDD : (s.net > 0 ? Infinity : 0);
                      const share = pf.combinedNet !== 0 ? (s.net / pf.combinedNet) * 100 : null;
                      return (
                        <tr key={s.run.run_id} style={{ borderTop: `1px solid ${c.border.dark}` }}>
                          <td style={{ padding: "9px 10px" }}><StratChip sid={s.run.strategy_id} c={c} /></td>
                          <td style={{ padding: "9px 10px", textAlign: "right", ...typography.mono, fontWeight: 700, ...pnlStyle(s.net) }}>{money(s.net)}</td>
                          <td style={{ padding: "9px 10px", textAlign: "right", ...typography.mono, color: c.text.secondary }}>
                            {share == null ? "—" : `${share.toFixed(0)}%`}
                          </td>
                          <td style={{ padding: "9px 10px", textAlign: "right", ...typography.mono, color: c.loss }}>{fmtInr(s.maxDD)}</td>
                          <td style={{ padding: "9px 10px", textAlign: "right", ...typography.mono }}>{rdd === Infinity ? "∞" : rdd.toFixed(2)}</td>
                          <td style={{ padding: "9px 10px", textAlign: "right", ...typography.mono }}>{s.tradeCount}</td>
                          <td style={{ padding: "9px 10px", textAlign: "right", ...typography.mono, ...pnlStyle(pf.ddContrib[i]) }}>
                            {money(pf.ddContrib[i])}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <div style={{ marginTop: spacing.md, fontSize: 11, color: c.text.tertiary, lineHeight: 1.5 }}>
                  "P&L during combined DD" attributes the portfolio's worst peak-to-trough window
                  {pf.ddPeakTs ? ` (${fmtTs(pf.ddPeakTs)} → ${fmtTs(pf.ddTroughTs)})` : ""} to each strategy — the most negative
                  cell is the strategy that dug the hole; a positive cell was cushioning it.
                  Per-strategy Max DD here is exit-realized, so it can differ slightly from the run's own Advanced tab (entry-stepped).
                </div>
              </Card>
            </>
          )}

          {/* ── EQUITY ── */}
          {tab === "equity" && (
            <Card elevated style={{ padding: 16 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                <span style={{ fontSize: 14, fontWeight: 600 }}>Combined equity (exit-realized, real time axis)</span>
                <span style={{ fontSize: 12, fontWeight: 700, ...pnlStyle(pf.combinedNet) }}>
                  End: {money(pf.combinedNet)}
                </span>
              </div>
              <PortfolioEquityChart pf={pf} width={w} c={c} fmtInr={fmtInr} />
            </Card>
          )}

          {/* ── CORRELATION ── */}
          {tab === "correlation" && (
            <>
              <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.lg }}>
                <div style={{ ...typography.label, color: c.text.muted, marginBottom: spacing.md }}>
                  Daily net-P&L correlation (Pearson)
                </div>
                <table style={{ borderCollapse: "collapse", ...typography.bodyMedium }}>
                  <thead>
                    <tr>
                      <th style={{ padding: "8px 12px" }} />
                      {pf.perStrat.map((s) => (
                        <th key={s.run.run_id} style={{ padding: "8px 12px" }}><StratChip sid={s.run.strategy_id} c={c} /></th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {pf.perStrat.map((si, i) => (
                      <tr key={si.run.run_id}>
                        <td style={{ padding: "8px 12px" }}><StratChip sid={si.run.strategy_id} c={c} /></td>
                        {pf.perStrat.map((sj, j) => {
                          const r = pf.corr[i][j];
                          // diversification read: low/negative r = green, high positive = red
                          const col = i === j ? c.text.muted
                            : r == null ? c.text.muted
                            : r <= 0.2 ? c.profit
                            : r <= 0.5 ? c.warning
                            : c.loss;
                          return (
                            <td key={j} style={{ padding: "8px 12px", textAlign: "center", ...typography.mono,
                              fontWeight: i === j ? 400 : 800, color: col }}>
                              {r == null ? "—" : r.toFixed(2)}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div style={{ marginTop: spacing.md, fontSize: 11, color: c.text.tertiary, lineHeight: 1.5 }}>
                  Computed over the union of the pair's realization days (a day only one side traded counts as 0 for the other).
                  Low or negative correlation means their loss days don't coincide — that's structural DD reduction.
                  High positive correlation means the reduction you see may be luck of the sample.
                </div>
              </Card>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: spacing.md, marginBottom: spacing.lg }}>
                <KpiTile label="Rescued days" value={String(pf.rescuedDays)}
                  good={pf.rescuedDays > 0}
                  sub="portfolio green while ≥1 strategy red" />
                <KpiTile label="Cluster-loss days" value={String(pf.clusterDays)}
                  bad={pf.clusterDays > 0}
                  sub="≥2 strategies red the same day" />
                <KpiTile label="Portfolio red days" value={`${pf.redDays} / ${pf.days.length}`}
                  sub={`${pf.greenDays} green · ${pf.days.length - pf.greenDays - pf.redDays} flat`} />
                <KpiTile label="Worst combined day"
                  value={pf.worstCombinedDay ? money(pf.worstCombinedDay.total) : "—"}
                  bad
                  sub={pf.worstCombinedDay ? fmtDayLabel(pf.worstCombinedDay.day) : ""} />
              </div>

              <Card elevated style={{ padding: spacing.lg }}>
                <div style={{ ...typography.label, color: c.text.muted, marginBottom: spacing.md }}>Worst single days — do losses cluster?</div>
                <table style={{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}>
                  <thead style={{ background: c.bg.tertiary }}>
                    <tr>
                      {["Strategy", "Worst own day", "On that date", "Portfolio total that day"].map((h, i) => (
                        <th key={h} style={{ padding: "9px 10px", textAlign: i === 0 ? "left" : "right",
                          ...typography.label, color: c.text.muted, borderBottom: `2px solid ${c.border.light}` }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {pf.perStrat.map((s, i) => {
                      const wi = pf.worstIndividual[i];
                      const dayRow = wi ? pf.days.find((d) => d.day === wi.k) : null;
                      return (
                        <tr key={s.run.run_id} style={{ borderTop: `1px solid ${c.border.dark}` }}>
                          <td style={{ padding: "9px 10px" }}><StratChip sid={s.run.strategy_id} c={c} /></td>
                          <td style={{ padding: "9px 10px", textAlign: "right", ...typography.mono, color: c.loss, fontWeight: 700 }}>
                            {wi ? money(wi.v) : "—"}
                          </td>
                          <td style={{ padding: "9px 10px", textAlign: "right", ...typography.mono, fontSize: 11, color: c.text.tertiary }}>
                            {wi ? fmtDayLabel(wi.k) : "—"}
                          </td>
                          <td style={{ padding: "9px 10px", textAlign: "right", ...typography.mono, fontWeight: 700,
                            ...pnlStyle(dayRow ? dayRow.total : 0) }}>
                            {dayRow ? money(dayRow.total) : "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <div style={{ marginTop: spacing.md, fontSize: 11, color: c.text.tertiary, lineHeight: 1.5 }}>
                  If the portfolio total on a strategy's worst day is much better than that strategy's own loss, the other
                  strategies were absorbing it. If it's similar or worse, the bad days coincide — the diversification is weaker
                  than the correlation number alone suggests.
                </div>
              </Card>
            </>
          )}

          {/* ── EXPOSURE ── */}
          {tab === "exposure" && (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: spacing.md, marginBottom: spacing.lg }}>
                <KpiTile label="Max concurrent trades" value={String(pf.exposure.maxConcurrent)}
                  sub="across all strategies at once" />
                <KpiTile label="Max strategies in-trade" value={`${pf.exposure.maxStrats} / ${pf.perStrat.length}`}
                  sub="simultaneously holding positions" />
                <KpiTile label="Peak premium notional" value={fmtInr(pf.exposure.peakNotional)}
                  sub="Σ entry price × qty of open trades" />
                <KpiTile label="Overlap of in-trade time" value={`${pf.exposure.overlapPct.toFixed(1)}%`}
                  sub={`${fmtDurS(pf.exposure.tMulti)} of ${fmtDurS(pf.exposure.tAny)} with ≥2 strategies open`} />
              </div>
              <Card elevated style={{ padding: spacing.lg }}>
                <div style={{ fontSize: 12, color: c.text.secondary, lineHeight: 1.6 }}>
                  Composition assumes you can fund every strategy simultaneously — this tab is the reality check.
                  Premium notional is the actual capital outlay for LONG option legs (V3/V4 hedge buys, V5, HA);
                  for SHORT strategies (V1, HA Sell) the true requirement is exchange margin, which is substantially larger
                  than premium — treat their contribution here as a floor, not the requirement.
                  A low time-overlap means your effective capital requirement is well below the naive sum of each strategy's peak.
                  Qty is estimated as lots × lot size ({LOT_SIZE.NIFTY} NIFTY / {LOT_SIZE.BANKNIFTY} BANKNIFTY) from each run's config.
                </div>
              </Card>
            </>
          )}

          {/* ── MONTHLY ── */}
          {tab === "monthly" && (
            <Card elevated style={{ padding: spacing.lg, overflowX: "auto" }}>
              <div style={{ ...typography.label, color: c.text.muted, marginBottom: spacing.md }}>Monthly net P&L — per strategy and combined</div>
              <table style={{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}>
                <thead style={{ background: c.bg.tertiary }}>
                  <tr>
                    <th style={{ padding: "9px 10px", textAlign: "left", ...typography.label, color: c.text.muted, borderBottom: `2px solid ${c.border.light}` }}>Month</th>
                    {pf.perStrat.map((s) => (
                      <th key={s.run.run_id} style={{ padding: "9px 10px", textAlign: "right", borderBottom: `2px solid ${c.border.light}` }}>
                        <StratChip sid={s.run.strategy_id} c={c} />
                      </th>
                    ))}
                    <th style={{ padding: "9px 10px", textAlign: "right", ...typography.label, color: c.text.primary, borderBottom: `2px solid ${c.border.light}` }}>Combined</th>
                  </tr>
                </thead>
                <tbody>
                  {pf.monthly.map((m) => (
                    <tr key={m.month} style={{ borderTop: `1px solid ${c.border.dark}` }}>
                      <td style={{ padding: "8px 10px", fontSize: 12, color: c.text.secondary, whiteSpace: "nowrap" }}>{fmtMonthLabel(m.month)}</td>
                      {m.per.map((p, i) => (
                        <td key={i} style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, ...pnlStyle(p) }}>
                          {p === 0 ? <span style={{ color: c.text.muted }}>—</span> : money(p)}
                        </td>
                      ))}
                      <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, fontWeight: 800, ...pnlStyle(m.total) }}>
                        {money(m.total)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div style={{ marginTop: spacing.md, fontSize: 11, color: c.text.tertiary }}>
                Months where one strategy's red is covered by another's green are the diversification working; months where
                every column is red are the risk that remains.
              </div>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}