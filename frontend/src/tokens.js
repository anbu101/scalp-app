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
// ── THEME_PHASE1_20260831 ── every value is a CSS custom-property reference. The real
// hex lives in index.css under :root[data-theme="…"]; see theme.js. Keep this
// object's SHAPE stable — 25+ files read it. Never concat an alpha suffix
// onto one of these (e.g. colors.primary + "60") — use alpha() below.
export const colors = {
  // Semantic  — profit / loss
  profit:    "var(--c-profit)",
  profitBg:  "var(--c-profit-bg)",
  loss:      "var(--c-loss)",
  lossBg:    "var(--c-loss-bg)",
  neutral:   "var(--c-neutral)",

  // Brand
  primary:      "var(--c-primary)",
  primaryBg:    "var(--c-primary-bg)",
  primaryHover: "var(--c-primary-hover)",

  // Semantic states
  success:   "var(--c-success)",
  successBg: "var(--c-success-bg)",
  warning:   "var(--c-warning)",
  warningBg: "var(--c-warning-bg)",
  danger:    "var(--c-danger)",
  dangerBg:  "var(--c-danger-bg)",

  // Backgrounds
  bg: {
    primary:   "var(--c-bg-primary)",    // page background
    secondary: "var(--c-bg-secondary)",  // cards, panels
    tertiary:  "var(--c-bg-tertiary)",   // elevated cards, table odd rows
    elevated:  "var(--c-bg-elevated)",   // alias used by some components
    input:     "var(--c-bg-input)",      // form inputs
  },

  // Borders
  border: {
    light:  "var(--c-border-light)",   // visible borders
    medium: "var(--c-border-medium)",  // stronger dividers
    dark:   "var(--c-border-dark)",    // subtle separators
  },

  // Text
  text: {
    primary:   "var(--c-text-primary)",
    secondary: "var(--c-text-secondary)",
    tertiary:  "var(--c-text-tertiary)",
    muted:     "var(--c-text-muted)",
  },
};

/**
 * alpha(token, pct) — translucent version of ANY colour token (or a plain
 * hex). Replaces the old `colors.primary + "60"` string trick, which can't
 * work on a var() reference. pct is 0–100.
 *   alpha(colors.primary, 38)  ≈ old colors.primary + "60"
 */
export const alpha = (token, pct) =>
  `color-mix(in srgb, ${token} ${pct}%, transparent)`;

/* ─────────────────────────────────────────────
   Convenience helpers
───────────────────────────────────────────── */

/** Returns a colour based on sign of a P&L value */
export const pnlColor = (v) =>
  v > 0 ? colors.profit : v < 0 ? colors.loss : colors.neutral;

/** Inline style object for a P&L number */
export const pnlStyle = (v) => ({ color: pnlColor(v), fontWeight: 600 });