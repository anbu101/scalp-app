/**
 * SCALP PANEL  (redesigned to match ScalpV2Panel's card language)
 *
 * Intended path: src/strategies/scalp/ScalpPanel.jsx
 *
 * Visual parity with ScalpV2Panel:
 *   - Header (label · status · live P&L · mode) + config/mode strip
 *   - A ROW OF SLOT CARDS (instead of the old 12-col table), each with a
 *     colored top-border accent, a state badge, a "selected contract" line,
 *     and an HA-style Entry/SL/TP + distance bar + live P&L when in trade.
 *   - Idle cards show the SELECTED contract under surveillance (symbol, strike,
 *     live LTP) — mirrors the V2 "under surveillance" requirement.
 *
 * SCALP_V1 differences from V2 (handled here):
 *   - LONG (buy): P&L = (ltp - entry) * qty; SL below entry, TP above.
 *   - Up to 4 independent slots (CE_1/CE_2/PE_1/PE_2), filtered by CE/BOTH/PE.
 *   - Accent = amber (V1 identity); V2 is violet — siblings, still distinct.
 *
 * PRESERVED behaviors (unchanged logic, just re-homed into cards):
 *   CE/BOTH/PE toggle, audio alerts, toast notifications, activity feed,
 *   P&L pulse/flash, sparkline trend, 3s/15s polling.
 *
 * Props: ltpMap, isPrimary, onBecomePrimary
 */

import { useEffect, useState, useMemo, useRef } from "react";
import { useIsMobile } from "../../hooks/useIsMobile";
import {
  getTradeState,
  getActiveTrade,
  getLogs,
  getCurrentSelection,
  getStrategyConfig,
} from "../../api";
import { getTradeSideMode, setTradeSideMode } from "../../api";
import { EmptyState } from "../../components/LoadingStates";
import { PnLTrendArrow } from "../../components/DataVisualization";
import { colors, spacing } from "../../tokens";

const STRATEGY_ID   = "SCALP_V1";
const ACTIVE_STATES = ["BUY_PLACED", "PROTECTED", "BUY_FILLED", "IN_TRADE"];

/* ─── Tokens (aligned with ScalpV2Panel's C system) ───────────── */
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
  // SCALP_V1 accent — amber (its dashboard identity)
  scalp:     colors.warning        ?? "#f59e0b",
  scalpDim:  "rgba(245,158,11,0.13)",
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
const formatTimestamp = (timestamp) => {
  if (!timestamp) return "—";
  const date  = new Date(timestamp);
  const today = new Date();
  const isToday =
    date.getDate()     === today.getDate()  &&
    date.getMonth()    === today.getMonth() &&
    date.getFullYear() === today.getFullYear();
  return isToday
    ? date.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false })
    : date.toLocaleString("en-IN", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
};

/* ─── Small atoms (match V2 panel) ────────────────────────────── */
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

function SlotStateBadge({ state }) {
  const active = ACTIVE_STATES.includes(state);
  if (active) {
    return (
      <span style={{ fontSize: 10, fontWeight: 600, padding: "2px 8px", borderRadius: 4,
        background: C.amberDim, color: C.amber, border: `1px solid ${C.amber}`, textTransform: "uppercase" }}>
        ● {state === "IN_TRADE" ? "In Trade" : state.replace("_", " ")}
      </span>
    );
  }
  // exited states get colored; ARMED is neutral
  if (state === "TP_HIT")      return <span style={badgeStyle(C.green, C.greenDim)}>TP Hit</span>;
  if (state === "SL_HIT")      return <span style={badgeStyle(C.red, C.redDim)}>SL Hit</span>;
  if (state === "EXITED" || state === "CLOSED") return <span style={badgeStyle(C.textMuted, C.bgSurf)}>Closed</span>;
  return (
    <span style={{ fontSize: 10, fontWeight: 600, padding: "2px 8px", borderRadius: 4,
      background: C.bgSurf, color: C.textMuted, border: `1px solid ${C.borderDim}`, textTransform: "uppercase" }}>
      ○ Armed
    </span>
  );
}
function badgeStyle(col, dim) {
  return { fontSize: 10, fontWeight: 600, padding: "2px 8px", borderRadius: 4,
    background: dim, color: col, border: `1px solid ${col}`, textTransform: "uppercase" };
}

/* ─── PriceRow (match V2/HA) ──────────────────────────────────── */
function PriceRow({ label, value, color, mono = true, sub }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline",
      padding: "3px 0", borderBottom: `1px solid ${C.borderDim}` }}>
      <span style={{ fontSize: 11, color: C.textMuted }}>{label}</span>
      <div style={{ textAlign: "right" }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: color ?? C.text, fontFamily: mono ? MONO : FONT }}>{value}</span>
        {sub && <div style={{ fontSize: 9, color: C.textMuted, marginTop: 1 }}>{sub}</div>}
      </div>
    </div>
  );
}

/* ─── Distance bar — LONG-aware (entry low→high: SL..TP) ──────── */
function DistanceBar({ entry, current, sl, tp }) {
  if (!entry || !current || !sl || !tp) return null;
  // LONG: sl BELOW entry, tp ABOVE. Range = tp - sl. Near tp → 100.
  const range = tp - sl;
  if (range <= 0) return null;
  const pct = Math.max(0, Math.min(100, ((current - sl) / range) * 100));
  const barColor = pct < 20 ? C.red : pct > 80 ? C.green : C.amber;
  return (
    <div style={{ margin: "8px 0 4px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: C.textMuted, marginBottom: 3 }}>
        <span>SL {fmt(sl)}</span>
        <span style={{ color: C.textSec, fontSize: 10, fontWeight: 600, fontFamily: MONO }}>{fmt(current)}</span>
        <span>TP {fmt(tp)}</span>
      </div>
      <div style={{ height: 4, background: C.borderDim, borderRadius: 2, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: barColor, borderRadius: 2, transition: "width 0.5s ease" }} />
      </div>
    </div>
  );
}

/* ─── SlotCard — one card per CE/PE slot (V2 card anatomy) ────── */
function SlotCard({ row, slot, ltp, pnl, history, flash, pulse, lotSize }) {
  const state   = slot ? slot.state : "ARMED";
  const inTrade = slot && ACTIVE_STATES.includes(state);
  const accent  = flash === "tp" ? C.green
                : flash === "sl" ? C.red
                : flash === "enter" ? C.blue
                : inTrade ? C.scalp : C.borderDim;
  const symbol  = row.tradingsymbol;

  return (
    <div style={{
      flex: 1, minWidth: 0,
      background: C.bgCard,
      border: `1px solid ${inTrade ? accent : C.borderDim}`,
      borderTop: `3px solid ${accent}`,
      borderRadius: 8,
      padding: spacing.md,
      display: "flex", flexDirection: "column", gap: spacing.sm,
      transition: "border-color 0.35s ease",
    }}>
      {/* Header: side + slot idx + state */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <SideBadge side={row.side} />
          <span style={{ fontSize: 11, fontWeight: 700, color: C.textSec }}>#{row.idx}</span>
        </div>
        <SlotStateBadge state={state} />
      </div>

      {/* Strike + time line */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
        fontSize: 10, color: C.textMuted, borderBottom: `1px solid ${C.borderDim}`, paddingBottom: spacing.sm }}>
        <span>Strike {row.strike ?? "—"}</span>
        <span>{formatTimestamp(row.selected_at)}</span>
      </div>

      {/* Selected contract (always shown — surveillance) */}
      <div style={{ fontSize: 12, fontWeight: 700, color: symbol ? C.text : C.textMuted, fontFamily: MONO,
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={symbol}>
        {symbol || "No contract selected"}
      </div>

      {inTrade ? (
        <>
          {/* LTP + live P&L with pulse */}
          <div
            key={`pnl-${symbol}-${pulse ?? "0"}`}
            style={{
              display: "flex", alignItems: "baseline", gap: 8, marginBottom: 2,
              borderRadius: 5, padding: "2px 4px",
              animation: pulse === "up" ? "scalpFlashGreen 0.55s ease"
                       : pulse === "dn" ? "scalpFlashRed 0.55s ease" : "none",
            }}
          >
            <span style={{ fontSize: 20, fontWeight: 700, fontFamily: MONO,
              color: pnl != null ? (pnl > 0 ? C.green : pnl < 0 ? C.red : C.text) : C.text }}>
              {ltp != null ? fmt(ltp) : "—"}
            </span>
            {pnl != null && (
              <span style={{ fontSize: 12, fontWeight: 700, fontFamily: MONO,
                color: pnl > 0 ? C.green : pnl < 0 ? C.red : C.textMuted }}>
                {fmtPnL(pnl)}
              </span>
            )}
            {history && history.length > 1 && (
              <span style={{ marginLeft: "auto" }}><PnLTrendArrow history={history} /></span>
            )}
          </div>
          <PriceRow label="Entry" value={fmt(slot.buy_price)} />
          <PriceRow label="SL"    value={fmt(slot.sl_price)} color={C.red} />
          <PriceRow label="TP"    value={fmt(slot.tp_price)} color={C.green} />
          <PriceRow label="Qty"   value={slot.qty != null ? `${slot.qty}` : "—"} color={C.textSec} />
          <DistanceBar entry={slot.buy_price} current={ltp} sl={slot.sl_price} tp={slot.tp_price} />
        </>
      ) : (
        /* Armed / surveillance view */
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <span style={{ fontSize: 9, color: C.textMuted, textTransform: "uppercase", letterSpacing: "0.5px" }}>Live LTP</span>
            <span style={{ fontSize: 18, fontWeight: 700, fontFamily: MONO, color: ltp != null ? C.text : C.textMuted }}>
              {ltp != null ? fmt(ltp) : "—"}
            </span>
          </div>
          <div style={{ fontSize: 9, color: C.textMuted, marginTop: 2 }}>
            {symbol ? "Under surveillance — awaiting entry signal" : "Waiting for selection"}
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── Compact view (collapsed in grid) — match V2 dot pattern ── */
function CompactView({ mode, inTrade, livePnl, onBecomePrimary }) {
  return (
    <div onClick={onBecomePrimary} style={{
      height: "100%", display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", gap: spacing.lg,
      cursor: "pointer", padding: spacing.md, background: C.bgCard,
      border: `1px solid ${C.borderDim}`, borderRadius: 8,
    }}>
      <div style={{ writingMode: "vertical-rl", textOrientation: "mixed", transform: "rotate(180deg)",
        fontSize: 11, fontWeight: 800, color: C.scalp, letterSpacing: "1.5px", textTransform: "uppercase" }}>
        SCALP
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
        <span style={{ width: 10, height: 10, borderRadius: "50%",
          background: inTrade ? C.scalp : C.textMuted,
          boxShadow: inTrade ? `0 0 8px ${C.scalp}` : "none" }} />
        <span style={{ fontSize: 8, color: inTrade ? C.scalp : C.textMuted, fontWeight: 700 }}>
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

/* ─── ModeToggle (CE/BOTH/PE) — preserved control ─────────────── */
function SideModeToggle({ value, onChange, compact }) {
  return (
    <div style={{ display: "flex", gap: 3, background: C.bg, padding: 3, borderRadius: 6 }}>
      {["BOTH", "CE", "PE"].map((m) => {
        const active = value === m;
        return (
          <button key={m} onClick={() => onChange(m)}
            style={{
              padding: compact ? "5px 12px" : "6px 14px", borderRadius: 4, border: "none",
              background: active ? C.scalp : "transparent",
              color: active ? "#0a0f1e" : C.textMuted,
              fontSize: compact ? 11 : 12, fontWeight: 700, cursor: "pointer",
              transition: "all 0.2s ease",
            }}>
            {m === "BOTH" ? "CE+PE" : m}
          </button>
        );
      })}
    </div>
  );
}

/* ─── Main component ─────────────────────────────────────────── */
export default function ScalpPanel({ ltpMap, isPrimary, onBecomePrimary }) {
  const isMobile = useIsMobile();

  const [selection,      setSelection]       = useState(null);
  const [tradeState,     setTradeState]       = useState(null);
  const [trade,          setTrade]            = useState(null);
  const [logs,           setLogs]             = useState([]);
  const [tradeSideMode,  setTradeSideModeLocal] = useState("BOTH");
  const [strategyConfig, setStrategyConfig]   = useState(null);

  const [prevTradeState, setPrevTradeState] = useState(null);
  const [pnlHistory,     setPnlHistory]     = useState({});
  const [slotFlash,      setSlotFlash]      = useState({});
  const prevPnlRef       = useRef({});
  const [pnlPulse,       setPnlPulse]       = useState({});
  const [activityFeed,   setActivityFeed]   = useState([]);

  /* ── Polling (unchanged) ── */
  useEffect(() => {
    async function loadFast() {
      try { setTrade(await getActiveTrade()); } catch {}
      try { setTradeState(await getTradeState("SCALP_V1")); } catch {}
      try { setSelection(await getCurrentSelection(STRATEGY_ID)); } catch {}
    }
    async function loadSlow() {
      try { setStrategyConfig(await getStrategyConfig("SCALP_V1")); } catch {}
      try { const l = await getLogs(); setLogs(Array.isArray(l) ? l : l?.logs || []); } catch {}
      try { const res = await getTradeSideMode(); setTradeSideModeLocal(res?.mode || "BOTH"); } catch {}
    }
    loadFast(); loadSlow();
    const fast = setInterval(loadFast, 3000);
    const slow = setInterval(loadSlow, 15000);
    return () => { clearInterval(fast); clearInterval(slow); };
  }, []);

  /* ── PnL history for sparklines (unchanged logic) ── */
  useEffect(() => {
    if (!tradeState || !ltpMap || Object.keys(ltpMap).length === 0) return;
    setPnlHistory((prev) => {
      const updated = { ...prev }; let hasChanges = false;
      Object.entries(tradeState).forEach(([, state]) => {
        if (!state || typeof state !== "object") return;
        const symbol = state.symbol;
        const liveLtp = ltpMap[symbol];
        const buyPrice = state.buy_price; const qty = state.qty;
        if (!symbol || !ACTIVE_STATES.includes(state.state) ||
            typeof buyPrice !== "number" || typeof liveLtp !== "number" || typeof qty !== "number") return;
        const pnl = (liveLtp - buyPrice) * qty;
        const history = updated[symbol] || [];
        const last = history[history.length - 1];
        if (last !== pnl) {
          updated[symbol] = [...history, pnl].slice(-10); hasChanges = true;
          const dir = pnl > (prevPnlRef.current[symbol] ?? pnl) ? "up" : "dn";
          prevPnlRef.current[symbol] = pnl;
          setPnlPulse((p) => ({ ...p, [symbol]: dir }));
          setTimeout(() => setPnlPulse((p) => ({ ...p, [symbol]: null })), 600);
        }
      });
      return hasChanges ? updated : prev;
    });
  }, [tradeState, ltpMap]);

  /* ── Audio + toast + activity feed on transitions (unchanged) ── */
  useEffect(() => {
    if (!tradeState || !prevTradeState) { setPrevTradeState(tradeState); return; }
    Object.entries(tradeState).forEach(([slot, currentState]) => {
      const prevState = prevTradeState[slot];
      if (!prevState || !currentState) return;
      const curr = typeof currentState === "object" ? currentState.state : currentState;
      const prev = typeof prevState === "object" ? prevState.state : prevState;
      if (curr === prev) return;
      const symbol = typeof currentState === "object" ? currentState.symbol : slot;
      const price  = typeof currentState === "object" ? currentState.buy_price : null;
      const pnl    = typeof currentState === "object" ? (currentState.realized_pnl ?? currentState.pnl) : null;

      const pushActivity = (type, icon, label) => {
        setActivityFeed((prev) => [{ id: Date.now() + Math.random(), time: new Date(), type, icon, label, symbol, pnl, price, slot }, ...prev].slice(0, 50));
      };
      const flash = (kind) => {
        setSlotFlash((f) => ({ ...f, [slot]: kind }));
        setTimeout(() => setSlotFlash((f) => { const n = { ...f }; delete n[slot]; return n; }), 900);
      };

      if (prev === "ARMED" && (curr === "BUY_PLACED" || curr === "BUY_FILLED" || curr === "PROTECTED" || curr === "IN_TRADE")) {
        flash("enter"); pushActivity("enter", "🎯", "Entered");
      }
      if (ACTIVE_STATES.includes(prev) && (curr === "SL_HIT" || curr === "TP_HIT" || curr === "EXITED" || curr === "CLOSED")) {
        if (curr === "SL_HIT") {
          flash("sl"); pushActivity("sl", "🔴", "SL Hit");
        } else if (curr === "TP_HIT") {
          flash("tp"); pushActivity("tp", "🎉", "TP Hit");
        } else {
          if (pnl && pnl > 0) { flash("tp"); pushActivity("exit", "✅", "Closed +"); }
          else { flash("sl"); pushActivity("exit", "⚪", "Closed"); }
        }
      }
    });
    setPrevTradeState(tradeState);
  }, [tradeState]);

  /* ── Derived ── */
  const activeTradeBySymbol = useMemo(() => {
    if (!tradeState) return {};
    const map = {};
    Object.entries(tradeState).forEach(([slot, t]) => {
      if (t && typeof t === "object" && t.symbol) map[normalizeSymbol(t.symbol)] = { ...t, slot };
    });
    return map;
  }, [tradeState]);

  const rows = useMemo(() => {
    if (!selection) return [];
    const result = [];
    const ceSlots = ["CE_1", "CE_2"]; const peSlots = ["PE_1", "PE_2"];
    if (tradeSideMode !== "PE") ceSlots.forEach((slot, i) => {
      const o = selection.CE?.[i];
      result.push({ ...(o || {}), side: "CE", idx: i + 1, slot, tradingsymbol: o?.tradingsymbol || null, strike: o?.strike || null, selected_at: o?.selected_at || null });
    });
    if (tradeSideMode !== "CE") peSlots.forEach((slot, i) => {
      const o = selection.PE?.[i];
      result.push({ ...(o || {}), side: "PE", idx: i + 1, slot, tradingsymbol: o?.tradingsymbol || null, strike: o?.strike || null, selected_at: o?.selected_at || null });
    });
    return result;
  }, [selection, tradeSideMode]);

  const inTrade = useMemo(() => {
    if (!tradeState) return false;
    return Object.values(tradeState).some((v) => typeof v === "object" ? ACTIVE_STATES.includes(v.state) : v === "IN_TRADE");
  }, [tradeState]);

  const executionMode = strategyConfig?.trade_execution_mode || "LIVE";

  const livePnl = useMemo(() => {
    if (!tradeState || !ltpMap) return 0;
    return Object.values(tradeState).reduce((sum, slot) => {
      if (!slot || typeof slot !== "object") return sum;
      if (!ACTIVE_STATES.includes(slot.state)) return sum;
      const ltp = ltpMap[normalizeSymbol(slot.symbol)];
      if (typeof slot.buy_price !== "number" || typeof ltp !== "number") return sum;
      return sum + (ltp - slot.buy_price) * safeNum(slot.qty);
    }, 0);
  }, [tradeState, ltpMap]);

  const lotSize = strategyConfig?.quantity?.lot_size ?? 65;

  /* ── Compact ── */
  if (!isPrimary) {
    return <CompactView mode={executionMode} inTrade={inTrade} livePnl={livePnl} onBecomePrimary={onBecomePrimary} />;
  }

  /* ── Per-row card data ── */
  const cardFor = (r) => {
    const sym  = r.tradingsymbol ? normalizeSymbol(r.tradingsymbol) : null;
    const slot = activeTradeBySymbol[sym] || null;
    const ltp  = sym ? ltpMap[sym] : null;
    const hist = sym ? pnlHistory[sym] : null;
    let pnl = null;
    if (slot && ACTIVE_STATES.includes(slot.state) && typeof slot.buy_price === "number" && typeof ltp === "number") {
      pnl = (ltp - slot.buy_price) * (slot.qty || 0);
    }
    return { slot, ltp, pnl, history: hist, flash: slotFlash[r.slot], pulse: sym ? pnlPulse[sym] : null };
  };

  /* ── Primary ── */
  return (
    <div style={{
      background: C.bg, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden",
      display: "flex", flexDirection: "column", height: "100%", fontFamily: FONT,
    }}>
      <style>{`
        @keyframes scalpFlashGreen { 0% { background: rgba(16,185,129,0.45); } 100% { background: transparent; } }
        @keyframes scalpFlashRed   { 0% { background: rgba(239,68,68,0.45); }  100% { background: transparent; } }
      `}</style>

      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: spacing.md, padding: "10px 14px",
        background: C.bgCard, borderBottom: `1px solid ${C.borderDim}`, flexShrink: 0, flexWrap: "wrap" }}>
        <div style={{ fontSize: 12, fontWeight: 800, color: C.scalp, letterSpacing: "1px", textTransform: "uppercase" }}>
          SCALP
        </div>
        <div style={{ fontSize: 11, color: C.textMuted }}>Intraday CE/PE · 1m · Zerodha</div>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 9px", borderRadius: 4,
          background: inTrade ? C.amberDim : C.bgSurf, color: inTrade ? C.amber : C.textMuted,
          border: `1px solid ${inTrade ? C.amber : C.borderDim}`, textTransform: "uppercase" }}>
          {inTrade ? "● In Trade" : "○ Armed"}
        </span>
        {inTrade && (
          <span style={{ fontSize: 13, fontWeight: 800, fontFamily: MONO,
            color: livePnl > 0 ? C.green : livePnl < 0 ? C.red : C.textMuted }}>
            {fmtPnL(livePnl)}
          </span>
        )}
        <ModeBadge mode={executionMode} />
      </div>

      {/* Config / mode strip */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, padding: "6px 14px",
        background: C.bgSurf, borderBottom: `1px solid ${C.borderDim}`, flexShrink: 0, flexWrap: "wrap" }}>
        {[
          { label: "Premium", value: `₹${strategyConfig?.option_premium?.min ?? "—"}–₹${strategyConfig?.option_premium?.max ?? "—"}` },
          { label: "R:R",     value: `1 : ${strategyConfig?.risk_reward_ratio ?? "—"}` },
          { label: "Lots",    value: strategyConfig?.quantity?.lots ?? "—" },
          { label: "Slots",   value: rows.length },
        ].map((s, i) => (
          <div key={i} style={{ display: "flex", flexDirection: "column", gap: 1, flexShrink: 0 }}>
            <span style={{ fontSize: 8, color: C.textMuted, letterSpacing: "0.5px", textTransform: "uppercase", fontWeight: 600 }}>{s.label}</span>
            <span style={{ fontSize: 12, fontWeight: 700, color: C.text, fontFamily: MONO }}>{s.value}</span>
          </div>
        ))}
        <div style={{ flex: 1 }} />
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 8, color: C.textMuted, letterSpacing: "0.5px", textTransform: "uppercase", fontWeight: 600 }}>Mode</span>
          <SideModeToggle
            value={tradeSideMode}
            compact
            onChange={async (m) => { setTradeSideModeLocal(m); try { await setTradeSideMode(m); } catch {} }}
          />
        </div>
      </div>

      {/* Slot cards */}
      {rows.length === 0 ? (
        <div style={{ flex: 1, minHeight: 160 }}>
          <EmptyState icon="📊" title="No slots selected" description="Selected contracts will appear here as cards once the strategy picks them." />
        </div>
      ) : (
        <div style={{ flex: 1, display: "flex", gap: spacing.md, padding: spacing.md, minHeight: 0,
          overflowX: "auto", overflowY: "auto", flexWrap: isMobile ? "wrap" : "nowrap" }}>
          {rows.map((r) => {
            const cd = cardFor(r);
            return (
              <div key={r.slot} style={{ flex: isMobile ? "1 1 100%" : "1 1 0%", minWidth: isMobile ? "100%" : 200 }}>
                <SlotCard row={r} slot={cd.slot} ltp={cd.ltp} pnl={cd.pnl} history={cd.history}
                  flash={cd.flash} pulse={cd.pulse} lotSize={lotSize} />
              </div>
            );
          })}
        </div>
      )}

      {/* Activity feed (preserved) */}
      {activityFeed.length > 0 && (
        <div style={{ borderTop: `1px solid ${C.borderDim}`, background: C.bgCard, flexShrink: 0 }}>
          <div style={{ padding: "6px 14px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <span style={{ fontSize: 9, color: C.textMuted, letterSpacing: "0.5px", textTransform: "uppercase", fontWeight: 600 }}>
              Activity Feed
            </span>
            <button onClick={() => setActivityFeed([])}
              style={{ background: "none", border: "none", color: C.textMuted, fontSize: 11, cursor: "pointer", padding: "2px 6px" }}>
              Clear
            </button>
          </div>
          <div style={{ maxHeight: 140, overflowY: "auto" }}>
            {activityFeed.map((entry) => {
              const pnlStr = entry.pnl != null ? ` · ${entry.pnl > 0 ? "+" : ""}₹${Math.round(entry.pnl).toLocaleString("en-IN")}` : "";
              const priceStr = entry.price != null ? ` @ ₹${entry.price.toFixed ? entry.price.toFixed(2) : entry.price}` : "";
              const feedColor = entry.type === "tp" ? C.green : entry.type === "sl" ? C.red : entry.type === "enter" ? C.blue : C.textSec;
              return (
                <div key={entry.id} style={{ display: "flex", alignItems: "center", gap: spacing.sm,
                  padding: `5px 14px`, borderTop: `1px solid ${C.borderDim}` }}>
                  <span style={{ fontSize: 13, flexShrink: 0 }}>{entry.icon}</span>
                  <span style={{ fontSize: 11, fontWeight: 600, color: feedColor, minWidth: 56 }}>{entry.label}</span>
                  <span style={{ fontSize: 11, fontFamily: MONO, color: C.textSec, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {entry.symbol}<span style={{ color: feedColor, fontWeight: 600 }}>{pnlStr || priceStr}</span>
                  </span>
                  <span style={{ fontSize: 10, fontFamily: MONO, color: C.textMuted, flexShrink: 0 }}>
                    {entry.time.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Footer legend */}
      <div style={{ borderTop: `1px solid ${C.borderDim}`, padding: "6px 14px", background: C.bgCard, flexShrink: 0,
        display: "flex", gap: spacing.lg, alignItems: "center" }}>
        <span style={{ fontSize: 9, color: C.textMuted }}>
          <span style={{ color: C.green }}>●</span> CE · <span style={{ color: C.red }}>●</span> PE · entry = buy (LONG)
        </span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 9, color: C.textMuted }}>Up to 4 slots · {executionMode}</span>
      </div>
    </div>
  );
}