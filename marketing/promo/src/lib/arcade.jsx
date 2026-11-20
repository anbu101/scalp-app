// Shared pieces of the Arcade promos (promo-arcade landscape, reel-arcade vertical): palette, fonts,
// timing helpers, text entrances, the moving Arcade stage, demo-data adapters for the Calm Dashboard,
// and the scene copy (alerts, execution facts, lot ladder, Telegram messages) — one source, so the
// two cuts never say different things.
// fence: PROMO_ARCADE_20261120
import { stratName } from "@app/strategies/displayNames";
import { indicesAt, OPEN_MIN } from "./demoData";
export const META = {
  IC_V2: { accent: "#6366f1" }, TSG_V1: { accent: "#d946ef" }, TMA_V2: { accent: "#c084fc" },
  VET_V1: { accent: "#34d399" }, ORB_V1: { accent: "#f59e0b" }, BRK_V1: { accent: "#f59e0b" },
  EMC_V1: { accent: "#e11d48" }, HA_V1: { accent: "#14b8a6" }, BB_V1: { accent: "#3b82f6" },
};
export const IS_ADMIN = false;   // standard licence view => codenames only (and no admin meters)
export const nameOf = (id) => stratName(id, IS_ADMIN);

// CalmHero reads the index overlay from localStorage at mount: NIFTY dashed behind the MTM line
try { localStorage.setItem("scalp.dashboard.indexOverlay", JSON.stringify(["NIFTY"])); } catch {}

/* ── Arcade palette (index.css THEME_ARCADE_20260906) ── */
export const A = { bg: "#0b0621", bg2: "#160c3a", magenta: "#ff3cac", cyan: "#3ce7ff", green: "#39ff8c", red: "#ff4d6d", yellow: "#ffd23f", ink: "#f2e9ff", sub: "#cdbff0", muted: "#a493d6", line: "#3b2a6e", line2: "#5a45a0" };
export const ORB = "'Orbitron', 'Space Grotesk', sans-serif";
export const SG = "'Space Grotesk', 'Segoe UI', sans-serif";
export const MONO = "'JetBrains Mono Variable', monospace";
export const glowM = "0 0 22px rgba(255,60,172,0.7)";
export const glowC = "0 0 22px rgba(60,231,255,0.6)";

/* ── section times (s) ── */
export const S = { auto: 2, exec: 6, bt: 10, comp: 14, tg: 18, outro: 22, end: 26 };

/* ── timing helpers ── */
export const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
export const seg = (t, a, b) => clamp((t - a) / (b - a));
export const outCubic = (x) => 1 - Math.pow(1 - x, 3);
export const inOutCubic = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
export const outQuint = (x) => 1 - Math.pow(1 - x, 5);
export const outBack = (x) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); };
export const lerp = (a, b, f) => a + (b - a) * f;
export const hhmm = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(Math.floor(m % 60)).padStart(2, "0")}`;
export const inr = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;

/* text that rises in, holds, and leaves */
export function Line({ t, inAt, outAt, children, style, dy = 26 }) {
  const a = outCubic(seg(t, inAt, inAt + 0.3));
  const b = outAt == null ? 0 : seg(t, outAt, outAt + 0.22);
  const o = a * (1 - b);
  if (o <= 0.001) return null;
  return <div style={{ opacity: o, transform: `translateY(${(1 - a) * dy - b * 14}px)`, ...style }}>{children}</div>;
}
/* a punchier entrance: scales down onto the beat */
export function Slam({ t, at, out, children, style, from = 1.6 }) {
  if (t < at || (out != null && t >= out + 0.2)) return null;
  const a = seg(t, at, at + 0.16);
  const o = out == null ? 1 : 1 - seg(t, out, out + 0.2);
  return <div style={{ transform: `scale(${lerp(from, 1, outBack(a))})`, opacity: clamp(a * 3) * o, ...style }}>{children}</div>;
}
/* section headline: an Orbitron kicker + a Space Grotesk line */
export function Head({ t, at, out, kicker, children, top = 64, color = A.magenta }) {
  return (
    <div style={{ position: "absolute", left: 0, right: 0, top, display: "flex", flexDirection: "column", alignItems: "center", zIndex: 30 }}>
      <Slam t={t} at={at} out={out} style={{ fontFamily: ORB, fontSize: 78, fontWeight: 900, letterSpacing: "0.05em", color: A.ink, textShadow: color === A.cyan ? glowC : glowM }}>{kicker}</Slam>
      <Line t={t} inAt={at + 0.35} outAt={out} style={{ fontFamily: SG, fontSize: 36, fontWeight: 500, color: A.sub, marginTop: 10, textShadow: "0 2px 14px rgba(7,3,26,.95)" }}>{children}</Line>
    </div>
  );
}

/* ── the Arcade stage: violet sky, magenta grid floor rolling toward the viewer, horizon glow, scanlines ── */
export function ArcadeStage({ t, horizon = 720, glow = 1 }) {
  const roll = (t * 46) % 46;
  return (
    <div style={{ position: "absolute", inset: 0, overflow: "hidden", background: `linear-gradient(180deg, #07031a 0%, ${A.bg} 45%, #1a0b44 ${horizon / 10.8}%, ${A.bg} 100%)` }}>
      <div style={{ position: "absolute", left: "50%", top: horizon - 420, width: 1500, height: 840, marginLeft: -750, borderRadius: "50%",
        background: `radial-gradient(closest-side, rgba(255,60,172,${0.22 * glow}), rgba(255,60,172,0) 70%)` }} />
      <div style={{ position: "absolute", left: -1200, right: -1200, top: horizon, height: 1400, transformOrigin: "50% 0", transform: "perspective(420px) rotateX(62deg)",
        backgroundImage: "linear-gradient(rgba(255,60,172,0.55) 2px, transparent 2px), linear-gradient(90deg, rgba(255,60,172,0.45) 2px, transparent 2px)",
        backgroundSize: "100% 46px, 92px 100%", backgroundPosition: `0 ${roll}px, 0 0`,
        maskImage: "linear-gradient(180deg, rgba(0,0,0,0.15), #000 40%)", WebkitMaskImage: "linear-gradient(180deg, rgba(0,0,0,0.15), #000 40%)" }} />
      <div style={{ position: "absolute", left: 0, right: 0, top: horizon - 1, height: 2, background: A.magenta, boxShadow: `0 0 24px 4px rgba(255,60,172,${0.7 * glow})`, opacity: 0.9 }} />
      <div style={{ position: "absolute", inset: 0, backgroundImage: "repeating-linear-gradient(0deg, rgba(0,0,0,0.18) 0 1px, transparent 1px 3px)", pointerEvents: "none" }} />
    </div>
  );
}

/* index paths for the hero's NIFTY / BANKNIFTY chips, shaped like useIndexPaths() */
export function marketAt(nowMin) {
  const out = {};
  ["NIFTY", "BANKNIFTY"].forEach((k) => {
    const path = [];
    for (let m = OPEN_MIN; m <= nowMin; m++) path.push([m, indicesAt(m)[k].ltp]);
    const now = indicesAt(nowMin)[k];
    out[k] = { ltp: now.ltp, prev: now.prev_close, path };
  });
  return out;
}

/* one card per strategy: Live and Paper rows together — buildCards() in pages/CalmDashboard.jsx (filter "ALL") */
export function buildCards(rows) {
  const by = new Map();
  rows.forEach((r) => {
    const g = by.get(r.id) || { id: r.id, live: null, paper: null };
    if (r.book === "LIVE") g.live = r; else g.paper = r;
    by.set(r.id, g);
  });
  const out = [];
  by.forEach((g) => {
    const row = g.live || g.paper;
    const twin = row === g.live ? g.paper : g.live;
    out.push({ id: g.id, row, twin, books: g.live && g.paper ? "Live + Paper" : g.live ? "Live" : "Paper" });
  });
  const grp = (c) => (c.row.state === "OPEN" || c.twin?.state === "OPEN" ? 0 : 1);   // CARD_ORDER_20261120
  return out.sort((a, b) => grp(a) - grp(b) || nameOf(a.id).localeCompare(nameOf(b.id), undefined, { sensitivity: "base" }));
}

/* the Arcade theme's own page canvas (index.css: scanlines, fade, magenta grid rows / columns) */
export const ARCADE_CANVAS = {
  backgroundColor: "#0b0621",
  backgroundImage: [
    "repeating-linear-gradient(0deg, rgba(0, 0, 0, 0.16) 0 1px, transparent 1px 3px)",
    "linear-gradient(180deg, #0b0621 0, #0b0621 55%, rgba(11, 6, 33, 0.35) 100%)",
    "linear-gradient(rgba(255, 60, 172, 0.22) 1px, transparent 1px)",
    "linear-gradient(90deg, rgba(255, 60, 172, 0.22) 1px, transparent 1px)",
  ].join(", "),
  backgroundSize: "100% 3px, 100% 100%, 100% 26px, 44px 100%",
};

/* ── scene 1: auto trades — alerts pop in over the live dashboard, one a beat (times from demoData) ── */
export const ALERTS = [
  { at: 2.5, time: "09:16", id: "TSG_V1", book: "Paper", kind: "ENTRY", text: "4 legs filled", col: A.cyan },
  { at: 3.0, time: "09:20", id: "IC_V2", book: "Live", kind: "ENTRY", text: "4 legs filled", col: A.cyan },
  { at: 3.5, time: "09:45", id: "ORB_V1", book: "Paper", kind: "ENTRY", text: "breakout · 1 leg", col: A.cyan },
  { at: 4.0, time: "10:20", id: "VET_V1", book: "Paper", kind: "ENTRY", text: "trend · 1 leg", col: A.cyan },
  { at: 4.5, time: "10:40", id: "ORB_V1", book: "Paper", kind: "TARGET HIT", text: "+₹4,150", col: A.green },
  { at: 5.0, time: "11:05", id: "VET_V1", book: "Paper", kind: "STOP-LOSS HIT", text: "−₹3,860", col: A.red },
];
/* ── scene 2: execution — three facts slam in over the (dimmed) dashboard ── */
export const EXEC = [
  { icon: "🛰️", k: "Static-IP order relay", v: "Your own relay, set up in one click from Connections", col: A.cyan },
  { icon: "🛡️", k: "Stops at the broker", v: "Stop-loss and target held as Zerodha GTTs", col: A.magenta },
  { icon: "⏱️", k: "Slippage on every order", v: "Send, ack and fill stamped and measured", col: A.green },
];
/* ── scene 4: compounding — lot sizing (illustrative), never an equity curve ── */
export const LADDER = [10, 12, 14, 11, 15, 18, 20, 20];
/* ── scene 5: Telegram — alerts as they happen, then the 15:40 day card (the real renderer's PNG) ── */
export const TG_MSGS = [
  { at: S.tg + 0.5, head: "🎯 TRADE ENTRY", badge: "🟢 LIVE", lines: [["Strategy", "Icarus"], ["Legs", "4 · NIFTY weekly"]], time: "09:20" },
  { at: S.tg + 1.0, head: "🟢 TARGET HIT", badge: "📄 PAPER", lines: [["Strategy", "Outrider"], ["P&L", "₹4,150"]], time: "10:40" },
  { at: S.tg + 1.5, head: "🛑 STOP-LOSS HIT", badge: "📄 PAPER", lines: [["Strategy", "Velvet"], ["Loss", "₹-3,860"]], time: "11:05" },
];
