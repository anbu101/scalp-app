/**
 * MODERN DASHBOARD — src/pages/ModernDashboard.jsx
 *
 * ── DASH_MODERN_20260925 ── the redesigned Dashboard (Settings → App Settings
 * → Dashboard layout = Modern, the default). Desktop only: Dashboard.jsx
 * routes mobile to the legacy master/detail page.
 *
 *   header      date, index badges, session pill, feed status
 *   FleetKpis   Live MTM and Paper MTM as two separate numbers, then net,
 *               from-peak, risk-to-stops and traded-today, all per book
 *   FleetChart  intraday MTM lines: Live total, Paper total, one per
 *               (strategy, book) row that traded today
 *   FleetBlotter one row per (strategy, book) that traded today + idle chips;
 *               rows and chips FOCUS the strategy panel below
 *   StrategyHost the existing focused panel (KillSwitch + panel), rail hidden,
 *               closed-recent card folded under a drawer
 *
 * Data: ONE feed, GET /api/fleet/today (useFleetToday, 5 s). The KPIs, the
 * chart and the blotter read the same payload, so they cannot disagree. The
 * legacy "Today's Performance" read broker positions (/positions/today) and
 * therefore showed ₹0 on a paper-only day; this page reads the books.
 *
 * Focus: remembered in the same localStorage key StrategyHost uses
 * (scalp.strategyHost.focusId), so switching layouts keeps the pick. Until
 * the user picks, the first OPEN row (else the first traded row) is focused.
 */

import { useEffect, useRef, useState } from "react";   // ── DASH_MODERN_MOBILE_20261007 ── useRef
import { useIsMobile } from "../hooks/useIsMobile";
import { useEntitlements } from "../hooks/useEntitlements";
import { useMarketData } from "../context/MarketDataContext";
import StrategyHost, { META } from "../components/StrategyHost";
import MarketBadge from "../components/MarketBadge";
import FleetKpis from "../components/fleet/FleetKpis";
import FleetChart from "../components/fleet/FleetChart";
import FleetBlotter from "../components/fleet/FleetBlotter";
import TodayStrip from "../components/fleet/TodayStrip";   // ── FLEET_TODAY_STRIP_20261015 ──
import { useBrokerCheck, BrokerBadge, BrokerIssues } from "../components/fleet/SystemChecks";   // ── SYSTEM_CHECKS_UI_20261029 ── ── CHECKS_LAYOUT_20261029 ── badge + on-demand card
import { useFleetToday } from "../components/fleet/useFleetToday";
import { useStrategyVisibility } from "../hooks/useStrategyVisibility";
import { SPACE, COLOR, TYPE, CARD, CARD_COMPACT } from "../components/fleet/dash";   // ── DASH_SYSTEM_20261117 ──   // ── OFF_HIDDEN_20261117 ── (TD-009)
import { nowMinIst, bookLabel } from "../components/fleet/fleetFormat";
import { stratName } from "../strategies/displayNames";
import { colors, spacing, typography } from "../tokens";
import { MARKET_START_MIN, CAS_START_MIN, FNO_END_MIN } from "../marketSession";

const FOCUS_STORAGE_KEY = "scalp.strategyHost.focusId";   // shared with StrategyHost (PERSIST_FOCUS)
const CHART_OPEN_KEY = "scalp.dashboard.fleetChartOpen";   // ── FLEET_CHART_COLLAPSE_20260925 ── "0" = collapsed; absent = open
const DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MON_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function sessionLabel(nowMin) {
  const dow = new Date().getDay();
  if (dow === 0 || dow === 6) return { label: "Weekend", color: colors.text.muted };
  if (nowMin < MARKET_START_MIN) return { label: "Pre-market", color: colors.warning };
  if (nowMin < CAS_START_MIN) return { label: "Market open", color: colors.success };
  if (nowMin < FNO_END_MIN) return { label: "Closing auction", color: colors.warning };
  return { label: "Market closed", color: colors.text.muted };
}

// ── DASH_SYSTEM_20261117 ── one card: 1 px line, radius 8, no fill / shadow, padding 24 (16 on a phone)
function Card({ children, compact = false }) {
  return <div data-dash-part="card" style={compact ? CARD_COMPACT : CARD}>{children}</div>;
}

export default function ModernDashboard() {
  const isMobile = useIsMobile();
  const { isAdminUi } = useEntitlements();
  const { ltpMap, indices } = useMarketData();
  const { data, loaded, error, ts } = useFleetToday(5000);
  const broker = useBrokerCheck(isAdminUi);   // ── CHECKS_LAYOUT_20261029 ──
  const [nowMin, setNowMin] = useState(nowMinIst);
  useEffect(() => { const t = setInterval(() => setNowMin(nowMinIst()), 15000); return () => clearInterval(t); }, []);
  // ── FLEET_CHART_COLLAPSE_20260925 ── the chart's open/closed state, remembered across sessions
  const [chartOpen, setChartOpen] = useState(() => { try { return localStorage.getItem(CHART_OPEN_KEY) !== "0"; } catch { return true; } });
  const toggleChart = () => setChartOpen((o) => { try { localStorage.setItem(CHART_OPEN_KEY, o ? "0" : "1"); } catch {} return !o; });

  const [focusId, setFocusId] = useState(() => { try { return localStorage.getItem(FOCUS_STORAGE_KEY) || null; } catch { return null; } });
  const [userPicked, setUserPicked] = useState(() => { try { return !!localStorage.getItem(FOCUS_STORAGE_KEY); } catch { return false; } });
  useEffect(() => {
    if (userPicked || !data) return;
    const rows = data.strategies || [];
    const first = rows.find((r) => r.state === "OPEN") || rows[0];
    if (first) setFocusId(first.id);
  }, [data, userPicked]);
  const hostRef = useRef(null);   // ── DASH_MODERN_MOBILE_20261007 ──
  const pick = (id) => {
    setUserPicked(true);
    setFocusId(id);
    try { localStorage.setItem(FOCUS_STORAGE_KEY, id); } catch {}
    // ── DASH_MODERN_MOBILE_20261007 ── on a phone the panel sits below the cards: bring it into view
    // ── DASH_FOCUS_SCROLL_20261030 ── on desktop too: a click on a Traded-today row, an idle
    // chip or a chart-grid cell scrolls to the focused strategy's LIVE CHART card (admin;
    // else its panel area), clearing the sticky header — no manual scrolling down.
    if (hostRef.current) {
      const host = hostRef.current;
      // the strip / card above can change height as the new strategy renders, so the scroll
      // is re-aimed after the layout settles (a smooth scroll fixes its destination at start)
      // a wheel / touch / key scroll by the user cancels the follow-up re-aims
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
    }
  };

  const rows = data?.strategies || [];
  // ── OFF_HIDDEN_20261117 ── OFF strategies that are flat and did not trade today get no idle
  // chip; a remembered focus on one falls back to the first row (rows are always visible).
  const sv = useStrategyVisibility();
  const idle = (data?.idle || []).filter((r) => !sv.isHidden(r.id));
  const shownFocus = sv.isHidden(focusId) ? (rows[0]?.id || idle[0]?.id || null) : focusId;
  const series = [
    { key: "__LIVE__", name: "Live", color: "var(--c-text-primary)", width: 2.2, fill: true, path: data?.paths?.LIVE || [], value: data?.totals?.LIVE?.gross },
    { key: "__PAPER__", name: "Paper", color: "var(--c-text-secondary)", width: 2, dash: true, path: data?.paths?.PAPER || [], value: data?.totals?.PAPER?.gross },
    ...rows.map((r) => ({ key: r.key, strategyId: r.id, name: `${stratName(r.id, isAdminUi, META[r.id]?.name)} ${bookLabel(r.book)}`, color: META[r.id]?.accent || colors.border.light, width: 1.4, path: r.path || [], value: r.gross })),   // ── FLEET_CHART_V2_20260928 ── strategyId for grid-cell focus
  ].filter((s) => !((s.key === "__LIVE__" || s.key === "__PAPER__")   // ── LIVE_ROW_HIDE_20260925 ── a book with no orders today is not drawn
    && !(data?.totals?.[s.key === "__LIVE__" ? "LIVE" : "PAPER"]?.rows > 0)));

  const now = new Date();
  const dateStr = `${DAY_NAMES[now.getDay()]}, ${now.getDate()} ${MON_NAMES[now.getMonth()]} ${now.getFullYear()}`;
  const session = sessionLabel(nowMin);
  const feedAge = ts ? Math.round((Date.now() - ts) / 1000) : null;
  const feedStale = loaded && (!!error || (feedAge != null && feedAge > 20));

  return (
    <div data-dash-root style={{ padding: isMobile ? SPACE.lg : SPACE.xxl, background: colors.bg.page, color: COLOR.ink,   /* ── DASH_SYSTEM_20261117 ── */
      minHeight: "100vh", fontFamily: "var(--c-font-ui)", display: "flex", flexDirection: "column", gap: isMobile ? SPACE.lg : SPACE.xl }}>

      {isMobile && (   /* ── DASH_MODERN_MOBILE_20261007 ── phone header: date, session, feed dot; index badges swipe */
        <div style={{ display: "flex", flexDirection: "column", gap: SPACE.sm }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: SPACE.sm, flexWrap: "wrap", paddingRight: SPACE.xxl + SPACE.sm }}>   {/* ── DASH_SYSTEM_20261117 ── right room: the floating bell */}
            <div style={{ ...TYPE.title, color: COLOR.ink }}>{dateStr}</div>
            <span style={{ ...TYPE.meta, color: COLOR.muted }}>{session.label}</span>
            <BrokerBadge {...broker} />   {/* ── CHECKS_LAYOUT_20261029 ── */}
            <span title={error ? String(error) : "fleet feed"} style={{ marginLeft: "auto", ...TYPE.meta, color: feedStale ? COLOR.down : COLOR.muted }}>
              {!loaded ? "loading" : feedStale ? (error ? "error" : `stale ${feedAge}s`) : "live"}
            </span>
          </div>
          <div className="md-idx" style={{ display: "flex", gap: spacing.sm, overflowX: "auto", paddingBottom: 2 }}>
            <style>{`.md-idx::-webkit-scrollbar { display: none; } .md-idx { scrollbar-width: none; }`}</style>
            <span style={{ flex: "0 0 auto" }}><MarketBadge name="NIFTY" data={indices?.NIFTY} /></span>
            <span style={{ flex: "0 0 auto" }}><MarketBadge name="BANKNIFTY" data={indices?.BANKNIFTY} /></span>
          </div>
        </div>
      )}

      {!isMobile && (<div data-dash-part="header" style={{ display: "flex", alignItems: "baseline", gap: SPACE.md, flexWrap: "wrap" }}>   {/* ── DASH_SYSTEM_20261117 ── */}
        <div style={{ ...TYPE.title, color: COLOR.ink }}>{dateStr}</div>
        <span style={{ ...TYPE.meta, color: COLOR.muted }}>{session.label}</span>
        <BrokerBadge {...broker} />   {/* ── CHECKS_LAYOUT_20261029 ── */}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "baseline", gap: SPACE.lg, flexWrap: "wrap" }}>
          <MarketBadge name="NIFTY" data={indices?.NIFTY} />
          <MarketBadge name="BANKNIFTY" data={indices?.BANKNIFTY} />
          <span title={error ? String(error) : "fleet feed"} style={{ ...TYPE.meta, color: feedStale ? COLOR.down : COLOR.muted }}>
            {!loaded ? "loading feed" : feedStale ? (error ? "feed error, showing last good data" : `feed stale ${feedAge}s`) : "books live"}
          </span>
        </div>
      </div>)}

      {error && !data && (
        <div style={{ padding: SPACE.lg, borderRadius: 8, border: `1px solid ${COLOR.down}`, color: COLOR.down, ...TYPE.body }}>
          Fleet feed unavailable ({String(error)}). The backend may still be starting; Settings → App Settings → Dashboard layout switches to Legacy.
        </div>
      )}

      {/* ── SYSTEM_CHECKS_UI_20261029 ── ── CHECKS_LAYOUT_20261029 ── broker-check detail card: only when
          positions differ, or the header badge was clicked (admin; hidden without the recon patch) */}
      <BrokerIssues {...broker} isAdminUi={isAdminUi} meta={META} compact={isMobile} />

      <FleetKpis totals={data?.totals} paths={data?.paths} active={data?.active} fleet={data ? (data.active ?? 0) + idle.length : undefined} nowMin={nowMin} compact={isMobile} />   {/* ── DASH_MODERN_MOBILE_20261007 ── compact   ── OFF_HIDDEN_20261117 ── "of N" excludes hidden OFF strategies */}

      <Card compact={isMobile}>   {/* ── DASH_SYSTEM_20261117 ── */}
        <FleetChart series={series} nowMin={nowMin} height={isMobile ? 176 : 216} collapsed={!chartOpen} onToggle={toggleChart} onFocusStrategy={pick} compact={isMobile} />   {/* ── FLEET_CHART_V2_20260928 ── ── DASH_MODERN_MOBILE_20261007 ── */}
      </Card>

      <Card compact={isMobile}>
        <FleetBlotter rows={rows} idle={idle} focusId={shownFocus} onFocus={pick} isAdminUi={isAdminUi} meta={META} compact={isMobile} />   {/* ── DASH_MODERN_MOBILE_20261007 ── compact */}
      </Card>

      {/* ── DASH_MODERN_MOBILE_20261007 ── scroll target on a phone; a panel wider than the
          phone (TSG's legs table) scrolls sideways inside its own box instead of widening the page */}
      <div ref={hostRef} style={{ scrollMarginTop: 12, minWidth: 0, overflowX: isMobile ? "auto" : undefined }}>
        {/* ── FLEET_TODAY_STRIP_20261015 ── the row's own numbers + what the panel below measures */}
        <TodayStrip rows={rows.filter((r) => r.id === shownFocus)} name={stratName(shownFocus, isAdminUi, META[shownFocus]?.name)} compact={isMobile} />
        <StrategyHost ltpMap={ltpMap} focus={shownFocus} onFocus={pick} rail={false} recent="collapsed" />
      </div>

      {/* ── DASH_TIDY_20261126 ── the Debug tools live on Settings (admin) now */}
    </div>
  );
}
