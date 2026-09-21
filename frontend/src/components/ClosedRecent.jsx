// frontend/src/components/ClosedRecent.jsx
//
// ── CLOSED_RECENT_20260921 ── "Closed positions · today and recent"
// The section VETPanel v2 introduced, as ONE shared component so IC_V1/IC_V2
// and TMA_V1/TMA_V2 render the identical table instead of four diverging
// copies (the paramFormat.js lesson).
//
// One row per POSITION, never per leg — a hedge/wing is not a trade; its P&L
// is folded into the position's net. Today's exits render at full strength,
// the recent tail dimmed. "Today" is by EXIT timestamp, IST calendar day:
// these are positional strategies, so an entry-day filter would hide exactly
// the rows that matter (the lifetime-totals-mislabelled-as-today bug family
// starts with entry-day filters).
//
// Normalised group shape (all optional except group_id / exit_ts / net):
//   { group_id, kind: "S"|"L"|"IC", label, sub, lots, qty, entry, exit,
//     entry_ts, exit_ts, reason, charges, net, today, approx }
//
// Exports:
//   default ClosedRecent      presentational card
//   useClosedGroups(url)      poller for routes that return {groups, today_net}
//   groupSpreadLegs(trades)   tma_trades / tma2_trades legs -> groups
//   isTodayIST(ts)            the one "today" rule, shared
//
// UI_MASK: `showParams=false` replaces every exit reason with "CLOSED".

import { useEffect, useState } from "react";
import { colors, spacing, typography, pnlStyle, alpha } from "../tokens";

const IST = { timeZone: "Asia/Kolkata" };
const dayKey = (d) => d.toLocaleDateString("en-CA", IST);              // YYYY-MM-DD in IST

export function isTodayIST(ts) {
  return !!ts && dayKey(new Date(ts * 1000)) === dayKey(new Date());
}

function hhmm(ts) {
  return new Date(ts * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, ...IST });
}
// Positional: a bare "10:21" is ambiguous across days — other-day stamps carry the date.
function fmtTs(ts) {
  if (!ts) return "—";
  if (isTodayIST(ts)) return hhmm(ts);
  return `${new Date(ts * 1000).toLocaleDateString("en-IN", { day: "2-digit", month: "short", ...IST })} · ${hhmm(ts)}`;
}
function fmtInr(v) {
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(Math.round(v));
  return `${v < 0 ? "−" : v > 0 ? "+" : ""}₹${a.toLocaleString("en-IN")}`;
}
const fmt2 = (v) => (v == null || isNaN(v) ? "—" : Number(v).toFixed(2));

/* tma_trades / tma2_trades rows (one per LEG: direction SELL = the short,
 * BUY = the hedge, linked by group_id) -> one group per spread. A spread is
 * closed only when every leg of it is; its exit time is the LAST leg's. */
export function groupSpreadLegs(trades) {
  const by = new Map();
  for (const t of trades || []) {
    if (!t || t.group_id == null) continue;
    if (!by.has(t.group_id)) by.set(t.group_id, []);
    by.get(t.group_id).push(t);
  }
  const out = [];
  for (const [gid, legs] of by) {
    if (legs.some((l) => l.status !== "CLOSED" || !l.exit_ts)) continue;
    const main = legs.find((l) => l.direction === "SELL") || legs[0];
    const hedge = legs.find((l) => l !== main);
    const lotSize = main.lot_size || null;
    const exitTs = Math.max(...legs.map((l) => l.exit_ts));
    out.push({
      group_id: gid,
      kind: main.direction === "SELL" ? "S" : "L",
      label: main.tradingsymbol,
      sub: hedge ? `+ hedge ${hedge.tradingsymbol}` : null,
      lots: main.lots ?? (lotSize ? Math.round(main.qty / lotSize) : null),
      qty: main.qty,
      entry: main.entry_price, exit: main.exit_price,
      entry_ts: main.entry_ts, exit_ts: exitTs,
      reason: main.exit_reason,
      charges: legs.reduce((a, l) => a + (l.charges || 0), 0),
      net: legs.reduce((a, l) => a + (l.net_pnl ?? l.pnl ?? 0), 0),
      today: isTodayIST(exitTs),
    });
  }
  return out.sort((a, b) => b.exit_ts - a.exit_ts);
}

/* Poller for a route returning { groups, today_net }. Keeps the last good
 * payload when the backend blips; never throws into the panel. */
export function useClosedGroups(url, pollMs = 15000) {
  const [data, setData] = useState({ groups: [], today_net: 0, loaded: false });
  useEffect(() => {
    if (!url) return undefined;
    let alive = true;
    const pull = async () => {
      try {
        const r = await fetch(url);
        if (!r.ok) return;
        const d = await r.json();
        if (alive && d && Array.isArray(d.groups)) setData({ groups: d.groups, today_net: d.today_net ?? 0, loaded: true });
      } catch { /* keep last */ }
    };
    pull();
    const t = setInterval(pull, pollMs);
    return () => { alive = false; clearInterval(t); };
  }, [url, pollMs]);
  return data;
}

const KIND = {
  S:  ["S",  "loss",    "SHORT — option sold"],
  L:  ["L",  "profit",  "LONG — option bought"],
  IC: ["IC", "primary", "Iron condor — all legs, one row"],
};

export default function ClosedRecent({
  groups = [],
  todayNet,                       // omit -> summed from groups flagged today
  showParams = true,              // UI_MASK: false -> reasons read "CLOSED"
  limit = 12,
  entryLabel = "Entry",
  exitLabel = "Exit px",
  sizeLabel = "Lots",
  emptyText = "No closed positions yet.",
  style,
}) {
  const rows = groups.slice(0, limit);
  const net = todayNet ?? groups.filter((g) => g.today).reduce((a, g) => a + (g.net || 0), 0);
  const num = { ...typography.mono };
  const heads = [["Exit", "left"], ["", "left"], ["Position", "left"], [sizeLabel, "right"], [entryLabel, "right"],
                 [exitLabel, "right"], ["Reason", "left"], ["Charges", "right"], ["Net P&L", "right"]];
  const td = (align = "left") => ({ padding: "5px 6px", textAlign: align, verticalAlign: "top" });

  return (
    <div style={{ background: colors.bg.secondary, borderRadius: 10, padding: spacing.lg,
                  border: `1px solid ${colors.border.light}`, ...style }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
        <div style={{ ...typography.label, fontSize: 10.5, color: colors.text.muted }}>Closed positions · today and recent</div>
        <div style={{ fontSize: 12, ...num }}>
          <span style={{ color: colors.text.muted }}>today </span><b style={pnlStyle(net)}>{fmtInr(net)}</b>
        </div>
      </div>
      {rows.length === 0 && <div style={{ fontSize: 12, color: colors.text.muted }}>{emptyText}</div>}
      {rows.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, ...num }}>
            <thead>
              <tr style={{ color: colors.text.muted }}>
                {heads.map(([h, align], i) => (
                  <th key={i} style={{ fontWeight: 600, fontSize: 10.5, letterSpacing: 0.4, textTransform: "uppercase",
                                       padding: "4px 6px", textAlign: align, whiteSpace: "nowrap",
                                       borderBottom: `1px solid ${colors.border.light}` }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((g) => {
                const [kText, kTone, kTitle] = KIND[g.kind] || KIND.S;
                const tone = colors[kTone];
                return (
                  <tr key={g.group_id} style={{ borderBottom: `1px solid ${colors.border.dark}`, opacity: g.today ? 1 : 0.62 }}>
                    <td style={{ ...td(), whiteSpace: "nowrap" }}>{fmtTs(g.exit_ts)}</td>
                    <td style={td()}>
                      <span title={kTitle} style={{ background: alpha(tone, 16), color: tone, border: `1px solid ${alpha(tone, 35)}`,
                                                    borderRadius: 6, padding: "1px 7px", fontSize: 10.5, fontWeight: 700 }}>{kText}</span>
                    </td>
                    <td style={td()}>
                      <b>{g.label || "—"}</b>
                      {g.book === "LIVE" && <span style={{ marginLeft: 6, fontSize: 10, fontWeight: 700, color: colors.loss }}>LIVE</span>}
                      {g.sub && <div style={{ fontSize: 10.5, color: colors.text.muted, fontWeight: 400 }}>{g.sub}</div>}
                    </td>
                    <td style={td("right")}>{g.lots ?? g.qty ?? "—"}</td>
                    <td style={td("right")}>{fmt2(g.entry)}</td>
                    <td style={td("right")}>{fmt2(g.exit)}</td>
                    <td style={{ ...td(), color: colors.text.secondary }}>{showParams ? (g.reason || "—") : "CLOSED"}</td>
                    <td style={{ ...td("right"), color: colors.text.muted }}>{g.charges ? `₹${Math.round(g.charges).toLocaleString("en-IN")}` : "—"}</td>
                    <td style={{ ...td("right"), fontWeight: 700, ...pnlStyle(g.net) }}
                        title={g.approx ? "approximate — a leg closed without an exit price, or charges could not be modelled" : undefined}>
                      {g.approx ? "≈ " : ""}{fmtInr(g.net)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
