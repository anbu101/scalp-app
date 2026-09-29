// frontend/src/components/replay/TradeReplay.jsx — ── TRADE_REPLAY_20260928 ──
//
// Full-screen "Trade replay" for one backtest trade group. Data comes from
// GET /api/backtest/runs/{run}/replay/{trade}: 1m underlying + contract bars,
// the strategy's OWN indicators (built server-side by the runner's code), the
// legs, and SL/TP already placed on the pane they belong to (spot vs premium).
//
// Panes share one compressed session axis (09:15–15:30 per day, overnight
// gaps removed) and one crosshair:
//   Underlying  candles at the strategy's timeframe + its overlays
//   Premium     a leg's candles (Heikin Ashi for HA_V1) with its levels, or
//               the basket MTM for multi-leg groups
//   Oscillator  RSI where the strategy uses one
// Keys: ← / → previous / next trade, Esc closes. Theme tokens only; SVG
// colours via inline style so var() resolves on macOS WebKit too.
// ── TRADE_REPLAY_FIX1_20260928 ── entry/exit price tags on both panes, an
// exit-price level, level lines never narrower than a stub, right-axis
// labels de-overlapped, and the pane choices (timeframes, HA, which leg or
// basket) remembered per strategy across Next/Prev and re-opening.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getApiBase } from "../../api/base";
import { alpha, colors, spacing, typography } from "../../tokens";

const OPEN = 555, CLOSE = 930, SEG = CLOSE - OPEN;
const MONO = "'JetBrains Mono', 'Fira Code', monospace";
const PAD = { l: 64, r: 92, t: 8, b: 6 };
const SERIES = { a: "#f59e0b", b: "#38bdf8", c: "#a78bfa", d: "#f472b6", e: "#34d399", f: "#fb923c" };
const TF_CHOICES = [1, 3, 5, 15];
const TIER_TEXT = {
  exact: "Exact — the strategy's own code on the run's inputs",
  converged: "Converged — own code, deep warm-up of a continuous indicator",
  copied: "Copied — runner-inline arithmetic reproduced verbatim",
  levels: "Levels — this strategy has no chart indicator; stored levels shown",
  none: "Generic replay only",
};

const PREF_KEY = (sid) => `scalp.replay.prefs.${sid || "any"}`;
function readPrefs(sid) {
  try { return JSON.parse(localStorage.getItem(PREF_KEY(sid))) || {}; } catch { return {}; }
}
function writePrefs(sid, patch) {
  try { localStorage.setItem(PREF_KEY(sid), JSON.stringify({ ...readPrefs(sid), ...patch })); } catch { /* storage unavailable */ }
}
// the remembered lower-pane choice is a ROLE, not a leg id: basket / the
// trade's main leg / the leg with the same tag (L1…) or side / the signal contract
function viewRole(d, key) {
  if (!d || !key) return null;
  if (key === "basket") return { kind: "basket" };
  if (key === d.default_view) return { kind: "default" };
  if (key.startsWith("sig:") || key.startsWith("sym:")) return { kind: "signal" };
  const leg = (d.group?.legs || []).find((l) => `leg:${l.id}` === key);
  if (!leg) return null;
  if (leg.id === d.group?.primary_id) return { kind: "primary" };
  return { kind: "leg", tag: leg.condition || null, short: !!leg.short, type: leg.instrument_type };
}
function resolveView(d, role) {
  const views = d.views || [];
  const has = (k) => views.some((v) => v.key === k);
  const legs = d.group?.legs || [];
  if (!role || role.kind === "default") return d.default_view;
  if (role.kind === "basket") return has("basket") ? "basket" : d.default_view;
  if (role.kind === "signal") return (views.find((v) => v.key.startsWith("sig:") || v.key.startsWith("sym:")) || {}).key || d.default_view;
  if (role.kind === "primary") return has(`leg:${d.group?.primary_id}`) ? `leg:${d.group.primary_id}` : d.default_view;
  const others = legs.filter((l) => l.id !== d.group?.primary_id);
  const hit = others.find((l) => role.tag && l.condition === role.tag)
    || others.find((l) => !!l.short === role.short) || others[0];
  return hit ? `leg:${hit.id}` : d.default_view;
}

function col(k) {
  if (!k) return colors.text.secondary;
  if (SERIES[k]) return SERIES[k];
  return { profit: colors.profit, loss: colors.loss, warning: colors.warning, primary: colors.primary,
           muted: colors.text.muted }[k] || k;
}
const inr = (v) => (v == null ? "—" : `${v < 0 ? "−" : v > 0 ? "+" : ""}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`);
const px2 = (v) => (v == null ? "—" : Number(v).toFixed(2));
const modOf = (ts) => Math.floor(((ts + 19800) % 86400) / 60);
const hhmm = (ts) => { const m = modOf(ts); return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`; };
function dayLabel(iso) {
  const d = new Date(`${iso}T00:00:00+05:30`);
  return d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", timeZone: "Asia/Kolkata" });
}
function longDay(iso) {
  const d = new Date(`${iso}T00:00:00+05:30`);
  return d.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });
}

// session-anchored aggregation (09:15 grid) — same buckets the runners use
export function aggBars(bars, tf, dayStarts) {
  if (!bars || tf <= 1) return bars || [];
  const out = [];
  let cur = null;
  for (const b of bars) {
    const ds = dayStarts.find((d) => b[0] >= d && b[0] < d + 86400);
    if (ds == null) continue;
    const m = Math.floor((b[0] - ds) / 60);
    const bucket = ds + (OPEN + Math.floor((m - OPEN) / tf) * tf) * 60;
    if (!cur || cur[0] !== bucket) {
      if (cur) out.push(cur);
      cur = [bucket, b[1], b[2], b[3], b[4]];
    } else {
      cur[2] = Math.max(cur[2], b[2]); cur[3] = Math.min(cur[3], b[3]); cur[4] = b[4];
    }
  }
  if (cur) out.push(cur);
  return out;
}

function niceStep(span) {
  const raw = span / 6;
  const p = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-9))));
  return [1, 2, 2.5, 5, 10].map((k) => k * p).find((s) => s >= raw) || 10 * p;
}
function fmtTick(v, step) {
  const d = step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
  return Math.abs(v) >= 10000 ? `${(v / 1000).toFixed(step >= 1000 ? 0 : 2)}k` : v.toFixed(d);
}
function lowerBound(arr, ts) {
  let lo = 0, hi = arr.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m][0] < ts) lo = m + 1; else hi = m; }
  return lo;
}

/* ───────────────────────── one pane ───────────────────────── */
function Pane({ y0, h, width, xOf, bars, tf, overlays, levels, markers, vlines, line, zero, fixed, label,
                hidden, pxPerMin, crossTs }) {
  const plotW = width - PAD.l - PAD.r;
  const { lo, hi } = useMemo(() => {
    if (fixed) return { lo: fixed[0], hi: fixed[1] };
    let lo = Infinity, hi = -Infinity;
    const take = (v) => { if (v == null || !isFinite(v)) return; if (v < lo) lo = v; if (v > hi) hi = v; };
    (bars || []).forEach((b) => { take(b[2]); take(b[3]); });
    (line || []).forEach((p) => take(p[1]));
    (overlays || []).forEach((o) => {
      if (hidden[o.label]) return;
      if (o.type === "line" || o.type === "dots") o.points.forEach((p) => take(p[1]));
      if (o.type === "band") { o.upper.forEach((p) => take(p[1])); o.lower.forEach((p) => take(p[1])); }
      if (o.type === "box") { take(o.top); take(o.bottom); }
    });
    const base = [lo, hi];
    (levels || []).forEach((l) => { if (!hidden[l.label]) take(l.value); });
    (markers || []).forEach((m) => take(m.price));
    if (zero) take(0);
    if (!isFinite(lo)) return { lo: 0, hi: 1 };
    // levels may widen the range, but never beyond 3× the bar span
    const span0 = (base[1] - base[0]) || Math.abs(base[0]) * 0.01 || 1;
    lo = Math.max(lo, base[0] - span0 * 3); hi = Math.min(hi, base[1] + span0 * 3);
    const pad = (hi - lo) * 0.06 || 1;
    return { lo: lo - pad, hi: hi + pad };
  }, [bars, line, overlays, levels, markers, hidden, zero, fixed]);
  const y = (v) => y0 + PAD.t + (1 - (v - lo) / (hi - lo)) * (h - PAD.t - PAD.b);
  const step = niceStep(hi - lo);
  const ticks = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) ticks.push(v);
  const cw = Math.max(1, Math.min(14, pxPerMin * tf * 0.68));
  const clipId = `clip-${y0}`;
  const pathOf = (pts, tfp, stepLine) => {
    let d = "", pen = false;
    for (const p of pts) {
      if (p[1] == null) { pen = false; continue; }
      const xa = xOf(p[0]), xb = xOf(p[0] + (tfp || 1) * 60);
      if (xa == null) { pen = false; continue; }
      const yy = y(p[1]);
      if (stepLine) { d += `${pen ? "L" : "M"}${xa.toFixed(1)},${yy.toFixed(1)}L${(xb ?? xa).toFixed(1)},${yy.toFixed(1)}`; }
      else { const xc = (xa + (xb ?? xa)) / 2; d += `${pen ? "L" : "M"}${xc.toFixed(1)},${yy.toFixed(1)}`; }
      pen = true;
    }
    return d;
  };
  return (
    <g>
      <defs><clipPath id={clipId}><rect x={PAD.l} y={y0} width={plotW} height={h} /></clipPath></defs>
      <rect x={PAD.l} y={y0} width={plotW} height={h} style={{ fill: "transparent", stroke: colors.border.dark }} />
      {ticks.map((v) => (
        <g key={v}>
          <line x1={PAD.l} x2={PAD.l + plotW} y1={y(v)} y2={y(v)} style={{ stroke: colors.border.dark, strokeWidth: 1, opacity: 0.6 }} />
          <text x={PAD.l - 6} y={y(v) + 3.5} textAnchor="end" style={{ fill: colors.text.muted, fontSize: 10.5, fontFamily: MONO }}>{fmtTick(v, step)}</text>
        </g>
      ))}
      {zero && lo < 0 && hi > 0 && (
        <line x1={PAD.l} x2={PAD.l + plotW} y1={y(0)} y2={y(0)} style={{ stroke: colors.text.muted, strokeWidth: 1 }} />
      )}
      <text x={PAD.l + 6} y={y0 + 14} style={{ fill: colors.text.muted, fontSize: 10.5, fontWeight: 600, letterSpacing: 0.5 }}>{label}</text>
      <g clipPath={`url(#${clipId})`}>
        {(overlays || []).filter((o) => !hidden[o.label]).map((o, i) => {
          if (o.type === "shade" || o.type === "box") {
            const xa = xOf(o.from_ts), xb = xOf(o.to_ts);
            if (xa == null || xb == null) return null;
            if (o.type === "shade") {
              return <rect key={i} x={xa} y={y0} width={Math.max(1, xb - xa)} height={h} style={{ fill: alpha(col(o.color), 12) }} />;
            }
            return (
              <rect key={i} x={xa} y={y(o.top)} width={Math.max(1, xb - xa)} height={Math.max(1, y(o.bottom) - y(o.top))}
                style={{ fill: o.fill ? alpha(col(o.color), o.strong ? 26 : 13) : "none", stroke: col(o.color),
                         strokeWidth: o.strong ? 2 : 1, strokeDasharray: o.dash ? "4 3" : undefined }} />
            );
          }
          if (o.type === "band") {
            const up = o.upper.map((p) => [xOf(p[0] + (o.tf || 1) * 30), y(p[1])]).filter((p) => p[0] != null);
            const lowr = o.lower.map((p) => [xOf(p[0] + (o.tf || 1) * 30), y(p[1])]).filter((p) => p[0] != null).reverse();
            if (up.length < 2) return null;
            const d = `M${up.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("L")}L${lowr.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("L")}Z`;
            return <path key={i} d={d} style={{ fill: alpha(col(o.color), 10), stroke: "none" }} />;
          }
          if (o.type === "regime") {
            return (
              <g key={i}>
                {o.points.map((p, k) => {
                  if (!p[2]) return null;
                  const xa = xOf(p[0]), xb = xOf(p[0] + (o.tf || 1) * 60);
                  if (xa == null || xb == null) return null;
                  return <rect key={k} x={xa} y={y0 + h - 5} width={Math.max(1, xb - xa)} height={4}
                    style={{ fill: p[2] > 0 ? colors.profit : colors.loss, opacity: 0.55 }} />;
                })}
              </g>
            );
          }
          return null;
        })}
        {(bars || []).map((b) => {
          const xa = xOf(b[0]), xb = xOf(b[0] + tf * 60);
          if (xa == null) return null;
          const xc = (xa + (xb ?? xa + pxPerMin * tf)) / 2;
          const up = b[4] >= b[1];
          const c = up ? colors.profit : colors.loss;
          const top = y(Math.max(b[1], b[4])), bot = y(Math.min(b[1], b[4]));
          return (
            <g key={b[0]}>
              <line x1={xc} x2={xc} y1={y(b[2])} y2={y(b[3])} style={{ stroke: c, strokeWidth: 1 }} />
              <rect x={xc - cw / 2} y={top} width={cw} height={Math.max(1, bot - top)} style={{ fill: c, opacity: up ? 0.85 : 0.9 }} />
            </g>
          );
        })}
        {line && line.length > 1 && (
          <path d={pathOf(line, 1, false)} style={{ fill: "none", stroke: colors.primary, strokeWidth: 1.8 }} />
        )}
        {(overlays || []).filter((o) => !hidden[o.label] && (o.type === "line" || o.type === "dots")).map((o, i) => (
          o.type === "line"
            ? <path key={`l${i}`} d={pathOf(o.points, o.tf, o.step)}
                style={{ fill: "none", stroke: col(o.color), strokeWidth: o.width || 1.5, strokeDasharray: o.dash ? "5 4" : undefined }} />
            : <g key={`d${i}`}>{o.points.map((p, k) => {
                const xa = xOf(p[0] + (o.tf || 1) * 30);
                if (xa == null) return null;
                return <circle key={k} cx={xa} cy={y(p[1])} r={3.2} style={{ fill: col(o.color) }}><title>{p[2] || o.label}</title></circle>;
              })}</g>
        ))}
        {(levels || []).filter((l) => !hidden[l.label]).map((l, i) => {
          const xa = l.from_ts != null ? xOf(l.from_ts) : PAD.l;
          const xb = l.to_ts != null ? xOf(l.to_ts) : PAD.l + plotW;
          if (xa == null || xb == null || l.value < lo || l.value > hi) return null;
          // a very short trade still gets a visible stub (never past the plot edge)
          return <line key={`v${i}`} x1={xa} x2={Math.min(PAD.l + plotW, Math.max(xb, xa + 56))} y1={y(l.value)} y2={y(l.value)}
            style={{ stroke: col(l.color), strokeWidth: 1.3, strokeDasharray: l.dash === false ? undefined : "6 4" }} />;
        })}
        {(vlines || []).map((v, i) => {
          const xa = xOf(v.ts);
          if (xa == null) return null;
          return <line key={`x${i}`} x1={xa} x2={xa} y1={y0} y2={y0 + h}
            style={{ stroke: v.kind === "entry" ? colors.primary : colors.text.muted, strokeWidth: 1, strokeDasharray: "3 3", opacity: 0.7 }} />;
        })}
        {(markers || []).map((m, i) => {   // price tags: entry to the left, exit to the right (flipped at the edges)
          const xa = xOf(m.ts);
          if (xa == null || m.price == null || !m.tag) return null;
          const text = m.kind === "exit"
            ? `exit ${px2(m.price)}${m.reason ? ` ${m.reason}` : ""} ${hhmm(m.ts)}`
            : `${m.onSpot ? "entry" : m.short ? "sell" : "buy"} ${px2(m.price)} ${hhmm(m.ts)}`;
          const w = text.length * 6.1 + 10;
          let x0 = m.kind === "exit" ? xa + 9 : xa - 9 - w;
          if (x0 < PAD.l + 2) x0 = xa + 9;
          if (x0 + w > PAD.l + plotW - 2) x0 = xa - 9 - w;
          const yy = Math.min(Math.max(y(m.price) + (m.kind === "exit" ? 12 : -12), y0 + 18), y0 + h - 10);
          const tone = m.kind === "exit" ? (m.win == null ? colors.text.secondary : m.win ? colors.profit : colors.loss)
            : m.onSpot ? colors.primary : (m.short ? colors.loss : colors.profit);
          return (
            <g key={`tag${i}`} pointerEvents="none">
              <rect x={x0} y={yy - 8} width={w} height={15} rx={3}
                style={{ fill: colors.bg.elevated, stroke: tone, strokeWidth: 1, opacity: 0.94 }} />
              <text x={x0 + 5} y={yy + 3} style={{ fill: tone, fontSize: 10, fontFamily: MONO, fontWeight: 600 }}>{text}</text>
            </g>
          );
        })}
        {(markers || []).map((m, i) => {
          const xa = xOf(m.ts);
          if (xa == null || m.price == null) return null;
          const yy = y(m.price);
          if (m.kind === "exit") {
            return (
              <g key={`m${i}`} style={{ stroke: colors.text.primary, strokeWidth: 2.4 }}>
                <line x1={xa - 5} x2={xa + 5} y1={yy - 5} y2={yy + 5} /><line x1={xa - 5} x2={xa + 5} y1={yy + 5} y2={yy - 5} />
                <title>{`exit ${hhmm(m.ts)} @ ${px2(m.price)}${m.reason ? ` · ${m.reason}` : ""}`}</title>
              </g>
            );
          }
          const upArrow = !m.short;
          const d = upArrow ? `M${xa},${yy - 1}L${xa - 6},${yy + 10}L${xa + 6},${yy + 10}Z` : `M${xa},${yy + 1}L${xa - 6},${yy - 10}L${xa + 6},${yy - 10}Z`;
          return <path key={`m${i}`} d={d} style={{ fill: upArrow ? colors.profit : colors.loss, stroke: colors.bg.primary, strokeWidth: 1 }}>
            <title>{`${m.short ? "sell" : "buy"} ${hhmm(m.ts)} @ ${px2(m.price)}`}</title></path>;
        })}
      </g>
      {(() => {   // right-axis labels, nudged apart so equal/near levels (SL = exit) stay readable
        const tags = (levels || []).filter((l) => !hidden[l.label] && l.value >= lo && l.value <= hi)
          .map((l) => ({ l, yy: y(l.value) + 3.5 })).sort((a, b) => a.yy - b.yy);
        for (let k = 1; k < tags.length; k++) if (tags[k].yy - tags[k - 1].yy < 11) tags[k].yy = tags[k - 1].yy + 11;
        return tags.map(({ l, yy }, i) => (
          <text key={`t${i}`} x={PAD.l + plotW + 4} y={yy}
            style={{ fill: col(l.color), fontSize: 10, fontFamily: MONO }}>{String(l.label).slice(0, 15)}</text>
        ));
      })()}
      {crossTs != null && xOf(crossTs) != null && (
        <line x1={xOf(crossTs)} x2={xOf(crossTs)} y1={y0} y2={y0 + h} style={{ stroke: colors.text.secondary, strokeWidth: 1, opacity: 0.55 }} />
      )}
    </g>
  );
}

function Legend({ items: raw, hidden, toggle }) {
  const seen = new Set();
  const items = raw.filter((o) => (seen.has(o.label) ? false : seen.add(o.label)));   // one chip per label (toggles all)
  if (!items.length) return null;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
      {items.map((o) => (
        <button key={o.label} type="button" onClick={() => toggle(o.label)} title={hidden[o.label] ? "show" : "hide"}
          style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "2px 8px", borderRadius: 999,
                   border: `1px solid ${colors.border.light}`, background: "transparent", cursor: "pointer",
                   color: colors.text.secondary, fontSize: 11, opacity: hidden[o.label] ? 0.4 : 1 }}>
          <span style={{ width: 10, height: 3, borderRadius: 2, background: col(o.color) }} />{o.label}
        </button>
      ))}
    </div>
  );
}

function Chip({ on, children, onClick, title }) {
  return (
    <button type="button" onClick={onClick} title={title}
      style={{ padding: "3px 9px", borderRadius: 6, fontSize: 11.5, cursor: "pointer", fontWeight: on ? 600 : 500,
               border: `1px solid ${on ? colors.primary : colors.border.light}`,
               background: on ? alpha(colors.primary, 16) : "transparent",
               color: on ? colors.text.primary : colors.text.secondary }}>{children}</button>
  );
}

/* ───────────────────────── modal ───────────────────────── */
export default function TradeReplay({ runId, tradeId, onClose }) {
  const [tid, setTid] = useState(tradeId);
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(false);
  const [spotTf, setSpotTf] = useState(null);
  const [premTf, setPremTf] = useState(null);
  const [view, setView] = useState(null);
  const [useHa, setUseHa] = useState(true);
  const [hidden, setHidden] = useState({});
  const [cross, setCross] = useState(null);
  const [width, setWidth] = useState(1100);
  const hostRef = useRef(null);
  const cache = useRef({});

  useEffect(() => { setTid(tradeId); }, [tradeId]);
  useEffect(() => {
    let dead = false;
    const key = `${runId}:${tid}`;
    const done = (d) => {
      if (dead) return;
      setData(d); setErr(null); setLoading(false);
      const p = readPrefs(d.strategy_id);   // ── TRADE_REPLAY_FIX1_20260928 ── remembered choices win
      setSpotTf(p.spotTf || d.spot?.tf || 1); setPremTf(p.premTf || d.premium?.tf || 1);
      setUseHa(p.ha !== undefined ? !!p.ha : true);
      setView(resolveView(d, p.view)); setCross(null);
    };
    if (cache.current[key]) { done(cache.current[key]); return undefined; }
    setLoading(true);
    fetch(`${getApiBase()}/api/backtest/runs/${runId}/replay/${tid}`)
      .then(async (r) => { if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`); return r.json(); })
      .then((d) => {   // cache under every leg id of the group: Prev/Next land on its first leg
        (d.group?.ids || [tid]).forEach((i) => { cache.current[`${runId}:${i}`] = d; });
        cache.current[key] = d; done(d);
      })
      .catch((e) => { if (!dead) { setErr(String(e.message || e)); setLoading(false); } });
    return () => { dead = true; };
  }, [runId, tid]);

  const go = useCallback((which) => {
    const n = data?.nav?.[which === "prev" ? "prev_trade_id" : "next_trade_id"];
    if (n != null) setTid(n);
  }, [data]);
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose && onClose();
      else if (e.key === "ArrowLeft") go("prev");
      else if (e.key === "ArrowRight") go("next");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, onClose]);
  useEffect(() => {
    const el = hostRef.current;
    if (!el) return undefined;
    const measure = () => setWidth(Math.max(640, el.clientWidth || 1100));
    measure();
    if (typeof ResizeObserver === "undefined") { window.addEventListener("resize", measure); return () => window.removeEventListener("resize", measure); }
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [data]);

  const dayStarts = useMemo(() => data?.window?.day_starts || [], [data]);
  const nDays = dayStarts.length || 1;
  const plotW = width - PAD.l - PAD.r;
  const pxPerMin = plotW / (SEG * nDays);
  const xOf = useCallback((ts) => {
    const k = dayStarts.findIndex((d) => ts >= d && ts < d + 86400 + 60);
    if (k < 0) return null;
    const m = (ts - dayStarts[k]) / 60;
    if (m < OPEN || m > CLOSE) return null;
    return PAD.l + (k * SEG + (m - OPEN)) * pxPerMin;
  }, [dayStarts, pxPerMin]);
  const tsOfX = useCallback((x) => {
    const u = (x - PAD.l) / pxPerMin;
    const k = Math.min(nDays - 1, Math.max(0, Math.floor(u / SEG)));
    const m = Math.min(CLOSE - 1, Math.max(OPEN, OPEN + Math.floor(u - k * SEG)));
    return dayStarts[k] + m * 60;
  }, [dayStarts, nDays, pxPerMin]);

  // ── derived per view ──
  const legs = data?.group?.legs || [];
  const viewObj = (data?.views || []).find((v) => v.key === view) || null;
  const isBasket = view === "basket";
  const vSym = viewObj?.symbol || null;
  const spotBars = useMemo(() => aggBars(data?.spot?.bars, spotTf || 1, dayStarts), [data, spotTf, dayStarts]);
  const haBars = data?.premium?.ha?.[vSym];
  const premBars = useMemo(() => {
    if (!vSym || isBasket) return [];
    if (haBars && useHa) return haBars;
    return aggBars(data?.legs_bars?.[vSym], premTf || 1, dayStarts);
  }, [data, vSym, isBasket, premTf, dayStarts, haBars, useHa]);
  const premTfEff = haBars && useHa ? 1 : (premTf || 1);
  const premOverlays = isBasket ? [] : (data?.premium?.overlays?.[vSym] || []);
  const premLevels = isBasket ? (data?.mtm?.levels || []) : (data?.premium?.levels?.[vSym] || []);
  const spotOverlays = (data?.spot?.overlays || []).filter((o) => o.type !== "level");
  const spotLevels = (data?.spot?.overlays || []).filter((o) => o.type === "level");
  const legWin = (id) => { const l = legs.find((x) => x.id === id); return l && l.net != null ? l.net > 0 : null; };
  const premMarkers = isBasket ? [] : (data?.markers || []).filter((m) => m.symbol === vSym)
    .map((m) => ({ ...m, tag: true, win: legWin(m.leg) }));
  // on the underlying the arrow shows the trade's VIEW: long CE / short PE = up, long PE / short CE = down
  const primLeg = legs.find((l) => l.id === data?.group?.primary_id);
  const bullish = primLeg ? ((primLeg.instrument_type === "PE") === !!primLeg.short) : true;
  const spotMarkers = (data?.markers || []).filter((m) => m.leg === data?.group?.primary_id && m.spot != null)
    .map((m) => ({ ...m, price: m.spot, short: !bullish, tag: true, reason: null, onSpot: true,
                   win: m.kind === "exit" ? legWin(m.leg) : null }));
  const vlines = (data?.markers || []).filter((m) => m.leg === data?.group?.primary_id).map((m) => ({ ts: m.ts, kind: m.kind }));
  const osc = data?.osc;
  const oscOverlays = osc ? (osc.overlays || osc.by_symbol?.[vSym] || osc.by_symbol?.[osc.default_symbol] || []) : [];
  const hasOsc = oscOverlays.some((o) => o.points && o.points.length);

  const H_SPOT = 300, H_PREM = 250, H_OSC = hasOsc ? 90 : 0, GAP = 10, H_AX = 34;
  const yPrem = H_SPOT + GAP, yOsc = yPrem + H_PREM + GAP;
  const totalH = yOsc + (hasOsc ? H_OSC + GAP : 0) + H_AX;
  const toggle = (label) => setHidden((h) => ({ ...h, [label]: !h[label] }));
  const sid = data?.strategy_id;
  const pickSpotTf = (t) => { setSpotTf(t); writePrefs(sid, { spotTf: t }); };
  const pickPremTf = (t) => { setPremTf(t); writePrefs(sid, { premTf: t }); };
  const pickView = (k) => { setView(k); writePrefs(sid, { view: viewRole(data, k) }); };
  const flipHa = () => setUseHa((u) => { writePrefs(sid, { ha: !u }); return !u; });

  const tooltip = useMemo(() => {
    if (cross == null || !data) return null;
    const pick = (arr) => { if (!arr || !arr.length) return null; const i = lowerBound(arr, cross + 1) - 1; return i >= 0 ? arr[i] : null; };
    const sb = pick(spotBars), pb = isBasket ? null : pick(premBars);
    const mt = isBasket ? pick(data.mtm?.path || []) : null;
    return { ts: cross, sb, pb, mt };
  }, [cross, data, spotBars, premBars, isBasket]);

  const g = data?.group;
  const firstDay = data?.window?.days?.[0];
  const card = { background: colors.bg.secondary, border: `1px solid ${colors.border.light}`, borderRadius: 10 };

  return (
    <div role="dialog" aria-modal="true" aria-label="Trade replay"
      onMouseDown={(e) => { if (e.target === e.currentTarget && onClose) onClose(); }}
      style={{ position: "fixed", inset: 0, zIndex: 1200, background: "rgba(2,6,23,0.62)",
               display: "flex", alignItems: "center", justifyContent: "center", padding: "2vh 2vw" }}>
      <div style={{ ...card, width: "min(1500px, 96vw)", height: "96vh", display: "flex", flexDirection: "column",
                    background: colors.bg.primary, boxShadow: "0 24px 64px rgba(0,0,0,0.45)", overflow: "hidden" }}>
        {/* header */}
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: spacing.md,
                      padding: "14px 18px 10px", borderBottom: `1px solid ${colors.border.dark}` }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <span style={{ ...typography.headingLarge, color: colors.text.primary }}>Trade replay</span>
              {data && <span style={{ ...typography.label, color: colors.text.muted }}>{data.strategy_id} · {data.underlying}</span>}
              {data && (
                <span title={`${TIER_TEXT[data.parity?.tier] || ""}\n${data.parity?.detail || ""}`}
                  style={{ fontSize: 10.5, padding: "2px 8px", borderRadius: 999, border: `1px solid ${colors.border.light}`,
                           color: data.parity?.tier === "exact" ? colors.profit : colors.text.secondary }}>
                  indicators: {data.parity?.tier}
                </span>
              )}
            </div>
            {g && (
              <div style={{ marginTop: 4, fontSize: 12.5, color: colors.text.secondary }}>
                {firstDay ? longDay(firstDay) : ""}{data.window.days.length > 1 ? ` → ${dayLabel(data.window.days[data.window.days.length - 1])}` : ""}
                {g.dte != null ? ` · DTE ${g.dte}` : ""} · {legs.length} leg{legs.length > 1 ? "s" : ""}
                {g.exit_reasons?.length ? ` · ${g.exit_reasons.join(", ")}` : ""} ·{" "}
                <b style={{ color: g.net > 0 ? colors.profit : g.net < 0 ? colors.loss : colors.text.primary, fontFamily: MONO }}>{inr(g.net)}</b>
                <span style={{ color: colors.text.muted }}> net ({inr(g.gross)} gross)</span>
              </div>
            )}
            {data?.config_brief && <div style={{ marginTop: 2, fontSize: 11.5, color: colors.text.muted }}>{data.config_brief}</div>}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
            <button type="button" onClick={() => go("prev")} disabled={data?.nav?.prev_trade_id == null}
              style={navBtn(data?.nav?.prev_trade_id == null)}>‹ Prev</button>
            <span style={{ fontSize: 12, color: colors.text.muted, fontFamily: MONO, minWidth: 74, textAlign: "center" }}>
              {data ? `${data.nav.index + 1} / ${data.nav.count}` : "…"}
            </span>
            <button type="button" onClick={() => go("next")} disabled={data?.nav?.next_trade_id == null}
              style={navBtn(data?.nav?.next_trade_id == null)}>Next ›</button>
            <button type="button" onClick={onClose} aria-label="Close" title="Close (Esc)"
              style={{ ...navBtn(false), padding: "6px 10px", marginLeft: 6 }}>✕</button>
          </div>
        </div>

        {/* body */}
        <div style={{ flex: 1, overflow: "auto", padding: "10px 18px 16px" }}>
          {err && <div style={{ ...card, padding: 14, color: colors.loss, fontSize: 13 }}>Replay unavailable: {err}</div>}
          {!data && !err && <div style={{ padding: 40, textAlign: "center", color: colors.text.muted }}>Loading replay…</div>}
          {data && (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", marginBottom: 8 }}>
                <span style={{ ...typography.label, color: colors.text.muted }}>{data.spot.label}</span>
                <div style={{ display: "flex", gap: 4 }}>
                  {TF_CHOICES.map((t) => <Chip key={t} on={spotTf === t} onClick={() => pickSpotTf(t)}>{t}m</Chip>)}
                </div>
                <span style={{ width: 1, height: 18, background: colors.border.light }} />
                <span style={{ ...typography.label, color: colors.text.muted }}>Lower pane</span>
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                  {(data.views || []).map((v) => (
                    <Chip key={v.key} on={view === v.key} onClick={() => pickView(v.key)}
                      title={v.synthetic ? "model-priced leg — no candles" : v.symbol || ""}>{v.label}</Chip>
                  ))}
                </div>
                {!isBasket && (
                  <div style={{ display: "flex", gap: 4 }}>
                    {haBars && <Chip on={useHa} onClick={flipHa} title="Heikin Ashi candles (HA_V1)">HA</Chip>}
                    {(!haBars || !useHa) && TF_CHOICES.map((t) => <Chip key={t} on={premTf === t} onClick={() => pickPremTf(t)}>{t}m</Chip>)}
                  </div>
                )}
                {loading && <span style={{ fontSize: 11, color: colors.text.muted }}>loading…</span>}
              </div>

              <div ref={hostRef} style={{ position: "relative", width: "100%" }}>
                <svg width={width} height={totalH} style={{ display: "block", userSelect: "none" }}
                  onMouseMove={(e) => {
                    const r = e.currentTarget.getBoundingClientRect();
                    const x = e.clientX - r.left;
                    setCross(x < PAD.l || x > PAD.l + plotW ? null : tsOfX(x));
                  }}
                  onMouseLeave={() => setCross(null)}>
                  <Pane y0={0} h={H_SPOT} width={width} xOf={xOf} bars={spotBars} tf={spotTf || 1}
                    overlays={spotOverlays} levels={spotLevels} markers={spotMarkers} vlines={vlines}
                    label={`${data.spot.label} · ${spotTf}m`} hidden={hidden} pxPerMin={pxPerMin} crossTs={cross} />
                  {isBasket ? (
                    <Pane y0={yPrem} h={H_PREM} width={width} xOf={xOf} bars={[]} tf={1} line={data.mtm?.path || []} zero
                      overlays={[]} levels={premLevels} markers={[]} vlines={vlines}
                      label={`basket MTM · ${data.mtm?.note || ""}`} hidden={hidden} pxPerMin={pxPerMin} crossTs={cross} />
                  ) : (
                    <Pane y0={yPrem} h={H_PREM} width={width} xOf={xOf} bars={premBars} tf={premTfEff}
                      overlays={premOverlays} levels={premLevels} markers={premMarkers} vlines={vlines}
                      label={`${vSym || ""} · ${haBars && useHa ? "Heikin Ashi 1m" : `${premTfEff}m`}${viewObj?.synthetic ? " · synthetic (no candles)" : ""}`}
                      hidden={hidden} pxPerMin={pxPerMin} crossTs={cross} />
                  )}
                  {hasOsc && (
                    <Pane y0={yOsc} h={H_OSC} width={width} xOf={xOf} bars={[]} tf={1} overlays={oscOverlays}
                      levels={(osc.guides || []).map((v) => ({ type: "level", label: String(v), value: v, color: "muted", dash: true }))}
                      markers={[]} vlines={vlines} fixed={osc.range || [0, 100]} label={osc.label || ""}
                      hidden={hidden} pxPerMin={pxPerMin} crossTs={cross} />
                  )}
                  {/* x axis: day labels + hour ticks */}
                  <g>
                    {dayStarts.map((ds, k) => (
                      <g key={ds}>
                        {k > 0 && <line x1={xOf(ds + OPEN * 60)} x2={xOf(ds + OPEN * 60)} y1={0} y2={totalH - H_AX}
                          style={{ stroke: colors.border.medium, strokeWidth: 1 }} />}
                        {[600, 660, 720, 780, 840, 900].map((m) => {
                          const xx = xOf(ds + m * 60);
                          if (xx == null || (nDays > 4 && m % 120 !== 0)) return null;
                          return <text key={m} x={xx} y={totalH - H_AX + 13} textAnchor="middle"
                            style={{ fill: colors.text.muted, fontSize: 10.5, fontFamily: MONO }}>{`${m / 60}:00`}</text>;
                        })}
                        <text x={xOf(ds + OPEN * 60) + 4} y={totalH - 4} style={{ fill: colors.text.secondary, fontSize: 11, fontWeight: 600 }}>
                          {dayLabel(data.window.days[k])}</text>
                      </g>
                    ))}
                  </g>
                </svg>
                {tooltip && (
                  <div style={{ position: "absolute", top: 6, right: PAD.r + 8, pointerEvents: "none", ...card,
                                background: colors.bg.elevated, padding: "6px 9px", fontSize: 11, fontFamily: MONO,
                                color: colors.text.secondary, lineHeight: 1.55, minWidth: 170 }}>
                    <div style={{ color: colors.text.primary, fontWeight: 600 }}>{hhmm(tooltip.ts)}</div>
                    {tooltip.sb && <div>spot O {px2(tooltip.sb[1])} H {px2(tooltip.sb[2])}<br />L {px2(tooltip.sb[3])} C {px2(tooltip.sb[4])}</div>}
                    {tooltip.pb && <div style={{ marginTop: 3 }}>prem O {px2(tooltip.pb[1])} H {px2(tooltip.pb[2])}<br />L {px2(tooltip.pb[3])} C {px2(tooltip.pb[4])}</div>}
                    {tooltip.mt && <div style={{ marginTop: 3 }}>MTM {inr(tooltip.mt[1])}</div>}
                  </div>
                )}
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: 6, margin: "8px 0 12px" }}>
                <Legend items={[...spotOverlays.filter((o) => o.label && o.type !== "regime"), ...spotLevels]} hidden={hidden} toggle={toggle} />
                <Legend items={[...premOverlays.filter((o) => o.label), ...premLevels]} hidden={hidden} toggle={toggle} />
              </div>

              {/* legs */}
              <div style={{ ...card, overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <thead>
                    <tr style={{ background: colors.bg.tertiary }}>
                      {["Leg", "Side", "Qty", "In", "Out", "Entry", "Exit", "SL", "TP", "Reason", "MAE", "Net ₹"].map((h) => (
                        <th key={h} style={{ padding: "7px 8px", textAlign: h === "Leg" || h === "Reason" || h === "Side" ? "left" : "right",
                                             ...typography.label, color: colors.text.muted, whiteSpace: "nowrap" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {legs.map((l) => (
                      <tr key={l.id} onClick={() => pickView(`leg:${l.id}`)} style={{ cursor: "pointer", borderTop: `1px solid ${colors.border.dark}`,
                        background: view === `leg:${l.id}` ? alpha(colors.primary, 10) : "transparent" }}>
                        <td style={{ padding: "7px 8px", fontFamily: MONO, whiteSpace: "nowrap", color: colors.text.primary }}>
                          {l.tradingsymbol}{l.synthetic ? <span style={{ color: colors.warning }}> · synthetic</span> : null}
                          {l.condition ? <span style={{ color: colors.text.muted, fontSize: 10.5 }}> · {String(l.condition).slice(0, 40)}</span> : null}
                        </td>
                        <td style={{ padding: "7px 8px", color: l.short ? colors.loss : colors.profit }}>{l.short ? "Sell" : "Buy"} {l.instrument_type}</td>
                        <td style={num}>{l.qty}</td>
                        <td style={num}>{l.entry_ts ? hhmm(l.entry_ts) : "—"}</td>
                        <td style={num}>{l.exit_ts ? hhmm(l.exit_ts) : "open"}</td>
                        <td style={num}>{px2(l.entry_price)}</td>
                        <td style={num}>{px2(l.exit_price)}</td>
                        <td style={{ ...num, color: colors.loss }}>{px2(l.sl)}{l.sl != null && data.sl_basis === "spot" ? "ˢ" : ""}</td>
                        <td style={{ ...num, color: colors.profit }}>{px2(l.tp)}{l.tp != null && data.tp_basis === "spot" ? "ˢ" : ""}</td>
                        <td style={{ padding: "7px 8px" }}>{l.exit_reason || "—"}{l.ambiguous ? " ⚠️" : ""}</td>
                        <td style={num}>{px2(l.max_adverse)}</td>
                        <td style={{ ...num, fontWeight: 700, color: l.net > 0 ? colors.profit : l.net < 0 ? colors.loss : colors.text.primary }}>{inr(l.net)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div style={{ marginTop: 8, fontSize: 11.5, color: colors.text.muted, lineHeight: 1.6 }}>
                {(data.sl_basis === "spot" || data.tp_basis === "spot") && <div>ˢ level on the underlying (drawn on the upper pane)</div>}
                <div>{TIER_TEXT[data.parity?.tier] || ""}{data.parity?.detail ? ` · ${data.parity.detail}` : ""}</div>
                {(data.notes || []).map((n, i) => <div key={i}>• {n}</div>)}
                <div style={{ opacity: 0.8 }}>← / → previous / next trade · Esc closes · click a legend chip to hide a line</div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

const num = { padding: "7px 8px", textAlign: "right", fontFamily: MONO, whiteSpace: "nowrap" };
function navBtn(disabled) {
  return { padding: "6px 12px", borderRadius: 7, border: `1px solid ${colors.border.light}`, fontSize: 12.5,
           background: colors.bg.secondary, color: disabled ? colors.text.muted : colors.text.primary,
           cursor: disabled ? "default" : "pointer", opacity: disabled ? 0.5 : 1, fontWeight: 600 };
}
