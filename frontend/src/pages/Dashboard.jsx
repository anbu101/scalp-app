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
        color
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

/* ----------------------------------
   LOG COLORING
----------------------------------- */

function logStyle(line) {
  if (line.includes("[TP]")) return { color: "#7CFFB2" };
  if (line.includes("[SL]")) return { color: "#FF7C7C" };
  if (line.includes("[ERROR]")) return { color: "#FF4D4D", fontWeight: "bold" };
  if (line.includes("[STATE]")) return { color: "#FFD27C" };
  if (line.includes("[SIGNAL]")) return { color: "#7CA7FF" };
  return { color: "#9CA3AF" };
}

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

  const [positions, setPositions] = useState({
    open: [],
    closed: [],
    totals: { realised: 0, unrealised: 0, total: 0 }
  });

  useEffect(() => {
    loadFast();
    loadSlow();

    const fast = setInterval(loadFast, 3000);
    const slow = setInterval(loadSlow, 15000);

    return () => {
      clearInterval(fast);
      clearInterval(slow);
    };
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
     Build table rows
  ----------------------------------- */

  const rows = [];
  if (selection) {
    (selection.CE || []).forEach((o, i) =>
      rows.push({ ...o, side: "CE", idx: i + 1, slot: `CE_${i + 1}` })
    );
    (selection.PE || []).forEach((o, i) =>
      rows.push({ ...o, side: "PE", idx: i + 1, slot: `PE_${i + 1}` })
    );
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

  /* ----------------------------------
     Slot Activity
  ----------------------------------- */

  const IMPORTANT_LOG = /(SIGNAL|TP|SL|STATE)/;

  const lastSlotEvents = (() => {
    const slots = ["CE_1", "CE_2", "PE_1", "PE_2"];
    const out = {};

    for (const slot of slots) {
      const match = [...logs]
        .reverse()
        .find(
          line =>
            line.includes(`SLOT=${slot}`) &&
            IMPORTANT_LOG.test(line)
        );

      if (match) {
        out[slot] = match;
      }
    }

    return out;
  })();

  return (
    <div style={{
      padding: 24,
      background: "#0b1220",
      color: "#e6e6e6",
      minHeight: "100vh",
      fontFamily: "Inter, system-ui, sans-serif"
    }}>

      {/* ---------- HEADER ---------- */}
      <div style={{ marginBottom: 20 }}>
        <h2 style={{ margin: 0 }}>Scalp App</h2>
        <div style={{ marginTop: 6, display: "flex", gap: 8, flexWrap: "wrap" }}>
          <StatusBadge ok={zerodha?.connected} text={zerodha?.connected ? "Zerodha Attached" : "Zerodha Not Attached"} />
          <StatusBadge ok={status?.engine_running} text="Engine Running" />
          <StatusBadge ok={tradingEnabled} warn={!tradingEnabled} text={tradingEnabled ? "TRADING ENABLED" : "TRADING DISABLED"} />
          <StatusBadge ok={inTrade} warn={!inTrade} text={inTrade ? "IN TRADE" : "ARMED"} />
          <StatusBadge ok text={`MODE: ${tradeSideMode}`} />
          {maxLossHit && <StatusBadge danger text="TRADING HALTED (MAX LOSS)" />}
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

      <div style={{ border: "1px solid #243055", borderRadius: 8, overflow: "hidden", marginBottom: 24 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead style={{ background: "#121a30" }}>
            <tr>
              <th style={th}>#</th>
              <th style={th}>Side</th>
              <th style={th}>Symbol</th>
              <th style={th}>Strike</th>
              <th style={th}>LTP</th>
              <th style={th}>Selected At</th>
              <th style={th}>State</th>
              <th style={th}>Buy</th>
              <th style={th}>SL</th>
              <th style={th}>TP</th>
              <th style={th}>PnL</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const slot = tradeState?.[r.slot];
              const state = typeof slot === "object" ? slot.state : slot || "ARMED";

              let pnl = null;
              if (
                slot &&
                ACTIVE_STATES.includes(slot.state) &&
                typeof slot.buy_price === "number" &&
                typeof r.ltp === "number"
              ) {
                pnl = (r.ltp - slot.buy_price) * (slot.qty || 0);
              }

              return (
                <tr key={i} style={{ background: i % 2 ? "#0f1628" : "#0b1220" }}>
                  <td style={td}>{r.idx}</td>
                  <td style={td}>{r.side}</td>
                  <td style={{ ...td, fontFamily: "monospace" }}>{r.tradingsymbol}</td>
                  <td style={td}>{r.strike}</td>
                  <td style={td}>{r.ltp}</td>
                  <td style={td}>{r.selected_at || "—"}</td>
                  <td style={td}>
                    <StatusBadge ok={ACTIVE_STATES.includes(state)} warn={!ACTIVE_STATES.includes(state)} text={state} />
                  </td>
                  <td style={td}>{slot?.buy_price ?? "—"}</td>
                  <td style={td}>{slot?.sl_price ?? "—"}</td>
                  <td style={td}>{slot?.tp_price ?? "—"}</td>
                  <td style={{ ...td, ...pnlStyle(pnl) }}>
                    {pnl === null ? "—" : `₹${Math.round(pnl)}`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* ---------- TODAY'S PNL ---------- */}
      <h3>Today's PnL (Zerodha)</h3>

      <div style={{ display: "flex", gap: 20, marginBottom: 24 }}>
        <div style={tradeBox}>
          <strong>Summary</strong>
          <div style={pnlStyle(positions.totals.realised)}>Realised: ₹{positions.totals.realised}</div>
          <div style={pnlStyle(positions.totals.unrealised)}>Unrealised: ₹{positions.totals.unrealised}</div>
          <div style={pnlStyle(positions.totals.total)}>Total: ₹{positions.totals.total}</div>
        </div>

        <div style={tradeBox}>
          <strong>Open Positions</strong>
          {positions.open.length === 0
            ? <div>No open positions</div>
            : positions.open.map((p, i) => (
              <div key={i} style={pnlStyle(safeNum(p.pnl))}>
                {p.tradingsymbol} | Qty {p.quantity} | ₹{safeNum(p.pnl)}
              </div>
            ))}
        </div>

        <div style={tradeBox}>
          <strong>Closed Positions</strong>
          {positions.closed.length === 0
            ? <div>No closed positions</div>
            : positions.closed.map((p, i) => (
              <div key={i} style={pnlStyle(safeNum(p.pnl))}>
                {p.tradingsymbol} | Qty {p.day_buy_quantity} | ₹{safeNum(p.pnl)}
              </div>
            ))}
        </div>
      </div>

      {/* ---------- DEBUG ---------- */}
      <DebugPanel rows={rows} />

      {/* ---------- SLOT SUMMARY ---------- */}
      <h3>Slot Activity</h3>
      <div style={tradeBox}>
        {["CE_1", "CE_2", "PE_1", "PE_2"].map(slot => (
          <div key={slot}>
            <strong>{slot}</strong>{" "}
            {lastSlotEvents[slot] || <span style={{ opacity: 0.5 }}>— No activity —</span>}
          </div>
        ))}
      </div>

      {/* ---------- LOGS ---------- */}
      <h3>Logs</h3>
      <div style={tradeBox}>
        {logs.slice(-200).map((l, i) => (
          <div key={i} style={logStyle(l)}>
            {l}
          </div>
        ))}
      </div>
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
