import { BrowserRouter, Routes, Route, Link } from "react-router-dom";

import Dashboard from "./pages/Dashboard";
import Settings from "./pages/Settings";
import ZerodhaLogin from "./pages/ZerodhaLogin";

export default function App() {
  return (
    <BrowserRouter>
      {/* Top Nav */}
      <div
        style={{
          padding: "12px 20px",
          background: "#0b1220",
          borderBottom: "1px solid #1e293b",
          display: "flex",
          gap: 20,
        }}
      >
        <Link style={linkStyle} to="/">Dashboard</Link>
        <Link style={linkStyle} to="/settings">Settings</Link>
        <Link style={linkStyle} to="/zerodha">Zerodha</Link>
      </div>

      {/* Pages */}
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/zerodha" element={<ZerodhaLogin />} />
      </Routes>
    </BrowserRouter>
  );
}

const linkStyle = {
  color: "#e5e7eb",
  textDecoration: "none",
  fontWeight: 500,
};
