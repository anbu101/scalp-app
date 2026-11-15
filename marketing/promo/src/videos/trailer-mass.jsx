// Trailer B — "Mass": the app's own crimson-and-gold theme, Bebas Neue slams, shake, gold rays, flashes.
import { mount } from "../lib/runtime";
import "@fontsource/bebas-neue/400.css";
import { PhoneDash, Phone, DemoTag, FEES, seg, outCubic, outBack, inOutCubic, lerp, clamp, hhmm } from "../lib/common";
import { OPEN_MIN } from "../lib/demoData";

const BEB = "'Bebas Neue', sans-serif";
const CAT = "'Catamaran', sans-serif";
const GOLD = "linear-gradient(180deg, #fff6c8 0%, #ffe98a 25%, #f5c518 55%, #b8860b 100%)";
const gold = { background: GOLD, WebkitBackgroundClip: "text", backgroundClip: "text", color: "transparent", filter: "drop-shadow(0 6px 0 #5a0d1c) drop-shadow(0 0 24px rgba(245,197,24,.35))" };
const CREAM = "#fff3dc";

// every slam: [time, strength]
const HITS = [[0.05, 1], [1.0, 1], [2.0, 1.2], [2.5, 0.5], [3.0, 0.5], [6.0, 1.3], [6.5, 0.8], [7.0, 0.6], [9.0, 1], [9.5, 1.1],
  [10.0, 0.35], [10.25, 0.35], [10.5, 0.35], [10.75, 0.35], [11.0, 0.35], [11.25, 0.35], [12.5, 1.2], [14.0, 1.2], [14.5, 0.8], [17.0, 1.5]];
function shake(t) {
  let x = 0, y = 0;
  HITS.forEach(([h, k]) => { if (t >= h) { const e = Math.exp(-(t - h) / 0.11) * 16 * k; x += e * Math.sin((t - h) * 71); y += e * Math.cos((t - h) * 53); } });
  return [x, y];
}
function flash(t) {
  let f = 0;
  HITS.forEach(([h, k]) => { if (t >= h && k >= 1) f = Math.max(f, Math.exp(-(t - h) / 0.07) * 0.55); });
  return f;
}
// slam-in text: big → settle with overshoot
function Slam({ t, at, out, children, style, from = 1.9 }) {
  if (t < at) return null;
  const a = seg(t, at, at + 0.2);
  const s = lerp(from, 1, outBack(a));
  const o = clamp(a * 3) * (out == null ? 1 : 1 - seg(t, out, out + 0.12));
  if (o <= 0.002) return null;
  return <div style={{ transform: `scale(${s})`, opacity: o, ...style }}>{children}</div>;
}

function Rays({ t, power = 1 }) {
  const rot = t * 9;
  return (
    <div style={{ position: "absolute", inset: 0, overflow: "hidden" }}>
      <div style={{ position: "absolute", inset: 0, background: "radial-gradient(1200px 1400px at 50% 30%, #5a0d1c 0%, #2a060d 55%, #12020a 100%)" }} />
      <div style={{ position: "absolute", left: -1000, top: -1000, width: 3080, height: 3080, transformOrigin: "1540px 1540px", transform: `translateY(-260px) rotate(${rot}deg)`,
        background: `repeating-conic-gradient(from 0deg at 50% 50%, rgba(245,197,24,${0.10 * power}) 0 5deg, transparent 5deg 15deg)` }} />
      <div style={{ position: "absolute", inset: 0, background: "radial-gradient(900px 1000px at 50% 34%, transparent 0%, rgba(18,2,10,.25) 50%, rgba(18,2,10,.85) 100%)" }} />
    </div>
  );
}

function Hook({ t }) {
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
      <Slam t={t} at={0.05} from={2.6} style={{ fontFamily: BEB, fontSize: 520, lineHeight: 0.8, ...gold }}>9</Slam>
      <Slam t={t} at={0.05} style={{ fontFamily: BEB, fontSize: 196, lineHeight: 0.95, letterSpacing: "0.03em", ...gold }}>STRATEGIES</Slam>
      <Slam t={t} at={1.0} style={{ fontFamily: BEB, fontSize: 150, lineHeight: 1, color: "#2a060d", background: "#fff3dc", padding: "6px 30px 0", letterSpacing: "0.06em", marginTop: 26, transform: "rotate(-2deg)", boxShadow: "0 8px 0 #7a1a2c" }}>1 TERMINAL</Slam>
    </div>
  );
}

let ROWS = {};
function PhoneScene({ t }) {
  const nowMin = OPEN_MIN + (850 - OPEN_MIN) * inOutCubic(seg(t, 2.25, 5.7));
  const a = outBack(seg(t, 2.0, 2.3));
  const rot = lerp(-9, -2.5, a) + 1.0 * Math.sin(t * 0.9);
  const scroll = 560 * inOutCubic(seg(t, 4.0, 4.7));
  const sc = 1.62;
  return (
    <div style={{ position: "absolute", inset: 0 }}>
      <div style={{ position: "absolute", left: 0, right: 0, top: 120, display: "flex", flexDirection: "column", alignItems: "center", zIndex: 5 }}>
        <Slam t={t} at={2.5} style={{ fontFamily: BEB, fontSize: 150, lineHeight: 0.95, ...gold }}>LIVE + PAPER</Slam>
        <Slam t={t} at={3.0} style={{ fontFamily: BEB, fontSize: 104, lineHeight: 1, color: CREAM, letterSpacing: "0.08em", textShadow: "0 5px 0 #5a0d1c",
          background: "#7a1a2c", padding: "4px 26px 0", marginTop: 8, transform: "rotate(-2deg)" }}>NEVER SUMMED</Slam>
      </div>
      <div style={{ position: "absolute", left: 540 - (424 * sc) / 2, top: lerp(1400, 470, a), transform: `rotate(${rot}deg) scale(${lerp(1.3, 1, a)})`, transformOrigin: "50% 30%", opacity: clamp(a * 2) }}>
        <Phone viewH={830} scale={sc} bezel="#140308" glow="rgba(245,197,24,0.25)">
          <PhoneDash nowMin={nowMin} scroll={scroll} onRows={(r) => { ROWS = r; }} />
        </Phone>
      </div>
      <DemoTag bottom={40} color="#e6c9b5" bg="rgba(42,6,13,0.85)" border="#7a1a2c" dot="#f5c518" font={CAT} />
    </div>
  );
}

function Years({ t }) {
  const y = 2020 + Math.min(6, Math.floor(Math.max(0, t - 7.0) / 0.22));
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
      <Slam t={t} at={6.0} from={2.6} style={{ fontFamily: BEB, fontSize: 620, lineHeight: 0.8, ...gold }}>6+</Slam>
      <Slam t={t} at={6.5} style={{ fontFamily: BEB, fontSize: 230, lineHeight: 0.95, color: CREAM, letterSpacing: "0.04em", textShadow: "0 7px 0 #5a0d1c" }}>YEARS</Slam>
      <Slam t={t} at={7.0} style={{ fontFamily: BEB, fontSize: 132, lineHeight: 1, letterSpacing: "0.12em", color: "#2a060d", background: "#f5c518", padding: "6px 34px 0", marginTop: 16, transform: "rotate(-2.5deg)", boxShadow: "0 8px 0 #7a1a2c" }}>BACKTESTED</Slam>
      {t >= 7.0 && (
        <div style={{ marginTop: 56, fontFamily: "'JetBrains Mono Variable', monospace", fontWeight: 700, fontSize: 64, color: CREAM, opacity: outCubic(seg(t, 7.0, 7.2)) }}>
          2020 → <span style={{ color: "#f5c518" }}>{y}</span>
        </div>
      )}
      {t >= 7.4 && <div style={{ marginTop: 18, fontFamily: CAT, fontWeight: 700, fontSize: 46, color: "#e6c9b5", opacity: outCubic(seg(t, 7.4, 7.7)) }}>on 1-minute NIFTY options data</div>}
    </div>
  );
}

function Fees({ t }) {
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
      <Slam t={t} at={9.0} style={{ fontFamily: BEB, fontSize: 168, lineHeight: 0.92, textAlign: "center", ...gold }}>EVERY RUPEE<br />OF FEES</Slam>
      <Slam t={t} at={9.5} from={2.4} style={{ fontFamily: BEB, fontSize: 150, lineHeight: 1, color: "#ff6b6b", border: "8px solid #ff6b6b", borderRadius: 14, padding: "6px 34px 0",
        marginTop: 26, transform: "rotate(-6deg)", letterSpacing: "0.1em", textShadow: "0 0 18px rgba(255,107,107,.4)" }}>COUNTED</Slam>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18, marginTop: 64, width: 940 }}>
        {FEES.map(([k, v], i) => {
          const at = 10.0 + i * 0.25;
          if (t < at) return <div key={k} />;
          const a = seg(t, at, at + 0.16);
          return (
            <div key={k} style={{ transform: `scale(${lerp(1.5, 1, outBack(a))})`, opacity: clamp(a * 3), background: "#3d0b16", border: "2px solid #7a1a2c", borderRadius: 16, padding: "16px 22px 12px" }}>
              <div style={{ fontFamily: BEB, fontSize: 52, color: "#f5c518", letterSpacing: "0.04em", lineHeight: 1 }}>{k}</div>
              <div style={{ fontFamily: "'JetBrains Mono Variable', monospace", fontSize: 30, color: CREAM, marginTop: 6 }}>{v}</div>
            </div>
          );
        })}
      </div>
      {t >= 11.4 && <div style={{ marginTop: 34, fontFamily: CAT, fontWeight: 700, fontSize: 40, color: "#e6c9b5", opacity: outCubic(seg(t, 11.4, 11.6)) }}>Zerodha rates · in every backtest trade</div>}
    </div>
  );
}

function Target({ t }) {
  const nowMin = 637 + (t - 12.0) * 6;     // crosses 10:40 (Outrider's target) at t = 12.5
  const b = ROWS.__blotter;
  const scroll = b ? b.y + 6 : 900;
  const a = outCubic(seg(t, 12.0, 12.25));
  const sc = 1.62;
  const burst = seg(t, 12.5, 13.6);
  return (
    <div style={{ position: "absolute", inset: 0 }}>
      <div style={{ position: "absolute", left: 540 - (424 * sc) / 2, top: lerp(560, 520, a), transform: `rotate(${lerp(4, 2, a)}deg)`, opacity: a }}>
        <Phone viewH={830} scale={sc} bezel="#140308" glow={`rgba(245,197,24,${0.25 + 0.4 * Math.exp(-(Math.max(0, t - 12.5)) / 0.4) * (t >= 12.5 ? 1 : 0)})`}>
          <PhoneDash nowMin={nowMin} scroll={scroll} focusId={t >= 12.5 ? "ORB_V1" : null} onRows={(r) => { ROWS = r; }} />
        </Phone>
      </div>
      <div style={{ position: "absolute", left: 0, right: 0, top: 150, display: "flex", flexDirection: "column", alignItems: "center", zIndex: 5 }}>
        <div style={{ fontFamily: "'JetBrains Mono Variable', monospace", fontSize: 58, fontWeight: 700, color: CREAM, opacity: a, background: "rgba(42,6,13,.8)", padding: "4px 22px", borderRadius: 12, border: "2px solid #7a1a2c" }}>{hhmm(nowMin)}</div>
        <Slam t={t} at={12.5} from={2.8} style={{ fontFamily: BEB, fontSize: 230, lineHeight: 0.95, marginTop: 10, ...gold }}>TARGET HIT!</Slam>
      </div>
      {t >= 12.5 && Array.from({ length: 36 }).map((_, i) => {
        const ang = (i / 36) * Math.PI * 2 + (i % 3) * 0.2;
        const d = 120 + 760 * outCubic(burst) * (0.6 + 0.4 * ((i * 37) % 10) / 10);
        return <i key={i} style={{ position: "absolute", left: 540 + Math.cos(ang) * d, top: 420 + Math.sin(ang) * d * 0.8 + 400 * burst * burst,
          width: 18, height: 30, background: i % 2 ? "#f5c518" : "#ffe98a", transform: `rotate(${i * 40 + t * 600}deg)`, opacity: 1 - burst, borderRadius: 3, zIndex: 6 }} />;
      })}
      <DemoTag bottom={40} color="#e6c9b5" bg="rgba(42,6,13,0.85)" border="#7a1a2c" dot="#f5c518" font={CAT} />
    </div>
  );
}

const LADDER = [10, 12, 14, 11, 15, 18, 20, 20];
function Compound({ t }) {
  const on = outCubic(seg(t, 14.55, 14.75));
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
      <Slam t={t} at={14.0} style={{ fontFamily: BEB, fontSize: 196, lineHeight: 0.92, ...gold }}>COMPOUNDING</Slam>
      <Slam t={t} at={14.5} style={{ display: "flex", alignItems: "center", gap: 26, marginTop: 14 }}>
        <span style={{ fontFamily: BEB, fontSize: 120, color: CREAM, letterSpacing: "0.06em", textShadow: "0 6px 0 #5a0d1c" }}>MODE</span>
        <span style={{ width: 190, height: 96, borderRadius: 48, background: on > 0.5 ? "#4ade80" : "#521021", position: "relative", border: "4px solid #fff3dc", boxSizing: "border-box" }}>
          <i style={{ position: "absolute", top: 8, left: lerp(10, 102, on), width: 72, height: 72, borderRadius: 36, background: "#fff3dc" }} />
        </span>
        <span style={{ fontFamily: BEB, fontSize: 120, color: "#4ade80", letterSpacing: "0.06em", opacity: on }}>ON</span>
      </Slam>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", width: 900, height: 470, marginTop: 50, position: "relative" }}>
        <div style={{ position: "absolute", left: 0, right: 0, bottom: 20 * 19 + 46, borderTop: "3px dashed #f5c518", opacity: seg(t, 15.7, 15.9) }}>
          <span style={{ position: "absolute", right: 0, top: -50, fontFamily: BEB, fontSize: 40, color: "#f5c518" }}>MAX LOTS</span>
        </div>
        {LADDER.map((v, i) => {
          const at = 14.8 + i * 0.12;
          const g = outBack(seg(t, at, at + 0.22));
          const dd = i === 3;
          return (
            <div key={i} style={{ width: 90, display: "flex", flexDirection: "column", alignItems: "center" }}>
              <span style={{ fontFamily: BEB, fontSize: 44, color: dd ? "#ff6b6b" : CREAM, opacity: clamp(g) }}>{v}</span>
              <div style={{ width: 90, height: Math.max(0, v * 19 * g), borderRadius: "10px 10px 3px 3px", border: "3px solid #2a060d",
                background: dd ? "linear-gradient(180deg,#ff6b6b,#7a1a2c)" : "linear-gradient(180deg,#ffe98a,#f5c518 40%,#b8860b)" }} />
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", width: 900, fontFamily: CAT, fontWeight: 700, fontSize: 30, color: "#c99a8a", marginTop: 12 }}>
        <span>lots · illustrative</span><span style={{ color: "#ff6b6b" }}>drawdown guard ▲</span>
      </div>
      {t >= 15.6 && <div style={{ marginTop: 26, fontFamily: CAT, fontWeight: 800, fontSize: 44, color: CREAM, opacity: outCubic(seg(t, 15.6, 15.8)) }}>Lots grow with equity. Capped. Guarded.</div>}
      <Slam t={t} at={16.0} style={{ marginTop: 26, fontFamily: BEB, fontSize: 84, letterSpacing: "0.03em", color: "#2a060d", background: "#f5c518", padding: "6px 30px 0", borderRadius: 14, transform: "rotate(-1.5deg)" }}>#COMPOUNDINGINTRADING</Slam>
    </div>
  );
}

function Title({ t }) {
  const shim = seg(t, 17.2, 19.2);
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
      <Slam t={t} at={17.0} from={3.2} style={{ fontSize: 170, lineHeight: 1, filter: "drop-shadow(0 0 40px rgba(245,197,24,.7))" }}>⚡</Slam>
      <Slam t={t} at={17.0} from={2.2} style={{ filter: "drop-shadow(0 8px 0 #5a0d1c) drop-shadow(0 0 40px rgba(245,197,24,.4))" }}>
        {["SCALP", "TERMINAL"].map((w) => (
          <div key={w} style={{ textAlign: "center", lineHeight: 0.84 }}>
            <span style={{ display: "inline-block", fontFamily: BEB, fontSize: 290, letterSpacing: "0.03em",
              backgroundImage: "linear-gradient(100deg, #b8860b 0%, #f5c518 30%, #f5c518 42%, #fffbe6 50%, #f5c518 58%, #f5c518 70%, #b8860b 100%)",
              backgroundSize: "300% 100%", backgroundPosition: `${100 - shim * 100}% 0`,
              WebkitBackgroundClip: "text", backgroundClip: "text", color: "transparent" }}>{w}</span>
          </div>
        ))}
      </Slam>
      {t >= 17.5 && <div style={{ marginTop: 34, fontFamily: BEB, fontSize: 70, letterSpacing: "0.12em", color: CREAM, opacity: outCubic(seg(t, 17.5, 17.75)) }}>ALGO OPTIONS DESK</div>}
      {t >= 17.8 && (
        <div style={{ display: "flex", gap: 14, marginTop: 22, fontFamily: CAT, fontWeight: 800, fontSize: 34, opacity: outCubic(seg(t, 17.8, 18.05)) }}>
          {["NIFTY", "Zerodha", "Live + Paper", "macOS · Windows"].map((x) => (
            <span key={x} style={{ padding: "8px 18px 4px", borderRadius: 10, border: "2px solid #7a1a2c", color: "#ffe98a", background: "#3d0b16" }}>{x}</span>
          ))}
        </div>
      )}
      {t >= 18.1 && <div style={{ marginTop: 34, fontFamily: CAT, fontWeight: 800, fontSize: 48, color: "#f5c518", opacity: outCubic(seg(t, 18.1, 18.35)) }}>#CompoundingInTrading</div>}
      {t >= 18.3 && <div style={{ position: "absolute", left: 70, right: 70, bottom: 60, textAlign: "center", fontFamily: CAT, fontSize: 26, color: "#a97d6c", opacity: outCubic(seg(t, 18.3, 18.6)) }}>
        Backtests use historical data and don't guarantee future results.</div>}
    </div>
  );
}

function Video({ t }) {
  const [sx, sy] = shake(t);
  const f = flash(t);
  const power = 1 + 0.8 * f + (t >= 17 ? 0.6 : 0);
  return (
    <div style={{ position: "relative", width: 1080, height: 1920, overflow: "hidden", background: "#12020a" }}>
      <div style={{ position: "absolute", inset: -40, transform: `translate(${sx}px, ${sy}px)` }}>
        <div style={{ position: "absolute", inset: 40 }}>
          <Rays t={t} power={power} />
          {t < 2.0 && <Hook t={t} />}
          {t >= 2.0 && t < 6.0 && <PhoneScene t={t} />}
          {t >= 6.0 && t < 9.0 && <Years t={t} />}
          {t >= 9.0 && t < 12.0 && <Fees t={t} />}
          {t >= 12.0 && t < 14.0 && <Target t={t} />}
          {t >= 14.0 && t < 17.0 && <Compound t={t} />}
          {t >= 17.0 && <Title t={t} />}
        </div>
      </div>
      <div style={{ position: "absolute", inset: 0, background: "radial-gradient(circle at 50% 40%, #fff6c8, #f5c518 60%)", opacity: f, mixBlendMode: "screen", pointerEvents: "none" }} />
      <div style={{ position: "absolute", inset: 0, background: "radial-gradient(1100px 1700px at 50% 50%, transparent 60%, rgba(0,0,0,.6))", pointerEvents: "none" }} />
    </div>
  );
}

export const VIDEO_META = { width: 1080, height: 1920, duration: 20, theme: "thalapathy", poster: 19.0, warmup: [3.0, 9.0, 13.0, 15.0, 18.0] };
mount(Video, VIDEO_META);
