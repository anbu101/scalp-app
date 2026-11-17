// frontend/src/components/calm/CalmChart.jsx — ── DASH_CALM_20261117 ──
// Today's MTM per book as one quiet chart: the hero book a solid line in its sign
// colour, the other book dashed and muted. The time axis fits what has happened
// (09:15 → now), so a young session is not a short line in an empty box. Hover
// shows both books at that minute. Live and Paper are never summed.
import { useMemo, useRef, useState } from "react";
import { C, T, SP } from "./calm";
import { fmtInr, hhmm, valueAt } from "../fleet/fleetFormat";

const OPEN = 555, CLOSE = 930, W = 1000;

export default function CalmChart({ lines, nowMin, height = 220, compact = false }) {
  // lines: [{ key, label, path: [[min, v], …], color, dash, primary }]
  const box = useRef(null);
  const [hover, setHover] = useState(null);
  const drawn = lines.filter((l) => (l.path || []).length > 0);
  const { x0, x1, lo, hi } = useMemo(() => {
    let last = OPEN;
    drawn.forEach((l) => { const p = l.path[l.path.length - 1]; if (p && p[0] > last) last = p[0]; });
    const end = Math.min(CLOSE, Math.max(last, Math.min(nowMin, CLOSE), OPEN + 15));
    const vals = drawn.flatMap((l) => l.path.map((p) => p[1])).concat([0]);
    let a = Math.min(...vals), b = Math.max(...vals);
    const pad = (b - a || 1000) * 0.1;
    return { x0: OPEN, x1: end, lo: a - pad, hi: b + pad };
  }, [drawn, nowMin]);
  const X = (m) => ((m - x0) / Math.max(1, x1 - x0)) * W;
  const Y = (v) => 6 + (height - 12) * (hi - v) / (hi - lo || 1);
  const d = (path) => path.filter((p) => p[0] >= x0 && p[0] <= x1).map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
  const zero = Y(0);
  const ticks = [x0, Math.round(x0 + (x1 - x0) / 3), Math.round(x0 + (2 * (x1 - x0)) / 3), x1];
  const endLabel = (m) => (m >= CLOSE ? hhmm(CLOSE) : `Now ${hhmm(m)}`);
  const move = (e) => {
    const r = box.current?.getBoundingClientRect();
    if (!r || !r.width) return;
    const m = Math.round(x0 + ((e.clientX - r.left) / r.width) * (x1 - x0));
    setHover(Math.max(x0, Math.min(x1, m)));
  };
  const both = drawn.length > 1;
  const aria = drawn.map((l) => `${l.label} ${fmtInr(l.path[l.path.length - 1][1])}`).join(", ");
  if (!drawn.length) {
    return (
      <div data-testid="calm-chart" style={{ height, display: "flex", alignItems: "center", justifyContent: "center", ...T.body, color: C.muted,
        border: `1px dashed ${C.line}`, borderRadius: 12 }}>The day's line starts at the first order.</div>
    );
  }
  return (
    <div data-testid="calm-chart" style={{ minWidth: 0 }}>
      <div ref={box} onMouseMove={move} onMouseLeave={() => setHover(null)} style={{ position: "relative", height }}>
        <svg viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" role="img" aria-label={`MTM today, ${hhmm(x0)} to ${hhmm(x1)}: ${aria}`}
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%", overflow: "visible" }}>
          <line x1="0" x2={W} y1={zero} y2={zero} stroke={C.line} strokeWidth="1" vectorEffect="non-scaling-stroke" />
          {drawn.filter((l) => !l.primary).map((l) => (
            <path key={l.key} d={d(l.path)} fill="none" stroke={l.color} strokeWidth="1.75" strokeDasharray={l.dash ? "6 5" : undefined} vectorEffect="non-scaling-stroke" />
          ))}
          {drawn.filter((l) => l.primary).map((l) => (
            <path key={l.key} data-line={l.key} d={d(l.path)} fill="none" stroke={l.color} strokeWidth="2.5" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
          ))}
          {hover != null && <line x1={X(hover)} x2={X(hover)} y1="0" y2={height} stroke={C.line} strokeWidth="1" vectorEffect="non-scaling-stroke" />}
        </svg>
        {drawn.map((l) => {
          const p = l.path[l.path.length - 1];
          const top = (Y(p[1]) / height) * 100, left = (X(Math.min(p[0], x1)) / W) * 100;
          return (
            <span key={l.key}>
              <span aria-hidden="true" style={{ position: "absolute", left: `calc(${left}% - 5px)`, top: `calc(${top}% - 5px)`, width: 10, height: 10, borderRadius: 5,
                background: l.primary ? l.color : C.page, border: l.primary ? 0 : `2px solid ${l.color}`, boxSizing: "border-box" }} />
              {both && <span style={{ position: "absolute", right: compact ? 0 : SP.lg, top: `calc(${top}% ${l.primary ? "- 26px" : "+ 8px"})`, ...T.meta, color: l.primary ? l.color : C.muted }}>{l.label}</span>}
            </span>
          );
        })}
        {hover != null && (
          <div role="status" style={{ position: "absolute", top: 0, left: `min(calc(${(X(hover) / W) * 100}% + 8px), calc(100% - 150px))`, padding: `${SP.xs}px ${SP.sm}px`,
            background: C.surface, border: `1px solid ${C.line}`, borderRadius: 8, ...T.meta, color: C.ink, pointerEvents: "none", whiteSpace: "nowrap" }}>
            <div style={{ color: C.muted }}>{hhmm(hover)}</div>
            {drawn.map((l) => { const v = valueAt(l.path, hover); return <div key={l.key}>{l.label} <b style={{ ...T.num }}>{v == null ? "—" : fmtInr(v)}</b></div>; })}
          </div>
        )}
      </div>
      <div aria-hidden="true" style={{ display: "flex", justifyContent: "space-between", marginTop: SP.sm, ...T.meta, color: C.muted }}>
        {ticks.map((m, i) => <span key={i}>{i === ticks.length - 1 ? endLabel(m) : hhmm(m)}</span>)}
      </div>
    </div>
  );
}
