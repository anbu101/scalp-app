// frontend/src/strategies/brk/BRKPanel.jsx
//
// ── BRK_V1 dashboard panel ── (VETPanel conventions, 2026-09-02)
// 09:25 premium breakout scalp: nearest-below ₹180 per side at 09:25, buy
// whichever side's 09:29 close holds ≥₹180 at 09:30; SL −16 / TP +46 on the
// bought premium, EOD 15:15; optional 10:25 second session (Config B).
// The panel's job is health + selection + position + today's rows. The
// engine-health strip surfaces the PREFIX-GUARD frozen flag — the "why is
// it not trading?" answer — because silent failure is the enemy.
// Square-off is TWO-TAP (arm → confirm): window.confirm is silently
// swallowed by Tauri's webview, so it is never used here.

import { useEffect, useRef, useState } from "react";
import { getApiBase } from "../../api/base";
import { colors, spacing, pnlStyle } from "../../tokens";
import { stratName } from "../displayNames";                      // ── UI_MASK ──

const ACCENT = "#f59e0b";

function fmtInr(v) {
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(Math.round(v));
  return `${v < 0 ? "−" : ""}₹${a.toLocaleString("en-IN")}`;
}

export default function BRKPanel({ strategyId = "BRK_V1" }) {
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
  const closed = trades.filter((t) => t.state === "CLOSED");
  const dayNet = closed.reduce(
    (a, t) => a + ((t.exit_price ?? 0) - t.entry_price) * t.qty, 0);

  const card = {
    background: colors.bg.secondary, borderRadius: 10,
    padding: spacing.md, marginBottom: spacing.md,
    border: `1px solid ${colors.border.subtle}`,
  };
  const label = { fontSize: 11, color: colors.text.muted, textTransform: "uppercase", letterSpacing: 0.5 };
  const chip = (bg, fg, text) => (
    <span style={{ background: bg, color: fg, borderRadius: 6, padding: "2px 8px", fontSize: 11, fontWeight: 600 }}>{text}</span>
  );

  return (
    <div style={{ padding: spacing.md }}>
      {/* health strip */}
      <div style={{ ...card, display: "flex", gap: spacing.md, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontWeight: 700, color: ACCENT }}>{stratName(strategyId)}</span>
        {chip(st?.running ? "#14351f" : "#3a1420", st?.running ? "#4ade80" : "#f87171",
          st?.running ? "ENGINE UP" : "ENGINE DOWN")}
        {st?.frozen && chip("#3a1420", "#f87171", "PREFIX-GUARD FROZEN — no decisions today")}
        <span style={{ marginLeft: "auto", ...label }}>Day (closed): <b style={pnlStyle(dayNet)}>{fmtInr(dayNet)}</b></span>
      </div>

      {/* sessions */}
      <div style={card}>
        <div style={{ ...label, marginBottom: 8 }}>Sessions</div>
        {sessions.length === 0 && <div style={{ fontSize: 12, color: colors.text.muted }}>Waiting for the day to arm (engine rolls at first tick).</div>}
        {sessions.map((s) => (
          <div key={s.tag} style={{ display: "flex", gap: spacing.md, fontSize: 12, padding: "4px 0", alignItems: "center" }}>
            <b style={{ width: 64 }}>{s.tag}</b>
            <span>CE {s.ce_sym || "—"}{s.sel_prints?.CE ? ` @ ${s.sel_prints.CE}` : ""}</span>
            <span>PE {s.pe_sym || "—"}{s.sel_prints?.PE ? ` @ ${s.sel_prints.PE}` : ""}</span>
            <span style={{ marginLeft: "auto" }}>
              {s.entered ? chip("#14351f", "#4ade80", "ENTERED")
                : s.done ? chip(colors.bg.tertiary, colors.text.muted, "CLOSED / NO TRADE")
                  : chip("#1c2a4a", "#93c5fd", "WATCHING")}
            </span>
          </div>
        ))}
      </div>

      {/* open position */}
      <div style={card}>
        <div style={{ ...label, marginBottom: 8 }}>Open Position</div>
        {!pos && <div style={{ fontSize: 12, color: colors.text.muted }}>Flat.</div>}
        {pos && (
          <div style={{ fontSize: 13, display: "flex", gap: spacing.lg, flexWrap: "wrap", alignItems: "center" }}>
            <b>{pos.symbol}</b>
            <span>{pos.tag} · {pos.mode}</span>
            <span>entry {pos.entry}</span>
            <span>SL {pos.sl}</span>
            <span>TP {pos.tp ?? "—"}</span>
            <span>qty {pos.qty}</span>
            {pos.mode === "LIVE" && (pos.gtt_id
              ? chip("#14351f", "#4ade80", `OCO GTT ${pos.gtt_id}`)
              : chip("#3a1420", "#f87171", "NO GTT — engine ticks only"))}
            <button onClick={squareOff}
              style={{
                marginLeft: "auto", cursor: "pointer", borderRadius: 8,
                padding: "6px 14px", fontWeight: 700, fontSize: 12,
                border: `1px solid ${armed ? "#f87171" : colors.border.subtle}`,
                background: armed ? "#3a1420" : colors.bg.tertiary,
                color: armed ? "#f87171" : colors.text.primary,
              }}>
              {armed ? "TAP AGAIN TO SQUARE OFF" : "Square off"}
            </button>
          </div>
        )}
      </div>

      {/* today's rows */}
      <div style={card}>
        <div style={{ ...label, marginBottom: 8 }}>Today</div>
        {trades.length === 0 && <div style={{ fontSize: 12, color: colors.text.muted }}>No trades yet.</div>}
        {trades.map((t) => {
          const pnl = t.state === "CLOSED" ? ((t.exit_price ?? 0) - t.entry_price) * t.qty : null;
          return (
            <div key={t.paper_trade_id} style={{ display: "flex", gap: spacing.md, fontSize: 12, padding: "3px 0" }}>
              <span style={{ width: 56 }}>{t.group_id || "BRK"}</span>
              <b>{t.symbol}</b>
              <span>{t.trade_mode}</span>
              <span>{t.entry_price} → {t.state === "CLOSED" ? `${t.exit_price} (${t.exit_reason})` : "open"}</span>
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