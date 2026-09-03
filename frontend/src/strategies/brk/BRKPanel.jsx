// frontend/src/strategies/brk/BRKPanel.jsx
//
// ── BRK_V1 dashboard panel · v2 ── (fleet-grade rework, 2026-09-03)
// Day-1 feedback: v1 was static and thin. v2 brings it to ScalpV3Panel
// parity where BRK has an equivalent concept:
//   * LIVE ticking via the ltpMap prop (LTPStore snapshot — the engine
//     publishes every quote it takes): watched premiums tick against the
//     ₹180 break line while WATCHING, and the open position marks to
//     market between SL and TP on a position-track bar.
//   * Header day P&L = closed rows + open MTM, ticking.
//   * OCO GTT badge, PREFIX-GUARD banner, session chips, today's rows.
// Structure (sessions/position/rows) still comes from /api/brk/state
// (4s poll); prices tick at ltpMap cadence between polls.
// Square-off stays TWO-TAP (window.confirm is swallowed by Tauri webview).

import { useEffect, useMemo, useRef, useState } from "react";
import { getApiBase } from "../../api/base";
import { colors, spacing, pnlStyle } from "../../tokens";
import { stratName } from "../displayNames";                      // ── UI_MASK ──

const ACCENT = "#f59e0b";
const BREAK_LEVEL = 180;

function normalizeSymbol(sym) {
  if (!sym) return sym;
  return sym.replace(/\s+/g, "").toUpperCase();
}
function fmtInr(v) {
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(v);
  const s = a >= 100 ? Math.round(a).toLocaleString("en-IN") : a.toFixed(2);
  return `${v < 0 ? "−" : ""}₹${s}`;
}
function fmt2(v) { return v == null || isNaN(v) ? "—" : Number(v).toFixed(2); }

export default function BRKPanel({ strategyId = "BRK_V1", ltpMap = {} }) {
  const [st, setSt] = useState(null);
  const [armed, setArmed] = useState(false);
  const armTimer = useRef(null);

  useEffect(() => {
    let alive = true;
    const pull = async () => {
      try {
        const r = await fetch(`${getApiBase()}/api/brk/state`);
        const d = await r.json();
        if (alive) setSt(d);
      } catch { /* backend down — keep last */ }
    };
    pull();
    const t = setInterval(pull, 4000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const squareOff = async () => {
    if (!armed) {
      setArmed(true);
      clearTimeout(armTimer.current);
      armTimer.current = setTimeout(() => setArmed(false), 4000);
      return;
    }
    setArmed(false);
    clearTimeout(armTimer.current);
    try { await fetch(`${getApiBase()}/api/brk/square_off`, { method: "POST" }); } catch { }
  };

  const pos = st?.position;
  const sessions = st?.sessions || [];
  const trades = st?.trades || [];

  // ── live marks ──
  const liveLtp = (sym) => {
    if (!sym) return null;
    const v = ltpMap[normalizeSymbol(sym)];
    return typeof v === "number" && v > 0 ? v : null;
  };
  const posLtp = liveLtp(pos?.symbol);
  const openMtm = pos && posLtp != null ? (posLtp - pos.entry) * pos.qty : null;
  const closedPnl = trades
    .filter((t) => t.state === "CLOSED")
    .reduce((a, t) => a + ((t.exit_price ?? 0) - t.entry_price) * t.qty, 0);
  const dayTotal = closedPnl + (openMtm ?? 0);

  // SL→TP position track: 0 at SL, 1 at TP (or entry+SL-distance headroom
  // when TP is off).
  const track = useMemo(() => {
    if (!pos || posLtp == null) return null;
    const lo = pos.sl;
    const hi = pos.tp ?? pos.entry + (pos.entry - pos.sl);
    if (hi <= lo) return null;
    const frac = Math.min(1, Math.max(0, (posLtp - lo) / (hi - lo)));
    const entryFrac = Math.min(1, Math.max(0, (pos.entry - lo) / (hi - lo)));
    return { frac, entryFrac };
  }, [pos, posLtp]);

  const card = {
    background: colors.bg.secondary, borderRadius: 10,
    padding: spacing.md, marginBottom: spacing.md,
    border: `1px solid ${colors.border.subtle}`,
  };
  const label = { fontSize: 11, color: colors.text.muted, textTransform: "uppercase", letterSpacing: 0.5 };
  const chip = (bg, fg, text) => (
    <span style={{ background: bg, color: fg, borderRadius: 6, padding: "2px 8px", fontSize: 11, fontWeight: 600, whiteSpace: "nowrap" }}>{text}</span>
  );

  // one watched side (CE or PE) while WATCHING: live premium vs the ₹180 line
  const WatchRow = ({ side, sym, selPrint }) => {
    const ltp = liveLtp(sym);
    const pct = ltp != null ? Math.min(1, Math.max(0, ltp / (BREAK_LEVEL * 1.25))) : null;
    const over = ltp != null && ltp >= BREAK_LEVEL;
    return (
      <div style={{ display: "flex", alignItems: "center", gap: spacing.md, fontSize: 12, padding: "3px 0" }}>
        <span style={{ width: 26, color: colors.text.muted }}>{side}</span>
        <b style={{ minWidth: 170 }}>{sym || "—"}</b>
        <span style={{ color: colors.text.muted }}>sel {fmt2(selPrint)}</span>
        <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 700, color: over ? "#4ade80" : colors.text.primary }}>
          {ltp != null ? fmt2(ltp) : "…"}
        </span>
        <div style={{ flex: 1, height: 6, background: colors.bg.tertiary, borderRadius: 3, position: "relative", overflow: "hidden" }}>
          {pct != null && (
            <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${pct * 100}%`, background: over ? "#4ade80" : ACCENT, opacity: 0.8, borderRadius: 3 }} />
          )}
          {/* the ₹180 break line sits at 180/225 of the bar */}
          <div style={{ position: "absolute", left: `${(BREAK_LEVEL / (BREAK_LEVEL * 1.25)) * 100}%`, top: 0, bottom: 0, width: 2, background: "#f87171" }} />
        </div>
        <span style={{ width: 66, textAlign: "right", color: over ? "#4ade80" : colors.text.muted }}>
          {ltp != null ? `${over ? "+" : ""}${(ltp - BREAK_LEVEL).toFixed(2)}` : ""}
        </span>
      </div>
    );
  };

  return (
    <div style={{ padding: spacing.md }}>
      {/* header strip: identity + health + LIVE day total */}
      <div style={{ ...card, display: "flex", gap: spacing.md, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontWeight: 700, color: ACCENT, fontSize: 15 }}>{stratName(strategyId)}</span>
        {chip(st?.running ? "#14351f" : "#3a1420", st?.running ? "#4ade80" : "#f87171",
          st?.running ? "ENGINE UP" : "ENGINE DOWN")}
        {st?.frozen && chip("#3a1420", "#f87171", "PREFIX-GUARD FROZEN — no decisions today")}
        <span style={{ marginLeft: "auto", display: "flex", gap: spacing.lg, alignItems: "baseline" }}>
          {pos && openMtm != null && (
            <span style={label}>Open MTM <b style={{ ...pnlStyle(openMtm), fontSize: 14, fontVariantNumeric: "tabular-nums" }}>{fmtInr(openMtm)}</b></span>
          )}
          <span style={label}>Day <b style={{ ...pnlStyle(dayTotal), fontSize: 15, fontVariantNumeric: "tabular-nums" }}>{fmtInr(dayTotal)}</b></span>
        </span>
      </div>

      {/* sessions — live premiums tick against the break line while watching */}
      <div style={card}>
        <div style={{ ...label, marginBottom: 8 }}>Sessions · break ≥ ₹{BREAK_LEVEL} on a 1m close</div>
        {sessions.length === 0 && <div style={{ fontSize: 12, color: colors.text.muted }}>Waiting for the day to arm.</div>}
        {sessions.map((s) => (
          <div key={s.tag} style={{ padding: "6px 0", borderBottom: `1px solid ${colors.border.subtle}` }}>
            <div style={{ display: "flex", alignItems: "center", gap: spacing.md, marginBottom: 4 }}>
              <b style={{ width: 64 }}>{s.tag}</b>
              <span style={{ marginLeft: "auto" }}>
                {s.entered ? chip("#14351f", "#4ade80", "ENTERED")
                  : s.done ? chip(colors.bg.tertiary, colors.text.muted, "CLOSED / NO TRADE")
                    : chip("#1c2a4a", "#93c5fd", "WATCHING")}
              </span>
            </div>
            {!s.done || s.entered ? (
              <>
                <WatchRow side="CE" sym={s.ce_sym} selPrint={s.sel_prints?.CE} />
                <WatchRow side="PE" sym={s.pe_sym} selPrint={s.sel_prints?.PE} />
              </>
            ) : (
              <div style={{ fontSize: 12, color: colors.text.muted }}>
                CE {s.ce_sym || "—"}{s.sel_prints?.CE ? ` @ ${s.sel_prints.CE}` : ""} · PE {s.pe_sym || "—"}{s.sel_prints?.PE ? ` @ ${s.sel_prints.PE}` : ""}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* open position — MTM + SL↔TP track */}
      <div style={card}>
        <div style={{ ...label, marginBottom: 8 }}>Open Position</div>
        {!pos && <div style={{ fontSize: 12, color: colors.text.muted }}>Flat.</div>}
        {pos && (
          <>
            <div style={{ fontSize: 13, display: "flex", gap: spacing.lg, flexWrap: "wrap", alignItems: "center", marginBottom: 8 }}>
              <b>{pos.symbol}</b>
              <span>{pos.tag} · {pos.mode}</span>
              <span>qty {pos.qty}</span>
              {pos.mode === "LIVE" && (pos.gtt_id
                ? chip("#14351f", "#4ade80", `OCO GTT ${pos.gtt_id}`)
                : chip("#3a1420", "#f87171", "NO GTT — engine ticks only"))}
              <span style={{ marginLeft: "auto", fontVariantNumeric: "tabular-nums" }}>
                LTP <b style={{ fontSize: 15 }}>{posLtp != null ? fmt2(posLtp) : "…"}</b>
                {openMtm != null && <b style={{ ...pnlStyle(openMtm), marginLeft: 10 }}>{fmtInr(openMtm)}</b>}
              </span>
              <button onClick={squareOff}
                style={{
                  cursor: "pointer", borderRadius: 8, padding: "6px 14px",
                  fontWeight: 700, fontSize: 12,
                  border: `1px solid ${armed ? "#f87171" : colors.border.subtle}`,
                  background: armed ? "#3a1420" : colors.bg.tertiary,
                  color: armed ? "#f87171" : colors.text.primary,
                }}>
                {armed ? "TAP AGAIN TO SQUARE OFF" : "Square off"}
              </button>
            </div>
            <div style={{ position: "relative", height: 10, background: colors.bg.tertiary, borderRadius: 5, overflow: "hidden" }}>
              {track && (
                <div style={{
                  position: "absolute", left: 0, top: 0, bottom: 0,
                  width: `${track.frac * 100}%`, borderRadius: 5,
                  background: `linear-gradient(90deg, #f87171, ${ACCENT} ${track.entryFrac * 100}%, #4ade80)`,
                  opacity: 0.85,
                }} />
              )}
              {track && (
                <div style={{ position: "absolute", left: `calc(${track.entryFrac * 100}% - 1px)`, top: 0, bottom: 0, width: 2, background: colors.text.primary, opacity: 0.7 }} />
              )}
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: colors.text.muted, marginTop: 4, fontVariantNumeric: "tabular-nums" }}>
              <span>SL {fmt2(pos.sl)}</span>
              <span>entry {fmt2(pos.entry)}</span>
              <span>{pos.tp != null ? `TP ${fmt2(pos.tp)}` : "no TP"}</span>
            </div>
          </>
        )}
      </div>

      {/* today's rows */}
      <div style={card}>
        <div style={{ ...label, marginBottom: 8 }}>Today</div>
        {trades.length === 0 && <div style={{ fontSize: 12, color: colors.text.muted }}>No trades yet.</div>}
        {trades.map((t) => {
          const pnl = t.state === "CLOSED" ? ((t.exit_price ?? 0) - t.entry_price) * t.qty : null;
          return (
            <div key={t.paper_trade_id} style={{ display: "flex", gap: spacing.md, fontSize: 12, padding: "3px 0", fontVariantNumeric: "tabular-nums" }}>
              <span style={{ width: 56 }}>{t.group_id || "BRK"}</span>
              <b>{t.symbol}</b>
              <span>{t.trade_mode}</span>
              <span>{fmt2(t.entry_price)} → {t.state === "CLOSED" ? `${fmt2(t.exit_price)} (${t.exit_reason})` : "open"}</span>
              <span style={{ marginLeft: "auto", ...(pnl == null ? {} : pnlStyle(pnl)) }}>
                {pnl == null ? "—" : fmtInr(pnl)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}