/**
 * TSG_V1 PANEL — Phase 1 (LD9) · IC-style active layout (2026-08-03)
 *
 * Data: GET /api/tsg_v1/state every 4s. The backend refreshes leg marks +
 * live short-strike IVs every ~4s (display only — exits are still decided
 * exclusively at 1m closes, LD2), so the panel breathes like IC's.
 *
 * IV column shows LIVE reading / THRESHOLD with proximity coloring:
 * green normally, amber within 2 vol pts of the breaker, red at/over
 * (at which point the exit fires on the next 1m close if the short is
 * losing — IV9).
 *
 * Manual square-off = TWO-TAP arm/confirm (window.confirm is blocked in
 * Tauri's webview). Kill goes through the shared KillSwitch.
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

const MONO = "'SF Mono', 'Roboto Mono', Menlo, monospace";

const REASON_COLOR = {
  MTM_SL: C.red, MTM_TARGET: C.green, IV_SL: C.amber, IV_SL_HEDGE: C.amber,
  EOD: C.textSec, UNWIND: C.red, KILL: C.red, MANUAL: C.textSec,
};

const inr = (v, sign = true) =>
  v == null ? "—"
    : `${sign ? (v < 0 ? "-" : "+") : v < 0 ? "-" : ""}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;

const px2 = (v) => (v == null ? "—" : Number(v).toFixed(2));

function ivCell(l) {
  if (l.iv_threshold == null) return <span style={{ color: C.muted }}>—</span>;
  const thr = l.iv_threshold * 100;
  if (l.last_iv == null)
    return <span style={{ color: C.muted }}>— / {thr.toFixed(1)}%</span>;
  const iv = l.last_iv * 100;
  const col = iv >= thr ? C.red : iv >= thr - 2 ? C.amber : C.green;
  return (
    <span>
      <b style={{ color: col }}>{iv.toFixed(1)}%</b>
      <span style={{ color: C.muted }}> / {thr.toFixed(1)}%</span>
    </span>
  );
}

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
    const id = setInterval(tick, 4000);
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
    } catch (e) { setBanner(`Failed: ${e?.message || e}`); }
    setTimeout(() => setBanner(""), 6000);
  };

  if (!st) return <div style={{ color: C.muted, padding: 16 }}>TSG_V1 loading…</div>;

  const day = st.day;
  const legs = day?.legs || [];
  const nOpen = legs.filter((l) => l.state === "OPEN").length;
  const nClosed = legs.filter((l) => l.state === "CLOSED").length;
  const mtm = day?.day_mtm;
  const unreal = mtm != null && day?.realized != null ? mtm - day.realized : null;
  const slEff = day?.mtm_sl_effective ?? st.mtm_sl;
  const modeColor = st.mode === "LIVE" ? C.red : st.mode === "PAPER" ? C.green : C.muted;

  return (
    <div style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 10, padding: spacing?.md ?? 14 }}>
      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <div style={{ fontWeight: 800, color: ACCENT, fontSize: 15 }}>TSG V1 · Time Strangle</div>
        <span style={{ fontSize: 11, fontWeight: 700, color: modeColor, border: `1px solid ${modeColor}`, borderRadius: 5, padding: "1px 7px" }}>{st.mode}</span>
        {day && (
          <span style={{ fontSize: 11, fontWeight: 700, color: day.skip_reason ? C.amber : C.green, border: `1px solid ${day.skip_reason ? C.amber : C.border}`, borderRadius: 5, padding: "1px 7px", background: C.bgSurf }}>
            {day.skip_reason ? "NO ENTRY" : day.state}
          </span>
        )}
        {!st.engine_up && <span style={{ fontSize: 11, color: C.red }}>engine down</span>}
        <div style={{ marginLeft: "auto", fontSize: 11, color: C.muted, fontFamily: MONO }}>
          {st.entry_time} → {st.exit_time} · lots {st.lots}
          {Number(st.expiry_lots) > 0 ? ` (expiry ${st.expiry_lots})` : ""}
        </div>
      </div>

      {!day && (
        <div style={{ color: C.muted, fontSize: 12, padding: "10px 0" }}>
          {st.latched_today ? "No position today (skipped or done)."
            : `Waiting for the ${st.entry_time} entry window.`}
        </div>
      )}
      {day?.skip_reason && (
        <div style={{ color: C.amber, fontSize: 12, marginBottom: 8, background: C.bgSurf, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 12px" }}>
          Today's entry was rejected — {day.skip_reason}
        </div>
      )}

      {day && !day.skip_reason && (
        <>
          {/* MTM strip (IC-style) */}
          <div style={{ display: "flex", alignItems: "center", gap: 18, background: C.bgSurf, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 12px", marginBottom: 10 }}>
            <span style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>MTM</span>
            <b style={{ fontFamily: MONO, fontSize: 18, color: (mtm ?? 0) >= 0 ? C.green : C.red }}>{inr(mtm)}</b>
            <span style={{ fontSize: 12, color: C.textSec }}>Unrealised <b style={{ fontFamily: MONO, color: (unreal ?? 0) >= 0 ? C.green : C.red }}>{inr(unreal)}</b></span>
            <span style={{ fontSize: 12, color: C.textSec }}>Realised <b style={{ fontFamily: MONO, color: (day.realized ?? 0) >= 0 ? C.green : C.red }}>{inr(day.realized)}</b></span>
            <span style={{ fontSize: 12, color: C.muted }}>SL <span style={{ fontFamily: MONO }}>-₹{Number(slEff).toLocaleString("en-IN")}</span>
              {Number(slEff) !== Number(st.mtm_sl) ? " (scaled)" : ""}</span>
            <span style={{ fontSize: 12, color: C.muted }}>peak <span style={{ fontFamily: MONO }}>{inr(day.peak_mtm)}</span></span>
            {day.iv_armed_used && <span style={{ fontSize: 11, color: C.amber }}>IV breaker fired</span>}
            <span style={{ marginLeft: "auto", fontSize: 12, color: C.muted }}>{nOpen} open · {nClosed} closed</span>
          </div>

          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead><tr>
              {["LEG", "SYMBOL", "QTY", "ENTRY", "LTP", "IV / THR", "P&L", "STATE"].map((h, i) => (
                <th key={h} style={{ textAlign: i < 2 ? "left" : "right", color: C.muted, fontSize: 10, letterSpacing: 0.5, padding: "4px 6px", borderBottom: `1px solid ${C.border}` }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {legs.map((l) => {
                const badge = `${l.action === "SELL" ? "S" : "B"}·${l.opt_type}`;
                const badgeCol = l.action === "SELL" ? C.red : C.green;
                const closed = l.state === "CLOSED";
                return (
                  <tr key={l.leg_id} style={{ opacity: l.state === "DEAD" ? 0.4 : 1 }}>
                    <td style={{ padding: "6px 6px", fontWeight: 700 }}>
                      {l.leg_id} <span style={{ color: badgeCol, fontSize: 10, fontWeight: 800 }}>{badge}</span>
                    </td>
                    <td style={{ padding: "6px 6px", color: C.textSec, fontFamily: MONO }}>{l.symbol}</td>
                    <td style={{ padding: "6px 6px", textAlign: "right", fontFamily: MONO }}>{l.qty}</td>
                    <td style={{ padding: "6px 6px", textAlign: "right", fontFamily: MONO }}>{px2(l.entry_price)}</td>
                    <td style={{ padding: "6px 6px", textAlign: "right", fontFamily: MONO }}>
                      {closed
                        ? <span style={{ color: C.muted }}>{px2(l.exit_price)}</span>
                        : px2(l.last_mark)}
                    </td>
                    <td style={{ padding: "6px 6px", textAlign: "right", fontFamily: MONO }}>{closed ? <span style={{ color: C.muted }}>—</span> : ivCell(l)}</td>
                    <td style={{ padding: "6px 6px", textAlign: "right", fontFamily: MONO, color: (l.pnl ?? 0) >= 0 ? C.green : C.red }}>{inr(l.pnl)}</td>
                    <td style={{ padding: "6px 6px", textAlign: "right" }}>
                      {closed
                        ? <span style={{ color: REASON_COLOR[l.exit_reason] || C.textSec, fontWeight: 700, fontSize: 11 }}>{l.exit_reason}</span>
                        : <span style={{ color: l.state === "OPEN" ? C.green : C.amber, fontWeight: 700, fontSize: 11 }}>{l.state}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {/* footer (IC-style) */}
          <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 11, color: C.muted }}>gross, before charges</span>
            {day.paper && (
              <span style={{ fontSize: 10, fontWeight: 700, color: ACCENT, border: `1px solid ${ACCENT}`, borderRadius: 5, padding: "1px 7px" }}>PAPER FILLS</span>
            )}
            {banner && <span style={{ fontSize: 11, color: C.amber }}>{banner}</span>}
            {nOpen > 0 && (
              <button onClick={doSquareOff}
                style={{ marginLeft: "auto", background: armed ? C.red : C.bgSurf, color: armed ? "#fff" : C.textSec, border: `1px solid ${armed ? C.red : C.border}`, borderRadius: 6, padding: "6px 14px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
                {armed ? "CONFIRM SQUARE-OFF" : "Square off all"}
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}