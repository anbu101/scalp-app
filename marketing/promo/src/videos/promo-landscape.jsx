// Promo-video stage. Renders the REAL Scalp Terminal fleet components
// (FleetKpis, FleetChart, FleetBlotter, MarketBadge) from the repo, fed with
// fictional demo data, as a pure function of time t (seconds).
import { mount } from "../lib/runtime";
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
import { fleetAt, indicesAt, OPEN_MIN } from "../lib/demoData";

// Accent map copied from StrategyHost.META (importing StrategyHost would pull every panel).
const META = {
  IC_V2: { accent: "#6366f1" }, TSG_V1: { accent: "#d946ef" }, TMA_V2: { accent: "#c084fc" },
  VET_V1: { accent: "#34d399" }, ORB_V1: { accent: "#f59e0b" }, BRK_V1: { accent: "#f59e0b" },
  EMC_V1: { accent: "#e11d48" }, HA_V1: { accent: "#14b8a6" }, BB_V1: { accent: "#3b82f6" },
};
const IS_ADMIN = false;   // standard licence view => codenames only

try {
  localStorage.setItem("scalp.dashboard.fleetChartView", JSON.stringify("fleet"));
  localStorage.setItem("scalp.dashboard.fleetChartOverlays", JSON.stringify(["TMA_V2:LIVE", "TSG_V1:PAPER"]));
} catch {}

/* ── timing helpers ── */
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const seg = (t, a, b) => clamp((t - a) / (b - a));
const outCubic = (x) => 1 - Math.pow(1 - x, 3);
const inOutCubic = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
const outQuint = (x) => 1 - Math.pow(1 - x, 5);
const lerp = (a, b, f) => a + (b - a) * f;
const hhmm = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(Math.floor(m % 60)).padStart(2, "0")}`;
const inr = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;

const W = 1680, VIEW_H = 900, TITLE_H = 30;
const REG = { kpis: null, chart: null, blotter: null, rows: [] };

/* ── text that rises in, holds, and leaves (staggered, never crossfaded) ── */
function Line({ t, inAt, outAt, children, style, dy = 26 }) {
  const a = outCubic(seg(t, inAt, inAt + 0.3));
  const b = outAt == null ? 0 : seg(t, outAt, outAt + 0.22);
  const o = a * (1 - b);
  if (o <= 0.001) return null;
  return <div style={{ opacity: o, transform: `translateY(${(1 - a) * dy - b * 14}px)`, ...style }}>{children}</div>;
}

function DemoTag({ opacity = 1 }) {
  return (
    <div style={{ position: "absolute", right: 28, bottom: 22, fontFamily: "'Inter Variable', Inter, sans-serif", fontSize: 17, fontWeight: 500, zIndex: 50,
      color: "#94a3b8", letterSpacing: "0.2px", opacity, display: "flex", alignItems: "center", gap: 8,
      background: "rgba(2,8,23,0.82)", border: "1px solid #1e293b", borderRadius: 8, padding: "6px 12px" }}>
      <i style={{ width: 7, height: 7, borderRadius: 4, background: "#f59e0b", display: "inline-block" }} />
      Illustrative demo data
    </div>
  );
}

/* ── app chrome, rebuilt from App.jsx's nav markup ── */
const NAV = [["📊", "Dashboard"], ["📈", "Analytics"], ["📋", "Trades"], ["⚙️", "Settings"], ["🔗", "Connections"]];
function AppNav() {
  return (
    <nav style={{ background: colors.bg.secondary, borderBottom: `1px solid ${colors.border.light}`, boxShadow: "0 1px 3px var(--c-shadow)" }}>
      <div style={{ padding: "0 24px", display: "flex", alignItems: "center", height: 54, gap: 16, fontFamily: "var(--c-font-ui)" }}>
        <div style={{ fontSize: 17, fontWeight: 700, color: colors.text.primary, display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 22 }}>⚡</span> Scalp Terminal
        </div>
        <div style={{ display: "flex", gap: 2, marginLeft: 40 }}>
          {NAV.map(([icon, label], i) => (
            <span key={label} style={{ padding: "7px 14px", borderRadius: 6, fontSize: 13, fontWeight: 600, display: "flex", alignItems: "center", gap: 5,
              background: i === 0 ? colors.primary : "transparent", color: i === 0 ? colors.text.primary : colors.text.secondary }}>
              <span style={{ fontSize: 13 }}>{icon}</span>{label}
            </span>
          ))}
        </div>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10, fontSize: 12, color: colors.text.secondary }}>
          <span style={{ padding: "4px 10px", borderRadius: 6, border: `1px solid ${colors.success}`, color: colors.success, fontWeight: 700, fontSize: 11 }}>● Trading</span>
          <span style={{ padding: "4px 10px", borderRadius: 6, background: colors.bg.tertiary, fontWeight: 600 }}>Zerodha · DEMO01</span>
          <span style={{ fontSize: 16 }}>🔔</span>
        </div>
      </div>
    </nav>
  );
}

function Card({ children, style, id }) {
  return (
    <div data-reg={id} style={{ background: colors.bg.secondary, border: `1px solid ${colors.border.light}`, borderRadius: 8,
      boxShadow: "0 1px 3px var(--c-shadow)", padding: `${spacing.md}px ${spacing.lg}px`, ...style }}>
      {children}
    </div>
  );
}

/* ── the Modern dashboard body, assembled like pages/ModernDashboard.jsx ── */
function Dashboard({ nowMin, focusId, scroll }) {
  const data = fleetAt(nowMin);
  const idx = indicesAt(nowMin);
  const rows = data.strategies;
  const series = [
    { key: "__LIVE__", name: "Live", color: "var(--c-text-primary)", width: 2.2, fill: true, path: data.paths.LIVE, value: data.totals.LIVE.gross },
    { key: "__PAPER__", name: "Paper", color: "var(--c-text-secondary)", width: 2, dash: true, path: data.paths.PAPER, value: data.totals.PAPER.gross },
    ...rows.map((r) => ({ key: r.key, strategyId: r.id, name: `${stratName(r.id, IS_ADMIN)} ${bookLabel(r.book)}`, color: META[r.id]?.accent, width: 1.4, path: r.path, value: r.gross })),
  ].filter((s) => !((s.key === "__LIVE__" || s.key === "__PAPER__") && !(data.totals[s.key === "__LIVE__" ? "LIVE" : "PAPER"].rows > 0)));
  const ref = useRef(null);
  useLayoutEffect(() => {
    const root = ref.current; if (!root) return;
    const rel = (el) => { let y = 0, x = 0, e = el; while (e && e !== root) { y += e.offsetTop; x += e.offsetLeft; e = e.offsetParent; } return { x, y, w: el.offsetWidth, h: el.offsetHeight }; };
    ["kpis", "chart", "blotter"].forEach((k) => { const el = root.querySelector(`[data-reg=${k}]`); if (el) REG[k] = rel(el); });
    REG.rows = [...root.querySelectorAll(".flb-row")].map((el) => ({ id: el.getAttribute("data-k"), ...rel(el) }));
  });
  return (
    <div ref={ref} style={{ position: "relative", transform: `translateY(${-scroll}px)` }}>
      <div style={{ height: 54 }} />
      <div style={{ padding: spacing.xxl, display: "flex", flexDirection: "column", gap: spacing.lg, fontFamily: "var(--c-font-ui)", color: colors.text.primary }}>
        <div style={{ display: "flex", alignItems: "center", gap: spacing.md }}>
          <div style={{ ...typography.headingMedium, color: colors.text.primary }}>Monday, 9 Nov 2026</div>
          <span style={{ fontSize: 11, fontWeight: 600, padding: "3px 10px", borderRadius: 20, color: colors.success, border: `1px solid ${colors.success}`, opacity: 0.9 }}>Market open</span>
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: spacing.sm }}>
            <MarketBadge name="NIFTY" data={idx.NIFTY} />
            <MarketBadge name="BANKNIFTY" data={idx.BANKNIFTY} />
            <span style={{ fontSize: 11, color: colors.text.muted, display: "inline-flex", alignItems: "center", gap: 6 }}>
              <i style={{ width: 6, height: 6, borderRadius: "50%", background: colors.success, display: "inline-block" }} />books live
            </span>
          </div>
        </div>
        <div data-reg="kpis"><FleetKpis totals={data.totals} active={data.active} fleet={data.fleet} nowMin={Math.floor(nowMin)} /></div>
        <Card id="chart"><FleetChart series={series} nowMin={Math.floor(nowMin)} height={220} collapsed={false} onToggle={() => {}} onFocusStrategy={() => {}} /></Card>
        <Card id="blotter" style={{ padding: `${spacing.md}px ${spacing.lg}px ${spacing.xs}px` }}>
          <RowKeys>
            <FleetBlotter rows={rows} idle={data.idle} focusId={focusId} onFocus={() => {}} isAdminUi={IS_ADMIN} meta={META} />
          </RowKeys>
        </Card>
      </div>
    </div>
  );
}
// tag blotter rows with their strategy id so the cursor can find them
function RowKeys({ children }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    const trs = ref.current ? [...ref.current.querySelectorAll(".flb-row")] : [];
    trs.forEach((tr) => { const b = tr.querySelector("b"); if (b) tr.setAttribute("data-k", b.textContent); });
  });
  return <div ref={ref}>{children}</div>;
}

function Spot({ reg, a, scroll }) {
  if (!reg || a <= 0.001) return null;
  const pad = 8;
  return (
    <div style={{ position: "absolute", left: reg.x - pad, top: reg.y - scroll - pad, width: reg.w + pad * 2, height: reg.h + pad * 2, borderRadius: 12,
      boxShadow: `0 0 0 4000px rgba(2,8,23,${0.78 * a}), 0 0 0 ${1.5 * a}px rgba(59,130,246,${0.9 * a}), 0 0 40px rgba(59,130,246,${0.25 * a})`, zIndex: 8, pointerEvents: "none" }} />
  );
}

function AppWindow({ nowMin, focusId, scroll, spot }) {
  return (
    <div style={{ width: W, height: VIEW_H + TITLE_H, borderRadius: 12, overflow: "hidden", background: colors.bg.primary,
      boxShadow: "0 40px 120px rgba(0,0,0,0.65), 0 0 0 1px #1e293b", position: "relative" }}>
      <div style={{ height: TITLE_H, background: "#0b1222", display: "flex", alignItems: "center", gap: 8, padding: "0 14px", borderBottom: "1px solid #1e293b" }}>
        {["#ff5f57", "#febc2e", "#28c840"].map((c) => <i key={c} style={{ width: 12, height: 12, borderRadius: 6, background: c, display: "inline-block" }} />)}
        <span style={{ flex: 1, textAlign: "center", fontFamily: "'Inter Variable', Inter, sans-serif", fontSize: 12.5, color: "#94a3b8", marginRight: 52 }}>Scalp Terminal</span>
      </div>
      <div style={{ position: "relative", height: VIEW_H, overflow: "hidden" }}>
        <Dashboard nowMin={nowMin} focusId={focusId} scroll={scroll} />
        <div style={{ position: "absolute", top: 0, left: 0, right: 0, zIndex: 5 }}><AppNav /></div>
        {spot && <Spot {...spot} scroll={scroll} />}
      </div>
    </div>
  );
}

function Cursor({ x, y, press }) {
  return (
    <svg width="34" height="40" viewBox="0 0 17 20" style={{ position: "absolute", left: x, top: y, transform: `scale(${1 - press * 0.12})`, transformOrigin: "0 0", filter: "drop-shadow(0 3px 6px rgba(0,0,0,.5))", zIndex: 20 }}>
      <path d="M1 1 L1 15.5 L4.6 12.2 L7 18 L9.6 16.9 L7.2 11.3 L12 11.3 Z" fill="#f8fafc" stroke="#020817" strokeWidth="1.1" strokeLinejoin="round" />
    </svg>
  );
}

/* ── camera: map window point (cx, cy) to frame point (fx, fy) at scale s ── */
function cam(t) {
  const k = REG.kpis || { x: 24, y: 150, w: 1632, h: 130 };
  const b = REG.blotter || { x: 24, y: 640, w: 1632, h: 380 };
  const whole = { cx: W / 2, cy: (VIEW_H + TITLE_H) / 2, s: 0.84, fx: 960, fy: 610 };
  const kp = { cx: k.x + k.w / 2, cy: TITLE_H + k.y + k.h / 2, s: 1.06, fx: 960, fy: 430 };
  const bl = { cx: b.x + b.w / 2, cy: TITLE_H + b.y - 190 + b.h / 2, s: 1.08, fx: 960, fy: 640 };
  const mix = (A, B, f) => ({ cx: lerp(A.cx, B.cx, f), cy: lerp(A.cy, B.cy, f), s: lerp(A.s, B.s, f), fx: lerp(A.fx, B.fx, f), fy: lerp(A.fy, B.fy, f) });
  if (t < 6.0) return { ...whole, s: lerp(0.84, 0.875, seg(t, 2.0, 6.0)) };
  const w2 = { ...whole, s: 0.875 };
  if (t < 10.0) { const f = inOutCubic(seg(t, 6.0, 6.75)); const c = mix(w2, kp, f); c.s += 0.04 * seg(t, 6.75, 10); return c; }
  const k2 = { ...kp, s: 1.10 };
  return mix(k2, bl, inOutCubic(seg(t, 10.0, 10.75)));
}
function spotAt(t) {
  if (t >= 6.4 && t < 10.0) return { reg: REG.kpis, a: outCubic(seg(t, 6.45, 6.9)) * (1 - seg(t, 9.8, 10.0)) };
  if (t >= 10.2 && t < 14.3) return { reg: REG.blotter, a: outCubic(seg(t, 10.3, 10.75)) * (1 - seg(t, 13.9, 14.15)) };
  return null;
}
// blotter scroll inside the app viewport (scene 4)
const scrollAt = (t) => 190 * inOutCubic(seg(t, 10.0, 10.75));

/* ── scenes ── */
function Hook({ t }) {
  const f = outQuint(seg(t, 0.1, 1.75));
  const m = OPEN_MIN + (940 - OPEN_MIN) * f;
  const v = fleetAt(Math.floor(m)).totals.LIVE.gross;
  const pop = 1 + 0.035 * Math.max(0, 1 - Math.abs(t - 1.78) / 0.12);
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
      <div style={{ position: "absolute", inset: 0, background: "radial-gradient(1200px 700px at 50% 58%, rgba(16,185,129,0.10), transparent 60%)" }} />
      <Line t={t} inAt={0.05} style={{ fontFamily: "'Inter Variable', Inter, sans-serif", fontSize: 64, fontWeight: 700, color: "#f8fafc", letterSpacing: "-0.02em", marginBottom: 54 }}>
        Nine strategies. One screen.
      </Line>
      <div style={{ display: "flex", alignItems: "center", gap: 18, fontFamily: "'Inter Variable', Inter, sans-serif", marginBottom: 6, opacity: outCubic(seg(t, 0.0, 0.25)) }}>
        <span style={{ fontSize: 22, fontWeight: 700, letterSpacing: "0.5px", padding: "4px 14px", borderRadius: 6, border: "2px solid #f59e0b", color: "#f59e0b" }}>LIVE</span>
        <span style={{ fontFamily: "'JetBrains Mono Variable', monospace", fontSize: 30, color: "#94a3b8" }}>MTM today · {hhmm(m)}</span>
      </div>
      <div style={{ fontFamily: "'JetBrains Mono Variable', monospace", fontVariantNumeric: "tabular-nums", fontSize: 196, fontWeight: 700, letterSpacing: "-0.04em",
        color: v >= 0 ? "#10b981" : "#ef4444", transform: `scale(${pop})`, opacity: outCubic(seg(t, 0.0, 0.2)) }}>
        {inr(v)}
      </div>
    </div>
  );
}

function Captions({ t }) {
  const cap = { fontFamily: "'Inter Variable', Inter, sans-serif", fontWeight: 700, color: "#f8fafc", letterSpacing: "-0.02em" };
  return (
    <>
      <div style={{ position: "absolute", left: 0, right: 0, top: 70, textAlign: "center" }}>
        <Line t={t} inAt={2.35} outAt={5.78} style={{ ...cap, fontSize: 58 }}>
          Scalp Terminal. <span style={{ color: "#94a3b8" }}>Your whole fleet, live.</span>
        </Line>
      </div>
      <div style={{ position: "absolute", left: 0, right: 0, top: 690, textAlign: "center" }}>
        <Line t={t} inAt={6.55} outAt={9.78} style={{ ...cap, fontSize: 62 }}>
          <span style={{ color: "#f59e0b" }}>Live</span> and <span style={{ color: "#cbd5e1" }}>Paper</span>. Side by side. Never summed.
        </Line>
      </div>
      <div style={{ position: "absolute", left: 0, right: 0, top: 230, textAlign: "center" }}>
        <Line t={t} inAt={10.55} outAt={13.78} style={{ ...cap, fontSize: 58 }}>
          Every strategy gets its own row.
        </Line>
      </div>
    </>
  );
}

function DashScenes({ t }) {
  try { localStorage.setItem("scalp.dashboard.fleetChartView", JSON.stringify("fleet")); } catch {}
  // session clock: draws 09:15 → 14:10 in the reveal, then creeps
  const nowMin = t < 5.6 ? OPEN_MIN + (850 - OPEN_MIN) * inOutCubic(seg(t, 2.25, 5.6)) : 850 + (t - 5.6) * 1.6;
  // cursor walk through the blotter rows (scene 4), one row per beat
  const order = ["Timberwolf", "Icarus", "Tigris", "Icarus#2", "Outrider", "Velvet"];
  let focusId = null, cur = null;
  if (t >= 10.5 && t < 14.2 && REG.rows.length) {
    const rows = REG.rows;
    const step = Math.min(rows.length - 1, Math.floor((t - 10.75) / 0.5));
    const idx = Math.max(0, step);
    const prev = Math.max(0, idx - 1);
    const local = clamp((t - 10.75 - idx * 0.5) / 0.22);
    const r0 = rows[t < 10.75 ? 0 : prev], r1 = rows[idx];
    const g = t < 10.75 ? 0 : inOutCubic(local);
    const ry = lerp(r0.y, r1.y, idx === 0 ? 1 : g) + r1.h / 2;
    const c = cam(t);
    const sc = scrollAt(t);
    const wx = REG.blotter.x + 250, wy = TITLE_H + ry - sc;
    const fx = c.fx + (wx - c.cx) * c.s, fy = c.fy + (wy - c.cy) * c.s;
    const enter = outCubic(seg(t, 10.5, 10.85));
    cur = { x: lerp(fx + 220, fx, enter), y: lerp(fy + 160, fy, enter), press: Math.max(0, 1 - Math.abs(local - 1) * 4) * (t >= 10.75 ? 1 : 0) };
    if (t >= 10.9) {
      const ids = ["TMA_V2", "IC_V2", "TSG_V1", "IC_V2", "ORB_V1", "VET_V1"];
      focusId = ids[Math.min(idx, ids.length - 1)];
    }
    void order;
  }
  const c = cam(t);
  // scene 5: the window recedes behind the Telegram panel
  const rec = inOutCubic(seg(t, 14.0, 14.6));
  const op = 1 - 0.72 * rec;
  const s = c.s * (1 - 0.18 * rec);
  const fx = c.fx - 380 * rec;
  const enter = outCubic(seg(t, 2.0, 2.55));
  const transform = `translate(${fx - c.cx * s}px, ${c.fy - c.cy * s + (1 - enter) * 120}px) scale(${s})`;
  return (
    <>
      <div style={{ position: "absolute", left: 0, top: 0, transformOrigin: "0 0", transform, opacity: Math.min(op, enter), filter: rec > 0 ? `blur(${rec * 5}px)` : undefined }}>
        <AppWindow nowMin={nowMin} focusId={focusId} scroll={scrollAt(t)} spot={spotAt(t)} />
      </div>
      {cur && t < 14.0 && <Cursor {...cur} />}
      <Captions t={t} />
    </>
  );
}

function TelegramScene({ t }) {
  const a = outCubic(seg(t, 14.15, 14.65));
  const msg = outCubic(seg(t, 14.55, 14.95));
  const out = seg(t, 16.85, 17.05);
  if (t < 14.1 || out >= 1) return null;
  return (
    <div style={{ position: "absolute", inset: 0, opacity: 1 - out }}>
      <div style={{ position: "absolute", left: 120, top: 360, width: 640 }}>
        <Line t={t} inAt={14.35} style={{ fontFamily: "'JetBrains Mono Variable', monospace", fontSize: 40, fontWeight: 700, color: "#f59e0b", marginBottom: 14 }}>15:40</Line>
        <Line t={t} inAt={14.45} style={{ fontFamily: "'Inter Variable', Inter, sans-serif", fontSize: 72, fontWeight: 700, color: "#f8fafc", letterSpacing: "-0.02em", lineHeight: 1.08 }}>
          The day lands<br />in Telegram.
        </Line>
      </div>
      <div style={{ position: "absolute", right: 120, top: 70, width: 900, height: 940, borderRadius: 22, overflow: "hidden", background: "#0e1621",
        boxShadow: "0 40px 120px rgba(0,0,0,.6), 0 0 0 1px #1c2733", transform: `translateX(${(1 - a) * 260}px)`, opacity: a }}>
        <div style={{ height: 76, background: "#17212b", display: "flex", alignItems: "center", gap: 16, padding: "0 24px", fontFamily: "'Inter Variable', Inter, sans-serif" }}>
          <div style={{ width: 46, height: 46, borderRadius: 23, background: "linear-gradient(135deg,#3b82f6,#6366f1)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 24 }}>⚡</div>
          <div>
            <div style={{ color: "#f5f5f5", fontSize: 20, fontWeight: 600 }}>Scalp Terminal</div>
            <div style={{ color: "#6c7883", fontSize: 15 }}>bot</div>
          </div>
        </div>
        <div style={{ padding: "26px 28px", display: "flex", flexDirection: "column", alignItems: "flex-start" }}>
          <div style={{ background: "#182533", borderRadius: 16, padding: 8, opacity: msg, transform: `translateY(${(1 - msg) * 40}px) scale(${0.96 + 0.04 * msg})`, transformOrigin: "0 100%" }}>
            <img src="eod_card.png" alt="" style={{ width: 828, display: "block", borderRadius: 11 }} />
            <div style={{ textAlign: "right", color: "#6c7883", fontFamily: "'Inter Variable', Inter, sans-serif", fontSize: 14, padding: "6px 6px 0" }}>15:40</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function GridBackdrop({ t }) {
  // localStorage is read at mount: switch the view to Grid just for this instance
  try { localStorage.setItem("scalp.dashboard.fleetChartView", JSON.stringify("grid")); } catch {}
  const data = fleetAt(940);
  const series = data.strategies.map((r) => ({ key: r.key, strategyId: r.id, name: `${stratName(r.id, IS_ADMIN)} ${bookLabel(r.book)}`, color: META[r.id]?.accent, width: 1.4, path: r.path, value: r.gross }));
  const books = [
    { key: "__LIVE__", name: "Live", color: "var(--c-text-primary)", width: 2.2, fill: true, path: data.paths.LIVE },
    { key: "__PAPER__", name: "Paper", color: "var(--c-text-secondary)", width: 2, dash: true, path: data.paths.PAPER },
  ];
  const drift = (t - 17) * 10;
  return (
    <div style={{ position: "absolute", left: 60, top: 400 - drift, width: 1800, opacity: 0.45, filter: "blur(1px)" }}>
      <FleetChart series={[...books, ...series]} nowMin={940} height={220} collapsed={false} />
    </div>
  );
}

function Outro({ t }) {
  const a = seg(t, 16.95, 17.4);
  if (t < 16.95) return null;
  return (
    <div style={{ position: "absolute", inset: 0, opacity: a }}>
      <GridBackdrop t={t} />
      <div style={{ position: "absolute", inset: 0, background: "radial-gradient(1100px 420px at 50% 52%, rgba(2,8,23,.93) 30%, rgba(2,8,23,.45))" }} />
      <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
        <Line t={t} inAt={17.15} dy={34} style={{ fontFamily: "'Inter Variable', Inter, sans-serif", fontSize: 128, fontWeight: 800, color: "#f8fafc", letterSpacing: "-0.035em", display: "flex", alignItems: "center", gap: 26 }}>
          <span style={{ fontSize: 108 }}>⚡</span>Scalp Terminal
        </Line>
        <Line t={t} inAt={17.45} style={{ fontFamily: "'Inter Variable', Inter, sans-serif", fontSize: 38, fontWeight: 500, color: "#cbd5e1", marginTop: 26 }}>
          Algorithmic options desk · NIFTY · Zerodha
        </Line>
        <Line t={t} inAt={17.7} style={{ display: "flex", gap: 14, marginTop: 34, fontFamily: "'Inter Variable', Inter, sans-serif", fontSize: 22, fontWeight: 600 }}>
          {["Live + Paper", "Fleet MTM", "Telegram EOD", "macOS · Windows"].map((x) => (
            <span key={x} style={{ padding: "8px 18px", borderRadius: 8, border: "1px solid #334155", color: "#94a3b8", background: "#0f172a" }}>{x}</span>
          ))}
        </Line>
      </div>
    </div>
  );
}

function Video({ t }) {
  return (
    <div style={{ position: "relative", width: 1920, height: 1080, overflow: "hidden", background: "#020817" }}>
      <div style={{ position: "absolute", inset: 0, backgroundImage: "radial-gradient(rgba(148,163,184,0.07) 1px, transparent 1px)", backgroundSize: "28px 28px" }} />
      {t < 2.0 && <Hook t={t} />}
      {t >= 1.95 && t < 17.1 && <DashScenes t={t} />}
      <TelegramScene t={t} />
      <Outro t={t} />
      <DemoTag />
    </div>
  );
}

export const VIDEO_META = { width: 1920, height: 1080, duration: 20, theme: "dark", poster: 5.5, warmup: [0.5, 2.5, 8.0, 12.0] };
mount(Video, VIDEO_META);
