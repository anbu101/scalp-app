/**
 * HAPanel — src/strategies/ha_v1/HAPanel.jsx
 *
 * Primary mode  : info strip + live HA candle table (last 20 bars)
 * Compact mode  : mode badge + last signal badge
 *
 * Polls /api/ha/status every 10s for trade state,
 * and /api/config?strategy_id=HA_V1 for config.
 * No dedicated chart endpoint needed — candle data comes
 * from the existing paper_trades / trade_state endpoints.
 */

import { useEffect, useState, useCallback } from "react";
import { getApiBase } from "../../api/base";
import { getStrategyConfig } from "../../api";

/* ─── Design tokens (identical to BBPanel) ───────────────────── */
const C = {
  bg:        "#020817",
  bgCard:    "#0f172a",
  bgSurface: "#1e293b",
  border:    "#334155",
  borderDim: "#1a2540",
  text:      "#f1f5f9",
  textSec:   "#94a3b8",
  textMuted: "#4b6280",
  green:     "#10b981",
  greenDim:  "rgba(16,185,129,0.12)",
  red:       "#ef4444",
  redDim:    "rgba(239,68,68,0.12)",
  amber:     "#f59e0b",
  amberDim:  "rgba(245,158,11,0.12)",
  blue:      "#3b82f6",
  blueDim:   "rgba(59,130,246,0.12)",
  cyan:      "#06b6d4",
  orange:    "#f97316",
};

const FONT = "'Inter', -apple-system, sans-serif";
const MONO = "'JetBrains Mono','Fira Code',monospace";

/* ─── Helpers ────────────────────────────────────────────────── */
function fmtPrice(v) {
  if (v == null) return "—";
  return Number(v).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function isMarketHours() {
  const d = new Date();
  if (d.getDay() === 0 || d.getDay() === 6) return false;
  const m = d.getHours() * 60 + d.getMinutes();
  return m >= 555 && m < 930;
}

/* ─── Atoms ──────────────────────────────────────────────────── */
function Pill({ label, color, bg, border, icon }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: "0.5px",
        padding: "2px 8px",
        borderRadius: 4,
        background: bg,
        color,
        border: `1px solid ${border}`,
      }}
    >
      {icon && <span style={{ fontSize: 9 }}>{icon}</span>}
      {label}
    </span>
  );
}

function SignalBadge({ action }) {
  if (!action) return null;
  const map = {
    ENTER_CE: { label: "▲ CE", bg: "rgba(6,182,212,0.15)",  color: C.cyan,   border: C.cyan   },
    ENTER_PE: { label: "▼ PE", bg: "rgba(249,115,22,0.15)", color: C.orange, border: C.orange },
    SL:       { label: "✕ SL", bg: C.redDim,                color: C.red,    border: C.red    },
    TP:       { label: "✓ TP", bg: C.greenDim,              color: C.green,  border: C.green  },
  };
  const s = map[action];
  if (!s) return <span style={{ fontSize: 11, color: C.textMuted }}>{action}</span>;
  return (
    <span
      style={{
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: "0.4px",
        padding: "2px 7px",
        borderRadius: 4,
        background: s.bg,
        color: s.color,
        border: `1px solid ${s.border}`,
      }}
    >
      {s.label}
    </span>
  );
}

/* ─── Info strip (primary mode only) ────────────────────────── */
function InfoStrip({ config, tradeState }) {
  if (!config) return null;

  const mode   = config.trade_execution_mode || "PAPER";
  const isLive = mode === "LIVE";

  const statStyle = {
    display: "flex",
    flexDirection: "column",
    gap: 1,
    minWidth: 0,
  };
  const statLabel = {
    fontSize: 8,
    fontWeight: 600,
    color: C.textMuted,
    letterSpacing: "0.6px",
    textTransform: "uppercase",
    whiteSpace: "nowrap",
  };
  const statVal = {
    fontSize: 12,
    fontWeight: 700,
    color: C.text,
    fontFamily: MONO,
    whiteSpace: "nowrap",
  };

  const Stat = ({ label, value, color }) => (
    <div style={statStyle}>
      <span style={statLabel}>{label}</span>
      <span style={{ ...statVal, color: color || C.text }}>{value ?? "—"}</span>
    </div>
  );

  const divider = (
    <div
      style={{
        width: 1,
        background: C.borderDim,
        alignSelf: "stretch",
        margin: "0 2px",
      }}
    />
  );

  const premium = config.option_premium || {};
  const qty     = config.quantity || {};
  const session = config.session?.primary || {};

  return (
    <div
      style={{
        borderBottom: `1px solid ${C.borderDim}`,
        background: C.bgCard,
      }}
    >
      {/* Row 1: mode + strategy label */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "7px 14px 5px",
        }}
      >
        <Pill
          label={mode}
          color={isLive ? C.red : C.green}
          bg={isLive ? C.redDim : C.greenDim}
          border={isLive ? C.red : C.green}
          icon={isLive ? "⚡" : "✎"}
        />
        <span style={{ fontSize: 10, color: C.textMuted, marginLeft: 4 }}>
          NIFTY OPTIONS · 1M HEIKIN ASHI · EMA20 BOUNCE
        </span>
      </div>

      {/* Row 2: key stats */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 14,
          padding: "4px 14px 8px",
          overflowX: "auto",
        }}
      >
        <Stat label="R:R" value={config.risk_reward_ratio != null ? `1:${config.risk_reward_ratio}` : "—"} />
        {divider}
        <Stat label="Min Premium" value={premium.min != null ? `₹${premium.min}` : "—"} />
        {divider}
        <Stat label="Max Premium" value={premium.max != null ? `₹${premium.max}` : "—"} />
        {divider}
        <Stat label="Lots" value={qty.lots} />
        {divider}
        <Stat label="Lot Size" value={qty.lot_size} />
        {divider}
        <Stat
          label="Session"
          value={
            session.start && session.end
              ? `${session.start} – ${session.end}`
              : "—"
          }
        />
        {divider}
        <Stat
          label="Max Trades/Side"
          value={config.max_trades_per_side}
        />
        {divider}
        <Stat
          label="Side Mode"
          value={config.trade_side_mode || "BOTH"}
          color={
            config.trade_side_mode === "CE"
              ? C.cyan
              : config.trade_side_mode === "PE"
              ? C.orange
              : C.textMuted
          }
        />
      </div>
    </div>
  );
}

/* ─── Panel header ───────────────────────────────────────────── */
function PanelHeader({
  isPrimary,
  onBecomePrimary,
  config,
  ceTradeOpen,
  peTradeOpen,
  lastSignal,
}) {
  const mode   = config?.trade_execution_mode || "PAPER";
  const isLive = mode === "LIVE";

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: isPrimary ? "9px 14px 7px" : "7px 10px",
        borderBottom: `1px solid ${C.borderDim}`,
        cursor: isPrimary ? "default" : "pointer",
        flexWrap: "wrap",
        flexShrink: 0,
      }}
      onClick={!isPrimary ? onBecomePrimary : undefined}
    >
      {/* Strategy label */}
      <div
        style={{
          fontSize: 11,
          fontWeight: 700,
          color: C.amber,
          letterSpacing: "0.8px",
        }}
      >
        HA
      </div>

      {/* Compact badges */}
      {!isPrimary && config && (
        <Pill
          label={mode}
          color={isLive ? C.red : C.green}
          bg={isLive ? C.redDim : C.greenDim}
          border={isLive ? C.red : C.green}
          icon={isLive ? "⚡" : "✎"}
        />
      )}

      <div style={{ fontSize: 11, color: C.textMuted, letterSpacing: "0.5px" }}>
        NIFTY 1m HA
      </div>

      {/* Open trade indicators */}
      {ceTradeOpen && (
        <span
          style={{
            fontSize: 10,
            fontWeight: 700,
            padding: "1px 6px",
            borderRadius: 3,
            background: "rgba(6,182,212,0.15)",
            color: C.cyan,
            border: `1px solid ${C.cyan}`,
          }}
        >
          CE OPEN
        </span>
      )}
      {peTradeOpen && (
        <span
          style={{
            fontSize: 10,
            fontWeight: 700,
            padding: "1px 6px",
            borderRadius: 3,
            background: "rgba(249,115,22,0.15)",
            color: C.orange,
            border: `1px solid ${C.orange}`,
          }}
        >
          PE OPEN
        </span>
      )}

      {/* Last signal */}
      {isPrimary && lastSignal && (
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ fontSize: 10, color: C.textMuted }}>Last:</span>
          <SignalBadge action={lastSignal.action} />
          <span
            style={{ fontSize: 10, color: C.textMuted, fontFamily: MONO }}
          >
            {lastSignal.symbol}
          </span>
        </div>
      )}
    </div>
  );
}

/* ─── HA candle table (primary mode) ─────────────────────────── */
function HATradeTable({ trades }) {
  if (!trades || trades.length === 0) {
    return (
      <div
        style={{
          padding: "32px 16px",
          textAlign: "center",
          color: C.textMuted,
          fontSize: 12,
        }}
      >
        No trades today yet
      </div>
    );
  }

  const cols = [
    { key: "symbol",     label: "Symbol" },
    { key: "side",       label: "Side"   },
    { key: "entry_time", label: "Entry"  },
    { key: "entry_price",label: "Entry ₹"},
    { key: "sl_price",   label: "SL ₹"  },
    { key: "tp_price",   label: "TP ₹"  },
    { key: "exit_price", label: "Exit ₹" },
    { key: "exit_reason",label: "Reason" },
    { key: "net_pnl",    label: "Net P&L"},
    { key: "state",      label: "State"  },
  ];

  const fmtTs = (ts) => {
    if (!ts) return "—";
    return new Date(ts * 1000).toLocaleTimeString("en-IN", {
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    });
  };

  const pnlColor = (v) =>
    v == null ? C.textMuted : v > 0 ? C.green : v < 0 ? C.red : C.textMuted;

  return (
    <div style={{ overflowX: "auto" }}>
      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
          fontSize: 11,
          fontFamily: MONO,
        }}
      >
        <thead>
          <tr>
            {cols.map((c) => (
              <th
                key={c.key}
                style={{
                  padding: "6px 10px",
                  textAlign: "left",
                  color: C.textMuted,
                  fontWeight: 600,
                  fontSize: 9,
                  letterSpacing: "0.5px",
                  textTransform: "uppercase",
                  borderBottom: `1px solid ${C.border}`,
                  whiteSpace: "nowrap",
                }}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => {
            const pnl = t.net_pnl;
            const isOpen = t.state === "OPEN";
            return (
              <tr
                key={i}
                style={{
                  background: i % 2 === 0 ? "transparent" : C.bgSurface,
                }}
              >
                <td style={{ padding: "5px 10px", color: C.text }}>{t.symbol || "—"}</td>
                <td style={{ padding: "5px 10px" }}>
                  <span
                    style={{
                      color: t.side === "CE" ? C.cyan : C.orange,
                      fontWeight: 700,
                    }}
                  >
                    {t.side || "—"}
                  </span>
                </td>
                <td style={{ padding: "5px 10px", color: C.textSec }}>{fmtTs(t.entry_time)}</td>
                <td style={{ padding: "5px 10px", color: C.text }}>{fmtPrice(t.entry_price)}</td>
                <td style={{ padding: "5px 10px", color: C.red }}>{fmtPrice(t.sl_price)}</td>
                <td style={{ padding: "5px 10px", color: C.green }}>{fmtPrice(t.tp_price)}</td>
                <td style={{ padding: "5px 10px", color: C.textSec }}>
                  {isOpen ? <span style={{ color: C.amber }}>LIVE</span> : fmtPrice(t.exit_price)}
                </td>
                <td style={{ padding: "5px 10px", color: C.textMuted }}>{t.exit_reason || "—"}</td>
                <td
                  style={{
                    padding: "5px 10px",
                    color: pnlColor(pnl),
                    fontWeight: 700,
                  }}
                >
                  {pnl != null
                    ? `${pnl >= 0 ? "+" : ""}₹${Math.round(pnl).toLocaleString("en-IN")}`
                    : "—"}
                </td>
                <td style={{ padding: "5px 10px" }}>
                  <span
                    style={{
                      fontSize: 9,
                      fontWeight: 700,
                      padding: "2px 6px",
                      borderRadius: 3,
                      background: isOpen ? C.amberDim : C.bgSurface,
                      color: isOpen ? C.amber : C.textMuted,
                      border: `1px solid ${isOpen ? C.amber : C.border}`,
                    }}
                  >
                    {t.state}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ─── Compact squeeze bar (reused from BBPanel concept) ──────── */
function CompactStatusBar({ ceOpen, peOpen }) {
  return (
    <div
      style={{
        padding: "6px 10px 8px",
        borderTop: `1px solid ${C.borderDim}`,
        display: "flex",
        gap: 6,
      }}
    >
      <div
        style={{
          flex: 1,
          height: 4,
          borderRadius: 2,
          background: ceOpen ? C.cyan : C.borderDim,
          transition: "background 0.4s",
        }}
        title="CE slot"
      />
      <div
        style={{
          flex: 1,
          height: 4,
          borderRadius: 2,
          background: peOpen ? C.orange : C.borderDim,
          transition: "background 0.4s",
        }}
        title="PE slot"
      />
    </div>
  );
}

/* ─── Main component ─────────────────────────────────────────── */
export default function HAPanel({ ltpMap, isPrimary, onBecomePrimary }) {
  const [config, setConfig]       = useState(null);
  const [trades, setTrades]       = useState([]);
  const [lastSignal, setLastSignal] = useState(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(null);

  /* ── Fetch config ─────────────────────────────────────────── */
  const fetchConfig = useCallback(async () => {
    try {
      const cfg = await getStrategyConfig("HA_V1");
      setConfig(cfg || null);
    } catch { /* non-fatal */ }
  }, []);

  /* ── Fetch today's HA paper trades ───────────────────────── */
  const fetchTrades = useCallback(async () => {
    try {
      const res = await fetch(
        `${getApiBase()}/api/paper_trades?strategy_name=HA_V1&today=true`
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const list = data.trades || data || [];
      setTrades(list);

      // Derive last signal from most recent trade
      const sorted = [...list].sort((a, b) => (b.entry_time || 0) - (a.entry_time || 0));
      if (sorted.length > 0) {
        const t = sorted[0];
        setLastSignal({
          action: t.exit_reason === "SL"
            ? "SL"
            : t.exit_reason === "TP"
            ? "TP"
            : t.side === "CE"
            ? "ENTER_CE"
            : "ENTER_PE",
          symbol: t.symbol,
        });
      }

      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConfig();
    fetchTrades();

    const interval = isMarketHours() ? 10_000 : 60_000;
    const t1 = setInterval(fetchConfig, 30_000);
    const t2 = setInterval(fetchTrades, interval);

    return () => {
      clearInterval(t1);
      clearInterval(t2);
    };
  }, [fetchConfig, fetchTrades]);

  // Derived
  const ceOpen = trades.some((t) => t.symbol?.endsWith("CE") && t.state === "OPEN");
  const peOpen = trades.some((t) => t.symbol?.endsWith("PE") && t.state === "OPEN");

  // Summary stats
  const closed    = trades.filter((t) => t.state === "CLOSED");
  const totalPnl  = closed.reduce((s, t) => s + (t.net_pnl || 0), 0);
  const wins      = closed.filter((t) => (t.net_pnl || 0) > 0).length;
  const losses    = closed.filter((t) => (t.net_pnl || 0) <= 0).length;

  return (
    <div
      style={{
        background: C.bg,
        border: `1px solid ${C.border}`,
        borderRadius: 8,
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minWidth: 0,
      }}
    >
      {/* Header — always visible */}
      <PanelHeader
        isPrimary={isPrimary}
        onBecomePrimary={onBecomePrimary}
        config={config}
        ceTradeOpen={ceOpen}
        peTradeOpen={peOpen}
        lastSignal={lastSignal}
      />

      {/* Info strip — primary only */}
      {isPrimary && <InfoStrip config={config} />}

      {/* Primary body */}
      {isPrimary && (
        <div
          style={{
            flex: 1,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
          }}
        >
          {/* Summary row */}
          <div
            style={{
              display: "flex",
              gap: 24,
              padding: "10px 14px 8px",
              borderBottom: `1px solid ${C.borderDim}`,
              background: C.bgCard,
              flexWrap: "wrap",
            }}
          >
            {[
              { label: "Open",   value: trades.filter((t) => t.state === "OPEN").length, color: C.amber },
              { label: "Closed", value: closed.length,  color: C.textMuted },
              { label: "Wins",   value: wins,           color: C.green     },
              { label: "Losses", value: losses,         color: C.red       },
              {
                label: "Net P&L",
                value: `${totalPnl >= 0 ? "+" : ""}₹${Math.round(totalPnl).toLocaleString("en-IN")}`,
                color: totalPnl >= 0 ? C.green : C.red,
              },
            ].map(({ label, value, color }) => (
              <div key={label} style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                <span
                  style={{
                    fontSize: 8,
                    fontWeight: 600,
                    color: C.textMuted,
                    letterSpacing: "0.6px",
                    textTransform: "uppercase",
                  }}
                >
                  {label}
                </span>
                <span
                  style={{
                    fontSize: 13,
                    fontWeight: 700,
                    color,
                    fontFamily: MONO,
                  }}
                >
                  {value}
                </span>
              </div>
            ))}
          </div>

          {/* Trade table */}
          <div style={{ flex: 1, overflowY: "auto" }}>
            {loading ? (
              <div
                style={{
                  padding: 24,
                  textAlign: "center",
                  color: C.textMuted,
                  fontSize: 12,
                }}
              >
                Loading…
              </div>
            ) : error ? (
              <div
                style={{ padding: 16, color: C.red, fontSize: 12 }}
              >
                Error: {error}
              </div>
            ) : (
              <HATradeTable trades={trades} />
            )}
          </div>
        </div>
      )}

      {/* Compact: CE/PE slot bars */}
      {!isPrimary && <CompactStatusBar ceOpen={ceOpen} peOpen={peOpen} />}
    </div>
  );
}