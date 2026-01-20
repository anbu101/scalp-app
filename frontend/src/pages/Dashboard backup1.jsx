import { useEffect, useState, useMemo } from "react";
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

/* ----------------------------------
   Small helpers
----------------------------------- */

function StatusBadge({ ok, text, warn, danger }) {
  let bg = "#3f1212";
  let color = "#FF7C7C";

  if (ok) {
    bg = "#123f2a";
    color = "#7CFFB2";
  } else if (danger) {
    bg = "#3f1212";
    color = "#FF7C7C";
  } else if (warn) {
    bg = "#3b2f12";
    color = "#FFD27C";
  }

  return (
    <span
      style={{
        padding: "4px 10px",
        borderRadius: 12,
        fontSize: 12,
        background: bg,
        color,
        display: "inline-block",
        minWidth: "80px",
        textAlign: "center"
      }}
    >
      {text}
    </span>
  );
}

const safeNum = (v) => (typeof v === "number" && !isNaN(v) ? v : 0);
const pnlStyle = (v) => ({
  color:
    v > 0 ? "#7CFFB2" :
    v < 0 ? "#FF7C7C" :
    "#9CA3AF"
});

const formatTimestamp = (timestamp) => {
  if (!timestamp) return "—";
  
  const date = new Date(timestamp);
  const today = new Date();
  
  // Check if it's today
  const isToday = 
    date.getDate() === today.getDate() &&
    date.getMonth() === today.getMonth() &&
    date.getFullYear() === today.getFullYear();
  
  if (isToday) {
    // Show only time for today
    return date.toLocaleTimeString('en-IN', { 
      hour: '2-digit', 
      minute: '2-digit', 
      second: '2-digit',
      hour12: false 
    });
  } else {
    // Show date and time for other days
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
   Dashboard
----------------------------------- */

export default function Dashboard() {
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

  const [positions, setPositions] = useState({
    open: [],
    closed: [],
    totals: { realised: 0, unrealised: 0, total: 0 }
  });

  const [ltpMap, setLtpMap] = useState({});

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

  // 🔴 NEW: Ultra-fast LTP polling (isolated, safe)
  useEffect(() => {
    let alive = true;

    async function pollLtp() {
      while (alive) {
        try {
          const res = await fetch("/ltp_snapshot");
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
          const res = await fetch("/market_indices");
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
    try { setStatus(await getStatus()); } catch {}
    try { setTrade(await getActiveTrade()); } catch {}
    try { setTradeState(await getTradeState()); } catch {}
    try { setSelection(await getCurrentSelection()); } catch {}
  }

  async function loadSlow() {
    try { setZerodha(await getZerodhaStatus()); } catch {}
    try { setStrategyConfig(await getStrategyConfig()); } catch {}

    try {
      const l = await getLogs();
      setLogs(Array.isArray(l) ? l : l?.logs || []);
    } catch {}

    try {
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
    } catch {}

    try {
      const res = await getTradeSideMode();
      setTradeSideModeState(res?.mode || "BOTH");
    } catch {}
  }

  /* ----------------------------------
   SLOT ↔ SYMBOL MAP (AUTHORITATIVE)
  ----------------------------------- */

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
  
  /* ----------------------------------
     Build table rows
  ----------------------------------- */

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


  /* ----------------------------------
     HEADER STATE
  ----------------------------------- */

  const tradingEnabled = strategyConfig?.trade_on === true;

  const ACTIVE_STATES = ["BUY_PLACED", "PROTECTED", "BUY_FILLED", "IN_TRADE"];

  const inTrade =
    tradeState &&
    Object.values(tradeState).some(
      v => typeof v === "object"
        ? ACTIVE_STATES.includes(v.state)
        : v === "IN_TRADE"
    );

  const maxLossHit = status?.trading_halted === true;

  return (
    <div style={{
      padding: 24,
      background: "#0b1220",
      color: "#e6e6e6",
      minHeight: "100vh",
      fontFamily: "Inter, system-ui, sans-serif"
    }}>

      {loading && (
        <div style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          height: 3,
          background: "#243055",
          zIndex: 1000
        }}>
          <div style={{
            height: "100%",
            background: "linear-gradient(90deg, #7CFFB2, #3b82f6)",
            animation: "loading 1.5s ease-in-out infinite",
            width: "40%"
          }} />
        </div>
      )}

      {/* ---------- HEADER ---------- */}
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ margin: 0, marginBottom: 16 }}>Scalp App</h2>
        {/* ---------- MARKET INDICES ---------- */}
        <div
          style={{
            marginTop: 0,
            display: "flex",
            alignItems: "center",
            gap: 16,
            flexWrap: "nowrap",
          }}
        >
          {/* LEFT: STATUS BADGES */}
          <div
            style={{
              display: "flex",
              gap: 8,
              flexWrap: "wrap",
              whiteSpace: "nowrap",
              flexShrink: 0,
            }}
          >
            <StatusBadge ok={zerodha?.connected} text={zerodha?.connected ? "Zerodha Attached" : "Zerodha Not Attached"} />
            <StatusBadge ok={status?.engine_running} text="Engine Running" />
            <StatusBadge ok={tradingEnabled} warn={!tradingEnabled} text={tradingEnabled ? "TRADING ENABLED" : "TRADING DISABLED"} />
            <StatusBadge ok={inTrade} warn={!inTrade} text={inTrade ? "IN TRADE" : "ARMED"} />
            <StatusBadge ok text={`MODE: ${tradeSideMode}`} />
            {maxLossHit && <StatusBadge danger text="TRADING HALTED (MAX LOSS)" />}
          </div>

          {/* RIGHT: MARKET INDICES */}
          <div
            style={{
              display: "flex",
              gap: 8,
              flexShrink: 1,
              overflowX: "auto",
            }}
          >
            <IndexTile name="NIFTY" data={indices.NIFTY} compact />
            <IndexTile name="BANKNIFTY" data={indices.BANKNIFTY} compact />
            <IndexTile name="SENSEX" data={indices.SENSEX} compact />
          </div>
        </div>

      </div>

      {/* ---------- TRADE SIDE ---------- */}
      <div style={{ marginTop: 10 }}>
        <label style={{ marginRight: 10 }}>Trade Side:</label>
        <select
          value={tradeSideMode}
          onChange={async (e) => {
            const mode = e.target.value;
            setTradeSideModeState(mode);
            try {
              await setTradeSideMode(mode);
            } catch {}
          }}
          style={{
            background: "#020617",
            color: "#e6e6e6",
            border: "1px solid #243055",
            padding: "6px 10px",
            borderRadius: 6
          }}
        >
          <option value="BOTH">Both (CE + PE)</option>
          <option value="CE">CE Only</option>
          <option value="PE">PE Only</option>
        </select>
      </div>

      {/* ---------- OPTION TABLE ---------- */}
      <h3>Current Option Selection</h3>

      <div style={{ border: "1px solid #243055", borderRadius: 8, overflowX: "auto", marginBottom: 24 }}>
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontSize: 14,
            tableLayout: "auto"
          }}
        >
          <thead style={{ background: "#121a30" }}>
            <tr>
              <th style={th}>#</th>
              <th style={th}>Side</th>
              <th style={th}>Symbol</th>
              <th style={th}>Strike</th>
              <th style={th}>Selected At</th>
              <th style={th}>State</th>
              <th style={{ ...th, textAlign: "right" }}>LTP</th>
              <th style={{ ...th, textAlign: "right" }}>Buy</th>
              <th style={{ ...th, textAlign: "right" }}>SL</th>
              <th style={{ ...th, textAlign: "right" }}>TP</th>
              <th style={{ ...th, textAlign: "right" }}>PnL</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan="11" style={{ ...td, textAlign: "center", padding: "40px", color: "#9CA3AF" }}>
                  No options selected
                </td>
              </tr>
            ) : (
              rows.map((r, i) => {
              const slot = activeTradeBySymbol[r.tradingsymbol] || null;
              const state = slot ? slot.state : "ARMED";              

              const liveLtp = ltpMap[r.tradingsymbol] ?? r.ltp;

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
                    background: i % 2 ? "#0f1628" : "#0b1220",
                    transition: "background 0.2s ease",
                    cursor: "default"
                  }}
                  onMouseEnter={(e) => e.currentTarget.style.background = "#1a2642"}
                  onMouseLeave={(e) => e.currentTarget.style.background = i % 2 ? "#0f1628" : "#0b1220"}
                >
                  <td style={td}>{r.idx}</td>
                  <td style={td}>{r.side}</td>
                  <td
                    style={{
                      ...td,
                      fontFamily: "monospace",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      maxWidth: "220px",
                      fontWeight: 600,
                      color: "#e6f0ff"
                    }}
                    title={r.tradingsymbol}
                  >
                    {r.tradingsymbol}
                  </td>
                  <td style={td}>{r.strike}</td>
                  <td style={td}>{formatTimestamp(r.selected_at)}</td>
                  <td style={td}>
                    <StatusBadge
                      ok={ACTIVE_STATES.includes(state)}
                      warn={!ACTIVE_STATES.includes(state)}
                      text={state}
                    />
                  </td>
                  <td style={{ ...td, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {typeof liveLtp === "number" ? liveLtp.toFixed(2) : "—"}
                  </td>
                  <td style={{ ...td, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {typeof slot?.buy_price === "number" ? slot.buy_price.toFixed(2) : "—"}
                  </td>
                  <td style={{ ...td, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {typeof slot?.sl_price === "number" ? slot.sl_price.toFixed(2) : "—"}
                  </td>
                  <td style={{ ...td, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {typeof slot?.tp_price === "number" ? slot.tp_price.toFixed(2) : "—"}
                  </td>
                  <td
                    style={{
                      ...td,
                      textAlign: "right",
                      fontVariantNumeric: "tabular-nums",
                      ...pnlStyle(pnl),
                      fontWeight: 600,
                      background: pnl !== null ? (
                        pnl > 0 ? "rgba(124, 255, 178, 0.08)" :
                        pnl < 0 ? "rgba(255, 124, 124, 0.08)" :
                        "transparent"
                      ) : "transparent"
                    }}
                  >
                    {pnl === null ? "—" : `₹${Math.round(pnl)}`}
                  </td>
                </tr>
              );
            }))}
          </tbody>
        </table>
      </div>

      {/* ---------- TODAY'S PNL ---------- */}
      <h3>Today's PnL (Zerodha)</h3>

      <div style={{ display: "flex", gap: 20, marginBottom: 24 }}>
        <div style={tradeBox}>
          <strong>Summary</strong>
          <div style={{ ...pnlStyle(positions.totals.realised), fontVariantNumeric: "tabular-nums" }}>
            Realised: ₹{Math.round(positions.totals.realised).toLocaleString('en-IN')}
          </div>
          <div style={{ ...pnlStyle(positions.totals.unrealised), fontVariantNumeric: "tabular-nums" }}>
            Unrealised: ₹{Math.round(positions.totals.unrealised).toLocaleString('en-IN')}
          </div>
          <div style={{ ...pnlStyle(positions.totals.total), fontVariantNumeric: "tabular-nums" }}>
            Total: ₹{Math.round(positions.totals.total).toLocaleString('en-IN')}
          </div>
        </div>

        <div style={tradeBox}>
          <strong>Open Positions</strong>
          {positions.open.length === 0
            ? <div style={{ color: "#9CA3AF", marginTop: 8 }}>No open positions</div>
            : positions.open.map((p, i) => (
              <div key={i} style={{ ...pnlStyle(safeNum(p.pnl)), fontVariantNumeric: "tabular-nums" }}>
                {p.tradingsymbol} | Qty {p.quantity} | ₹{Math.round(safeNum(p.pnl)).toLocaleString('en-IN')}
              </div>
            ))}
        </div>

        <div style={tradeBox}>
          <strong>Closed Positions</strong>
          {positions.closed.length === 0
            ? <div style={{ color: "#9CA3AF", marginTop: 8 }}>No closed positions</div>
            : positions.closed.map((p, i) => (
              <div key={i} style={{ ...pnlStyle(safeNum(p.pnl)), fontVariantNumeric: "tabular-nums" }}>
                {p.tradingsymbol} | Qty {p.day_buy_quantity} | ₹{Math.round(safeNum(p.pnl)).toLocaleString('en-IN')}
              </div>
            ))}
        </div>
      </div>

      {/* ---------- DEBUG ---------- */}
      <DebugPanel rows={rows} />
    </div>
  );
}

/* ----------------------------------
   Styles
----------------------------------- */

const th = {
  padding: "10px 8px",
  textAlign: "left",
  borderBottom: "1px solid #243055",
  fontWeight: 600
};

const td = { padding: "8px" };

const tradeBox = {
  background: "#020617",
  border: "1px solid #243055",
  borderRadius: 8,
  padding: 12,
  maxHeight: 240,
  overflow: "auto",
  fontSize: 12,
  fontFamily: "monospace",
  flex: 1
};

const styles = `
  @keyframes loading {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(400%); }
  }
`;

// Inject animation styles
if (typeof document !== 'undefined' && !document.getElementById('dashboard-styles')) {
  const styleSheet = document.createElement('style');
  styleSheet.id = 'dashboard-styles';
  styleSheet.textContent = styles;
  document.head.appendChild(styleSheet);
}

function IndexTile({ name, data, compact }) {
  if (!data || typeof data.ltp !== "number" || typeof data.prev_close !== "number") {
    return null;
  }

  const change = data.ltp - data.prev_close;
  const pct = (change / data.prev_close) * 100;
  const up = change >= 0;

  return (
    <div
      style={{
        padding: compact ? "4px 8px" : "6px 10px",
        borderRadius: 8,
        background: up ? "#123f2a" : "#3f1212",
        color: up ? "#7CFFB2" : "#FF7C7C",
        fontSize: 11,
  
        /* 🔒 FIX: stable layout */
        display: "grid",
        gridTemplateColumns: "50px 90px 75px",
        alignItems: "center",
        columnGap: 6,
        minWidth: 230,
        maxWidth: 230,
      }}
    >
      <div style={{ fontSize: 11, opacity: 0.7 }}>
        {name}
      </div>
  
      <div
        style={{
          fontSize: 16,
          fontWeight: 600,
          textAlign: "right",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {data.ltp.toFixed(2)}
      </div>
  
      <div
        style={{
          fontSize: 10,
          textAlign: "right",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {up ? "+" : ""}
        {change.toFixed(2)} ({up ? "+" : ""}
        {pct.toFixed(2)}%)
      </div>
    </div>
  );  
}