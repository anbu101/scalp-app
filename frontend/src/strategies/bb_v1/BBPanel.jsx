/**
 * BB PANEL — Display Only
 *
 * Intended path: src/strategies/bb_v1/BBPanel.jsx
 *
 * Displays live state for the BB_V1 (NIFTY Bollinger Breakout Options) strategy.
 * NO configuration editing here. All config changes go through Settings page.
 *
 * Props:
 *   ltpMap        {Object}  — live LTP data from Dashboard, passed via StrategyHost
 *   isPrimary     {boolean} — true = full expanded view, false = compact summary
 *   onBecomePrimary {fn}    — called when compact panel is clicked to expand
 *
 * Data sources:
 *   getTradeState("BB_V1")    — 3s poll — slot-based trade state (same shape as SCALP_V1)
 *   getStrategyConfig("BB_V1") — 15s poll — config for display (sl_pct, tp_pct, etc.)
 *
 * Side derivation:
 *   BB_V1 has no CE/PE selection object. Side (CE/PE) is inferred from the
 *   trading symbol suffix ("CE" / "PE") in each slot's symbol field.
 */

import { useEffect, useState, useMemo } from "react";
import { getTradeState, getStrategyConfig } from "../../api";

const STRATEGY_ID = "BB_V1";

const ACTIVE_STATES = ["BUY_PLACED", "PROTECTED", "BUY_FILLED", "IN_TRADE"];

/* ----------------------------------
   Default config shape — safe merge target
   Matches what Settings will save for BB_V1.
----------------------------------- */
const DEFAULT_BB_CONFIG = {
  trade_execution_mode: "PAPER",
  sl_pct:              1.0,
  tp_pct:              2.0,
  max_premium:         200,
  max_trades_per_side: 2,
  ce_lots:             1,
  pe_lots:             1,
  auto_square_off_time: "15:15",
  session_start:       "09:15",
  session_end:         "15:15",
};

/* ----------------------------------
   Design tokens — local copy
   TODO: extract to src/styles/tokens.js when Dashboard is refactored
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
  headingLarge:  { fontSize: 18, fontWeight: 600, lineHeight: 1.4 },
  headingSmall:  { fontSize: 14, fontWeight: 600, lineHeight: 1.4 },
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
    primary:   "#0a0f1e",
    secondary: "#111827",
    tertiary:  "#1f2937",
    elevated:  "#374151",
  },
  border: {
    light:  "#374151",
    medium: "#4b5563",
    dark:   "#1f2937",
  },
  text: {
    primary:   "#f9fafb",
    secondary: "#d1d5db",
    tertiary:  "#9ca3af",
    muted:     "#6b7280",
  },
};

/* ----------------------------------
   Helpers
----------------------------------- */
const safeNum = (v) => (typeof v === "number" && !isNaN(v) ? v : 0);

const pnlStyle = (v) => ({
  color: v > 0 ? colors.profit : v < 0 ? colors.loss : colors.neutral,
  fontWeight: 600,
});

/** Infer CE or PE from symbol suffix. Returns "CE", "PE", or "—". */
function inferSide(symbol) {
  if (!symbol) return "—";
  const s = symbol.toUpperCase();
  if (s.endsWith("CE")) return "CE";
  if (s.endsWith("PE")) return "PE";
  return "—";
}

function normalizeSymbol(sym) {
  if (!sym) return sym;
  return sym.replace(/\s+/g, "").toUpperCase();
}

/* ----------------------------------
   UI sub-components — local copies
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

function StatusBadge({ ok, warn, danger, text, icon }) {
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

/** Single config stat: label on top, value below */
function StatCell({ label, value }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ ...typography.label, color: colors.text.muted }}>
        {label}
      </span>
      <span style={{ ...typography.bodyMedium, ...typography.mono, color: colors.text.primary, fontWeight: 600 }}>
        {value ?? "—"}
      </span>
    </div>
  );
}

/* ----------------------------------
   Compact summary — shown when isPrimary === false
----------------------------------- */
function CompactBBSummary({ inTrade, executionMode, livePnl, onBecomePrimary }) {
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
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ ...typography.headingSmall, color: colors.text.primary }}>
          BB
        </span>
        <span style={{ ...typography.bodySmall, color: colors.text.muted }}>
          ↗ expand
        </span>
      </div>

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
   BBPanel
----------------------------------- */
export default function BBPanel({ ltpMap, isPrimary, onBecomePrimary }) {
  const [tradeState,     setTradeState]     = useState(null);
  const [strategyConfig, setStrategyConfig] = useState(null);

  // ---- Polling ----
  useEffect(() => {
    async function loadFast() {
      try { setTradeState(await getTradeState(STRATEGY_ID)); } catch {}
    }

    async function loadSlow() {
      try { setStrategyConfig(await getStrategyConfig(STRATEGY_ID)); } catch {}
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

  // ---- Derived values ----

  /** Config with safe defaults — never read config fields directly */
  const cfg = useMemo(() => ({
    ...DEFAULT_BB_CONFIG,
    ...strategyConfig,
  }), [strategyConfig]);

  const executionMode = cfg.trade_execution_mode;

  const inTrade = useMemo(() => {
    if (!tradeState) return false;
    return Object.values(tradeState).some((v) =>
      typeof v === "object" ? ACTIVE_STATES.includes(v.state) : v === "IN_TRADE"
    );
  }, [tradeState]);

  /** All active slots as a flat array with derived side and live PnL */
  const activeSlots = useMemo(() => {
    if (!tradeState) return [];
    return Object.entries(tradeState)
      .map(([slotKey, slot]) => {
        if (!slot || typeof slot !== "object") return null;
        const liveLtp = ltpMap[normalizeSymbol(slot.symbol)];
        const pnl =
          ACTIVE_STATES.includes(slot.state) &&
          typeof slot.buy_price === "number" &&
          typeof liveLtp === "number"
            ? (liveLtp - slot.buy_price) * safeNum(slot.qty)
            : null;

        return {
          slotKey,
          symbol:   slot.symbol,
          state:    slot.state,
          side:     inferSide(slot.symbol),
          buyPrice: slot.buy_price,
          slPrice:  slot.sl_price,
          tpPrice:  slot.tp_price,
          qty:      slot.qty,
          liveLtp,
          pnl,
        };
      })
      .filter(Boolean)
      .filter((s) => s.state && s.state !== "IDLE");
  }, [tradeState, ltpMap]);

  /** Count of today CE and PE slots (any non-idle state = a trade was attempted) */
  const todayCE = useMemo(
    () => activeSlots.filter((s) => s.side === "CE").length,
    [activeSlots]
  );
  const todayPE = useMemo(
    () => activeSlots.filter((s) => s.side === "PE").length,
    [activeSlots]
  );

  /** Open slots = currently in an active trade state */
  const openSlots = useMemo(
    () => activeSlots.filter((s) => ACTIVE_STATES.includes(s.state)),
    [activeSlots]
  );

  /** Sum of live PnL across open slots — used in compact card */
  const livePnl = useMemo(
    () => openSlots.reduce((sum, s) => sum + safeNum(s.pnl), 0),
    [openSlots]
  );

  // ---- Compact render ----
  if (!isPrimary) {
    return (
      <CompactBBSummary
        inTrade={inTrade}
        executionMode={executionMode}
        livePnl={livePnl}
        onBecomePrimary={onBecomePrimary}
      />
    );
  }

  // ---- Full / primary render ----
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: spacing.xxl }}>

      {/* ---------- UNIFIED PANEL ---------- */}
      <Card elevated>

        {/* Header bar */}
        <div
          style={{
            padding: spacing.md,
            borderBottom: `1px solid ${colors.border.light}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexWrap: "wrap",
            gap: spacing.md,
          }}
        >
          {/* Left: label + status badges */}
          <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap" }}>
            <span style={{ ...typography.headingSmall, color: colors.text.muted }}>
              BB
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

          {/* Right: strategy metadata — read-only, edit in Settings */}
          <span
            style={{
              ...typography.label,
              color: colors.text.muted,
              fontSize: 10,
            }}
          >
            NIFTY · BB OPTIONS · 3m · Edit in Settings
          </span>
        </div>

        {/* Config stats row — display only */}
        <div
          style={{
            padding: spacing.md,
            borderBottom: `1px solid ${colors.border.dark}`,
            display: "flex",
            gap: spacing.xxl,
            flexWrap: "wrap",
          }}
        >
          <StatCell label="SL %"              value={`${cfg.sl_pct}%`} />
          <StatCell label="TP %"              value={`${cfg.tp_pct}%`} />
          <StatCell label="Max Premium"       value={`₹${cfg.max_premium}`} />
          <StatCell label="Max Trades/Side"   value={cfg.max_trades_per_side} />
          <StatCell label="CE Lots"           value={cfg.ce_lots} />
          <StatCell label="PE Lots"           value={cfg.pe_lots} />
          <StatCell label="Session"           value={`${cfg.session_start} – ${cfg.session_end}`} />
          <StatCell label="Square-Off"        value={cfg.auto_square_off_time} />
          <StatCell label="Today CE"          value={todayCE} />
          <StatCell label="Today PE"          value={todayPE} />
        </div>

        {/* Open trades list */}
        <div style={{ padding: spacing.md }}>
          <span style={{ ...typography.headingLarge, color: colors.text.primary }}>
            Open Trades
          </span>

          {openSlots.length === 0 ? (
            <div
              style={{
                marginTop: spacing.lg,
                padding: spacing.xl,
                textAlign: "center",
                color: colors.text.muted,
                ...typography.bodyMedium,
              }}
            >
              No open trades
            </div>
          ) : (
            <div style={{ marginTop: spacing.md, display: "flex", flexDirection: "column", gap: spacing.sm }}>
              {openSlots.map((s, i) => (
                <div
                  key={s.slotKey}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "auto 1fr auto auto auto auto",
                    alignItems: "center",
                    gap: spacing.md,
                    padding: `${spacing.sm}px ${spacing.md}px`,
                    background: i % 2 ? colors.bg.tertiary : colors.bg.primary,
                    borderRadius: 6,
                    border: `1px solid ${colors.border.dark}`,
                  }}
                >
                  {/* Side badge */}
                  <span
                    style={{
                      padding: "2px 8px",
                      borderRadius: 4,
                      background: s.side === "CE" ? colors.successBg : s.side === "PE" ? colors.dangerBg : colors.bg.elevated,
                      color:      s.side === "CE" ? colors.success    : s.side === "PE" ? colors.danger    : colors.text.muted,
                      fontSize: 11,
                      fontWeight: 600,
                    }}
                  >
                    {s.side}
                  </span>

                  {/* Symbol */}
                  <span
                    style={{
                      ...typography.mono,
                      fontSize: 12,
                      fontWeight: 600,
                      color: colors.text.primary,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={s.symbol}
                  >
                    {s.symbol || "—"}
                  </span>

                  {/* State */}
                  <StatusBadge
                    ok={ACTIVE_STATES.includes(s.state)}
                    warn={!ACTIVE_STATES.includes(s.state)}
                    text={s.state || "—"}
                  />

                  {/* LTP */}
                  <span style={{ ...typography.mono, fontSize: 12, color: colors.text.primary }}>
                    {typeof s.liveLtp === "number" ? s.liveLtp.toFixed(2) : "—"}
                  </span>

                  {/* Entry */}
                  <span style={{ ...typography.mono, fontSize: 12, color: colors.text.secondary }}>
                    {typeof s.buyPrice === "number" ? `@ ${s.buyPrice.toFixed(2)}` : "—"}
                  </span>

                  {/* Live P&L */}
                  <span
                    style={{
                      ...typography.mono,
                      fontSize: 13,
                      fontWeight: 700,
                      ...pnlStyle(s.pnl ?? 0),
                      padding: "2px 8px",
                      borderRadius: 4,
                      background:
                        s.pnl !== null
                          ? s.pnl > 0 ? colors.profitBg
                          : s.pnl < 0 ? colors.lossBg
                          : "transparent"
                          : "transparent",
                    }}
                  >
                    {s.pnl === null ? "—" : `₹${Math.round(s.pnl).toLocaleString("en-IN")}`}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

      </Card>
    </div>
  );
}