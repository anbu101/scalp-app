/**
 * GC_V1 PANEL — Glacier (2026-08-15, LD-sheet).
 *
 * Data: GET /api/gc_v1/state every 4s. Exits are decided exclusively at 1m
 * closes by the replay-diff core (the panel breathes for display only).
 * Manual square-off = TWO-TAP arm/confirm (window.confirm is blocked in
 * Tauri's webview). Kill goes through the shared KillSwitch.
 * ── UI_MASK ── non-admins see position facts, not strategy parameters.
 */

import { useEffect, useRef, useState } from "react";
import { getGCV1State, squareOffGCV1 } from "../../api";
import { colors, spacing } from "../../tokens";
import { useEntitlements } from "../../hooks/useEntitlements";   // ── UI_MASK ──
import { stratName } from "../displayNames";                      // ── UI_MASK ──
import BrokerChip from "../../components/BrokerChip";

const ACCENT = "#38bdf8";

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
  SL: C.red, EOD: C.textSec, MAX_LOSS_TRADE: C.red, MAX_PROFIT_TRADE: C.green,
  MAX_LOSS_DAY: C.red, MAX_PROFIT_DAY: C.green, KILL: C.red,
  MANUAL: C.textSec, UNWIND: C.red, HISTORY_DIVERGED: C.amber,
};

const inr = (v) =>
  v == null ? "—"
    : `${v < 0 ? "-" : "+"}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;

export default function GCV1Panel() {
  const { loaded: licenseLoaded, isAdminUi } = useEntitlements();
  const showParams = !licenseLoaded || isAdminUi;   // fail-OPEN convention
  const [st, setSt] = useState(null);
  const [armed, setArmed] = useState(false);
  const [banner, setBanner] = useState("");
  const armTimer = useRef(null);

  useEffect(() => {
    let live = true;
    const tick = async () => {
      const s = await getGCV1State();
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
      const r = await squareOffGCV1();
      setBanner(r?.ok ? `Squared off ${r.flattened_legs} leg(s)` : `Failed: ${r?.error || "?"}`);
    } catch (e) { setBanner(`Failed: ${e?.message || e}`); }
    setTimeout(() => setBanner(""), 6000);
  };

  if (!st) return <div style={{ color: C.muted, padding: 16 }}>{stratName("GC_V1")} loading…</div>;

  const day = st.day;
  const legs = day?.open_legs || [];
  const modeColor = st.mode === "LIVE" ? C.red : st.mode === "PAPER" ? C.green : C.muted;
  const dayNet = day ? (day.day_realized ?? 0) + (day.open_mtm ?? 0) : null;

  return (
    <div style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 10, padding: spacing?.md ?? 14 }}>
      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
        <b style={{ color: ACCENT }}>{stratName("GC_V1")}</b>
        <BrokerChip broker="ZERODHA" />
        <span style={{ color: modeColor, fontWeight: 700, fontSize: 12 }}>{st.mode}</span>
        <span style={{ color: st.engine_up ? C.green : C.red, fontSize: 12 }}>
          {st.engine_up ? "engine up" : "engine down"}
        </span>
        {showParams && (
          <span style={{ color: C.muted, fontSize: 11 }}>
            {st.gc_mode} · prem&lt;{st.premium_max}
            {st.gc_mode === "SELL" && Number(st.hedge_premium_max) > 0 ? ` · hdg≤${st.hedge_premium_max}` : ""}
            {" "}· {st.lots}L · cap {st.max_trades_per_day}/day · cutoff {st.entry_cutoff_time} · EOD {st.exit_time}
          </span>
        )}
        <div style={{ flex: 1 }} />
        <button
          onClick={doSquareOff}
          style={{ background: armed ? C.red : C.bgSurf, color: armed ? "#fff" : C.textSec,
                   border: `1px solid ${armed ? C.red : C.border}`, borderRadius: 6,
                   padding: "4px 12px", fontSize: 12, cursor: "pointer" }}>
          {armed ? "CONFIRM square-off" : "Square off"}
        </button>
      </div>
      {banner && <div style={{ color: C.amber, fontSize: 12, marginBottom: 8 }}>{banner}</div>}

      {!day && <div style={{ color: C.muted, fontSize: 12 }}>No day state yet (runtime arms 08:30 IST).</div>}
      {day && (
        <>
          <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 12, marginBottom: 8 }}>
            <span style={{ color: C.muted }}>day <b style={{ color: C.textSec }}>{day.day_date || "—"}</b></span>
            <span style={{ color: C.muted }}>entries <b style={{ color: C.textSec }}>{day.entries}</b> · exits <b style={{ color: C.textSec }}>{day.exits}</b></span>
            <span style={{ color: C.muted }}>open MTM <b style={{ color: (day.open_mtm ?? 0) >= 0 ? C.green : C.red, fontFamily: MONO }}>{inr(day.open_mtm)}</b></span>
            <span style={{ color: C.muted }}>day gross <b style={{ color: (dayNet ?? 0) >= 0 ? C.green : C.red, fontFamily: MONO }}>{inr(dayNet)}</b></span>
            {day.halted && <span style={{ color: C.amber, fontWeight: 700 }}>HALTED · {day.halt_reason}</span>}
            {day.skip_reason && <span style={{ color: C.muted }}>day skipped: {day.skip_reason}</span>}
          </div>

          {legs.length > 0 && (
            <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse", marginBottom: 8 }}>
              <thead>
                <tr style={{ color: C.muted, textAlign: "left" }}>
                  {["Leg", "Symbol", "Entry", "Mark", "Qty", "MTM"].map((h) => (
                    <th key={h} style={{ padding: "3px 8px", borderBottom: `1px solid ${C.border}` }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {legs.map((l) => {
                  const mtm = l.last_mark != null
                    ? ((l.action === "BUY" ? l.last_mark - l.entry_price
                        : l.entry_price - l.last_mark) * l.qty) : null;
                  return (
                    <tr key={l.symbol}>
                      <td style={{ padding: "3px 8px", color: C.textSec }}>{l.action} {l.role === "HEDGE" ? "· wing" : ""} <span style={{ color: C.muted }}>{l.tag}</span></td>
                      <td style={{ padding: "3px 8px", fontFamily: MONO }}>{l.symbol}</td>
                      <td style={{ padding: "3px 8px", fontFamily: MONO }}>{Number(l.entry_price).toFixed(2)}</td>
                      <td style={{ padding: "3px 8px", fontFamily: MONO }}>{l.last_mark != null ? Number(l.last_mark).toFixed(2) : "—"}</td>
                      <td style={{ padding: "3px 8px", fontFamily: MONO }}>{l.qty}</td>
                      <td style={{ padding: "3px 8px", fontFamily: MONO, color: (mtm ?? 0) >= 0 ? C.green : C.red }}>{inr(mtm)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}

          {(day.trades || []).length > 0 && (
            <div style={{ fontSize: 11, color: C.muted }}>
              {(day.trades || []).map((t, i) => (
                <div key={i}>
                  <span style={{ color: C.textSec }}>{t.tag}</span>
                  {" → "}
                  <span style={{ color: REASON_COLOR[t.reason] || C.textSec }}>{t.reason}</span>
                  {" "}
                  <span style={{ fontFamily: MONO, color: (t.pnl ?? 0) >= 0 ? C.green : C.red }}>{inr(t.pnl)}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}