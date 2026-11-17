// frontend/src/components/calm/CalmChart.jsx — ── DASH_CALM_20261117 ──
// Today's MTM per book as one quiet chart: the hero book a solid line in its sign
// colour, the other book dashed and muted. The time axis fits what has happened
// (09:15 → now), so a young session is not a short line in an empty box. Hover
// shows both books at that minute. Live and Paper are never summed.
// ── INDEX_PATH_20261117 ── overlays: NIFTY / BANKNIFTY as % from yesterday's close,
// dashed, on their own scale on the right; hover shows price and % at the same minute.
import { useMemo, useRef, useState } from "react";
import { C, T, SP } from "./calm";
import { fmtInr, hhmm, valueAt } from "../fleet/fleetFormat";

const OPEN = 555, CLOSE = 930, W = 1000;
const pctTxt = (v) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(2)}%`;
const px = (v) => v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function CalmChart({ lines, overlays = [], nowMin, height = 220, compact = false }) {
  // lines: [{ key, label, path: [[min, v], …], color, dash, primary }]
  // overlays: [{ key, label, color, prev, path: [[min, price], …] }]
  const box = useRef(null);
  const [hover, setHover] = useState(null);
  const drawn = lines.filter((l) => (l.path || []).length > 0);
  const ovs = overlays.filter((o) => o.prev && (o.path || []).length > 1)
    .map((o) => ({ ...o, pct: o.path.map((p) => [p[0], ((p[1] - o.prev) / o.prev) * 100]) }));
  const { x0, x1, lo, hi, plo, phi } = useMemo(() => {
    let last = OPEN;
    [...drawn.map((l) => l.path), ...ovs.map((o) => o.path)].forEach((p) => { const q = p[p.length - 1]; if (q && q[0] > last) last = q[0]; });
    const end = Math.min(CLOSE, Math.max(last, Math.min(nowMin, CLOSE), OPEN + 15));
    const vals = drawn.flatMap((l) => l.path.map((p) => p[1])).concat([0]);
    const a = Math.min(...vals), b = Math.max(...vals);
    const pad = (b - a || 1000) * 0.1;
    const pv = ovs.flatMap((o) => o.pct.map((p) => p[1])).concat([0]);
    const pa = Math.min(...pv), pb = Math.max(...pv), ppad = Math.max(0.05, (pb - pa) * 0.12);
    return { x0: OPEN, x1: end, lo: a - pad, hi: b + pad, plo: pa - ppad, phi: pb + ppad };
  }, [drawn, ovs, nowMin]);
  const X = (m) => ((m - x0) / Math.max(1, x1 - x0)) * W;
  const Y = (v) => 6 + (height - 12) * (hi - v) / (hi - lo || 1);
  const YP = (v) => 6 + (height - 12) * (phi - v) / (phi - plo || 1);
  const d = (path, y = Y) => path.filter((p) => p[0] >= x0 && p[0] <= x1).map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
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
  const aria = drawn.map((l) => `${l.label} ${fmtInr(l.path[l.path.length - 1][1])}`)
    .concat(ovs.map((o) => `${o.label} ${pctTxt(o.pct[o.pct.length - 1][1])} from yesterday's close`)).join(", ");
  if (!drawn.length && !ovs.length) {
    return (
      <div data-testid="calm-chart" style={{ height, display: "flex", alignItems: "center", justifyContent: "center", ...T.body, color: C.muted,
        border: `1px dashed ${C.line}`, borderRadius: 12 }}>The day's line starts at the first order.</div>
    );
  }
  const rightPad = ovs.length ? 52 : 0;
  const pTicks = [];
  if (ovs.length) {   // top of the scale, 0 % (yesterday's close), bottom when the index is below it — never two labels on top of each other
    const rng = phi - plo, top = phi - rng * 0.06, bot = plo + rng * 0.06;
    pTicks.push(top);
    if (plo < 0 && phi > 0 && Math.abs(top) > rng * 0.15) pTicks.push(0);
    if (bot < -0.05 && Math.abs(bot) > rng * 0.15 && Math.abs(bot - top) > rng * 0.15) pTicks.push(bot);
  }
  return (
    <div data-testid="calm-chart" style={{ minWidth: 0 }}>
      <div style={{ position: "relative", height, paddingRight: rightPad }}>
        <div ref={box} onMouseMove={move} onMouseLeave={() => setHover(null)} style={{ position: "relative", height: "100%" }}>
          <svg viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" role="img" aria-label={`MTM today, ${hhmm(x0)} to ${hhmm(x1)}: ${aria}`}
            style={{ position: "absolute", inset: 0, width: "100%", height: "100%", overflow: "visible" }}>
            {drawn.length > 0 && <line x1="0" x2={W} y1={zero} y2={zero} stroke={C.line} strokeWidth="1" vectorEffect="non-scaling-stroke" />}
            {ovs.map((o) => (
              <path key={o.key} data-overlay={o.key} d={d(o.pct, YP)} fill="none" stroke={o.color} strokeWidth="1.5" strokeDasharray="5 4" strokeOpacity="0.9" vectorEffect="non-scaling-stroke" />
            ))}
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
            // ── END_LABELS_20261117 ── the line that ends highest takes its label ABOVE its dot, every other line BELOW it
            // (stacked 16 px apart), so two end labels can never land on each other, whichever book ends higher
            const order = [...drawn].sort((a, b) => Y(a.path[a.path.length - 1][1]) - Y(b.path[b.path.length - 1][1]) || (b.primary ? 1 : 0) - (a.primary ? 1 : 0));
            const rank = order.indexOf(l);
            const lblTop = rank === 0 ? "- 24px" : `+ ${8 + (rank - 1) * 16}px`;
            return (
              <span key={l.key}>
                <span aria-hidden="true" style={{ position: "absolute", left: `calc(${left}% - 5px)`, top: `calc(${top}% - 5px)`, width: 10, height: 10, borderRadius: 5,
                  background: l.primary ? l.color : C.page, border: l.primary ? 0 : `2px solid ${l.color}`, boxSizing: "border-box" }} />
                {both && <span data-chart-end-label={l.key} style={{ position: "absolute", right: compact ? 0 : SP.lg, top: `calc(${top}% ${lblTop})`, ...T.meta, color: l.primary ? l.color : C.muted }}>{l.label}</span>}
              </span>
            );
          })}
          {hover != null && (
            <div role="status" data-testid="calm-chart-tip" style={{ position: "absolute", top: 0, left: `min(calc(${(X(hover) / W) * 100}% + 8px), calc(100% - 190px))`, padding: `${SP.xs}px ${SP.sm}px`,
              background: C.surface, border: `1px solid ${C.line}`, borderRadius: 8, ...T.meta, color: C.ink, pointerEvents: "none", whiteSpace: "nowrap" }}>
              <div style={{ color: C.muted }}>{hhmm(hover)}</div>
              {drawn.map((l) => { const v = valueAt(l.path, hover); return <div key={l.key}>{l.label} <b style={{ ...T.num }}>{v == null ? "—" : fmtInr(v)}</b></div>; })}
              {ovs.map((o) => { const v = valueAt(o.path, hover); return <div key={o.key}>{o.label} <b style={{ ...T.num }}>{v == null ? "—" : px(v)}</b>{v == null ? null : <span style={{ color: v >= o.prev ? C.up : C.down }}> {pctTxt(((v - o.prev) / o.prev) * 100)}</span>}</div>; })}
            </div>
          )}
        </div>
        {ovs.length > 0 && (
          <div aria-hidden="true" data-testid="calm-chart-pct-axis" style={{ position: "absolute", right: 0, top: 0, bottom: 0, width: rightPad - 6, ...T.meta, fontSize: 11 }}>
            {pTicks.map((v, i) => <span key={i} style={{ position: "absolute", right: 0, top: `calc(${(YP(v) / height) * 100}% - 8px)`, color: ovs[0].color }}>{Math.abs(v) < 0.005 ? "0%" : pctTxt(v).replace(/(\.\d)\d%/, "$1%")}</span>)}
          </div>
        )}
      </div>
      <div aria-hidden="true" style={{ display: "flex", justifyContent: "space-between", marginTop: SP.sm, marginRight: rightPad, ...T.meta, color: C.muted }}>
        {ticks.map((m, i) => <span key={i}>{i === ticks.length - 1 ? endLabel(m) : hhmm(m)}</span>)}
      </div>
      {ovs.length > 0 && !compact && (
        <div style={{ marginTop: SP.xs, ...T.meta, color: C.muted }}>Dashed: {ovs.map((o) => o.label).join(" and ")} % from yesterday's close, on the right scale.</div>
      )}
    </div>
  );
}
