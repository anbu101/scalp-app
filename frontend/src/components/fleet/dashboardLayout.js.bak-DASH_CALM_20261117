// frontend/src/components/fleet/dashboardLayout.js
//
// ── DASH_MODERN_20260925 ── the Dashboard layout setting (app settings key
// `dashboard_layout`, backend is the source of truth; app_settings_api.py
// mirrors this list). "modern" = fleet MTM chart + per-book KPIs + blotter of
// the strategies that traded today; "legacy" = the previous rail + Today's
// Performance page. Unknown values fall back to modern, same as the backend.

export const DASHBOARD_LAYOUTS = [
  { id: "modern", name: "Modern", desc: "Fleet MTM chart, Live and Paper KPIs, and a blotter of the strategies that traded today." },
  { id: "legacy", name: "Legacy", desc: "The previous dashboard: strategy rail, focused panel and Today's Performance cards." },
];

export function normalizeLayout(v) {
  return v === "legacy" ? "legacy" : "modern";
}
