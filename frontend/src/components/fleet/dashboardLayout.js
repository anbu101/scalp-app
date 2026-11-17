// frontend/src/components/fleet/dashboardLayout.js
//
// ── DASH_MODERN_20260925 ── the Dashboard layout setting (app settings key
// `dashboard_layout`, backend is the source of truth; app_settings_api.py
// mirrors this list). "modern" = fleet MTM chart + per-book KPIs + blotter of
// the strategies that traded today; "legacy" = the previous rail + Today's
// Performance page. Unknown values fall back to modern, same as the backend.

// ── DASH_CALM_20261117 ── "modern" is now the redesigned page (one big number, a card per
// strategy with its meter); the previous Modern page stays selectable as "classic".
export const DASHBOARD_LAYOUTS = [
  { id: "modern", name: "Modern", desc: "One big number for today, the day's chart, and a card per strategy with its entry / exit meter." },
  { id: "classic", name: "Classic", desc: "Book summary, Fleet MTM chart with overlays and grid, and the Traded-today table." },
  { id: "legacy", name: "Legacy", desc: "The previous dashboard: strategy rail, focused panel and Today's Performance cards." },
];

export function normalizeLayout(v) {
  return v === "legacy" || v === "classic" ? v : "modern";
}
