/**
 * DASHBOARD  (redesigned — consumes app-level MarketDataContext)
 *
 * Intended path: src/pages/Dashboard.jsx
 *
 * The market-data + P&L pipeline (ltpMap, indices, positions) now lives in
 * MarketDataProvider (src/context/MarketDataContext.jsx) so it can be shown
 * app-wide (nav P&L pill + status bar). Dashboard is now a CONSUMER of that
 * shared state, not the owner.
 *
 * Dashboard still owns its own light concerns:
 *   - Zerodha connection status (3s)
 *   - Backend/engine health (3s)
 *   - trade_on global flag (15s)
 *
 * The Today's Performance section remains here in full detail; the nav/status
 * bar show condensed versions of the same numbers.
 */

import { useEffect, useState } from "react";
import { useIsMobile } from "../hooks/useIsMobile";
import { getZerodhaStatus, getStatus, getGlobalConfig } from "../api";
import {
  LoadingAnimations, FullPageLoader, EmptyState, CardSkeleton,
} from "../components/LoadingStates";
import StrategyHost from "../components/StrategyHost";
import DebugPanel from "../components/DebugPanel";
import { useEntitlements } from "../hooks/useEntitlements";
import { useMarketData } from "../context/MarketDataContext";
import MarketBadge from "../components/MarketBadge"; // ── INDEX_BADGES_20260827 ──
import { colors, spacing, typography, pnlStyle as _pnlStyle } from "../tokens";
// ── CAS_2026 ── single source of truth for session boundaries
import { MARKET_START_MIN, CAS_START_MIN, FNO_END_MIN } from "../marketSession";

const safeNum = (v) => (typeof v === "number" && !isNaN(v) ? v : 0);
const pnlStyle = _pnlStyle;

/* ── UI atoms ── */
function Card({ children, style, elevated }) {
  return (
    <div style={{
      background: elevated ? colors.bg.tertiary : colors.bg.secondary,
      border: `1px solid ${colors.border.light}`,
      borderRadius: 8,
      boxShadow: elevated ? "0 4px 6px -1px rgba(0,0,0,0.3), 0 2px 4px -1px rgba(0,0,0,0.2)" : "0 1px 3px rgba(0,0,0,0.2)",
      ...style,
    }}>{children}</div>
  );
}

function StatusBadge({ ok, text, warn, danger, icon }) {
  let bg = colors.dangerBg, color = colors.danger, borderColor = colors.danger;
  if (ok)        { bg = colors.successBg; color = colors.success; borderColor = colors.success; }
  else if (warn) { bg = colors.warningBg; color = colors.warning; borderColor = colors.warning; }
  else if (danger){ bg = colors.dangerBg; color = colors.danger;  borderColor = colors.danger; }
  return (
    <span style={{
      padding: "4px 10px", borderRadius: 6, ...typography.bodySmall, fontWeight: 600,
      background: bg, color, border: `1px solid ${borderColor}40`,
      display: "inline-flex", alignItems: "center", gap: 4, minWidth: "90px",
      justifyContent: "center", textTransform: "uppercase", letterSpacing: "0.3px",
    }}>
      {icon && <span style={{ fontSize: 10 }}>{icon}</span>}{text}
    </span>
  );
}

/* ── INDEX_BADGES_20260827 ── MarketBadge extracted to components/MarketBadge.jsx ── */

function PnLRow({ label, value, large }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <span style={{ ...(large ? typography.bodyLarge : typography.bodyMedium), color: colors.text.secondary, fontWeight: large ? 600 : 400 }}>{label}</span>
      <span style={{ ...(large ? typography.headingMedium : typography.bodyMedium), ...typography.mono, ...pnlStyle(value) }}>
        ₹{Math.round(value).toLocaleString("en-IN")}
      </span>
    </div>
  );
}

// ACC2_D9: `account` tag + `showAccount` — badge renders ONLY when rows from
// more than one account are present, so today's single-account view is
// pixel-identical.
function AccountBadge({ account }) {
  const a = account === "ANGELONE";
  return (
    <span style={{ fontSize: 9, fontWeight: 800, borderRadius: 4, padding: "1px 5px",
      marginRight: 6, flexShrink: 0, letterSpacing: "0.5px",
      color: a ? "#f59e0b" : "#3b82f6",
      background: a ? "rgba(245,158,11,0.12)" : "rgba(59,130,246,0.12)",
      border: `1px solid ${a ? "#f59e0b55" : "#3b82f655"}` }}>
      {a ? "A" : "Z"}
    </span>
  );
}

function PositionRow({ symbol, qty, pnl, account, showAccount }) {
  const v = safeNum(pnl);
  const accent = v > 0 ? colors.profit : v < 0 ? colors.loss : colors.border.light;
  return (
    <div style={{ ...typography.bodySmall, ...typography.mono, marginBottom: spacing.xs,
      padding: `${spacing.xs}px ${spacing.sm}px`, background: colors.bg.secondary, borderRadius: 4,
      borderLeft: `3px solid ${accent}`, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <span style={{ color: colors.text.secondary }}>{showAccount && <AccountBadge account={account} />}{symbol} × {qty}</span>
      <span style={pnlStyle(v)}>{v > 0 ? "+" : ""}₹{Math.round(v).toLocaleString("en-IN")}</span>
    </div>
  );
}

const DAY_NAMES = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
const MON_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

function getSessionLabel() {
  const now = new Date(); const dow = now.getDay();
  if (dow === 0 || dow === 6) return { label: "Weekend", color: colors.text.muted, bg: "rgba(100,116,139,0.12)" };
  const mins = now.getHours() * 60 + now.getMinutes();
  // ── CAS_2026 ── NFO closes 15:40 from 2026-08-03. The auction window is
  // surfaced as its own label: cash trading in F&O-underlying stocks has
  // stopped and the index is indicative, but options still trade to 15:40.
  if (mins < MARKET_START_MIN) return { label: "Pre-Market",  color: colors.warning,     bg: colors.warningBg };
  if (mins < CAS_START_MIN)    return { label: "Market Open", color: colors.success,     bg: colors.successBg };
  if (mins < FNO_END_MIN)      return { label: "Closing Auction", color: colors.warning, bg: colors.warningBg };
  return { label: "Market Closed", color: colors.text.muted, bg: "rgba(100,116,139,0.12)" };
}

function DashboardHeader({ indices }) {
  const [session, setSession] = useState(getSessionLabel);
  useEffect(() => { const t = setInterval(() => setSession(getSessionLabel()), 30000); return () => clearInterval(t); }, []);
  const now = new Date();
  const dateStr = `${DAY_NAMES[now.getDay()]}, ${now.getDate()} ${MON_NAMES[now.getMonth()]} ${now.getFullYear()}`;
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.lg, flexWrap: "wrap", gap: spacing.sm }}>
      <div>
        <div style={{ ...typography.label, color: colors.text.muted, marginBottom: 3 }}>Dashboard</div>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: colors.text.primary, lineHeight: 1.2 }}>{dateStr}</h1>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: spacing.sm, flexWrap: "wrap" }}>
        <MarketBadge name="NIFTY"     data={indices.NIFTY}     />
        <MarketBadge name="BANKNIFTY" data={indices.BANKNIFTY} />
        <span style={{ fontSize: 12, fontWeight: 600, padding: "5px 14px", borderRadius: 20,
          background: session.bg, color: session.color, border: `1px solid ${session.color}40`, letterSpacing: "0.3px" }}>
          {session.label}
        </span>
      </div>
    </div>
  );
}

export default function Dashboard() {
  const isMobile = useIsMobile();
  const { isAdminUi } = useEntitlements();
  const { ltpMap, indices, positions, positionsLoading } = useMarketData();
  // ACC2_D9: badge only when both accounts contribute rows; banner only when
  // the backend reports a configured account it could not read (fail-visible).
  const multiAccount = new Set(
    [...(positions.open || []), ...(positions.closed || [])].map((r) => r.account || "ZERODHA")
  ).size > 1;
  const positionsPartial = positions.partial === true;

  const [zerodha, setZerodha] = useState(null);
  const [status, setStatus] = useState(null);
  const [backendHealth, setBackendHealth] = useState("BOOTING");
  const [loading, setLoading] = useState(true);
  const [globalConfig, setGlobalConfig] = useState(null);

  useEffect(() => {
    async function loadFast() {
      try {
        const s = await getStatus(); setStatus(s);
        setBackendHealth(s?.backend === "UP" ? "UP" : "DOWN");
      } catch { setBackendHealth((prev) => (prev === "UP" ? "DOWN" : prev)); }
      try { setZerodha(await getZerodhaStatus()); } catch {}
    }
    loadFast(); setLoading(false);
    const t = setInterval(loadFast, 3000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    async function loadConfig() { try { setGlobalConfig(await getGlobalConfig()); } catch {} }
    loadConfig();
    const t = setInterval(loadConfig, 15000);
    return () => clearInterval(t);
  }, []);

  const tradingEnabled = globalConfig?.trade_on === true;

  if (loading) {
    return (<><LoadingAnimations /><FullPageLoader message="Loading dashboard..." /></>);
  }

  return (
    <div style={{ padding: isMobile ? spacing.md : spacing.xxl, background: colors.bg.primary,
      color: colors.text.primary, minHeight: "100vh",
      fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" }}>

      <div style={{ marginBottom: spacing.xxl }}>
        <DashboardHeader indices={indices} />
        <Card elevated style={{ padding: spacing.md }}>
          <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap" }}>
            <StatusBadge
              ok={zerodha?.connected === true} warn={zerodha === null} danger={zerodha?.connected === false}
              text={zerodha === null ? "Checking..." : zerodha?.connected ? "Connected" : "Disconnected"}
              icon={zerodha === null ? "◐" : zerodha?.connected ? "●" : "○"} />
            <StatusBadge
              ok={backendHealth === "UP" && status?.engine === "RUNNING"}
              warn={backendHealth === "BOOTING"} danger={backendHealth === "DOWN"}
              text={backendHealth === "BOOTING" ? "Backend Starting" : backendHealth === "DOWN" ? "Backend Down"
                : status?.engine === "RUNNING" ? "Engine Running" : "Engine Paused"} icon="⚡" />
            <StatusBadge ok={tradingEnabled} warn={!tradingEnabled}
              text={tradingEnabled ? "Trading" : "Paused"} icon={tradingEnabled ? "▶" : "⏸"} />
          </div>
        </Card>
      </div>

      <div style={{ marginBottom: spacing.xxl }}>
        <StrategyHost ltpMap={ltpMap} />
      </div>

      <div>
        <h2 style={{ ...typography.headingLarge, color: colors.text.primary, marginBottom: spacing.md }}>
          Today's Performance
        </h2>
        <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "repeat(auto-fit, minmax(300px, 1fr))", gap: spacing.md }}>
          {(() => {
            const total = positions.totals.total;
            const isProfit = total > 0, isLoss = total < 0;
            const accent = isProfit ? colors.profit : isLoss ? colors.loss : colors.border.light;
            return (
              <div style={{ background: colors.bg.secondary, border: `1px solid ${colors.border.light}`,
                borderLeft: `3px solid ${accent}`, borderRadius: 8, boxShadow: "0 4px 6px -1px rgba(0,0,0,0.3)", padding: spacing.lg }}>
                <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>Summary</div>
                {positionsLoading ? <CardSkeleton rows={3} /> : (
                  <div style={{ display: "flex", flexDirection: "column", gap: spacing.sm }}>
                    <PnLRow label="Realised" value={positions.totals.realised} />
                    <PnLRow label="Unrealised" value={positions.totals.unrealised} />
                    <div style={{ marginTop: spacing.sm, paddingTop: spacing.md, borderTop: `1px solid ${colors.border.dark}` }}>
                      <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.xs }}>Total P&L</div>
                      <div style={{ ...typography.mono, fontSize: 30, fontWeight: 700, ...pnlStyle(total), lineHeight: 1.1 }}>
                        {total >= 0 ? "+" : ""}₹{Math.round(Math.abs(total)).toLocaleString("en-IN")}
                      </div>
                      {total !== 0 && (
                        <div style={{ marginTop: spacing.sm, display: "inline-block", padding: "2px 10px", borderRadius: 12,
                          fontSize: 11, fontWeight: 600, background: isProfit ? colors.successBg : colors.dangerBg,
                          color: isProfit ? colors.profit : colors.loss, border: `1px solid ${accent}30` }}>
                          {isProfit ? "▲ Profit" : "▼ Loss"} today
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })()}

          {positionsPartial && ( /* ACC2_D9 */
            <div style={{ padding: spacing.md, marginBottom: spacing.md, borderRadius: 8,
              background: colors.warningBg, border: `1px solid ${colors.warning}55`,
              color: colors.warning, fontSize: 12, fontWeight: 600 }}>
              ⚠ Account 2 (Angel One) data unavailable — totals below cover Zerodha only.
            </div>
          )}
          <Card elevated style={{ padding: spacing.lg }}>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>
              Open Positions
              {positions.open.length > 0 && (
                <span style={{ marginLeft: 8, fontSize: 10, padding: "1px 6px", borderRadius: 10, background: colors.warningBg, color: colors.warning }}>
                  {positions.open.length}
                </span>
              )}
            </div>
            {positionsLoading ? <CardSkeleton rows={3} /> : (
              <div style={{ maxHeight: 200, overflowY: "auto" }}>
                {positions.open.length === 0 ? <EmptyState icon="🔭" title="No open positions" description="" />
                  : positions.open.map((p, i) => <PositionRow key={i} symbol={p.tradingsymbol} qty={p.quantity} pnl={p.pnl} account={p.account} showAccount={multiAccount} />)}
              </div>
            )}
          </Card>

          <Card elevated style={{ padding: spacing.lg }}>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>
              Closed Positions
              {positions.closed.length > 0 && (
                <span style={{ marginLeft: 8, fontSize: 10, padding: "1px 6px", borderRadius: 10, background: colors.bg.tertiary, color: colors.text.muted }}>
                  {positions.closed.length}
                </span>
              )}
            </div>
            {positionsLoading ? <CardSkeleton rows={3} /> : (
              <div style={{ maxHeight: 200, overflowY: "auto" }}>
                {positions.closed.length === 0 ? <EmptyState icon="🔭" title="No closed positions" description="" />
                  : positions.closed.map((p, i) => <PositionRow key={i} symbol={p.tradingsymbol} qty={p.day_buy_quantity} pnl={p.pnl} account={p.account} showAccount={multiAccount} />)}
              </div>
            )}
          </Card>
        </div>
      </div>

      {/* Global, all-strategy debug tool — admin licenses only */}
      {isAdminUi && <DebugPanel />}
    </div>
  );
}