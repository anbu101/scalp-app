// frontend/src/strategies/orb/ORBPanel.jsx
//
// ── ORB_V1 dashboard panel · v2 ── (fleet-grade rework, 2026-09-16)
// v1 was a static stub. v2 is BRKPanel-parity where ORB has the concept,
// plus the two things that make ORB legible at a glance:
//   * RANGE TRACK — live NIFTY spot on a bar between ORB low and ORB high
//     (both edges are the triggers), ticking via ltpMap between polls.
//   * POSITION TRACKS — premium entry→TP (ticking LTP + MTM) and the
//     spot-side stop distance (0.04% close-trigger), because ORB's two
//     exits live on two different instruments.
//   * Header day P&L = closed rows + open MTM, ticking. Phase chip,
//     PREFIX-GUARD / REFUSED banners, budgets, drop counters, today's rows.
// Structure from /api/orb_v1/state (4 s poll); prices at ltpMap cadence.
// Square-off is TWO-TAP (window.confirm is swallowed by Tauri).

import { useEffect, useMemo, useRef, useState } from "react";
import { getApiBase } from "../../api/base";
import { colors, spacing, pnlStyle } from "../../tokens";
import { stratName } from "../displayNames";                      // ── UI_MASK ──

const ACCENT = "#f59e0b";
const SPOT_KEY = "NIFTY50";

function normalizeSymbol(sym) { return sym ? sym.replace(/\s+/g, "").toUpperCase() : sym; }
function fmtInr(v) {
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(v);
  const s = a >= 100 ? Math.round(a).toLocaleString("en-IN") : a.toFixed(2);
  return `${v < 0 ? "−" : ""}₹${s}`;
}
function fmt2(v) { return v == null || isNaN(v) ? "—" : Number(v).toFixed(2); }
function fmtPts(v) { return v == null || isNaN(v) ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(1)}`; }
function hhmm(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
}
function ago(ts) {
  if (!ts) return null;
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m`;
}

const PHASE = {
  OFFLINE:        ["#1f2937", "#9ca3af", "OFFLINE"],
  PRE_OPEN:       ["#1c2a4a", "#93c5fd", "PRE-OPEN"],
  WINDOW:         ["#1c2a4a", "#93c5fd", "ORB WINDOW 09:15–09:30"],
  ARMED:          ["#14351f", "#4ade80", "ARMED · both edges live"],
  IN_POSITION:    ["#3b2a05", ACCENT,    "IN POSITION"],
  NO_NEW_ENTRIES: ["#2a2a2a", "#d1d5db", "NO NEW ENTRIES ≥ 12:00"],
  DONE:           ["#2a2a2a", "#d1d5db", "DAY DONE · flat since 13:00"],
  REFUSED:        ["#3a1420", "#f87171", "DAY REFUSED"],
  FROZEN:         ["#3a1420", "#f87171", "FROZEN — prefix guard"],
};

export default function ORBPanel({ strategyId = "ORB_V1", ltpMap = {} }) {
  const [st, setSt] = useState(null);
  const [armed, setArmed] = useState(false);
  const armTimer = useRef(null);

  useEffect(() => {
    let alive = true;
    const pull = async () => {
      try {
        const r = await fetch(`${getApiBase()}/api/orb_v1/state`);
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
    try { await fetch(`${getApiBase()}/api/orb_v1/square_off`, { method: "POST" }); } catch { }
  };

  const pos = st?.position;
  const lv = st?.levels;
  const day = st?.day || {};
  const cfg = st?.cfg || {};
  const trades = st?.trades || [];
  const phase = st?.phase || "OFFLINE";

  // ── live marks (ltpMap ticks between polls; route values as fallback) ──
  const liveLtp = (sym) => {
    if (!sym) return null;
    const v = ltpMap[normalizeSymbol(sym)];
    return typeof v === "number" && v > 0 ? v : null;
  };
  const spot = liveLtp(SPOT_KEY) ?? st?.spot?.ltp ?? null;
  const posLtp = liveLtp(pos?.symbol) ?? pos?.mark ?? null;
  const openMtm = pos && posLtp != null ? (posLtp - pos.entry) * pos.qty : null;
  const closedPnl = trades
    .filter((t) => t.exit_price != null)
    .reduce((a, t) => a + ((t.exit_price ?? 0) - t.entry_price) * t.qty, 0);
  const dayTotal = closedPnl + (openMtm ?? 0);

  // range track: 0 at ORB low, 1 at ORB high (spot may sit outside)
  const range = useMemo(() => {
    if (!lv || spot == null || lv.high <= lv.low) return null;
    const pad = (lv.high - lv.low) * 0.35;
    const lo = lv.low - pad, hi = lv.high + pad;
    const f = (x) => Math.min(1, Math.max(0, (x - lo) / (hi - lo)));
    return { spot: f(spot), low: f(lv.low), high: f(lv.high),
             toHigh: lv.high - spot, toLow: spot - lv.low,
             outside: spot > lv.high || spot < lv.low };
  }, [lv, spot]);

  // premium track: entry→TP (headroom below entry = one TP-distance)
  const ptrack = useMemo(() => {
    if (!pos || posLtp == null || pos.tp == null) return null;
    const hi = pos.tp, lo = pos.entry - (pos.tp - pos.entry);
    if (hi <= lo) return null;
    const f = (x) => Math.min(1, Math.max(0, (x - lo) / (hi - lo)));
    return { frac: f(posLtp), entryFrac: f(pos.entry), toTp: pos.tp - posLtp };
  }, [pos, posLtp]);

  // spot-side stop distance (close-trigger); sign = room left before breach
  const stopRoom = pos && spot != null && pos.sl_spot
    ? (pos.side === "CE" ? spot - pos.sl_spot : pos.sl_spot - spot) : null;

  const card = { background: colors.bg.secondary, borderRadius: 10, padding: spacing.md,
                 marginBottom: spacing.md, border: `1px solid ${colors.border.subtle}` };
  const label = { fontSize: 11, color: colors.text.muted, textTransform: "uppercase", letterSpacing: 0.5 };
  const num = { fontVariantNumeric: "tabular-nums" };
  const chip = (bg, fg, text) => (
    <span style={{ background: bg, color: fg, borderRadius: 6, padding: "2px 8px", fontSize: 11, fontWeight: 600, whiteSpace: "nowrap" }}>{text}</span>
  );
  const Stat = ({ k, v, sub, style }) => (
    <div style={{ minWidth: 92 }}>
      <div style={label}>{k}</div>
      <div style={{ fontSize: 16, fontWeight: 700, ...num, ...style }}>{v}</div>
      {sub && <div style={{ fontSize: 10.5, color: colors.text.muted }}>{sub}</div>}
    </div>
  );
  const [pBg, pFg, pText] = PHASE[phase] || PHASE.OFFLINE;
  const hb = ago(st?.heartbeat_ts);
  const stale = st?.running && st?.heartbeat_ts && (Date.now() / 1000 - st.heartbeat_ts) > 20;
  const sideUsed = (s) => (day.side_trades?.[s] ?? 0) >= (cfg.max_trades_per_side ?? 1);

  return (
    <div style={{ padding: spacing.md, color: colors.text.primary }}>
      {/* ── header ── */}
      <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap", marginBottom: spacing.md }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 800 }}>{stratName(strategyId)}
            <span style={{ marginLeft: 8, fontSize: 11, color: colors.text.muted, fontWeight: 500 }}>Outrider · 15m opening-range breakout</span>
          </div>
          <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 2 }}>
            long weekly NIFTY options · touch of either edge · TP +{cfg.target_value ?? 50}% · stop {cfg.sl_pct ?? 0.04}% of spot on 1m closes · flat by {cfg.eod_square_off ?? "13:00"}
          </div>
        </div>
        <span style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          {chip(pBg, pFg, pText)}
          {st?.mode === "LIVE" ? chip("#3a1420", "#f87171", "LIVE") : st?.mode === "PAPER" ? chip("#1c2a4a", "#93c5fd", "PAPER") : chip("#1f2937", "#9ca3af", st?.mode || "—")}
          {st?.running && chip(stale ? "#3a1420" : colors.bg.tertiary, stale ? "#f87171" : colors.text.muted, stale ? `ENGINE STALE ${hb}` : `♥ ${hb ?? "…"}`)}
        </span>
      </div>

      {(st?.frozen || st?.refused) && (
        <div style={{ ...card, borderColor: "#f87171", background: "#3a1420", color: "#fecaca", fontSize: 12 }}>
          <b>{st.frozen ? "PREFIX GUARD TRIPPED" : "DAY REFUSED"}</b> — {st.refused || day.frozen || "inputs unreliable; the day will not trade (fail-closed)."}
        </div>
      )}
      {!st?.running && (
        <div style={{ ...card, fontSize: 12, color: colors.text.muted }}>
          Runtime not up — mode OFF, not licensed, or the backend is still booting (40–45 s).
        </div>
      )}

      {/* ── day scoreboard ── */}
      <div style={{ ...card, display: "flex", gap: spacing.lg, flexWrap: "wrap", alignItems: "flex-start" }}>
        <Stat k="Day P&L" v={fmtInr(dayTotal)} style={pnlStyle(dayTotal)} sub={openMtm != null ? `closed ${fmtInr(closedPnl)} · open ${fmtInr(openMtm)}` : "closed rows"} />
        <Stat k="NIFTY spot" v={spot != null ? Math.round(spot).toLocaleString("en-IN") : "…"} sub={st?.last_bar ? `last 1m ${hhmm(st.last_bar.ts)} · ${st.last_bar.bars} bars` : "no bars yet"} />
        <Stat k="Trades" v={`${day.day_trades ?? 0} / ${cfg.max_trades_per_day ?? 2}`} sub={`CE ${day.side_trades?.CE ?? 0}·PE ${day.side_trades?.PE ?? 0} of ${cfg.max_trades_per_side ?? 1} each`} />
        <Stat k="Signals" v={day.signals ?? day.consumed_signals ?? 0} sub={`dropped open ${day.dropped_open ?? 0} · budget ${day.dropped_budget ?? 0} · block ${day.dropped_block ?? 0}`} />
        <Stat k="Exits" v={Object.values(day.exits || {}).reduce((a, b) => a + b, 0)} sub={Object.entries(day.exits || {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"} />
      </div>

      {/* ── range track ── */}
      <div style={card}>
        <div style={{ ...label, marginBottom: 8 }}>Opening range · first 15 min · either edge is a trigger</div>
        {!lv && <div style={{ fontSize: 12, color: colors.text.muted }}>{phase === "WINDOW" ? "Building the range from 1-minute bars…" : phase === "PRE_OPEN" ? "Levels lock at 09:30." : "No levels today."}</div>}
        {lv && (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 6, ...num }}>
              <span>PE trigger · low <b>{lv.low.toLocaleString("en-IN")}</b> {sideUsed("PE") ? chip(colors.bg.tertiary, colors.text.muted, "PE used") : chip("#14351f", "#4ade80", "PE armed")}</span>
              <span style={{ color: colors.text.muted }}>range {lv.range} pts</span>
              <span>{sideUsed("CE") ? chip(colors.bg.tertiary, colors.text.muted, "CE used") : chip("#14351f", "#4ade80", "CE armed")} high <b>{lv.high.toLocaleString("en-IN")}</b> · CE trigger</span>
            </div>
            <div style={{ position: "relative", height: 12, background: colors.bg.tertiary, borderRadius: 6 }}>
              {range && (
                <>
                  <div style={{ position: "absolute", top: 0, bottom: 0, left: `${range.low * 100}%`, width: `${(range.high - range.low) * 100}%`, background: "#1c2a4a", opacity: 0.9, borderRadius: 6 }} />
                  <div style={{ position: "absolute", top: 0, bottom: 0, left: `calc(${range.low * 100}% - 1px)`, width: 2, background: "#93c5fd" }} />
                  <div style={{ position: "absolute", top: 0, bottom: 0, left: `calc(${range.high * 100}% - 1px)`, width: 2, background: "#93c5fd" }} />
                  <div style={{ position: "absolute", top: -3, bottom: -3, left: `calc(${range.spot * 100}% - 4px)`, width: 8, borderRadius: 4, background: range.outside ? "#4ade80" : ACCENT, boxShadow: "0 0 6px rgba(0,0,0,.5)" }} />
                </>
              )}
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: colors.text.muted, marginTop: 4, ...num }}>
              <span>{range ? `${fmtPts(-range.toLow)} to low` : "—"}</span>
              <span>spot <b style={{ color: colors.text.primary }}>{spot != null ? spot.toFixed(1) : "…"}</b>{range?.outside && " · OUTSIDE the range"}</span>
              <span>{range ? `${fmtPts(range.toHigh)} to high` : "—"}</span>
            </div>
          </>
        )}
      </div>

      {/* ── open position ── */}
      <div style={card}>
        <div style={{ ...label, marginBottom: 8 }}>Open position · exits at 1-minute closes only</div>
        {!pos && <div style={{ fontSize: 12, color: colors.text.muted }}>{day.pending_side ? `Signal ${day.pending_side} pending fill…` : "Flat."}</div>}
        {pos && (
          <>
            <div style={{ fontSize: 13, display: "flex", gap: spacing.lg, flexWrap: "wrap", alignItems: "center", marginBottom: 8 }}>
              {chip(pos.side === "CE" ? "#14351f" : "#3a1420", pos.side === "CE" ? "#4ade80" : "#f87171", pos.side)}
              <b>{pos.symbol}</b>
              <span style={{ color: colors.text.muted }}>{pos.mode} · qty {pos.qty} ({pos.lots} lot{pos.lots > 1 ? "s" : ""}) · in since {hhmm(pos.entry_ts)}</span>
              <span style={{ marginLeft: "auto", ...num }}>
                LTP <b style={{ fontSize: 15 }}>{posLtp != null ? fmt2(posLtp) : "…"}</b>
                {openMtm != null && <b style={{ ...pnlStyle(openMtm), marginLeft: 10 }}>{fmtInr(openMtm)}</b>}
              </span>
              <button onClick={squareOff}
                style={{ cursor: "pointer", borderRadius: 8, padding: "6px 14px", fontWeight: 700, fontSize: 12,
                         border: `1px solid ${armed ? "#f87171" : colors.border.subtle}`,
                         background: armed ? "#3a1420" : colors.bg.tertiary, color: armed ? "#f87171" : colors.text.primary }}>
                {armed ? "TAP AGAIN TO SQUARE OFF" : "Square off"}
              </button>
            </div>
            {/* premium track: entry → TP */}
            <div style={{ position: "relative", height: 10, background: colors.bg.tertiary, borderRadius: 5, overflow: "hidden" }}>
              {ptrack && (
                <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${ptrack.frac * 100}%`, borderRadius: 5,
                              background: `linear-gradient(90deg, #f87171, ${ACCENT} ${ptrack.entryFrac * 100}%, #4ade80)`, opacity: 0.85 }} />
              )}
              {ptrack && <div style={{ position: "absolute", left: `calc(${ptrack.entryFrac * 100}% - 1px)`, top: 0, bottom: 0, width: 2, background: colors.text.primary, opacity: 0.7 }} />}
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: colors.text.muted, marginTop: 4, ...num }}>
              <span>premium</span>
              <span>entry {fmt2(pos.entry)}</span>
              <span>TP {fmt2(pos.tp)}{ptrack ? ` · ${fmt2(ptrack.toTp)} to go` : ""}</span>
            </div>
            {/* spot-side stop */}
            <div style={{ display: "flex", gap: spacing.lg, fontSize: 12, marginTop: 8, flexWrap: "wrap", ...num }}>
              <span style={{ color: colors.text.muted }}>spot stop <b style={{ color: colors.text.primary }}>{fmt2(pos.sl_spot)}</b> ({pos.side === "CE" ? "close below" : "close above"})</span>
              <span style={{ color: stopRoom != null && stopRoom < 3 ? "#f87171" : colors.text.muted }}>
                room <b>{stopRoom != null ? `${stopRoom.toFixed(1)} pts` : "…"}</b>{stopRoom != null && stopRoom < 0 && " · breached intrabar — waits for the close"}
              </span>
              {pos.mode === "LIVE" && chip("#1c2a4a", "#93c5fd", "engine exits · no GTT")}
            </div>
          </>
        )}
      </div>

      {/* ── today's rows ── */}
      <div style={card}>
        <div style={{ ...label, marginBottom: 8 }}>Today · {trades.length} row{trades.length === 1 ? "" : "s"}</div>
        {trades.length === 0 && <div style={{ fontSize: 12, color: colors.text.muted }}>No trades yet today.</div>}
        {trades.length > 0 && (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, ...num }}>
            <thead>
              <tr style={{ color: colors.text.muted, textAlign: "left" }}>
                {["Time", "Side", "Symbol", "Qty", "Entry", "Exit", "Reason", "P&L"].map((h) => (
                  <th key={h} style={{ fontWeight: 600, padding: "4px 6px", borderBottom: `1px solid ${colors.border.subtle}` }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => {
                const pnl = t.exit_price != null ? (t.exit_price - t.entry_price) * t.qty : null;
                return (
                  <tr key={t.paper_trade_id} style={{ borderBottom: `1px solid ${colors.border.subtle}` }}>
                    <td style={{ padding: "4px 6px" }}>{hhmm(t.entry_time)}{t.exit_time ? `→${hhmm(t.exit_time)}` : ""}</td>
                    <td style={{ padding: "4px 6px" }}>{chip(t.side === "CE" ? "#14351f" : "#3a1420", t.side === "CE" ? "#4ade80" : "#f87171", t.side)}</td>
                    <td style={{ padding: "4px 6px" }}><b>{t.symbol}</b> <span style={{ color: colors.text.muted }}>{t.trade_mode}</span></td>
                    <td style={{ padding: "4px 6px" }}>{t.qty}</td>
                    <td style={{ padding: "4px 6px" }}>{fmt2(t.entry_price)}</td>
                    <td style={{ padding: "4px 6px" }}>{t.exit_price != null ? fmt2(t.exit_price) : chip("#3b2a05", ACCENT, "OPEN")}</td>
                    <td style={{ padding: "4px 6px" }}>{t.exit_reason || "—"}</td>
                    <td style={{ padding: "4px 6px", fontWeight: 700, ...pnlStyle(pnl ?? 0) }}>{pnl != null ? fmtInr(pnl) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <div style={{ fontSize: 10.5, color: colors.text.muted, padding: "0 4px" }}>
        Sealed 2026-09-03 · Config {Number(cfg.target_value) === 60 ? "B (+60%)" : "A (+50%)"} · premium band ₹{cfg.premium_min ?? 150}–{cfg.premium_max ?? 200} · no new entries ≥ {cfg.entry_block_time ?? "12:00"} · budgets {cfg.max_trades_per_day ?? 2}/day, {cfg.max_trades_per_side ?? 1}/side · docs/ORB_V1_BIBLE.pdf
      </div>
    </div>
  );
}
