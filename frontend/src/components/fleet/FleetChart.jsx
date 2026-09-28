// frontend/src/components/fleet/FleetChart.jsx — ── DASH_MODERN_20260925 ──
// ── FLEET_CHART_COLLAPSE_20260925 ── collapsible, state remembered.
// ── FLEET_CHART_V2_20260928 ── two views, both usable with a full fleet:
//   Fleet  the book totals (Live / Paper) drawn alone; strategies are OVERLAYS
//          you opt into from the legend (multi-select, remembered), so the
//          default is one line per book instead of twelve
//   Grid   small multiples — one small panel per (strategy, book) row, own
//          y-scale, shared session x-axis, sorted by |MTM|; click a panel to
//          focus that strategy's panel below
// Series come from /api/fleet/today ([[minute, mtm], …]). Theme tokens only.

import { useEffect, useMemo, useRef, useState } from "react";
import { colors, spacing, typography } from "../../tokens";
import { MARKET_START_MIN, FNO_END_MIN } from "../../marketSession";
import { fmtInr, hhmm, valueAt } from "./fleetFormat";

const P = { l: 60, r: 14, t: 16, b: 22 };
const X_TICKS = [MARKET_START_MIN, 600, 660, 720, 780, 840, 900, FNO_END_MIN];
const VIEW_KEY = "scalp.dashboard.fleetChartView";        // "fleet" | "grid"
const OVERLAY_KEY = "scalp.dashboard.fleetChartOverlays";  // JSON array of series keys
const MONO = "'JetBrains Mono', monospace";
const isBook = (s) => s.key === "__LIVE__" || s.key === "__PAPER__";

function yStep(span) {
  return [250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 25000, 50000, 100000].find((s) => span / s <= 7) || 200000;
}
function fmtTick(v) {
  if (v === 0) return "0";
  const a = Math.abs(v), s = v > 0 ? "+" : "−";
  return a >= 1000 ? `${s}${(a / 1000).toFixed(a % 1000 ? 1 : 0)}k` : `${s}${a}`;
}
function lastOf(s) { return Array.isArray(s.path) && s.path.length ? s.path[s.path.length - 1][1] : (s.value ?? null); }
function tone(v) { return v > 0 ? colors.profit : v < 0 ? colors.loss : colors.text.muted; }
function readJson(key, fallback) { try { const v = JSON.parse(localStorage.getItem(key)); return v == null ? fallback : v; } catch { return fallback; } }

/* ───────────────────────── Fleet view ───────────────────────── */
function FleetPlot({ series, nowMin, height, collapsed }) {
  const hostRef = useRef(null);
  const [width, setWidth] = useState(1200);
  const [hover, setHover] = useState(null);
  useEffect(() => {
    const el = hostRef.current;
    if (!el) return undefined;
    const measure = () => setWidth(Math.max(320, el.clientWidth || 1200));
    measure();
    if (typeof ResizeObserver === "undefined") { window.addEventListener("resize", measure); return () => window.removeEventListener("resize", measure); }
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [collapsed]);

  const visible = series.filter((s) => Array.isArray(s.path) && s.path.length > 0);
  const { lo, hi } = useMemo(() => {
    let lo = 0, hi = 0;
    visible.forEach((s) => s.path.forEach(([, v]) => { if (v < lo) lo = v; if (v > hi) hi = v; }));
    const pad = (hi - lo) * 0.14 || 500;
    return { lo: lo - pad, hi: hi + pad };
  }, [visible]);
  const plotW = width - P.l - P.r, plotH = height - P.t - P.b;
  const x = (m) => P.l + ((m - MARKET_START_MIN) / (FNO_END_MIN - MARKET_START_MIN)) * plotW;
  const y = (v) => P.t + ((hi - v) / (hi - lo)) * plotH;
  const step = yStep(hi - lo);
  const yTicks = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) yTicks.push(v);
  const nowX = x(Math.min(Math.max(nowMin, MARKET_START_MIN), FNO_END_MIN));
  const empty = visible.length === 0;
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - r.left;
    let m = Math.round(MARKET_START_MIN + ((px - P.l) / plotW) * (FNO_END_MIN - MARKET_START_MIN));
    m = Math.max(MARKET_START_MIN, Math.min(FNO_END_MIN, m));
    setHover({ m, px, py: e.clientY - r.top });
  };
  return (
    <div ref={hostRef} style={{ position: "relative", height }}>
      <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} style={{ display: "block", overflow: "visible" }}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={P.l} x2={width - P.r} y1={y(v)} y2={y(v)} style={{ stroke: v === 0 ? colors.border.light : colors.border.dark }} strokeDasharray={v === 0 ? "" : "2 4"} />
            <text x={P.l - 8} y={y(v) + 3.5} textAnchor="end" style={{ fill: colors.text.muted, fontSize: 10.5, fontFamily: MONO }}>{fmtTick(v)}</text>
          </g>
        ))}
        {X_TICKS.map((m) => (
          <text key={m} x={x(m)} y={height - 6} textAnchor={m === MARKET_START_MIN ? "start" : m === FNO_END_MIN ? "end" : "middle"}
            style={{ fill: colors.text.muted, fontSize: 10.5, fontFamily: MONO }}>{hhmm(m)}</text>
        ))}
        {nowMin < FNO_END_MIN && (
          <g>
            <rect x={nowX} y={P.t} width={Math.max(0, x(FNO_END_MIN) - nowX)} height={plotH} style={{ fill: colors.text.primary, fillOpacity: 0.025 }} />
            <line x1={nowX} x2={nowX} y1={P.t - 4} y2={height - P.b} style={{ stroke: colors.text.muted }} strokeDasharray="3 3" />
            <text x={nowX + 5} y={P.t + 4} style={{ fill: colors.text.muted, fontSize: 10.5, fontFamily: MONO }}>now {hhmm(nowMin)}</text>
          </g>
        )}
        {visible.map((s) => {
          const d = s.path.map(([m, v], i) => `${i ? "L" : "M"}${x(m).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
          const [lm, lv] = s.path[s.path.length - 1];
          return (
            <g key={s.key}>
              {s.fill && <path d={`${d} L${x(lm).toFixed(1)} ${y(0)} L${x(s.path[0][0]).toFixed(1)} ${y(0)} Z`} fill={s.color} fillOpacity="0.07" />}
              <path d={d} fill="none" stroke={s.color} strokeWidth={s.width || 1.4} strokeDasharray={s.dash ? "6 4" : ""} strokeLinejoin="round" strokeLinecap="round" />
              <circle cx={x(lm)} cy={y(lv)} r={s.width > 2 ? 3.2 : 2.2} fill={s.color} />
            </g>
          );
        })}
        {hover && <line x1={x(hover.m)} x2={x(hover.m)} y1={P.t} y2={height - P.b} style={{ stroke: colors.text.primary, strokeOpacity: 0.35 }} />}
      </svg>
      {empty && (
        <div style={{ position: "absolute", inset: `${P.t}px ${P.r}px ${P.b}px ${P.l}px`, display: "flex", alignItems: "center", justifyContent: "center", color: colors.text.muted, fontSize: 12.5, pointerEvents: "none" }}>
          The MTM path builds one point a minute while the market is open.
        </div>
      )}
      {hover && !empty && (() => {
        const rows = visible.map((s) => [s, valueAt(s.path, hover.m)]).filter(([, v]) => v != null);
        if (!rows.length) return null;
        const right = hover.px > width * 0.62;
        return (
          <div style={{ position: "absolute", top: Math.max(P.t, hover.py - 10), [right ? "right" : "left"]: right ? width - hover.px + 14 : hover.px + 14,
            background: colors.bg.tertiary, border: `1px solid ${colors.border.light}`, borderRadius: 6, padding: "6px 9px",
            ...typography.mono, fontSize: 11.5, whiteSpace: "nowrap", pointerEvents: "none", zIndex: 2, boxShadow: "0 6px 20px var(--c-shadow)" }}>
            <div style={{ color: colors.text.muted, marginBottom: 3 }}>{hhmm(hover.m)}</div>
            {rows.map(([s, v]) => (
              <div key={s.key} style={{ display: "flex", justifyContent: "space-between", gap: 14 }}>
                <span><i style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: s.color, marginRight: 6, verticalAlign: -1 }} />{s.name}</span>
                <span style={{ color: tone(v) }}>{fmtInr(v)}</span>
              </div>
            ))}
          </div>
        );
      })()}
    </div>
  );
}

/* ───────────────────────── Grid view (small multiples) ───────────────────────── */
function Mini({ path, color, w = 260, h = 56 }) {
  const pts = Array.isArray(path) ? path : [];
  if (pts.length < 2) {
    return <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: h, display: "block", opacity: 0.5 }}>
      <line x1="0" x2={w} y1={h / 2} y2={h / 2} style={{ stroke: colors.border.light }} strokeDasharray="2 3" /></svg>;
  }
  let lo = 0, hi = 0;
  pts.forEach(([, v]) => { if (v < lo) lo = v; if (v > hi) hi = v; });
  const pad = (hi - lo) * 0.1 || 1; lo -= pad; hi += pad;
  const x = (m) => ((m - MARKET_START_MIN) / (FNO_END_MIN - MARKET_START_MIN)) * w;
  const y = (v) => h - ((v - lo) / (hi - lo)) * (h - 2) - 1;
  const d = pts.map(([m, v], i) => `${i ? "L" : "M"}${x(m).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const [lm, lv] = pts[pts.length - 1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: h, display: "block" }}>
      <line x1="0" x2={w} y1={y(0)} y2={y(0)} style={{ stroke: colors.border.light }} strokeDasharray="2 3" />
      <path d={`${d} L${x(lm).toFixed(1)} ${y(0)} L${x(pts[0][0]).toFixed(1)} ${y(0)} Z`} fill={color} fillOpacity="0.08" />
      <path d={d} fill="none" stroke={color} strokeWidth="1.5" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      <circle cx={x(lm)} cy={y(lv)} r="2.5" fill={color} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function GridView({ series, onFocusStrategy }) {
  const cells = series.filter((s) => !isBook(s)).slice().sort((a, b) => Math.abs(lastOf(b) ?? 0) - Math.abs(lastOf(a) ?? 0));
  const books = series.filter(isBook);
  if (!cells.length) {
    return <div style={{ padding: "26px 0", textAlign: "center", color: colors.text.muted, fontSize: 12.5 }}>No strategy has traded yet today — panels appear at the first fill.</div>;
  }
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(232px, 1fr))", gap: spacing.sm, paddingTop: 4 }}>
      {[...books.filter((b) => Array.isArray(b.path) && b.path.length), ...cells].map((s) => {
        const v = lastOf(s);
        let pk = null, tr = null;
        (s.path || []).forEach(([, val]) => { pk = pk == null || val > pk ? val : pk; tr = tr == null || val < tr ? val : tr; });
        const book = isBook(s);
        return (
          <button key={s.key} type="button" onClick={() => !book && s.strategyId && onFocusStrategy && onFocusStrategy(s.strategyId)}
            title={pk == null ? undefined : `peak ${fmtInr(pk)}, trough ${fmtInr(tr)}${book ? "" : " — click to focus"}`}
            className={book ? undefined : "flc-cell"}
            style={{ textAlign: "left", background: book ? colors.bg.tertiary : colors.bg.primary, border: `1px solid ${book ? colors.border.light : colors.border.dark}`,
              borderRadius: 6, padding: "8px 10px 6px", cursor: book ? "default" : "pointer", font: "inherit", color: "inherit", minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8, marginBottom: 4 }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: colors.text.primary, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", display: "inline-flex", alignItems: "center", gap: 6, minWidth: 0 }}>
                <i style={{ width: 10, height: 0, borderTop: `2px ${s.dash ? "dashed" : "solid"} ${s.color}`, display: "inline-block", flexShrink: 0 }} />
                <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{s.name}</span>
              </span>
              <b style={{ ...typography.mono, fontSize: 12.5, fontWeight: 600, color: tone(v), whiteSpace: "nowrap" }}>{v == null ? "—" : fmtInr(v)}</b>
            </div>
            <Mini path={s.path} color={s.color} />
          </button>
        );
      })}
    </div>
  );
}

/* ───────────────────────── shell ───────────────────────── */
export default function FleetChart({ series, nowMin, height = 220, collapsed = false, onToggle = null, onFocusStrategy = null }) {
  const [view, setView] = useState(() => (readJson(VIEW_KEY, "fleet") === "grid" ? "grid" : "fleet"));
  const [overlays, setOverlays] = useState(() => new Set(Array.isArray(readJson(OVERLAY_KEY, [])) ? readJson(OVERLAY_KEY, []) : []));
  const all = Array.isArray(series) ? series : [];
  const books = all.filter(isBook);
  const strategies = all.filter((s) => !isBook(s)).slice().sort((a, b) => Math.abs(lastOf(b) ?? 0) - Math.abs(lastOf(a) ?? 0));
  const pickView = (v) => { setView(v); try { localStorage.setItem(VIEW_KEY, JSON.stringify(v)); } catch {} };
  const toggleOverlay = (key) => setOverlays((prev) => {
    const n = new Set(prev); if (n.has(key)) n.delete(key); else n.add(key);
    try { localStorage.setItem(OVERLAY_KEY, JSON.stringify([...n])); } catch {}
    return n;
  });
  const clearOverlays = () => { setOverlays(new Set()); try { localStorage.setItem(OVERLAY_KEY, "[]"); } catch {} };
  const shown = [...books, ...strategies.filter((s) => overlays.has(s.key))];
  const activeOverlays = strategies.filter((s) => overlays.has(s.key)).length;
  const chip = (on, color) => ({ border: `1px solid ${on ? color : colors.border.dark}`, background: on ? "var(--c-bg-tertiary)" : "transparent", color: on ? colors.text.primary : colors.text.secondary,
    font: "inherit", fontSize: 11.5, padding: "2px 8px", borderRadius: 5, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 6, opacity: on ? 1 : 0.75 });
  const seg = (on) => ({ border: 0, background: on ? colors.bg.tertiary : "transparent", color: on ? colors.text.primary : colors.text.muted, font: "inherit", fontSize: 11.5, fontWeight: 600, padding: "3px 10px", borderRadius: 5, cursor: "pointer" });

  return (
    <div style={{ fontFamily: "var(--c-font-ui)" }}>
      <style>{`.flc-cell:hover { border-color: var(--c-primary) !important; } .flc-chip:hover { opacity: 1 !important; border-color: var(--c-border-light) !important; }`}</style>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: spacing.md, marginBottom: 4, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
          {onToggle && (
            <button type="button" onClick={onToggle} aria-expanded={!collapsed} title={collapsed ? "Expand the chart" : "Collapse the chart"}
              style={{ border: `1px solid ${colors.border.dark}`, background: "transparent", color: colors.text.secondary, width: 22, height: 22,
                borderRadius: 5, cursor: "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center", padding: 0, font: "inherit", fontSize: 11 }}>
              <span style={{ display: "inline-block", transform: collapsed ? "none" : "rotate(90deg)", transition: "transform .15s ease" }}>▸</span>
            </button>
          )}
          <div style={{ ...typography.headingSmall, color: colors.text.primary }}>Fleet MTM today</div>
          {collapsed && (
            <span style={{ ...typography.mono, fontSize: 12.5, color: colors.text.muted, display: "inline-flex", gap: 16, marginLeft: 6 }}>
              {books.map((s) => { const v = lastOf(s); return <span key={s.key}>{s.name} <b style={{ fontWeight: 500, color: tone(v) }}>{v == null ? "—" : fmtInr(v)}</b></span>; })}
            </span>
          )}
        </div>
        {!collapsed && (
          <div role="group" aria-label="Chart view" style={{ display: "inline-flex", border: `1px solid ${colors.border.dark}`, borderRadius: 6, padding: 2, gap: 2 }}>
            <button type="button" aria-pressed={view === "fleet"} onClick={() => pickView("fleet")} style={seg(view === "fleet")}>Fleet</button>
            <button type="button" aria-pressed={view === "grid"} onClick={() => pickView("grid")} style={seg(view === "grid")}>Grid</button>
          </div>
        )}
      </div>
      {!collapsed && view === "fleet" && (
        <>
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap", alignItems: "center", marginBottom: 4 }}>
            {books.map((s) => { const v = lastOf(s); return (
              <span key={s.key} style={{ ...chip(true, colors.border.light), cursor: "default", opacity: 1 }}>
                <i style={{ width: 12, height: 0, borderTop: `2px ${s.dash ? "dashed" : "solid"} ${s.color}`, display: "inline-block" }} />{s.name}
                <b style={{ ...typography.mono, fontWeight: 500, color: tone(v) }}>{v == null ? "—" : fmtInr(v)}</b>
              </span>); })}
            {strategies.length > 0 && <span style={{ fontSize: 11, color: colors.text.muted, margin: "0 4px 0 8px" }}>overlay</span>}
            {strategies.map((s) => { const on = overlays.has(s.key); const v = lastOf(s); return (
              <button key={s.key} type="button" className="flc-chip" aria-pressed={on} onClick={() => toggleOverlay(s.key)} title={on ? "remove from chart" : "draw on chart"} style={chip(on, s.color)}>
                <i style={{ width: 12, height: 0, borderTop: `2px solid ${s.color}`, display: "inline-block", opacity: on ? 1 : 0.55 }} />{s.name}
                <b style={{ ...typography.mono, fontWeight: 500, color: tone(v) }}>{v == null ? "—" : fmtInr(v)}</b>
              </button>); })}
            {activeOverlays > 0 && <button type="button" onClick={clearOverlays} style={{ ...seg(false), fontWeight: 500, textDecoration: "underline", padding: "2px 6px" }}>clear</button>}
          </div>
          <FleetPlot series={shown} nowMin={nowMin} height={height} collapsed={collapsed} />
        </>
      )}
      {!collapsed && view === "grid" && <GridView series={all} onFocusStrategy={onFocusStrategy} />}
    </div>
  );
}
