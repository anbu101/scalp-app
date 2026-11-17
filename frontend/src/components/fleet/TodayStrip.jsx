// frontend/src/components/fleet/TodayStrip.jsx
//
// ── FLEET_TODAY_STRIP_20261015 ── one number per strategy, explained.
//
// The "Traded today" row and a strategy's own panel measure different things:
//   row   = EVERYTHING the strategy did today — legs open now + every leg
//           closed today, including a carried position closed this morning;
//           gross, charges in their own column;
//   panel = the CURRENT position, from its own entry.
// Shown side by side with no explanation they read as two answers to one
// question (IC V2, 15 Oct: +1,04,293 vs +95,290 — the gap was yesterday's
// position closed at 09:16). This strip sits directly above the focused
// panel, repeats the row's numbers from the SAME /api/fleet/today payload
// (so it can never disagree with the table) and says in words what the panel
// below measures.

import { fmtInr } from "./fleetFormat";
import { SPACE, COLOR, TYPE, NUM, CAPS, tone } from "./dash";   // ── DASH_SYSTEM_20261117 ──

const clock = (ts) => {
  if (!ts) return null;
  const d = new Date(Number(ts) * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

export function todayExplain(r) {
  if (!r) return null;
  const closed = Number(r.closed_trades || 0);
  const open = Number(r.open_legs || 0);
  const parts = [];
  if (closed > 0) parts.push(`${closed} position${closed === 1 ? "" : "s"} closed today`);
  if (open > 0) parts.push(`${open} open leg${open === 1 ? "" : "s"}`);
  const scope = parts.length ? parts.join(" + ") : "nothing traded";
  let panel;
  if (open > 0 && closed > 0) {
    panel = "The panel below follows only the OPEN position, from its own entry — so its MTM leaves out what was closed earlier today.";
  } else if (open > 0) {
    panel = r.carried
      ? "The open position was carried from a previous session: its P&L here and below is measured from its entry, not from today's open."
      : "The panel below follows this same open position; its figures differ only by charges (net) or a few seconds of price.";
  } else {
    panel = "Flat now — the panel below shows the closed trades.";
  }
  return { scope, panel };
}

// ── DASH_SYSTEM_20261117 ── rebuilt on dash.js: no card of its own — a two-line caption
// under a rule, headline MTM coloured, the equation in ink, the explanation muted.
export default function TodayStrip({ rows = [], name, compact = false }) {
  if (!rows.length) return null;
  return (
    <div data-testid="today-strip" data-dash-part="strip" style={{ display: "flex", flexDirection: "column", gap: SPACE.sm, marginBottom: SPACE.lg }}>
      {rows.map((r) => {
        const ex = todayExplain(r);
        const asof = r.marks_stale > 0 ? clock(r.mark_asof) : null;
        return (
          <div key={r.key} style={{ borderTop: `1px solid ${COLOR.line}`, paddingTop: SPACE.md, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "baseline", flexWrap: "wrap", gap: `${SPACE.xs}px ${compact ? SPACE.md : SPACE.lg}px` }}>
              <span style={CAPS}>Today · {name} · {r.book === "LIVE" ? "Live" : "Paper"}</span>
              <span style={{ ...TYPE.title, ...NUM, color: tone(r.gross) }}>{fmtInr(r.gross)}</span>
              <span style={{ ...TYPE.body, color: COLOR.muted }}>
                realised <span style={{ ...NUM, color: COLOR.ink }}>{fmtInr(r.realised)}</span>
                {" · "}unrealised <span style={{ ...NUM, color: COLOR.ink }}>{fmtInr(r.unrealised)}</span>
                {" · "}charges <span style={{ ...NUM, color: COLOR.ink }}>₹{Math.round(Number(r.charges) || 0).toLocaleString("en-IN")}</span>
                {" · "}net <span style={{ ...NUM, color: COLOR.ink }}>{fmtInr(r.net)}</span>
                {asof ? ` · marks as of ${asof}` : ""}
              </span>
            </div>
            {ex && (
              <div style={{ ...TYPE.meta, color: COLOR.muted, marginTop: SPACE.xs }}>
                Counts {ex.scope}. {ex.panel}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
