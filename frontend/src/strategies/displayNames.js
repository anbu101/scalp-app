/**
 * STRATEGY DISPLAY NAMES — UI_MASK
 *
 * Intended path: src/strategies/displayNames.js
 *
 * SINGLE SOURCE OF TRUTH for strategy naming at both ui levels. Every
 * component that renders a strategy name (host rail, settings rail,
 * analytics, connections, notifications) resolves it through stratName()
 * instead of a local META map, so admin sees real names and standard
 * (non-admin) licenses see CODENAMES only.
 *
 * Codename decode rule (admin eyes only — do not document in-app):
 *   The codename embeds the strategy id's letters, in order, starting
 *   with the same letter. For the three SCALP variants the FINAL letter
 *   encodes the version by alphabet position: a=1, c=3, e=5.
 *
 *     SCALP_V1  → Scala      (S-C…, ends 'a' = V1)
 *     SCALP_V3  → Scenic     (S-C…, ends 'c' = V3)
 *     SCALP_V5  → Scribe     (S-C…, ends 'e' = V5)
 *     IC_V1     → Indica     (I…C…, ends 'a' = V1, SCALP rule)
 *     IC_V2     → Icarus     (I-C…, the incumbent name stays with the
 *                             incumbent BEHAVIOUR — every "Icarus" trade
 *                             in history was V2 semantics)
 *     TSG_V1    → Tigris     (T…S, G in the tail)
 *     BB_V1     → Bobbin     (B-o-B…, ends short = V1)
 *     BB_V2     → Baobab     (B…B, the "other" B word = V2)
 *     HA_V1     → Harbor     (H-A…)
 *     PST_SELL  → Pistol     (P-i-S-T…, aggressive = short seller)
 *     PST_HEDGE → Pastel     (P-a-S-T…, soft = hedge)
 *     TMA_V1    → Tomahawk   (T-o-M-A…)
 *
 * Fail direction: these helpers are pure formatting — the caller passes
 * its own isAdminUi. Panels follow the Phase 3 fail-OPEN convention
 * (curtain, not wall); the backend masking remains the actual wall.
 */

export const STRATEGY_DISPLAY = {
  SCALP_V1:  { real: "Scalp V1",      code: "Scala",    sub: "NIFTY options" },
  // Removed strategies — appear only in historical trade rows (same decode
  // rule: final letter b=V2, d=V4).
  SCALP_V2:  { real: "Scalp V2",      code: "Scarab",   sub: "NIFTY options" },
  SCALP_V4:  { real: "Scalp V4",      code: "Scaffold", sub: "NIFTY options" },
  SCALP_V3:  { real: "Scalp V3",      code: "Scenic",   sub: "NIFTY options" },
  SCALP_V5:  { real: "Scalp V5",      code: "Scribe",   sub: "NIFTY options" },
  IC_V1:     { real: "Iron Condor V1", code: "Indica",   sub: "NIFTY weekly" },
  IC_V2:     { real: "Iron Condor V2", code: "Icarus",   sub: "NIFTY weekly" },
  TSG_V1:    { real: "Time Strangle", code: "Tigris",   sub: "NIFTY weekly" },
  GC_V1:     { real: "GC V1",         code: "Glacier",  sub: "NIFTY options" },
  BB_V1:     { real: "BB V1",         code: "Bobbin",   sub: "BANKNIFTY options" },
  BB_V2:     { real: "BB V2",         code: "Baobab",   sub: "BANKNIFTY options" },
  HA_V1:     { real: "Heikin Ashi",   code: "Harbor",   sub: "NIFTY options" },
  PST_SELL:  { real: "PST Sell",      code: "Pistol",   sub: "NIFTY options" },
  PST_HEDGE: { real: "PST Hedge",     code: "Pastel",   sub: "NIFTY options" },
  TMA_V1:    { real: "TMA V1",        code: "Tomahawk", sub: "NIFTY weekly" },
  TMA_V2:    { real: "TMA V2",        code: "Timberwolf", sub: "NIFTY weekly" },
};

/**
 * Display name for a strategy id at the given ui level.
 * `adminFallback` lets callers keep their historical admin-facing label
 * (e.g. the host's META names) without duplicating the map.
 */
export function stratName(id, isAdminUi, adminFallback) {
  const d = STRATEGY_DISPLAY[id];
  if (!d) return adminFallback || id;
  return isAdminUi ? (adminFallback || d.real) : d.code;
}

/**
 * The small id tag rendered under rail cards ("IC_V1 · PAPER").
 * Non-admin never sees a raw strategy id — returns "" so callers can
 * render just the mode.
 */
export function stratIdTag(id, isAdminUi) {
  return isAdminUi ? id : "";
}

/**
 * Sub-header text. Admin keeps its (possibly mechanism-describing)
 * sub; non-admin gets only the instrument class.
 */
export function stratSub(id, isAdminUi, adminSub) {
  const d = STRATEGY_DISPLAY[id];
  if (isAdminUi) return adminSub || d?.sub || "";
  return d?.sub || "";
}