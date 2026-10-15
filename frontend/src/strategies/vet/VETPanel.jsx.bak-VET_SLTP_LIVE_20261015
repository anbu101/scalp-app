// frontend/src/strategies/vet/VETPanel.jsx
//
// ── VET_V1 dashboard panel · v2 ── Fence: VET_PANEL_V2_20260921
// v1 rendered "SHORT <symbol> @ <entry>" and nothing else, and its cards read
// tokens that do not exist (colors.bgAlt, colors.border as a string) so they
// had no surface at all. v2 is ORB/TMA2-panel parity where VET has the
// concept:
//   * POSITION CARD — direction/side chips, qty, held-since (carried days),
//     ticking LTP + MTM, points, premium-captured track (SHORT) or points
//     track (LONG), wing line + combined P&L, expiry and the auto-exit time.
//   * SCOREBOARD — open MTM, today's closed net, day total, spot, entries
//     vs cap, last 5m bar (the engine heartbeat: VET decides on 5m closes).
//   * Banners — FROZEN, WARMUP SHORT, and the one v1 could not show at all:
//     an OPEN ROW IN THE DB WHILE THE LOOP IS NOT UP (unmanaged position).
//   * Closed table — today's exits plus the recent tail (positional: "today"
//     is usually empty, the last few exits are the context).
// Structure from /api/vet/state (4 s poll). The mark ticks from ltpMap ONLY
// when the route says a fresh publisher exists (mark_src === "LTP"); VET's
// own tick engine does not feed LTPStore, so otherwise the route's 1m-close
// quote is the honest number and the card says so.
// UI_MASK: regime/condition and exit reasons are admin-only (the route
// strips them server-side too; this is the curtain).
// No square-off button by design — VET's only manual path is the kill
// switch (flatten + freeze); a bare manual close would be re-entered by the
// next 5m decision.

import { useEffect, useState } from "react";
import { getApiBase } from "../../api/base";
import { colors, spacing, typography, pnlStyle, alpha } from "../../tokens";
import { useEntitlements } from "../../hooks/useEntitlements";   // ── UI_MASK ──
import { stratName } from "../displayNames";                      // ── UI_MASK ──

const ACCENT = "#34d399";
const IST = { timeZone: "Asia/Kolkata" };

function normalizeSymbol(sym) { return sym ? sym.replace(/\s+/g, "").toUpperCase() : sym; }
function fmtInr(v) {
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(Math.round(v));
  return `${v < 0 ? "−" : v > 0 ? "+" : ""}₹${a.toLocaleString("en-IN")}`;
}
function fmt2(v) { return v == null || isNaN(v) ? "—" : Number(v).toFixed(2); }
function fmtPts(v) { return v == null || isNaN(v) ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(2)}`; }
function dayKey(d) { return d.toLocaleDateString("en-CA", IST); }           // YYYY-MM-DD in IST
function hhmm(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, ...IST });
}
// Positional: a bare "10:21" is ambiguous across days — other-day stamps carry the date.
function fmtTs(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  if (dayKey(d) === dayKey(new Date())) return hhmm(ts);
  return `${d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", ...IST })} · ${hhmm(ts)}`;
}
function heldFor(ts) {
  if (!ts) return null;
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
}
// Calendar days to expiry, both ends in IST. 0 = expires today.
function daysToExpiry(iso) {
  if (!iso) return null;
  const a = Date.parse(`${dayKey(new Date())}T00:00:00Z`), b = Date.parse(`${String(iso).slice(0, 10)}T00:00:00Z`);
  return isNaN(a) || isNaN(b) ? null : Math.round((b - a) / 86400000);
}
function expiryLabel(iso) {
  if (!iso) return "—";
  const d = new Date(`${String(iso).slice(0, 10)}T00:00:00+05:30`);
  return d.toLocaleDateString("en-IN", { weekday: "short", day: "2-digit", month: "short", ...IST });
}
function minHHMM(a, b) { return String(a) <= String(b) ? a : b; }

const clamp01 = (x) => Math.max(0, Math.min(1, x));

export default function VETPanel({ strategyId = "VET_V1", ltpMap = {} }) {
  const [st, setSt] = useState(null);
  const [down, setDown] = useState(false);
  const { loaded: licenseLoaded, isAdminUi } = useEntitlements();
  const showParams = !licenseLoaded || isAdminUi;                 // fail-OPEN curtain

  useEffect(() => {
    let alive = true;
    const pull = async () => {
      try {
        const r = await fetch(`${getApiBase()}/api/vet/state`);
        if (!r.ok) throw new Error(String(r.status));
        const d = await r.json();
        if (alive) { setSt(d); setDown(false); }
      } catch { if (alive) setDown(true); /* keep last payload */ }
    };
    pull();
    const t = setInterval(pull, 4000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const pos = st?.position || null;
  const wing = pos?.wing || null;
  const cfg = st?.cfg || {};
  const eng = st?.engine;
  const sig = st?.signal;
  const closed = st?.closed || [];
  const isShort = pos?.direction === "SHORT";

  // ── marks: tick from ltpMap only when the route vouches for a fresh publisher ──
  const markOf = (leg) => {
    if (!leg) return { px: null, live: false };
    if (leg.mark_src === "LTP") {
      const v = ltpMap?.[normalizeSymbol(leg.symbol)];
      if (typeof v === "number" && v > 0) return { px: v, live: true };
    }
    return { px: leg.mark ?? null, live: false };
  };
  const mk = markOf(pos), wmk = markOf(wing);
  const pts = pos && mk.px != null ? (isShort ? pos.entry - mk.px : mk.px - pos.entry) : null;
  const mainMtm = pts != null ? pts * (pos.qty || 0) : null;
  const wingMtm = wing && wmk.px != null ? (wmk.px - wing.entry) * (wing.qty || 0) : null;
  const openMtm = mainMtm != null ? mainMtm + (wingMtm || 0) : null;
  const todayNet = st?.today_net ?? 0;
  const dayTotal = todayNet + (openMtm ?? 0);
  const pctOfPremium = pts != null && pos.entry > 0 ? (pts / pos.entry) * 100 : null;

  const spotLive = ltpMap?.NIFTY50;
  const spot = typeof spotLive === "number" && spotLive > 0 ? spotLive : st?.spot?.ltp ?? sig?.spot ?? null;

  const dte = daysToExpiry(pos?.expiry);
  const expiryExit = minHHMM(cfg.exit_time || "15:15", "15:20");
  const cap = cfg.max_trades_per_day || 0;
  const entries = st?.day?.entries ?? 0;
  const warmShort = eng && eng.warmup_ok === false;
  const unmanaged = pos && st?.position_source === "DB";
  const barAge = eng?.last_bar_ts ? Date.now() / 1000 - (eng.last_bar_ts + 300) : null;   // since that bar CLOSED
  const barStale = st?.running && barAge != null && barAge > 480 && barAge < 6 * 3600;

  const phase = !st ? ["OFFLINE", "muted"]
    : st.frozen ? ["FROZEN", "bad"]
    : unmanaged ? ["POSITION UNMANAGED", "bad"]
    : !st.running ? ["LOOP NOT UP", "muted"]
    : warmShort ? ["WARMUP SHORT", "bad"]
    : pos ? ["IN POSITION", "accent"]
    : cap > 0 && entries >= cap ? ["DAY CAP REACHED", "muted"]
    : ["FLAT · ARMED", "good"];

  // ── styles (tokens only — every surface follows the theme) ──
  const card = { background: colors.bg.secondary, borderRadius: 10, padding: spacing.lg,
                 marginBottom: spacing.md, border: `1px solid ${colors.border.light}` };
  const label = { ...typography.label, fontSize: 10.5, color: colors.text.muted };
  const num = { ...typography.mono };
  const tone = { good: colors.profit, bad: colors.loss, accent: ACCENT, muted: colors.text.muted, info: colors.primary };
  const chip = (text, t = "muted") => (
    <span style={{ background: alpha(tone[t], 16), color: tone[t], border: `1px solid ${alpha(tone[t], 35)}`,
                   borderRadius: 6, padding: "2px 8px", fontSize: 11, fontWeight: 700, whiteSpace: "nowrap", letterSpacing: 0.3 }}>{text}</span>
  );
  const Stat = ({ k, v, sub, style, big }) => (
    <div style={{ minWidth: 104 }}>
      <div style={label}>{k}</div>
      <div style={{ fontSize: big ? 22 : 16, fontWeight: 700, lineHeight: 1.25, ...num, ...style }}>{v}</div>
      {sub && <div style={{ fontSize: 10.5, color: colors.text.muted, marginTop: 1 }}>{sub}</div>}
    </div>
  );
  const banner = (title, body) => (
    <div style={{ ...card, borderColor: alpha(colors.loss, 60), background: alpha(colors.loss, 12), fontSize: 12 }}>
      <b style={{ color: colors.loss }}>{title}</b> <span style={{ color: colors.text.secondary }}>— {body}</span>
    </div>
  );

  const regimeText = sig
    ? (sig.in_range ? "IN CHANNEL" : sig.condition > 0 ? "BULL" : sig.condition < 0 ? "BEAR" : "NEUTRAL")
    : null;

  // premium track. SHORT: entry → 0, fill = premium captured; overshoot above entry fills red.
  // LONG: no natural bound — fill to ±100% of entry premium around a centre line.
  const track = (() => {
    if (!pos || mk.px == null || !(pos.entry > 0)) return null;
    const f = clamp01(Math.abs(pts) / pos.entry);
    return { frac: f, win: pts >= 0 };
  })();

  return (
    <div style={{ padding: spacing.md, color: colors.text.primary }}>
      {/* ── header ── */}
      <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap", marginBottom: spacing.md }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 800 }}>
            {stratName(strategyId, showParams)}
            <span style={{ marginLeft: 8, fontSize: 11, color: colors.text.muted, fontWeight: 500 }}>
              {showParams ? "Velvet · dual-EMA regime channel · 5m NIFTY spot" : "NIFTY 5m trend"}
            </span>
          </div>
          <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 2 }}>
            {st ? `${st.leg_action === "SELL" ? "option selling" : "option buying"} · one position at a time · ${st.positional ? "positional (carries overnight)" : `intraday, flat by ${cfg.exit_time || "15:15"}`}${st.hedged ? " · wing-hedged" : ""} · no SL/TP by design — exits on signal` : "loading…"}
          </div>
        </div>
        <span style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          {chip(phase[0], phase[1])}
          {st?.mode && chip(st.mode, st.mode === "LIVE" ? "bad" : "info")}
          {st?.running && chip(eng?.last_bar_ts ? `${barStale ? "NO 5m BAR SINCE" : "5m bar"} ${hhmm(eng.last_bar_ts)}` : "awaiting first 5m bar", barStale ? "bad" : "muted")}
          {down && chip("BACKEND UNREACHABLE", "bad")}
        </span>
      </div>

      {/* ── banners: silent failure is the enemy ── */}
      {st?.frozen && banner("ENGINE FROZEN", st.freeze_reason || "prefix guard tripped — no new entries (fail closed). An open position still exits.")}
      {!st?.frozen && warmShort && banner("WARMUP SHORT", `${eng.warmup_sessions}/${eng.warmup_required} sessions — decisions are blocked until warm.`)}
      {unmanaged && banner("OPEN POSITION IS NOT BEING MANAGED", "this row is read from the database; the VET loop is not up (waiting for the Zerodha session or warmup, mode OFF, or still booting). No exit will fire until it starts.")}
      {st && !st.running && !unmanaged && (
        <div style={{ ...card, fontSize: 12, color: colors.text.muted }}>
          Loop not up — waiting for the Zerodha session / warmup, mode OFF, not licensed, or the backend is still booting (40–45 s).
        </div>
      )}

      {/* ── scoreboard ── */}
      <div style={{ ...card, display: "flex", gap: spacing.xl, flexWrap: "wrap", alignItems: "flex-start" }}>
        <Stat big k="Open MTM" v={openMtm != null ? fmtInr(openMtm) : pos ? "…" : "—"} style={openMtm != null ? pnlStyle(openMtm) : { color: colors.text.muted }}
              sub={pos ? (mk.live ? "ticking · before charges" : mk.px != null ? "last 1m close · before charges" : "no price yet") : "flat"} />
        <Stat k="Closed today" v={fmtInr(todayNet)} style={pnlStyle(todayNet)} sub={`${st?.day?.closed_today ?? 0} position${(st?.day?.closed_today ?? 0) === 1 ? "" : "s"} · net of charges`} />
        <Stat k="Day total" v={fmtInr(dayTotal)} style={pnlStyle(dayTotal)} sub="closed net + open MTM" />
        <Stat k="NIFTY spot" v={spot != null ? Number(spot).toLocaleString("en-IN", { maximumFractionDigits: 1 }) : "…"} sub={eng ? `${eng.bars_today ?? 0} 1m bars today` : "engine not up"} />
        <Stat k="Entries today" v={cap > 0 ? `${entries} / ${cap}` : `${entries}`} sub={cap > 0 ? "daily cap" : "no daily cap"} />
        {showParams && regimeText && (
          <Stat k="Regime · last 5m" v={regimeText} style={{ color: sig.in_range ? colors.text.secondary : sig.condition > 0 ? colors.profit : sig.condition < 0 ? colors.loss : colors.text.secondary }}
                sub={`trend ${sig.dir_trend > 0 ? "up" : sig.dir_trend < 0 ? "down" : "flat"} · bar ${hhmm(sig.bar_ts)}`} />
        )}
      </div>

      {/* ── open position ── */}
      <div style={{ ...card, borderLeft: `3px solid ${pos ? ACCENT : colors.border.light}` }}>
        <div style={{ ...label, marginBottom: 10 }}>Open position · decisions at 5-minute closes only</div>
        {!pos && (
          <div style={{ fontSize: 12, color: colors.text.muted }}>
            Flat{eng?.last_bar_ts ? ` · last 5m bar ${fmtTs(eng.last_bar_ts)}` : ""} · no new entries after {cfg.entry_cutoff || "15:00"}.
          </div>
        )}
        {pos && (
          <>
            <div style={{ display: "flex", gap: spacing.md, flexWrap: "wrap", alignItems: "center", marginBottom: 12 }}>
              {chip(pos.direction, isShort ? "bad" : "good")}
              {chip(pos.side, pos.side === "CE" ? "good" : "bad")}
              <b style={{ fontSize: 15, ...num }}>{pos.symbol}</b>
              <span style={{ fontSize: 12, color: colors.text.muted }}>
                {pos.mode} · {pos.lots ?? "—"} lot{pos.lots === 1 ? "" : "s"} · qty {pos.qty}
              </span>
              {pos.carried && chip("CARRIED", "info")}
              <span style={{ marginLeft: "auto", textAlign: "right", ...num }}>
                <span style={{ fontSize: 11, color: colors.text.muted }}>LTP </span>
                <b style={{ fontSize: 20 }}>{mk.px != null ? fmt2(mk.px) : "…"}</b>
                {mainMtm != null && <b style={{ ...pnlStyle(mainMtm), fontSize: 20, marginLeft: 14 }}>{fmtInr(mainMtm)}</b>}
              </span>
            </div>

            {/* premium track */}
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, color: colors.text.muted, marginBottom: 4, ...num }}>
              <span>{isShort ? `sold @ ${fmt2(pos.entry)} — falling premium is profit` : `bought @ ${fmt2(pos.entry)} — rising premium is profit`}</span>
              <span>{isShort ? "premium → 0 (max profit)" : "+100% of premium"}</span>
            </div>
            <div style={{ position: "relative", height: 8, background: colors.bg.tertiary, borderRadius: 4, overflow: "hidden" }}>
              {track && <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${track.frac * 100}%`, borderRadius: 4,
                                      background: track.win ? colors.profit : colors.loss, transition: "width 0.5s ease" }} />}
            </div>
            <div style={{ fontSize: 10.5, color: colors.text.tertiary, marginTop: 4, ...num }}>
              {track
                ? (track.win
                    ? `${Math.abs(pctOfPremium).toFixed(0)}% of the ${isShort ? "sold" : "paid"} premium ${isShort ? "captured" : "gained"}`
                    : `${Math.abs(pctOfPremium).toFixed(0)}% of entry premium AGAINST the position — no stop by design; the exit is the signal`)
                : "price unavailable — levels only"}
            </div>

            {/* facts grid */}
            <div style={{ display: "flex", gap: spacing.xl, flexWrap: "wrap", marginTop: 14, paddingTop: 12, borderTop: `1px solid ${colors.border.dark}` }}>
              <Stat k="Entry" v={fmt2(pos.entry)} sub={fmtTs(pos.entry_ts)} />
              <Stat k="Points" v={fmtPts(pts)} style={pts != null ? pnlStyle(pts) : undefined} sub={pctOfPremium != null ? `${pctOfPremium >= 0 ? "+" : "−"}${Math.abs(pctOfPremium).toFixed(1)}% of premium` : "—"} />
              <Stat k="Held" v={heldFor(pos.entry_ts) ?? "—"} sub={pos.carried ? "carried from a prior session" : "opened today"} />
              <Stat k="Strike" v={pos.strike != null ? Number(pos.strike).toLocaleString("en-IN") : "—"}
                    sub={spot != null && pos.strike != null ? `${Math.abs(spot - pos.strike).toFixed(0)} pts ${((pos.side === "PE") === (spot > pos.strike)) ? "OTM" : "ITM"}` : pos.side} />
              <Stat k="Expiry" v={expiryLabel(pos.expiry)}
                    style={dte != null && dte <= 0 ? { color: colors.warning } : undefined}
                    sub={dte == null ? "—" : dte <= 0 ? `TODAY · auto-exit ${expiryExit}` : dte === 1 ? `tomorrow · auto-exit ${expiryExit}` : `in ${dte} days · auto-exit ${expiryExit} that day`} />
              {isShort && mk.px != null && <Stat k="Left on table" v={fmtInr(mk.px * (pos.qty || 0)).replace("+", "")} sub="if premium decays to 0" />}
            </div>

            {/* wing */}
            {wing && (
              <div style={{ display: "flex", gap: spacing.lg, flexWrap: "wrap", alignItems: "center", fontSize: 12, marginTop: 12, paddingTop: 10, borderTop: `1px solid ${colors.border.dark}`, ...num }}>
                {chip("WING · LONG", "info")}
                <b>{wing.symbol}</b>
                <span style={{ color: colors.text.muted }}>@ {fmt2(wing.entry)} · LTP {wmk.px != null ? fmt2(wmk.px) : "…"}</span>
                {wingMtm != null && <b style={pnlStyle(wingMtm)}>{fmtInr(wingMtm)}</b>}
                <span style={{ marginLeft: "auto" }}>
                  <span style={{ color: colors.text.muted }}>spread </span>
                  <b style={openMtm != null ? pnlStyle(openMtm) : undefined}>{fmtInr(openMtm)}</b>
                </span>
              </div>
            )}

            <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 12 }}>
              exits: {showParams ? "FLIP · SIGNAL EXIT" : "signal"} · {st?.positional ? `expiry day ${expiryExit}` : `EOD ${cfg.exit_time || "15:15"}`} · kill switch
              {pos.mode === "LIVE" ? " · engine exits, no GTT at the broker" : ""}
              {!mk.live && mk.px != null ? " · price is the last 1-minute close (this contract is not on the shared tick feed)" : ""}
            </div>
          </>
        )}
      </div>

      {/* ── closed positions ── */}
      <div style={card}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
          <div style={label}>Closed positions · today and recent</div>
          <div style={{ fontSize: 12, ...num }}>
            <span style={{ color: colors.text.muted }}>today </span><b style={pnlStyle(todayNet)}>{fmtInr(todayNet)}</b>
          </div>
        </div>
        {closed.length === 0 && <div style={{ fontSize: 12, color: colors.text.muted }}>No closed positions yet.</div>}
        {closed.length > 0 && (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, ...num }}>
              <thead>
                <tr style={{ color: colors.text.muted, textAlign: "left" }}>
                  {["Exit", "Dir", "Symbol", "Lots", "Entry", "Exit px", "Reason", "Charges", "Net P&L"].map((h, i) => (
                    <th key={h} style={{ fontWeight: 600, fontSize: 10.5, letterSpacing: 0.4, textTransform: "uppercase", padding: "4px 6px",
                                         textAlign: i >= 3 && h !== "Reason" ? "right" : "left", borderBottom: `1px solid ${colors.border.light}` }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {closed.map((g) => (
                  <tr key={g.group_id} style={{ borderBottom: `1px solid ${colors.border.dark}`, opacity: g.today ? 1 : 0.62 }}>
                    <td style={{ padding: "5px 6px" }}>{fmtTs(g.exit_ts)}</td>
                    <td style={{ padding: "5px 6px" }}>{chip(g.direction === "SHORT" ? "S" : "L", g.direction === "SHORT" ? "bad" : "good")}</td>
                    <td style={{ padding: "5px 6px" }}><b>{g.symbol}</b>{g.has_wing && <span style={{ color: colors.text.muted }}> + wing</span>}</td>
                    <td style={{ padding: "5px 6px", textAlign: "right" }}>{g.lots ?? "—"}</td>
                    <td style={{ padding: "5px 6px", textAlign: "right" }}>{fmt2(g.entry)}</td>
                    <td style={{ padding: "5px 6px", textAlign: "right" }}>{fmt2(g.exit)}</td>
                    <td style={{ padding: "5px 6px", color: colors.text.secondary }}>{showParams ? (g.reason || "—") : "CLOSED"}</td>
                    <td style={{ padding: "5px 6px", textAlign: "right", color: colors.text.muted }}>{g.charges ? `₹${Math.round(g.charges).toLocaleString("en-IN")}` : "—"}</td>
                    <td style={{ padding: "5px 6px", textAlign: "right", fontWeight: 700, ...pnlStyle(g.net) }}>{fmtInr(g.net)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div style={{ fontSize: 10.5, color: colors.text.muted, padding: "0 4px" }}>
        {cfg.lots ?? "—"} lot{cfg.lots === 1 ? "" : "s"} × {cfg.lot_size ?? "—"} · no new entries ≥ {cfg.entry_cutoff || "15:00"}
        {cfg.hedge_enabled ? ` · wing ≤ ₹${cfg.hedge_max_premium}` : ""} · today = exit-timestamp based (IST) · open card ignores entry day
      </div>
    </div>
  );
}
