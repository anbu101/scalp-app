// frontend/src/strategies/emc/EMCPanel.jsx
//
// ── EMC_V1 dashboard panel ── ("Ember", EMC_V1_LIVE_20261011)
// ORBPanel's layout and conventions: structure from /api/emc_v1/state every
// 4 s, prices ticking from ltpMap between polls, two-tap square-off
// (window.confirm is swallowed by Tauri). Shows the three things that make
// EMC legible: the spot EMA state + last cross, the pending setup (armed
// contract waiting for its own EMA alignment), and the open position against
// its premium stop and target.

import { useEffect, useRef, useState } from "react";
import { getApiBase } from "../../api/base";
import { colors, spacing, pnlStyle } from "../../tokens";
import { stratName } from "../displayNames";

const ACCENT = "#e11d48";

function normalizeSymbol(sym) { return sym ? sym.replace(/\s+/g, "").toUpperCase() : sym; }
function fmtInr(v) {
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(v);
  const s = a >= 100 ? Math.round(a).toLocaleString("en-IN") : a.toFixed(2);
  return `${v < 0 ? "−" : ""}₹${s}`;
}
function fmt2(v) { return v == null || isNaN(v) ? "—" : Number(v).toFixed(2); }
function hhmm(ts) {
  if (!ts) return "—";
  const d = new Date((typeof ts === "number" ? ts : Date.parse(ts) / 1000) * 1000);
  if (isNaN(d.getTime())) return "—";
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
  BOOTING:        ["#1c2a4a", "#93c5fd", "BOOTING · loading warm-up"],
  WAITING_CROSS:  ["#14351f", "#4ade80", "WAITING FOR A SPOT CROSS"],
  SETUP_PENDING:  ["#3b2a05", "#fbbf24", "ARMED · waiting for the option's EMAs"],
  IN_POSITION:    ["#3b0a1a", "#fb7185", "IN POSITION"],
  HALTED:         ["#2a2a2a", "#d1d5db", "HALTED · daily losing-trade cap"],
  NO_NEW_ENTRIES: ["#2a2a2a", "#d1d5db", "NO NEW ENTRIES"],
  DONE:           ["#2a2a2a", "#d1d5db", "DAY DONE"],
  REFUSED:        ["#3a1420", "#f87171", "DAY REFUSED"],
  FROZEN:         ["#3a1420", "#f87171", "FROZEN — prefix guard"],
};

export default function EMCPanel({ strategyId = "EMC_V1", ltpMap = {} }) {
  const [st, setSt] = useState(null);
  const [armed, setArmed] = useState(false);
  const armTimer = useRef(null);

  useEffect(() => {
    let alive = true;
    const pull = async () => {
      try {
        const r = await fetch(`${getApiBase()}/api/emc_v1/state`);
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
    try { await fetch(`${getApiBase()}/api/emc_v1/square_off`, { method: "POST" }); } catch { }
  };

  const pos = st?.position;
  const setup = st?.setup;
  const day = st?.day || {};
  const cfg = st?.cfg || {};
  const trades = st?.trades || [];
  const phase = st?.phase || "OFFLINE";
  const liveLtp = (sym) => {
    if (!sym) return null;
    const v = ltpMap[normalizeSymbol(sym)];
    return typeof v === "number" && v > 0 ? v : null;
  };
  const posLtp = liveLtp(pos?.symbol) ?? pos?.mark ?? null;
  const openMtm = pos && posLtp != null ? (posLtp - pos.entry) * pos.qty : null;
  const closedPnl = trades.filter((t) => t.exit_price != null)
    .reduce((a, t) => a + (t.net_pnl != null ? t.net_pnl : ((t.exit_price ?? 0) - t.entry_price) * t.qty), 0);
  const dayTotal = closedPnl + (openMtm ?? 0);   // ── FLEET_TODAY_STRIP_20261015 ── net (label says so)
  const ema = st?.ema || {};
  const trend = ema.fast != null && ema.slow != null ? (ema.fast > ema.slow ? "UP" : ema.fast < ema.slow ? "DOWN" : "FLAT") : null;

  const card = { background: colors.bg.secondary, borderRadius: 10, padding: spacing.md,
                 marginBottom: spacing.md, border: `1px solid ${colors.border.subtle}` };
  const label = { fontSize: 11, color: colors.text.muted, textTransform: "uppercase", letterSpacing: 0.5 };
  const num = { fontVariantNumeric: "tabular-nums" };
  const chip = (bg, fg, text) => (
    <span style={{ background: bg, color: fg, borderRadius: 6, padding: "2px 8px", fontSize: 11, fontWeight: 600, whiteSpace: "nowrap" }}>{text}</span>
  );
  const sideChip = (s) => chip(s === "CE" ? "#14351f" : "#3a1420", s === "CE" ? "#4ade80" : "#f87171", s);
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
  const lossCap = Number(cfg.max_losses_per_day) || 0;

  // premium track: SL → TP
  const ptrack = pos && posLtp != null && pos.sl != null && pos.tp != null && pos.tp > pos.sl
    ? { f: Math.min(1, Math.max(0, (posLtp - pos.sl) / (pos.tp - pos.sl))), e: (pos.entry - pos.sl) / (pos.tp - pos.sl) } : null;

  return (
    <div style={{ padding: spacing.md, color: colors.text.primary }}>
      <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap", marginBottom: spacing.md }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 800 }}>{stratName(strategyId)}
            <span style={{ marginLeft: 8, fontSize: 11, color: colors.text.muted, fontWeight: 500 }}>Ember · EMA cross, spot arms · option fires</span>
          </div>
          <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 2 }}>
            NIFTY {cfg.spot_tf ?? 5}m EMA{cfg.ema_fast ?? 7}/{cfg.ema_slow ?? 20} cross → weekly option ₹{cfg.sel_prem_min ?? 150}–{cfg.sel_prem_max ?? 200} (highest) → bought on its own {cfg.opt_tf ?? 5}m EMA alignment · SL −{cfg.sl_pts ?? 31} / TP +{cfg.tp_pts ?? 100} pts · flat by {cfg.eod_square_off ?? "15:15"}
          </div>
        </div>
        <span style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          {chip(pBg, pFg, pText)}
          {st?.mode === "LIVE" ? chip("#3a1420", "#f87171", "LIVE") : st?.mode === "PAPER" ? chip("#1c2a4a", "#93c5fd", "PAPER") : chip("#1f2937", "#9ca3af", st?.mode || "—")}
          {st?.running && chip(stale ? "#3a1420" : colors.bg.tertiary, stale ? "#f87171" : colors.text.muted, stale ? `ENGINE STALE ${hb}` : `♥ ${hb ?? "…"}`)}
        </span>
      </div>

      {(st?.frozen || (st?.refused && phase === "REFUSED")) && (
        <div style={{ ...card, borderColor: "#f87171", background: "#3a1420", color: "#fecaca", fontSize: 12 }}>
          <b>{st.frozen ? "PREFIX GUARD TRIPPED" : "DAY REFUSED"}</b> — {st.refused || "a spot cross was restated; no new setups today (an open position is still managed)."}
        </div>
      )}
      {!st?.running && (
        <div style={{ ...card, fontSize: 12, color: colors.text.muted }}>
          Runtime not up — strategy disabled, not licensed, or the backend is still booting.
        </div>
      )}

      <div style={{ ...card, display: "flex", gap: spacing.lg, flexWrap: "wrap", alignItems: "flex-start" }}>
        <Stat k="Day P&L · net" v={fmtInr(dayTotal)} style={pnlStyle(dayTotal)} sub={openMtm != null ? `closed ${fmtInr(closedPnl)} · open ${fmtInr(openMtm)}` : "closed rows (net)"} />
        <Stat k="NIFTY 1m" v={st?.spot ? Math.round(st.spot.close).toLocaleString("en-IN") : "…"} sub={st?.spot ? `last bar ${hhmm(st.spot.ts)}` : "no bars yet"} />
        <Stat k={`EMA ${cfg.ema_fast ?? 7}/${cfg.ema_slow ?? 20}`} v={trend ?? "…"} style={{ color: trend === "UP" ? "#4ade80" : trend === "DOWN" ? "#f87171" : undefined }}
              sub={ema.fast != null ? `${fmt2(ema.fast)} / ${fmt2(ema.slow)}` : "warming"} />
        <Stat k="Last cross" v={st?.last_cross ? `${st.last_cross.dir} ${st.last_cross.at}` : "—"} sub={st?.gap_cross ? `gap cross ${st.gap_cross}` : `no arming ${cfg.arm_skip_from || "—"}–${cfg.arm_skip_to || "—"}`} />
        <Stat k="Trades" v={day.entries ?? 0} sub={`losing ${day.losses ?? 0}${lossCap ? ` of ${lossCap}` : ""}${day.halted ? " · HALTED" : ""}`} />
        <Stat k="Exits" v={Object.values(day.exits || {}).reduce((a, b) => a + b, 0)} sub={Object.entries(day.exits || {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"} />
      </div>

      <div style={card}>
        <div style={{ ...label, marginBottom: 8 }}>Setup · spot cross arms one contract, its own EMAs pull the trigger</div>
        {!setup && <div style={{ fontSize: 12, color: colors.text.muted }}>{pos ? "In a position — new setups wait for the next spot cross." : "No pending setup."}</div>}
        {setup && (
          <div style={{ fontSize: 13, display: "flex", gap: spacing.lg, flexWrap: "wrap", alignItems: "center", ...num }}>
            {sideChip(setup.side)}
            <b>{setup.symbol}</b>
            <span style={{ color: colors.text.muted }}>armed {setup.armed_at} at ₹{fmt2(setup.sel_px)}</span>
            <span>LTP <b>{fmt2(liveLtp(setup.symbol))}</b></span>
            {setup.warming && chip("#1c2a4a", "#93c5fd", "option EMA warming")}
            <span style={{ color: colors.text.muted }}>buys when its EMA{cfg.ema_fast ?? 7} is {setup.side === "CE" ? "above" : "below"} EMA{cfg.ema_slow ?? 20} · expires at {cfg.entry_cutoff ?? "14:00"} or on the opposite cross</span>
          </div>
        )}
      </div>

      <div style={card}>
        <div style={{ ...label, marginBottom: 8 }}>Open position · SL / TP checked every 2 s · engine exits (no GTT)</div>
        {!pos && <div style={{ fontSize: 12, color: colors.text.muted }}>Flat.</div>}
        {pos && (
          <>
            <div style={{ fontSize: 13, display: "flex", gap: spacing.lg, flexWrap: "wrap", alignItems: "center", marginBottom: 8 }}>
              {sideChip(pos.side)}
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
            <div style={{ position: "relative", height: 10, background: colors.bg.tertiary, borderRadius: 5, overflow: "hidden" }}>
              {ptrack && <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${ptrack.f * 100}%`, borderRadius: 5,
                                      background: `linear-gradient(90deg, #f87171, ${ACCENT} ${ptrack.e * 100}%, #4ade80)`, opacity: 0.85 }} />}
              {ptrack && <div style={{ position: "absolute", left: `calc(${ptrack.e * 100}% - 1px)`, top: 0, bottom: 0, width: 2, background: colors.text.primary, opacity: 0.7 }} />}
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: colors.text.muted, marginTop: 4, ...num }}>
              <span>SL {fmt2(pos.sl)}</span>
              <span>entry {fmt2(pos.entry)}</span>
              <span>TP {fmt2(pos.tp)}</span>
            </div>
          </>
        )}
      </div>

      <div style={card}>
        <div style={{ ...label, marginBottom: 8 }}>Today · {trades.length} row{trades.length === 1 ? "" : "s"}</div>
        {trades.length === 0 && <div style={{ fontSize: 12, color: colors.text.muted }}>No trades yet today.</div>}
        {trades.length > 0 && (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, ...num }}>
            <thead>
              <tr style={{ color: colors.text.muted, textAlign: "left" }}>
                {["Time", "Side", "Symbol", "Qty", "Entry", "Exit", "Reason", "Net"].map((h) => (
                  <th key={h} style={{ fontWeight: 600, padding: "4px 6px", borderBottom: `1px solid ${colors.border.subtle}` }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => {
                const pnl = t.exit_price != null ? (t.net_pnl != null ? t.net_pnl : (t.exit_price - t.entry_price) * t.qty) : null;
                return (
                  <tr key={t.paper_trade_id} style={{ borderBottom: `1px solid ${colors.border.subtle}` }}>
                    <td style={{ padding: "4px 6px" }}>{hhmm(t.entry_time)}{t.exit_time ? `→${hhmm(t.exit_time)}` : ""}</td>
                    <td style={{ padding: "4px 6px" }}>{sideChip(t.side)}</td>
                    <td style={{ padding: "4px 6px" }}><b>{t.symbol}</b> <span style={{ color: colors.text.muted }}>{t.trade_mode}</span></td>
                    <td style={{ padding: "4px 6px" }}>{t.qty}</td>
                    <td style={{ padding: "4px 6px" }}>{fmt2(t.entry_price)}</td>
                    <td style={{ padding: "4px 6px" }}>{t.exit_price != null ? fmt2(t.exit_price) : chip("#3b0a1a", "#fb7185", "OPEN")}</td>
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
        Sealed 2026-10-11 · cutoff {cfg.entry_cutoff ?? "14:00"} · losing-trade cap {lossCap || "off"} · no arming {cfg.arm_skip_from || "—"}–{cfg.arm_skip_to || "—"} · {st?.dte_tag && st.dte_tag !== "base" ? `DTE ${st.dte ?? "?"} (${st.dte_tag}) · ` : ""}EMC_V1_Strategy_Bible_20261011.pdf
      </div>
    </div>
  );
}
