/**
 * MarketBadge — src/components/MarketBadge.jsx
 * ── INDEX_BADGES_20260827 ──
 *
 * NIFTY / BANKNIFTY index chip. Lifted VERBATIM from Dashboard.jsx so the
 * exact same badge renders on Dashboard, Paper Trades, and Analytics.
 * Data contract: { ltp, prev_close } slice from useMarketData().indices
 * (MarketDataProvider, 500ms app-wide poll). Renders null until ltp arrives.
 *
 * Tokens-based on purpose: pages with local palettes (Analytics C/FONT)
 * still get a badge pixel-identical to the Dashboard reference.
 */

import { colors, typography } from "../tokens";

function MarketBadge({ name, data }) {
  const ltp       = typeof data?.ltp        === "number" ? data.ltp        : null;
  const prevClose = typeof data?.prev_close === "number" ? data.prev_close : null;
  if (ltp === null) return null;
  const hasChange = prevClose !== null && prevClose > 0;
  const change    = hasChange ? ltp - prevClose : null;
  const pct       = hasChange && prevClose !== 0 ? (change / prevClose) * 100 : null;
  const up        = change !== null ? change >= 0 : true;
  const moveColor = change === null
    ? colors.text.secondary
    : up ? colors.success : colors.danger;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 8, padding: "6px 12px", minHeight: 28,
      borderRadius: 6, background: colors.bg.tertiary,
      color: colors.text.secondary,
      border: `1px solid ${colors.border.light}40`,
      fontSize: 11, fontWeight: 600, letterSpacing: "0.3px", textTransform: "uppercase",
    }}>
      <span style={{ opacity: 0.9 }}>{name}</span>
      <span style={{ ...typography.mono, fontSize: 12, color: moveColor }}>
        {ltp.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </span>
      {change !== null && pct !== null && (
        <span style={{ ...typography.mono, fontSize: 11, color: moveColor }}>
          {up ? "▲" : "▼"} {up ? "+" : ""}{change.toFixed(1)} ({up ? "+" : ""}{pct.toFixed(2)}%)
        </span>
      )}
    </span>
  );
}

export default MarketBadge;