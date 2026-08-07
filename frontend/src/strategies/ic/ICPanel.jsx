/**
 * IC PANEL (shared V1/V2) — IC_SPLIT 2026-08-04
 *
 * Intended path: src/strategies/ic/ICPanel.jsx
 *
 * ONE component for BOTH IC instances, selected by the strategyId prop
 * ("IC_V1" | "IC_V2"). The V2-only chrome (CARRY banner, ·ADJ chips, SIM
 * phantom legs) renders purely off engine state — an IC_V1 group never
 * carries or adjusts, so nothing needs forking here.
 *
 * Dashboard panel for the time-entry NIFTY weekly iron condor. IC has no
 * selection/surveillance phase — before entry_time the panel shows the
 * schedule; after entry it shows the leg table (L1/L2 shorts · L3/L4 wings
 * · L1A/L2A adjustments) with live SL/MTC/carry state.
 *
 * IC_V2 DISPLAY ADDITIONS:
 *   - CARRY badge + banner: a group carried overnight (ONE_NIGHT_MAX) is
 *     unambiguous — every carried leg gets an amber CARRIED chip with its
 *     ORIGINAL entry date, and a banner states the mandatory close time
 *     (next_open_time, default 09:16). Evening after the carry commit, the
 *     banner announces the overnight hold.
 *   - ·ADJ legs: adjustment BUY legs render as "L1·ADJ"/"L2·ADJ" with a
 *     violet chip linking them to their source short.
 *   - ADJ_ONLY: phantom condor legs render dimmed with a SIM chip (they are
 *     simulated — no orders, no rows); only ·ADJ legs are booked.
 *   - Exit reason NEXT_OPEN added to the vocabulary/coloring.
 *
 * Data: GET /api/ic/{sid}/state every 5s (getICState).
 * Action: manual square-off via TWO-TAP arm/confirm + inline banner —
 * window.confirm is silently blocked in Tauri's webview (house learning).
 *
 * Exit-reason vocabulary shown verbatim from the engine: SL, TP, MTC_COST,
 * MTC_MARKET_OUT, EOD_MTC, EOD, NEXT_OPEN, UNWIND, BROKER_EXIT, MANUAL.
 */

import { useEffect, useRef, useState } from "react";
import { getICState, squareOffIC } from "../../api";
import { colors, spacing } from "../../tokens";
import { useEntitlements } from "../../hooks/useEntitlements";   // ── UI_MASK ──
import { stratName } from "../displayNames";                      // ── UI_MASK ──
import BrokerChip from "../../components/BrokerChip"; // ACC2_W3

// ── IC_SPLIT ── per-strategy accent: IC_V2 keeps the incumbent indigo
// (eyes are trained on it = the carrying condor); IC_V1 is teal.
const ACCENTS = { IC_V1: "#14b8a6", IC_V2: "#6366f1" };

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
// signed variant for P&L/MTM figures: +₹1,234 / −₹1,234
const inrS = (v) => {
  if (v == null) return "—";
  const n = Number(v);
  const s = `₹${Math.abs(n).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
  return n > 0 ? `+${s}` : n < 0 ? `−${s}` : s;
};
const px = (v) => (v == null ? "—" : Number(v).toFixed(2));
const pnlColor = (v) =>
  v == null ? C.textMuted : Number(v) >= 0 ? C.green : C.red;

// ── IC_MTM ── how far LTP has travelled from entry toward the SL, 0..1+.
// Shorts: price RISING toward sl (sl > entry). ·ADJ longs: price FALLING
// toward sl (sl < entry). null when not computable (wings without SL).
function slProgress(l) {
  if (l.state !== "OPEN" || l.ltp == null || !l.sl || !l.entry_price) return null;
  const span = l.action === "SELL" ? l.sl - l.entry_price : l.entry_price - l.sl;
  if (span <= 0) return null;
  const moved = l.action === "SELL" ? l.ltp - l.entry_price : l.entry_price - l.ltp;
  return Math.max(0, moved / span);
}
const progColor = (p) => (p >= 0.8 ? C.red : p >= 0.5 ? C.amber : C.green);

function Badge({ children, color, bg, title }) {
  return (
    <span title={title} style={{
      fontSize: 10, fontWeight: 700, letterSpacing: "0.5px",
      padding: "2px 8px", borderRadius: 10,
      color: color, background: bg ?? `${color}1f`,
      textTransform: "uppercase", whiteSpace: "nowrap",
    }}>
      {children}
    </span>
  );
}

function Chip({ children, color, title }) {
  return (
    <span title={title} style={{
      marginLeft: 5, fontSize: 9, fontWeight: 700, padding: "1px 5px",
      borderRadius: 3, color, background: `${color}1a`,
      border: `1px solid ${color}55`, whiteSpace: "nowrap",
    }}>
      {children}
    </span>
  );
}

function modeBadge(mode, accent) {
  if (mode === "LIVE")  return <Badge color={C.green}>LIVE</Badge>;
  if (mode === "PAPER") return <Badge color={accent}>PAPER</Badge>;
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
  if (reason === "NEXT_OPEN") return C.amber;
  if (reason.startsWith("MTC")) return C.amber;
  if (reason === "SL" || reason === "UNWIND") return C.red;
  return C.textSec;
}

/** "L1" → "L1" · "L1A" → "L1·ADJ" */
function legLabel(l) {
  return l.is_adjust ? `${l.adjust_of ?? l.leg_id.replace(/A$/, "")}·ADJ` : l.leg_id;
}

function todayIST() {
  // en-CA gives YYYY-MM-DD; IST fixed offset matches the backend stamps.
  return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
}

export default function ICPanel({ strategyId = "IC_V2" }) {
  const ACCENT = ACCENTS[strategyId] || ACCENTS.IC_V2;
  // ── UI_MASK ── fail-OPEN until first license read (Phase 3 convention).
  // tt(): mechanism-narrating tooltips are admin-only.
  const { loaded: licenseLoaded, isAdminUi } = useEntitlements();
  const showParams = !licenseLoaded || isAdminUi;
  const tt = (t) => (showParams ? t : undefined);
  const [state, setState] = useState(null);
  const [armed, setArmed] = useState(false);
  const [banner, setBanner] = useState(null);   // {kind:"ok"|"err", text}
  const armTimer = useRef(null);

  const load = async () => {
    const s = await getICState(strategyId);
    if (s) setState(s);
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => { clearInterval(t); if (armTimer.current) clearTimeout(armTimer.current); };
  }, []);

  const onSquareOff = async () => {
    if (!armed) {
      setArmed(true);
      armTimer.current = setTimeout(() => setArmed(false), 4000);
      return;
    }
    setArmed(false);
    if (armTimer.current) clearTimeout(armTimer.current);
    try {
      const res = await squareOffIC(strategyId);
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
  const closedPnl = g?.realized_pnl ?? legs
    .filter((l) => !l.phantom)
    .reduce((a, l) => a + (l.pnl ?? 0), 0);
  const anyOpen = legs.some((l) => l.state === "OPEN");
  // ── IC_MTM ── backend aggregates; mtm is null until every booked open
  // leg has a price (an honest partial is shown as "pricing…", not a lie)
  const unrealized = g?.unrealized_pnl ?? null;
  const mtm = g?.mtm ?? null;
  const pricing = (g?.open_legs_total ?? 0) > (g?.open_legs_priced ?? 0);
  const nextOpenT = state.next_open_time ?? "09:16";
  const isNextOpenMode = (state.exit_mode ?? "NEXT_OPEN") === "NEXT_OPEN";

  // ── carry situational awareness ──
  const openCarried  = legs.filter((l) => l.state === "OPEN" && l.carried);
  const carriedFrom  = openCarried[0]?.entry_date;
  const committedTonight = !!g?.carry_committed && openCarried.length === 0 && anyOpen;

  return (
    <div style={{
      background: C.bgCard, border: `1px solid ${C.border}`,
      borderTop: `3px solid ${ACCENT}`, borderRadius: 10,
      padding: spacing?.md ?? 14, display: "flex", flexDirection: "column",
      gap: spacing?.sm ?? 10,
    }}>
      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontSize: 14, fontWeight: 800, color: C.text }}>{showParams ? (strategyId === "IC_V2" ? "Iron Condor V2" : "Iron Condor V1") : stratName(strategyId, false)}</span>   {/* ── UI_MASK ── */}
        {modeBadge(state.mode, ACCENT)}
        <BrokerChip strategyId={strategyId} /> {/* ACC2_W3 */}
        {g && groupBadge(g.state)}
        {/* ── UI_MASK BEGIN ── mechanism badges are admin-only */}
        {showParams && g?.mtc_fired && <Badge color={C.amber}>MTC</Badge>}
        {showParams && g?.double_sl_minute && <Badge color={C.red}>DOUBLE SL</Badge>}
        {showParams && g?.adjust_only && (
          <Badge color={ACCENT} title="ADJ_ONLY: condor is simulated — only ·ADJ legs are booked">
            ADJ ONLY
          </Badge>
        )}
        {(openCarried.length > 0 || committedTonight) && (
          <Badge color={C.amber} title={tt("ONE_NIGHT_MAX overnight carry")}>CARRY</Badge>
        )}
        {/* ── UI_MASK END ── */}
        {/* ── MODE_CAPTURE ── group mode is captured at entry; a live group
            stays live-managed regardless of later Settings changes. Make
            live exposure unmissable, especially when config now says PAPER. */}
        {g && !g.paper && anyOpen && (
          <Badge color={C.red}
            title={tt("This group was entered in LIVE mode — real positions, real GTTs, real exits — regardless of the current Settings mode.")}>
            LIVE POSITIONS
          </Badge>
        )}
        <span style={{ marginLeft: "auto", fontSize: 10, color: C.textMuted }}>
          {/* ── UI_MASK ── entry/exit schedule is a parameter */}
          {showParams && <>{state.entry_time} → {isNextOpenMode ? `${nextOpenT} (+1d)` : state.exit_time}</>}
          {!state.engine_up && "  · ENGINE DOWN"}
        </span>
      </div>

      {/* action banner */}
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

      {/* ── MODE_CAPTURE ── config/group mode mismatch warning */}
      {g && !g.paper && anyOpen && state.mode !== "LIVE" && (
        <div style={{
          fontSize: 11, padding: "6px 10px", borderRadius: 6, lineHeight: 1.5,
          color: C.red, background: `${C.red}14`, border: `1px solid ${C.red}44`,
        }}>
          <strong>Mode is {state.mode}, but a LIVE group is still open.</strong>{" "}
          It will keep being managed with real orders (exits, GTTs, morning
          close) until fully flat — mode changes apply from the NEXT entry.
        </div>
      )}

      {/* carry banner — unambiguous overnight state */}
      {openCarried.length > 0 && (
        <div style={{
          fontSize: 11, padding: "6px 10px", borderRadius: 6, lineHeight: 1.5,
          color: C.amber, background: `${C.amber}14`, border: `1px solid ${C.amber}44`,
        }}>
          <strong>Overnight carry</strong> — {openCarried.length} leg(s) carried
          from <strong>{carriedFrom || "previous session"}</strong>. Mandatory
          close at <strong>{nextOpenT}</strong> today{showParams ? " (no exits before it; GTTs removed pre-market)" : ""}.   {/* ── UI_MASK ── */}
        </div>
      )}
      {committedTonight && (
        <div style={{
          fontSize: 11, padding: "6px 10px", borderRadius: 6, lineHeight: 1.5,
          color: C.amber, background: `${C.amber}14`, border: `1px solid ${C.amber}44`,
        }}>
          <strong>Carrying overnight</strong> — open legs are held past the
          close{showParams ? " (ONE_NIGHT_MAX)" : ""} and will be squared off at{" "}
          <strong>{nextOpenT}</strong> next session.{showParams ? " Broker-side SL GTTs stay armed overnight." : ""}   {/* ── UI_MASK ── */}
        </div>
      )}

      {/* ── IC_MTM ── group P&L strip: the first thing the eye should hit */}
      {g && (
        <div style={{ display: "flex", alignItems: "baseline", gap: 18, flexWrap: "wrap",
          padding: "8px 10px", borderRadius: 8, background: C.bgSurf,
          border: `1px solid ${C.border}` }}>
          <span>
            <span style={{ fontSize: 9, color: C.textMuted, textTransform: "uppercase",
              letterSpacing: "0.6px", marginRight: 8 }}>MTM</span>
            <span style={{ fontSize: 18, fontWeight: 800, fontFamily: "monospace",
              color: pnlColor(mtm) }}>
              {mtm != null ? inrS(mtm) : pricing ? "pricing…" : inrS(closedPnl)}
            </span>
          </span>
          <span style={{ fontSize: 11, color: C.textMuted }}>
            Unrealised{" "}
            <span style={{ fontWeight: 700, fontFamily: "monospace",
              color: pnlColor(unrealized) }}>
              {unrealized != null ? inrS(unrealized) : anyOpen ? "…" : inrS(0)}
            </span>
          </span>
          <span style={{ fontSize: 11, color: C.textMuted }}>
            Realised{" "}
            <span style={{ fontWeight: 700, fontFamily: "monospace",
              color: pnlColor(closedPnl) }}>{inrS(closedPnl)}</span>
          </span>
          <span style={{ marginLeft: "auto", fontSize: 10, color: C.textMuted }}>
            {legs.filter((l) => l.state === "OPEN").length} open ·{" "}
            {legs.filter((l) => l.state === "CLOSED").length} closed
            {g.adjust_only ? " · booked legs only" : ""}
          </span>
        </div>
      )}

      {/* body */}
      {!g ? (
        <div style={{ fontSize: 12, color: C.textMuted, padding: "10px 2px", lineHeight: 1.6 }}>
          No group today{state.latched_today ? " (day latch set — entry attempted/skipped)" : ""}.
          {/* ── UI_MASK ── the admin string narrates the full recipe */}
          {state.mode === "OFF"
            ? " Strategy is OFF — flip mode in Settings to arm the daily entry."
            : showParams
              ? ` Next entry at ${state.entry_time} IST: SELL CE+PE ≤ ₹85 (42% SL, Move-To-Cost` +
                `, adjustment BUY on stop exits) + BUY wings ≤ ₹4.` +
                (isNextOpenMode
                  ? ` Positions carry one night and close at ${nextOpenT} next session; expiry-day entries square off ${state.expiry_exit_time ?? "15:28"}.`
                  : ` Square-off ${state.exit_time}.`)
              : " Waiting for the next scheduled entry."}
        </div>
      ) : (
        <>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
              <thead>
                <tr style={{ color: C.textMuted, textAlign: "left" }}>
                  {["Leg", "Symbol", "Qty", "Entry", "LTP", "SL", "State", "Exit", "Reason", "P&L"].map((h) => (
                    <th key={h} style={{ padding: "4px 8px", borderBottom: `1px solid ${C.border}`,
                      fontWeight: 600, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.4px" }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {legs.map((l) => (
                  <tr key={l.leg_id} style={{
                    color: C.textSec,
                    opacity: l.phantom ? 0.55 : 1,     // ADJ_ONLY sim legs dimmed
                  }}>
                    <td style={{ padding: "5px 8px", fontWeight: 700, color: C.text, whiteSpace: "nowrap" }}>
                      {legLabel(l)}
                      <span style={{ marginLeft: 5, fontSize: 9, fontWeight: 600,
                        color: l.action === "SELL" ? C.red : C.green }}>
                        {l.action === "SELL" ? "S" : "B"}·{l.opt_type}
                      </span>
                      {showParams && l.is_adjust && (   /* ── UI_MASK ── */
                        <Chip color={ACCENT}
                          title={`Adjustment BUY armed by ${l.adjust_of}'s stop exit`}>
                          ADJ
                        </Chip>
                      )}
                      {l.carried && (
                        <Chip color={C.amber}
                          title={tt(`Carried overnight — entered ${l.entry_date}, closes at ${nextOpenT}`)}>
                          CARRIED {l.entry_date && l.entry_date !== todayIST()
                            ? l.entry_date.slice(5) : ""}
                        </Chip>
                      )}
                      {showParams && l.phantom && (   /* ── UI_MASK ── */
                        <Chip color={C.textMuted}
                          title="ADJ_ONLY: simulated leg — no orders, no rows">
                          SIM
                        </Chip>
                      )}
                      {showParams && l.wing_fallback && (   /* ── UI_MASK ── */
                        <span title="wing fell back to cheapest available strike"
                          style={{ marginLeft: 4, fontSize: 9, color: C.amber }}>FB</span>
                      )}
                    </td>
                    <td style={{ padding: "5px 8px", fontFamily: "monospace" }}>{l.symbol}</td>
                    <td style={{ padding: "5px 8px" }}>{l.qty}</td>
                    <td style={{ padding: "5px 8px" }}>{px(l.entry_price)}</td>
                    <td style={{ padding: "5px 8px", fontFamily: "monospace",
                      opacity: (l.ltp_age_s ?? 0) > 60 ? 0.5 : 1 }}
                      title={l.ltp_age_s != null ? `updated ${l.ltp_age_s}s ago` : undefined}>
                      {l.state === "OPEN" ? px(l.ltp) : "—"}
                      {l.state === "OPEN" && (l.ltp_age_s ?? 0) > 60 && (
                        <span style={{ marginLeft: 3, fontSize: 8, color: C.amber }}>stale</span>
                      )}
                    </td>
                    <td style={{ padding: "5px 8px" }}>
                      {/* ── UI_MASK ── SL level exposes the SL% parameter */}
                      {showParams ? px(l.sl) : "—"}
                      {showParams && l.mtc_repinned && (
                        <span title="SL re-pinned to cost (Move-To-Cost)"
                          style={{ marginLeft: 4, fontSize: 9, color: C.amber, fontWeight: 700 }}>@COST</span>
                      )}
                      {showParams && l.carried && l.state === "OPEN" && (l.gtt_ids?.length ?? 0) === 0 && (
                        <span title="Overnight GTTs removed pre-market — 09:16 market close is the sole exit"
                          style={{ marginLeft: 4, fontSize: 9, color: C.textMuted, fontWeight: 700 }}>NO-GTT</span>
                      )}
                      {(() => {
                        if (!showParams) return null;   /* ── UI_MASK ── */
                        const p = slProgress(l);
                        if (p == null) return null;
                        return (
                          <div title={`${Math.round(p * 100)}% of the way from entry to SL`}
                            style={{ marginTop: 3, height: 3, borderRadius: 2,
                              background: `${C.border}88`, overflow: "hidden", maxWidth: 72 }}>
                            <div style={{ height: "100%", borderRadius: 2,
                              width: `${Math.min(100, Math.round(p * 100))}%`,
                              background: progColor(p),
                              transition: "width 0.4s ease" }} />
                          </div>
                        );
                      })()}
                    </td>
                    <td style={{ padding: "5px 8px" }}>
                      <span style={{ color: l.state === "OPEN" ? C.green
                        : l.state === "DEAD" ? C.red : C.textMuted, fontWeight: 600 }}>
                        {l.state}
                      </span>
                    </td>
                    <td style={{ padding: "5px 8px" }}>{px(l.exit_price)}</td>
                    <td style={{ padding: "5px 8px", color: reasonColor(l.exit_reason), fontWeight: 600 }}>
                      {/* ── UI_MASK ── raw reason codes narrate the mechanism */}
                      {showParams ? (l.exit_reason ?? "—") : (l.exit_reason ? "CLOSED" : "—")}
                    </td>
                    <td style={{ padding: "5px 8px", fontWeight: 700, fontFamily: "monospace",
                      color: pnlColor(l.state === "OPEN" ? l.open_pnl : l.pnl) }}>
                      {l.state === "OPEN"
                        ? (l.open_pnl != null
                            ? <span title="live (unrealised)">{inrS(l.open_pnl)}</span>
                            : "—")
                        : inrS(l.pnl)}
                      {l.phantom && (l.state === "OPEN" ? l.open_pnl : l.pnl) != null ? " (sim)" : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <span style={{ fontSize: 10, color: C.textMuted }}>
              gross, before charges{g.adjust_only ? " · booked legs only" : ""}
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