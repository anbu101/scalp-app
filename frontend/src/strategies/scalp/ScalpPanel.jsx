/**
 * SCALP PANEL
 *
 * Intended path: src/strategies/scalp/ScalpPanel.jsx
 *
 * Owns everything specific to the SCALP_V1 strategy:
 *   - State: selection, tradeState, trade, logs, tradeSideMode, strategyConfig
 *   - Polling: loadFast (3s), loadSlow (15s) — independent from Dashboard
 *   - Derived: rows, inTrade, executionMode, activeTradeBySymbol
 *   - Rendering: strategy header, CE/PE switcher, active positions table, debug panel
 *   - Audio alerts and toast notifications on state transitions
 *
 * Props:
 *   ltpMap        {Object}   — live LTP data, polled globally in Dashboard, passed down
 *   isPrimary     {boolean}  — true = full expanded view, false = compact summary
 *   onBecomePrimary {fn}     — called when compact panel is clicked to expand
 *
 * What this does NOT own:
 *   - globalConfig / trade_on  → Dashboard
 *   - Zerodha status           → Dashboard
 *   - today's positions/P&L    → Dashboard
 *   - ltpMap / indices polling → Dashboard
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
import DebugPanel from "../../components/DebugPanel";
import {
  EmptyState,
} from "../../components/LoadingStates";
import { useToast } from "../../components/ToastNotifications";
import { PnLTrendArrow } from "../../components/DataVisualization";

const STRATEGY_ID = "SCALP_V1";

const ACTIVE_STATES = ["BUY_PLACED", "PROTECTED", "BUY_FILLED", "IN_TRADE"];

/* ----------------------------------
   Design Tokens
   Local copies — extract to src/tokens.js when Dashboard is refactored.
----------------------------------- */
const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 24,
};

const typography = {
  displaySmall:  { fontSize: 24, fontWeight: 600, lineHeight: 1.3 },
  headingLarge:  { fontSize: 18, fontWeight: 600, lineHeight: 1.4 },
  headingMedium: { fontSize: 16, fontWeight: 600, lineHeight: 1.4 },
  headingSmall:  { fontSize: 14, fontWeight: 600, lineHeight: 1.4 },
  bodyLarge:     { fontSize: 14, fontWeight: 400, lineHeight: 1.5 },
  bodyMedium:    { fontSize: 13, fontWeight: 400, lineHeight: 1.5 },
  bodySmall:     { fontSize: 12, fontWeight: 400, lineHeight: 1.4 },
  label:         { fontSize: 11, fontWeight: 500, lineHeight: 1.3, letterSpacing: "0.5px", textTransform: "uppercase" },
  mono:          { fontFamily: "'JetBrains Mono', 'Fira Code', monospace", fontVariantNumeric: "tabular-nums" },
};

const colors = {
  profit:    "#10b981",
  profitBg:  "rgba(16, 185, 129, 0.12)",
  loss:      "#ef4444",
  lossBg:    "rgba(239, 68, 68, 0.12)",
  neutral:   "#6b7280",
  primary:   "#3b82f6",
  success:   "#10b981",
  successBg: "rgba(16, 185, 129, 0.15)",
  warning:   "#f59e0b",
  warningBg: "rgba(245, 158, 11, 0.15)",
  danger:    "#ef4444",
  dangerBg:  "rgba(239, 68, 68, 0.15)",
  bg: {
    primary:  "#0a0f1e",
    secondary: "#111827",
    tertiary:  "#1f2937",
    elevated:  "#374151",
  },
  border: {
    light: "#374151",
    medium: "#4b5563",
    dark:  "#1f2937",
  },
  text: {
    primary:   "#f9fafb",
    secondary: "#d1d5db",
    tertiary:  "#9ca3af",
    muted:     "#6b7280",
  },
};

/* ----------------------------------
   Audio Alert System
   Moved from Dashboard — scalp-specific event sounds.
----------------------------------- */
const AudioAlerts = {
  context: null,

  init() {
    if (!this.context && typeof window !== "undefined") {
      this.context = new (window.AudioContext || window.webkitAudioContext)();
    }
  },

  playTone(frequency, duration, type = "sine") {
    this.init();
    if (!this.context) return;

    const oscillator = this.context.createOscillator();
    const gainNode   = this.context.createGain();

    oscillator.connect(gainNode);
    gainNode.connect(this.context.destination);

    oscillator.frequency.value = frequency;
    oscillator.type            = type;

    gainNode.gain.setValueAtTime(0.3, this.context.currentTime);
    gainNode.gain.exponentialRampToValueAtTime(0.01, this.context.currentTime + duration);

    oscillator.start(this.context.currentTime);
    oscillator.stop(this.context.currentTime + duration);
  },

  positionEntered() {
    this.playTone(800, 0.15);
    setTimeout(() => this.playTone(1000, 0.15), 150);
  },

  stopLossHit() {
    this.playTone(400, 0.2);
    setTimeout(() => this.playTone(350, 0.2), 200);
    setTimeout(() => this.playTone(300, 0.3), 400);
  },

  takeProfitHit() {
    this.playTone(600, 0.1);
    setTimeout(() => this.playTone(800, 0.1), 100);
    setTimeout(() => this.playTone(1000, 0.15), 200);
  },
};

/* ----------------------------------
   Small helpers
----------------------------------- */
function normalizeSymbol(sym) {
  if (!sym) return sym;
  return sym.replace(/\s+/g, "").toUpperCase();
}

const safeNum = (v) => (typeof v === "number" && !isNaN(v) ? v : 0);

const pnlStyle = (v) => ({
  color: v > 0 ? colors.profit : v < 0 ? colors.loss : colors.neutral,
  fontWeight: 600,
});

const formatTimestamp = (timestamp) => {
  if (!timestamp) return "—";
  const date  = new Date(timestamp);
  const today = new Date();
  const isToday =
    date.getDate()     === today.getDate()  &&
    date.getMonth()    === today.getMonth() &&
    date.getFullYear() === today.getFullYear();

  return isToday
    ? date.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })
    : date.toLocaleString("en-IN",     { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
};

/* ----------------------------------
   Local UI sub-components
   These are copies of what's in Dashboard.jsx.
   Extract to src/components/shared/ when Dashboard is refactored (File 4 step).
----------------------------------- */
function Card({ children, style, elevated }) {
  return (
    <div
      style={{
        background: elevated ? colors.bg.tertiary : colors.bg.secondary,
        border: `1px solid ${colors.border.light}`,
        borderRadius: 8,
        boxShadow: elevated
          ? "0 4px 6px -1px rgba(0,0,0,0.3), 0 2px 4px -1px rgba(0,0,0,0.2)"
          : "0 1px 3px rgba(0,0,0,0.2)",
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function StatusBadge({ ok, text, warn, danger, icon }) {
  let bg          = colors.dangerBg;
  let color       = colors.danger;
  let borderColor = colors.danger;

  if (ok) {
    bg = colors.successBg; color = colors.success; borderColor = colors.success;
  } else if (warn) {
    bg = colors.warningBg; color = colors.warning; borderColor = colors.warning;
  } else if (danger) {
    bg = colors.dangerBg;  color = colors.danger;  borderColor = colors.danger;
  }

  return (
    <span
      style={{
        padding: "4px 10px",
        borderRadius: 6,
        ...typography.bodySmall,
        fontWeight: 600,
        background: bg,
        color,
        border: `1px solid ${borderColor}40`,
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        minWidth: "90px",
        justifyContent: "center",
        textTransform: "uppercase",
        letterSpacing: "0.3px",
      }}
    >
      {icon && <span style={{ fontSize: 10 }}>{icon}</span>}
      {text}
    </span>
  );
}

/* ----------------------------------
   Table style constants
----------------------------------- */
const th = {
  padding: "12px 12px",
  textAlign: "left",
  ...typography.label,
  color: colors.text.muted,
  borderBottom: `2px solid ${colors.border.light}`,
  fontWeight: 600,
};

const td = {
  padding: "12px 12px",
  ...typography.bodyMedium,
};

/* ----------------------------------
   Compact summary — shown when isPrimary === false
----------------------------------- */
function CompactScalpSummary({ inTrade, executionMode, livePnl, onBecomePrimary }) {
  return (
    <div
      onClick={onBecomePrimary}
      style={{
        height: "100%",
        minHeight: 120,
        background: colors.bg.secondary,
        border: `1px solid ${colors.border.light}`,
        borderRadius: 8,
        padding: spacing.md,
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        cursor: "pointer",
        userSelect: "none",
        transition: "border-color 0.2s ease",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.borderColor = colors.primary)}
      onMouseLeave={(e) => (e.currentTarget.style.borderColor = colors.border.light)}
    >
      {/* Top row: label + click hint */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ ...typography.headingSmall, color: colors.text.primary }}>
          SCALP
        </span>
        <span style={{ ...typography.bodySmall, color: colors.text.muted }}>
          ↗ expand
        </span>
      </div>

      {/* Badges */}
      <div style={{ display: "flex", flexDirection: "column", gap: spacing.xs, marginTop: spacing.sm }}>
        <StatusBadge
          ok={inTrade}
          warn={!inTrade}
          text={inTrade ? "In Trade" : "Armed"}
          icon={inTrade ? "🎯" : "⚪"}
        />
        <StatusBadge
          ok={executionMode === "LIVE"}
          warn={executionMode === "PAPER"}
          text={executionMode || "—"}
          icon={executionMode === "LIVE" ? "🟢" : "🧪"}
        />
      </div>

      {/* Live P&L if in trade */}
      {inTrade && (
        <div
          style={{
            marginTop: spacing.sm,
            ...typography.mono,
            fontSize: 13,
            fontWeight: 700,
            ...pnlStyle(livePnl),
          }}
        >
          ₹{Math.round(livePnl).toLocaleString("en-IN")}
        </div>
      )}
    </div>
  );
}

/* ----------------------------------
   ScalpPanel
----------------------------------- */
export default function ScalpPanel({ ltpMap, isPrimary, onBecomePrimary }) {
  const toast    = useToast();
  const isMobile = useIsMobile();

  // ---- Scalp-specific state ----
  const [selection,      setSelection]      = useState(null);
  const [tradeState,     setTradeState]      = useState(null);
  const [trade,          setTrade]           = useState(null);
  const [logs,           setLogs]            = useState([]);
  const [tradeSideMode,  setTradeSideModeLocal] = useState("BOTH");
  const [strategyConfig, setStrategyConfig]  = useState(null);

  // Audio alert diffing
  const [prevTradeState, setPrevTradeState] = useState(null);

  // Sparkline history
  const [pnlHistory, setPnlHistory] = useState({});

  // Row flash on state change — { [slot]: "enter"|"sl"|"tp"|"exit" }
  const [slotFlash, setSlotFlash] = useState({});

  // P&L cell pulse — track direction per symbol
  const prevPnlRef  = useRef({});   // symbol → last P&L value
  const [pnlPulse, setPnlPulse] = useState({}); // symbol → "up"|"dn"|null

  // Live activity feed — chronological, newest first, max 50
  const [activityFeed, setActivityFeed] = useState([]);

  // ---- Polling ----
  useEffect(() => {
    async function loadFast() {
      try { setTrade(await getActiveTrade()); }                          catch {}
      try { setTradeState(await getTradeState("SCALP_V1")); }           catch {}
      try { setSelection(await getCurrentSelection(STRATEGY_ID)); }     catch {}
    }

    async function loadSlow() {
      try {
        const cfg = await getStrategyConfig("SCALP_V1");
        console.log("[ScalpPanel] strategyConfig:", cfg);
        setStrategyConfig(cfg);
      } catch (err) {
        console.error("[ScalpPanel] getStrategyConfig failed:", err);
      }
      try {
        const l = await getLogs();
        setLogs(Array.isArray(l) ? l : l?.logs || []);
      } catch {}
      try {
        const res = await getTradeSideMode();
        setTradeSideModeLocal(res?.mode || "BOTH");
      } catch {}
    }

    loadFast();
    loadSlow();

    const fast = setInterval(loadFast, 3000);
    const slow = setInterval(loadSlow, 15000);

    return () => {
      clearInterval(fast);
      clearInterval(slow);
    };
  }, []);

  // ---- PnL history for sparklines ----
  useEffect(() => {
    if (!tradeState || !ltpMap || Object.keys(ltpMap).length === 0) return;

    setPnlHistory((prev) => {
      const updated    = { ...prev };
      let   hasChanges = false;

      Object.entries(tradeState).forEach(([, state]) => {
        if (!state || typeof state !== "object") return;

        const symbol   = state.symbol;
        const liveLtp  = ltpMap[symbol];
        const buyPrice = state.buy_price;
        const qty      = state.qty;

        if (
          !symbol ||
          !ACTIVE_STATES.includes(state.state) ||
          typeof buyPrice !== "number" ||
          typeof liveLtp  !== "number" ||
          typeof qty      !== "number"
        ) return;

        const pnl     = (liveLtp - buyPrice) * qty;
        const history = updated[symbol] || [];
        const last    = history[history.length - 1];

        if (last !== pnl) {
          updated[symbol] = [...history, pnl].slice(-10);
          hasChanges = true;

          // P&L cell pulse
          const dir = pnl > (prevPnlRef.current[symbol] ?? pnl) ? "up" : "dn";
          prevPnlRef.current[symbol] = pnl;
          setPnlPulse((p) => ({ ...p, [symbol]: dir }));
          setTimeout(() => setPnlPulse((p) => ({ ...p, [symbol]: null })), 600);
        }
      });

      return hasChanges ? updated : prev;
    });
  }, [tradeState, ltpMap]);

  // ---- Audio alerts + toast on state transitions ----
  useEffect(() => {
    if (!tradeState || !prevTradeState) {
      setPrevTradeState(tradeState);
      return;
    }

    Object.entries(tradeState).forEach(([slot, currentState]) => {
      const prevState = prevTradeState[slot];
      if (!prevState || !currentState) return;

      const curr = typeof currentState === "object" ? currentState.state : currentState;
      const prev = typeof prevState    === "object" ? prevState.state    : prevState;

      if (curr === prev) return;

      const symbol = typeof currentState === "object" ? currentState.symbol   : slot;
      const price  = typeof currentState === "object" ? currentState.buy_price : null;
      const pnl    = typeof currentState === "object" ? (currentState.realized_pnl ?? currentState.pnl) : null;

      // ── Activity feed entry helper ──────────────────────────────────────
      const pushActivity = (type, icon, label) => {
        const entry = {
          id:     Date.now() + Math.random(),
          time:   new Date(),
          type,
          icon,
          label,
          symbol,
          pnl,
          price,
          slot,
        };
        setActivityFeed((prev) => [entry, ...prev].slice(0, 50));
      };

      // ── Row flash helper ────────────────────────────────────────────────
      const flash = (kind) => {
        setSlotFlash((f) => ({ ...f, [slot]: kind }));
        setTimeout(() => setSlotFlash((f) => { const n = { ...f }; delete n[slot]; return n; }), 900);
      };

      // Position entered
      if (prev === "ARMED" && (curr === "BUY_PLACED" || curr === "BUY_FILLED" || curr === "PROTECTED" || curr === "IN_TRADE")) {
        AudioAlerts.positionEntered();
        toast.info("Position Entered", `${symbol}${price ? ` @ ₹${price.toFixed(2)}` : ""}`, { duration: 4000 });
        flash("enter");
        pushActivity("enter", "🎯", "Entered");
      }

      // Position exited
      if (ACTIVE_STATES.includes(prev) && (curr === "SL_HIT" || curr === "TP_HIT" || curr === "EXITED" || curr === "CLOSED")) {
        const pnlStr = pnl ? ` ${pnl > 0 ? "+" : ""}₹${Math.round(pnl).toLocaleString("en-IN")}` : "";

        if (curr === "SL_HIT") {
          AudioAlerts.stopLossHit();
          toast.error("Stop Loss Hit", `${symbol}${pnlStr}`, { duration: 6000 });
          flash("sl");
          pushActivity("sl", "🔴", "SL Hit");
        } else if (curr === "TP_HIT") {
          AudioAlerts.takeProfitHit();
          toast.success("Target Reached", `${symbol}${pnlStr} 🎉`, { duration: 6000, icon: "🎯" });
          flash("tp");
          pushActivity("tp", "🎉", "TP Hit");
        } else {
          if (pnl && pnl > 0) {
            AudioAlerts.takeProfitHit();
            toast.success("Position Closed", `${symbol}${pnlStr}`, { duration: 5000 });
            flash("tp");
            pushActivity("exit", "✅", "Closed +");
          } else {
            AudioAlerts.stopLossHit();
            toast.warning("Position Closed", `${symbol}${pnlStr}`, { duration: 5000 });
            flash("sl");
            pushActivity("exit", "⚪", "Closed");
          }
        }
      }
    });

    setPrevTradeState(tradeState);
  }, [tradeState, toast]);

  // ---- Derived values ----
  const symbolToSlot = useMemo(() => {
    if (!tradeState) return {};
    const map = {};
    Object.entries(tradeState).forEach(([slot, data]) => {
      if (data && typeof data === "object" && data.symbol) {
        map[data.symbol] = slot;
      }
    });
    return map;
  }, [tradeState]);

  const activeTradeBySymbol = useMemo(() => {
    if (!tradeState) return {};
    const map = {};
    Object.entries(tradeState).forEach(([slot, t]) => {
      if (t && typeof t === "object" && t.symbol) {
        // Normalize symbol for consistent lookup
        const normalizedSymbol = normalizeSymbol(t.symbol);
        map[normalizedSymbol] = { ...t, slot };
      }
    });
    return map;
  }, [tradeState]);

  // Build CE/PE rows from selection, filtered by tradeSideMode
const rows = useMemo(() => {
  if (!selection) return [];

  const result = [];

  const ceSlots = ["CE_1", "CE_2"];
  const peSlots = ["PE_1", "PE_2"];

  if (tradeSideMode !== "PE") {
    ceSlots.forEach((slot, i) => {
      const o = selection.CE?.[i];

      result.push({
        ...(o || {}),
        side: "CE",
        idx: i + 1,
        slot,
        tradingsymbol: o?.tradingsymbol || null,
        strike: o?.strike || null,
        selected_at: o?.selected_at || null,
      });
    });
  }

  if (tradeSideMode !== "CE") {
    peSlots.forEach((slot, i) => {
      const o = selection.PE?.[i];

      result.push({
        ...(o || {}),
        side: "PE",
        idx: i + 1,
        slot,
        tradingsymbol: o?.tradingsymbol || null,
        strike: o?.strike || null,
        selected_at: o?.selected_at || null,
      });
    });
  }

  return result;
}, [selection, tradeSideMode]);

  const inTrade = useMemo(() => {
    if (!tradeState) return false;
    return Object.values(tradeState).some((v) =>
      typeof v === "object" ? ACTIVE_STATES.includes(v.state) : v === "IN_TRADE"
    );
  }, [tradeState]);

  const executionMode = strategyConfig?.trade_execution_mode || "LIVE";

  // Sum of live P&L across all active slots — used in compact view
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

  // ---- Compact render ----
  if (!isPrimary) {
    return (
      <CompactScalpSummary
        inTrade={inTrade}
        executionMode={executionMode}
        livePnl={livePnl}
        onBecomePrimary={onBecomePrimary}
      />
    );
  }

  // ---- Mobile render — status strip + CE/PE toggle + slim 5-col slots table ----
  if (isMobile) {
    return (
      <>
      <div style={{
        background:   colors.bg.secondary,
        border:       `1px solid ${colors.border.light}`,
        borderRadius: 8,
        overflow:     "hidden",
        marginBottom: spacing.lg,
      }}>
        {/* Header: badges + CE/PE toggle */}
        <div style={{
          padding:        `${spacing.sm}px ${spacing.md}px`,
          borderBottom:   `1px solid ${colors.border.dark}`,
          display:        "flex",
          alignItems:     "center",
          justifyContent: "space-between",
          flexWrap:       "wrap",
          gap:            spacing.sm,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: spacing.sm }}>
            <span style={{ ...typography.headingSmall, color: colors.text.muted }}>SCALP</span>
            <StatusBadge ok={inTrade} warn={!inTrade} text={inTrade ? "In Trade" : "Armed"} icon={inTrade ? "🎯" : "⚪"} />
            <StatusBadge ok={executionMode === "LIVE"} warn={executionMode === "PAPER"} text={executionMode} icon={executionMode === "LIVE" ? "🟢" : "🧪"} />
          </div>

          {/* CE/PE toggle — the one allowed control */}
          <div style={{ display: "flex", gap: 3, background: colors.bg.primary, padding: 3, borderRadius: 6 }}>
            {["BOTH", "CE", "PE"].map((mode) => (
              <button
                key={mode}
                onClick={async () => {
                  setTradeSideModeLocal(mode);
                  try { await setTradeSideMode(mode); } catch {}
                }}
                style={{
                  padding:    "5px 12px",
                  borderRadius: 4,
                  border:     "none",
                  background: tradeSideMode === mode ? colors.primary : "transparent",
                  color:      tradeSideMode === mode ? colors.text.primary : colors.text.tertiary,
                  ...typography.bodySmall,
                  fontWeight: 600,
                  cursor:     "pointer",
                  fontSize:   12,
                }}
              >
                {mode === "BOTH" ? "CE+PE" : mode}
              </button>
            ))}
          </div>
        </div>

        {/* Live P&L strip — only when in trade */}
        {inTrade && (
          <div style={{ padding: `${spacing.xs}px ${spacing.lg}px`, borderBottom: `1px solid ${colors.border.dark}`, display: "flex", alignItems: "baseline", gap: spacing.sm }}>
            <span style={{ ...typography.label, fontSize: 9, color: colors.text.muted }}>Live P&L</span>
            <span style={{ ...typography.mono, fontSize: 22, fontWeight: 800, ...pnlStyle(livePnl) }}>
              {livePnl >= 0 ? "+" : ""}₹{Math.round(livePnl).toLocaleString("en-IN")}
            </span>
          </div>
        )}

        {/* Slim 5-col slots table: Side · Symbol · Strike · Time · State */}
        {rows.length === 0 ? (
          <div style={{ padding: `${spacing.sm}px ${spacing.md}px`, fontSize: 12, color: colors.text.muted }}>
            No slots selected
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
              <thead>
                <tr style={{ background: colors.bg.tertiary }}>
                  {["Side", "Symbol", "Strike", "Time", "State"].map((h) => (
                    <th key={h} style={{ padding: "5px 8px", textAlign: "center", ...typography.label, fontSize: 9, color: colors.text.muted, fontWeight: 600, whiteSpace: "nowrap" }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const normalizedSymbol = r.tradingsymbol ? normalizeSymbol(r.tradingsymbol) : null;
                  const slot  = activeTradeBySymbol[normalizedSymbol] || null;
                  const state = slot ? slot.state : "ARMED";
                  return (
                    <tr key={i} style={{
                      background:  i % 2 ? colors.bg.secondary : colors.bg.primary,
                      borderTop:   `1px solid ${colors.border.dark}`,
                    }}>
                      {/* Side */}
                      <td style={{ padding: "6px 8px", textAlign: "center" }}>
                        <span style={{
                          padding: "2px 6px", borderRadius: 4, fontSize: 10, fontWeight: 700,
                          background: r.side === "CE" ? colors.successBg  : colors.dangerBg,
                          color:      r.side === "CE" ? colors.success     : colors.danger,
                        }}>
                          {r.side}
                        </span>
                      </td>
                      {/* Symbol */}
                      <td style={{ padding: "6px 8px", ...typography.mono, fontSize: 11, color: colors.text.primary, textAlign: "center", maxWidth: 110, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                          title={r.tradingsymbol}>
                        {r.tradingsymbol || "—"}
                      </td>
                      {/* Strike */}
                      <td style={{ padding: "6px 8px", ...typography.mono, fontSize: 11, color: colors.text.secondary, textAlign: "center" }}>
                        {r.strike || "—"}
                      </td>
                      {/* Time */}
                      <td style={{ padding: "6px 8px", ...typography.mono, fontSize: 10, color: colors.text.muted, textAlign: "center", whiteSpace: "nowrap" }}>
                        {formatTimestamp(r.selected_at)}
                      </td>
                      {/* State */}
                      <td style={{ padding: "6px 8px", textAlign: "center" }}>
                        <StatusBadge ok={ACTIVE_STATES.includes(state)} warn={!ACTIVE_STATES.includes(state)} text={state} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <DebugPanel rows={rows} />
    </>
    );
  }

  // ---- Full / primary render (desktop only) ----
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: spacing.xxl }}>
      <style>{`
        @keyframes pnlFlashGreen {
          0%   { background: rgba(16, 185, 129, 0.55); }
          100% { background: transparent; }
        }
        @keyframes pnlFlashRed {
          0%   { background: rgba(239, 68, 68, 0.55); }
          100% { background: transparent; }
        }
      `}</style>

      {/* ---------- UNIFIED PANEL: header + slots table ---------- */}
      <Card elevated>

        {/* Top bar: SCALP label, status badges, CE/PE switcher */}
        <div style={{
          padding: spacing.md,
          borderBottom: `1px solid ${colors.border.light}`,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: spacing.md,
        }}>

          {/* Left: strategy-specific badges */}
          <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap" }}>
            <span style={{ ...typography.headingSmall, color: colors.text.muted, marginRight: spacing.xs }}>
              SCALP
            </span>

            <StatusBadge
              ok={inTrade}
              warn={!inTrade}
              text={inTrade ? "In Trade" : "Armed"}
              icon={inTrade ? "🎯" : "⚪"}
            />
            <StatusBadge
              ok={executionMode === "LIVE"}
              warn={executionMode === "PAPER"}
              text={executionMode}
              icon={executionMode === "LIVE" ? "🟢" : "🧪"}
            />
          </div>

          {/* Right: CE/PE mode selector */}
          <div style={{ display: "flex", alignItems: "center", gap: spacing.lg, flexWrap: "wrap" }}>
            <span style={{ ...typography.bodySmall, color: colors.text.muted, fontWeight: 500 }}>
              MODE:
            </span>
            <div style={{ display: "flex", gap: 4, background: colors.bg.primary, padding: 4, borderRadius: 6 }}>
              {["BOTH", "CE", "PE"].map((mode) => (
                <button
                  key={mode}
                  onClick={async () => {
                    setTradeSideModeLocal(mode);
                    try { await setTradeSideMode(mode); } catch {}
                  }}
                  style={{
                    padding: "6px 14px",
                    borderRadius: 4,
                    border: "none",
                    background: tradeSideMode === mode ? colors.primary : "transparent",
                    color:      tradeSideMode === mode ? colors.text.primary : colors.text.tertiary,
                    ...typography.bodySmall,
                    fontWeight: 600,
                    cursor: "pointer",
                    transition: "all 0.2s ease",
                  }}
                  onMouseEnter={(e) => {
                    if (tradeSideMode !== mode) {
                      e.target.style.background = colors.bg.tertiary;
                      e.target.style.color      = colors.text.secondary;
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (tradeSideMode !== mode) {
                      e.target.style.background = "transparent";
                      e.target.style.color      = colors.text.tertiary;
                    }
                  }}
                >
                  {mode === "BOTH" ? "CE + PE" : mode}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Active Slots label */}
        <div style={{ padding: `${spacing.lg}px ${spacing.md}px ${spacing.sm}px` }}>
          <span style={{ ...typography.headingLarge, color: colors.text.primary }}>
            Active Slots
          </span>
        </div>

        {/* Table — no inner Card, sits directly inside the unified panel */}
        <div style={{ overflowX: "auto" }}>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                ...typography.bodyMedium,
                tableLayout: "fixed",
              }}
            >
              <colgroup>
                <col style={{ width: "3%" }} />
                <col style={{ width: "5%" }} />
                <col style={{ width: "18%" }} />
                <col style={{ width: "7%" }} />
                <col style={{ width: "11%" }} />
                <col style={{ width: "10%" }} />
                <col style={{ width: "10%" }} />
                <col style={{ width: "7%" }} />
                <col style={{ width: "7%" }} />
                <col style={{ width: "7%" }} />
                <col style={{ width: "10%" }} />
                <col style={{ width: "9%" }} />
              </colgroup>

              <thead style={{ background: colors.bg.tertiary }}>
                <tr>
                  <th style={{ ...th, textAlign: "center" }}>#</th>
                  <th style={{ ...th, textAlign: "center" }}>Side</th>
                  <th style={{ ...th, textAlign: "center" }}>Symbol</th>
                  <th style={{ ...th, textAlign: "center" }}>Strike</th>
                  <th style={{ ...th, textAlign: "center" }}>Time</th>
                  <th style={{ ...th, textAlign: "center" }}>State</th>
                  <th style={{ ...th, textAlign: "center" }}>LTP</th>
                  <th style={{ ...th, textAlign: "center" }}>Entry</th>
                  <th style={{ ...th, textAlign: "center" }}>SL</th>
                  <th style={{ ...th, textAlign: "center" }}>TP</th>
                  <th style={{ ...th, textAlign: "center" }}>P&L Trend</th>
                  <th style={{ ...th, textAlign: "center" }}>P&L</th>
                </tr>
              </thead>

              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan="12" style={{ padding: 0, border: "none" }}>
                      <EmptyState
                        icon="📊"
                        title="No active positions"
                        description="Positions will appear here once trades are executed based on your strategy settings."
                      />
                    </td>
                  </tr>
                ) : (
                  rows.map((r, i) => {
                    // Normalize symbol for consistent lookup
                    const normalizedSymbol = r.tradingsymbol
                      ? normalizeSymbol(r.tradingsymbol)
                      : null;
                    const slot     = activeTradeBySymbol[normalizedSymbol] || null;
                    const state    = slot ? slot.state : "ARMED";
                    const liveLtp  = ltpMap[normalizedSymbol];
                    const history  = pnlHistory[normalizeSymbol(r.tradingsymbol)] || [];

                    let pnl = null;
                    if (
                      slot &&
                      ACTIVE_STATES.includes(slot.state) &&
                      typeof slot.buy_price === "number" &&
                      typeof liveLtp        === "number"
                    ) {
                      pnl = (liveLtp - slot.buy_price) * (slot.qty || 0);
                    }

                    return (
                      <tr
                        key={i}
                        style={{
                          background: slotFlash[r.slot] === "enter"
                            ? "rgba(59, 130, 246, 0.18)"
                            : slotFlash[r.slot] === "tp"
                            ? "rgba(16, 185, 129, 0.20)"
                            : slotFlash[r.slot] === "sl"
                            ? "rgba(239, 68, 68, 0.18)"
                            : i % 2 ? colors.bg.secondary : colors.bg.primary,
                          transition: "background 0.35s ease",
                          cursor: "default",
                          borderTop: `1px solid ${colors.border.dark}`,
                        }}
                        onMouseEnter={(e) => { if (!slotFlash[r.slot]) e.currentTarget.style.background = colors.bg.tertiary; }}
                        onMouseLeave={(e) => { if (!slotFlash[r.slot]) e.currentTarget.style.background = i % 2 ? colors.bg.secondary : colors.bg.primary; }}
                      >
                        <td style={{ ...td, textAlign: "center" }}>
                          <span style={{ color: colors.text.muted }}>{r.idx}</span>
                        </td>

                        <td style={{ ...td, textAlign: "center" }}>
                          <span style={{
                            padding: "2px 8px",
                            borderRadius: 4,
                            background: r.side === "CE" ? colors.successBg : colors.dangerBg,
                            color:      r.side === "CE" ? colors.success    : colors.danger,
                            fontSize: 11,
                            fontWeight: 600,
                          }}>
                            {r.side}
                          </span>
                        </td>

                        <td
                          style={{
                            ...td,
                            ...typography.mono,
                            fontWeight: 600,
                            color: colors.text.primary,
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            textAlign: "center",
                          }}
                          title={r.tradingsymbol}
                        >
                          {r.tradingsymbol}
                        </td>

                        <td style={{ ...td, ...typography.mono, color: colors.text.secondary, textAlign: "center" }}>
                          {r.strike}
                        </td>

                        <td style={{ ...td, ...typography.mono, fontSize: 11, color: colors.text.tertiary, textAlign: "center" }}>
                          {formatTimestamp(r.selected_at)}
                        </td>

                        <td style={{ ...td, textAlign: "center" }}>
                          <StatusBadge
                            ok={ACTIVE_STATES.includes(state)}
                            warn={!ACTIVE_STATES.includes(state)}
                            text={state}
                          />
                        </td>

                        <td style={{ ...td, ...typography.mono, color: colors.text.primary, textAlign: "center" }}>
                          {typeof liveLtp === "number" ? liveLtp.toFixed(2) : "—"}
                        </td>

                        <td style={{ ...td, ...typography.mono, color: colors.text.secondary, textAlign: "center" }}>
                          {typeof slot?.buy_price === "number" ? slot.buy_price.toFixed(2) : "—"}
                        </td>

                        <td style={{ ...td, ...typography.mono, color: colors.text.tertiary, textAlign: "center" }}>
                          {typeof slot?.sl_price === "number" ? slot.sl_price.toFixed(2) : "—"}
                        </td>

                        <td style={{ ...td, ...typography.mono, color: colors.text.tertiary, textAlign: "center" }}>
                          {typeof slot?.tp_price === "number" ? slot.tp_price.toFixed(2) : "—"}
                        </td>

                        <td style={{ ...td, ...typography.mono, textAlign: "center" }}>
                          {history.length > 1 ? (
                            <PnLTrendArrow history={history} />
                          ) : (
                            <span style={{ color: colors.text.muted }}>—</span>
                          )}
                        </td>

                        <td
                          key={`pnl-${r.tradingsymbol}-${pnlPulse[r.tradingsymbol] ?? "0"}`}
                          style={{
                            ...td,
                            ...typography.mono,
                            textAlign: "center",
                            ...pnlStyle(pnl ?? 0),
                            fontSize: 14,
                            background:
                              pnl !== null
                                ? pnl > 0 ? colors.profitBg
                                : pnl < 0 ? colors.lossBg
                                : "transparent"
                                : "transparent",
                            animation: pnlPulse[r.tradingsymbol] === "up"
                              ? "pnlFlashGreen 0.55s ease"
                              : pnlPulse[r.tradingsymbol] === "dn"
                              ? "pnlFlashRed 0.55s ease"
                              : "none",
                          }}
                        >
                          {pnl === null ? "—" : `₹${Math.round(pnl).toLocaleString("en-IN")}`}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
      </Card>

      {/* ---------- ACTIVITY FEED ---------- */}
      {activityFeed.length > 0 && (
        <Card>
          <div style={{
            padding: `${spacing.md}px ${spacing.lg}px`,
            borderBottom: `1px solid ${colors.border.dark}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}>
            <span style={{ ...typography.label, color: colors.text.muted }}>
              Activity Feed
            </span>
            <button
              onClick={() => setActivityFeed([])}
              style={{ background: "none", border: "none", color: colors.text.muted, fontSize: 11, cursor: "pointer", padding: "2px 6px", borderRadius: 4 }}
              onMouseEnter={(e) => (e.target.style.color = colors.text.secondary)}
              onMouseLeave={(e) => (e.target.style.color = colors.text.muted)}
            >
              Clear
            </button>
          </div>
          <div style={{ maxHeight: 200, overflowY: "auto", padding: `${spacing.sm}px 0` }}>
            {activityFeed.map((entry) => {
              const pnlStr = entry.pnl != null
                ? ` · ${entry.pnl > 0 ? "+" : ""}₹${Math.round(entry.pnl).toLocaleString("en-IN")}`
                : "";
              const priceStr = entry.price != null
                ? ` @ ₹${entry.price.toFixed ? entry.price.toFixed(2) : entry.price}`
                : "";
              const feedColor = entry.type === "tp"    ? colors.profit
                              : entry.type === "sl"    ? colors.loss
                              : entry.type === "enter" ? colors.primary
                              : colors.text.secondary;
              return (
                <div key={entry.id} style={{
                  display:     "flex",
                  alignItems:  "center",
                  gap:         spacing.sm,
                  padding:     `5px ${spacing.lg}px`,
                  borderBottom: `1px solid ${colors.border.dark}`,
                  transition:  "background 0.15s ease",
                }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = colors.bg.tertiary)}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                >
                  <span style={{ fontSize: 14, flexShrink: 0 }}>{entry.icon}</span>
                  <span style={{ fontSize: 11, fontWeight: 600, color: feedColor, minWidth: 60 }}>
                    {entry.label}
                  </span>
                  <span style={{ ...typography.mono, fontSize: 11, color: colors.text.secondary, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {entry.symbol}
                    <span style={{ color: feedColor, fontWeight: 600 }}>{pnlStr || priceStr}</span>
                  </span>
                  <span style={{ ...typography.mono, fontSize: 10, color: colors.text.muted, flexShrink: 0 }}>
                    {entry.time.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })}
                  </span>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* DEBUG — renders as a fixed drawer+trigger, no layout impact */}
      <DebugPanel rows={rows} />

    </div>
  );
}