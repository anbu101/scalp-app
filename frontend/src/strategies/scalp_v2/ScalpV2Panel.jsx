/**
 * ScalpV2Panel — src/strategies/scalp_v2/ScalpV2Panel.jsx  (v2.0 — 3-leg model)
 *
 * RUNTIME dashboard panel for SCALP_V2 (V1 clone + 3-leg order split, SHORT).
 *
 * REDESIGN: the old Class A/B/C cards are gone. SCALP_V2 now mirrors SCALP_V1
 * upstream (single premium range, one surveillance list) and differs only at
 * order placement: a signal is split into 3 legs —
 *    L1 = signal strike (signal's exact TP/SL)
 *    L2 = +1 strike, L3 = -1 strike (pct-derived TP/SL)
 * Exit is all-or-nothing (any leg hits TP/SL → all close).
 *
 * Two views:
 *   - WATCHING: a single list of the selected CE/PE contracts under surveillance
 *     (with live premium + in-range dot), like SCALP_V1.
 *   - IN TRADE: the 3 legs (L1/L2/L3) with entry/TP/SL, distance bar, live P&L.
 *
 * Data: GET /api/scalp_v2/state
 *   { available, mode, premium_range:{min,max},
 *     group: { status, direction, signal_instrument, sl_pct, tp_pct, paper,
 *              exit_reason, realized_pnl, legs:{L1,L2,L3} } | null,
 *     watched: [ { symbol, side, premium, in_band } ] }
 *
 * Props: ltpMap, isPrimary, onBecomePrimary
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { getApiBase } from "../../api/base";
import { colors, spacing } from "../../tokens";

const POLL_FAST_MS = 3_000;
const LEG_ORDER    = ["L1", "L2", "L3"];
const LEG_LABEL    = { L1: "Signal", L2: "+1 Strike", L3: "−1 Strike" };

/* ─── Helpers ─── */
function fmt(v, dec = 2) {
  if (v == null || isNaN(v)) return "—";
  return Number(v).toFixed(dec);
}
function fmtPnL(v) {
  if (v == null || isNaN(v)) return "—";
  const r = Math.round(v);
  return `${r >= 0 ? "+" : ""}₹${Math.abs(r).toLocaleString("en-IN")}`;
}
function normalizeSymbol(sym) {
  if (!sym) return sym;
  return sym.replace(/\s+/g, "").toUpperCase();
}

/* ─── Tokens (aligned with prior panel) ─── */
const C = {
  bg:        colors.bg?.primary    ?? "#0a0f1e",
  bgCard:    colors.bg?.secondary  ?? "#111827",
  bgSurf:    colors.bg?.tertiary   ?? "#1f2937",
  border:    colors.border?.light  ?? "#374151",
  borderDim: colors.border?.dark   ?? "#1f2937",
  text:      colors.text?.primary  ?? "#f9fafb",
  textSec:   colors.text?.secondary ?? "#d1d5db",
  textMuted: colors.text?.muted    ?? "#6b7280",
  green:     colors.success        ?? "#10b981",
  greenDim:  "rgba(16,185,129,0.12)",
  red:       colors.danger         ?? "#ef4444",
  redDim:    "rgba(239,68,68,0.12)",
  amber:     colors.warning        ?? "#f59e0b",
  amberDim:  "rgba(245,158,11,0.12)",
  blue:      colors.primary        ?? "#3b82f6",
  blueDim:   "rgba(59,130,246,0.12)",
  v2:        "#a855f7",
  v2Dim:     "rgba(168,85,247,0.13)",
};
const MONO = "'JetBrains Mono','Fira Code','Courier New',monospace";
const FONT = "'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif";

function statusMeta(group) {
  if (!group) return { label: "Watching", color: C.textMuted, dim: C.bgSurf };
  switch (group.status) {
    case "OPEN":     return { label: "In Trade", color: C.amber, dim: C.amberDim };
    case "CLOSING":  return { label: "Closing",  color: C.red,   dim: C.redDim };
    case "PENDING":  return { label: "Entering", color: C.blue,  dim: C.blueDim };
    case "CLOSED":   return { label: "Closed",   color: C.textMuted, dim: C.bgSurf };
    default:         return { label: group.status, color: C.textMuted, dim: C.bgSurf };
  }
}

/* ─── Atoms ─── */
function ModeBadge({ mode }) {
  const isLive = mode === "LIVE";
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, letterSpacing: "0.4px",
      padding: "2px 8px", borderRadius: 4,
      background: isLive ? C.redDim : C.greenDim,
      color:      isLive ? C.red    : C.green,
      border:     `1px solid ${isLive ? C.red : C.green}30`,
      textTransform: "uppercase",
    }}>
      {isLive ? "⚡ LIVE" : "✎ PAPER"}
    </span>
  );
}

function SideBadge({ side }) {
  if (!side) return null;
  const isCE = side === "CE";
  return (
    <span style={{
      fontSize: 9, fontWeight: 700, padding: "1px 6px", borderRadius: 3,
      background: isCE ? C.greenDim : C.redDim,
      color:      isCE ? C.green    : C.red,
    }}>{side}</span>
  );
}

function LegStateBadge({ leg }) {
  if (!leg) return null;
  if (leg.open) {
    return (
      <span style={{ fontSize: 10, fontWeight: 600, padding: "2px 8px", borderRadius: 4,
        background: C.amberDim, color: C.amber, border: `1px solid ${C.amber}`, textTransform: "uppercase" }}>
        ● In Trade
      </span>
    );
  }
  const tp = leg.exit_reason === "TP" || leg.exit_reason === "GTT_TP";
  const sl = leg.exit_reason === "SL" || leg.exit_reason === "GTT_SL";
  const col = tp ? C.green : sl ? C.red : C.textMuted;
  const dim = tp ? C.greenDim : sl ? C.redDim : C.bgSurf;
  return (
    <span style={{ fontSize: 10, fontWeight: 600, padding: "2px 8px", borderRadius: 4,
      background: dim, color: col, border: `1px solid ${col}`, textTransform: "uppercase" }}>
      {leg.exit_reason || "Closed"}
    </span>
  );
}

function PriceRow({ label, value, color }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline",
      padding: "3px 0", borderBottom: `1px solid ${C.borderDim}` }}>
      <span style={{ fontSize: 11, color: C.textMuted }}>{label}</span>
      <span style={{ fontSize: 13, fontWeight: 700, color: color ?? C.text, fontFamily: MONO }}>{value}</span>
    </div>
  );
}

function DistanceBar({ entry, current, sl, tp }) {
  if (!entry || !current || !sl || !tp) return null;
  const range = sl - tp;                 // SHORT: sl above, tp below
  if (range <= 0) return null;
  const pct = Math.max(0, Math.min(100, ((sl - current) / range) * 100)); // near tp → 100
  const barColor = pct < 20 ? C.red : pct > 80 ? C.green : C.amber;
  return (
    <div style={{ margin: "8px 0 4px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: C.textMuted, marginBottom: 3 }}>
        <span>TP {fmt(tp)}</span>
        <span style={{ color: C.textSec, fontSize: 10, fontWeight: 600, fontFamily: MONO }}>{fmt(current)}</span>
        <span>SL {fmt(sl)}</span>
      </div>
      <div style={{ height: 4, background: C.borderDim, borderRadius: 2, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: barColor, borderRadius: 2, transition: "width 0.5s ease" }} />
      </div>
    </div>
  );
}

/* ─── Surveillance list (WATCHING view) ─── */
function WatchList({ watched, ltpMap }) {
  if (!watched || watched.length === 0) {
    return <div style={{ fontSize: 11, color: C.textMuted, padding: "8px 0" }}>No contracts selected yet</div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      {watched.map((w) => {
        const liveLtp = ltpMap?.[normalizeSymbol(w.symbol)] ?? w.premium;
        return (
          <div key={w.symbol} style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            padding: "5px 8px", borderRadius: 5,
            background: w.in_band ? C.v2Dim : "transparent",
            border: `1px solid ${w.in_band ? C.v2 + "33" : "transparent"}`,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 0 }}>
              <SideBadge side={w.side} />
              <span style={{ fontSize: 12, fontFamily: MONO, color: w.in_band ? C.text : C.textMuted,
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={w.symbol}>
                {w.symbol}
              </span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 7, flexShrink: 0 }}>
              <span style={{ fontSize: 12, fontFamily: MONO, fontWeight: 700, color: w.in_band ? C.text : C.textMuted }}>
                {fmt(liveLtp)}
              </span>
              <span style={{ width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
                background: w.in_band ? C.green : C.textMuted,
                boxShadow: w.in_band ? `0 0 5px ${C.green}80` : "none" }}
                title={w.in_band ? "In premium range — eligible" : "Out of range"} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ─── Leg card (IN TRADE view) ─── */
function LegCard({ role, leg, ltpMap }) {
  const isSignal = role === "L1";
  const accent   = isSignal ? C.v2 : C.amber;
  const symbol   = leg?.symbol;
  const ltp      = symbol ? (ltpMap?.[normalizeSymbol(symbol)] ?? null) : null;
  const open     = leg && leg.open;

  const unrealized = open && ltp && leg.entry_price
    ? (leg.entry_price - ltp) * leg.qty       // SHORT
    : null;

  return (
    <div style={{
      flex: 1, minWidth: 0, background: C.bgCard,
      border: `1px solid ${leg ? accent : C.borderDim}`,
      borderTop: `3px solid ${accent}`, borderRadius: 8,
      padding: spacing.md, display: "flex", flexDirection: "column", gap: spacing.sm,
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 12, fontWeight: 800, color: isSignal ? C.v2 : C.textSec, letterSpacing: "0.4px" }}>
            {LEG_LABEL[role]}
          </span>
          {isSignal && (
            <span style={{ fontSize: 9, fontWeight: 800, padding: "1px 6px", borderRadius: 3,
              background: C.v2Dim, color: C.v2, border: `1px solid ${C.v2}50`, textTransform: "uppercase" }}>
              ★ Signal
            </span>
          )}
        </div>
        {leg && <LegStateBadge leg={leg} />}
      </div>

      {leg ? (
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, color: C.text, fontFamily: MONO,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginBottom: 4 }} title={symbol}>
            {symbol}
          </div>
          {open ? (
            <>
              <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 4 }}>
                <span style={{ fontSize: 20, fontWeight: 700, fontFamily: MONO,
                  color: unrealized != null ? (unrealized > 0 ? C.green : unrealized < 0 ? C.red : C.text) : C.text }}>
                  {ltp != null ? fmt(ltp) : "—"}
                </span>
                {unrealized != null && (
                  <span style={{ fontSize: 12, fontWeight: 700, fontFamily: MONO,
                    color: unrealized > 0 ? C.green : unrealized < 0 ? C.red : C.textMuted }}>
                    {fmtPnL(unrealized)}
                  </span>
                )}
              </div>
              <PriceRow label="Entry" value={fmt(leg.entry_price)} />
              <PriceRow label="TP" value={fmt(leg.tp)} color={C.green} />
              <PriceRow label="SL" value={fmt(leg.sl)} color={C.red} />
              <DistanceBar entry={leg.entry_price} current={ltp} sl={leg.sl} tp={leg.tp} />
            </>
          ) : (
            <>
              <PriceRow label="Entry" value={fmt(leg.entry_price)} />
              <PriceRow label="Exit"  value={fmt(leg.exit_price)} color={C.textSec} />
              <PriceRow label="P&L"   value={fmtPnL(leg.realized_pnl)}
                color={(leg.realized_pnl ?? 0) > 0 ? C.green : (leg.realized_pnl ?? 0) < 0 ? C.red : C.textMuted} />
            </>
          )}
        </div>
      ) : (
        <div style={{ fontSize: 10, color: C.textMuted, padding: "10px 0", textAlign: "center" }}>
          not placed
        </div>
      )}
    </div>
  );
}

/* ─── Compact view ─── */
function CompactView({ group, onBecomePrimary }) {
  const meta = statusMeta(group);
  return (
    <div onClick={onBecomePrimary} style={{
      height: "100%", display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", gap: spacing.lg,
      cursor: "pointer", padding: spacing.md, background: C.bgCard,
      border: `1px solid ${C.borderDim}`, borderRadius: 8,
    }}>
      <div style={{ writingMode: "vertical-rl", textOrientation: "mixed", transform: "rotate(180deg)",
        fontSize: 11, fontWeight: 800, color: C.v2, letterSpacing: "1.5px", textTransform: "uppercase" }}>
        SCALP V2
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
        {LEG_ORDER.map((role) => {
          const leg = group?.legs?.[role];
          const on  = leg && leg.open;
          const color = on ? (role === "L1" ? C.v2 : C.amber) : C.textMuted;
          return (
            <div key={role} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3 }}>
              <span style={{ width: 8, height: 8, borderRadius: "50%", background: color,
                boxShadow: on ? `0 0 8px ${color}` : "none" }} />
              <span style={{ fontSize: 8, color, fontWeight: 700 }}>{role}</span>
            </div>
          );
        })}
      </div>
      <div style={{ width: 1, flex: 1, background: C.borderDim }} />
      <div style={{ writingMode: "vertical-rl", transform: "rotate(180deg)",
        fontSize: 9, fontWeight: 600, textTransform: "uppercase", color: meta.color }}>
        {meta.label}
      </div>
    </div>
  );
}

/* ─── Main ─── */
export default function ScalpV2Panel({ ltpMap, isPrimary, onBecomePrimary }) {
  const [state, setState] = useState(null);
  const [loading, setLoading] = useState(true);
  const pollRef = useRef(null);

  const fetchState = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/scalp_v2/state`);
      if (!res.ok) return;
      const data = await res.json();
      setState(data ?? null);
    } catch { /* keep last */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    fetchState();
    const fast = setInterval(fetchState, POLL_FAST_MS);
    pollRef.current = fast;
    return () => clearInterval(fast);
  }, [fetchState]);

  const group   = state?.group ?? null;
  const mode    = state?.mode ?? "PAPER";
  const watched = state?.watched ?? [];
  const range   = state?.premium_range ?? { min: 0, max: 0 };
  const meta    = statusMeta(group);

  const groupPnl = (() => {
    if (!group) return null;
    let total = 0; let any = false;
    for (const role of LEG_ORDER) {
      const leg = group.legs?.[role];
      if (!leg) continue;
      if (!leg.open && leg.realized_pnl != null) { total += leg.realized_pnl; any = true; }
      else if (leg.open) {
        const ltp = ltpMap?.[normalizeSymbol(leg.symbol)];
        if (typeof ltp === "number" && typeof leg.entry_price === "number") {
          total += (leg.entry_price - ltp) * leg.qty; any = true;
        }
      }
    }
    return any ? total : null;
  })();

  if (!isPrimary) {
    return <CompactView group={group} onBecomePrimary={onBecomePrimary} />;
  }

  const inTrade = group && (group.status === "OPEN" || group.status === "CLOSING");

  return (
    <div style={{
      background: C.bg, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden",
      display: "flex", flexDirection: "column", height: "100%", fontFamily: FONT,
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: spacing.md, padding: "10px 14px",
        background: C.bgCard, borderBottom: `1px solid ${C.borderDim}`, flexShrink: 0, flexWrap: "wrap" }}>
        <div style={{ fontSize: 12, fontWeight: 800, color: C.v2, letterSpacing: "1px", textTransform: "uppercase" }}>
          SCALP V2
        </div>
        <div style={{ fontSize: 11, color: C.textMuted }}>3-leg split · 1m · NIFTY · SHORT</div>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 9px", borderRadius: 4,
          background: meta.dim, color: meta.color, border: `1px solid ${meta.color}40`, textTransform: "uppercase" }}>
          {group && group.direction ? `${meta.label} · ${group.direction}` : meta.label}
        </span>
        {groupPnl != null && (
          <span style={{ fontSize: 13, fontWeight: 800, fontFamily: MONO,
            color: groupPnl > 0 ? C.green : groupPnl < 0 ? C.red : C.textMuted }}>
            {fmtPnL(groupPnl)}
          </span>
        )}
        <ModeBadge mode={mode} />
      </div>

      {/* Config strip */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, padding: "6px 14px",
        background: C.bgSurf, borderBottom: `1px solid ${C.borderDim}`, flexShrink: 0, flexWrap: "wrap" }}>
        {[
          { label: "Premium", value: `₹${range.min}–₹${range.max}` },
          { label: "Direction", value: group?.direction ?? "—" },
          { label: "Legs", value: "3 (signal ±1)" },
          { label: "SL%", value: group ? `${(group.sl_pct * 100).toFixed(1)}%` : "—" },
          { label: "TP%", value: group ? `${(group.tp_pct * 100).toFixed(1)}%` : "—" },
        ].map((s, i) => (
          <div key={i} style={{ display: "flex", flexDirection: "column", gap: 1, flexShrink: 0 }}>
            <span style={{ fontSize: 8, color: C.textMuted, letterSpacing: "0.5px", textTransform: "uppercase", fontWeight: 600 }}>
              {s.label}
            </span>
            <span style={{ fontSize: 12, fontWeight: 700, color: C.text, fontFamily: MONO }}>{s.value}</span>
          </div>
        ))}
      </div>

      {/* Body */}
      {loading ? (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: C.textMuted, fontSize: 12 }}>
          Loading…
        </div>
      ) : state && !state.available ? (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
          gap: 6, color: C.textMuted, fontSize: 12, padding: spacing.xl, textAlign: "center" }}>
          <span style={{ fontSize: 22 }}>🛰️</span>
          SCALP_V2 engine not started
          <span style={{ fontSize: 10 }}>Surveillance begins when the strategy is enabled and the market session opens.</span>
        </div>
      ) : inTrade ? (
        <div style={{ flex: 1, display: "flex", gap: spacing.md, padding: spacing.md, minHeight: 0, overflowY: "auto" }}>
          {LEG_ORDER.map((role) => (
            <LegCard key={role} role={role} leg={group?.legs?.[role] ?? null} ltpMap={ltpMap} />
          ))}
        </div>
      ) : (
        <div style={{ flex: 1, padding: spacing.md, minHeight: 0, overflowY: "auto" }}>
          <div style={{ fontSize: 9, color: C.textMuted, marginBottom: 8, letterSpacing: "0.5px", textTransform: "uppercase" }}>
            Under Surveillance · {watched.length} contracts
          </div>
          <WatchList watched={watched} ltpMap={ltpMap} />
        </div>
      )}

      {/* Footer */}
      <div style={{ borderTop: `1px solid ${C.borderDim}`, padding: "6px 14px", background: C.bgCard, flexShrink: 0 }}>
        <div style={{ display: "flex", gap: spacing.lg, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ fontSize: 9, color: C.textMuted }}>
            <span style={{ color: C.green }}>●</span> in premium range · <span style={{ color: C.v2 }}>★</span> signal leg ·
            {" "}all-or-nothing exit
          </span>
          <div style={{ flex: 1 }} />
          {group?.exit_reason && (
            <span style={{ fontSize: 9, color: C.textMuted }}>
              Last exit: <span style={{ color: C.textSec }}>{group.exit_reason}</span>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}