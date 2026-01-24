import { HashRouter, Routes, Route, Link, useLocation } from "react-router-dom";

import Dashboard from "./pages/Dashboard";
import Settings from "./pages/Settings";
import ZerodhaLogin from "./pages/ZerodhaLogin";
import Analytics from "./pages/Analytics";
import PaperTrades from "./pages/PaperTrades";

import { ToastProvider, ToastAnimations } from "./components/ToastNotifications";
import LicenseBanner from "./components/LicenseBanner";

/* -------------------------
   Design Tokens
-------------------------- */

const colors = {
  primary: "#2563eb",
  bg: {
    primary: "#020817",
    secondary: "#0f172a",
  },
  border: {
    light: "#334155",
  },
  text: {
    primary: "#f8fafc",
    secondary: "#cbd5e1",
    muted: "#64748b",
  },
};

/* -------------------------
   Navigation Component
-------------------------- */

function Navigation() {
  const location = useLocation();

  const navItems = [
    { path: "/", label: "Dashboard", icon: "📊" },
    { path: "/analytics", label: "Analytics", icon: "📈" },
    { path: "/paper-trades", label: "Paper Trades", icon: "📝" },
    { path: "/settings", label: "Settings", icon: "⚙️" },
    { path: "/zerodha", label: "Zerodha", icon: "🔗" },
  ];

  return (
    <nav
      style={{
        background: colors.bg.secondary,
        borderBottom: `1px solid ${colors.border.light}`,
        boxShadow: "0 1px 3px rgba(0, 0, 0, 0.3)",
        position: "sticky",
        top: 0,
        zIndex: 100,
      }}
    >
      <div
        style={{
          padding: "0 24px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          height: 56,
        }}
      >
        {/* Logo / Brand */}
        <div
          style={{
            fontSize: 18,
            fontWeight: 700,
            color: colors.text.primary,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <span style={{ fontSize: 24 }}>⚡</span>
          Scalp Terminal
        </div>

        {/* Navigation Links */}
        <div style={{ display: "flex", gap: 4 }}>
          {navItems.map((item) => {
            const isActive = location.pathname === item.path;

            return (
              <Link
                key={item.path}
                to={item.path}
                style={{
                  padding: "8px 16px",
                  borderRadius: 6,
                  textDecoration: "none",
                  fontSize: 13,
                  fontWeight: 600,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  transition: "all 0.2s ease",
                  background: isActive ? colors.primary : "transparent",
                  color: isActive
                    ? colors.text.primary
                    : colors.text.secondary,
                  border: isActive ? "none" : "1px solid transparent",
                }}
                onMouseEnter={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.background =
                      "rgba(255, 255, 255, 0.05)";
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
                <span style={{ fontSize: 14 }}>{item.icon}</span>
                {item.label}
              </Link>
            );
          })}
        </div>

        {/* Status Indicator */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: 11,
            color: colors.text.muted,
            fontWeight: 500,
            textTransform: "uppercase",
            letterSpacing: "0.5px",
          }}
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: "#10b981",
              boxShadow: "0 0 8px rgba(16, 185, 129, 0.5)",
              animation: "pulse 2s ease-in-out infinite",
            }}
          />
          Live
        </div>
      </div>
    </nav>
  );
}

/* -------------------------
   App (SINGLE ROOT)
-------------------------- */

export default function App() {
  return (
    <>
      {/* 🔒 License banner must be OUTSIDE router */}
      <LicenseBanner />

      <HashRouter>
        <ToastProvider>
          <ToastAnimations />
          <Navigation />

          {/* Pages */}
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/paper-trades" element={<PaperTrades />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/zerodha" element={<ZerodhaLogin />} />
          </Routes>

          {/* Global Styles */}
          <style>{`
            @keyframes pulse {
              0%, 100% { opacity: 1; }
              50% { opacity: 0.5; }
            }

            * {
              box-sizing: border-box;
            }

            body {
              margin: 0;
              font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
              -webkit-font-smoothing: antialiased;
              -moz-osx-font-smoothing: grayscale;
            }
          `}</style>
        </ToastProvider>
      </HashRouter>
    </>
  );
}
