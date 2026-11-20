/**
 * CALM DASHBOARD — src/pages/CalmDashboard.jsx
 *
 * ── DASH_CALM_20261117 ── the redesigned Dashboard (option B "Calm", chosen by
 * Anbu 17 Nov; App Settings → Dashboard layout = Modern, the default). The previous
 * Modern page is kept as "Classic", the old rail page as "Legacy".
 *
 *   hero      ONE big number (Live if it traded today, else Paper; the other book
 *             beside it, never summed), a sentence, Today / Week / Month tabs, chart
 *   cards     one per strategy that traded today (its Live and Paper rows share a
 *             card) + a dashed card per armed strategy that has not traded yet;
 *             each card carries its Entry/Exit meter (admin) — tap to see why
 *   focus     tap a card → its panel and live chart below (StrategyHost, as before)
 *
 * Data: GET /api/fleet/today (5 s) for the books and rows — the same feed the
 * Classic page reads; GET /api/replay/meters (admin) for every card's meter.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useIsMobile } from "../hooks/useIsMobile";
import { useEntitlements } from "../hooks/useEntitlements";
import { useMarketData } from "../context/MarketDataContext";
import StrategyHost, { META } from "../components/StrategyHost";
import { TodayRowsContext } from "../components/replay/ReplayHost";   // ── CHART_SAME_MTM_20261117 ──
import DebugPanel from "../components/DebugPanel";
import TodayStrip from "../components/fleet/TodayStrip";
import { useBrokerCheck, BrokerBadge, BrokerIssues } from "../components/fleet/SystemChecks";
import { useFleetToday } from "../components/fleet/useFleetToday";
import { useStrategyVisibility } from "../hooks/useStrategyVisibility";
import { nowMinIst } from "../components/fleet/fleetFormat";
import { stratName } from "../strategies/displayNames";
import { MARKET_START_MIN, CAS_START_MIN, FNO_END_MIN } from "../marketSession";
import { C, T, SP, RADIUS, inSession } from "../components/calm/calm";
import CalmHero from "../components/calm/CalmHero";
import { StrategyCard, IdleCard } from "../components/calm/StrategyCard";
import { useMeters } from "../components/calm/useMeters";
import { useIndexPaths } from "../components/calm/useIndexPaths";
import ReadyPanel from "../components/calm/ReadyPanel";   // ── READY_PANEL_20261118 ──   // ── INDEX_PATH_20261117 ──

const FOCUS_STORAGE_KEY = "scalp.strategyHost.focusId";   // shared with StrategyHost and the Classic page
const FILTER_KEY = "scalp.dashboard.calmFilter";
const DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MON_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function sessionText(nowMin) {
  const dow = new Date().getDay();
  if (dow === 0 || dow === 6) return "Weekend";
  if (nowMin < MARKET_START_MIN) { const m = MARKET_START_MIN - nowMin; return `market opens in ${m >= 60 ? `${Math.floor(m / 60)}h ` : ""}${m % 60}m`; }
  if (nowMin < CAS_START_MIN) { const m = FNO_END_MIN - nowMin; return `closes in ${Math.floor(m / 60)}h ${m % 60}m`; }
  if (nowMin < FNO_END_MIN) return "closing auction";
  return "market closed";
}
const clockNow = () => { const d = new Date(); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };

/* ── CARD_ORDER_20261120 ── fixed card order: in a trade first, then traded-and-closed; waiting
   (idle) cards come after both. Inside a group by name, so a card never moves when P/L changes. */
const byName = (nameOf) => (a, b) => nameOf(a.id).localeCompare(nameOf(b.id), undefined, { sensitivity: "base" });
const cardGroup = (c) => (c.row.state === "OPEN" || c.twin?.state === "OPEN" ? 0 : 1);
export function orderCards(cards, nameOf) {
  const n = byName(nameOf);
  return [...cards].sort((a, b) => cardGroup(a) - cardGroup(b) || n(a, b));
}
export const orderIdle = (idle, nameOf) => [...idle].sort(byName(nameOf));

/* one card per strategy: its Live and Paper rows together (the shown book chosen by the filter) */
function buildCards(rows, filter, nameOf) {
  const by = new Map();
  rows.forEach((r) => {
    const g = by.get(r.id) || { id: r.id, live: null, paper: null };
    if (r.book === "LIVE") g.live = r; else g.paper = r;
    by.set(r.id, g);
  });
  const out = [];
  by.forEach((g) => {
    const row = filter === "PAPER" ? g.paper : filter === "LIVE" ? g.live : (g.live || g.paper);
    if (!row) return;
    const twin = row === g.live ? g.paper : g.live;
    out.push({ id: g.id, row, twin, books: g.live && g.paper ? "Live + Paper" : g.live ? "Live" : "Paper" });
  });
  return orderCards(out, nameOf);   // ── CARD_ORDER_20261120 ── was: biggest |P/L| first (cards jumped around)
}

export default function CalmDashboard() {
  const isMobile = useIsMobile();
  const { isAdminUi } = useEntitlements();
  const { ltpMap } = useMarketData();
  const { data, loaded, error, ts } = useFleetToday(5000);
  const broker = useBrokerCheck(isAdminUi);
  const [nowMin, setNowMin] = useState(nowMinIst);
  useEffect(() => { const t = setInterval(() => setNowMin(nowMinIst()), 15000); return () => clearInterval(t); }, []);

  const [focusId, setFocusId] = useState(() => { try { return localStorage.getItem(FOCUS_STORAGE_KEY) || null; } catch { return null; } });
  const [userPicked, setUserPicked] = useState(() => { try { return !!localStorage.getItem(FOCUS_STORAGE_KEY); } catch { return false; } });
  const [filter, setFilterState] = useState(() => { try { return localStorage.getItem(FILTER_KEY) || "ALL"; } catch { return "ALL"; } });
  const setFilter = (f) => { setFilterState(f); try { localStorage.setItem(FILTER_KEY, f); } catch {} };
  const [open, setOpen] = useState(null);   // the card whose meter is expanded
  useEffect(() => {
    if (userPicked || !data) return;
    const rows = data.strategies || [];
    const first = rows.find((r) => r.state === "OPEN") || rows[0];
    if (first) setFocusId(first.id);
  }, [data, userPicked]);

  const hostRef = useRef(null);
  const pick = (id) => {
    setUserPicked(true);
    setFocusId(id);
    try { localStorage.setItem(FOCUS_STORAGE_KEY, id); } catch {}
    // same follow-the-layout scroll as the Classic page: aim at the live chart (desktop
    // admin) or the panel, clear the sticky nav; a user scroll cancels the re-aims
    if (!hostRef.current) return;
    const host = hostRef.current;
    const ctl = { stop: false };
    const stop = () => { ctl.stop = true; };
    const evs = ["wheel", "touchmove", "keydown"];
    evs.forEach((e) => window.addEventListener(e, stop, { passive: true }));
    setTimeout(() => evs.forEach((e) => window.removeEventListener(e, stop)), 1600);
    const aim = (smooth) => {
      if (ctl.stop) return;
      try {
        const target = host;   // ── CHART_BELOW_PANEL_20261117 ── the panel (legs) first; the live chart sits below it
        const nav = document.querySelector("nav");
        const navH = nav && getComputedStyle(nav).position === "sticky" ? nav.getBoundingClientRect().height : 0;
        const want = Math.round(navH + 10);
        const off = target.getBoundingClientRect().top - want;
        const maxY = document.documentElement.scrollHeight - window.innerHeight;
        if (Math.abs(off) <= 6 || (off > 0 && window.scrollY >= maxY - 2)) return;
        target.style.scrollMarginTop = `${want}px`;
        target.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
      } catch {}
    };
    setTimeout(() => aim(true), 80);
    setTimeout(() => aim(true), 750);
    setTimeout(() => aim(false), 1500);
  };

  const rows = data?.strategies || [];
  const sv = useStrategyVisibility();
  const nameOf = useCallback((id) => stratName(id, isAdminUi, META[id]?.name), [isAdminUi]);   // ── CARD_ORDER_20261120 ──
  const idle = useMemo(() => orderIdle((data?.idle || []).filter((r) => !sv.isHidden(r.id) && r.mode !== "OFF"), nameOf), [data, sv, nameOf]);
  const shownFocus = sv.isHidden(focusId) ? (rows[0]?.id || idle[0]?.id || null) : focusId;
  const both = !!(data?.totals?.LIVE?.rows > 0 && data?.totals?.PAPER?.rows > 0);
  const f = both ? filter : "ALL";
  const cards = useMemo(() => buildCards(rows, f, nameOf), [rows, f, nameOf]);

  const meterIds = useMemo(() => [...new Set([...rows.filter((r) => r.state === "OPEN").map((r) => r.id), ...idle.map((r) => r.id)])], [rows, idle]);
  const meters = useMeters(meterIds, { enabled: isAdminUi, fast: !!open, session: inSession(nowMin) });
  const market = useIndexPaths(nowMin, inSession(nowMin));   // ── INDEX_PATH_20261117 ── NIFTY / BANKNIFTY behind the MTM chart
  const toggle = (id) => { setOpen((o) => (o === id ? null : id)); if (open !== id) meters.refresh(); };

  const now = new Date();
  const dateStr = `${DAY_NAMES[now.getDay()]} ${now.getDate()} ${MON_NAMES[now.getMonth()]}`;
  const feedAge = ts ? Math.round((Date.now() - ts) / 1000) : null;
  const feedStale = loaded && (!!error || (feedAge != null && feedAge > 20));
  const active = data?.active ?? rows.reduce((s, r) => s.add(r.id), new Set()).size;
  const fleet = data ? active + idle.length : undefined;
  const nOpen = new Set(rows.filter((r) => r.state === "OPEN").map((r) => r.id)).size;
  const carried = rows.filter((r) => r.carried && r.state === "OPEN").length;

  const dateLine = (
    <div data-testid="calm-dateline" style={{ display: "flex", alignItems: "center", gap: SP.sm, flexWrap: "wrap", ...T.meta, fontSize: 13, color: C.muted, paddingRight: isMobile ? SP.xxl + SP.sm : 0 }}>
      <span>{dateStr} · {clockNow()} · {sessionText(nowMin)}</span>
      <BrokerBadge {...broker} />
      <span title={error ? String(error) : "fleet feed"} style={{ color: feedStale ? C.down : C.muted }}>
        {!loaded ? "· loading" : feedStale ? (error ? "· feed error, showing last good data" : `· feed stale ${feedAge}s`) : ""}
      </span>
    </div>
  );

  const grid = isMobile ? { display: "flex", flexDirection: "column", gap: SP.md - 2 }
    : { display: "grid", gridTemplateColumns: `repeat(auto-fill, minmax(max(300px, calc((100% - ${SP.lg * 3}px) / 4)), 1fr))`, gap: SP.lg,   /* WIDE_SCREEN_20261117: at most 4 columns, so wide screens get wider cards, not an empty column */ gridAutoFlow: "dense",
        alignItems: open ? "start" : "stretch" };   // an open card is tall: its row neighbours keep their own height

  return (
    <div data-calm-root data-testid="calm-root" style={{ padding: isMobile ? `${SP.lg}px ${SP.lg}px ${SP.xxl}px` : `${SP.xl}px ${SP.xxl}px ${SP.xxxl}px`,
      background: C.page, color: C.ink, minHeight: "100vh", fontFamily: "var(--c-font-ui)", ...T.body }}>
      <style>{`[data-testid=calm-why] [data-testid=replay-waiting] { border-bottom: 0 !important; }`}</style>
      <div style={{ maxWidth: 3200 /* WIDE_SCREEN2_20261118: was 2000; a 32-inch (2560 px) fills, a 4K TV at 1x keeps readable lines */, margin: "0 auto",   /* WIDE_SCREEN_20261117: was 1360 — left 184 px empty on each side of a 16-inch MacBook Pro */ display: "flex", flexDirection: "column", gap: isMobile ? SP.xl : SP.xxxl }}>

        {/* ── INDEX_PATH_20261117 ── the NIFTY / BANKNIFTY badges are now chips above the MTM chart */}

        {error && !data && (
          <div role="alert" style={{ padding: SP.lg, borderRadius: RADIUS.inner, border: `1px solid ${C.down}`, color: C.down }}>
            Fleet feed unavailable ({String(error)}). The backend may still be starting; Settings → App Settings → Dashboard layout switches to Classic or Legacy.
          </div>
        )}

        <BrokerIssues {...broker} isAdminUi={isAdminUi} meta={META} compact={isMobile} />

        <CalmHero totals={data?.totals} paths={data?.paths} nowMin={nowMin} armed={idle.length} carried={carried} compact={isMobile} dateLine={dateLine} market={market}
          aside={<ReadyPanel nowMin={nowMin} armed={fleet} isAdminUi={isAdminUi} compact={isMobile} />} />   {/* ── READY_PANEL_20261118 ── */}

        <section aria-label="Strategies today" data-testid="calm-strategies">
          <div style={{ display: "flex", alignItems: "baseline", gap: SP.md, flexWrap: "wrap", marginBottom: SP.lg }}>
            <h2 style={{ margin: 0, ...(isMobile ? { ...T.title, fontSize: 17 } : T.h2) }}>Strategies</h2>
            <span data-testid="calm-count" style={{ ...T.body, fontSize: 13, color: C.muted }}>
              {data ? `${active} of ${fleet} traded today${nOpen ? ` · ${nOpen} in a trade` : ""}` : "loading…"}
            </span>
            {both && (
              <div role="group" aria-label="Book" style={{ marginLeft: "auto", display: "flex", gap: SP.xs }}>
                {[["ALL", "All"], ["LIVE", "Live"], ["PAPER", "Paper"]].map(([k, l]) => (
                  <button key={k} type="button" aria-pressed={f === k} data-filter={k} onClick={() => setFilter(k)}
                    style={{ background: f === k ? C.surface : "transparent", border: `1px solid ${f === k ? C.accent : "transparent"}`, borderRadius: RADIUS.pill,   /* ── SELECT_ACCENT_20261117 ── */
                      padding: `${SP.xs + 2}px ${SP.md + 2}px`, color: f === k ? C.ink : C.muted, font: "inherit", fontSize: 13, cursor: "pointer", minHeight: 32 }}>{l}</button>
                ))}
              </div>
            )}
          </div>
          {!cards.length && !idle.length && data && (
            <div style={{ ...T.body, color: C.muted, padding: SP.xl, border: `1px dashed ${C.line}`, borderRadius: RADIUS.card }}>
              No strategy is armed and nothing traded today. Switch strategies on in Settings.
            </div>
          )}
          <div style={grid}>
            {cards.map((c) => (
              <StrategyCard key={c.id} id={c.id} name={stratName(c.id, isAdminUi, META[c.id]?.name)} accent={META[c.id]?.accent}
                row={c.row} twin={c.twin} books={c.books} meter={meters.meters[c.id]} expanded={open === c.id} onToggle={() => toggle(c.id)}
                selected={shownFocus === c.id} onFocus={pick} compact={isMobile} />
            ))}
            {f === "ALL" && idle.map((r) => (
              <IdleCard key={r.id} id={r.id} name={stratName(r.id, isAdminUi, META[r.id]?.name)} accent={META[r.id]?.accent} mode={r.mode}
                meter={meters.meters[r.id]} expanded={open === r.id} onToggle={() => toggle(r.id)} selected={shownFocus === r.id} onFocus={pick} compact={isMobile} />
            ))}
          </div>
          {meters.stale && isAdminUi && <div style={{ ...T.meta, color: C.muted, marginTop: SP.sm }}>Meters: last reading shown, the latest read failed.</div>}
        </section>

        <section ref={hostRef} aria-label="Focused strategy" data-testid="calm-focus" style={{ scrollMarginTop: 12, minWidth: 0, overflowX: isMobile ? "auto" : undefined }}>
          {shownFocus && (
            <div style={{ display: "flex", alignItems: "center", gap: SP.sm, marginBottom: SP.md }}>
              <span aria-hidden="true" style={{ width: 10, height: 10, borderRadius: 5, background: META[shownFocus]?.accent || C.line }} />
              <h2 style={{ margin: 0, ...T.title }}>{stratName(shownFocus, isAdminUi, META[shownFocus]?.name)}</h2>
              <span style={{ ...T.meta, color: C.muted }}>panel and live chart</span>
            </div>
          )}
          <TodayStrip rows={rows.filter((r) => r.id === shownFocus)} name={stratName(shownFocus, isAdminUi, META[shownFocus]?.name)} compact={isMobile} />
          <TodayRowsContext.Provider value={rows}>   {/* ── CHART_SAME_MTM_20261117 ── */}
            <StrategyHost ltpMap={ltpMap} focus={shownFocus} onFocus={pick} rail={false} recent="collapsed" meter={false} />
          </TodayRowsContext.Provider>
        </section>

        {isAdminUi && <DebugPanel />}
      </div>
    </div>
  );
}
