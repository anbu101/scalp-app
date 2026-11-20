// Reel (vertical, 1080×1920, 26 s): the vertical cut of promo-arcade. Same feature story — auto
// trades, execution, backtests, compounding, Telegram — over the redesigned Dashboard's PHONE layout
// (CalmHero / StrategyCard / IdleCard with compact, the REAL components, fictional demo data) in the
// Arcade theme. Scene copy comes from lib/arcade.jsx, shared with the landscape cut.
// Sections on 2 s bars at 120 BPM; every slam sits ON the beat grid (0.25 s steps) so the
// music's hits (music/reel-arcade.py, from the Mass trailer's sound) land with them.
// fence: PROMO_ARCADE_20261120
import { mount } from "../lib/runtime";
import "@app/index.css";
import "@fontsource-variable/jetbrains-mono";
import CalmHero from "@app/components/calm/CalmHero";
import { StrategyCard, IdleCard } from "@app/components/calm/StrategyCard";
import { C, T, SP } from "@app/components/calm/calm";
import { fleetAt, OPEN_MIN } from "../lib/demoData";
import { DemoTag, FEES } from "../lib/common";
import { META, nameOf, A, ORB, SG, MONO, glowM, glowC, S, clamp, seg, outCubic, inOutCubic, outQuint, outBack, lerp, hhmm, inr,
  Line, Slam, ArcadeStage, marketAt, buildCards, ARCADE_CANVAS, ALERTS, EXEC, LADDER, TG_MSGS } from "../lib/arcade";

const PW = 400, PH = 700, PS = 1.95;   // phone screen in css px, and its scale on the frame

/* a stacked headline: Orbitron lines that slam in, a Space Grotesk line under them */
function VHead({ t, at, out, lines, sub, top, size = 104, color = A.magenta }) {
  return (
    <div style={{ position: "absolute", left: 0, right: 0, top, display: "flex", flexDirection: "column", alignItems: "center", zIndex: 30, textAlign: "center" }}>
      {lines.map((l, i) => (
        <Slam key={l} t={t} at={at + i * 0.25} out={out} style={{ fontFamily: ORB, fontSize: size, fontWeight: 900, letterSpacing: "0.04em", lineHeight: 1.08, color: A.ink,
          textShadow: color === A.cyan ? glowC : glowM }}>{l}</Slam>
      ))}
      {sub && <Line t={t} inAt={at + 0.25 * lines.length + 0.15} outAt={out} style={{ fontFamily: SG, fontSize: 42, fontWeight: 500, color: A.sub, marginTop: 14, padding: "0 60px",
        textShadow: "0 2px 14px rgba(7,3,26,.95)" }}>{sub}</Line>}
    </div>
  );
}

/* ── the Dashboard's phone layout, assembled like pages/CalmDashboard.jsx with isMobile ── */
function PhoneDash({ nowMin, scroll }) {
  const m = Math.floor(nowMin);
  const data = fleetAt(m);
  const rows = data.strategies;
  const cards = buildCards(rows);
  const idle = data.idle.filter((r) => r.mode !== "OFF").sort((a, b) => nameOf(a.id).localeCompare(nameOf(b.id)));
  const nOpen = new Set(rows.filter((r) => r.state === "OPEN").map((r) => r.id)).size;
  const closesIn = Math.max(0, 930 - m);
  const dateLine = (links) => (
    <div style={{ display: "flex", alignItems: "center", gap: SP.sm, flexWrap: "wrap", ...T.meta, fontSize: 13, color: C.muted }}>
      <span>Mon 9 Nov · {hhmm(m)} · closes in {Math.floor(closesIn / 60)}h {closesIn % 60}m</span>
      {links}
    </div>
  );
  return (
    <div style={{ transform: `translateY(${-scroll}px)` }}>
      <div data-calm-root style={{ padding: `${SP.lg}px ${SP.lg}px ${SP.xxl}px`, color: C.ink, fontFamily: "var(--c-font-ui)", ...T.body, display: "flex", flexDirection: "column", gap: SP.xl }}>
        <CalmHero totals={data.totals} paths={data.paths} nowMin={m} armed={idle.length} carried={rows.filter((r) => r.carried && r.state === "OPEN").length}
          compact dateLine={dateLine} market={marketAt(m)} />
        <section aria-label="Strategies today" data-testid="calm-strategies">
          <div style={{ display: "flex", alignItems: "baseline", gap: SP.md, flexWrap: "wrap", marginBottom: SP.lg }}>
            <span style={{ ...T.title, fontSize: 17, fontFamily: ORB, textTransform: "uppercase", letterSpacing: "0.06em" }}>Strategies</span>
            <span style={{ ...T.body, fontSize: 13, color: C.muted }}>{`${data.active} of ${data.active + idle.length} traded today${nOpen ? ` · ${nOpen} in a trade` : ""}`}</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: SP.md - 2 }}>
            {cards.map((c) => (
              <StrategyCard key={c.id} id={c.id} name={nameOf(c.id)} accent={META[c.id]?.accent} row={c.row} twin={c.twin} books={c.books}
                meter={undefined} expanded={false} onToggle={() => {}} selected={false} onFocus={() => {}} compact />
            ))}
            {idle.map((r) => (
              <IdleCard key={r.id} id={r.id} name={nameOf(r.id)} accent={META[r.id]?.accent} mode={r.mode} meter={undefined}
                expanded={false} onToggle={() => {}} selected={false} onFocus={() => {}} compact />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function Phone({ children }) {
  const w = PW + 24, h = PH + 24;
  return (
    <div style={{ width: w * PS, height: h * PS, position: "relative" }}>
      <div style={{ position: "absolute", left: 0, top: 0, width: w, height: h, transform: `scale(${PS})`, transformOrigin: "0 0", borderRadius: 46, background: "#07031a",
        boxShadow: "0 0 0 2px #5a45a0, 0 30px 90px rgba(0,0,0,.7), 0 0 90px rgba(255,60,172,.35)" }}>
        <div style={{ position: "absolute", left: 12, top: 12, width: PW, height: PH, borderRadius: 36, overflow: "hidden", ...ARCADE_CANVAS }}>
          <div style={{ height: 38 }} />
          {children}
          <div style={{ position: "absolute", top: 10, left: "50%", width: 110, height: 28, marginLeft: -55, borderRadius: 16, background: "#000" }} />
        </div>
      </div>
    </div>
  );
}

/* ── scene 0: hook ── */
function Hook({ t }) {
  const f = outQuint(seg(t, 0.1, 1.75));
  const m = OPEN_MIN + (940 - OPEN_MIN) * f;
  const v = fleetAt(Math.floor(m)).totals.LIVE.gross;
  const pop = 1 + 0.05 * Math.max(0, 1 - Math.abs(t - 1.75) / 0.12);
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", paddingBottom: 260 }}>
      <Line t={t} inAt={0.05} style={{ fontFamily: SG, fontSize: 50, fontWeight: 600, color: A.sub }}>Your NIFTY options desk.</Line>
      <Slam t={t} at={0.5} style={{ fontFamily: ORB, fontSize: 112, fontWeight: 900, color: A.ink, letterSpacing: "0.05em", textShadow: glowM, marginTop: 18, lineHeight: 1.05 }}>ON</Slam>
      <Slam t={t} at={0.75} style={{ fontFamily: ORB, fontSize: 112, fontWeight: 900, color: A.ink, letterSpacing: "0.05em", textShadow: glowM, marginBottom: 60, lineHeight: 1.05 }}>AUTOPILOT.</Slam>
      <div style={{ display: "flex", alignItems: "center", gap: 18, fontFamily: SG, opacity: outCubic(seg(t, 0.0, 0.25)) }}>
        <span style={{ fontSize: 26, fontWeight: 700, letterSpacing: "0.1em", padding: "3px 14px", borderRadius: 4, border: `2px solid ${A.cyan}`, color: A.cyan, boxShadow: "0 0 14px rgba(60,231,255,.45)" }}>LIVE</span>
        <span style={{ fontFamily: MONO, fontSize: 34, color: A.sub }}>MTM today · {hhmm(m)}</span>
      </div>
      <div style={{ fontFamily: ORB, fontVariantNumeric: "tabular-nums", fontSize: 150, fontWeight: 800, color: v >= 0 ? A.green : A.red, marginTop: 8,
        textShadow: `0 0 18px ${v >= 0 ? A.green : A.red}, 0 0 60px rgba(57,255,140,0.4)`, transform: `scale(${pop})`, opacity: outCubic(seg(t, 0.0, 0.2)) }}>
        {inr(v)}
      </div>
    </div>
  );
}

/* ── scenes 1–2: the phone, its alerts, then the execution facts over it ── */
function Alerts({ t }) {
  const shown = ALERTS.filter((a) => t >= a.at).slice(-3).reverse();
  const out = seg(t, S.exec - 0.25, S.exec);
  if (!shown.length || out >= 1) return null;
  return (
    <div style={{ position: "absolute", left: 70, right: 70, top: 1290, display: "flex", flexDirection: "column", gap: 16, zIndex: 25, opacity: 1 - out }}>
      {shown.map((a, i) => {
        const g = outBack(seg(t, a.at, a.at + 0.22));
        return (
          <div key={a.at} style={{ transform: i === 0 ? `translateY(${(1 - g) * 80}px) scale(${lerp(1.1, 1, g)})` : undefined, opacity: i === 0 ? clamp(g * 2) : 1,
            filter: i ? `brightness(${1 - i * 0.16})` : undefined, background: "#160c3a", border: `2px solid ${a.col}`, borderRadius: 14, padding: "20px 26px",
            boxShadow: `0 0 28px ${a.col}55, 0 18px 50px rgba(0,0,0,.6)`, fontFamily: SG }}>
            <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
              <span style={{ fontFamily: ORB, fontSize: 24, fontWeight: 800, letterSpacing: "0.08em", color: a.col }}>{a.kind}</span>
              <span style={{ fontSize: 22, color: A.muted }}>{a.book}</span>
              <span style={{ marginLeft: "auto", fontFamily: MONO, fontSize: 24, color: A.sub }}>{a.time}</span>
            </div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 14, marginTop: 6 }}>
              <i style={{ width: 14, height: 14, borderRadius: 7, background: META[a.id].accent, display: "inline-block" }} />
              <b style={{ fontSize: 34, color: A.ink }}>{nameOf(a.id)}</b>
              <span style={{ fontSize: 30, fontWeight: 600, color: a.kind === "ENTRY" ? A.sub : a.col, fontVariantNumeric: "tabular-nums" }}>{a.text}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ExecTiles({ t }) {
  const out = seg(t, S.bt - 0.3, S.bt - 0.05);
  if (t < S.exec + 0.6 || out >= 1) return null;
  return (
    <div style={{ position: "absolute", left: 80, right: 80, top: 640, display: "flex", flexDirection: "column", gap: 30, zIndex: 25, opacity: 1 - out }}>
      {EXEC.map((e, i) => {
        const at = S.exec + 1.0 + i * 0.5;
        if (t < at) return <div key={e.k} style={{ height: 250 }} />;
        const g = seg(t, at, at + 0.18);
        return (
          <div key={e.k} style={{ height: 250, boxSizing: "border-box", transform: `scale(${lerp(1.35, 1, outBack(g))})`, opacity: clamp(g * 3), display: "flex", alignItems: "center", gap: 34,
            background: "rgba(22,12,58,0.97)", border: `3px solid ${e.col}`, borderRadius: 18, padding: "0 40px", boxShadow: `0 0 50px ${e.col}44, 0 24px 60px rgba(0,0,0,.6)` }}>
            <div style={{ fontSize: 86, lineHeight: 1, flexShrink: 0 }}>{e.icon}</div>
            <div>
              <div style={{ fontFamily: ORB, fontSize: 40, fontWeight: 800, color: A.ink, letterSpacing: "0.02em", lineHeight: 1.15 }}>{e.k}</div>
              <div style={{ fontFamily: SG, fontSize: 32, color: A.sub, marginTop: 12, lineHeight: 1.3 }}>{e.v}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function PhoneScenes({ t }) {
  const nowMin = OPEN_MIN + (850 - OPEN_MIN) * inOutCubic(seg(t, 2.25, 5.6)) + Math.max(0, t - 5.6) * 1.6;
  const scroll = 330 * inOutCubic(seg(t, 3.6, 5.4));   // down to the strategies while the alerts land
  const enter = outCubic(seg(t, 2.0, 2.55));
  const leave = inOutCubic(seg(t, S.bt - 0.4, S.bt + 0.1));
  const dim = 0.66 * outCubic(seg(t, S.exec + 0.4, S.exec + 0.8));
  const lift = 200 * inOutCubic(seg(t, S.exec, S.exec + 0.6));   // the phone rises to sit behind the tiles
  const w = (PW + 24) * PS;
  const top = 470 - lift + (1 - enter) * 260 + leave * 200;
  return (
    <>
      <div style={{ position: "absolute", left: (1080 - w) / 2, top, opacity: Math.min(enter, 1 - leave), transform: `rotate(${lerp(3, 0, enter)}deg)`,
        filter: dim > 0 ? `blur(${dim * 5}px)` : undefined }}>
        <Phone><PhoneDash nowMin={nowMin} scroll={scroll} /></Phone>
        {dim > 0 && <div style={{ position: "absolute", inset: 0, borderRadius: 90, background: `rgba(7,3,26,${dim})` }} />}
      </div>
      <VHead t={t} at={S.auto} out={S.exec - 0.25} lines={["TRADES", "ITSELF."]} sub="Signal → order → exit. Hands-free." top={110} />
      <Alerts t={t} />
      <VHead t={t} at={S.exec} out={S.bt - 0.3} lines={["SLICK", "EXECUTION."]} sub={null} top={240} size={96} color={A.cyan} />
      <ExecTiles t={t} />
    </>
  );
}

/* ── scene 3: backtests ── */
function Backtest({ t }) {
  if (t < S.bt - 0.05 || t >= S.comp) return null;
  const out = seg(t, S.comp - 0.25, S.comp);
  const y = 2020 + Math.min(6, Math.floor(Math.max(0, t - (S.bt + 0.75)) / 0.125));
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", paddingTop: 200, opacity: 1 - out, textAlign: "center" }}>
      <Slam t={t} at={S.bt} from={2.4} style={{ fontFamily: ORB, fontSize: 330, fontWeight: 900, lineHeight: 0.95, color: A.cyan, textShadow: "0 0 40px rgba(60,231,255,.7)" }}>6+</Slam>
      <Slam t={t} at={S.bt + 0.25} style={{ fontFamily: ORB, fontSize: 130, fontWeight: 900, color: A.ink, letterSpacing: "0.05em", textShadow: glowM, lineHeight: 1.05 }}>YEARS</Slam>
      <Slam t={t} at={S.bt + 0.5} style={{ fontFamily: ORB, fontSize: 74, fontWeight: 800, color: A.magenta, letterSpacing: "0.08em", lineHeight: 1.2, marginTop: 6 }}>BACKTESTED</Slam>
      <Line t={t} inAt={S.bt + 0.75} style={{ fontFamily: SG, fontSize: 42, color: A.sub, marginTop: 26, lineHeight: 1.35 }}>
        on 1-minute NIFTY options data<br /><span style={{ fontFamily: MONO, fontSize: 52, color: A.ink }}>2020 → <span style={{ color: A.cyan }}>{y}</span></span>
      </Line>
      <Line t={t} inAt={S.bt + 1.6} style={{ fontFamily: ORB, fontSize: 44, fontWeight: 800, color: A.ink, letterSpacing: "0.04em", marginTop: 70, lineHeight: 1.2 }}>EVERY RUPEE OF FEES,<br />COUNTED</Line>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 440px)", gap: 20, marginTop: 34 }}>
        {FEES.map(([k, v], i) => {
          const at = S.bt + 2.0 + i * 0.25;
          if (t < at) return <div key={k} style={{ height: 112 }} />;
          const g = seg(t, at, at + 0.16);
          return (
            <div key={k} style={{ height: 112, boxSizing: "border-box", transform: `scale(${lerp(1.3, 1, outBack(g))})`, opacity: clamp(g * 3), background: "rgba(22,12,58,0.92)",
              border: `2px solid ${i % 2 ? A.cyan : A.magenta}`, borderRadius: 12, padding: "16px 24px", textAlign: "left", boxShadow: `0 0 20px ${i % 2 ? "rgba(60,231,255,.25)" : "rgba(255,60,172,.25)"}` }}>
              <div style={{ fontFamily: SG, fontSize: 30, fontWeight: 700, color: A.ink }}>{k}</div>
              <div style={{ fontFamily: MONO, fontSize: 26, color: A.sub, marginTop: 4 }}>{v}</div>
            </div>
          );
        })}
      </div>
      <Line t={t} inAt={S.bt + 3.5} style={{ fontFamily: SG, fontSize: 32, color: A.muted, marginTop: 26, padding: "0 80px" }}>Zerodha's published rates, on every backtest trade.</Line>
    </div>
  );
}

/* ── scene 4: compounding — lot sizing (illustrative), never an equity curve ── */
function Compound({ t }) {
  if (t < S.comp - 0.05 || t >= S.tg) return null;
  const out = seg(t, S.tg - 0.25, S.tg);
  const on = outCubic(seg(t, S.comp + 0.55, S.comp + 0.75));
  const U = 26;
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", paddingTop: 200, opacity: 1 - out, textAlign: "center" }}>
      <Slam t={t} at={S.comp} style={{ fontFamily: ORB, fontSize: 100, fontWeight: 900, color: A.ink, letterSpacing: "0.03em", textShadow: glowM }}>COMPOUNDING</Slam>
      <Slam t={t} at={S.comp + 0.5} style={{ marginTop: 34, width: 200, height: 100, borderRadius: 50, background: on > 0.5 ? A.green : "#2a1a5e", position: "relative", border: `4px solid ${A.ink}`, boxSizing: "border-box",
        boxShadow: on > 0.5 ? "0 0 34px rgba(57,255,140,.75)" : "none" }}>
        <i style={{ position: "absolute", top: 9, left: lerp(10, 106, on), width: 74, height: 74, borderRadius: 37, background: A.ink }} />
      </Slam>
      <Line t={t} inAt={S.comp + 0.4} style={{ fontFamily: SG, fontSize: 42, color: A.sub, marginTop: 30, lineHeight: 1.35 }}>Built into the backtester:<br />lots grow as equity does.</Line>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", width: 920, height: 600, marginTop: 60, position: "relative" }}>
        <div style={{ position: "absolute", left: 0, right: 0, bottom: 20 * U, borderTop: `4px dashed ${A.yellow}`, opacity: seg(t, S.comp + 2.0, S.comp + 2.15) }}>
          <span style={{ position: "absolute", left: 0, top: -50, fontFamily: ORB, fontSize: 30, fontWeight: 800, color: A.yellow, letterSpacing: "0.08em" }}>MAX LOTS CAP</span>
        </div>
        {LADDER.map((v, i) => {
          const at = S.comp + 1.0 + i * 0.125;
          const g = outBack(seg(t, at, at + 0.22));
          const dd = i === 3;
          const col = dd ? A.red : i % 2 ? A.cyan : A.magenta;
          return (
            <div key={i} style={{ width: 96, display: "flex", flexDirection: "column", alignItems: "center" }}>
              <span style={{ fontFamily: ORB, fontSize: 36, fontWeight: 800, color: dd ? A.red : A.ink, opacity: clamp(g), marginBottom: 8, position: "relative", zIndex: 1, padding: "0 6px", background: A.bg, borderRadius: 4 }}>{v}</span>
              <div style={{ width: 96, height: Math.max(0, v * U * g), borderRadius: "8px 8px 2px 2px", background: `linear-gradient(180deg, ${col}, ${col}33)`, boxShadow: `0 0 22px ${col}88` }} />
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", width: 920, fontFamily: SG, fontSize: 28, color: A.muted, marginTop: 14 }}>
        <span>lots per trade · illustrative</span><span style={{ color: A.red }}>▲ drawdown guard</span>
      </div>
      <Line t={t} inAt={S.comp + 2.5} style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: 16, marginTop: 44, padding: "0 70px", fontFamily: SG, fontSize: 32, fontWeight: 600 }}>
        {["Calendar-step or equity-based", "Max-lots cap", "Drawdown guard", "Tax set aside each FY"].map((x, i) => (
          <span key={x} style={{ padding: "10px 22px", borderRadius: 6, border: `2px solid ${i % 2 ? A.cyan : A.magenta}`, color: A.ink, background: "rgba(22,12,58,.9)" }}>{x}</span>
        ))}
      </Line>
    </div>
  );
}

/* ── scene 5: Telegram ── */
function TelegramScene({ t }) {
  if (t < S.tg - 0.05 || t >= S.outro + 0.1) return null;
  const a = outCubic(seg(t, S.tg, S.tg + 0.45));
  const out = seg(t, S.outro - 0.2, S.outro + 0.05);
  const card = outCubic(seg(t, S.tg + 2.0, S.tg + 2.4));
  const lift = 380 * inOutCubic(seg(t, S.tg + 1.75, S.tg + 2.25));
  return (
    <div style={{ position: "absolute", inset: 0, opacity: 1 - out }}>
      <VHead t={t} at={S.tg} out={null} lines={["EVERY TRADE,", "IN TELEGRAM."]} top={120} size={84}
        sub={t < S.tg + 2.0 ? "Entries, targets, stops, rejections — as they happen." : null} />
      <Line t={t} inAt={S.tg + 2.0} style={{ position: "absolute", left: 0, right: 0, top: 380, textAlign: "center", fontFamily: SG, fontSize: 42, color: A.sub, zIndex: 31 }}>
        <span style={{ fontFamily: ORB, fontWeight: 800, color: A.cyan }}>15:40</span> · the day's card, Live and Paper apart.
      </Line>
      <div style={{ position: "absolute", left: 60, right: 60, top: 480, height: 1280, borderRadius: 28, overflow: "hidden", background: "#0e1621",
        boxShadow: "0 40px 120px rgba(0,0,0,.6), 0 0 0 2px #5a45a0, 0 0 80px rgba(255,60,172,.3)", transform: `translateY(${(1 - a) * 300}px)`, opacity: a }}>
        <div style={{ position: "absolute", left: 0, right: 0, top: 0, height: 100, background: "#17212b", display: "flex", alignItems: "center", gap: 20, padding: "0 30px", fontFamily: SG, zIndex: 2 }}>
          <div style={{ width: 60, height: 60, borderRadius: 30, background: "linear-gradient(135deg,#ff3cac,#7b2ff7)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 32 }}>⚡</div>
          <div>
            <div style={{ color: "#f5f5f5", fontSize: 28, fontWeight: 600 }}>Scalp Terminal</div>
            <div style={{ color: "#6c7883", fontSize: 21 }}>bot</div>
          </div>
        </div>
        <div style={{ position: "absolute", left: 0, right: 0, top: 124 - lift, padding: "0 30px", display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 18 }}>
          {TG_MSGS.map((m) => {
            const g = outCubic(seg(t, m.at, m.at + 0.25));
            if (g <= 0) return null;
            return (
              <div key={m.at} style={{ background: "#182533", borderRadius: 20, padding: "18px 24px 10px", width: 640, fontFamily: SG, color: "#f5f5f5", fontSize: 30, lineHeight: 1.45,
                opacity: g, transform: `translateY(${(1 - g) * 40}px)` }}>
                <div><b>{m.head}</b> <span style={{ fontSize: 26 }}>{m.badge}</span></div>
                {m.lines.map(([k, v]) => <div key={k} style={{ color: "#c8d1da" }}>{k}: <b style={{ color: "#f5f5f5" }}>{v}</b></div>)}
                <div style={{ textAlign: "right", color: "#6c7883", fontSize: 20 }}>{m.time}</div>
              </div>
            );
          })}
          {card > 0 && (
            <div style={{ background: "#182533", borderRadius: 20, padding: 10, opacity: card, transform: `translateY(${(1 - card) * 50}px) scale(${0.96 + 0.04 * card})`, transformOrigin: "0 100%" }}>
              <img src="eod_card.png" alt="" style={{ width: 880, display: "block", borderRadius: 13 }} />
              <div style={{ textAlign: "right", color: "#6c7883", fontFamily: SG, fontSize: 20, padding: "6px 6px 0" }}>15:40</div>
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
  const flick = 0.9 + 0.1 * Math.sin(t * 37) * Math.sin(t * 11);
  const o = S.outro;
  const neon = { fontFamily: ORB, fontSize: 150, fontWeight: 900, color: A.ink, letterSpacing: "0.05em", lineHeight: 1.05,
    textShadow: `0 0 ${18 * flick}px rgba(255,60,172,.95), 0 0 70px rgba(255,60,172,.55)` };
  return (
    <div style={{ position: "absolute", inset: 0, opacity: a }}>
      <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", paddingBottom: 380, textAlign: "center" }}>
        <Slam t={t} at={o} style={{ fontSize: 150, lineHeight: 1 }}>⚡</Slam>
        <Slam t={t} at={o + 0.25} style={neon}>SCALP</Slam>
        <Slam t={t} at={o + 0.5} style={{ ...neon, fontSize: 112 }}>TERMINAL</Slam>
        <Line t={t} inAt={o + 0.7} style={{ fontFamily: SG, fontSize: 40, fontWeight: 500, color: A.sub, marginTop: 30 }}>Algorithmic options desk<br />NIFTY · Zerodha</Line>
        <Line t={t} inAt={o + 0.95} style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: 14, marginTop: 40, padding: "0 70px", fontFamily: SG, fontSize: 30, fontWeight: 600 }}>
          {["Auto trades", "Static-IP relay", "6+ yrs backtests", "Compounding", "Telegram alerts", "macOS · Windows"].map((x, i) => (
            <span key={x} style={{ padding: "10px 20px", borderRadius: 6, border: `2px solid ${i % 2 ? A.cyan : A.magenta}`, color: A.ink, background: "rgba(22,12,58,.85)",
              boxShadow: `0 0 14px ${i % 2 ? "rgba(60,231,255,.3)" : "rgba(255,60,172,.3)"}` }}>{x}</span>
          ))}
        </Line>
        <Line t={t} inAt={o + 1.15} style={{ fontFamily: SG, fontSize: 36, fontWeight: 700, color: A.magenta, marginTop: 34 }}>#CompoundingInTrading</Line>
        <Line t={t} inAt={o + 1.3} style={{ fontFamily: SG, fontSize: 24, color: A.muted, marginTop: 22, padding: "0 90px" }}>
          Backtests use historical data and don't guarantee future results.
        </Line>
      </div>
    </div>
  );
}

function Video({ t }) {
  const low = inOutCubic(seg(t, 1.8, 2.5)) * (1 - inOutCubic(seg(t, S.bt - 0.4, S.bt + 0.2)));
  return (
    <div style={{ position: "relative", width: 1080, height: 1920, overflow: "hidden", background: A.bg }}>
      <ArcadeStage t={t} horizon={lerp(1400, 1700, low)} glow={1 - 0.4 * low} />
      {t < 2.05 && <Hook t={t} />}
      {t >= 1.95 && t < S.bt + 0.15 && <PhoneScenes t={t} />}
      <Backtest t={t} />
      <Compound t={t} />
      <TelegramScene t={t} />
      <Outro t={t} />
      <DemoTag color={A.sub} bg="rgba(11,6,33,0.85)" border="#3b2a6e" dot={A.magenta} font={SG} bottom={40} />
    </div>
  );
}

export const VIDEO_META = { width: 1080, height: 1920, duration: S.end, theme: "arcade", poster: 4.6, warmup: [0.5, 2.5, 4.0, 7.5] };
mount(Video, VIDEO_META);
