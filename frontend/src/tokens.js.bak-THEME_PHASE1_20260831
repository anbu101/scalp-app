/**
 * tokens.js — Single source of truth for all design tokens.
 * Intended path: src/tokens.js
 *
 * Import paths:
 *   src/pages/*                  → import { colors, spacing, typography } from "../tokens";
 *   src/components/*             → import { colors, spacing, typography } from "../tokens";
 *   src/strategies/scalp/*       → import { colors, spacing, typography } from "../../tokens";
 *   src/App.jsx                  → import { colors, spacing, typography } from "./tokens";
 *
 * Previously two competing palettes existed across files:
 *   • "slate" family  (#020817 bg) — PaperTrades, Analytics, Settings, App
 *   • "gray"  family  (#0a0f1e bg) — Dashboard, ScalpPanel
 * This file canonicalises on the slate family (Tailwind slate-950/900/800).
 */

/* ─────────────────────────────────────────────
   Spacing  (px values used in style={{ padding: spacing.md }})
───────────────────────────────────────────── */
export const spacing = {
  xs:  4,
  sm:  8,
  md:  12,
  lg:  16,
  xl:  20,
  xxl: 24,
};

/* ─────────────────────────────────────────────
   Typography scale
───────────────────────────────────────────── */
export const typography = {
  displayLarge:  { fontSize: 28, fontWeight: 700, lineHeight: 1.2 },
  displaySmall:  { fontSize: 24, fontWeight: 600, lineHeight: 1.3 },
  headingLarge:  { fontSize: 18, fontWeight: 600, lineHeight: 1.4 },
  headingMedium: { fontSize: 16, fontWeight: 600, lineHeight: 1.4 },
  headingSmall:  { fontSize: 14, fontWeight: 600, lineHeight: 1.4 },
  bodyLarge:     { fontSize: 14, fontWeight: 400, lineHeight: 1.5 },
  bodyMedium:    { fontSize: 13, fontWeight: 400, lineHeight: 1.5 },
  bodySmall:     { fontSize: 12, fontWeight: 400, lineHeight: 1.4 },
  label:         { fontSize: 11, fontWeight: 500, lineHeight: 1.3, letterSpacing: "0.5px", textTransform: "uppercase" },
  mono:          { fontFamily: "'JetBrains Mono', 'Fira Code', monospace", fontVariantNumeric: "tabular-nums" },
};

/* ─────────────────────────────────────────────
   Colour palette  (Tailwind slate canonical)
───────────────────────────────────────────── */
export const colors = {
  // Semantic  — profit / loss
  profit:    "#10b981",
  profitBg:  "rgba(16, 185, 129, 0.10)",
  loss:      "#ef4444",
  lossBg:    "rgba(239, 68, 68, 0.10)",
  neutral:   "#6b7280",

  // Brand
  primary:      "#3b82f6",
  primaryBg:    "rgba(59, 130, 246, 0.12)",
  primaryHover: "#2563eb",

  // Semantic states
  success:   "#10b981",
  successBg: "rgba(16, 185, 129, 0.12)",
  warning:   "#f59e0b",
  warningBg: "rgba(245, 158, 11, 0.12)",
  danger:    "#ef4444",
  dangerBg:  "rgba(239, 68, 68, 0.12)",

  // Backgrounds  (slate-950 → slate-900 → slate-800 → slate-700)
  bg: {
    primary:   "#020817",   // page background
    secondary: "#0f172a",   // cards, panels
    tertiary:  "#1e293b",   // elevated cards, table odd rows
    elevated:  "#1e293b",   // alias used by some components
    input:     "#060d1a",   // form inputs
  },

  // Borders
  border: {
    light:  "#334155",   // visible borders
    medium: "#475569",   // stronger dividers
    dark:   "#1e293b",   // subtle separators
  },

  // Text
  text: {
    primary:   "#f8fafc",
    secondary: "#cbd5e1",
    tertiary:  "#94a3b8",
    muted:     "#64748b",
  },
};

/* ─────────────────────────────────────────────
   Convenience helpers
───────────────────────────────────────────── */

/** Returns a colour based on sign of a P&L value */
export const pnlColor = (v) =>
  v > 0 ? colors.profit : v < 0 ? colors.loss : colors.neutral;

/** Inline style object for a P&L number */
export const pnlStyle = (v) => ({ color: pnlColor(v), fontWeight: 600 });