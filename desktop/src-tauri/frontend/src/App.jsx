import { HashRouter, Routes, Route, Link, useLocation } from "react-router-dom";
import { useEffect, useState, useCallback } from "react";

import Dashboard    from "./pages/Dashboard";
import Settings     from "./pages/Settings";
import ZerodhaLogin from "./pages/ZerodhaLogin";
import Analytics    from "./pages/Analytics";
import PaperTrades  from "./pages/PaperTrades";

import { ToastProvider, ToastAnimations } from "./components/ToastNotifications";
import LicenseBanner    from "./components/LicenseBanner";
import BackendBootGuard from "./components/BackendBootGuard";
import StatusBar        from "./components/StatusBar";

import { getStatus, getZerodhaStatus } from "./api";

/* ─────────────────────────────────────────────
   Design tokens
───────────────────────────────────────────── */

const colors = {
  primary: "#2563eb",
  success: "#10b981",
  warning: "#f59e0b",
  danger:  "#ef4444",
  bg: {
    primary:   "#020817",
    secondary: "#0f172a",
  },
  border: { light: "#334155" },
  text: {
    primary:   "#f8fafc",
    secondary: "#cbd5e1",
    muted:     "#64748b",
  },
};

/* ─────────────────────────────────────────────
   Market hours helper
   Market: 09:15 – 15:30 IST  (375 min total)
───────────────────────────────────────────── */

const MARKET_START_MIN = 9 * 60 + 15;   // 555
const MARKET_END_MIN   = 15 * 60 + 30;  // 930
const MARKET_DURATION  = MARKET_END_MIN - MARKET_START_MIN; // 375

function getMarketProgress() {
  const now  = new Date();
  const mins = now.getHours() * 60 + now.getMinutes() + now.getSeconds() / 60;
  if (mins < MARKET_START_MIN) return 0;
  if (mins >= MARKET_END_MIN)  return 100;
  return ((mins - MARKET_START_MIN) / MARKET_DURATION) * 100;
}

function isMarketOpen() {
  const now  = new Date();
  const mins = now.getHours() * 60 + now.getMinutes();
  // Weekdays only (0=Sun, 6=Sat)
  const dow = now.getDay();
  if (dow === 0 || dow === 6) return false;
  return mins >= MARKET_START_MIN && mins < MARKET_END_MIN;
}

/* ─────────────────────────────────────────────
   Navigation
───────────────────────────────────────────── */

function Navigation({ health }) {
  const location = useLocation();
  const [progress, setProgress] = useState(getMarketProgress);

  // Tick progress every 30s
  useEffect(() => {
    const t = setInterval(() => setProgress(getMarketProgress()), 30_000);
    return () => clearInterval(t);
  }, []);

  const navItems = [
    { path: "/",             label: "Dashboard",    icon: "📊" },
    { path: "/analytics",   label: "Analytics",    icon: "📈" },
    { path: "/paper-trades",label: "Paper Trades",  icon: "📋" },
    { path: "/settings",    label: "Settings",      icon: "⚙️" },
    { path: "/zerodha",     label: "Zerodha",       icon: "🔗" },
  ];

  // Dot color — reflects real state
  const inMarketHours = (() => {
    const d = new Date(); const dow = d.getDay();
    if (dow === 0 || dow === 6) return false;
    const m = d.getHours() * 60 + d.getMinutes();
    return m >= 555 && m < 930; // 09:15–15:30
  })();

  const dotColor = !health.backendUp
    ? colors.danger
    : !health.zerodhaConnected
    ? colors.warning
    : health.trading || health.engineRunning
    ? colors.success
    : colors.primary;

  const dotGlow = !health.backendUp
    ? "rgba(239,68,68,0.5)"
    : !health.zerodhaConnected
    ? "rgba(245,158,11,0.5)"
    : health.trading || health.engineRunning
    ? "rgba(16,185,129,0.5)"
    : "rgba(59,130,246,0.5)";

  const dotLabel = !health.backendUp
    ? "Offline"
    : !health.zerodhaConnected
    ? "No Broker"
    : health.trading
    ? "Trading"
    : !health.engineRunning
    ? "Engine Off"
    : inMarketHours
    ? "Engine On"
    : "Engine Idle";

  // Progress bar color
  const barColor = progress > 90
    ? colors.danger
    : progress > 75
    ? colors.warning
    : colors.success;

  return (
    <nav style={{
      background:   colors.bg.secondary,
      borderBottom: `1px solid ${colors.border.light}`,
      boxShadow:    "0 1px 3px rgba(0,0,0,0.3)",
      position:     "sticky",
      top:          0,
      zIndex:       100,
    }}>
      {/* Main bar */}
      <div style={{
        padding:        "0 24px",
        display:        "flex",
        alignItems:     "center",
        justifyContent: "space-between",
        height:         54,
      }}>
        {/* Brand */}
        <div style={{ fontSize: 17, fontWeight: 700, color: colors.text.primary, display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 22 }}>⚡</span>
          Scalp Terminal
        </div>

        {/* Nav links */}
        <div style={{ display: "flex", gap: 2 }}>
          {navItems.map((item) => {
            const isActive = location.pathname === item.path;
            return (
              <Link
                key={item.path}
                to={item.path}
                style={{
                  padding:        "7px 14px",
                  borderRadius:   6,
                  textDecoration: "none",
                  fontSize:       13,
                  fontWeight:     600,
                  display:        "flex",
                  alignItems:     "center",
                  gap:            5,
                  transition:     "all 0.2s ease",
                  background:     isActive ? colors.primary : "transparent",
                  color:          isActive ? colors.text.primary : colors.text.secondary,
                  border:         isActive ? "none" : "1px solid transparent",
                }}
                onMouseEnter={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.background = "rgba(255,255,255,0.05)";
                    e.currentTarget.style.color = colors.text.primary;
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.background = "transparent";
                    e.currentTarget.style.color = colors.text.secondary;
                  }
                }}
              >
                <span style={{ fontSize: 13 }}>{item.icon}</span>
                {item.label}
              </Link>
            );
          })}
        </div>

        {/* Live status dot */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: dotColor, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px" }}>
          <span style={{
            width:        7,
            height:       7,
            borderRadius: "50%",
            background:   dotColor,
            boxShadow:    `0 0 8px ${dotGlow}`,
            animation:    health.engineRunning && health.backendUp ? "navPulse 2s ease-in-out infinite" : "none",
            flexShrink:   0,
          }} />
          {dotLabel}
        </div>
      </div>

      {/* ── Market hours progress bar ───────── */}
      <div style={{ height: 2, background: colors.bg.primary, position: "relative" }}>
        <div style={{
          position:   "absolute",
          left:       0,
          top:        0,
          height:     "100%",
          width:      `${progress}%`,
          background: barColor,
          boxShadow:  `0 0 6px ${barColor}80`,
          transition: "width 1s linear, background 0.5s ease",
          borderRadius: "0 1px 1px 0",
        }} />
        {/* Tick marks at 25%, 50%, 75% */}
        {[25, 50, 75].map((pct) => (
          <div key={pct} style={{
            position:   "absolute",
            left:       `${pct}%`,
            top:        0,
            width:      1,
            height:     "100%",
            background: "rgba(255,255,255,0.08)",
          }} />
        ))}
      </div>

      <style>{`
        @keyframes navPulse {
          0%, 100% { opacity: 1; }
          50%       { opacity: 0.45; }
        }
      `}</style>
    </nav>
  );
}

/* ─────────────────────────────────────────────
   App
───────────────────────────────────────────── */

const DEFAULT_HEALTH = {
  backendUp:       false,
  engineRunning:   false,
  trading:         false,
  zerodhaConnected: false,
};

export default function App() {
  const [health, setHealth] = useState(DEFAULT_HEALTH);

  const pollHealth = useCallback(async () => {
    try {
      const [s, z] = await Promise.allSettled([getStatus(), getZerodhaStatus()]);
      const status  = s.status  === "fulfilled" ? s.value  : null;
      const zerodha = z.status  === "fulfilled" ? z.value  : null;
      setHealth({
        backendUp:        status?.backend  === "UP",
        engineRunning:    status?.engine   === "RUNNING",
        trading:          status?.trading  === true,
        zerodhaConnected: zerodha?.connected === true && zerodha?.session_expired !== true,
      });
    } catch { /* keep previous health */ }
  }, []);

  useEffect(() => {
    pollHealth();
    const t = setInterval(pollHealth, 5000);
    return () => clearInterval(t);
  }, [pollHealth]);

  return (
    <>
      <LicenseBanner />

      <HashRouter>
        <ToastProvider>
          <ToastAnimations />

          <BackendBootGuard>
            <Navigation health={health} />

            <Routes>
              <Route path="/"              element={<Dashboard />}    />
              <Route path="/analytics"     element={<Analytics />}    />
              <Route path="/paper-trades"  element={<PaperTrades />}  />
              <Route path="/settings"      element={<Settings />}     />
              <Route path="/zerodha"       element={<ZerodhaLogin />} />
            </Routes>
          </BackendBootGuard>

          <StatusBar health={health} />

          <style>{`
            @keyframes pulse {
              0%, 100% { opacity: 1; }
              50%       { opacity: 0.5; }
            }
            * { box-sizing: border-box; }
            body {
              margin: 0;
              padding-bottom: 30px; /* room for StatusBar */
              font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
              -webkit-font-smoothing: antialiased;
            }
          `}</style>
        </ToastProvider>
      </HashRouter>
    </>
  );
}