// frontend/src/components/ClosedRecentFor.jsx
//
// ── CLOSED_RECENT_FLEET_20260921 ── the "Closed positions · today and recent"
// card for every strategy whose panel does not already carry one. Mounted
// ONCE by StrategyHost under the focused panel — nine panels get the section
// without nine panel edits (BBPanel among them stays untouched), and a
// strategy added later gets it with no work at all as long as it books into
// paper_trades / trades.
//
// Panels that render their own (own data source, own columns):
//   IC_V1 / IC_V2 / TMA_V1 / TMA_V2   fence CLOSED_RECENT_20260921
//   VET_V1                            fence VET_PANEL_V2_20260921
// Add an id here if a panel ever brings its own — otherwise it shows twice.
//
// Data: GET /api/closed/{id} (15 s poll, last good payload kept). Reasons are
// masked server-side for non-admin licences; showParams is the curtain.

import { getApiBase } from "../api/base";
import { spacing } from "../tokens";
import { useEntitlements } from "../hooks/useEntitlements";       // ── UI_MASK ──
import ClosedRecent, { useClosedGroups } from "./ClosedRecent";

const SELF_RENDERING = new Set(["IC_V1", "IC_V2", "TMA_V1", "TMA_V2", "VET_V1"]);

// Column wording per strategy; everything else is the shared default.
const LABELS = {
  TSG_V1:   { entryLabel: "Credit", exitLabel: "Debit", emptyText: "No closed strangles yet." },
  SCALP_V1: { entryLabel: "Sold @", exitLabel: "Covered @" },
};

export default function ClosedRecentFor({ strategyId }) {
  const own = !strategyId || SELF_RENDERING.has(strategyId);
  const { loaded: licenseLoaded, isAdminUi } = useEntitlements();
  // hooks run unconditionally; a null url makes the poller a no-op
  const data = useClosedGroups(own ? null : `${getApiBase()}/api/closed/${strategyId}`);
  if (own) return null;
  const l = LABELS[strategyId] || {};
  return (
    <ClosedRecent
      groups={data.groups}
      todayNet={data.today_net}
      showParams={!licenseLoaded || isAdminUi}
      entryLabel={l.entryLabel}
      exitLabel={l.exitLabel}
      emptyText={data.loaded ? (l.emptyText || "No closed positions yet.") : "Loading closed positions…"}
      style={{ marginTop: spacing.md }}
    />
  );
}
