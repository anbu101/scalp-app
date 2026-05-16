/**
 * HAPanel — src/strategies/ha_v1/HAPanel.jsx
 *
 * Design: mirrors ScalpPanel — slot cards, not a chart.
 *
 * Primary mode: two slot cards (CE + PE) each showing:
 *   • Selected symbol + live LTP from ltpMap
 *   • Trade state (IDLE / IN TRADE)
 *   • Entry · SL · TP with distance indicators when in trade
 *   • Unrealized P&L (live, from LTP)
 *   • Last signal badge (ENTER_CE / ENTER_PE / -)
 *   • Session + config summary strip
 *
 * Compact mode: two status dots + mode badge + "HA" label
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { getApiBase } from "../../api/base";
import { getStrategyConfig } from "../../api";
import { colors, spacing, typography } from "../../tokens";

/* ─── Constants ──────────────────────────────────────────────── */
const STRATEGY_ID   = "HA_V1";
const POLL_FAST_MS  = 3_000;   // LTP + trade state
const POLL_SLOW_MS  = 15_000;  // config + candle info
const LOT_SIZE      = 65;

/* ─── Helpers ────────────────────────────────────────────────── */
function fmt(v, dec = 2) {
  if (v == null) return "—";
  return Number(v).toFixed(dec);
}

function fmtPnL(v) {
  if (v == null) return "—";
  const rounded = Math.round(v);
  return `${rounded >= 0 ? "+" : ""}₹${Math.abs(rounded).toLocaleString("en-IN")}`;
}

function isMarketOpen() {
  const d = new Date();
  if (d.getDay() === 0 || d.getDay() === 6) return false;
  const m = d.getHours() * 60 + d.getMinutes();
  return m >= 555 && m < 930;
}

function normalizeSymbol(sym) {
  if (!sym) return sym;
  return sym.replace(/\s+/g, "").toUpperCase();
}

/* ─── Design tokens (aligned with app-wide palette) ─────────── */
const C = {
  bg:        colors.bg?.primary    ?? "#020817",
  bgCard:    colors.bg?.secondary  ?? "#0f172a",
  bgSurf:    colors.bg?.tertiary   ?? "#1e293b",
  border:    colors.border?.light  ?? "#334155",
  borderDim: colors.border?.dark   ?? "#1a2540",
  text:      colors.text?.primary  ?? "#f1f5f9",
  textSec:   colors.text?.secondary ?? "#94a3b8",
  textMuted: colors.text?.muted    ?? "#4b6280",
  green:     colors.success        ?? "#10b981",
  greenDim:  "rgba(16,185,129,0.12)",
  red:       colors.danger         ?? "#ef4444",
  redDim:    "rgba(239,68,68,0.12)",
  amber:     colors.warning        ?? "#f59e0b",
  amberDim:  "rgba(245,158,11,0.12)",
  blue:      colors.primary        ?? "#3b82f6",
  blueDim:   "rgba(59,130,246,0.12)",
  // HA-specific accent (teal — distinct from SCALP amber and BB blue)
  ha:        "#14b8a6",
  haDim:     "rgba(20,184,166,0.12)",
};

const MONO = "'JetBrains Mono','Fira Code','Courier New',monospace";
const FONT = "'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif";

/* ─── Small atoms ────────────────────────────────────────────── */

function ModeBadge({ mode }) {
  const isLive = mode === "LIVE";
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, letterSpacing: "0.4px",
      padding: "2px 8px", borderRadius: 4,
      background: isLive ? C.redDim   : C.greenDim,
      color:      isLive ? C.red      : C.green,
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
      fontSize: 11, fontWeight: 700,
      padding: "2px 9px", borderRadius: 4,
      background: isCE ? C.greenDim : C.redDim,
      color:      isCE ? C.green    : C.red,
      border:     `1px solid ${isCE ? C.green : C.red}30`,
    }}>
      {side}
    </span>
  );
}

function StateBadge({ inTrade }) {
  return (
    <span style={{
      fontSize: 10, fontWeight: 600,
      padding: "2px 8px", borderRadius: 4, textTransform: "uppercase",
      background: inTrade ? C.amberDim : C.bgSurf,
      color:      inTrade ? C.amber    : C.textMuted,
      border:     `1px solid ${inTrade ? C.amber : C.borderDim}`,
    }}>
      {inTrade ? "● IN TRADE" : "○ IDLE"}
    </span>
  );
}

function SignalBadge({ action }) {
  if (!action) return <span style={{ color: C.textMuted, fontSize: 11 }}>—</span>;
  const isCE = action.includes("CE");
  return (
    <span style={{
      fontSize: 10, fontWeight: 700,
      padding: "2px 7px", borderRadius: 4,
      background: isCE ? "rgba(6,182,212,0.15)" : "rgba(249,115,22,0.15)",
      color:      isCE ? "#06b6d4"              : "#f97316",
      border:     `1px solid ${isCE ? "#06b6d4" : "#f97316"}40`,
    }}>
      {action === "ENTER_CE" ? "▲ CE" : "▼ PE"}
    </span>
  );
}

function HAColorDot({ isGreen }) {
  if (isGreen == null) return null;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      fontSize: 10, fontWeight: 600,
      color: isGreen ? C.green : C.red,
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: "50%",
        background: isGreen ? C.green : C.red,
        boxShadow: `0 0 5px ${isGreen ? C.green : C.red}80`,
      }} />
      {isGreen ? "GREEN" : "RED"}
    </span>
  );
}

/* ─── PriceRow: label + value with optional colour ────────────── */
function PriceRow({ label, value, color, mono = true, sub }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", padding: "3px 0", borderBottom: `1px solid ${C.borderDim}` }}>
      <span style={{ fontSize: 11, color: C.textMuted }}>{label}</span>
      <div style={{ textAlign: "right" }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: color ?? C.text, fontFamily: mono ? MONO : FONT }}>
          {value}
        </span>
        {sub && <div style={{ fontSize: 9, color: C.textMuted, marginTop: 1 }}>{sub}</div>}
      </div>
    </div>
  );
}

/* ─── Distance bar (shows proximity to SL/TP) ────────────────── */
function DistanceBar({ entry, current, sl, tp }) {
  if (!entry || !current || !sl || !tp) return null;
  const range  = tp - sl;
  if (range <= 0) return null;
  const pct    = Math.max(0, Math.min(100, ((current - sl) / range) * 100));
  const isProfit = current >= entry;
  const barColor = pct < 20 ? C.red : pct > 80 ? C.green : C.amber;
  return (
    <div style={{ margin: "8px 0 4px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: C.textMuted, marginBottom: 3 }}>
        <span>SL {fmt(sl)}</span>
        <span style={{ color: C.textSec, fontSize: 10, fontWeight: 600, fontFamily: MONO }}>
          {fmt(current)}
        </span>
        <span>TP {fmt(tp)}</span>
      </div>
      <div style={{ height: 4, background: C.borderDim, borderRadius: 2, overflow: "hidden" }}>
        <div style={{
          height: "100%", width: `${pct}%`,
          background: barColor, borderRadius: 2,
          transition: "width 0.5s ease",
        }} />
      </div>
    </div>
  );
}

/* ─── Main SlotCard ───────────────────────────────────────────── */
function SlotCard({ side, trade, ltp, lastCandle, config }) {
  const lots    = config?.quantity?.lots  ?? 1;
  const qty     = lots * LOT_SIZE;
  const inTrade = !!trade;

  const unrealized = inTrade && ltp && trade.entry_price
    ? (ltp - trade.entry_price) * (trade.qty || qty)
    : null;

  const slDist = inTrade && ltp && trade.sl_price
    ? (ltp - trade.sl_price).toFixed(2)
    : null;

  const tpDist = inTrade && ltp && trade.tp_price
    ? (trade.tp_price - ltp).toFixed(2)
    : null;

  const symbol = trade?.symbol ?? "—";
  const accent = side === "CE" ? C.green : C.red;

  return (
    <div style={{
      flex: 1,
      background: C.bgCard,
      border: `1px solid ${inTrade ? accent : C.borderDim}`,
      borderTop: `3px solid ${inTrade ? accent : C.borderDim}`,
      borderRadius: 8,
      padding: `${spacing.md}px`,
      display: "flex", flexDirection: "column", gap: spacing.sm,
      transition: "border-color 0.3s ease",
      minWidth: 0,
    }}>

      {/* ── Slot header ── */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <SideBadge side={side} />
        <StateBadge inTrade={inTrade} />
      </div>

      {/* ── Symbol + LTP ── */}
      <div style={{ borderBottom: `1px solid ${C.borderDim}`, paddingBottom: spacing.sm }}>
        <div style={{
          fontSize: 12, fontWeight: 700, color: C.text, fontFamily: MONO,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          marginBottom: 4,
        }}>
          {symbol}
        </div>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span style={{
            fontSize: 20, fontWeight: 700, fontFamily: MONO,
            color: unrealized != null
              ? unrealized > 0 ? C.green : unrealized < 0 ? C.red : C.text
              : C.text,
          }}>
            {ltp != null ? fmt(ltp) : "—"}
          </span>
          {inTrade && unrealized != null && (
            <span style={{
              fontSize: 12, fontWeight: 700, fontFamily: MONO,
              color: unrealized > 0 ? C.green : unrealized < 0 ? C.red : C.textMuted,
            }}>
              {fmtPnL(unrealized)}
            </span>
          )}
        </div>
      </div>

      {/* ── In trade: price levels ── */}
      {inTrade ? (
        <div>
          <PriceRow label="Entry"  value={fmt(trade.entry_price)} />
          <PriceRow
            label="SL"
            value={fmt(trade.sl_price)}
            color={C.red}
            sub={slDist ? `${Number(slDist) > 0 ? "+" : ""}${slDist} pts` : undefined}
          />
          <PriceRow
            label="TP"
            value={fmt(trade.tp_price)}
            color={C.green}
            sub={tpDist ? `${Number(tpDist) > 0 ? "+" : ""}${tpDist} pts` : undefined}
          />
          <PriceRow label="Qty"   value={`${trade.qty ?? qty} (${lots}L)`} color={C.textSec} />

          <DistanceBar
            entry={trade.entry_price}
            current={ltp}
            sl={trade.sl_price}
            tp={trade.tp_price}
          />
        </div>
      ) : (
        /* ── Idle: show config + last candle info ── */
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          <PriceRow
            label="Premium range"
            value={`${config?.option_premium?.min ?? "—"} – ${config?.option_premium?.max ?? "—"}`}
            color={C.textSec}
          />
          <PriceRow
            label="R:R"
            value={`1 : ${config?.risk_reward_ratio ?? "—"}`}
            color={C.textSec}
          />
          <PriceRow
            label="Lots"
            value={`${lots} × ${LOT_SIZE} = ${lots * LOT_SIZE}`}
            color={C.textSec}
          />

          {lastCandle && (
            <div style={{
              marginTop: 4, padding: "6px 8px",
              background: C.bgSurf, borderRadius: 5,
              border: `1px solid ${C.borderDim}`,
            }}>
              <div style={{ fontSize: 9, color: C.textMuted, marginBottom: 4, letterSpacing: "0.5px", textTransform: "uppercase" }}>
                Last HA Candle
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <HAColorDot isGreen={lastCandle.is_green} />
                <span style={{ fontSize: 10, fontFamily: MONO, color: C.textSec }}>
                  C:{fmt(lastCandle.ha_close)}
                </span>
                <span style={{ fontSize: 10, fontFamily: MONO, color: C.ha }}>
                  EMA:{lastCandle.ema20_low != null ? fmt(lastCandle.ema20_low) : "…"}
                </span>
              </div>
              {lastCandle.signal_action && (
                <div style={{ marginTop: 4 }}>
                  <SignalBadge action={lastCandle.signal_action} />
                  {lastCandle.signal_reason && (
                    <span style={{ fontSize: 9, color: C.textMuted, marginLeft: 6 }}>
                      {lastCandle.signal_reason}
                    </span>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ─── Compact side dot (for collapsed panel) ─────────────────── */
function CompactDot({ side, inTrade }) {
  const color = inTrade ? C.amber : C.textMuted;
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3 }}>
      <span style={{
        width: 8, height: 8, borderRadius: "50%", background: color,
        boxShadow: inTrade ? `0 0 8px ${C.amber}` : "none",
        animation: inTrade ? "haPulse 2s ease-in-out infinite" : "none",
      }} />
      <span style={{ fontSize: 8, color, fontWeight: 600 }}>{side}</span>
    </div>
  );
}

/* ─── Compact panel (not primary) ───────────────────────────────*/
function CompactView({ mode, ceTrade, peTrade, onBecomePrimary }) {
  return (
    <div
      onClick={onBecomePrimary}
      style={{
        height: "100%", display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center", gap: spacing.lg,
        cursor: "pointer", padding: spacing.md, background: C.bgCard,
        border: `1px solid ${C.borderDim}`, borderRadius: 8,
      }}
    >
      {/* Label */}
      <div style={{
        writingMode: "vertical-rl", textOrientation: "mixed",
        transform: "rotate(180deg)",
        fontSize: 11, fontWeight: 700, color: C.ha,
        letterSpacing: "1.5px", textTransform: "uppercase",
      }}>
        HA
      </div>

      {/* Slot dots */}
      <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
        <CompactDot side="CE" inTrade={!!ceTrade} />
        <CompactDot side="PE" inTrade={!!peTrade} />
      </div>

      {/* Divider */}
      <div style={{ width: 1, flex: 1, background: C.borderDim }} />

      {/* Mode badge — rotated */}
      <div style={{
        writingMode: "vertical-rl", transform: "rotate(180deg)",
        fontSize: 9, fontWeight: 600, textTransform: "uppercase",
        color: mode === "LIVE" ? C.red : C.green,
      }}>
        {mode === "LIVE" ? "LIVE" : "PAPER"}
      </div>
    </div>
  );
}

/* ─── Main component ─────────────────────────────────────────── */
export default function HAPanel({ ltpMap, isPrimary, onBecomePrimary }) {

  const [config,     setConfig]     = useState(null);
  const [openTrades, setOpenTrades] = useState([]);
  const [selection,  setSelection]  = useState({ CE: null, PE: null });
  const [lastCandle, setLastCandle] = useState({ CE: null, PE: null });
  const [todayStats, setTodayStats] = useState({ ce: 0, pe: 0, net: 0 });
  const [loading,    setLoading]    = useState(true);

  const pollRef = useRef(null);

  /* ── Config (slow poll) ── */
  const fetchConfig = useCallback(async () => {
    try {
      const c = await getStrategyConfig(STRATEGY_ID);
      setConfig(c ?? null);
    } catch { /* keep last */ }
  }, []);

  /* ── Open trades for HA_V1 from paper_trades ── */
  const fetchTrades = useCallback(async () => {
    try {
      const res  = await fetch(`${getApiBase()}/paper_trades`);
      if (!res.ok) return;
      const data = await res.json();
      const open = (data?.open ?? []).filter(
        (t) => t.strategy_name === STRATEGY_ID || t.strategy_name === "HA"
      );
      setOpenTrades(open);

      // Today's closed stats
      const closed = (data?.closed ?? []).filter(
        (t) => (t.strategy_name === STRATEGY_ID || t.strategy_name === "HA")
      );
      const ce  = closed.filter(t => t.symbol?.endsWith("CE")).length;
      const pe  = closed.filter(t => t.symbol?.endsWith("PE")).length;
      const net = closed.reduce((s, t) => s + (t.pnl_value ?? 0), 0);
      setTodayStats({ ce, pe, net });
    } catch { /* keep last */ }
    finally { setLoading(false); }
  }, []);

  /* ── Selection: read SCALP_V1 selection (HA uses it) ── */
  const fetchSelection = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/selection/current?strategy_id=SCALP_V1`);
      if (!res.ok) return;
      const data = await res.json();
      const ce   = data?.CE?.[0] ?? null;
      const pe   = data?.PE?.[0] ?? null;
      setSelection({ CE: ce, PE: pe });
    } catch { /* keep last */ }
  }, []);

  /* ── Last HA candle per selected symbol ── */
  const fetchLastCandles = useCallback(async () => {
    for (const side of ["CE", "PE"]) {
      const sym = selection[side]?.symbol ?? selection[side]?.tradingsymbol;
      if (!sym) continue;
      try {
        const res = await fetch(
          `${getApiBase()}/api/ha_candles/latest?symbol=${sym}&timeframe=1m`
        );
        if (!res.ok) continue;
        const data = await res.json();
        setLastCandle(prev => ({ ...prev, [side]: data ?? null }));
      } catch { /* best-effort */ }
    }
  }, [selection]);

  /* ── Polling ── */
  useEffect(() => {
    fetchConfig();
    fetchTrades();
    fetchSelection();

    const fast = setInterval(() => {
      fetchTrades();
      fetchLastCandles();
    }, POLL_FAST_MS);

    const slow = setInterval(() => {
      fetchConfig();
      fetchSelection();
    }, POLL_SLOW_MS);

    pollRef.current = { fast, slow };
    return () => { clearInterval(fast); clearInterval(slow); };
  }, [fetchConfig, fetchTrades, fetchSelection, fetchLastCandles]);

  useEffect(() => {
    fetchLastCandles();
  }, [fetchLastCandles]);

  /* ── Derived ── */
  const mode    = config?.trade_execution_mode ?? "PAPER";
  const ceTrade = openTrades.find(t => t.symbol?.endsWith("CE")) ?? null;
  const peTrade = openTrades.find(t => t.symbol?.endsWith("PE")) ?? null;

  // LTP for selected symbols
  const ceSym   = ceTrade?.symbol ?? selection.CE?.symbol ?? selection.CE?.tradingsymbol;
  const peSym   = peTrade?.symbol ?? selection.PE?.symbol ?? selection.PE?.tradingsymbol;
  const ceLtp   = ceSym ? (ltpMap[normalizeSymbol(ceSym)] ?? null) : null;
  const peLtp   = peSym ? (ltpMap[normalizeSymbol(peSym)] ?? null) : null;

  const sessionStart = config?.session?.primary?.start ?? "09:15";
  const sessionEnd   = config?.session?.primary?.end   ?? "15:20";
  const rr           = config?.risk_reward_ratio ?? "—";
  const inSession    = isMarketOpen();

  /* ── Compact mode ─────────────────────────────────────────── */
  if (!isPrimary) {
    return (
      <>
        <CompactView
          mode={mode}
          ceTrade={ceTrade}
          peTrade={peTrade}
          onBecomePrimary={onBecomePrimary}
        />
        <style>{`
          @keyframes haPulse {
            0%, 100% { opacity: 1; }
            50%       { opacity: 0.4; }
          }
        `}</style>
      </>
    );
  }

  /* ── Primary mode ─────────────────────────────────────────── */
  return (
    <div style={{
      background: C.bg, border: `1px solid ${C.border}`,
      borderRadius: 8, overflow: "hidden",
      display: "flex", flexDirection: "column", height: "100%",
      fontFamily: FONT,
    }}>

      {/* ════ Header ════════════════════════════════════════════ */}
      <div style={{
        display: "flex", alignItems: "center", gap: spacing.md,
        padding: "10px 14px",
        background: C.bgCard,
        borderBottom: `1px solid ${C.borderDim}`,
        flexShrink: 0, flexWrap: "wrap",
      }}>
        {/* Strategy label */}
        <div style={{
          fontSize: 12, fontWeight: 800, color: C.ha,
          letterSpacing: "1px", textTransform: "uppercase",
        }}>
          HA
        </div>
        <div style={{ fontSize: 11, color: C.textMuted }}>
          Heikin Ashi · 1m · NIFTY Options
        </div>

        <div style={{ flex: 1 }} />

        {/* Session indicator */}
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{
            width: 6, height: 6, borderRadius: "50%",
            background: inSession ? C.green : C.textMuted,
            boxShadow: inSession ? `0 0 6px ${C.green}` : "none",
          }} />
          <span style={{ fontSize: 10, color: inSession ? C.green : C.textMuted, fontWeight: 600 }}>
            {sessionStart} – {sessionEnd}
          </span>
        </div>

        <ModeBadge mode={mode} />
      </div>

      {/* ════ Config strip ══════════════════════════════════════ */}
      <div style={{
        display: "flex", alignItems: "center", gap: 16,
        padding: "6px 14px",
        background: C.bgSurf, borderBottom: `1px solid ${C.borderDim}`,
        flexShrink: 0, flexWrap: "wrap", overflowX: "auto",
      }}>
        {[
          { label: "R:R",      value: `1 : ${rr}` },
          { label: "Premium",  value: `₹${config?.option_premium?.min ?? "—"} – ₹${config?.option_premium?.max ?? "—"}` },
          { label: "Lots",     value: config?.quantity?.lots ?? "—" },
          { label: "Side",     value: config?.trade_side_mode ?? "BOTH" },
          { label: "CE today", value: todayStats.ce, color: C.green },
          { label: "PE today", value: todayStats.pe, color: C.red },
          {
            label: "Net P&L",
            value: todayStats.net !== 0 ? fmtPnL(todayStats.net) : "—",
            color: todayStats.net > 0 ? C.green : todayStats.net < 0 ? C.red : C.textMuted,
          },
        ].map((s, i) => (
          <div key={i} style={{ display: "flex", flexDirection: "column", gap: 1, flexShrink: 0 }}>
            <span style={{ fontSize: 8, color: C.textMuted, letterSpacing: "0.5px", textTransform: "uppercase", fontWeight: 600 }}>
              {s.label}
            </span>
            <span style={{ fontSize: 12, fontWeight: 700, color: s.color ?? C.text, fontFamily: MONO }}>
              {s.value}
            </span>
          </div>
        ))}
      </div>

      {/* ════ Slot Cards ════════════════════════════════════════ */}
      {loading ? (
        <div style={{
          flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
          color: C.textMuted, fontSize: 12,
        }}>
          Loading…
        </div>
      ) : (
        <div style={{
          flex: 1, display: "flex", gap: spacing.md,
          padding: spacing.md, minHeight: 0, overflowY: "auto",
        }}>
          <SlotCard
            side="CE"
            trade={ceTrade}
            ltp={ceLtp}
            lastCandle={lastCandle.CE}
            config={config}
          />
          <SlotCard
            side="PE"
            trade={peTrade}
            ltp={peLtp}
            lastCandle={lastCandle.PE}
            config={config}
          />
        </div>
      )}

      {/* ════ Footer: entry conditions legend ═══════════════════ */}
      <div style={{
        borderTop: `1px solid ${C.borderDim}`,
        padding: "6px 14px",
        background: C.bgCard,
        flexShrink: 0,
      }}>
        <div style={{ display: "flex", gap: spacing.lg, flexWrap: "wrap" }}>
          {[
            { label: "Cond 1", desc: "RED→EMA→GREEN" },
            { label: "Cond 2", desc: "GREEN+EMA, prev RED" },
            { label: "Cond 3", desc: "GREEN+EMA, prev GREEN↑RED" },
          ].map((c, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <span style={{
                fontSize: 9, fontWeight: 700, padding: "1px 5px",
                borderRadius: 3, background: C.haDim, color: C.ha,
                border: `1px solid ${C.ha}30`,
              }}>
                {c.label}
              </span>
              <span style={{ fontSize: 9, color: C.textMuted }}>{c.desc}</span>
            </div>
          ))}
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: 9, color: C.textMuted }}>
            SL check: candle close only
          </span>
        </div>
      </div>

      <style>{`
        @keyframes haPulse {
          0%, 100% { opacity: 1; }
          50%       { opacity: 0.4; }
        }
      `}</style>
    </div>
  );
}