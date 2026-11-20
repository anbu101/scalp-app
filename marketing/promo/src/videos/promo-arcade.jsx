// Promo video (landscape, 1920×1080, 26 s): Scalp Terminal's FEATURES — auto trading, execution,
// backtests, compounding, Telegram — told over the redesigned Dashboard (CalmHero / StrategyCard /
// IdleCard, the REAL components, fictional demo data) in the app's Arcade theme. The look is the
// eye-catch only: no line talks about themes or cards. Every frame is a pure function of t.
// Sections sit on 2 s bars at 120 BPM (music/promo-arcade.py uses the same times).
// fence: PROMO_ARCADE_20261120
import { mount } from "../lib/runtime";
import "@app/index.css";
import "@fontsource-variable/jetbrains-mono";
import { useLayoutEffect, useRef } from "react";
import CalmHero from "@app/components/calm/CalmHero";
import { StrategyCard, IdleCard } from "@app/components/calm/StrategyCard";
import { C, T, SP, RADIUS } from "@app/components/calm/calm";
import { stratName } from "@app/strategies/displayNames";
import { colors } from "@app/tokens";
import { fleetAt, indicesAt, OPEN_MIN } from "../lib/demoData";
import { DemoTag, FEES } from "../lib/common";

// Accent map copied from StrategyHost.META (importing StrategyHost would pull every panel).
const META = {
  IC_V2: { accent: "#6366f1" }, TSG_V1: { accent: "#d946ef" }, TMA_V2: { accent: "#c084fc" },
  VET_V1: { accent: "#34d399" }, ORB_V1: { accent: "#f59e0b" }, BRK_V1: { accent: "#f59e0b" },
  EMC_V1: { accent: "#e11d48" }, HA_V1: { accent: "#14b8a6" }, BB_V1: { accent: "#3b82f6" },
};
const IS_ADMIN = false;   // standard licence view => codenames only (and no admin meters)
const nameOf = (id) => stratName(id, IS_ADMIN);

// CalmHero reads the index overlay from localStorage at mount: NIFTY dashed behind the MTM line
try { localStorage.setItem("scalp.dashboard.indexOverlay", JSON.stringify(["NIFTY"])); } catch {}

/* ── Arcade palette (index.css THEME_ARCADE_20260906) ── */
const A = { bg: "#0b0621", bg2: "#160c3a", magenta: "#ff3cac", cyan: "#3ce7ff", green: "#39ff8c", red: "#ff4d6d", yellow: "#ffd23f", ink: "#f2e9ff", sub: "#cdbff0", muted: "#a493d6", line: "#3b2a6e", line2: "#5a45a0" };
const ORB = "'Orbitron', 'Space Grotesk', sans-serif";
const SG = "'Space Grotesk', 'Segoe UI', sans-serif";
const MONO = "'JetBrains Mono Variable', monospace";
const glowM = "0 0 22px rgba(255,60,172,0.7)";
const glowC = "0 0 22px rgba(60,231,255,0.6)";

/* ── section times (s) ── */
const S = { auto: 2, exec: 6, bt: 10, comp: 14, tg: 18, outro: 22, end: 26 };

/* ── timing helpers ── */
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const seg = (t, a, b) => clamp((t - a) / (b - a));
const outCubic = (x) => 1 - Math.pow(1 - x, 3);
const inOutCubic = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
const outQuint = (x) => 1 - Math.pow(1 - x, 5);
const outBack = (x) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); };
const lerp = (a, b, f) => a + (b - a) * f;
const hhmm = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(Math.floor(m % 60)).padStart(2, "0")}`;
const inr = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;

const W = 1680, VIEW_H = 900, TITLE_H = 30, NAV_H = 54;
const REG = { cards: null };

/* text that rises in, holds, and leaves */
function Line({ t, inAt, outAt, children, style, dy = 26 }) {
  const a = outCubic(seg(t, inAt, inAt + 0.3));
  const b = outAt == null ? 0 : seg(t, outAt, outAt + 0.22);
  const o = a * (1 - b);
  if (o <= 0.001) return null;
  return <div style={{ opacity: o, transform: `translateY(${(1 - a) * dy - b * 14}px)`, ...style }}>{children}</div>;
}
/* a punchier entrance: scales down onto the beat */
function Slam({ t, at, out, children, style, from = 1.6 }) {
  if (t < at || (out != null && t >= out + 0.2)) return null;
  const a = seg(t, at, at + 0.16);
  const o = out == null ? 1 : 1 - seg(t, out, out + 0.2);
  return <div style={{ transform: `scale(${lerp(from, 1, outBack(a))})`, opacity: clamp(a * 3) * o, ...style }}>{children}</div>;
}
/* section headline: an Orbitron kicker + a Space Grotesk line */
function Head({ t, at, out, kicker, children, top = 64, color = A.magenta }) {
  return (
    <div style={{ position: "absolute", left: 0, right: 0, top, display: "flex", flexDirection: "column", alignItems: "center", zIndex: 30 }}>
      <Slam t={t} at={at} out={out} style={{ fontFamily: ORB, fontSize: 78, fontWeight: 900, letterSpacing: "0.05em", color: A.ink, textShadow: color === A.cyan ? glowC : glowM }}>{kicker}</Slam>
      <Line t={t} inAt={at + 0.35} outAt={out} style={{ fontFamily: SG, fontSize: 36, fontWeight: 500, color: A.sub, marginTop: 10, textShadow: "0 2px 14px rgba(7,3,26,.95)" }}>{children}</Line>
    </div>
  );
}

/* ── the Arcade stage: violet sky, magenta grid floor rolling toward the viewer, horizon glow, scanlines ── */
function ArcadeStage({ t, horizon = 720, glow = 1 }) {
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

/* ── app chrome, rebuilt from App.jsx's nav markup (NAV_CENTER_20261117: centred menu) ── */
const NAV = [["📊", "Dashboard"], ["📈", "Analytics"], ["📋", "Trades"], ["⚙️", "Settings"], ["🔗", "Connections"]];
function AppNav() {
  return (
    <nav style={{ background: colors.bg.secondary, borderBottom: `1px solid ${colors.border.light}`, boxShadow: "0 1px 3px var(--c-shadow)" }}>
      <div style={{ padding: "0 24px", display: "grid", gridTemplateColumns: "1fr auto 1fr", alignItems: "center", height: NAV_H, columnGap: 16, fontFamily: "var(--c-font-ui)" }}>
        <div style={{ fontSize: 17, fontWeight: 700, color: colors.text.primary, display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 22 }}>⚡</span> Scalp Terminal
        </div>
        <div style={{ display: "flex", gap: 2 }}>
          {NAV.map(([icon, label], i) => (
            <span key={label} style={{ padding: "7px 14px", borderRadius: 4, fontSize: 13, fontWeight: 600, display: "flex", alignItems: "center", gap: 5,
              background: i === 0 ? colors.primary : "transparent", color: i === 0 ? colors.text.primary : colors.text.secondary }}>
              <span style={{ fontSize: 13 }}>{icon}</span>{label}
            </span>
          ))}
        </div>
        <div style={{ justifySelf: "end", display: "flex", alignItems: "center", gap: 10, fontSize: 12, color: colors.text.secondary }}>
          <span style={{ padding: "4px 10px", borderRadius: 4, border: `1px solid ${colors.success}`, color: colors.success, fontWeight: 700, fontSize: 11 }}>● Trading</span>
          <span style={{ padding: "4px 10px", borderRadius: 4, background: colors.bg.tertiary, fontWeight: 600 }}>Zerodha · DEMO01</span>
          <span style={{ fontSize: 16 }}>🔔</span>
        </div>
      </div>
    </nav>
  );
}

/* index paths for the hero's NIFTY / BANKNIFTY chips, shaped like useIndexPaths() */
function marketAt(nowMin) {
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
function buildCards(rows) {
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

/* ── the Dashboard body, assembled like pages/CalmDashboard.jsx ── */
function Dashboard({ nowMin, scroll }) {
  const m = Math.floor(nowMin);
  const data = fleetAt(m);
  const rows = data.strategies;
  const cards = buildCards(rows);
  const idle = data.idle.filter((r) => r.mode !== "OFF").sort((a, b) => nameOf(a.id).localeCompare(nameOf(b.id)));
  const nOpen = new Set(rows.filter((r) => r.state === "OPEN").map((r) => r.id)).size;
  const fleet = data.active + idle.length;
  const closesIn = Math.max(0, 930 - m);
  const dateLine = (links) => (
    <div style={{ display: "flex", alignItems: "center", gap: SP.sm, flexWrap: "wrap", ...T.meta, fontSize: 13, color: C.muted }}>
      <span>Monday 9 Nov · {hhmm(m)} · closes in {Math.floor(closesIn / 60)}h {closesIn % 60}m</span>
      {links}
    </div>
  );
  const ref = useRef(null);
  useLayoutEffect(() => {
    const root = ref.current; if (!root) return;
    const s = root.querySelector("[data-testid=calm-strategies]");
    if (s) { let y = 0, e = s; while (e && e !== root) { y += e.offsetTop; e = e.offsetParent; } REG.cards = { y, h: s.offsetHeight }; }
  });
  return (
    <div ref={ref} style={{ position: "relative", transform: `translateY(${-scroll}px)` }}>
      <div style={{ height: NAV_H }} />
      <div data-calm-root style={{ padding: `${SP.xl}px ${SP.xxl}px ${SP.xxxl}px`, color: C.ink, fontFamily: "var(--c-font-ui)", ...T.body, display: "flex", flexDirection: "column", gap: SP.xxxl }}>
        <CalmHero totals={data.totals} paths={data.paths} nowMin={m} armed={idle.length} carried={rows.filter((r) => r.carried && r.state === "OPEN").length}
          dateLine={dateLine} market={marketAt(m)} />
        <section aria-label="Strategies today" data-testid="calm-strategies">
          <div style={{ display: "flex", alignItems: "baseline", gap: SP.md, flexWrap: "wrap", marginBottom: SP.lg }}>
            <span style={{ ...T.h2, fontFamily: ORB, textTransform: "uppercase", letterSpacing: "0.06em" }}>Strategies</span>
            <span style={{ ...T.body, fontSize: 13, color: C.muted }}>{`${data.active} of ${fleet} traded today${nOpen ? ` · ${nOpen} in a trade` : ""}`}</span>
            <div style={{ marginLeft: "auto", display: "flex", gap: SP.xs }}>
              {[["ALL", "All"], ["LIVE", "Live"], ["PAPER", "Paper"]].map(([k, l]) => (
                <span key={k} style={{ background: k === "ALL" ? C.surface : "transparent", border: `1px solid ${k === "ALL" ? C.accent : "transparent"}`, borderRadius: RADIUS.pill,
                  padding: `${SP.xs + 2}px ${SP.md + 2}px`, color: k === "ALL" ? C.ink : C.muted, fontSize: 13 }}>{l}</span>
              ))}
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: `repeat(auto-fill, minmax(max(300px, calc((100% - ${SP.lg * 3}px) / 4)), 1fr))`, gap: SP.lg, gridAutoFlow: "dense", alignItems: "stretch" }}>
            {cards.map((c) => (
              <StrategyCard key={c.id} id={c.id} name={nameOf(c.id)} accent={META[c.id]?.accent} row={c.row} twin={c.twin} books={c.books}
                meter={undefined} expanded={false} onToggle={() => {}} selected={false} onFocus={() => {}} />
            ))}
            {idle.map((r) => (
              <IdleCard key={r.id} id={r.id} name={nameOf(r.id)} accent={META[r.id]?.accent} mode={r.mode} meter={undefined}
                expanded={false} onToggle={() => {}} selected={false} onFocus={() => {}} />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

/* the Arcade theme's own page canvas (index.css: scanlines, fade, magenta grid rows / columns) */
const ARCADE_CANVAS = {
  backgroundColor: "#0b0621",
  backgroundImage: [
    "repeating-linear-gradient(0deg, rgba(0, 0, 0, 0.16) 0 1px, transparent 1px 3px)",
    "linear-gradient(180deg, #0b0621 0, #0b0621 55%, rgba(11, 6, 33, 0.35) 100%)",
    "linear-gradient(rgba(255, 60, 172, 0.22) 1px, transparent 1px)",
    "linear-gradient(90deg, rgba(255, 60, 172, 0.22) 1px, transparent 1px)",
  ].join(", "),
  backgroundSize: "100% 3px, 100% 100%, 100% 26px, 44px 100%",
};

function AppWindow({ nowMin, scroll }) {
  return (
    <div style={{ width: W, height: VIEW_H + TITLE_H, borderRadius: 12, overflow: "hidden", ...ARCADE_CANVAS, position: "relative",
      boxShadow: "0 40px 120px rgba(0,0,0,0.7), 0 0 0 1px #5a45a0, 0 0 60px rgba(255,60,172,0.25)" }}>
      <div style={{ height: TITLE_H, background: "#07031a", display: "flex", alignItems: "center", gap: 8, padding: "0 14px", borderBottom: "1px solid #3b2a6e", position: "relative", zIndex: 6 }}>
        {["#ff5f57", "#febc2e", "#28c840"].map((c) => <i key={c} style={{ width: 12, height: 12, borderRadius: 6, background: c, display: "inline-block" }} />)}
        <span style={{ flex: 1, textAlign: "center", fontFamily: SG, fontSize: 12.5, color: A.muted, marginRight: 52 }}>Scalp Terminal</span>
      </div>
      <div style={{ position: "relative", height: VIEW_H, overflow: "hidden" }}>
        <Dashboard nowMin={nowMin} scroll={scroll} />
        <div style={{ position: "absolute", top: 0, left: 0, right: 0, zIndex: 5 }}><AppNav /></div>
      </div>
    </div>
  );
}

/* ── scene 0: hook ── */
function Hook({ t }) {
  const f = outQuint(seg(t, 0.1, 1.75));
  const m = OPEN_MIN + (940 - OPEN_MIN) * f;
  const v = fleetAt(Math.floor(m)).totals.LIVE.gross;
  const pop = 1 + 0.04 * Math.max(0, 1 - Math.abs(t - 1.78) / 0.12);
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", paddingBottom: 130 }}>
      <Line t={t} inAt={0.05} style={{ fontFamily: SG, fontSize: 46, fontWeight: 600, color: A.sub, letterSpacing: "0.02em" }}>Your NIFTY options desk.</Line>
      <Slam t={t} at={0.5} style={{ fontFamily: ORB, fontSize: 96, fontWeight: 900, color: A.ink, letterSpacing: "0.06em", textShadow: glowM, marginTop: 6, marginBottom: 34 }}>ON AUTOPILOT.</Slam>
      <div style={{ display: "flex", alignItems: "center", gap: 18, fontFamily: SG, marginBottom: 2, opacity: outCubic(seg(t, 0.0, 0.25)) }}>
        <span style={{ fontSize: 20, fontWeight: 700, letterSpacing: "0.1em", padding: "3px 12px", borderRadius: 4, border: `2px solid ${A.cyan}`, color: A.cyan, boxShadow: "0 0 14px rgba(60,231,255,.45)" }}>LIVE</span>
        <span style={{ fontFamily: MONO, fontSize: 26, color: A.sub }}>MTM today · {hhmm(m)}</span>
      </div>
      <div style={{ fontFamily: ORB, fontVariantNumeric: "tabular-nums", fontSize: 140, fontWeight: 800, color: v >= 0 ? A.green : A.red,
        textShadow: `0 0 18px ${v >= 0 ? A.green : A.red}, 0 0 60px rgba(57,255,140,0.4)`, transform: `scale(${pop})`, opacity: outCubic(seg(t, 0.0, 0.2)) }}>
        {inr(v)}
      </div>
    </div>
  );
}

/* ── scene 1: auto trades — alerts pop in over the live dashboard, one a beat (times from demoData) ── */
const ALERTS = [
  { at: 2.5, time: "09:16", id: "TSG_V1", book: "Paper", kind: "ENTRY", text: "4 legs filled", col: A.cyan },
  { at: 3.0, time: "09:20", id: "IC_V2", book: "Live", kind: "ENTRY", text: "4 legs filled", col: A.cyan },
  { at: 3.5, time: "09:45", id: "ORB_V1", book: "Paper", kind: "ENTRY", text: "breakout · 1 leg", col: A.cyan },
  { at: 4.0, time: "10:20", id: "VET_V1", book: "Paper", kind: "ENTRY", text: "trend · 1 leg", col: A.cyan },
  { at: 4.5, time: "10:40", id: "ORB_V1", book: "Paper", kind: "TARGET HIT", text: "+₹4,150", col: A.green },
  { at: 5.0, time: "11:05", id: "VET_V1", book: "Paper", kind: "STOP-LOSS HIT", text: "−₹3,860", col: A.red },
];
function Alerts({ t }) {
  const shown = ALERTS.filter((a) => t >= a.at).slice(-4).reverse();
  const out = seg(t, S.exec - 0.25, S.exec);
  if (!shown.length || out >= 1) return null;
  return (
    <div style={{ position: "absolute", right: 56, top: 268, width: 470, display: "flex", flexDirection: "column", gap: 14, zIndex: 25, opacity: 1 - out }}>
      {shown.map((a, i) => {
        const g = outBack(seg(t, a.at, a.at + 0.22));
        return (
          <div key={a.at} style={{ transform: i === 0 ? `translateX(${(1 - g) * 120}px) scale(${lerp(1.08, 1, g)})` : undefined, opacity: i === 0 ? clamp(g * 2) : 1, filter: i ? `brightness(${1 - i * 0.14})` : undefined,
            background: "#160c3a", border: `1px solid ${a.col}`, borderRadius: 10, padding: "14px 18px", boxShadow: `0 0 22px ${a.col}55, 0 14px 40px rgba(0,0,0,.5)`,
            fontFamily: SG, display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontFamily: ORB, fontSize: 16, fontWeight: 800, letterSpacing: "0.08em", color: a.col }}>{a.kind}</span>
              <span style={{ fontSize: 14, color: A.muted }}>{a.book}</span>
              <span style={{ marginLeft: "auto", fontFamily: MONO, fontSize: 16, color: A.sub }}>{a.time}</span>
            </div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
              <i style={{ width: 10, height: 10, borderRadius: 5, background: META[a.id].accent, display: "inline-block" }} />
              <b style={{ fontSize: 22, color: A.ink }}>{nameOf(a.id)}</b>
              <span style={{ fontSize: 20, fontWeight: 600, color: a.kind === "ENTRY" ? A.sub : a.col, fontVariantNumeric: "tabular-nums" }}>{a.text}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ── scene 2: execution — three facts slam in over the (dimmed) dashboard ── */
const EXEC = [
  { icon: "🛰️", k: "Static-IP order relay", v: "Your own relay, set up in one click from Connections", col: A.cyan },
  { icon: "🛡️", k: "Stops at the broker", v: "Stop-loss and target held as Zerodha GTTs", col: A.magenta },
  { icon: "⏱️", k: "Slippage on every order", v: "Send, ack and fill stamped and measured", col: A.green },
];
function ExecTiles({ t }) {
  const out = seg(t, S.bt - 0.3, S.bt - 0.05);
  if (t < S.exec + 0.6 || out >= 1) return null;
  return (
    <div style={{ position: "absolute", left: 0, right: 0, top: 470, display: "flex", justifyContent: "center", gap: 28, zIndex: 25, opacity: 1 - out }}>
      {EXEC.map((e, i) => {
        const at = S.exec + 1.0 + i * 0.5;
        if (t < at) return <div key={e.k} style={{ width: 520 }} />;
        const g = seg(t, at, at + 0.18);
        return (
          <div key={e.k} style={{ width: 520, boxSizing: "border-box", transform: `scale(${lerp(1.4, 1, outBack(g))})`, opacity: clamp(g * 3),
            background: "rgba(22,12,58,0.96)", border: `2px solid ${e.col}`, borderRadius: 14, padding: "28px 30px", boxShadow: `0 0 40px ${e.col}44, 0 24px 60px rgba(0,0,0,.6)` }}>
            <div style={{ fontSize: 54, lineHeight: 1 }}>{e.icon}</div>
            <div style={{ fontFamily: ORB, fontSize: 28, fontWeight: 800, color: A.ink, letterSpacing: "0.03em", marginTop: 18, lineHeight: 1.2 }}>{e.k}</div>
            <div style={{ fontFamily: SG, fontSize: 24, color: A.sub, marginTop: 10, lineHeight: 1.3 }}>{e.v}</div>
          </div>
        );
      })}
    </div>
  );
}

/* ── the dashboard across scenes 1–2: whole window, then scrolled to the strategies and dimmed ── */
function DashScenes({ t }) {
  const nowMin = OPEN_MIN + (850 - OPEN_MIN) * inOutCubic(seg(t, 2.25, 5.6)) + Math.max(0, t - 5.6) * 1.6;
  const push = inOutCubic(seg(t, S.exec, S.exec + 0.75));
  const c = REG.cards;
  const scroll = (c ? Math.max(0, c.y - NAV_H - 14) : 420) * push;
  const s = lerp(0.8, 0.9, push) + 0.02 * seg(t, S.exec + 0.75, S.bt);
  const cx = W / 2, cy = (VIEW_H + TITLE_H) / 2;
  const fx = lerp(860, 960, push), fy = lerp(660, 600, push);
  const enter = outCubic(seg(t, 2.0, 2.55));
  const leave = inOutCubic(seg(t, S.bt - 0.4, S.bt + 0.1));
  const dim = 0.62 * outCubic(seg(t, S.exec + 0.5, S.exec + 0.9));
  const transform = `translate(${fx - cx * s}px, ${fy - cy * s + (1 - enter) * 120 + leave * 140}px) scale(${s * (1 - 0.1 * leave)})`;
  return (
    <>
      <div style={{ position: "absolute", left: 0, top: 0, transformOrigin: "0 0", transform, opacity: Math.min(enter, 1 - leave), filter: dim > 0 ? `blur(${dim * 4}px)` : undefined }}>
        <AppWindow nowMin={nowMin} scroll={scroll} />
        {dim > 0 && <div style={{ position: "absolute", inset: 0, borderRadius: 12, background: `rgba(7,3,26,${dim})` }} />}
      </div>
      <Head t={t} at={S.auto + 0.1} out={S.exec - 0.25} kicker="TRADES ITSELF." top={56}>
        Signal → order → exit. <span style={{ color: A.ink }}>Hands-free</span>, on Zerodha.
      </Head>
      <Alerts t={t} />
      <Head t={t} at={S.exec + 0.1} out={S.bt - 0.3} kicker="SLICK EXECUTION." top={190} color={A.cyan}>
        Built for the moment the signal fires.
      </Head>
      <ExecTiles t={t} />
    </>
  );
}

/* ── scene 3: backtests ── */
function Backtest({ t }) {
  if (t < S.bt - 0.05 || t >= S.comp) return null;
  const out = seg(t, S.comp - 0.25, S.comp);
  const y = 2020 + Math.min(6, Math.floor(Math.max(0, t - (S.bt + 0.6)) / 0.16));
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", paddingTop: 92, opacity: 1 - out }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 34 }}>
        <Slam t={t} at={S.bt} from={2.2} style={{ fontFamily: ORB, fontSize: 210, fontWeight: 900, lineHeight: 1, color: A.cyan, textShadow: "0 0 30px rgba(60,231,255,.7)" }}>6+</Slam>
        <div>
          <Slam t={t} at={S.bt + 0.25} style={{ fontFamily: ORB, fontSize: 92, fontWeight: 900, color: A.ink, letterSpacing: "0.05em", textShadow: glowM, lineHeight: 1 }}>YEARS</Slam>
          <Slam t={t} at={S.bt + 0.5} style={{ fontFamily: ORB, fontSize: 52, fontWeight: 800, color: A.magenta, letterSpacing: "0.08em", lineHeight: 1.2, marginTop: 8 }}>BACKTESTED</Slam>
        </div>
      </div>
      <Line t={t} inAt={S.bt + 0.6} style={{ fontFamily: SG, fontSize: 38, color: A.sub, marginTop: 18 }}>
        on 1-minute NIFTY options data · <span style={{ fontFamily: MONO, color: A.ink }}>2020 → <span style={{ color: A.cyan }}>{y}</span></span>
      </Line>
      <Line t={t} inAt={S.bt + 1.6} style={{ fontFamily: ORB, fontSize: 34, fontWeight: 800, color: A.ink, letterSpacing: "0.05em", marginTop: 66 }}>EVERY RUPEE OF FEES, COUNTED</Line>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 440px)", gap: 18, marginTop: 26 }}>
        {FEES.map(([k, v], i) => {
          const at = S.bt + 2.0 + i * 0.18;
          if (t < at) return <div key={k} style={{ height: 96 }} />;
          const g = seg(t, at, at + 0.16);
          return (
            <div key={k} style={{ transform: `scale(${lerp(1.3, 1, outBack(g))})`, opacity: clamp(g * 3), background: "rgba(22,12,58,0.92)", border: `1px solid ${i % 2 ? A.cyan : A.magenta}`,
              borderRadius: 10, padding: "16px 22px", boxShadow: `0 0 18px ${i % 2 ? "rgba(60,231,255,.22)" : "rgba(255,60,172,.22)"}` }}>
              <div style={{ fontFamily: SG, fontSize: 24, fontWeight: 700, color: A.ink }}>{k}</div>
              <div style={{ fontFamily: MONO, fontSize: 22, color: A.sub, marginTop: 4 }}>{v}</div>
            </div>
          );
        })}
      </div>
      <Line t={t} inAt={S.bt + 3.2} style={{ fontFamily: SG, fontSize: 28, color: A.muted, marginTop: 22 }}>Zerodha's published rates, charged on every backtest trade.</Line>
    </div>
  );
}

/* ── scene 4: compounding — lot sizing (illustrative), never an equity curve ── */
const LADDER = [10, 12, 14, 11, 15, 18, 20, 20];
function Compound({ t }) {
  if (t < S.comp - 0.05 || t >= S.tg) return null;
  const out = seg(t, S.tg - 0.25, S.tg);
  const on = outCubic(seg(t, S.comp + 0.55, S.comp + 0.75));
  const U = 15;   // px per lot
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", paddingTop: 80, opacity: 1 - out }}>
      <div style={{ display: "flex", alignItems: "center", gap: 34 }}>
        <Slam t={t} at={S.comp} style={{ fontFamily: ORB, fontSize: 92, fontWeight: 900, color: A.ink, letterSpacing: "0.05em", textShadow: glowM }}>COMPOUNDING</Slam>
        <Slam t={t} at={S.comp + 0.5} style={{ width: 150, height: 76, borderRadius: 38, background: on > 0.5 ? A.green : "#2a1a5e", position: "relative", border: `3px solid ${A.ink}`, boxSizing: "border-box",
          boxShadow: on > 0.5 ? "0 0 26px rgba(57,255,140,.7)" : "none" }}>
          <i style={{ position: "absolute", top: 7, left: lerp(8, 80, on), width: 56, height: 56, borderRadius: 28, background: A.ink }} />
        </Slam>
      </div>
      <Line t={t} inAt={S.comp + 0.4} style={{ fontFamily: SG, fontSize: 36, color: A.sub, marginTop: 10 }}>Built into the backtester: lots grow as equity does.</Line>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", width: 980, height: 360, marginTop: 40, position: "relative" }}>
        <div style={{ position: "absolute", left: 0, right: 0, bottom: 20 * U, borderTop: `3px dashed ${A.yellow}`, opacity: seg(t, S.comp + 1.9, S.comp + 2.1) }}>
          <span style={{ position: "absolute", left: 0, top: -40, fontFamily: ORB, fontSize: 22, fontWeight: 800, color: A.yellow, letterSpacing: "0.08em" }}>MAX LOTS CAP</span>
        </div>
        {LADDER.map((v, i) => {
          const at = S.comp + 0.9 + i * 0.12;
          const g = outBack(seg(t, at, at + 0.22));
          const dd = i === 3;
          const col = dd ? A.red : i % 2 ? A.cyan : A.magenta;
          return (
            <div key={i} style={{ width: 92, display: "flex", flexDirection: "column", alignItems: "center" }}>
              <span style={{ fontFamily: ORB, fontSize: 28, fontWeight: 800, color: dd ? A.red : A.ink, opacity: clamp(g), marginBottom: 6, position: "relative", zIndex: 1, padding: "0 6px", background: A.bg, borderRadius: 4 }}>{v}</span>
              <div style={{ width: 92, height: Math.max(0, v * U * g), borderRadius: "6px 6px 2px 2px", background: `linear-gradient(180deg, ${col}, ${col}33)`, boxShadow: `0 0 18px ${col}88` }} />
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", width: 980, fontFamily: SG, fontSize: 22, color: A.muted, marginTop: 10 }}>
        <span>lots per trade · illustrative</span><span style={{ color: A.red }}>▲ drawdown guard cuts size</span>
      </div>
      <Line t={t} inAt={S.comp + 2.4} style={{ display: "flex", gap: 14, marginTop: 30, fontFamily: SG, fontSize: 24, fontWeight: 600 }}>
        {["Calendar-step or equity-based", "Max-lots cap", "Drawdown guard", "Tax set aside each FY"].map((x, i) => (
          <span key={x} style={{ padding: "8px 18px", borderRadius: 4, border: `1px solid ${i % 2 ? A.cyan : A.magenta}`, color: A.ink, background: "rgba(22,12,58,.9)" }}>{x}</span>
        ))}
      </Line>
    </div>
  );
}

/* ── scene 5: Telegram — alerts as they happen, then the 15:40 day card (the real renderer's PNG) ── */
const TG_MSGS = [
  { at: S.tg + 0.5, head: "🎯 TRADE ENTRY", badge: "🟢 LIVE", lines: [["Strategy", "Icarus"], ["Legs", "4 · NIFTY weekly"]], time: "09:20" },
  { at: S.tg + 1.0, head: "🟢 TARGET HIT", badge: "📄 PAPER", lines: [["Strategy", "Outrider"], ["P&L", "₹4,150"]], time: "10:40" },
  { at: S.tg + 1.5, head: "🛑 STOP-LOSS HIT", badge: "📄 PAPER", lines: [["Strategy", "Velvet"], ["Loss", "₹-3,860"]], time: "11:05" },
];
function TelegramScene({ t }) {
  if (t < S.tg - 0.05 || t >= S.outro + 0.1) return null;
  const a = outCubic(seg(t, S.tg, S.tg + 0.45));
  const out = seg(t, S.outro - 0.2, S.outro + 0.05);
  const card = outCubic(seg(t, S.tg + 2.2, S.tg + 2.6));
  const lift = 330 * inOutCubic(seg(t, S.tg + 2.0, S.tg + 2.5));
  return (
    <div style={{ position: "absolute", inset: 0, opacity: 1 - out }}>
      <div style={{ position: "absolute", left: 110, top: 300, width: 720 }}>
        <Slam t={t} at={S.tg} style={{ fontFamily: ORB, fontSize: 64, fontWeight: 900, color: A.ink, letterSpacing: "0.04em", lineHeight: 1.1, textShadow: glowM, transformOrigin: "0 50%" }}>
          EVERY TRADE,<br />IN TELEGRAM.
        </Slam>
        <Line t={t} inAt={S.tg + 0.5} style={{ fontFamily: SG, fontSize: 34, color: A.sub, marginTop: 24, lineHeight: 1.35 }}>
          Entries, targets, stops and rejections, the moment they happen.
        </Line>
        <Line t={t} inAt={S.tg + 2.3} style={{ fontFamily: SG, fontSize: 34, color: A.sub, marginTop: 18, lineHeight: 1.35 }}>
          <span style={{ fontFamily: ORB, fontWeight: 800, color: A.cyan }}>15:40</span> · the day's card, Live and Paper apart.
        </Line>
      </div>
      <div style={{ position: "absolute", right: 110, top: 70, width: 860, height: 940, borderRadius: 22, overflow: "hidden", background: "#0e1621",
        boxShadow: "0 40px 120px rgba(0,0,0,.6), 0 0 0 1px #5a45a0, 0 0 70px rgba(255,60,172,.25)", transform: `translateX(${(1 - a) * 260}px)`, opacity: a }}>
        <div style={{ position: "absolute", left: 0, right: 0, top: 0, height: 76, background: "#17212b", display: "flex", alignItems: "center", gap: 16, padding: "0 24px", fontFamily: SG, zIndex: 2 }}>
          <div style={{ width: 46, height: 46, borderRadius: 23, background: "linear-gradient(135deg,#ff3cac,#7b2ff7)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 24 }}>⚡</div>
          <div>
            <div style={{ color: "#f5f5f5", fontSize: 20, fontWeight: 600 }}>Scalp Terminal</div>
            <div style={{ color: "#6c7883", fontSize: 15 }}>bot</div>
          </div>
        </div>
        <div style={{ position: "absolute", left: 0, right: 0, top: 96 - lift, padding: "0 28px", display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 14 }}>
          {TG_MSGS.map((m) => {
            const g = outCubic(seg(t, m.at, m.at + 0.25));
            if (g <= 0) return null;
            return (
              <div key={m.at} style={{ background: "#182533", borderRadius: 16, padding: "14px 18px 8px", width: 470, fontFamily: SG, color: "#f5f5f5", fontSize: 21, lineHeight: 1.45,
                opacity: g, transform: `translateY(${(1 - g) * 30}px)` }}>
                <div><b>{m.head}</b> <span style={{ fontSize: 18 }}>{m.badge}</span></div>
                {m.lines.map(([k, v]) => <div key={k} style={{ color: "#c8d1da" }}>{k}: <b style={{ color: "#f5f5f5" }}>{v}</b></div>)}
                <div style={{ textAlign: "right", color: "#6c7883", fontSize: 14 }}>{m.time}</div>
              </div>
            );
          })}
          {card > 0 && (
            <div style={{ background: "#182533", borderRadius: 16, padding: 8, opacity: card, transform: `translateY(${(1 - card) * 40}px) scale(${0.96 + 0.04 * card})`, transformOrigin: "0 100%" }}>
              <img src="eod_card.png" alt="" style={{ width: 788, display: "block", borderRadius: 11 }} />
              <div style={{ textAlign: "right", color: "#6c7883", fontFamily: SG, fontSize: 14, padding: "6px 6px 0" }}>15:40</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── scene 6: outro ── */
function Outro({ t }) {
  if (t < S.outro - 0.05) return null;
  const a = seg(t, S.outro - 0.05, S.outro + 0.4);
  const flick = 0.9 + 0.1 * Math.sin(t * 37) * Math.sin(t * 11);   // neon hum, pure function of t
  const o = S.outro;
  return (
    <div style={{ position: "absolute", inset: 0, opacity: a }}>
      <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", paddingBottom: 170 }}>
        <Line t={t} inAt={o + 0.15} dy={34} style={{ fontFamily: ORB, fontSize: 118, fontWeight: 900, color: A.ink, letterSpacing: "0.05em", display: "flex", alignItems: "center", gap: 28,
          textShadow: `0 0 ${18 * flick}px rgba(255,60,172,.95), 0 0 70px rgba(255,60,172,.55)` }}>
          <span style={{ fontSize: 100, textShadow: "none" }}>⚡</span>SCALP TERMINAL
        </Line>
        <Line t={t} inAt={o + 0.45} style={{ fontFamily: SG, fontSize: 36, fontWeight: 500, color: A.sub, marginTop: 24 }}>
          Algorithmic options desk · NIFTY · Zerodha
        </Line>
        <Line t={t} inAt={o + 0.7} style={{ display: "flex", gap: 12, marginTop: 34, fontFamily: SG, fontSize: 22, fontWeight: 600 }}>
          {["Auto trades", "Static-IP relay", "6+ yrs backtests", "Compounding", "Telegram alerts", "macOS · Windows"].map((x, i) => (
            <span key={x} style={{ padding: "8px 16px", borderRadius: 4, border: `1px solid ${i % 2 ? A.cyan : A.magenta}`, color: A.ink, background: "rgba(22,12,58,.85)",
              boxShadow: `0 0 14px ${i % 2 ? "rgba(60,231,255,.3)" : "rgba(255,60,172,.3)"}` }}>{x}</span>
          ))}
        </Line>
        <Line t={t} inAt={o + 1.0} style={{ fontFamily: SG, fontSize: 26, fontWeight: 600, color: A.magenta, marginTop: 28 }}>#CompoundingInTrading</Line>
        <Line t={t} inAt={o + 1.2} style={{ fontFamily: SG, fontSize: 18, color: A.muted, marginTop: 18 }}>
          Backtests use historical data and don't guarantee future results.
        </Line>
      </div>
    </div>
  );
}

function Video({ t }) {
  // the horizon sinks while the app is on screen, rises for the other scenes
  const low = inOutCubic(seg(t, 1.8, 2.5)) * (1 - inOutCubic(seg(t, S.bt - 0.4, S.bt + 0.2)));
  return (
    <div style={{ position: "relative", width: 1920, height: 1080, overflow: "hidden", background: A.bg }}>
      <ArcadeStage t={t} horizon={lerp(800, 960, low)} glow={1 - 0.5 * low} />
      {t < 2.05 && <Hook t={t} />}
      {t >= 1.95 && t < S.bt + 0.15 && <DashScenes t={t} />}
      <Backtest t={t} />
      <Compound t={t} />
      <TelegramScene t={t} />
      <Outro t={t} />
      <DemoTag color={A.sub} bg="rgba(11,6,33,0.85)" border="#3b2a6e" dot={A.magenta} font={SG} bottom={22} />
    </div>
  );
}

export const VIDEO_META = { width: 1920, height: 1080, duration: S.end, theme: "arcade", poster: 4.6, warmup: [0.5, 2.5, 4.0, 7.5] };
mount(Video, VIDEO_META);
