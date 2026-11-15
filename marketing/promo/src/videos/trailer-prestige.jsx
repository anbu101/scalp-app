// Trailer A — "Prestige": black & gold, Cinzel title cards, letterbox, grain, dips to black.
import { mount } from "../lib/runtime";
import "@fontsource/cinzel/500.css";
import "@fontsource/cinzel/700.css";
import "@fontsource/cinzel/900.css";
import { PhoneDash, Phone, Grain, DemoTag, FEES, seg, outCubic, outQuint, inOutCubic, lerp, win, clamp } from "../lib/common";
import { OPEN_MIN } from "../lib/demoData";

const GOLD = "linear-gradient(180deg, #fff1c1 0%, #f2cf6b 38%, #c9962a 70%, #8a6215 100%)";
const CIN = "'Cinzel', serif";
const goldText = { background: GOLD, WebkitBackgroundClip: "text", backgroundClip: "text", color: "transparent" };

function T({ t, at, out, children, style, dy = 18, blur = true }) {
  const a = outCubic(seg(t, at, at + 0.45));
  const b = out == null ? 0 : seg(t, out, out + 0.25);
  const o = a * (1 - b);
  if (o <= 0.002) return null;
  return <div style={{ opacity: o, transform: `translateY(${(1 - a) * dy}px)`, filter: blur && a < 1 ? `blur(${(1 - a) * 6}px)` : undefined, ...style }}>{children}</div>;
}
// dip to black around cut points
function dip(t) {
  const cuts = [2.0, 4.0, 8.0, 12.0, 16.0];
  let d = 0;
  cuts.forEach((c) => { d = Math.max(d, 1 - Math.abs(t - c) / 0.22); });
  if (t >= 19.72 && t < 20.0) d = 1;          // trailer silence before the title
  return clamp(d);
}
function Flare({ x, o }) {
  return (
    <div style={{ position: "absolute", left: 0, right: 0, top: "50%", height: 0, opacity: o }}>
      <div style={{ position: "absolute", left: x - 900, width: 1800, top: -2, height: 4, background: "linear-gradient(90deg, transparent, rgba(255,214,120,.9) 45%, #fff 50%, rgba(255,214,120,.9) 55%, transparent)", filter: "blur(1px)" }} />
      <div style={{ position: "absolute", left: x - 260, width: 520, top: -40, height: 80, background: "radial-gradient(closest-side, rgba(255,220,140,.55), transparent)", filter: "blur(6px)" }} />
    </div>
  );
}

function S1({ t }) {
  const s = 1 + 0.05 * seg(t, 0, 2);
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", transform: `scale(${s})` }}>
      <T t={t} at={0.1} style={{ fontFamily: CIN, fontWeight: 500, fontSize: 64, letterSpacing: "0.22em", color: "#e9e2d0" }}>EVERY MORNING</T>
      <T t={t} at={0.55} style={{ fontFamily: "'JetBrains Mono Variable', monospace", fontWeight: 700, fontSize: 132, marginTop: 26, ...goldText }}>09:15</T>
    </div>
  );
}
function S2({ t }) {
  const s = 1.06 - 0.06 * seg(t, 2, 4);
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", transform: `scale(${s})` }}>
      <T t={t} at={2.08} style={{ fontFamily: CIN, fontWeight: 900, fontSize: 104, letterSpacing: "0.08em", textAlign: "center", lineHeight: 1.08, ...goldText }}>NINE<br />STRATEGIES</T>
      <T t={t} at={2.6} style={{ fontFamily: CIN, fontWeight: 500, fontSize: 64, letterSpacing: "0.3em", color: "#e9e2d0", marginTop: 34 }}>WAKE UP.</T>
    </div>
  );
}
function S3({ t }) {
  const nowMin = OPEN_MIN + (850 - OPEN_MIN) * inOutCubic(seg(t, 4.6, 7.8));
  const scroll = 560 * inOutCubic(seg(t, 5.9, 6.8));
  const rise = outQuint(seg(t, 4.0, 4.9));
  const sc = 1.72 + 0.08 * seg(t, 4, 8);
  const W = 424 * sc;
  return (
    <div style={{ position: "absolute", inset: 0 }}>
      <div style={{ position: "absolute", left: 0, right: 0, top: 232, textAlign: "center" }}>
        <T t={t} at={4.35} style={{ fontFamily: CIN, fontWeight: 700, fontSize: 60, letterSpacing: "0.1em", color: "#f3ead3" }}>LIVE AND PAPER.</T>
        <T t={t} at={4.75} style={{ fontFamily: CIN, fontWeight: 700, fontSize: 60, letterSpacing: "0.1em", marginTop: 8, ...goldText }}>NEVER SUMMED.</T>
      </div>
      <div style={{ position: "absolute", left: 540 - W / 2, top: lerp(1900, 470, rise), opacity: rise }}>
        <Phone viewH={800} scale={sc} glow="rgba(242,207,107,0.10)">
          <PhoneDash nowMin={nowMin} scroll={scroll} />
        </Phone>
      </div>
      <DemoTag bottom={196} />
    </div>
  );
}
const YEARS = [2020, 2021, 2022, 2023, 2024, 2025, 2026];
function S4({ t }) {
  const s = 1 + 0.035 * seg(t, 8, 12);
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", transform: `scale(${s})` }}>
      <T t={t} at={8.1} style={{ fontFamily: CIN, fontWeight: 500, fontSize: 52, letterSpacing: "0.26em", color: "#e9e2d0" }}>BACKTESTED ACROSS</T>
      <T t={t} at={8.45} dy={30} style={{ fontFamily: CIN, fontWeight: 900, fontSize: 300, lineHeight: 1, marginTop: 24, ...goldText }}>6+</T>
      <T t={t} at={8.7} dy={30} style={{ fontFamily: CIN, fontWeight: 900, fontSize: 168, lineHeight: 1, letterSpacing: "0.08em", ...goldText }}>YEARS</T>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 0, marginTop: 64, width: 880, justifyContent: "space-between", position: "relative" }}>
        <div style={{ position: "absolute", left: 20, right: 20, top: 9, height: 2, background: "#3a3326" }} />
        <div style={{ position: "absolute", left: 20, top: 9, height: 2, width: `${840 * outCubic(seg(t, 8.9, 10.7))}px`, background: "linear-gradient(90deg,#8a6215,#f2cf6b)", boxShadow: "0 0 14px rgba(242,207,107,.6)" }} />
        {YEARS.map((y, i) => {
          const on = seg(t, 8.9 + i * 0.3, 9.1 + i * 0.3);
          return (
            <div key={y} style={{ display: "flex", flexDirection: "column", alignItems: "center", width: 40, position: "relative" }}>
              <i style={{ width: 20, height: 20, borderRadius: 10, background: on > 0 ? "#f2cf6b" : "#2a2519", boxShadow: on > 0 ? `0 0 ${18 * on}px rgba(242,207,107,.8)` : "none", display: "block" }} />
              <span style={{ fontFamily: "'JetBrains Mono Variable', monospace", fontSize: 26, marginTop: 14, color: on > 0 ? "#f3ead3" : "#5c5446" }}>{y}</span>
            </div>
          );
        })}
      </div>
      <T t={t} at={9.2} style={{ fontFamily: "'Inter Variable', Inter, sans-serif", fontWeight: 500, fontSize: 38, color: "#cfc6b0", marginTop: 56, textAlign: "center", lineHeight: 1.35 }}>
        1-minute NIFTY options data<br /><span style={{ color: "#8f8673" }}>Aug 2020 → today</span>
      </T>
    </div>
  );
}
function S5({ t }) {
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
      <T t={t} at={12.08} style={{ fontFamily: CIN, fontWeight: 900, fontSize: 112, letterSpacing: "0.06em", ...goldText }}>EVERY FEE.</T>
      <T t={t} at={12.45} style={{ fontFamily: CIN, fontWeight: 500, fontSize: 68, letterSpacing: "0.3em", color: "#f3ead3", marginTop: 6 }}>COUNTED.</T>
      <div style={{ marginTop: 60, width: 860, border: "1px solid #3a3326", borderRadius: 18, padding: "30px 40px", background: "rgba(20,17,12,0.7)",
        opacity: outCubic(seg(t, 12.7, 13.0)) }}>
        {FEES.map(([k, v], i) => {
          const a = outCubic(seg(t, 12.85 + i * 0.36, 13.15 + i * 0.36));
          return (
            <div key={k} style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", padding: "15px 0", borderBottom: i < FEES.length - 1 ? "1px dashed #3a3326" : "none",
              opacity: a, transform: `translateX(${(1 - a) * -24}px)` }}>
              <span style={{ fontFamily: "'Inter Variable', Inter, sans-serif", fontSize: 38, fontWeight: 600, color: "#f3ead3" }}>{k}</span>
              <span style={{ fontFamily: "'JetBrains Mono Variable', monospace", fontSize: 34, color: "#f2cf6b" }}>{v}</span>
            </div>
          );
        })}
      </div>
      <T t={t} at={15.0} style={{ fontFamily: "'Inter Variable', Inter, sans-serif", fontWeight: 500, fontSize: 32, color: "#a79e88", marginTop: 34, textAlign: "center" }}>
        Zerodha's published rates, on every backtest trade
      </T>
    </div>
  );
}
const LADDER = [10, 12, 14, 11, 15, 18, 20, 20];
function S6({ t }) {
  const s = 1 + 0.03 * seg(t, 16, 19.7);
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", transform: `scale(${s})` }}>
      <T t={t} at={16.08} style={{ fontFamily: CIN, fontWeight: 500, fontSize: 52, letterSpacing: "0.3em", color: "#e9e2d0" }}>AND THEN…</T>
      <T t={t} at={16.45} dy={26} style={{ fontFamily: CIN, fontWeight: 900, fontSize: 118, letterSpacing: "0.03em", marginTop: 14, ...goldText }}>COMPOUNDING</T>
      <div style={{ position: "relative", width: 860, height: 430, marginTop: 50, display: "flex", alignItems: "flex-end", justifyContent: "space-between", padding: "0 10px", boxSizing: "border-box" }}>
        <div style={{ position: "absolute", left: 0, right: 0, bottom: 20 * 18 + 40, borderTop: "2px dashed #8a6215", opacity: seg(t, 17.6, 17.9) }}>
          <span style={{ position: "absolute", right: 0, top: -40, fontFamily: "'Inter Variable', Inter, sans-serif", fontSize: 24, color: "#c9962a" }}>max lots</span>
        </div>
        {LADDER.map((v, i) => {
          const g = outCubic(seg(t, 16.9 + i * 0.13, 17.35 + i * 0.13));
          const dd = i === 3;
          return (
            <div key={i} style={{ width: 82, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "flex-end" }}>
              <span style={{ fontFamily: "'JetBrains Mono Variable', monospace", fontSize: 26, color: dd ? "#e58a6b" : "#f3ead3", marginBottom: 8, opacity: g }}>{v}</span>
              <div style={{ width: 82, height: v * 18 * g, borderRadius: "8px 8px 2px 2px",
                background: dd ? "linear-gradient(180deg,#e58a6b,#7a3b26)" : "linear-gradient(180deg,#f2cf6b,#8a6215)", boxShadow: "0 0 24px rgba(242,207,107,.18)" }} />
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", width: 860, fontFamily: "'Inter Variable', Inter, sans-serif", fontSize: 22, color: "#8f8673", marginTop: 10 }}>
        <span>lots, illustrative</span><span style={{ color: "#e58a6b" }}>▲ drawdown guard</span>
      </div>
      <T t={t} at={17.4} style={{ fontFamily: "'Inter Variable', Inter, sans-serif", fontWeight: 600, fontSize: 40, color: "#f3ead3", marginTop: 36, textAlign: "center" }}>Lots grow with equity.</T>
      <T t={t} at={17.85} style={{ fontFamily: "'Inter Variable', Inter, sans-serif", fontWeight: 500, fontSize: 32, color: "#cfc6b0", marginTop: 12, textAlign: "center" }}>Capped · drawdown-guarded · tax set aside each FY</T>
      <T t={t} at={18.35} style={{ marginTop: 34, fontFamily: "'Inter Variable', Inter, sans-serif", fontWeight: 700, fontSize: 40, padding: "12px 30px", borderRadius: 40, border: "1.5px solid #c9962a", color: "#f2cf6b", background: "rgba(242,207,107,0.08)" }}>#CompoundingInTrading</T>
    </div>
  );
}
function S7({ t }) {
  const fx = lerp(-300, 1380, inOutCubic(seg(t, 20.0, 21.6)));
  const s = 1.04 - 0.04 * outCubic(seg(t, 20, 24));
  return (
    <div style={{ position: "absolute", inset: 0 }}>
      <div style={{ position: "absolute", inset: 0, background: "radial-gradient(700px 500px at 50% 46%, rgba(242,207,107,0.10), transparent 70%)", opacity: seg(t, 20, 20.6) }} />
      <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", transform: `scale(${s})` }}>
        <T t={t} at={20.02} dy={0} style={{ fontSize: 120, marginBottom: 10, filter: "drop-shadow(0 0 30px rgba(242,207,107,.45))" }}>⚡</T>
        <T t={t} at={20.1} dy={0} style={{ fontFamily: CIN, fontWeight: 900, fontSize: 132, letterSpacing: "0.06em", lineHeight: 1.0, textAlign: "center", ...goldText }}>SCALP<br />TERMINAL</T>
        <T t={t} at={20.7} style={{ fontFamily: CIN, fontWeight: 500, fontSize: 40, letterSpacing: "0.24em", color: "#e9e2d0", marginTop: 40 }}>ALGORITHMIC OPTIONS DESK</T>
        <T t={t} at={21.1} style={{ display: "flex", gap: 14, marginTop: 38, fontFamily: "'Inter Variable', Inter, sans-serif", fontSize: 28, fontWeight: 600 }}>
          {["NIFTY", "Zerodha", "Live + Paper", "macOS · Windows"].map((x) => (
            <span key={x} style={{ padding: "9px 18px", borderRadius: 10, border: "1px solid #3a3326", color: "#cfc6b0", background: "rgba(20,17,12,0.8)" }}>{x}</span>
          ))}
        </T>
        <T t={t} at={21.4} style={{ marginTop: 40, fontFamily: "'Inter Variable', Inter, sans-serif", fontWeight: 700, fontSize: 40, color: "#f2cf6b" }}>#CompoundingInTrading</T>
      </div>
      <Flare x={fx} o={Math.sin(Math.PI * seg(t, 20.0, 21.6)) * 0.9} />
      <T t={t} at={21.6} style={{ position: "absolute", left: 80, right: 80, bottom: 214, textAlign: "center", fontFamily: "'Inter Variable', Inter, sans-serif", fontSize: 22, color: "#7d7563", lineHeight: 1.45 }}>
        Backtests use historical data and don't guarantee future results.
      </T>
    </div>
  );
}

function Video({ t }) {
  const frame = Math.round(t * 30);
  const d = dip(t);
  return (
    <div style={{ position: "relative", width: 1080, height: 1920, overflow: "hidden", background: "#0a0907" }}>
      <div style={{ position: "absolute", inset: 0, background: "radial-gradient(900px 1300px at 50% 50%, #17130c 0%, #0a0907 60%, #050403 100%)" }} />
      {t < 2.0 && <S1 t={t} />}
      {t >= 2.0 && t < 4.0 && <S2 t={t} />}
      {t >= 4.0 && t < 8.0 && <S3 t={t} />}
      {t >= 8.0 && t < 12.0 && <S4 t={t} />}
      {t >= 12.0 && t < 16.0 && <S5 t={t} />}
      {t >= 16.0 && t < 19.75 && <S6 t={t} />}
      {t >= 20.0 && <S7 t={t} />}
      <div style={{ position: "absolute", inset: 0, background: "#000", opacity: d, zIndex: 80 }} />
      <Grain frame={frame} opacity={0.07} />
      <div style={{ position: "absolute", left: 0, right: 0, top: 0, height: 170, background: "#000", zIndex: 100 }} />
      <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: 170, background: "#000", zIndex: 100 }} />
      <div style={{ position: "absolute", inset: 0, background: "radial-gradient(1200px 1500px at 50% 50%, transparent 55%, rgba(0,0,0,.55))", zIndex: 85, pointerEvents: "none" }} />
    </div>
  );
}

export const VIDEO_META = { width: 1080, height: 1920, duration: 24, theme: "dark", poster: 23.0, warmup: [3.0, 9.0, 13.0, 18.0, 21.0] };
mount(Video, VIDEO_META);
