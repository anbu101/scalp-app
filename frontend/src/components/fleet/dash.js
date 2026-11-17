// frontend/src/components/fleet/dash.js
//
// ── DASH_SYSTEM_20261117 ── the Dashboard's ONLY design vocabulary. Every piece of
// the Modern Dashboard shell (header, book summary, chart, Traded-today table,
// today strip) uses these and nothing else — no other sizes, colours or gaps.
//
//   SPACE  4-pt scale, six steps        4 · 8 · 12 · 16 · 24 · 32
//   TYPE   five steps, two weights      11 meta · 13 body · 15 title · 20 figure · 32 hero
//                                       400 regular · 600 emphasis; numbers in tabular mono
//   COLOUR five tokens, all theme vars  ink · muted · line · up · down
//          (page background is the app theme's; no fills, no shadows, no gradients)
//
// Rules the components follow:
//   * colour carries P&L ONLY on the one headline figure of each thing (a book's
//     MTM, a row's MTM, a chart value) — supporting figures stay ink; the sign says it
//   * one border style (1 px line, radius 8), one row height (40), one card padding (24)
//   * a badge is text, not a box — except LIVE, the one state that must shout

import { typography } from "../../tokens";

export const SPACE = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 };

export const COLOR = {
  ink:   "var(--c-text-primary)",
  muted: "var(--c-text-muted)",
  line:  "var(--c-border-light)",
  up:    "var(--c-profit)",
  down:  "var(--c-loss)",
};

const MONO = typography.mono.fontFamily;
export const TYPE = {
  meta:   { fontSize: 11, lineHeight: "16px", fontWeight: 400 },
  body:   { fontSize: 13, lineHeight: "20px", fontWeight: 400 },
  title:  { fontSize: 15, lineHeight: "22px", fontWeight: 600 },
  figure: { fontSize: 20, lineHeight: "28px", fontWeight: 600, fontFamily: MONO, fontVariantNumeric: "tabular-nums" },
  hero:   { fontSize: 32, lineHeight: "40px", fontWeight: 600, fontFamily: MONO, fontVariantNumeric: "tabular-nums", letterSpacing: "-0.02em" },
};
export const NUM = { fontFamily: MONO, fontVariantNumeric: "tabular-nums" };   // body-size numbers
export const CAPS = { ...TYPE.meta, color: COLOR.muted, textTransform: "uppercase", letterSpacing: "0.06em" };

export const ROW_H = 40;
export const CARD = { border: `1px solid ${COLOR.line}`, borderRadius: 8, padding: SPACE.xl, minWidth: 0 };
export const CARD_COMPACT = { ...CARD, padding: SPACE.lg };

export const tone = (v) => (v > 0 ? COLOR.up : v < 0 ? COLOR.down : COLOR.ink);

/* The LIVE tag — the only boxed label on the page. */
export const liveTag = {
  ...TYPE.meta, fontWeight: 600, color: COLOR.ink, border: `1px solid ${COLOR.ink}`,
  borderRadius: 4, padding: `0 ${SPACE.xs}px`, letterSpacing: "0.06em", whiteSpace: "nowrap",
};
