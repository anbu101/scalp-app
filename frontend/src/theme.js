/**
 * theme.js — THEME_PHASE1_20260831
 *
 * Runtime theme switch. The palette for each theme lives in index.css under
 * :root[data-theme="…"]; tokens.js exposes var() references to it. This
 * module just decides WHICH palette is active.
 *
 *   applyTheme(name)     → sets <html data-theme> + mirrors to localStorage
 *   readStoredTheme()    → localStorage → "dark"   (first-paint, pre-backend)
 *   useTheme()           → [theme, setTheme] hook (reads the DOM attribute)
 *
 * Source of truth is app_settings.json via /api/app/settings (D4);
 * localStorage is only a first-paint mirror so the window never flashes the
 * wrong palette while BackendBootGuard waits for the backend.
 */

import { useCallback, useEffect, useState } from "react";

export const THEMES = [
  { id: "dark",     name: "Dark",     desc: "Slate — the current look" },
  { id: "light",    name: "Light",    desc: "White panels, dark text" },
  { id: "terminal", name: "Terminal", desc: "True black, high contrast" },
];

export const THEME_DEFAULT = "dark";
export const THEME_IDS = THEMES.map((t) => t.id);

const LS_KEY = "scalp.theme";

export function normalizeTheme(name) {
  return THEME_IDS.includes(name) ? name : THEME_DEFAULT;
}

export function readStoredTheme() {
  try {
    return normalizeTheme(window.localStorage.getItem(LS_KEY));
  } catch {
    return THEME_DEFAULT;
  }
}

export function currentTheme() {
  try {
    return normalizeTheme(document.documentElement.getAttribute("data-theme"));
  } catch {
    return THEME_DEFAULT;
  }
}

/** Idempotent — safe to call from a 30 s settings poll. */
export function applyTheme(name) {
  const t = normalizeTheme(name);
  try {
    const el = document.documentElement;
    if (el.getAttribute("data-theme") !== t) {
      el.setAttribute("data-theme", t);
      el.dispatchEvent(new CustomEvent("scalp:theme", { detail: t }));
    }
  } catch { /* non-DOM env */ }
  try { window.localStorage.setItem(LS_KEY, t); } catch { /* private mode */ }
  return t;
}

/** Local view of the active theme; persistence goes through saveSettings. */
export function useTheme() {
  const [theme, setThemeState] = useState(currentTheme);
  useEffect(() => {
    const h = (e) => setThemeState(e.detail);
    document.documentElement.addEventListener("scalp:theme", h);
    return () => document.documentElement.removeEventListener("scalp:theme", h);
  }, []);
  const setTheme = useCallback((n) => setThemeState(applyTheme(n)), []);
  return [theme, setTheme];
}
