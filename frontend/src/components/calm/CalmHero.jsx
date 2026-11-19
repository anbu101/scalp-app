// frontend/src/components/calm/CalmHero.jsx — ── DASH_CALM_20261117 ──
// The top of the Dashboard: ONE big number (the hero book's MTM), a plain sentence
// saying what it means, Today / This week / This month tabs that each show their
// own figure, and the day's chart. Live is the hero when it traded today, Paper
// otherwise; with both, the other book sits beside it, smaller — never summed.
// Phone: a Live | Paper switch picks the hero book.
import { useState } from "react";
import { INDEX_KEYS } from "./useIndexPaths";   // ── INDEX_PATH_20261117 ──
import { C, T, SP, RADIUS, tone } from "./calm";
import { fmtInr } from "../fleet/fleetFormat";
import CalmChart from "./CalmChart";

const BOOK_NAME = { LIVE: "Live", PAPER: "Paper" };
const PERIODS = [["today", "Today"], ["week", "This week"], ["month", "This month"]];

function value(t, period) {
  if (!t) return 0;
  return period === "week" ? t.week_net ?? 0 : period === "month" ? t.month_net ?? 0 : t.gross ?? 0;
}

function sentence(book, t, period, both) {
  if (period !== "today") {
    const n = period === "week" ? t.week_trades : t.month_trades;
    const from = period === "week" ? t.week_from : t.month_from;
    return `${n ?? 0} trade${n === 1 ? "" : "s"} exited since ${from || "—"}, after charges.`;
  }
  const parts = [];
  if (t.peak != null) {
    const off = (t.peak ?? 0) - (t.gross ?? 0);
    parts.push(off > 0.5 ? `${BOOK_NAME[book]} is ₹${Math.round(off).toLocaleString("en-IN")} below today's high.` : `${BOOK_NAME[book]} is at today's high.`);
  }
  const r = Math.round(t.realised || 0), u = Math.round(t.unrealised || 0);
  const bits = [];
  if (r) bits.push(`closed trades ${r > 0 ? "made" : "lost"} ₹${Math.abs(r).toLocaleString("en-IN")}`);
  if (u) bits.push(`open positions are ${u > 0 ? "up" : "down"} ₹${Math.abs(u).toLocaleString("en-IN")}`);
  if (bits.length) { const s = bits.join("; "); parts.push(s[0].toUpperCase() + s.slice(1) + "."); }
  if (both) parts.push("Live and Paper are never added together.");
  return parts.join(" ");
}

// ── INDEX_PATH_20261117 ── NIFTY / BANKNIFTY chips above the chart: price, % from yesterday's
// close, today's line in miniature. Tapping one draws it behind the MTM line (remembered).
export const INDEX_COLOR = { NIFTY: "#5FA8D3", BANKNIFTY: "#D9A441" };
const OVERLAY_KEY = "scalp.dashboard.indexOverlay";
const fmtPx = (v) => v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function Mini({ path, prev, up }) {
  const pts = (path || []).map((p) => p[1]);
  if (pts.length < 2 || !prev) return null;
  const lo = Math.min(prev, ...pts), hi = Math.max(prev, ...pts), r = hi - lo || 1;
  const y = (v) => 2 + 16 * (hi - v) / r;
  const d = pts.map((v, i) => `${i ? "L" : "M"}${((i / (pts.length - 1)) * 72).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return (
    <svg width="72" height="20" viewBox="0 0 72 20" aria-hidden="true" style={{ flexShrink: 0 }}>
      <line x1="0" x2="72" y1={y(prev)} y2={y(prev)} stroke={C.line} strokeDasharray="2 2" />
      <path d={d} fill="none" stroke={up ? C.up : C.down} strokeWidth="1.5" />
    </svg>
  );
}

function IndexChips({ market, on, toggle, compact, start = false }) {   // ── INDEX_LEFT_20261118 ── start: left-align (pre-open hero)
  return (
    <div role="group" aria-label="Show an index on the chart" data-testid="calm-index-chips"
      style={{ display: "flex", gap: SP.sm, flexWrap: "wrap", justifyContent: compact || start ? "flex-start" : "flex-end", minWidth: 0, maxWidth: "100%" }}>
      {INDEX_KEYS.map((k) => {
        const m = market?.[k] || {};
        if (m.ltp == null) return null;
        const chg = m.prev ? ((m.ltp - m.prev) / m.prev) * 100 : null;
        const up = chg == null || chg >= 0;
        const sel = on.includes(k);
        return (
          <button key={k} type="button" aria-pressed={sel} data-index={k} onClick={() => toggle(k)}
            title={sel ? `Hide ${k} from the chart` : `Draw ${k}'s move behind your MTM line`}
            style={{ display: "inline-flex", alignItems: "center", gap: SP.sm, flexShrink: 0, minHeight: 36, cursor: "pointer", font: "inherit",
              ...T.meta, fontSize: 13, color: C.ink, borderRadius: RADIUS.pill, padding: `${SP.xs + 2}px ${SP.md + 2}px`,
              background: sel ? C.surface : "transparent", border: `1px solid ${sel ? C.accent : C.line}`, boxShadow: sel ? `inset 0 0 0 1px ${C.accent}` : "none" }}>   {/* ── SELECT_ACCENT_20261117 ── theme accent, like the selected card */}
            <span aria-hidden="true" style={{ width: 14, borderTop: `2px dashed ${sel ? INDEX_COLOR[k] : C.muted}` }} />
            <b style={{ fontWeight: 600 }}>{k}</b>
            <span style={{ ...T.num }}>{fmtPx(m.ltp)}</span>
            {chg != null && <span style={{ ...T.num, fontWeight: 600, color: up ? C.up : C.down }}>{chg >= 0 ? "+" : "−"}{Math.abs(chg).toFixed(2)}%</span>}
            {!compact && <Mini path={m.path} prev={m.prev} up={up} />}
          </button>
        );
      })}
    </div>
  );
}

// ── READY_PANEL_20261118 ── nothing traded yet today: this week's and this month's net still
// show (Live when it has trades this month, else Paper; never added together)
function PeriodChips({ totals, compact }) {
  const book = ["LIVE", "PAPER"].find((b) => (totals?.[b]?.month_trades || 0) > 0 || (totals?.[b]?.week_trades || 0) > 0);
  if (!book) return null;
  const t = totals[book];
  return (
    <div data-testid="calm-period-chips" data-book={book} style={{ display: "flex", gap: SP.sm, flexWrap: "wrap" }}>
      {[["week", "This week"], ["month", "This month"]].map(([id, label]) => {
        const v = value(t, id);
        return (
          <div key={id} data-period={id} title={`${t[id + "_trades"] ?? 0} trades exited since ${t[id + "_from"] || "—"}, after charges`}
            style={{ minWidth: compact ? 0 : 150, flex: compact ? 1 : "0 0 auto", border: `1px solid ${C.line}`, borderRadius: RADIUS.inner, padding: `${SP.sm}px ${SP.lg}px` }}>
            <span style={{ display: "block", ...T.meta, color: C.muted }}>{label} · {BOOK_NAME[book]}</span>
            <span style={{ display: "block", ...T.body, fontWeight: 600, ...T.num, color: tone(v) }}>{fmtInr(v)}{t[id + "_approx"] ? " ≈" : ""}</span>
          </div>
        );
      })}
    </div>
  );
}

function LiveTag() {
  return <span style={{ ...T.meta, fontWeight: 600, letterSpacing: "0.06em", color: C.ink, border: `1px solid ${C.ink}`, borderRadius: 4, padding: `0 ${SP.xs}px` }}>LIVE</span>;
}

export default function CalmHero({ totals, paths, nowMin, armed = 0, carried = 0, compact = false, dateLine, market = null, aside = null }) {   // ── READY_PANEL_20261118 ── aside
  const traded = ["LIVE", "PAPER"].filter((b) => totals?.[b]?.rows > 0);
  const [period, setPeriod] = useState("today");
  const [pick, setPick] = useState(null);
  const [on, setOn] = useState(() => { try { const v = JSON.parse(localStorage.getItem(OVERLAY_KEY) || '["NIFTY"]'); return Array.isArray(v) ? v : ["NIFTY"]; } catch { return ["NIFTY"]; } });
  const toggle = (k) => setOn((o) => { const n = o.includes(k) ? o.filter((x) => x !== k) : [...o, k]; try { localStorage.setItem(OVERLAY_KEY, JSON.stringify(n)); } catch {} return n; });
  const overlays = on.filter((k) => market?.[k]?.prev && (market[k].path || []).length > 1)
    .map((k) => ({ key: k, label: k, color: INDEX_COLOR[k], prev: market[k].prev, path: market[k].path }));
  const chips = market ? <IndexChips market={market} on={on} toggle={toggle} compact={compact} /> : null;
  const heroBook = traded.includes(pick) ? pick : traded[0] || null;
  const other = traded.find((b) => b !== heroBook) || null;
  const both = !!other;
  const t = heroBook ? totals[heroBook] : null;
  const big = t ? value(t, period) : 0;

  const lines = traded.map((b) => ({
    key: b, label: BOOK_NAME[b], path: paths?.[b] || [], primary: b === heroBook,
    color: b === heroBook ? tone(totals[b].gross) : C.muted, dash: b !== heroBook,
  }));

  const bigStyle = compact ? T.heroSm : T.hero;
  const tabs = (
    <div role="tablist" aria-label="Period" style={{ display: "flex", gap: SP.sm, flexWrap: compact ? "nowrap" : "wrap" }}>
      {PERIODS.map(([id, label]) => {
        const on = id === period;
        return (
          <button key={id} type="button" role="tab" aria-selected={on} data-period={id} onClick={() => setPeriod(id)}
            style={{ flex: compact ? 1 : "0 0 auto", minWidth: compact ? 0 : 136, minHeight: 44, textAlign: "left", cursor: "pointer", font: "inherit",
              background: on ? C.surface : "transparent", border: `1px solid ${on ? C.accent : C.line}`, boxShadow: on ? `inset 0 0 0 1px ${C.accent}` : "none", borderRadius: RADIUS.inner,   // ── SELECT_ACCENT_20261117 ──
              padding: `${SP.sm}px ${compact ? SP.md : SP.lg}px`, color: C.ink }}>
            <span style={{ display: "block", ...T.meta, color: C.muted }}>{compact ? label.replace("This w", "W").replace("This m", "M") : label}</span>
            <span style={{ display: "block", ...T.body, fontWeight: 600, ...T.num }}>
              {t ? <span style={{ color: tone(value(t, id)) }}>{fmtInr(value(t, id))}</span> : "—"}
            </span>
          </button>
        );
      })}
    </div>
  );

  if (!heroBook) {
    return (
      <section aria-label="Today" data-testid="calm-hero" data-book="none" style={{ display: "flex", flexDirection: compact ? "column" : "row", flexWrap: "wrap", alignItems: "flex-start", gap: compact ? SP.lg : SP.xxxl }}>
        <div style={{ flex: compact ? "1 1 auto" : "1 1 460px", minWidth: 0, display: "flex", flexDirection: "column", gap: SP.lg }}>   {/* ── READY_PANEL_20261118 ── left column; the panel sits beside it */}
        {dateLine}
        <div style={{ ...bigStyle, color: C.muted }}>₹0</div>
        <p style={{ margin: 0, ...T.lead, color: C.sub, maxWidth: 520 }}>
          No trades yet today.{armed ? ` ${armed} strateg${armed === 1 ? "y is" : "ies are"} armed.` : ""}{carried ? ` ${carried} carried position${carried === 1 ? "" : "s"} from earlier sessions.` : ""}
        </p>
        <PeriodChips totals={totals} compact={compact} />
        {market ? <IndexChips market={market} on={on} toggle={toggle} compact={compact} start /> : null}
        {overlays.length > 0 && <CalmChart lines={[]} overlays={overlays} nowMin={nowMin} height={compact ? 120 : 180} compact={compact} />}
        </div>
        {aside}
      </section>
    );
  }

  const left = (
    <div style={{ flex: compact ? "1 1 auto" : "1 1 460px", minWidth: compact ? 0 : 300, maxWidth: compact ? undefined : 620, display: "flex", flexDirection: "column", gap: compact ? SP.md : SP.lg }}>
      {dateLine}
      {compact && both && (
        <div role="group" aria-label="Book" style={{ display: "flex", gap: SP.sm }}>
          {traded.map((b) => (
            <button key={b} type="button" aria-pressed={b === heroBook} onClick={() => setPick(b)}
              style={{ flex: 1, minHeight: 44, textAlign: "left", font: "inherit", cursor: "pointer", borderRadius: RADIUS.inner, padding: `${SP.sm}px ${SP.md}px`,
                background: b === heroBook ? C.surface : "transparent", border: `1px solid ${b === heroBook ? C.accent : C.line}`, boxShadow: b === heroBook ? `inset 0 0 0 1px ${C.accent}` : "none", color: C.ink }}>   {/* ── SELECT_ACCENT_20261117 ── */}
              <span style={{ display: "block", ...T.meta, color: C.muted }}>{BOOK_NAME[b]}</span>
              <span style={{ ...T.body, fontWeight: 600, color: tone(totals[b].gross), ...T.num }}>{fmtInr(totals[b].gross)}</span>
            </button>
          ))}
        </div>
      )}
      <div style={{ display: "flex", alignItems: "flex-end", gap: SP.xxl, flexWrap: "wrap" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: SP.sm, ...T.meta, color: C.muted }}>
            {heroBook === "LIVE" ? <LiveTag /> : <span>Paper</span>}
            <span>{period === "today" ? "MTM today" : period === "week" ? "this week, net" : "this month, net"}</span>
            {t.approx ? <span title="some legs are priced at their last seen mark">≈</span> : null}
          </div>
          <div data-testid="calm-hero-figure" style={{ ...bigStyle, color: tone(big), whiteSpace: "nowrap" }}>{fmtInr(big)}</div>
        </div>
        {both && !compact && (
          <button type="button" onClick={() => setPick(other)} title={`Make ${BOOK_NAME[other]} the big number`} data-testid="calm-hero-other"
            style={{ background: "transparent", border: 0, padding: `0 0 ${SP.xs}px`, textAlign: "left", cursor: "pointer", font: "inherit", color: C.ink }}>
            <span style={{ display: "block", ...T.meta, color: C.muted }}>{BOOK_NAME[other]}</span>
            <span style={{ ...T.second, color: C.sub }}>{fmtInr(value(totals[other], period))}</span>
          </button>
        )}
      </div>
      <p data-testid="calm-hero-sentence" style={{ margin: 0, ...(compact ? T.body : T.lead), color: C.sub, maxWidth: 480 }}>{sentence(heroBook, t, period, both)}</p>
      {!compact && tabs}
    </div>
  );

  return (
    <section aria-label="Today" data-testid="calm-hero" data-book={heroBook} style={{ display: "flex", flexDirection: compact ? "column" : "row", flexWrap: "wrap", gap: compact ? SP.lg : SP.xxxl }}>
      {left}
      {compact && tabs}
      <div style={{ flex: compact ? "1 1 auto" : "999 1 480px", minWidth: 0, display: "flex", flexDirection: "column", justifyContent: aside ? "flex-start" : "flex-end", gap: SP.md }}>
        {aside ? <div style={{ display: "flex" }}>{aside}</div> : null}   {/* ── READY_HERO_FIT_20261119 ── row wrapper: the panel's flex size is a width, not a height */}
        {chips}
        {aside && !lines.some((l) => l.path.length > 1) && !overlays.length ? null : <CalmChart lines={lines} overlays={overlays} nowMin={nowMin} height={compact ? 120 : 220} compact={compact} />}
      </div>
    </section>
  );
}
