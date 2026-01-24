import { useEffect, useState, useMemo, useRef } from "react";
import {
  getTradeState,
  getZerodhaStatus,
  getActiveTrade,
  getLogs,
  getCurrentSelection,
  getStatus,
  getTodayTrades,
  getTodayPositions,
  getStrategyConfig
} from "../api";
import { getTradeSideMode, setTradeSideMode } from "../api";
import DebugPanel from "../components/DebugPanel";
import { 
  LoadingAnimations, 
  FullPageLoader, 
  EmptyState, 
  TableSkeleton,
  CardSkeleton 
} from "../components/LoadingStates";
import { useToast } from "../components/ToastNotifications";
import {
  Sparkline,
  PriceChangeIndicator,
  ProgressBar,
  PriceWithSparkline,
  PnLTrendArrow
} from "../components/DataVisualization";
import {
  exportToCSV,
  formatTradesForExport,
  generateFilename
} from "../utils/export";
import { getApiBase } from "../api/base";

// BOOTING | UP | DOWN

/* ----------------------------------
   Typography & Spacing Tokens
----------------------------------- */
const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 24
};

const typography = {
  displayLarge: { fontSize: 28, fontWeight: 700, lineHeight: 1.2 },
  displaySmall: { fontSize: 24, fontWeight: 600, lineHeight: 1.3 },
  headingLarge: { fontSize: 18, fontWeight: 600, lineHeight: 1.4 },
  headingMedium: { fontSize: 16, fontWeight: 600, lineHeight: 1.4 },
  headingSmall: { fontSize: 14, fontWeight: 600, lineHeight: 1.4 },
  bodyLarge: { fontSize: 14, fontWeight: 400, lineHeight: 1.5 },
  bodyMedium: { fontSize: 13, fontWeight: 400, lineHeight: 1.5 },
  bodySmall: { fontSize: 12, fontWeight: 400, lineHeight: 1.4 },
  label: { fontSize: 11, fontWeight: 500, lineHeight: 1.3, letterSpacing: '0.5px', textTransform: 'uppercase' },
  mono: { fontFamily: "'JetBrains Mono', 'Fira Code', monospace", fontVariantNumeric: "tabular-nums" }
};

const colors = {
  profit: "#10b981",
  profitBg: "rgba(16, 185, 129, 0.12)",
  loss: "#ef4444",
  lossBg: "rgba(239, 68, 68, 0.12)",
  neutral: "#6b7280",
  primary: "#3b82f6",
  primaryBg: "rgba(59, 130, 246, 0.12)",
  success: "#10b981",
  successBg: "rgba(16, 185, 129, 0.15)",
  warning: "#f59e0b",
  warningBg: "rgba(245, 158, 11, 0.15)",
  danger: "#ef4444",
  dangerBg: "rgba(239, 68, 68, 0.15)",
  bg: {
    primary: "#0a0f1e",
    secondary: "#111827",
    tertiary: "#1f2937",
    elevated: "#374151"
  },
  border: {
    light: "#374151",
    medium: "#4b5563",
    dark: "#1f2937"
  },
  text: {
    primary: "#f9fafb",
    secondary: "#d1d5db",
    tertiary: "#9ca3af",
    muted: "#6b7280"
  }
};

const ACTIVE_STATES = ["BUY_PLACED", "PROTECTED", "BUY_FILLED", "IN_TRADE"];


/* ----------------------------------
   Audio Alert System
----------------------------------- */

const AudioAlerts = {
  context: null,
  
  init() {
    if (!this.context && typeof window !== 'undefined') {
      this.context = new (window.AudioContext || window.webkitAudioContext)();
    }
  },
  
  playTone(frequency, duration, type = 'sine') {
    this.init();
    if (!this.context) return;
    
    const oscillator = this.context.createOscillator();
    const gainNode = this.context.createGain();
    
    oscillator.connect(gainNode);
    gainNode.connect(this.context.destination);
    
    oscillator.frequency.value = frequency;
    oscillator.type = type;
    
    gainNode.gain.setValueAtTime(0.3, this.context.currentTime);
    gainNode.gain.exponentialRampToValueAtTime(0.01, this.context.currentTime + duration);
    
    oscillator.start(this.context.currentTime);
    oscillator.stop(this.context.currentTime + duration);
  },
  
  // New position entered
  positionEntered() {
    this.playTone(800, 0.15);
    setTimeout(() => this.playTone(1000, 0.15), 150);
  },
  
  // Stop loss hit
  stopLossHit() {
    this.playTone(400, 0.2);
    setTimeout(() => this.playTone(350, 0.2), 200);
    setTimeout(() => this.playTone(300, 0.3), 400);
  },
  
  // Take profit hit
  takeProfitHit() {
    this.playTone(600, 0.1);
    setTimeout(() => this.playTone(800, 0.1), 100);
    setTimeout(() => this.playTone(1000, 0.15), 200);
  }
};

/* ----------------------------------
   Small helpers
----------------------------------- */
function normalizeSymbol(sym) {
  if (!sym) return sym;
  return sym.replace(/\s+/g, "").toUpperCase();
}

function StatusBadge({ ok, text, warn, danger, icon }) {
  let bg = colors.dangerBg;
  let color = colors.danger;
  let borderColor = colors.danger;

  if (ok) {
    bg = colors.successBg;
    color = colors.success;
    borderColor = colors.success;
  } else if (danger) {
    bg = colors.dangerBg;
    color = colors.danger;
    borderColor = colors.danger;
  } else if (warn) {
    bg = colors.warningBg;
    color = colors.warning;
    borderColor = colors.warning;
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
        letterSpacing: "0.3px"
      }}
    >
      {icon && <span style={{ fontSize: 10 }}>{icon}</span>}
      {text}
    </span>
  );
}

const safeNum = (v) => (typeof v === "number" && !isNaN(v) ? v : 0);
const pnlStyle = (v) => ({
  color: v > 0 ? colors.profit : v < 0 ? colors.loss : colors.neutral,
  fontWeight: 600
});

const formatTimestamp = (timestamp) => {
  if (!timestamp) return "—";
  
  const date = new Date(timestamp);
  const today = new Date();
  
  const isToday = 
    date.getDate() === today.getDate() &&
    date.getMonth() === today.getMonth() &&
    date.getFullYear() === today.getFullYear();
  
  if (isToday) {
    return date.toLocaleTimeString('en-IN', { 
      hour: '2-digit', 
      minute: '2-digit', 
      second: '2-digit',
      hour12: false 
    });
  } else {
    return date.toLocaleString('en-IN', { 
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit', 
      minute: '2-digit',
      hour12: false 
    });
  }
};

/* ----------------------------------
   Card Component
----------------------------------- */
function Card({ children, style, elevated }) {
  return (
    <div
      style={{
        background: elevated ? colors.bg.tertiary : colors.bg.secondary,
        border: `1px solid ${colors.border.light}`,
        borderRadius: 8,
        boxShadow: elevated ? "0 4px 6px -1px rgba(0, 0, 0, 0.3), 0 2px 4px -1px rgba(0, 0, 0, 0.2)" : "0 1px 3px rgba(0, 0, 0, 0.2)",
        ...style
      }}
    >
      {children}
    </div>
  );
}

/* ----------------------------------
   Dashboard
----------------------------------- */

export default function Dashboard() {
  const toast = useToast();
  const [zerodha, setZerodha] = useState(null);
  const [status, setStatus] = useState(null);
  const [trade, setTrade] = useState(null);
  const [logs, setLogs] = useState([]);
  const [selection, setSelection] = useState(null);
  const [tradeState, setTradeState] = useState(null);
  const [tradeSideMode, setTradeSideModeState] = useState("BOTH");
  const [strategyConfig, setStrategyConfig] = useState(null);
  const [indices, setIndices] = useState({});
  const [loading, setLoading] = useState(true);
  const [backendHealth, setBackendHealth] = useState("BOOTING");
  const [booting, setBooting] = useState(true);

  const [positions, setPositions] = useState({
    open: [],
    closed: [],
    totals: { realised: 0, unrealised: 0, total: 0 }
  });

  const [ltpMap, setLtpMap] = useState({});
  const [prevTradeState, setPrevTradeState] = useState(null);
  const [positionsLoading, setPositionsLoading] = useState(true);
  
  // Track price history for sparklines
  const [priceHistory, setPriceHistory] = useState({});
  const [pnlHistory, setPnlHistory] = useState({});

  // Add this inside Dashboard component, after the useEffect
  useEffect(() => {
    console.log('[DEBUG] Window.__SCALP_API_BASE__:', window.__SCALP_API_BASE__);
    console.log('[DEBUG] Window.__TAURI__:', window.__TAURI__);
    console.log('[DEBUG] Resolved API base:', getApiBase());
  }, []);

  // Update price history when LTP changes
  useEffect(() => {
    if (ltpMap && Object.keys(ltpMap).length > 0) {
      setPriceHistory(prev => {
        const updated = { ...prev };
        Object.entries(ltpMap).forEach(([symbol, price]) => {
          if (typeof price === 'number') {
            if (!updated[symbol]) {
              updated[symbol] = [];
            }
            // Keep last 30 data points for sparkline
            updated[symbol] = [...updated[symbol], price].slice(-30);
          }
        });
        return updated;
      });
    }
  }, [ltpMap]);

  // Update PnL history when trade state or LTP changes
  useEffect(() => {
    if (!tradeState || !ltpMap || Object.keys(ltpMap).length === 0) return;

    setPnlHistory(prev => {
      const updated = { ...prev };
      let hasChanges = false;

      Object.entries(tradeState).forEach(([slot, state]) => {
        if (!state || typeof state !== 'object') return;
        
        const symbol = state.symbol;
        if (!symbol || !ACTIVE_STATES.includes(state.state)) return;

        const liveLtp = ltpMap[symbol];
        const buyPrice = state.buy_price;
        const qty = state.qty;

        if (typeof buyPrice === 'number' && typeof liveLtp === 'number' && typeof qty === 'number') {
          const pnl = (liveLtp - buyPrice) * qty;
          
          if (!updated[symbol]) {
            updated[symbol] = [];
          }
          
          // Only add if different from last value
          const lastPnl = updated[symbol][updated[symbol].length - 1];
          if (lastPnl !== pnl) {
            updated[symbol] = [...updated[symbol], pnl].slice(-10);
            hasChanges = true;
          }
        }
      });

      return hasChanges ? updated : prev;
    });
  }, [tradeState, ltpMap]);

  // Track previous states for audio alerts and toast notifications
  useEffect(() => {
    if (!tradeState || !prevTradeState) {
      setPrevTradeState(tradeState);
      return;
    }

    // Check for state changes
    Object.entries(tradeState).forEach(([slot, currentState]) => {
      const prevState = prevTradeState[slot];
      
      if (!prevState || !currentState) return;
      
      const curr = typeof currentState === 'object' ? currentState.state : currentState;
      const prev = typeof prevState === 'object' ? prevState.state : prevState;
      
      // Skip if no actual state change
      if (curr === prev) return;
      
      // Get symbol for notification
      const symbol = typeof currentState === 'object' ? currentState.symbol : slot;
      const price = typeof currentState === 'object' ? currentState.buy_price : null;
      const pnl = typeof currentState === 'object' ? currentState.realized_pnl || currentState.pnl : null;
      
      console.log(`State change detected: ${slot} ${prev} -> ${curr} (${symbol})`);
      
      // New position entered (ARMED -> BUY_PLACED, BUY_FILLED, PROTECTED, or IN_TRADE)
      if (prev === 'ARMED' && (curr === 'BUY_PLACED' || curr === 'BUY_FILLED' || curr === 'PROTECTED' || curr === 'IN_TRADE')) {
        console.log('🔊 Position entered alert');
        AudioAlerts.positionEntered();
        const priceStr = price ? ` @ ₹${price.toFixed(2)}` : '';
        toast.info(
          "Position Entered",
          `${symbol}${priceStr}`,
          { duration: 4000 }
        );
      }
      
      // Position exited - check multiple exit states
      if (ACTIVE_STATES.includes(prev) && (curr === 'SL_HIT' || curr === 'TP_HIT' || curr === 'EXITED' || curr === 'CLOSED')) {
        console.log(`🔊 Position exit alert: ${curr}`);
        
        const pnlStr = pnl ? ` ${pnl > 0 ? '+' : ''}₹${Math.round(pnl).toLocaleString('en-IN')}` : '';
        
        if (curr === 'SL_HIT') {
          AudioAlerts.stopLossHit();
          toast.error(
            "Stop Loss Hit",
            `${symbol}${pnlStr}`,
            { duration: 6000 }
          );
        } else if (curr === 'TP_HIT') {
          AudioAlerts.takeProfitHit();
          toast.success(
            "Target Reached",
            `${symbol}${pnlStr} 🎉`,
            { duration: 6000, icon: "🎯" }
          );
        } else {
          // Generic exit (EXITED/CLOSED)
          if (pnl && pnl > 0) {
            AudioAlerts.takeProfitHit();
            toast.success(
              "Position Closed",
              `${symbol}${pnlStr}`,
              { duration: 5000 }
            );
          } else {
            AudioAlerts.stopLossHit();
            toast.warning(
              "Position Closed",
              `${symbol}${pnlStr}`,
              { duration: 5000 }
            );
          }
        }
      }
    });

    setPrevTradeState(tradeState);
  }, [tradeState, toast]);


  useEffect(() => {
    async function initialLoad() {
      await loadFast();
      await loadSlow();
      setLoading(false);
    }
    
    initialLoad();

    const fast = setInterval(loadFast, 3000);
    const slow = setInterval(loadSlow, 15000);

    return () => {
      clearInterval(fast);
      clearInterval(slow);
    };
  }, []);

  useEffect(() => {
    let alive = true;
  
    async function pollLtp() {
      while (alive) {
        try {
          const res = await fetch(`${getApiBase()}/ltp_snapshot`);
          if (res.ok) {
            const data = await res.json();
            if (data && typeof data === "object") {
              setLtpMap(data);
            }
          }
        } catch {}
  
        await new Promise(r => setTimeout(r, 500));
      }
    }
  
    pollLtp();
    return () => { alive = false; };
  }, []);
  

  useEffect(() => {
    let alive = true;
  
    async function pollIndices() {
      while (alive) {
        try {
          const res = await fetch(`${getApiBase()}/market_indices`);
          if (res.ok) {
            const data = await res.json();
            if (data && typeof data === "object") {
              setIndices(data);
            }
          }
        } catch {}
  
        await new Promise(r => setTimeout(r, 500));
      }
    }
  
    pollIndices();
    return () => { alive = false; };
  }, []);
  
  async function loadFast() {
    // ---- DEBUG: Check API base ----
    console.log('[DEBUG] === loadFast called ===');
    console.log('[DEBUG] window.__SCALP_API_BASE__:', window.__SCALP_API_BASE__);
    console.log('[DEBUG] window.__TAURI__:', !!window.__TAURI__);
    
    try {
      const apiBase = getApiBase();
      console.log('[DEBUG] getApiBase() returned:', apiBase);
    } catch (e) {
      console.error('[DEBUG] getApiBase() error:', e);
    }

    // ---- BACKEND / ENGINE STATUS ----
    try {
      const statusUrl = `${getApiBase()}/status`;
      console.log('[DEBUG] Fetching status from:', statusUrl);
      
      const s = await getStatus();
      console.log('[DEBUG] Status response:', JSON.stringify(s, null, 2));
      
      setStatus(s);

      if (s?.backend === "UP") {
        console.log('[DEBUG] ✅ Backend is UP, setting health to UP');
        setBackendHealth("UP");
        setBooting(false);
      } else {
        console.log('[DEBUG] ❌ Backend not UP, status.backend =', s?.backend);
        setBackendHealth("DOWN");
      }
    } catch (error) {
      console.error('[DEBUG] ❌ Error fetching status:', error);
      console.error('[DEBUG] Error message:', error.message);
      console.error('[DEBUG] Error stack:', error.stack);
      setBackendHealth(prev => {
        console.log('[DEBUG] Setting backend health, prev was:', prev);
        return prev === "UP" ? "DOWN" : prev;
      });
    }

    // ---- REST ----
    try { 
      const trade = await getActiveTrade();
      console.log('[DEBUG] Active trade:', trade);
      setTrade(trade);
    } catch (e) {
      console.error('[DEBUG] getActiveTrade error:', e);
    }
    
    try { 
      const tradeState = await getTradeState();
      console.log('[DEBUG] Trade state:', tradeState);
      setTradeState(tradeState);
    } catch (e) {
      console.error('[DEBUG] getTradeState error:', e);
    }
    
    try { 
      const selection = await getCurrentSelection();
      console.log('[DEBUG] Current selection:', selection);
      setSelection(selection);
    } catch (e) {
      console.error('[DEBUG] getCurrentSelection error:', e);
    }
  }
  

  async function loadSlow() {
    try { setZerodha(await getZerodhaStatus()); } catch {}
    try { setStrategyConfig(await getStrategyConfig()); } catch {}

    try {
      const l = await getLogs();
      setLogs(Array.isArray(l) ? l : l?.logs || []);
    } catch {}

    try {
      setPositionsLoading(true);
      const p = await getTodayPositions();
      const open = p?.open || [];
      const closed = p?.closed || [];

      const realised = closed.reduce((s, x) => s + safeNum(x.pnl), 0);
      const unrealised = open.reduce((s, x) => s + safeNum(x.pnl), 0);

      setPositions({
        open,
        closed,
        totals: {
          realised,
          unrealised,
          total: realised + unrealised,
        },
      });
    } catch {
      setPositions({
        open: [],
        closed: [],
        totals: { realised: 0, unrealised: 0, total: 0 }
      });
    } finally {
      setPositionsLoading(false);
    }

    try {
      const res = await getTradeSideMode();
      setTradeSideModeState(res?.mode || "BOTH");
    } catch {}
  }

  const symbolToSlot = {};
  if (tradeState) {
    Object.entries(tradeState).forEach(([slot, data]) => {
      if (data && typeof data === "object" && data.symbol) {
        symbolToSlot[data.symbol] = slot;
      }
    });
  }

  const activeTradeBySymbol = useMemo(() => {
    if (!tradeState) return {};
    const map = {};
    Object.entries(tradeState).forEach(([slot, t]) => {
      if (t && typeof t === "object" && t.symbol) {
        map[t.symbol] = { ...t, slot };
      }
    });
    return map;
  }, [tradeState]);
  
  const rows = [];

  if (selection) {
    if (tradeSideMode !== "PE") {
      (selection.CE || []).forEach((o, i) =>
        rows.push({
          ...o,
          side: "CE",
          idx: i + 1,
          slot: symbolToSlot[o.tradingsymbol] || `CE_${i + 1}`,
        })
      );
    }

    if (tradeSideMode !== "CE") {
      (selection.PE || []).forEach((o, i) =>
        rows.push({
          ...o,
          side: "PE",
          idx: i + 1,
          slot: symbolToSlot[o.tradingsymbol] || `PE_${i + 1}`,
        })
      );
    }
  }

  const tradingEnabled = strategyConfig?.trade_on === true;
  const executionMode = strategyConfig?.trade_execution_mode || "LIVE";

  const inTrade =
    tradeState &&
    Object.values(tradeState).some(
      v => typeof v === "object"
        ? ACTIVE_STATES.includes(v.state)
        : v === "IN_TRADE"
    );

    // Export handler
  function handleExportTrades() {
    const allPositions = [...positions.open, ...positions.closed];
    if (allPositions.length === 0) {
      toast.warning('No Data', 'No trades to export');
      return;
    }
    const formattedTrades = formatTradesForExport(allPositions);
    exportToCSV(formattedTrades, generateFilename('today_trades', 'csv'));
    toast.success('Export Complete', `${allPositions.length} trades exported`);
  }

  if (loading) {
    return (
      <>
        <LoadingAnimations />
        <FullPageLoader message="Loading dashboard..." />
      </>
    );
  }

  return (
    <div style={{
      padding: spacing.xxl,
      background: colors.bg.primary,
      color: colors.text.primary,
      minHeight: "100vh",
      fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    }}>

      {loading && (
        <div style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          height: 2,
          background: colors.border.dark,
          zIndex: 1000
        }}>
          <div style={{
            height: "100%",
            background: `linear-gradient(90deg, ${colors.primary}, ${colors.success})`,
            animation: "loading 1.5s ease-in-out infinite",
            width: "40%"
          }} />
        </div>
      )}

      {/* ---------- HEADER ---------- */}
      <div style={{ marginBottom: spacing.xxl }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.lg }}>
          <h1 style={{ margin: 0, ...typography.displayLarge, color: colors.text.primary }}>
            Scalp Terminal
          </h1>
          <div style={{ ...typography.label, color: colors.text.muted }}>
            Live Trading Dashboard
          </div>
        </div>

        {/* Status Bar with Trade Mode */}
        <Card elevated style={{ padding: spacing.md, marginBottom: spacing.lg }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: spacing.md }}>
            {/* Left: Status Badges */}
            <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap" }}>
              <StatusBadge 
                ok={zerodha?.connected} 
                danger={!zerodha?.connected}
                text={zerodha?.connected ? "Connected" : "Disconnected"} 
                icon={zerodha?.connected ? "●" : "○"}
              />
              <StatusBadge
                ok={backendHealth === "UP" && status?.engine === "RUNNING"}
                warn={backendHealth === "BOOTING"}
                danger={backendHealth === "DOWN"}
                text={
                  backendHealth === "BOOTING"
                    ? "Backend Starting"
                    : backendHealth === "DOWN"
                      ? "Backend Down"
                      : status?.engine === "RUNNING"
                        ? "Engine Running"
                        : "Engine Paused"
                }
                icon="⚡"
              />
              <StatusBadge 
                ok={tradingEnabled} 
                warn={!tradingEnabled} 
                text={tradingEnabled ? "Trading" : "Paused"}
                icon={tradingEnabled ? "▶" : "⏸"}
              />
              <StatusBadge
                ok={executionMode === "LIVE"}
                warn={executionMode === "PAPER"}
                text={executionMode}
                icon={executionMode === "LIVE" ? "🟢" : "🧪"}
              />
              <StatusBadge 
                ok={inTrade} 
                warn={!inTrade} 
                text={inTrade ? "In Trade" : "Armed"}
                icon={inTrade ? "🎯" : "⚪"}
              />
              
              <MarketBadge name="NIFTY" data={indices.NIFTY} />
              <MarketBadge name="BANKNIFTY" data={indices.BANKNIFTY} />
            <div style={{ flex: 1 }} />
            </div>

            {/* Right: Trade Mode Selector */}
            <div style={{ display: "flex", alignItems: "center", gap: spacing.lg, flexWrap: "wrap" }}>
              <span style={{ ...typography.bodySmall, color: colors.text.muted, fontWeight: 500 }}>
                MODE:
              </span>
              <div style={{ display: "flex", gap: 4, background: colors.bg.primary, padding: 4, borderRadius: 6 }}>
                {["BOTH", "CE", "PE"].map((mode) => (
                  <button
                    key={mode}
                    onClick={async () => {
                      setTradeSideModeState(mode);
                      try {
                        await setTradeSideMode(mode);
                      } catch {}
                    }}
                    style={{
                      padding: "6px 14px",
                      borderRadius: 4,
                      border: "none",
                      background: tradeSideMode === mode ? colors.primary : "transparent",
                      color: tradeSideMode === mode ? colors.text.primary : colors.text.tertiary,
                      ...typography.bodySmall,
                      fontWeight: 600,
                      cursor: "pointer",
                      transition: "all 0.2s ease"
                    }}
                    onMouseEnter={(e) => {
                      if (tradeSideMode !== mode) {
                        e.target.style.background = colors.bg.tertiary;
                        e.target.style.color = colors.text.secondary;
                      }
                    }}
                    onMouseLeave={(e) => {
                      if (tradeSideMode !== mode) {
                        e.target.style.background = "transparent";
                        e.target.style.color = colors.text.tertiary;
                      }
                    }}
                  >
                    {mode === "BOTH" ? "CE + PE" : mode}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </Card>
      </div> 
      {/* ---------- OPTION TABLE ---------- */}
      <div style={{ marginBottom: spacing.xxl }}>
        <h2 style={{ ...typography.headingLarge, color: colors.text.primary, marginBottom: spacing.md }}>
          Active Positions
        </h2>

        <Card>
          <div style={{ overflowX: "auto" }}>
            <table style={{
              width: "100%",
              borderCollapse: "collapse",
              ...typography.bodyMedium,
              tableLayout: "fixed"
            }}>
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
                    const slot = activeTradeBySymbol[r.tradingsymbol] || null;
                    const state = slot ? slot.state : "ARMED";              

                    const liveLtp = ltpMap[normalizeSymbol(r.tradingsymbol)];

                    let pnl = null;
                    if (
                      slot &&
                      ACTIVE_STATES.includes(slot.state) &&
                      typeof slot.buy_price === "number" &&
                      typeof liveLtp === "number"
                    ) {
                      pnl = (liveLtp - slot.buy_price) * (slot.qty || 0);
                    }

                    return (
                      <tr 
                        key={i} 
                        style={{ 
                          background: i % 2 ? colors.bg.secondary : colors.bg.primary,
                          transition: "background 0.15s ease",
                          cursor: "default",
                          borderTop: `1px solid ${colors.border.dark}`
                        }}
                        onMouseEnter={(e) => e.currentTarget.style.background = colors.bg.tertiary}
                        onMouseLeave={(e) => e.currentTarget.style.background = i % 2 ? colors.bg.secondary : colors.bg.primary}
                      >
                        <td style={{ ...td, textAlign: "center" }}>
                          <span style={{ color: colors.text.muted }}>{r.idx}</span>
                        </td>
                        <td style={{ ...td, textAlign: "center" }}>
                          <span style={{
                            padding: "2px 8px",
                            borderRadius: 4,
                            background: r.side === "CE" ? colors.successBg : colors.dangerBg,
                            color: r.side === "CE" ? colors.success : colors.danger,
                            fontSize: 11,
                            fontWeight: 600
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
                            textAlign: "center"
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
                        <td
                          style={{
                            ...td,
                            ...typography.mono,
                            textAlign: "center",
                            ...pnlStyle(pnl),
                            fontSize: 14,
                            background: pnl !== null ? (
                              pnl > 0 ? colors.profitBg :
                              pnl < 0 ? colors.lossBg :
                              "transparent"
                            ) : "transparent"
                          }}
                        >
                          {pnl === null ? "—" : `₹${Math.round(pnl).toLocaleString('en-IN')}`}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      {/* ---------- TODAY'S PNL ---------- */}
      <div>
        <h2 style={{ ...typography.headingLarge, color: colors.text.primary, marginBottom: spacing.md }}>
          Today's Performance
        </h2>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: spacing.md }}>
          {/* Summary Card */}
          <Card elevated style={{ padding: spacing.lg }}>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>
              Summary
            </div>
            {positionsLoading ? (
              <CardSkeleton rows={3} />
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: spacing.sm }}>
                <PnLRow label="Realised" value={positions.totals.realised} />
                <PnLRow label="Unrealised" value={positions.totals.unrealised} />
                <div style={{ 
                  borderTop: `1px solid ${colors.border.dark}`, 
                  marginTop: spacing.sm, 
                  paddingTop: spacing.sm 
                }}>
                  <PnLRow label="Total P&L" value={positions.totals.total} large />
                </div>
              </div>
            )}
          </Card>

          {/* Open Positions */}
          <Card elevated style={{ padding: spacing.lg }}>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>
              Open Positions
            </div>
            {positionsLoading ? (
              <CardSkeleton rows={3} />
            ) : (
              <div style={{ maxHeight: 200, overflow: "auto" }}>
                {positions.open.length === 0 ? (
                  <EmptyState
                    icon="📭"
                    title="No open positions"
                    description=""
                  />
                ) : (
                  positions.open.map((p, i) => (
                    <div 
                      key={i} 
                      style={{ 
                        ...typography.bodySmall, 
                        ...typography.mono,
                        marginBottom: spacing.xs,
                        padding: spacing.xs,
                        background: colors.bg.secondary,
                        borderRadius: 4,
                        display: "flex",
                        justifyContent: "space-between"
                      }}
                    >
                      <span style={{ color: colors.text.secondary }}>
                        {p.tradingsymbol} × {p.quantity}
                      </span>
                      <span style={pnlStyle(safeNum(p.pnl))}>
                        ₹{Math.round(safeNum(p.pnl)).toLocaleString('en-IN')}
                      </span>
                    </div>
                  ))
                )}
              </div>
            )}
          </Card>

          {/* Closed Positions */}
          <Card elevated style={{ padding: spacing.lg }}>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>
              Closed Positions
            </div>
            {positionsLoading ? (
              <CardSkeleton rows={3} />
            ) : (
              <div style={{ maxHeight: 200, overflow: "auto" }}>
                {positions.closed.length === 0 ? (
                  <EmptyState
                    icon="📭"
                    title="No closed positions"
                    description=""
                  />
                ) : (
                  positions.closed.map((p, i) => (
                    <div 
                      key={i} 
                      style={{ 
                        ...typography.bodySmall,
                        ...typography.mono,
                        marginBottom: spacing.xs,
                        padding: spacing.xs,
                        background: colors.bg.secondary,
                        borderRadius: 4,
                        display: "flex",
                        justifyContent: "space-between"
                      }}
                    >
                      <span style={{ color: colors.text.secondary }}>
                        {p.tradingsymbol} × {p.day_buy_quantity}
                      </span>
                      <span style={pnlStyle(safeNum(p.pnl))}>
                        ₹{Math.round(safeNum(p.pnl)).toLocaleString('en-IN')}
                      </span>
                    </div>
                  ))
                )}
              </div>
            )}
          </Card>
        </div>
      </div>

      {/* ---------- DEBUG ---------- */}
      <div style={{ marginTop: spacing.xxl }}>
        <DebugPanel rows={rows} />
      </div>
    </div>
  );
}

/* ----------------------------------
   Helper Components
----------------------------------- */

function PnLRow({ label, value, large }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <span style={{ 
        ...(large ? typography.bodyLarge : typography.bodyMedium), 
        color: colors.text.secondary,
        fontWeight: large ? 600 : 400
      }}>
        {label}
      </span>
      <span style={{ 
        ...(large ? typography.headingMedium : typography.bodyMedium),
        ...typography.mono,
        ...pnlStyle(value)
      }}>
        ₹{Math.round(value).toLocaleString('en-IN')}
      </span>
    </div>
  );
}

function MarketBadge({ name, data }) {
  const [pulse, setPulse] = useState(false);
  const prevLtpRef = useRef(null);

  const ltp =
    typeof data?.ltp === "number" ? data.ltp : null;

  const prevClose =
    typeof data?.prev_close === "number" ? data.prev_close : ltp;

  // ✅ Hooks ALWAYS run
  useEffect(() => {
    if (ltp === null) return;

    if (prevLtpRef.current !== null && prevLtpRef.current !== ltp) {
      setPulse(true);
      const t = setTimeout(() => setPulse(false), 180);
      return () => clearTimeout(t);
    }

    prevLtpRef.current = ltp;
  }, [ltp]);

  
  // ✅ Conditional render AFTER hooks
  if (ltp === null || prevClose === null) {
    return null;
  }

  const change = ltp - prevClose;
  const pct = prevClose !== 0 ? (change / prevClose) * 100 : 0;
  const up = change >= 0;

  const bg = up ? colors.successBg : colors.dangerBg;
  const color = up ? colors.success : colors.danger;

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        padding: "6px 12px",
        minHeight: 28,
        borderRadius: 6,
        background: bg,
        color,
        border: `1px solid ${color}40`,
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: "0.3px",
        textTransform: "uppercase",
        filter: pulse ? "brightness(1.25)" : "brightness(1)",
        boxShadow: pulse ? `0 0 8px ${color}55` : "none",
        transition: "filter 0.18s ease, box-shadow 0.18s ease"
      }}
    >
      <span style={{ opacity: 0.9 }}>{name}</span>

      <span style={{ ...typography.mono, fontSize: 12 }}>
        {ltp.toFixed(2)}
      </span>

      <span style={{ ...typography.mono, fontSize: 11 }}>
        {up ? "▲" : "▼"} {pct.toFixed(2)}%
      </span>
    </span>
  );
}


/* ----------------------------------
   Styles
----------------------------------- */

const th = {
  padding: "12px 12px",
  textAlign: "left",
  ...typography.label,
  color: colors.text.muted,
  borderBottom: `2px solid ${colors.border.light}`,
  fontWeight: 600
};

const td = { 
  padding: "12px 12px",
  ...typography.bodyMedium
};

const styles = `
  @keyframes loading {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(400%); }
  }
`;

if (typeof document !== 'undefined' && !document.getElementById('dashboard-styles')) {
  const styleSheet = document.createElement('style');
  styleSheet.id = 'dashboard-styles';
  styleSheet.textContent = styles;
  document.head.appendChild(styleSheet);
}