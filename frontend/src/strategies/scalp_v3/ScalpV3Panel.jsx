/**
 * SCALP V3 PANEL  (option-BUYING hedge — TEST strategy)
 *
 * Intended path: src/strategies/scalp_v3/ScalpV3Panel.jsx
 *
 * SCALP_V3 is fundamentally different from V1/V2 and needs its own visual story:
 *   - The contract that FIRES the signal (e.g. 24500CE) is TRACKED, never traded.
 *   - V3 BUYS the highest-premium OPPOSITE-side option (e.g. 24450PE) — the hedge.
 *   - Exit fires when the SIGNAL contract hits its own SL/TP, or the hedge's
 *     own SL-only GTT fires.
 *   - At most ONE open trade at a time (DB-backed global gate).
 *
 * So this panel is NOT a slot grid. It is:
 *   - A SURVEILLANCE ROW: the 2 CE + 2 PE selected strikes, each with live LTP.
 *   - A HERO TRADE CARD (when open): shows BOTH legs side by side —
 *       SIGNAL (tracked): LTP + SL/TP + distance bar (this drives the exit)
 *       HEDGE  (bought):  entry + SL + live LTP + live LONG P&L
 *
 * Data: getScalpV3State() → { mode, selection:{CE,PE}, open_trade } and the
 * shared ltpMap prop (same as the other panels) for live prices.
 *
 * Props: ltpMap, isPrimary, onBecomePrimary  (matches ScalpPanel contract)
 */

import { useEffect, useState, useMemo } from "react";
import { useIsMobile } from "../../hooks/useIsMobile";
import { getScalpV3State, getStrategyConfig } from "../../api";
import { EmptyState } from "../../components/LoadingStates";
import { colors, spacing } from "../../tokens";
import { useEntitlements } from "../../hooks/useEntitlements";   // ── UI_MASK ──
import { stratName } from "../displayNames";                      // ── UI_MASK ──

const STRATEGY_ID = "SCALP_V3";

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
  // SCALP_V3 accent — pink/green (option-buying identity; distinct from V1 amber, V2 violet)
  v3:        "#ec4899",
  v3Dim:     "rgba(236,72,153,0.13)",
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

/* Hedge is ALWAYS LONG (a bought option): P&L = (ltp - entry) * qty. */
function hedgePnl(hedge, ltp) {
  if (!hedge) return null;
  const entry = safeNum(hedge.entry);
  const qty   = safeNum(hedge.qty);
  if (!entry || typeof ltp !== "number") return null;
  return (ltp - entry) * qty;
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

/* Signal distance bar — the SIGNAL contract uses SCALP_V1 SHORT semantics:
 * SL ABOVE entry, TP BELOW entry. Price near TP (falling) → 100 (about to
 * trigger the profit-exit of the hedge). This bar shows how close the TRACKED
 * contract is to firing an exit. */
function SignalDistanceBar({ current, sl, tp }) {
  if (current == null || !sl || !tp) return null;
  const range = Math.abs(sl - tp);
  if (range <= 0) return null;
  // SHORT-side signal: lower current → closer to TP (good for the trade)
  const pct = Math.max(0, Math.min(100, ((sl - current) / range) * 100));
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
      <div style={{ fontSize: 8, color: C.textMuted, marginTop: 3, textAlign: "center" }}>
        {Math.round(pct)}% toward TP-exit
      </div>
    </div>
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
        {symbol ? "Eligible" : "Waiting for selection"}
      </div>
    </div>
  );
}

/* ─── Hero trade card — BOTH legs of the open V3 trade ─── */
function HeroTradeCard({ trade, ltpMap, showParams = true }) {   // ── UI_MASK ──
  const sig   = trade.signal;
  const hedge = trade.hedge;

  const sigLtp   = sig?.symbol   ? ltpMap[normalizeSymbol(sig.symbol)]   ?? null : null;
  const hedgeLtp = hedge?.symbol ? ltpMap[normalizeSymbol(hedge.symbol)] ?? null : null;

  const pnl    = hedgePnl(hedge, hedgeLtp);
  const pnlCol = pnl == null ? C.textMuted : pnl > 0 ? C.green : pnl < 0 ? C.red : C.text;
  const hasGtt = !!hedge?.gtt_id;

  return (
    <div style={{
      background: C.bgCard,
      border: `1px solid ${C.v3}`,
      borderTop: `3px solid ${C.v3}`,
      borderRadius: 8, padding: spacing.md,
      display: "flex", flexDirection: "column", gap: spacing.sm,
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <span style={{ fontSize: 11, fontWeight: 800, color: C.v3, letterSpacing: "0.5px", textTransform: "uppercase" }}>
          ● Active Hedge Trade
        </span>
        <span style={{ fontSize: 10, fontWeight: 700, color: hasGtt ? C.green : C.amber }}>
          {hasGtt ? `✓ GTT ${String(hedge.gtt_id).slice(-4)}` : "⚠ No GTT (paper/pending)"}
        </span>
      </div>

      {/* Two-leg layout */}
      <div style={{ display: "flex", gap: spacing.md, flexWrap: "wrap" }}>

        {/* ── SIGNAL leg (tracked, never traded) ── */}
        {/* ── UI_MASK ── the signal-tracking leg IS the mechanism — admin only */}
        {showParams && (
        <div style={{ flex: 1, minWidth: 200, background: C.bgSurf, borderRadius: 6, padding: spacing.sm,
          border: `1px dashed ${C.border}` }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
            <span style={{ fontSize: 9, fontWeight: 700, color: C.textMuted, textTransform: "uppercase", letterSpacing: "0.5px" }}>
              📡 Tracking (signal)
            </span>
            {sig?.side && <SideBadge side={sig.side} />}
          </div>
          <div style={{ fontSize: 12, fontWeight: 700, color: C.text, fontFamily: MONO,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={sig?.symbol}>
            {sig?.symbol || "—"}
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginTop: 4 }}>
            <span style={{ fontSize: 18, fontWeight: 700, fontFamily: MONO, color: sigLtp != null ? C.text : C.textMuted }}>
              {sigLtp != null ? fmt(sigLtp) : "—"}
            </span>
            <span style={{ fontSize: 9, color: C.textMuted }}>live · never traded</span>
          </div>
          <SignalDistanceBar current={sigLtp} sl={sig?.sl} tp={sig?.tp} />
          <div style={{ fontSize: 9, color: C.textMuted, marginTop: 2 }}>
            Exit fires when this hits its SL / TP
          </div>
        </div>
        )}

        {/* ── HEDGE leg (bought, LONG) ── */}
        <div style={{ flex: 1, minWidth: 200, background: C.bgCard, borderRadius: 6, padding: spacing.sm,
          border: `1px solid ${C.v3}40` }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
            <span style={{ fontSize: 9, fontWeight: 700, color: C.v3, textTransform: "uppercase", letterSpacing: "0.5px" }}>
              {showParams ? "🛒 Bought (hedge)" : "Open position"}   {/* ── UI_MASK ── */}
            </span>
            <span style={{ fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 4,
              background: C.greenDim, color: C.green }}>↑ BUY</span>
          </div>
          <div style={{ fontSize: 12, fontWeight: 700, color: C.text, fontFamily: MONO,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={hedge?.symbol}>
            {hedge?.symbol || "—"}
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, margin: "4px 0 6px" }}>
            <span style={{ fontSize: 22, fontWeight: 700, fontFamily: MONO, color: hedgeLtp != null ? C.text : C.textMuted }}>
              {hedgeLtp != null ? fmt(hedgeLtp) : "—"}
            </span>
            <span style={{ fontSize: 13, fontWeight: 700, fontFamily: MONO, color: pnlCol }}>
              {fmtPnL(pnl)}
            </span>
          </div>
          <PriceRow label="Entry" value={fmt(hedge?.entry)} />
          {/* ── UI_MASK ── SL level + exit-mechanism note are admin-only */}
          {showParams && <PriceRow label="SL (−MaxSL)" value={fmt(hedge?.sl)} color={C.red} />}
          <PriceRow label="Qty" value={hedge?.qty != null ? `${hedge.qty}` : "—"} color={C.textSec} />
          {showParams && (
          <div style={{ fontSize: 9, color: C.textMuted, marginTop: 4 }}>
            No TP — exits via signal contract or own SL
          </div>
          )}
        </div>
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
        fontSize: 11, fontWeight: 800, color: C.v3, letterSpacing: "1.5px", textTransform: "uppercase" }}>
        SCALP V3
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
        <span style={{ width: 10, height: 10, borderRadius: "50%",
          background: inTrade ? C.v3 : C.textMuted,
          boxShadow: inTrade ? `0 0 8px ${C.v3}` : "none" }} />
        <span style={{ fontSize: 8, color: inTrade ? C.v3 : C.textMuted, fontWeight: 700 }}>
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
export default function ScalpV3Panel({ ltpMap, isPrimary, onBecomePrimary }) {
  // ── UI_MASK ── fail-OPEN until first license read (Phase 3 convention)
  const { loaded: licenseLoaded, isAdminUi } = useEntitlements();
  const showParams = !licenseLoaded || isAdminUi;
  const isMobile = useIsMobile();

  const [state, setState]                 = useState(null);
  const [strategyConfig, setStrategyConfig] = useState(null);

  useEffect(() => {
    async function loadFast() {
      try { setState(await getScalpV3State()); } catch {}
    }
    async function loadSlow() {
      try { setStrategyConfig(await getStrategyConfig(STRATEGY_ID)); } catch {}
    }
    loadFast(); loadSlow();
    const fast = setInterval(loadFast, 3000);
    const slow = setInterval(loadSlow, 15000);
    return () => { clearInterval(fast); clearInterval(slow); };
  }, []);

  const mode      = state?.mode || strategyConfig?.trade_execution_mode || "PAPER";
  const openTrade = state?.open_trade || null;
  const inTrade   = !!openTrade;

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

  // Live hedge P&L (LONG) for header + compact view.
  const livePnl = useMemo(() => {
    if (!openTrade?.hedge || !ltpMap) return 0;
    const ltp = ltpMap[normalizeSymbol(openTrade.hedge.symbol)];
    return hedgePnl(openTrade.hedge, ltp) ?? 0;
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
        <div style={{ fontSize: 12, fontWeight: 800, color: C.v3, letterSpacing: "1px", textTransform: "uppercase" }}>
          {showParams ? "SCALP V3" : stratName("SCALP_V3", false)}   {/* ── UI_MASK ── */}
        </div>
        <div style={{ fontSize: 11, color: C.textMuted }}>{showParams ? "Buy-hedge" : "NIFTY Options"}</div>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 9px", borderRadius: 4,
          background: inTrade ? C.v3Dim : C.bgSurf, color: inTrade ? C.v3 : C.textMuted,
          border: `1px solid ${inTrade ? C.v3 : C.borderDim}`, textTransform: "uppercase" }}>
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
          /* ── UI_MASK ── secret:true items are admin-only parameters */
          { label: "Premium", value: `₹${strategyConfig?.option_premium?.min ?? "—"}–₹${strategyConfig?.option_premium?.max ?? "—"}`, secret: true },
          { label: "Max SL",  value: strategyConfig?.max_sl_points ?? "—", secret: true },
          { label: "R:R",     value: `1 : ${strategyConfig?.risk_reward_ratio ?? "—"}`, secret: true },
          { label: "Lots",    value: strategyConfig?.quantity?.lots ?? "—" },
        ].filter((s) => showParams || !s.secret).map((s, i) => (
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
        {inTrade && <HeroTradeCard trade={openTrade} ltpMap={ltpMap} showParams={showParams} />}

        {/* Surveillance row */}
        <div>
          <div style={{ fontSize: 9, color: C.textMuted, textTransform: "uppercase", letterSpacing: "0.5px",
            fontWeight: 700, marginBottom: spacing.sm }}>
            Under Surveillance · {rows.length} strike{rows.length !== 1 ? "s" : ""}
          </div>
          {rows.length === 0 ? (
            <EmptyState icon="📊" title="No strikes selected"
              description="Selected CE/PE strikes will appear here once the strategy picks them." />
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
          signal = tracked · hedge = Buy Long
        </span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 9, color: C.textMuted }}>1 trade at a time · {mode}</span>
      </div>
    </div>
  );
}