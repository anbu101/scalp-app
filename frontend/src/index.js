import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";
import { applyTheme, readStoredTheme } from "./theme";   // ── THEME_PHASE1_20260831 ──

// Apply the last-used theme BEFORE the first render so the window never
// flashes the wrong palette while BackendBootGuard waits for the backend.
// app_settings.json (via NotificationProvider) remains the source of truth
// and will re-apply on load if it differs.
applyTheme(readStoredTheme());

const container = document.getElementById("root");
const root = createRoot(container);
root.render(<App />);
