// frontend/src/components/fleet/FleetKpis.jsx — ── DASH_MODERN_20260925 ──
// ── FLEET_KPIS_V2_20260925 ── redesigned as a two-row BOOK LEDGER instead of
// ── LIVE_ROW_HIDE_20260925 ── a book with no orders today is not shown at all.
// ── FLEET_KPIS_PERIOD_20260928 ── risk-to-stops / positions / path columns replaced
// by Week P/L and Month P/L (net realised, per book, from the same feed).
// six tiles: one row for Live, one for Paper, the same eight columns for both
// (MTM today, realised, unrealised, charges, net, from peak, risk to stops,
// positions) plus a sparkline, and the session block on the right. Live and
// Paper are still never summed; an idle book dims and says so instead of
// spending a quarter of the strip on a zero. Every value comes from
// /api/fleet/today (the same feed the chart and blotter read).

import { colors, spacing, typography, pnlStyle } from "../../tokens";
import { MARKET_START_MIN, FNO_END_MIN } from "../../marketSession";
import { fmtInr, hhmm } from "./fleetFormat";

const COLS = "86px minmax(176px, 1.35fr) repeat(7, minmax(112px, 1fr))";
const ROW_H = 48;
const BOOKS = [["LIVE", "Live"], ["PAPER", "Paper"]];   // ── LIVE_ROW_HIDE_20260925 ──

// ── LIVE_ROW_HIDE_20260925 ── the one row shown while no book has traded
function NoteRow({ text }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: COLS, alignItems: "center", height: ROW_H, borderTop: `1px solid ${colors.border.dark}` }}>
      <span style={{ gridColumn: "1 / -1", padding: "0 12px", fontSize: 12.5, color: colors.text.muted }}>{text}</span>
    </div>
  );
}

function BookTag({ live, children }) {
  const c = live ? colors.warning : colors.text.secondary;
  return (
    <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.3px", padding: "2px 8px", borderRadius: 4,
      border: `1px solid ${c}`, color: c, whiteSpace: "nowrap" }}>
      {children}
    </span>
  );
}

const cell = (align = "right") => ({ padding: "0 12px", textAlign: align, whiteSpace: "nowrap", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" });
const num = { ...typography.mono, fontSize: 14, fontWeight: 500 };

function BookRow({ book, label, t }) {
  const idle = !t || !t.rows;
  const dim = { color: colors.text.muted, fontWeight: 400 };
  const money = (v, tone = "pnl", extra = {}) => (
    idle ? <span style={dim}>—</span>
      : <span style={{ ...(tone === "pnl" ? pnlStyle(v) : {}), ...extra }}>{fmtInr(v)}</span>
  );
  const peakTitle = !idle && t.peak != null ? `peak ${fmtInr(t.peak)}${t.peak_min != null ? ` at ${hhmm(t.peak_min)}` : ""}` : undefined;
  return (
    <div style={{ display: "grid", gridTemplateColumns: COLS, alignItems: "center", height: ROW_H,
      borderTop: `1px solid ${colors.border.dark}`, opacity: idle ? 0.6 : 1 }}>
      <span style={cell("left")}><BookTag live={book === "LIVE"}>{label}</BookTag></span>
      <span style={{ ...cell(), ...typography.mono, fontSize: 23, fontWeight: 700, letterSpacing: "-0.01em", ...pnlStyle(idle ? 0 : t.gross), ...(idle ? dim : {}) }}>
        {!idle && t.approx ? "≈" : ""}{fmtInr(idle ? 0 : t.gross)}
      </span>
      <span style={{ ...cell(), ...num }}>{money(t?.realised ?? 0)}</span>
      <span style={{ ...cell(), ...num }}>{money(t?.unrealised ?? 0)}</span>
      <span style={{ ...cell(), ...num, color: colors.text.muted }}>{idle ? "—" : `₹${Math.round(t.charges || 0).toLocaleString("en-IN")}`}</span>
      <span style={{ ...cell(), ...num, fontWeight: 600 }}>{money(t?.net ?? 0)}</span>
      <span style={{ ...cell(), ...num }} title={peakTitle}>{idle || t.peak == null ? <span style={dim}>—</span> : money((t.gross ?? 0) - t.peak)}</span>
      <span style={{ ...cell(), ...num }} title={idle ? undefined : `${t.week_trades ?? 0} trades exited since ${t.week_from || "—"}, net of charges`}>
        {idle ? <span style={dim}>—</span> : <span style={pnlStyle(t.week_net ?? 0)}>{t.week_approx ? "≈" : ""}{fmtInr(t.week_net ?? 0)}</span>}
      </span>
      <span style={{ ...cell(), ...num }} title={idle ? undefined : `${t.month_trades ?? 0} trades exited since ${t.month_from || "—"}, net of charges`}>
        {idle ? <span style={dim}>—</span> : <span style={pnlStyle(t.month_net ?? 0)}>{t.month_approx ? "≈" : ""}{fmtInr(t.month_net ?? 0)}</span>}
      </span>
    </div>
  );
}

export default function FleetKpis({ totals, active, fleet, nowMin }) {
  const left = Math.max(0, FNO_END_MIN - nowMin);
  const session = nowMin < MARKET_START_MIN ? `opens ${hhmm(MARKET_START_MIN)}`
    : left > 0 ? `closes in ${Math.floor(left / 60)}h ${String(left % 60).padStart(2, "0")}m` : "market closed";
  const head = { ...cell(), fontSize: 11, color: colors.text.muted, fontWeight: 500 };
  return (
    <div style={{ display: "flex", background: colors.bg.secondary, border: `1px solid ${colors.border.light}`, borderRadius: 8,
      overflow: "hidden", fontFamily: "var(--c-font-ui)", boxShadow: "0 1px 3px var(--c-shadow)" }}>
      <div style={{ flex: 1, minWidth: 0, overflowX: "auto" }}>
        <div style={{ minWidth: 1040, padding: `${spacing.sm}px ${spacing.xs}px 0` }}>
          <div style={{ display: "grid", gridTemplateColumns: COLS, alignItems: "baseline", paddingBottom: 6 }}>
            <span style={{ ...head, textAlign: "left" }}>Book</span>
            <span style={head}>MTM today</span>
            <span style={head}>Realised</span>
            <span style={head}>Unrealised</span>
            <span style={head}>Charges</span>
            <span style={head}>Net</span>
            <span style={head} title="MTM now minus today's highest MTM (sampled each minute)">From peak</span>
            <span style={head} title="Net realised P&L of trades exited in the last 7 calendar days, today included">Week P/L</span>
            <span style={head} title="Net realised P&L of trades exited since this day last month">Month P/L</span>
          </div>
          {/* ── LIVE_ROW_HIDE_20260925 ── a book with no orders today is not shown at all;
              when neither book has traded, one quiet row says so. */}
          {(() => {
            if (!totals) return <NoteRow text="loading books…" />;
            const active = BOOKS.filter(([k]) => totals[k] && totals[k].rows > 0);
            if (!active.length) return <NoteRow text="No orders yet today — Live and Paper rows appear at the first fill." />;
            return active.map(([k, label]) => <BookRow key={k} book={k} label={label} t={totals[k]} />);
          })()}
        </div>
      </div>
      <div style={{ flex: "0 0 auto", borderLeft: `1px solid ${colors.border.dark}`, padding: `${spacing.sm}px ${spacing.xl}px`,
        display: "flex", flexDirection: "column", justifyContent: "center", minWidth: 168 }}>
        <div style={{ fontSize: 11, color: colors.text.muted }}>Traded today</div>
        <div style={{ ...typography.mono, fontSize: 28, fontWeight: 700, lineHeight: 1.1, whiteSpace: "nowrap", marginTop: 2 }}>
          {active ?? "—"}<span style={{ color: colors.text.muted, fontSize: 14, fontWeight: 400 }}> of {fleet ?? "—"}</span>
        </div>
        <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 3, whiteSpace: "nowrap" }}>{session}</div>
      </div>
    </div>
  );
}
