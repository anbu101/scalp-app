import { HashRouter, Routes, Route, Link, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useState, useCallback } from "react";

import Dashboard    from "./pages/Dashboard";
import Settings     from "./pages/Settings";
import Connections from "./pages/Connections";
import Analytics    from "./pages/Analytics";
import PaperTrades  from "./pages/PaperTrades";
import Backtest from "./pages/Backtest";

import { ToastProvider, ToastAnimations } from "./components/ToastNotifications";
import LicenseBanner    from "./components/LicenseBanner";
import UpdateBanner     from "./components/UpdateBanner";
import LicenseGate      from "./components/LicenseGate";
import BackendBootGuard from "./components/BackendBootGuard";
import StatusBar        from "./components/StatusBar";
import NotificationCenter from "./components/NotificationCenter";
import { useIsMobile }  from "./hooks/useIsMobile";

import { MarketDataProvider, useMarketData } from "./context/MarketDataContext";
import { NotificationProvider } from "./context/NotificationProvider";
import { getStatus, getZerodhaStatus, getAccountBalance } from "./api";
import { colors } from "./tokens";
// ── CAS_2026 ── single source of truth for session boundaries
import { getMarketProgress, isMarketOpen } from "./marketSession";
import { useAppSettings } from "./context/NotificationProvider";




/* ─────────────────────────────────────────────
   Market hours helper  (09:15 – 15:40 IST)
   ── CAS_2026 ── boundaries now live in ONE place:
   src/marketSession.js. NFO closes 15:40 from
   2026-08-03; do not re-inline 930 here.
───────────────────────────────────────────── */

/* ─────────────────────────────────────────────
   Compact P&L pill — shown in nav, reads context
───────────────────────────────────────────── */
function NavPnLPill() {
  const { positions } = useMarketData();
  const total = positions?.totals?.total ?? 0;
  const up = total >= 0;
  const color = total === 0 ? colors.text.muted : up ? colors.success : colors.danger;
  const bg    = total === 0 ? "rgba(100,116,139,0.10)" : up ? "rgba(16,185,129,0.10)" : "rgba(239,68,68,0.10)";
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 7, padding: "5px 12px", borderRadius: 7,
      background: bg, border: `1px solid ${color}33`,
    }}>
      <span style={{ fontSize: 9, color: colors.text.muted, textTransform: "uppercase", letterSpacing: "0.5px", fontWeight: 700 }}>
        Today P&L
      </span>
      <span style={{ fontSize: 15, fontWeight: 800, fontFamily: "'JetBrains Mono','Fira Code',monospace", color }}>
        {total >= 0 ? "+" : "−"}₹{Math.round(Math.abs(total)).toLocaleString("en-IN")}
      </span>
    </div>
  );
}

/* ─────────────────────────────────────────────
   Bottom Tab Bar — mobile only
───────────────────────────────────────────── */
function BottomTabBar({ health }) {
  const location = useLocation();
  const dotColor = !health.backendUp ? colors.danger
    : !health.zerodhaConnected ? colors.warning
    : health.trading || health.engineRunning ? colors.success : colors.primary;
  const tabs = [
    { path: "/",             label: "Dashboard", icon: "📊" },
    { path: "/analytics",    label: "Analytics", icon: "📈" },
    { path: "/paper-trades", label: "Paper",     icon: "📋" },
    { path: "/settings",     label: "Settings",  icon: "⚙️" },
    { path: "/connections",  label: "Connect",   icon: "🔗" },
  ];
  return (
    <nav style={{ position: "fixed", bottom: 0, left: 0, right: 0, zIndex: 200,
      background: colors.bg.secondary, borderTop: `1px solid ${colors.border.light}`,
      display: "flex", height: 58, paddingBottom: "env(safe-area-inset-bottom)" }}>
      {tabs.map((tab) => {
        const isActive = location.pathname === tab.path;
        return (
          <Link key={tab.path} to={tab.path} style={{
            flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
            gap: 2, textDecoration: "none", color: isActive ? dotColor : colors.text.muted,
            background: isActive ? `${dotColor}10` : "transparent",
            borderTop: isActive ? `2px solid ${dotColor}` : "2px solid transparent",
            transition: "all 0.2s ease", padding: "6px 0" }}>
            <span style={{ fontSize: 18, lineHeight: 1 }}>{tab.icon}</span>
            <span style={{ fontSize: 10, fontWeight: isActive ? 700 : 400, letterSpacing: "0.2px" }}>{tab.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

/* ─────────────────────────────────────────────
   Mobile P&L strip — above bottom tab bar
───────────────────────────────────────────── */
function MobilePnLStrip() {
  const { positions } = useMarketData();
  const t = positions?.totals ?? { realised: 0, unrealised: 0, total: 0 };
  const up = t.total >= 0;
  const color = t.total === 0 ? colors.text.muted : up ? colors.success : colors.danger;
  return (
    <div style={{ position: "fixed", bottom: 58, left: 0, right: 0, zIndex: 199,
      background: colors.bg.secondary, borderTop: `1px solid ${colors.border.dark}`,
      display: "flex", alignItems: "center", gap: 14, padding: "5px 14px",
      paddingBottom: "calc(5px + env(safe-area-inset-bottom))", fontSize: 11 }}>
      <span style={{ fontSize: 9, color: colors.text.muted, textTransform: "uppercase", letterSpacing: "0.5px", fontWeight: 700 }}>Today</span>
      <span style={{ fontFamily: "monospace", color: colors.text.secondary }}>R <span style={{ color: t.realised >= 0 ? colors.success : colors.danger, fontWeight: 700 }}>{t.realised >= 0 ? "+" : "−"}₹{Math.round(Math.abs(t.realised)).toLocaleString("en-IN")}</span></span>
      <span style={{ fontFamily: "monospace", color: colors.text.secondary }}>U <span style={{ color: t.unrealised >= 0 ? colors.success : colors.danger, fontWeight: 700 }}>{t.unrealised >= 0 ? "+" : "−"}₹{Math.round(Math.abs(t.unrealised)).toLocaleString("en-IN")}</span></span>
      <span style={{ flex: 1 }} />
      <span style={{ fontFamily: "monospace", fontWeight: 800, fontSize: 13, color }}>
        {t.total >= 0 ? "+" : "−"}₹{Math.round(Math.abs(t.total)).toLocaleString("en-IN")}
      </span>
    </div>
  );
}

/* ─────────────────────────────────────────────
   Navigation — desktop top bar
───────────────────────────────────────────── */
function Navigation({ health }) {
  const location  = useLocation();
  const isMobile  = useIsMobile();
  const [progress, setProgress] = useState(getMarketProgress);
  const { settings } = useAppSettings();
  const showBalance = settings?.show_account_balance === true;
  const balance = health.accountBalance;
  const hasBalance = showBalance && typeof balance === "number";
  const balanceText = hasBalance
    ? `₹${balance.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`
    : null;
    
  useEffect(() => { const t = setInterval(() => setProgress(getMarketProgress()), 30000); return () => clearInterval(t); }, []);
  if (isMobile) return null;

const navItems = [
    { path: "/",             label: "Dashboard",    icon: "📊", shortcut: "D" },
    { path: "/analytics",    label: "Analytics",    icon: "📈", shortcut: "A" },
    { path: "/paper-trades", label: "Paper Trades", icon: "📋", shortcut: "P" },
    { path: "/backtest",     label: "Backtest",     icon: "🧪", shortcut: "B" },
    { path: "/settings",     label: "Settings",     icon: "⚙️", shortcut: "S" },
    { path: "/connections",  label: "Connections",  icon: "🔗", shortcut: "C" },
  ];

  const inMarketHours = isMarketOpen();   // ── CAS_2026 ── was inline 555..930

  const dotColor = !health.backendUp ? colors.danger
    : !health.zerodhaConnected ? colors.warning
    : health.trading || health.engineRunning ? colors.success : colors.primary;
  const dotGlow = !health.backendUp ? "rgba(239,68,68,0.5)"
    : !health.zerodhaConnected ? "rgba(245,158,11,0.5)"
    : health.trading || health.engineRunning ? "rgba(16,185,129,0.5)" : "rgba(59,130,246,0.5)";
  const dotLabel = !health.backendUp ? "Offline"
    : !health.zerodhaConnected ? "No Broker"
    : health.trading ? "Trading"
    : !health.engineRunning ? "Engine Off"
    : inMarketHours ? "Engine On" : "Engine Idle";

  const barColor = progress > 90 ? colors.danger : progress > 75 ? colors.warning : colors.success;

  return (
    <nav style={{ background: colors.bg.secondary, borderBottom: `1px solid ${colors.border.light}`,
      boxShadow: "0 1px 3px rgba(0,0,0,0.3)", position: "sticky", top: 0, zIndex: 100 }}>
      <div style={{ padding: "0 24px", display: "flex", alignItems: "center", height: 54, gap: 16 }}>
        <div style={{ fontSize: 17, fontWeight: 700, color: colors.text.primary, display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          <span style={{ fontSize: 22 }}>⚡</span> Scalp Terminal
        </div>

        <div style={{ flex: 1 }} />

        <div style={{ display: "flex", gap: 2, flexShrink: 0 }}>
          {navItems.map((item) => {
            const isActive = location.pathname === item.path;
            return (
              <Link key={item.path} to={item.path} style={{
                padding: "7px 14px", borderRadius: 6, textDecoration: "none", fontSize: 13, fontWeight: 600,
                display: "flex", alignItems: "center", gap: 5, transition: "all 0.2s ease",
                background: isActive ? colors.primary : "transparent",
                color: isActive ? colors.text.primary : colors.text.secondary,
                border: isActive ? "none" : "1px solid transparent" }}
                onMouseEnter={(e) => { if (!isActive) { e.currentTarget.style.background = "rgba(255,255,255,0.05)"; e.currentTarget.style.color = colors.text.primary; } }}
                onMouseLeave={(e) => { if (!isActive) { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = colors.text.secondary; } }}>
                <span style={{ fontSize: 13 }}>{item.icon}</span>
                {item.label}
                {!isActive && (
                  <span style={{ fontSize: 9, fontWeight: 700, color: colors.text.muted,
                    background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)",
                    borderRadius: 3, padding: "1px 4px", letterSpacing: "0.3px", lineHeight: 1.4, marginLeft: 2 }}>
                    {item.shortcut}
                  </span>
                )}
              </Link>
            );
          })}
        </div>

        {/* Right cluster: P&L pill + notification bell + status dot */}
        <div style={{ flex: 1 }} />
        <div style={{ display: "flex", alignItems: "center", gap: 14, flexShrink: 0 }}>
          <NavPnLPill />
          <NotificationCenter />
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: hasBalance ? colors.text.primary : dotColor, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px" }}>
            <span style={{ width: 7, height: 7, borderRadius: "50%", background: dotColor, boxShadow: `0 0 8px ${dotGlow}`,
              animation: health.engineRunning && health.backendUp ? "navPulse 2s ease-in-out infinite" : "none", flexShrink: 0 }} />
            {balanceText ?? dotLabel}
          </div>
        </div>
      </div>

      <div style={{ height: 2, background: colors.bg.primary, position: "relative" }}>
        <div style={{ position: "absolute", left: 0, top: 0, height: "100%", width: `${progress}%`,
          background: barColor, boxShadow: `0 0 6px ${barColor}80`, transition: "width 1s linear, background 0.5s ease", borderRadius: "0 1px 1px 0" }} />
        {[25, 50, 75].map((pct) => (
          <div key={pct} style={{ position: "absolute", left: `${pct}%`, top: 0, width: 1, height: "100%", background: "rgba(255,255,255,0.08)" }} />
        ))}
      </div>

      <style>{`@keyframes navPulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }`}</style>
    </nav>
  );
}

/* ─────────────────────────────────────────────
   Mobile notification bell — floating, top-right
   (the desktop bell lives in the nav; mobile has no desktop nav,
    so surface the same bell as a small fixed control)
───────────────────────────────────────────── */
function MobileNotificationBell() {
  return (
    <div style={{
      position: "fixed",
      top: "calc(8px + env(safe-area-inset-top))",
      right: 10,
      zIndex: 201,
    }}>
      <NotificationCenter />
    </div>
  );
}

/* ─────────────────────────────────────────────
   Keyboard Shortcuts
───────────────────────────────────────────── */
const SHORTCUT_MAP = { d: "/", a: "/analytics", p: "/paper-trades", s: "/settings", c: "/connections", b: "/backtest" };

function KeyboardShortcuts() {
  const navigate = useNavigate();
  useEffect(() => {
    function handleKey(e) {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const tag = document.activeElement?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || tag === "button" || document.activeElement?.isContentEditable) return;
      const path = SHORTCUT_MAP[e.key.toLowerCase()];
      if (path) { e.preventDefault(); navigate(path); }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [navigate]);
  return null;
}

/* ─────────────────────────────────────────────
   App
───────────────────────────────────────────── */
const DEFAULT_HEALTH = { backendUp: false, engineRunning: false, trading: false, zerodhaConnected: false, accountBalance: null };

export default function App() {
  const [health, setHealth] = useState(DEFAULT_HEALTH);
  const isMobile = useIsMobile();

  const pollHealth = useCallback(async () => {
      try {
        const [s, z, b] = await Promise.allSettled([
          getStatus(),
          getZerodhaStatus(),
          getAccountBalance(),
        ]);
        const status  = s.status === "fulfilled" ? s.value : null;
        const zerodha = z.status === "fulfilled" ? z.value : null;
        const balance = b.status === "fulfilled" ? b.value : null;
        setHealth({
          backendUp: status?.backend === "UP",
          engineRunning: status?.engine === "RUNNING",
          trading: status?.trading === true,
          zerodhaConnected: zerodha?.connected === true && zerodha?.session_expired !== true,
          accountBalance: (balance && typeof balance.net === "number") ? balance.net : null,
        });
      } catch {}
    }, []);

  useEffect(() => { pollHealth(); const t = setInterval(pollHealth, 5000); return () => clearInterval(t); }, [pollHealth]);

  return (
    <>
      <LicenseBanner />
      <UpdateBanner />
      <HashRouter>
        <ToastProvider>
          <ToastAnimations />
          <MarketDataProvider>
            <NotificationProvider health={health}>
            <BackendBootGuard>
              <LicenseGate>
                <KeyboardShortcuts />
                <Navigation health={health} />
                {isMobile && <MobileNotificationBell />}
                <Routes>
                  <Route path="/"             element={<Dashboard />}   />
                  <Route path="/analytics"    element={<Analytics />}   />
                  <Route path="/paper-trades" element={<PaperTrades />} />
                  <Route path="/settings"     element={<Settings />}    />
                  <Route path="/backtest" element={<Backtest />} />
                  <Route path="/connections"  element={<Connections />} />
                </Routes>
                {isMobile && <MobilePnLStrip />}
                {isMobile && <BottomTabBar health={health} />}
              </LicenseGate>
            </BackendBootGuard>
            {!isMobile && <StatusBar health={health} />}
            </NotificationProvider>
          </MarketDataProvider>

          <style>{`
            @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
            * { box-sizing: border-box; }
            body {
              margin: 0;
              padding-bottom: ${isMobile ? "104px" : "30px"};
              font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
              -webkit-font-smoothing: antialiased;
            }
          `}</style>
        </ToastProvider>
      </HashRouter>
    </>
  );
}