// frontend/src/components/calm/calm.js
//
// ── DASH_CALM_20261117 ── the redesigned ("Modern", option B "Calm") Dashboard's
// vocabulary: one big number, a quiet chart, a card per strategy. Every colour is a
// theme variable, so all six themes work; type is the app's UI font with tabular
// numbers. Kept separate from fleet/dash.js, which the Classic layout still uses.

export const SP = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32, xxxl: 40 };

export const C = {
  page:    "var(--c-bg-page)",
  surface: "var(--c-bg-secondary)",   // cards
  inset:   "var(--c-bg-primary)",     // a panel inside a card (expanded meter)
  line:    "var(--c-border-light)",
  ink:     "var(--c-text-primary)",
  sub:     "var(--c-text-secondary)",
  muted:   "var(--c-text-muted)",
  up:      "var(--c-profit)",
  down:    "var(--c-loss)",
  warn:    "var(--c-warning)",
  accent:  "var(--c-primary)",
};

const NUMS = { fontVariantNumeric: "tabular-nums" };
export const T = {
  meta:   { fontSize: 12, lineHeight: "16px", fontWeight: 400 },
  body:   { fontSize: 14, lineHeight: "20px", fontWeight: 400 },
  lead:   { fontSize: 16, lineHeight: "24px", fontWeight: 400 },
  title:  { fontSize: 15, lineHeight: "22px", fontWeight: 600 },
  h2:     { fontSize: 20, lineHeight: "28px", fontWeight: 600 },
  card:   { fontSize: 26, lineHeight: "32px", fontWeight: 700, letterSpacing: "-0.02em", ...NUMS },
  second: { fontSize: 30, lineHeight: "36px", fontWeight: 600, letterSpacing: "-0.02em", ...NUMS },
  hero:   { fontSize: 64, lineHeight: "68px", fontWeight: 700, letterSpacing: "-0.035em", ...NUMS },
  heroSm: { fontSize: 44, lineHeight: "48px", fontWeight: 700, letterSpacing: "-0.03em", ...NUMS },
  num:    NUMS,
};

export const RADIUS = { card: 16, inner: 12, pill: 999 };
export const tone = (v) => (v > 0 ? C.up : v < 0 ? C.down : C.ink);

/* weekday 09:00–15:35 IST: meters refresh fast, else slowly */
export const inSession = (nowMin) => {
  const d = new Date().getDay();
  return d > 0 && d < 6 && nowMin >= 540 && nowMin <= 935;
};
