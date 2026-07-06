/**
 * IC_V1 PANEL
 *
 * Intended path: src/strategies/ic_v1/ICV1Panel.jsx
 *
 * Dashboard panel for the time-entry NIFTY weekly iron condor. IC has no
 * selection/surveillance phase — before entry_time the panel shows the
 * schedule; after entry it shows the 4-leg group (L1/L2 shorts · L3/L4
 * wings) with live SL/MTC state.
 *
 * Data: GET /api/ic_v1/state every 5s (getICV1State).
 * Action: manual square-off via a TWO-TAP arm/confirm button + inline
 * status banner — window.confirm is silently blocked in Tauri's webview
 * (house learning), so no browser dialogs anywhere.
 *
 * Exit-reason vocabulary shown verbatim from the engine: SL, TP, MTC_COST
 * (survivor scratched at its cost stop), MTC_MARKET_OUT (D5 fallback),
 * EOD_MTC (MTC survivor rode to EOD), EOD, UNWIND (entry failed →
 * all-or-unwind), BROKER_EXIT (backstop-confirmed broker-side exit).
 */

import { useEffect, useRef, useState } from "react";
import { getICV1State, squareOffICV1 } from "../../api";
import { colors, spacing } from "../../tokens";

const ACCENT = "#6366f1";

const C = {
  bgCard:    colors.bg?.secondary  ?? "#111827",
  bgSurf:    colors.bg?.tertiary   ?? "#1f2937",
  border:    colors.border?.light  ?? "#374151",
  text:      colors.text?.primary  ?? "#f9fafb",
  textSec:   colors.text?.secondary ?? "#d1d5db",
  textMuted: colors.text?.muted    ?? "#6b7280",
  green:     colors.success        ?? "#10b981",
  red:       colors.danger         ?? "#ef4444",
  amber:     colors.warning        ?? "#f59e0b",
};

const inr = (v) =>
  v == null ? "—" : `₹${Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
const px = (v) => (v == null ? "—" : Number(v).toFixed(2));

function Badge({ children, color, bg }) {
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, letterSpacing: "0.5px",
      padding: "2px 8px", borderRadius: 10,
      color: color, background: bg ?? `${color}1f`,
      textTransform: "uppercase", whiteSpace: "nowrap",
    }}>
      {children}
    </span>
  );
}

function modeBadge(mode) {
  if (mode === "LIVE")  return <Badge color={C.green}>LIVE</Badge>;
  if (mode === "PAPER") return <Badge color={ACCENT}>PAPER</Badge>;
  return <Badge color={C.textMuted}>OFF</Badge>;
}

function groupBadge(state) {
  const map = {
    OPEN:     [C.green, "OPEN"],
    ENTERING: [C.amber, "ENTERING"],
    CLOSING:  [C.amber, "CLOSING"],
    CLOSED:   [C.textMuted, "CLOSED"],
    ABORTED:  [C.red, "ABORTED"],
  };
  const [color, label] = map[state] ?? [C.textMuted, state];
  return <Badge color={color}>{label}</Badge>;
}

function reasonColor(reason) {
  if (!reason) return C.textMuted;
  if (reason === "TP" || reason === "EOD_MTC") return C.green;
  if (reason.startsWith("MTC")) return C.amber;
  if (reason === "SL" || reason === "UNWIND") return C.red;
  return C.textSec;
}

export default function ICV1Panel() {
  const [state, setState] = useState(null);
  const [armed, setArmed] = useState(false);
  const [banner, setBanner] = useState(null);   // {kind:"ok"|"err", text}
  const armTimer = useRef(null);

  const load = async () => {
    const s = await getICV1State();
    if (s) setState(s);
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => { clearInterval(t); if (armTimer.current) clearTimeout(armTimer.current); };
  }, []);

  const onSquareOff = async () => {
    if (!armed) {
      // Two-tap confirm: arm for 4s, then auto-disarm. No window.confirm —
      // browser dialogs are silently blocked inside Tauri's webview.
      setArmed(true);
      armTimer.current = setTimeout(() => setArmed(false), 4000);
      return;
    }
    setArmed(false);
    if (armTimer.current) clearTimeout(armTimer.current);
    try {
      const res = await squareOffICV1();
      if (res?.ok) {
        setBanner({ kind: "ok", text: `Squared off ${res.closed} leg(s).` });
      } else {
        setBanner({ kind: "err", text: `Square-off failed: ${res?.detail || "unknown"}` });
      }
    } catch (e) {
      setBanner({ kind: "err", text: `Square-off failed: ${String(e)}` });
    }
    setTimeout(() => setBanner(null), 6000);
    load();
  };

  if (!state) {
    return (
      <div style={{ padding: 16, background: C.bgCard, border: `1px solid ${C.border}`,
        borderRadius: 10, color: C.textMuted, fontSize: 13 }}>
        Loading IC V1…
      </div>
    );
  }

  const g = state.group;
  const legs = g?.legs ?? [];
  const closedPnl = legs.reduce((a, l) => a + (l.pnl ?? 0), 0);
  const anyOpen = legs.some((l) => l.state === "OPEN");

  return (
    <div style={{
      background: C.bgCard, border: `1px solid ${C.border}`,
      borderTop: `3px solid ${ACCENT}`, borderRadius: 10,
      padding: spacing?.md ?? 14, display: "flex", flexDirection: "column",
      gap: spacing?.sm ?? 10,
    }}>
      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontSize: 14, fontWeight: 800, color: C.text }}>Iron Condor V1</span>
        {modeBadge(state.mode)}
        {g && groupBadge(g.state)}
        {g?.mtc_fired && <Badge color={C.amber}>MTC</Badge>}
        {g?.double_sl_minute && <Badge color={C.red}>DOUBLE SL</Badge>}
        <span style={{ marginLeft: "auto", fontSize: 10, color: C.textMuted }}>
          {state.entry_time} → {state.exit_time}
          {!state.engine_up && "  · ENGINE DOWN"}
        </span>
      </div>

      {/* banner */}
      {banner && (
        <div style={{
          fontSize: 11, padding: "6px 10px", borderRadius: 6,
          color: banner.kind === "ok" ? C.green : C.red,
          background: banner.kind === "ok" ? `${C.green}14` : `${C.red}14`,
          border: `1px solid ${banner.kind === "ok" ? C.green : C.red}44`,
        }}>
          {banner.text}
        </div>
      )}

      {/* body */}
      {!g ? (
        <div style={{ fontSize: 12, color: C.textMuted, padding: "10px 2px", lineHeight: 1.6 }}>
          No group today{state.latched_today ? " (day latch set — entry attempted/skipped)" : ""}.
          {state.mode === "OFF"
            ? " Strategy is OFF — flip mode in Settings to arm the daily entry."
            : ` Next entry at ${state.entry_time} IST: SELL CE+PE ≤ ₹85 (42% SL, Move-To-Cost) + BUY wings ≤ ₹4, square-off ${state.exit_time}.`}
        </div>
      ) : (
        <>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
              <thead>
                <tr style={{ color: C.textMuted, textAlign: "left" }}>
                  {["Leg", "Symbol", "Qty", "Entry", "SL", "State", "Exit", "Reason", "P&L"].map((h) => (
                    <th key={h} style={{ padding: "4px 8px", borderBottom: `1px solid ${C.border}`,
                      fontWeight: 600, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.4px" }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {legs.map((l) => (
                  <tr key={l.leg_id} style={{ color: C.textSec }}>
                    <td style={{ padding: "5px 8px", fontWeight: 700, color: C.text }}>
                      {l.leg_id}
                      <span style={{ marginLeft: 5, fontSize: 9, fontWeight: 600,
                        color: l.action === "SELL" ? C.red : C.green }}>
                        {l.action === "SELL" ? "S" : "B"}·{l.opt_type}
                      </span>
                      {l.wing_fallback && (
                        <span title="wing fell back to cheapest available strike"
                          style={{ marginLeft: 4, fontSize: 9, color: C.amber }}>FB</span>
                      )}
                    </td>
                    <td style={{ padding: "5px 8px", fontFamily: "monospace" }}>{l.symbol}</td>
                    <td style={{ padding: "5px 8px" }}>{l.qty}</td>
                    <td style={{ padding: "5px 8px" }}>{px(l.entry_price)}</td>
                    <td style={{ padding: "5px 8px" }}>
                      {px(l.sl)}
                      {l.mtc_repinned && (
                        <span title="SL re-pinned to cost (Move-To-Cost)"
                          style={{ marginLeft: 4, fontSize: 9, color: C.amber, fontWeight: 700 }}>@COST</span>
                      )}
                    </td>
                    <td style={{ padding: "5px 8px" }}>
                      <span style={{ color: l.state === "OPEN" ? C.green
                        : l.state === "DEAD" ? C.red : C.textMuted, fontWeight: 600 }}>
                        {l.state}
                      </span>
                    </td>
                    <td style={{ padding: "5px 8px" }}>{px(l.exit_price)}</td>
                    <td style={{ padding: "5px 8px", color: reasonColor(l.exit_reason), fontWeight: 600 }}>
                      {l.exit_reason ?? "—"}
                    </td>
                    <td style={{ padding: "5px 8px", fontWeight: 700,
                      color: l.pnl == null ? C.textMuted : l.pnl >= 0 ? C.green : C.red }}>
                      {inr(l.pnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <span style={{ fontSize: 11, color: C.textMuted }}>
              Realised (gross):{" "}
              <span style={{ fontWeight: 800, color: closedPnl >= 0 ? C.green : C.red }}>
                {inr(closedPnl)}
              </span>
            </span>
            {g.paper && <Badge color={ACCENT}>PAPER FILLS</Badge>}
            {anyOpen && (
              <button
                onClick={onSquareOff}
                style={{
                  marginLeft: "auto", fontSize: 11, fontWeight: 700,
                  padding: "6px 12px", borderRadius: 6, cursor: "pointer",
                  border: `1px solid ${armed ? C.red : C.border}`,
                  background: armed ? `${C.red}22` : C.bgSurf,
                  color: armed ? C.red : C.textSec,
                  transition: "all 0.15s ease",
                }}
              >
                {armed ? "Tap again to CONFIRM square-off" : "Square off all"}
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
