// frontend/src/components/fleet/FleetChart.jsx — ── DASH_MODERN_20260925 ──
// ── FLEET_CHART_COLLAPSE_20260925 ── collapsible, state remembered.
// ── FLEET_CHART_V2_20260928 ── two views: Fleet (book totals, strategies as opt-in
//   overlays) and Grid (small multiples, click to focus).
// ── DASH_MODERN_MOBILE_20261007 ── `compact` (phone): tighter axes, touch scrubbing.
// ── DASH_SYSTEM_20261117 ── rebuilt on components/fleet/dash.js: no fills, no
//   accent palette. Live = ink 2 px, Paper = muted dashed, an overlay = thin ink
//   labelled at its end (direct labels instead of 15 colours). Values sit in the
//   title line once; overlay toggles carry names only (the table has the numbers).

import { useEffect, useMemo, useRef, useState } from "react";
import { MARKET_START_MIN, FNO_END_MIN } from "../../marketSession";
import { fmtInr, hhmm, valueAt } from "./fleetFormat";
import { SPACE, COLOR, TYPE, NUM, tone } from "./dash";

const P_FULL = { l: 56, r: 72, t: SPACE.lg, b: SPACE.xl };   // r: room for the end labels
const P_COMPACT = { l: 40, r: SPACE.sm, t: SPACE.lg, b: SPACE.xl };
const X_TICKS = [MARKET_START_MIN, 600, 660, 720, 780, 840, 900, FNO_END_MIN];
const X_TICKS_COMPACT = [MARKET_START_MIN, 660, 780, FNO_END_MIN];
const VIEW_KEY = "scalp.dashboard.fleetChartView";
const OVERLAY_KEY = "scalp.dashboard.fleetChartOverlays";
const isBook = (s) => s.key === "__LIVE__" || s.key === "__PAPER__";
const AXIS = { fill: COLOR.muted, fontSize: TYPE.meta.fontSize, fontFamily: NUM.fontFamily };

/* role → stroke (the series' own accent colour is deliberately ignored) */
function strokeOf(s) {
  if (s.key === "__LIVE__") return { stroke: COLOR.ink, width: 2, dash: "", opacity: 1 };
  if (s.key === "__PAPER__") return { stroke: COLOR.muted, width: 1.5, dash: "6 4", opacity: 1 };
  return { stroke: COLOR.ink, width: 1, dash: "", opacity: 0.5 };
}

function yStep(span) {
  return [250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 25000, 50000, 100000].find((s) => span / s <= 6) || 200000;
}
function fmtTick(v) {
  if (v === 0) return "0";
  const a = Math.abs(v), s = v > 0 ? "+" : "−";
  return a >= 1000 ? `${s}${(a / 1000).toFixed(a % 1000 ? 1 : 0)}k` : `${s}${a}`;
}
function lastOf(s) { return Array.isArray(s.path) && s.path.length ? s.path[s.path.length - 1][1] : (s.value ?? null); }
function readJson(key, fallback) { try { const v = JSON.parse(localStorage.getItem(key)); return v == null ? fallback : v; } catch { return fallback; } }
const shortName = (n) => String(n || "").replace(/ (Live|Paper)$/, (m) => (m === " Live" ? " · L" : " · P"));

/* ───────────────────────── Fleet view ───────────────────────── */
function FleetPlot({ series, nowMin, height, collapsed, compact = false }) {
  const P = compact ? P_COMPACT : P_FULL;
  const ticks = compact ? X_TICKS_COMPACT : X_TICKS;
  const hostRef = useRef(null);
  const hideRef = useRef(null);
  const [width, setWidth] = useState(1200);
  const [hover, setHover] = useState(null);
  useEffect(() => () => clearTimeout(hideRef.current), []);
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
    const pad = (hi - lo) * 0.12 || 500;
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
    clearTimeout(hideRef.current);
    const r = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - r.left;
    let m = Math.round(MARKET_START_MIN + ((px - P.l) / plotW) * (FNO_END_MIN - MARKET_START_MIN));
    m = Math.max(MARKET_START_MIN, Math.min(FNO_END_MIN, m));
    setHover({ m, px, py: e.clientY - r.top });
  };
  const onLeave = (e) => {
    clearTimeout(hideRef.current);
    if (e && e.pointerType === "touch") hideRef.current = setTimeout(() => setHover(null), 2500);
    else setHover(null);
  };
  const onCancel = () => { clearTimeout(hideRef.current); setHover(null); };

  // direct end labels (desktop): nudge apart so they never overlap
  const labels = compact ? [] : (() => {
    const ls = visible.map((s) => { const [lm, lv] = s.path[s.path.length - 1]; return { s, lx: x(lm), ly: y(lv) }; })
      .sort((a, b) => a.ly - b.ly);
    for (let i = 1; i < ls.length; i++) if (ls[i].ly - ls[i - 1].ly < 16) ls[i].ly = ls[i - 1].ly + 16;
    return ls;
  })();
  return (
    <div ref={hostRef} style={{ position: "relative", height }}>
      <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} style={{ display: "block", overflow: "visible", touchAction: "pan-y" }}
        onPointerMove={onMove} onPointerDown={onMove} onPointerLeave={onLeave} onPointerCancel={onCancel}>
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={P.l} x2={width - P.r} y1={y(v)} y2={y(v)} style={{ stroke: COLOR.line }} strokeOpacity={v === 0 ? 1 : 0.5} strokeDasharray={v === 0 ? "" : "2 6"} />
            <text x={P.l - SPACE.sm} y={y(v) + 4} textAnchor="end" style={AXIS}>{fmtTick(v)}</text>
          </g>
        ))}
        {ticks.map((m) => (
          <text key={m} x={x(m)} y={height - SPACE.xs} textAnchor={m === MARKET_START_MIN ? "start" : m === FNO_END_MIN ? "end" : "middle"} style={AXIS}>{hhmm(m)}</text>
        ))}
        {nowMin < FNO_END_MIN && nowMin >= MARKET_START_MIN && (
          <line x1={nowX} x2={nowX} y1={P.t} y2={height - P.b} style={{ stroke: COLOR.muted }} strokeDasharray="2 4" />
        )}
        {visible.map((s) => {
          const st = strokeOf(s);
          const d = s.path.map(([m, v], i) => `${i ? "L" : "M"}${x(m).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
          const [lm, lv] = s.path[s.path.length - 1];
          return (
            <g key={s.key} opacity={st.opacity}>
              <path d={d} fill="none" stroke={st.stroke} strokeWidth={st.width} strokeDasharray={st.dash} strokeLinejoin="round" strokeLinecap="round" />
              <circle cx={x(lm)} cy={y(lv)} r={st.width + 1} fill={st.stroke} />
            </g>
          );
        })}
        {labels.map(({ s, lx, ly }) => (
          <text key={`l-${s.key}`} x={lx + SPACE.sm} y={ly + 4} style={{ ...AXIS, fill: isBook(s) ? COLOR.ink : COLOR.muted, fontFamily: "var(--c-font-ui)" }}>
            {isBook(s) ? s.name : shortName(s.name)}
          </text>
        ))}
        {hover && <line x1={x(hover.m)} x2={x(hover.m)} y1={P.t} y2={height - P.b} style={{ stroke: COLOR.ink }} strokeOpacity="0.3" />}
      </svg>
      {empty && (
        <div style={{ position: "absolute", inset: `${P.t}px ${P.r}px ${P.b}px ${P.l}px`, display: "flex", alignItems: "center", justifyContent: "center", ...TYPE.body, color: COLOR.muted, pointerEvents: "none" }}>
          The MTM path builds one point a minute while the market is open.
        </div>
      )}
      {hover && !empty && (() => {
        const rows = visible.map((s) => [s, valueAt(s.path, hover.m)]).filter(([, v]) => v != null);
        if (!rows.length) return null;
        const right = hover.px > width * (compact ? 0.5 : 0.62);
        return (
          <div style={{ position: "absolute", top: compact ? P.t : Math.max(P.t, hover.py - SPACE.md), [right ? "right" : "left"]: right ? width - hover.px + SPACE.lg : hover.px + SPACE.lg,
            background: "var(--c-bg-primary)", border: `1px solid ${COLOR.line}`, borderRadius: 8, padding: `${SPACE.sm}px ${SPACE.md}px`,
            ...TYPE.meta, ...NUM, whiteSpace: "nowrap", pointerEvents: "none", zIndex: 2 }}>
            <div style={{ color: COLOR.muted, marginBottom: SPACE.xs }}>{hhmm(hover.m)}</div>
            {rows.map(([s, v]) => (
              <div key={s.key} style={{ display: "flex", justifyContent: "space-between", gap: SPACE.lg, color: COLOR.ink }}>
                <span>{s.name}</span>
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
function Mini({ path, h = 48, w = 260 }) {
  const pts = Array.isArray(path) ? path : [];
  if (pts.length < 2) {
    return <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: h, display: "block" }}>
      <line x1="0" x2={w} y1={h / 2} y2={h / 2} style={{ stroke: COLOR.line }} strokeDasharray="2 6" /></svg>;
  }
  let lo = 0, hi = 0;
  pts.forEach(([, v]) => { if (v < lo) lo = v; if (v > hi) hi = v; });
  const pad = (hi - lo) * 0.1 || 1; lo -= pad; hi += pad;
  const x = (m) => ((m - MARKET_START_MIN) / (FNO_END_MIN - MARKET_START_MIN)) * w;
  const y = (v) => h - ((v - lo) / (hi - lo)) * (h - 2) - 1;
  const d = pts.map(([m, v], i) => `${i ? "L" : "M"}${x(m).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1][1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: h, display: "block" }}>
      <line x1="0" x2={w} y1={y(0)} y2={y(0)} style={{ stroke: COLOR.line }} strokeDasharray="2 6" />
      <path d={d} fill="none" stroke={tone(last)} strokeWidth="1.5" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}

function GridView({ series, onFocusStrategy, compact = false }) {
  const cells = series.filter((s) => !isBook(s)).slice().sort((a, b) => Math.abs(lastOf(b) ?? 0) - Math.abs(lastOf(a) ?? 0));
  const books = series.filter(isBook);
  if (!cells.length) {
    return <div style={{ padding: `${SPACE.xl}px 0`, textAlign: "center", ...TYPE.body, color: COLOR.muted }}>No strategy has traded yet today — panels appear at the first fill.</div>;
  }
  return (
    <div style={{ display: "grid", gridTemplateColumns: compact ? "repeat(2, minmax(0, 1fr))" : "repeat(auto-fill, minmax(232px, 1fr))", gap: SPACE.sm }}>
      {[...books.filter((b) => Array.isArray(b.path) && b.path.length), ...cells].map((s) => {
        const v = lastOf(s);
        const book = isBook(s);
        return (
          <button key={s.key} type="button" onClick={() => !book && s.strategyId && onFocusStrategy && onFocusStrategy(s.strategyId)}
            className={book ? undefined : "flc-cell"}
            style={{ textAlign: "left", background: "transparent", border: `1px solid ${COLOR.line}`, borderRadius: 8,
              padding: SPACE.md, cursor: book ? "default" : "pointer", font: "inherit", color: "inherit", minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: SPACE.sm, marginBottom: SPACE.xs }}>
              <span style={{ ...TYPE.meta, color: book ? COLOR.ink : COLOR.muted, fontWeight: book ? 600 : 400, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{s.name}</span>
              <span style={{ ...TYPE.body, ...NUM, fontWeight: 600, color: tone(v), whiteSpace: "nowrap" }}>{v == null ? "—" : fmtInr(v)}</span>
            </div>
            <Mini path={s.path} h={compact ? 36 : 48} />
          </button>
        );
      })}
    </div>
  );
}

/* ───────────────────────── shell ───────────────────────── */
export default function FleetChart({ series, nowMin, height = 220, collapsed = false, onToggle = null, onFocusStrategy = null, compact = false }) {
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
  const textBtn = (on) => ({ border: 0, background: "transparent", padding: 0, cursor: "pointer", font: "inherit",
    ...TYPE.meta, color: on ? COLOR.ink : COLOR.muted, fontWeight: on ? 600 : 400, whiteSpace: "nowrap", flexShrink: 0 });
  const keyLine = (s) => { const st = strokeOf(s); return (
    <i style={{ width: SPACE.lg, height: 0, borderTop: `${st.width}px ${st.dash ? "dashed" : "solid"} ${st.stroke}`, display: "inline-block", opacity: st.opacity }} />); };

  const bookKeys = books.map((s) => { const v = lastOf(s); return (
    <span key={s.key} style={{ display: "inline-flex", alignItems: "center", gap: SPACE.sm, ...TYPE.body, color: COLOR.muted, whiteSpace: "nowrap" }}>
      {keyLine(s)}{s.name}<span style={{ ...NUM, fontWeight: 600, color: tone(v) }}>{v == null ? "—" : fmtInr(v)}</span>
    </span>); });
  return (
    <div style={{ fontFamily: "var(--c-font-ui)" }}>
      <style>{`.flc-cell:hover { border-color: ${COLOR.ink} !important; } .flc-legend::-webkit-scrollbar { display: none; } .flc-legend { scrollbar-width: none; }`}</style>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: SPACE.lg, flexWrap: compact ? "nowrap" : "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: SPACE.lg, minWidth: 0, flexWrap: "wrap" }}>
          <button type="button" onClick={onToggle || undefined} aria-expanded={!collapsed} disabled={!onToggle}
            title={collapsed ? "Expand the chart" : "Collapse the chart"}
            style={{ border: 0, background: "transparent", padding: 0, cursor: onToggle ? "pointer" : "default", font: "inherit",
              display: "inline-flex", alignItems: "center", gap: SPACE.sm, color: COLOR.ink }}>
            <span style={{ ...TYPE.meta, color: COLOR.muted, display: "inline-block", width: SPACE.md, transform: collapsed ? "none" : "rotate(90deg)", transition: "transform .15s ease" }}>▸</span>
            <span style={TYPE.title}>Fleet MTM</span>
          </button>
          {!compact && bookKeys}
        </div>
        {!collapsed && (
          <div role="group" aria-label="Chart view" style={{ display: "inline-flex", gap: SPACE.md }}>
            <button type="button" aria-pressed={view === "fleet"} onClick={() => pickView("fleet")} style={textBtn(view === "fleet")}>Fleet</button>
            <button type="button" aria-pressed={view === "grid"} onClick={() => pickView("grid")} style={textBtn(view === "grid")}>Grid</button>
          </div>
        )}
      </div>
      {compact && books.length > 0 && <div style={{ display: "flex", gap: SPACE.lg, flexWrap: "wrap", marginTop: SPACE.sm }}>{bookKeys}</div>}
      {!collapsed && view === "fleet" && (
        <>
          {strategies.length > 0 && (
            <div className={compact ? "flc-legend" : undefined} style={{ display: "flex", gap: SPACE.md, flexWrap: compact ? "nowrap" : "wrap",
              overflowX: compact ? "auto" : undefined, alignItems: "center", marginTop: SPACE.sm }}>
              <span style={{ ...TYPE.meta, color: COLOR.muted, flexShrink: 0 }}>Overlay</span>
              {strategies.map((s) => { const on = overlays.has(s.key); return (
                <button key={s.key} type="button" className="flc-chip" aria-pressed={on} onClick={() => toggleOverlay(s.key)}
                  title={on ? "remove from chart" : "draw on chart"} style={textBtn(on)}>{on ? "✓ " : ""}{s.name}</button>); })}
              {activeOverlays > 0 && <button type="button" onClick={clearOverlays} style={{ ...textBtn(false), textDecoration: "underline" }}>clear</button>}
            </div>
          )}
          <div style={{ marginTop: SPACE.md }}>
            <FleetPlot series={shown} nowMin={nowMin} height={height} collapsed={collapsed} compact={compact} />
          </div>
        </>
      )}
      {!collapsed && view === "grid" && <div style={{ marginTop: SPACE.md }}><GridView series={all} onFocusStrategy={onFocusStrategy} compact={compact} /></div>}
    </div>
  );
}
