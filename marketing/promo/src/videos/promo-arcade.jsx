// Promo video (landscape, 1920×1080, 20 s): the REDESIGNED Dashboard (the "Calm" page,
// DASH_CALM_20261117) in the app's Arcade theme. Renders the REAL CalmHero / CalmChart /
// StrategyCard / IdleCard components from the repo, fed with fictional demo data, as a
// pure function of time t (seconds). Structure follows promo-landscape.jsx.
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
import { DemoTag } from "../lib/common";

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
const A = { bg: "#0b0621", bg2: "#160c3a", magenta: "#ff3cac", cyan: "#3ce7ff", green: "#39ff8c", ink: "#f2e9ff", sub: "#cdbff0", muted: "#a493d6", line: "#3b2a6e" };
const ORB = "'Orbitron', 'Space Grotesk', sans-serif";
const SG = "'Space Grotesk', 'Segoe UI', sans-serif";

/* ── timing helpers ── */
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const seg = (t, a, b) => clamp((t - a) / (b - a));
const outCubic = (x) => 1 - Math.pow(1 - x, 3);
const inOutCubic = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
const outQuint = (x) => 1 - Math.pow(1 - x, 5);
const lerp = (a, b, f) => a + (b - a) * f;
const hhmm = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(Math.floor(m % 60)).padStart(2, "0")}`;
const inr = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;

const W = 1680, VIEW_H = 900, TITLE_H = 30, NAV_H = 54;
const REG = { hero: null, cards: null, card: [] };

/* ── text that rises in, holds, and leaves ── */
function Line({ t, inAt, outAt, children, style, dy = 26 }) {
  const a = outCubic(seg(t, inAt, inAt + 0.3));
  const b = outAt == null ? 0 : seg(t, outAt, outAt + 0.22);
  const o = a * (1 - b);
  if (o <= 0.001) return null;
  return <div style={{ opacity: o, transform: `translateY(${(1 - a) * dy - b * 14}px)`, ...style }}>{children}</div>;
}

/* ── the Arcade stage behind everything: violet sky, magenta grid floor scrolling toward
   the viewer, a horizon glow and faint scanlines — the theme's own canvas, made to move ── */
function ArcadeStage({ t, horizon = 720, glow = 1 }) {
  const roll = (t * 46) % 46;   // floor rows travel one cell every second
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

/* ── the Calm dashboard body, assembled like pages/CalmDashboard.jsx ── */
function Dashboard({ nowMin, focusId, scroll }) {
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
    const rel = (el) => { let y = 0, x = 0, e = el; while (e && e !== root) { y += e.offsetTop; x += e.offsetLeft; e = e.offsetParent; } return { x, y, w: el.offsetWidth, h: el.offsetHeight }; };
    const h = root.querySelector("[data-testid=calm-hero]"); if (h) REG.hero = rel(h);
    const s = root.querySelector("[data-testid=calm-strategies]"); if (s) REG.cards = rel(s);
    REG.card = [...root.querySelectorAll("[data-testid=calm-card],[data-testid=calm-idle]")].map((el) => ({ id: el.getAttribute("data-strategy"), ...rel(el) }));
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
                meter={undefined} expanded={false} onToggle={() => {}} selected={focusId === c.id} onFocus={() => {}} />
            ))}
            {idle.map((r) => (
              <IdleCard key={r.id} id={r.id} name={nameOf(r.id)} accent={META[r.id]?.accent} mode={r.mode} meter={undefined}
                expanded={false} onToggle={() => {}} selected={focusId === r.id} onFocus={() => {}} />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function Spot({ reg, a, scroll }) {
  if (!reg || a <= 0.001) return null;
  const pad = 14;
  return (
    <div style={{ position: "absolute", left: reg.x - pad, top: reg.y - scroll - pad, width: reg.w + pad * 2, height: reg.h + pad * 2, borderRadius: 18,
      boxShadow: `0 0 0 4000px rgba(7,3,26,${0.74 * a}), 0 0 0 ${2 * a}px rgba(255,60,172,${0.95 * a}), 0 0 48px rgba(255,60,172,${0.4 * a})`, zIndex: 8, pointerEvents: "none" }} />
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

function AppWindow({ nowMin, focusId, scroll, spot }) {
  return (
    <div style={{ width: W, height: VIEW_H + TITLE_H, borderRadius: 12, overflow: "hidden", ...ARCADE_CANVAS, position: "relative",
      boxShadow: "0 40px 120px rgba(0,0,0,0.7), 0 0 0 1px #5a45a0, 0 0 60px rgba(255,60,172,0.25)" }}>
      <div style={{ height: TITLE_H, background: "#07031a", display: "flex", alignItems: "center", gap: 8, padding: "0 14px", borderBottom: "1px solid #3b2a6e", position: "relative", zIndex: 6 }}>
        {["#ff5f57", "#febc2e", "#28c840"].map((c) => <i key={c} style={{ width: 12, height: 12, borderRadius: 6, background: c, display: "inline-block" }} />)}
        <span style={{ flex: 1, textAlign: "center", fontFamily: SG, fontSize: 12.5, color: A.muted, marginRight: 52 }}>Scalp Terminal</span>
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
    <svg width="34" height="40" viewBox="0 0 17 20" style={{ position: "absolute", left: x, top: y, transform: `scale(${1 - press * 0.12})`, transformOrigin: "0 0", filter: "drop-shadow(0 3px 6px rgba(0,0,0,.5)) drop-shadow(0 0 6px rgba(255,60,172,.6))", zIndex: 20 }}>
      <path d="M1 1 L1 15.5 L4.6 12.2 L7 18 L9.6 16.9 L7.2 11.3 L12 11.3 Z" fill="#f2e9ff" stroke="#07031a" strokeWidth="1.1" strokeLinejoin="round" />
    </svg>
  );
}

/* ── camera: map window point (cx, cy) to frame point (fx, fy) at scale s ── */
const scrollAt = (t) => {
  const c = REG.cards;
  const target = c ? Math.max(0, c.y - NAV_H - 14) : 420;   // the Strategies header settles just under the nav
  return target * inOutCubic(seg(t, 10.0, 10.75));
};
function cam(t) {
  const h = REG.hero || { x: 32, y: 24, w: 1616, h: 400 };
  const c = REG.cards || { x: 32, y: 460, w: 1616, h: 620 };
  const whole = { cx: W / 2, cy: (VIEW_H + TITLE_H) / 2, s: 0.84, fx: 960, fy: 610 };
  const hero = { cx: h.x + h.w / 2, cy: TITLE_H + h.y + h.h / 2, s: 1.0, fx: 960, fy: 420 };
  const sc = REG.cards ? Math.max(0, c.y - NAV_H - 14) : 420;
  const cards = { cx: c.x + c.w / 2, cy: TITLE_H + c.y - sc + Math.min(c.h, VIEW_H - NAV_H - 14) / 2, s: 0.98, fx: 960, fy: 640 };
  const mix = (P, Q, f) => ({ cx: lerp(P.cx, Q.cx, f), cy: lerp(P.cy, Q.cy, f), s: lerp(P.s, Q.s, f), fx: lerp(P.fx, Q.fx, f), fy: lerp(P.fy, Q.fy, f) });
  if (t < 6.0) return { ...whole, s: lerp(0.84, 0.875, seg(t, 2.0, 6.0)) };
  const w2 = { ...whole, s: 0.875 };
  if (t < 10.0) { const f = inOutCubic(seg(t, 6.0, 6.75)); const p = mix(w2, hero, f); p.s += 0.04 * seg(t, 6.75, 10); return p; }
  const h2 = { ...hero, s: 1.04 };
  return mix(h2, cards, inOutCubic(seg(t, 10.0, 10.75)));
}
function spotAt(t) {
  if (t >= 6.4 && t < 10.0) return { reg: REG.hero, a: outCubic(seg(t, 6.45, 6.9)) * (1 - seg(t, 9.8, 10.0)) };
  if (t >= 10.2 && t < 14.3) return { reg: REG.cards, a: outCubic(seg(t, 10.3, 10.75)) * (1 - seg(t, 13.9, 14.15)) };
  return null;
}

/* ── scenes ── */
function Hook({ t }) {
  const f = outQuint(seg(t, 0.1, 1.75));
  const m = OPEN_MIN + (940 - OPEN_MIN) * f;
  const v = fleetAt(Math.floor(m)).totals.LIVE.gross;
  const pop = 1 + 0.04 * Math.max(0, 1 - Math.abs(t - 1.78) / 0.12);
  const col = v >= 0 ? A.green : "#ff4d6d";
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", paddingBottom: 120 }}>
      <Line t={t} inAt={0.05} style={{ fontFamily: ORB, fontSize: 58, fontWeight: 800, color: A.ink, letterSpacing: "0.04em", textTransform: "uppercase", marginBottom: 48,
        textShadow: "0 0 22px rgba(255,60,172,0.65)" }}>
        One number. The whole day.
      </Line>
      <div style={{ display: "flex", alignItems: "center", gap: 18, fontFamily: SG, marginBottom: 4, opacity: outCubic(seg(t, 0.0, 0.25)) }}>
        <span style={{ fontSize: 22, fontWeight: 700, letterSpacing: "0.1em", padding: "3px 14px", borderRadius: 4, border: `2px solid ${A.cyan}`, color: A.cyan, boxShadow: "0 0 14px rgba(60,231,255,.45)" }}>LIVE</span>
        <span style={{ fontFamily: "'JetBrains Mono Variable', monospace", fontSize: 30, color: A.sub }}>MTM today · {hhmm(m)}</span>
      </div>
      <div style={{ fontFamily: ORB, fontVariantNumeric: "tabular-nums", fontSize: 176, fontWeight: 800, letterSpacing: "0.01em",
        color: col, textShadow: `0 0 18px ${col}, 0 0 60px rgba(57,255,140,0.45)`, transform: `scale(${pop})`, opacity: outCubic(seg(t, 0.0, 0.2)) }}>
        {inr(v)}
      </div>
    </div>
  );
}

function Captions({ t }) {
  const cap = { fontFamily: SG, fontWeight: 700, color: A.ink, letterSpacing: "-0.01em", textShadow: "0 2px 18px rgba(7,3,26,0.9)" };
  return (
    <>
      <div style={{ position: "absolute", left: 0, right: 0, top: 70, textAlign: "center" }}>
        <Line t={t} inAt={2.35} outAt={5.78} style={{ ...cap, fontSize: 60 }}>
          The new Dashboard. <span style={{ fontFamily: ORB, fontWeight: 800, letterSpacing: "0.04em", color: A.magenta, textShadow: "0 0 20px rgba(255,60,172,.7)" }}>ARCADE</span> <span style={{ color: A.sub }}>mode.</span>
        </Line>
      </div>
      <div style={{ position: "absolute", left: 0, right: 0, top: 820, textAlign: "center" }}>
        <Line t={t} inAt={6.55} outAt={9.78} style={{ ...cap, fontSize: 62 }}>
          <span style={{ color: A.cyan }}>Live</span> and <span style={{ color: A.sub }}>Paper</span>. Side by side. Never summed.
        </Line>
      </div>
      <div style={{ position: "absolute", left: 0, right: 0, top: 64, textAlign: "center" }}>
        <Line t={t} inAt={10.55} outAt={13.78} style={{ ...cap, fontSize: 60 }}>
          Every strategy gets <span style={{ color: A.magenta }}>its own card.</span>
        </Line>
      </div>
    </>
  );
}

const WALK = ["TMA_V2", "IC_V2", "TSG_V1", "ORB_V1", "VET_V1", "BRK_V1"];
function DashScenes({ t }) {
  // session clock: draws 09:15 → 14:10 in the reveal, then creeps
  const nowMin = t < 5.6 ? OPEN_MIN + (850 - OPEN_MIN) * inOutCubic(seg(t, 2.25, 5.6)) : 850 + (t - 5.6) * 1.6;
  const c = cam(t);
  const sc = scrollAt(t);
  // cursor walk across the cards (scene 4), one card per beat
  let focusId = null, cur = null;
  const regs = WALK.map((id) => REG.card.find((r) => r.id === id)).filter(Boolean);
  if (t >= 10.5 && t < 14.2 && regs.length) {
    const idx = Math.max(0, Math.min(regs.length - 1, Math.floor((t - 10.75) / 0.5)));
    const prev = Math.max(0, idx - 1);
    const local = clamp((t - 10.75 - idx * 0.5) / 0.22);
    const r0 = regs[t < 10.75 ? 0 : prev], r1 = regs[idx];
    const g = t < 10.75 || idx === 0 ? 1 : inOutCubic(local);
    const wx = lerp(r0.x, r1.x, g) + 120, wy = TITLE_H + lerp(r0.y, r1.y, g) + 60 - sc;
    const fx = c.fx + (wx - c.cx) * c.s, fy = c.fy + (wy - c.cy) * c.s;
    const enter = outCubic(seg(t, 10.5, 10.85));
    cur = { x: lerp(fx + 220, fx, enter), y: lerp(fy + 160, fy, enter), press: Math.max(0, 1 - Math.abs(local - 1) * 4) * (t >= 10.75 ? 1 : 0) };
    if (t >= 10.9) focusId = regs[idx].id;
  }
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
        <AppWindow nowMin={nowMin} focusId={focusId} scroll={sc} spot={spotAt(t)} />
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
        <Line t={t} inAt={14.35} style={{ fontFamily: ORB, fontSize: 40, fontWeight: 800, color: A.cyan, marginBottom: 14, letterSpacing: "0.06em", textShadow: "0 0 16px rgba(60,231,255,.6)" }}>15:40</Line>
        <Line t={t} inAt={14.45} style={{ fontFamily: SG, fontSize: 72, fontWeight: 700, color: A.ink, letterSpacing: "-0.01em", lineHeight: 1.08 }}>
          The day lands<br />in <span style={{ color: A.magenta }}>Telegram.</span>
        </Line>
      </div>
      <div style={{ position: "absolute", right: 120, top: 70, width: 900, height: 940, borderRadius: 22, overflow: "hidden", background: "#0e1621",
        boxShadow: "0 40px 120px rgba(0,0,0,.6), 0 0 0 1px #5a45a0, 0 0 70px rgba(255,60,172,.25)", transform: `translateX(${(1 - a) * 260}px)`, opacity: a }}>
        <div style={{ height: 76, background: "#17212b", display: "flex", alignItems: "center", gap: 16, padding: "0 24px", fontFamily: SG }}>
          <div style={{ width: 46, height: 46, borderRadius: 23, background: "linear-gradient(135deg,#ff3cac,#7b2ff7)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 24 }}>⚡</div>
          <div>
            <div style={{ color: "#f5f5f5", fontSize: 20, fontWeight: 600 }}>Scalp Terminal</div>
            <div style={{ color: "#6c7883", fontSize: 15 }}>bot</div>
          </div>
        </div>
        <div style={{ padding: "26px 28px", display: "flex", flexDirection: "column", alignItems: "flex-start" }}>
          <div style={{ background: "#182533", borderRadius: 16, padding: 8, opacity: msg, transform: `translateY(${(1 - msg) * 40}px) scale(${0.96 + 0.04 * msg})`, transformOrigin: "0 100%" }}>
            <img src="eod_card.png" alt="" style={{ width: 828, display: "block", borderRadius: 11 }} />
            <div style={{ textAlign: "right", color: "#6c7883", fontFamily: SG, fontSize: 14, padding: "6px 6px 0" }}>15:40</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Outro({ t }) {
  const a = seg(t, 16.95, 17.4);
  if (t < 16.95) return null;
  const flick = 0.9 + 0.1 * Math.sin(t * 37) * Math.sin(t * 11);   // neon hum, pure function of t
  return (
    <div style={{ position: "absolute", inset: 0, opacity: a }}>
      <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", paddingBottom: 170 }}>
        <Line t={t} inAt={17.15} dy={34} style={{ fontFamily: ORB, fontSize: 118, fontWeight: 900, color: A.ink, letterSpacing: "0.05em", display: "flex", alignItems: "center", gap: 28,
          textShadow: `0 0 ${18 * flick}px rgba(255,60,172,.95), 0 0 70px rgba(255,60,172,.55)` }}>
          <span style={{ fontSize: 100, textShadow: "none" }}>⚡</span>SCALP TERMINAL
        </Line>
        <Line t={t} inAt={17.45} style={{ fontFamily: SG, fontSize: 36, fontWeight: 500, color: A.sub, marginTop: 26 }}>
          Algorithmic options desk · NIFTY · Zerodha
        </Line>
        <Line t={t} inAt={17.7} style={{ display: "flex", gap: 14, marginTop: 34, fontFamily: SG, fontSize: 22, fontWeight: 600 }}>
          {["Calm Dashboard", "Live + Paper", "Six themes", "Telegram EOD", "macOS · Windows"].map((x, i) => (
            <span key={x} style={{ padding: "8px 18px", borderRadius: 4, border: `1px solid ${i % 2 ? A.cyan : A.magenta}`, color: A.ink, background: "rgba(22,12,58,.85)",
              boxShadow: `0 0 14px ${i % 2 ? "rgba(60,231,255,.3)" : "rgba(255,60,172,.3)"}` }}>{x}</span>
          ))}
        </Line>
        <Line t={t} inAt={18.0} style={{ fontFamily: SG, fontSize: 18, color: A.muted, marginTop: 30 }}>
          Backtests use historical data and don't guarantee future results.
        </Line>
      </div>
    </div>
  );
}

function Video({ t }) {
  // the horizon sinks while the app is on screen, rises for hook and outro
  const low = inOutCubic(seg(t, 1.8, 2.5)) * (1 - inOutCubic(seg(t, 16.9, 17.5)));
  return (
    <div style={{ position: "relative", width: 1920, height: 1080, overflow: "hidden", background: A.bg }}>
      <ArcadeStage t={t} horizon={lerp(700, 900, low)} glow={1 - 0.5 * low} />
      {t < 2.0 && <Hook t={t} />}
      {t >= 1.95 && t < 17.1 && <DashScenes t={t} />}
      <TelegramScene t={t} />
      <Outro t={t} />
      <DemoTag color={A.sub} bg="rgba(11,6,33,0.85)" border="#3b2a6e" dot={A.magenta} font={SG} bottom={22} />
    </div>
  );
}

export const VIDEO_META = { width: 1920, height: 1080, duration: 20, theme: "arcade", poster: 8.6, warmup: [0.5, 2.5, 8.0, 12.0] };
mount(Video, VIDEO_META);
