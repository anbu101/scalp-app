/**
 * TSG_V1 PANEL — Phase 1 (LD9, locked 2026-08-02)
 *
 * Dashboard panel for the 09:16 time-entry NIFTY weekly strangle (2 shorts
 * ≤85 + 2 wings ≤5). No selection/surveillance phase — before entry_time
 * the panel shows the schedule; after entry it shows the leg table with
 * per-minute marks, day MTM vs the -35k SL line, and each short's strike
 * IV threshold (entry IV + Δ pts).
 *
 * Data: GET /api/tsg_v1/state every 5s (getTSGV1State).
 * Action: manual square-off via TWO-TAP arm/confirm + inline banner —
 * window.confirm is silently blocked in Tauri's webview (house learning).
 * Kill goes through the shared KillSwitch (POST /api/kill/TSG_V1).
 *
 * Exit-reason vocabulary (verbatim from the engine, backtest parity):
 * MTM_SL, MTM_TARGET, IV_SL, IV_SL_HEDGE, EOD + UNWIND, KILL, MANUAL.
 */

import { useEffect, useRef, useState } from "react";
import { getTSGV1State, squareOffTSGV1 } from "../../api";
import { colors, spacing } from "../../tokens";

const ACCENT = "#d946ef";

const C = {
  bgCard: colors.bg?.secondary ?? "#111827",
  bgSurf: colors.bg?.tertiary ?? "#1f2937",
  border: colors.border?.light ?? "#374151",
  text: colors.text?.primary ?? "#f9fafb",
  textSec: colors.text?.secondary ?? "#d1d5db",
  muted: colors.text?.muted ?? "#6b7280",
  green: colors.success ?? "#10b981",
  red: colors.danger ?? "#ef4444",
  amber: colors.warning ?? "#f59e0b",
};

const REASON_COLOR = {
  MTM_SL: C.red, MTM_TARGET: C.green, IV_SL: C.amber, IV_SL_HEDGE: C.amber,
  EOD: C.textSec, UNWIND: C.red, KILL: C.red, MANUAL: C.textSec,
};

const inr = (v) =>
  v == null ? "—" : `${v < 0 ? "-" : "+"}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;

export default function TSGV1Panel() {
  const [st, setSt] = useState(null);
  const [armed, setArmed] = useState(false);
  const [banner, setBanner] = useState("");
  const armTimer = useRef(null);

  useEffect(() => {
    let live = true;
    const tick = async () => {
      const s = await getTSGV1State();
      if (live) setSt(s);
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => { live = false; clearInterval(id); };
  }, []);

  const doSquareOff = async () => {
    if (!armed) {
      setArmed(true);
      setBanner("Tap again within 5s to confirm MANUAL square-off");
      clearTimeout(armTimer.current);
      armTimer.current = setTimeout(() => { setArmed(false); setBanner(""); }, 5000);
      return;
    }
    clearTimeout(armTimer.current);
    setArmed(false);
    try {
      const r = await squareOffTSGV1();
      setBanner(r?.ok ? `Squared off ${r.closed} leg(s)` : `Failed: ${r?.reason || "?"}`);
    } catch (e) {
      setBanner(`Failed: ${e?.message || e}`);
    }
    setTimeout(() => setBanner(""), 6000);
  };

  if (!st) return <div style={{ color: C.muted, padding: 16 }}>TSG_V1 loading…</div>;

  const day = st.day;
  const legs = day?.legs || [];
  const openLegs = legs.filter((l) => l.state === "OPEN").length;
  const mtm = day?.day_mtm;
  const modeColor = st.mode === "LIVE" ? C.red : st.mode === "PAPER" ? C.green : C.muted;

  return (
    <div style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 10, padding: spacing?.md ?? 14 }}>
      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <div style={{ fontWeight: 800, color: ACCENT, fontSize: 15 }}>TSG V1 · Time Strangle</div>
        <span style={{ fontSize: 11, fontWeight: 700, color: modeColor, border: `1px solid ${modeColor}`, borderRadius: 5, padding: "1px 7px" }}>{st.mode}</span>
        {day?.paper === false && <span style={{ fontSize: 10, color: C.red }}>LIVE ORDERS</span>}
        {!st.engine_up && <span style={{ fontSize: 11, color: C.red }}>engine down</span>}
        <div style={{ marginLeft: "auto", fontSize: 11, color: C.muted }}>
          entry {st.entry_time} · exit {st.exit_time} · lots {st.lots}
          {Number(st.expiry_lots) > 0 ? ` (expiry ${st.expiry_lots})` : ""}
        </div>
      </div>

      {/* day summary */}
      {!day && (
        <div style={{ color: C.muted, fontSize: 12, padding: "10px 0" }}>
          {st.latched_today ? "No position today (skipped or done)." :
            `Waiting for the ${st.entry_time} entry window.`}
        </div>
      )}
      {day?.skip_reason && (
        <div style={{ color: C.amber, fontSize: 12, marginBottom: 8 }}>Skipped: {day.skip_reason}</div>
      )}
      {day && !day.skip_reason && (
        <>
          <div style={{ display: "flex", gap: 18, marginBottom: 10, fontSize: 12 }}>
            <div>state <b style={{ color: C.text }}>{day.state}</b></div>
            <div>day MTM <b style={{ color: (mtm ?? 0) >= 0 ? C.green : C.red }}>{inr(mtm)}</b>
              <span style={{ color: C.muted }}> / SL -₹{Number(day.mtm_sl_effective ?? st.mtm_sl).toLocaleString("en-IN")}
                {Number(day.mtm_sl_effective) !== Number(st.mtm_sl) && day.mtm_sl_effective != null ? " (scaled)" : ""}</span></div>
            <div style={{ color: C.muted }}>peak {inr(day.peak_mtm)}</div>
            <div style={{ color: C.muted }}>realized {inr(day.realized)}</div>
            {day.iv_armed_used && <div style={{ color: C.amber }}>IV breaker fired (one-shot)</div>}
          </div>

          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead><tr>
              {["Leg", "Symbol", "Qty", "Entry", "Mark", "IV thr", "P&L", "State"].map((h) => (
                <th key={h} style={{ textAlign: h === "Leg" || h === "Symbol" ? "left" : "right", color: C.muted, fontSize: 10, textTransform: "uppercase", padding: "4px 6px", borderBottom: `1px solid ${C.border}` }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {legs.map((l) => (
                <tr key={l.leg_id}>
                  <td style={{ padding: "5px 6px", fontWeight: 700 }}>{l.leg_id} <span style={{ color: C.muted }}>{l.action}</span></td>
                  <td style={{ padding: "5px 6px", color: C.textSec }}>{l.symbol}</td>
                  <td style={{ padding: "5px 6px", textAlign: "right" }}>{l.qty}</td>
                  <td style={{ padding: "5px 6px", textAlign: "right" }}>{l.entry_price ?? "—"}</td>
                  <td style={{ padding: "5px 6px", textAlign: "right" }}>{l.state === "CLOSED" ? (l.exit_price ?? "—") : (l.last_mark ?? "—")}</td>
                  <td style={{ padding: "5px 6px", textAlign: "right", color: C.muted }}>
                    {l.iv_threshold != null ? `${(l.iv_threshold * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td style={{ padding: "5px 6px", textAlign: "right", color: (l.pnl ?? 0) >= 0 ? C.green : C.red }}>{inr(l.pnl)}</td>
                  <td style={{ padding: "5px 6px", textAlign: "right" }}>
                    {l.state === "CLOSED"
                      ? <span style={{ color: REASON_COLOR[l.exit_reason] || C.textSec, fontWeight: 700 }}>{l.exit_reason}</span>
                      : <span style={{ color: C.green }}>{l.state}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {openLegs > 0 && (
            <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 10 }}>
              <button onClick={doSquareOff}
                style={{ background: armed ? C.red : C.bgSurf, color: armed ? "#fff" : C.textSec, border: `1px solid ${armed ? C.red : C.border}`, borderRadius: 6, padding: "6px 14px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
                {armed ? "CONFIRM SQUARE-OFF" : "Square off (manual)"}
              </button>
              {banner && <span style={{ fontSize: 11, color: C.amber }}>{banner}</span>}
            </div>
          )}
          {openLegs === 0 && banner && <div style={{ marginTop: 8, fontSize: 11, color: C.amber }}>{banner}</div>}
        </>
      )}
    </div>
  );
}