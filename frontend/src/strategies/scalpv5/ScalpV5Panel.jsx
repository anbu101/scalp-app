/**
 * SCALP V5 PANEL  (option-BUYING · 3-minute candles · TEST strategy)
 *
 * Intended path: src/strategies/scalpv5/ScalpV5Panel.jsx
 *
 * SCALP_V5 is a LONG option-buyer on 3-minute candles. Unlike V3/V4 (hedge:
 * track one contract, buy the opposite), V5 BUYS THE SIGNALLING CONTRACT
 * ITSELF — one logical trade = one instrument. So this panel is NOT a hedge
 * two-leg layout; it is closer to V1's slot cards, with two V5-specific twists:
 *
 *   1. LONG geometry: P&L = (ltp - entry) * qty; SL BELOW entry, TP ABOVE entry.
 *      SL and TP are OPTIONAL (0 = disabled) — a trade may run purely to its
 *      EMA exit with no SL/TP at all.
 *   2. EMA exit (the defining V5 mechanic): the position is held until a 3-minute
 *      candle on the held symbol CLOSES BELOW EMA20_HIGH, at which point it is
 *      force-closed (regardless of SL/TP). There is no time-based exit — a trade
 *      can span multiple candles.
 *
 * At most ONE open trade at a time (DB-backed global gate), like V3/V4.
 *
 * Data: getScalpV5State() → { mode, day_blocked, selection:{CE,PE}, open_trade }
 * and the shared ltpMap prop for live prices.
 *
 * Props: ltpMap, isPrimary, onBecomePrimary  (matches the ScalpPanel contract)
 */

import { useEffect, useState, useMemo } from "react";
import { useIsMobile } from "../../hooks/useIsMobile";
import { getScalpV5State, getStrategyConfig } from "../../api";
import { EmptyState } from "../../components/LoadingStates";
import { colors, spacing } from "../../tokens";

const STRATEGY_ID = "SCALP_V5";

/* ─── Tokens (aligned with the other panels) ───────────── */
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
  // SCALP_V5 accent — cyan/teal (option-buying, 3m; distinct from V1 amber,
  // V2 violet, V3 pink, V4 orange)
  v5:        "#06b6d4",
  v5Dim:     "rgba(6,182,212,0.13)",
};
const MONO = "'JetBrains Mono','Fira Code','Courier New',monospace";
const FONT = "'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif";

/* ─── Helpers ────────────────────────────────────────────────── */
function normalizeSymbol(sym) {
  if (!sym) return sym;
  return sym.replace(/\s+/g, "").toUpperCase();
}
const safeNum = (v) => (typeof v === "number" && !isNaN(v) ? v : 0);
function fmt(v, dec = 2) {
  if (v == null || isNaN(v)) return "—";
  return Number(v).toFixed(dec);
}
function fmtPnL(v) {
  if (v == null || isNaN(v)) return "—";
  const r = Math.round(v);
  return `${r >= 0 ? "+" : ""}₹${Math.abs(r).toLocaleString("en-IN")}`;
}

/* V5 is ALWAYS LONG (a bought option): P&L = (ltp - entry) * qty. */
function longPnl(entry, qty, ltp) {
  const e = safeNum(entry), q = safeNum(qty);
  if (!e || typeof ltp !== "number") return null;
  return (ltp - e) * q;
}

/* ─── Atoms ──────────────────────────────────────────────────── */
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
  const isCE = side === "CE";
  return (
    <span style={{
      fontSize: 11, fontWeight: 700, padding: "2px 9px", borderRadius: 4,
      background: isCE ? C.greenDim : C.redDim,
      color:      isCE ? C.green    : C.red,
      border:     `1px solid ${isCE ? C.green : C.red}30`,
    }}>
      {side}
    </span>
  );
}

function PriceRow({ label, value, color, sub }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline",
      padding: "3px 0", borderBottom: `1px solid ${C.borderDim}` }}>
      <span style={{ fontSize: 11, color: C.textMuted }}>{label}</span>
      <div style={{ textAlign: "right" }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: color ?? C.text, fontFamily: MONO }}>{value}</span>
        {sub && <div style={{ fontSize: 9, color: C.textMuted, marginTop: 1 }}>{sub}</div>}
      </div>
    </div>
  );
}

/* LONG distance bar: SL on the LEFT (bad), TP on the RIGHT (good). The live
 * dot sits between them; near the right = winning. When SL or TP is disabled
 * (0/None) the bar is hidden — the trade then runs to its EMA exit only. */
function LongDistanceBar({ entry, current, sl, tp }) {
  if (current == null || !sl || !tp) return null;
  const range = Math.abs(tp - sl);
  if (range <= 0) return null;
  const pct = Math.max(0, Math.min(100, ((current - sl) / range) * 100)); // higher ltp → toward TP
  const barColor = pct < 20 ? C.red : pct > 80 ? C.green : C.amber;
  return (
    <div style={{ margin: "8px 0 4px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: C.textMuted, marginBottom: 3 }}>
        <span style={{ color: C.red }}>SL {fmt(sl)}</span>
        <span style={{ color: C.textSec, fontSize: 10, fontWeight: 600, fontFamily: MONO }}>{fmt(current)}</span>
        <span style={{ color: C.green }}>TP {fmt(tp)}</span>
      </div>
      <div style={{ height: 4, background: C.borderDim, borderRadius: 2, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: barColor, borderRadius: 2, transition: "width 0.5s ease" }} />
      </div>
    </div>
  );
}

/* Static chip describing the exit rule (no countdown — exit is candle-driven). */
function ExitModeChip() {
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, fontFamily: MONO,
      padding: "2px 8px", borderRadius: 4,
      background: C.bgSurf, color: C.textSec,
      border: `1px solid ${C.borderDim}`,
    }}>
      ↘ exit &lt; EMA20H
    </span>
  );
}

/* ─── Surveillance card (one selected strike, no trade) ─── */
function SurveillanceCard({ row, ltp }) {
  const symbol = row.tradingsymbol || row.symbol;
  return (
    <div style={{
      flex: 1, minWidth: 0,
      background: C.bgCard,
      border: `1px solid ${C.borderDim}`,
      borderTop: `3px solid ${C.borderDim}`,
      borderRadius: 8, padding: spacing.md,
      display: "flex", flexDirection: "column", gap: spacing.sm,
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
        <SideBadge side={row.side} />
        <span style={{ fontSize: 10, fontWeight: 600, padding: "2px 8px", borderRadius: 4,
          background: C.bgSurf, color: C.textMuted, border: `1px solid ${C.borderDim}`, textTransform: "uppercase" }}>
          ○ Surveillance
        </span>
      </div>
      <div style={{ fontSize: 10, color: C.textMuted }}>Strike {row.strike ?? "—"}</div>
      <div style={{ fontSize: 12, fontWeight: 700, color: symbol ? C.text : C.textMuted, fontFamily: MONO,
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={symbol}>
        {symbol || "No contract"}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 2 }}>
        <span style={{ fontSize: 9, color: C.textMuted, textTransform: "uppercase", letterSpacing: "0.5px" }}>Live LTP</span>
        <span style={{ fontSize: 18, fontWeight: 700, fontFamily: MONO, color: ltp != null ? C.text : C.textMuted }}>
          {ltp != null ? fmt(ltp) : "—"}
        </span>
      </div>
      <div style={{ fontSize: 9, color: C.textMuted }}>
        {symbol ? "Eligible — awaiting BUY signal" : "Waiting for selection"}
      </div>
    </div>
  );
}

/* ─── Hero trade card — the single open LONG position ─── */
function HeroTradeCard({ trade, ltpMap }) {
  const sym = trade.symbol ? normalizeSymbol(trade.symbol) : null;
  const ltp = sym ? ltpMap[sym] ?? null : null;

  const pnl    = longPnl(trade.entry, trade.qty, ltp);
  const pnlCol = pnl == null ? C.textMuted : pnl > 0 ? C.green : pnl < 0 ? C.red : C.text;
  const hasGtt = !!trade.gtt_id;

  const slOn = trade.sl != null && trade.sl > 0;
  const tpOn = trade.tp != null && trade.tp > 0;

  return (
    <div style={{
      background: C.bgCard,
      border: `1px solid ${C.v5}`,
      borderTop: `3px solid ${C.v5}`,
      borderRadius: 8, padding: spacing.md,
      display: "flex", flexDirection: "column", gap: spacing.sm,
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 11, fontWeight: 800, color: C.v5, letterSpacing: "0.5px", textTransform: "uppercase" }}>
            ● Active Long
          </span>
          {trade.side && <SideBadge side={trade.side} />}
          <span style={{ fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 4,
            background: C.greenDim, color: C.green }}>↑ BUY</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <ExitModeChip />
          <span style={{ fontSize: 10, fontWeight: 700, color: hasGtt ? C.green : C.amber }}>
            {hasGtt ? `✓ GTT ${String(trade.gtt_id).slice(-4)}` : "⚠ No GTT (SL/TP off or paper)"}
          </span>
        </div>
      </div>

      {/* Symbol + live LTP + P&L */}
      <div style={{ fontSize: 13, fontWeight: 700, color: C.text, fontFamily: MONO,
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={trade.symbol}>
        {trade.symbol || "—"}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, margin: "2px 0 4px" }}>
        <span style={{ fontSize: 24, fontWeight: 700, fontFamily: MONO, color: ltp != null ? C.text : C.textMuted }}>
          {ltp != null ? fmt(ltp) : "—"}
        </span>
        <span style={{ fontSize: 14, fontWeight: 700, fontFamily: MONO, color: pnlCol }}>
          {fmtPnL(pnl)}
        </span>
        <span style={{ fontSize: 10, color: C.textMuted }}>live · LONG</span>
      </div>

      <PriceRow label="Entry" value={fmt(trade.entry)} />
      <PriceRow label="SL" value={slOn ? fmt(trade.sl) : "— (disabled)"} color={slOn ? C.red : C.textMuted} />
      <PriceRow label="TP" value={tpOn ? fmt(trade.tp) : "— (disabled)"} color={tpOn ? C.green : C.textMuted} />
      <PriceRow label="Qty" value={trade.qty != null ? `${trade.qty}` : "—"} color={C.textSec} />

      <LongDistanceBar entry={trade.entry} current={ltp} sl={slOn ? trade.sl : null} tp={tpOn ? trade.tp : null} />

      <div style={{ fontSize: 9, color: C.textMuted, marginTop: 2 }}>
        {slOn || tpOn
          ? "Exits on SL / TP, else at the next 3m candle close (time-exit)."
          : "Time-boxed — exits at the next 3m candle close (no SL/TP set)."}
      </div>
    </div>
  );
}

/* ─── Compact view (rail) ─── */
function CompactView({ mode, inTrade, livePnl, onBecomePrimary }) {
  return (
    <div onClick={onBecomePrimary} style={{
      height: "100%", display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", gap: spacing.lg,
      cursor: "pointer", padding: spacing.md, background: C.bgCard,
      border: `1px solid ${C.borderDim}`, borderRadius: 8,
    }}>
      <div style={{ writingMode: "vertical-rl", textOrientation: "mixed", transform: "rotate(180deg)",
        fontSize: 11, fontWeight: 800, color: C.v5, letterSpacing: "1.5px", textTransform: "uppercase" }}>
        SCALP V5
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
        <span style={{ width: 10, height: 10, borderRadius: "50%",
          background: inTrade ? C.v5 : C.textMuted,
          boxShadow: inTrade ? `0 0 8px ${C.v5}` : "none" }} />
        <span style={{ fontSize: 8, color: inTrade ? C.v5 : C.textMuted, fontWeight: 700 }}>
          {inTrade ? "LIVE" : "ARM"}
        </span>
      </div>
      {inTrade && (
        <span style={{ fontSize: 11, fontWeight: 800, fontFamily: MONO,
          color: livePnl > 0 ? C.green : livePnl < 0 ? C.red : C.textMuted,
          writingMode: "vertical-rl", transform: "rotate(180deg)" }}>
          {fmtPnL(livePnl)}
        </span>
      )}
      <div style={{ width: 1, flex: 1, background: C.borderDim }} />
      <div style={{ writingMode: "vertical-rl", transform: "rotate(180deg)",
        fontSize: 9, fontWeight: 600, textTransform: "uppercase", color: mode === "LIVE" ? C.red : C.green }}>
        {mode === "LIVE" ? "LIVE" : "PAPER"}
      </div>
    </div>
  );
}

/* ─── Main component ─── */
export default function ScalpV5Panel({ ltpMap, isPrimary, onBecomePrimary }) {
  const isMobile = useIsMobile();

  const [state, setState]                   = useState(null);
  const [strategyConfig, setStrategyConfig] = useState(null);

  useEffect(() => {
    async function loadFast() {
      try { setState(await getScalpV5State()); } catch {}
    }
    async function loadSlow() {
      try { setStrategyConfig(await getStrategyConfig(STRATEGY_ID)); } catch {}
    }
    loadFast(); loadSlow();
    const fast = setInterval(loadFast, 3000);
    const slow = setInterval(loadSlow, 15000);
    return () => { clearInterval(fast); clearInterval(slow); };
  }, []);

  const mode       = state?.mode || strategyConfig?.trade_execution_mode || "PAPER";
  const dayBlocked = !!state?.day_blocked;
  const openTrade  = state?.open_trade || null;
  const inTrade    = !!openTrade;

  // Surveillance rows (2 CE + 2 PE).
  const rows = useMemo(() => {
    if (!state?.selection) return [];
    const out = [];
    (state.selection.CE || []).slice(0, 2).forEach((o, i) =>
      out.push({ ...o, side: "CE", idx: i + 1, tradingsymbol: o.tradingsymbol || o.symbol, strike: o.strike }));
    (state.selection.PE || []).slice(0, 2).forEach((o, i) =>
      out.push({ ...o, side: "PE", idx: i + 1, tradingsymbol: o.tradingsymbol || o.symbol, strike: o.strike }));
    return out;
  }, [state]);

  // Live LONG P&L for header + compact view.
  const livePnl = useMemo(() => {
    if (!openTrade || !ltpMap) return 0;
    const ltp = ltpMap[normalizeSymbol(openTrade.symbol)];
    return longPnl(openTrade.entry, openTrade.qty, ltp) ?? 0;
  }, [openTrade, ltpMap]);

  /* ── Compact (rail) ── */
  if (!isPrimary) {
    return <CompactView mode={mode} inTrade={inTrade} livePnl={livePnl} onBecomePrimary={onBecomePrimary} />;
  }

  /* ── Primary ── */
  return (
    <div style={{
      background: C.bg, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden",
      display: "flex", flexDirection: "column", height: "100%", fontFamily: FONT,
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: spacing.md, padding: "10px 14px",
        background: C.bgCard, borderBottom: `1px solid ${C.borderDim}`, flexShrink: 0, flexWrap: "wrap" }}>
        <div style={{ fontSize: 12, fontWeight: 800, color: C.v5, letterSpacing: "1px", textTransform: "uppercase" }}>
          SCALP V5
        </div>
        <div style={{ fontSize: 11, color: C.textMuted }}>Buy · 3m · EMA20H exit</div>
        <div style={{ flex: 1 }} />
        {dayBlocked && (
          <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 9px", borderRadius: 4,
            background: C.redDim, color: C.red, border: `1px solid ${C.red}40`, textTransform: "uppercase" }}>
            ⛔ Day Blocked
          </span>
        )}
        <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 9px", borderRadius: 4,
          background: inTrade ? C.v5Dim : C.bgSurf, color: inTrade ? C.v5 : C.textMuted,
          border: `1px solid ${inTrade ? C.v5 : C.borderDim}`, textTransform: "uppercase" }}>
          {inTrade ? "● In Trade" : "○ Armed"}
        </span>
        {inTrade && (
          <span style={{ fontSize: 13, fontWeight: 800, fontFamily: MONO,
            color: livePnl > 0 ? C.green : livePnl < 0 ? C.red : C.textMuted }}>
            {fmtPnL(livePnl)}
          </span>
        )}
        <ModeBadge mode={mode} />
      </div>

      {/* Config strip */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, padding: "6px 14px",
        background: C.bgSurf, borderBottom: `1px solid ${C.borderDim}`, flexShrink: 0, flexWrap: "wrap" }}>
        {[
          { label: "Premium", value: `₹${strategyConfig?.option_premium?.min ?? "—"}–₹${strategyConfig?.option_premium?.max ?? "—"}` },
          { label: "SL pts",  value: strategyConfig?.sl_points ? strategyConfig.sl_points : "off" },
          { label: "TP pts",  value: strategyConfig?.tp_points ? strategyConfig.tp_points : "off" },
          { label: "Lots",    value: strategyConfig?.quantity?.lots ?? "—" },
        ].map((s, i) => (
          <div key={i} style={{ display: "flex", flexDirection: "column", gap: 1, flexShrink: 0 }}>
            <span style={{ fontSize: 8, color: C.textMuted, letterSpacing: "0.5px", textTransform: "uppercase", fontWeight: 600 }}>{s.label}</span>
            <span style={{ fontSize: 12, fontWeight: 700, color: C.text, fontFamily: MONO }}>{s.value}</span>
          </div>
        ))}
      </div>

      {/* Body */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: spacing.md, padding: spacing.md,
        minHeight: 0, overflowY: "auto" }}>

        {/* Hero trade card (when open) */}
        {inTrade && <HeroTradeCard trade={openTrade} ltpMap={ltpMap} />}

        {/* Surveillance row */}
        <div>
          <div style={{ fontSize: 9, color: C.textMuted, textTransform: "uppercase", letterSpacing: "0.5px",
            fontWeight: 700, marginBottom: spacing.sm }}>
            Under Surveillance · {rows.length} strike{rows.length !== 1 ? "s" : ""}
          </div>
          {rows.length === 0 ? (
            <EmptyState icon="📊" title="No strikes selected"
              description="Selected CE/PE strikes will appear here once V5 picks them." />
          ) : (
            <div style={{ display: "flex", gap: spacing.md, flexWrap: isMobile ? "wrap" : "nowrap" }}>
              {rows.map((r, i) => {
                const sym = r.tradingsymbol ? normalizeSymbol(r.tradingsymbol) : null;
                const ltp = sym ? ltpMap[sym] : null;
                return (
                  <div key={`${r.side}_${r.idx}_${i}`} style={{ flex: isMobile ? "1 1 100%" : "1 1 0%", minWidth: isMobile ? "100%" : 180 }}>
                    <SurveillanceCard row={r} ltp={ltp} />
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Footer legend */}
      <div style={{ borderTop: `1px solid ${C.borderDim}`, padding: "6px 14px", background: C.bgCard, flexShrink: 0,
        display: "flex", gap: spacing.lg, alignItems: "center" }}>
        <span style={{ fontSize: 9, color: C.textMuted }}>
          entry = Buy Long on EMA8↗EMA20H · exit when candle closes &lt; EMA20H · SL/TP optional
        </span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 9, color: C.textMuted }}>1 trade at a time · {mode}</span>
      </div>
    </div>
  );
}