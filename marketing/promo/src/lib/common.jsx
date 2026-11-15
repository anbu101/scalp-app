// Shared pieces for the vertical trailers: timing helpers, the REAL mobile
// Modern dashboard (compact FleetKpis / FleetChart / FleetBlotter), grain.
import "@app/index.css";
import "@fontsource-variable/jetbrains-mono";
import "@fontsource-variable/inter";
import { useLayoutEffect, useRef } from "react";
import FleetKpis from "@app/components/fleet/FleetKpis";
import FleetChart from "@app/components/fleet/FleetChart";
import FleetBlotter from "@app/components/fleet/FleetBlotter";
import MarketBadge from "@app/components/MarketBadge";
import { stratName } from "@app/strategies/displayNames";
import { bookLabel } from "@app/components/fleet/fleetFormat";
import { colors, spacing, typography } from "@app/tokens";
import { fleetAt, indicesAt } from "./demoData";

export const META = {
  IC_V2: { accent: "#6366f1" }, TSG_V1: { accent: "#d946ef" }, TMA_V2: { accent: "#c084fc" },
  VET_V1: { accent: "#34d399" }, ORB_V1: { accent: "#f59e0b" }, BRK_V1: { accent: "#f59e0b" },
  EMC_V1: { accent: "#e11d48" }, HA_V1: { accent: "#14b8a6" }, BB_V1: { accent: "#3b82f6" },
};
const IS_ADMIN = false;

export const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
export const seg = (t, a, b) => clamp((t - a) / (b - a));
export const outCubic = (x) => 1 - Math.pow(1 - x, 3);
export const outQuint = (x) => 1 - Math.pow(1 - x, 5);
export const inOutCubic = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
export const outBack = (x) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); };
export const lerp = (a, b, f) => a + (b - a) * f;
export const hhmm = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(Math.floor(m % 60)).padStart(2, "0")}`;
// a window that is fully on between [a, b] with fades of `f`
export const win = (t, a, b, fi = 0.3, fo = 0.25) => outCubic(seg(t, a, a + fi)) * (1 - seg(t, b - fo, b));

try {
  localStorage.setItem("scalp.dashboard.fleetChartView", JSON.stringify("fleet"));
  localStorage.setItem("scalp.dashboard.fleetChartOverlays", JSON.stringify(["TMA_V2:LIVE"]));
} catch {}

export const PHONE_W = 400;

/* The Modern dashboard's phone layout, assembled like pages/ModernDashboard.jsx (isMobile). */
export function PhoneDash({ nowMin, scroll = 0, dateStr = "Monday, 9 Nov 2026", focusId = null, onRows }) {
  const m = Math.floor(nowMin);
  const data = fleetAt(m);
  const idx = indicesAt(m);
  const rows = data.strategies;
  const series = [
    { key: "__LIVE__", name: "Live", color: "var(--c-text-primary)", width: 2.2, fill: true, path: data.paths.LIVE, value: data.totals.LIVE.gross },
    { key: "__PAPER__", name: "Paper", color: "var(--c-text-secondary)", width: 2, dash: true, path: data.paths.PAPER, value: data.totals.PAPER.gross },
    ...rows.map((r) => ({ key: r.key, strategyId: r.id, name: `${stratName(r.id, IS_ADMIN)} ${bookLabel(r.book)}`, color: META[r.id]?.accent, width: 1.4, path: r.path, value: r.gross })),
  ].filter((s) => !((s.key === "__LIVE__" || s.key === "__PAPER__") && !(data.totals[s.key === "__LIVE__" ? "LIVE" : "PAPER"].rows > 0)));
  const ref = useRef(null);
  useLayoutEffect(() => {
    if (!onRows || !ref.current) return;
    const root = ref.current;
    const rel = (el) => { let y = 0, e = el; while (e && e !== root) { y += e.offsetTop; e = e.offsetParent; } return { y, h: el.offsetHeight }; };
    const out = {};
    root.querySelectorAll(".flm-card").forEach((el) => { const b = el.querySelector("b"); if (b) out[b.textContent + (el.textContent.includes("LIVE") ? ":L" : ":P")] = rel(el); });
    const k = root.querySelector("[data-reg=kpis]"); if (k) out.__kpis = rel(k);
    const c = root.querySelector("[data-reg=chart]"); if (c) out.__chart = rel(c);
    const b = root.querySelector("[data-reg=blotter]"); if (b) out.__blotter = rel(b);
    onRows(out);
  });
  const card = { background: colors.bg.secondary, border: `1px solid ${colors.border.light}`, borderRadius: 8, boxShadow: "0 1px 3px var(--c-shadow)" };
  return (
    <div ref={ref} style={{ width: PHONE_W, transform: `translateY(${-scroll}px)`, padding: spacing.md, boxSizing: "border-box",
      display: "flex", flexDirection: "column", gap: spacing.md, fontFamily: "var(--c-font-ui)", color: colors.text.primary }}>
      <div style={{ display: "flex", flexDirection: "column", gap: spacing.sm }}>
        <div style={{ display: "flex", alignItems: "center", gap: spacing.sm, flexWrap: "wrap" }}>
          <div style={{ ...typography.headingMedium, fontSize: 18, color: colors.text.primary }}>{dateStr}</div>
          <span style={{ fontSize: 11, fontWeight: 600, padding: "3px 10px", borderRadius: 20, color: colors.success, border: `1px solid ${colors.success}`, opacity: 0.9 }}>Market open</span>
          <span style={{ marginLeft: "auto", fontSize: 11, color: colors.text.muted, display: "inline-flex", alignItems: "center", gap: 5 }}>
            <i style={{ width: 7, height: 7, borderRadius: "50%", background: colors.success, display: "inline-block" }} />live
          </span>
        </div>
        <div style={{ display: "flex", gap: spacing.sm, overflow: "hidden" }}>
          <span style={{ flex: "0 0 auto" }}><MarketBadge name="NIFTY" data={idx.NIFTY} /></span>
          <span style={{ flex: "0 0 auto" }}><MarketBadge name="BANKNIFTY" data={idx.BANKNIFTY} /></span>
        </div>
      </div>
      <div data-reg="kpis"><FleetKpis totals={data.totals} active={data.active} fleet={data.fleet} nowMin={m} compact /></div>
      <div data-reg="chart" style={{ ...card, padding: `${spacing.sm + 2}px ${spacing.md}px` }}>
        <FleetChart series={series} nowMin={m} height={180} collapsed={false} onToggle={() => {}} onFocusStrategy={() => {}} compact />
      </div>
      <div data-reg="blotter" style={{ ...card, padding: `${spacing.md}px ${spacing.md}px` }}>
        <FleetBlotter rows={rows} idle={data.idle} focusId={focusId} onFocus={() => {}} isAdminUi={IS_ADMIN} meta={META} compact />
      </div>
    </div>
  );
}

/* A phone body around the dashboard. viewH = visible screen height in css px. */
export function Phone({ children, viewH = 820, scale = 2, bezel = "#0b0b0f", glow }) {
  const W = PHONE_W + 24, H = viewH + 24;
  return (
    <div style={{ width: W * scale, height: H * scale, position: "relative" }}>
      <div style={{ position: "absolute", left: 0, top: 0, width: W, height: H, transform: `scale(${scale})`, transformOrigin: "0 0",
        borderRadius: 46, background: bezel, boxShadow: `0 0 0 2px #2a2a33, 0 30px 90px rgba(0,0,0,.7)${glow ? `, 0 0 120px ${glow}` : ""}` }}>
        <div style={{ position: "absolute", left: 12, top: 12, width: PHONE_W, height: viewH, borderRadius: 36, overflow: "hidden", background: "var(--c-bg-primary)" }}>
          <div style={{ height: 34 }} />
          {children}
          <div style={{ position: "absolute", top: 10, left: "50%", width: 110, height: 28, marginLeft: -55, borderRadius: 16, background: "#000" }} />
        </div>
      </div>
    </div>
  );
}

/* deterministic film grain */
export function Grain({ frame, opacity = 0.08 }) {
  const seed = (frame * 7919) % 1000;
  return (
    <svg width="1080" height="1920" style={{ position: "absolute", inset: 0, pointerEvents: "none", opacity, mixBlendMode: "screen", zIndex: 90 }}>
      <filter id={`g${seed}`}><feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" seed={seed} stitchTiles="stitch" /><feColorMatrix type="saturate" values="0" /></filter>
      <rect width="100%" height="100%" filter={`url(#g${seed})`} />
    </svg>
  );
}

export function DemoTag({ color = "#94a3b8", bg = "rgba(0,0,0,0.6)", border = "#27272a", dot = "#f59e0b", font = "Inter", bottom = 44 }) {
  return (
    <div style={{ position: "absolute", right: 36, bottom, fontFamily: font === "Inter" ? "'Inter Variable', Inter, sans-serif" : font, fontSize: 22, fontWeight: 500, zIndex: 95, color,
      display: "flex", alignItems: "center", gap: 9, background: bg, border: `1px solid ${border}`, borderRadius: 9, padding: "7px 14px" }}>
      <i style={{ width: 8, height: 8, borderRadius: 4, background: dot, display: "inline-block" }} />App UI: illustrative demo data
    </div>
  );
}

/* the app's real charge model (backend/app/backtest/charges/charges_model.py, LOCKED v4) */
export const FEES = [
  ["Brokerage", "₹20 / order"],
  ["STT", "0.15% · sell leg"],
  ["NSE exchange charge", "0.03553%"],
  ["SEBI fee", "₹10 / crore"],
  ["Stamp duty", "0.003% · buy leg"],
  ["GST", "18%"],
];
