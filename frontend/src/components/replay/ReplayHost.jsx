// frontend/src/components/replay/ReplayHost.jsx — ── TRADE_REPLAY_STREAM_20261017 ──
//
// Trade Replay on the Dashboard (Modern and Legacy), ADMIN ONLY.
// Dashboard rows do not carry the Trades / Analytics row ids, so a launcher
// asks GET /api/replay/find for one — by strategy (+ book): the strategy's
// newest open group, else its newest trade; or by symbol + entry time: that
// row — and opens the same TradeReplay modal (live while the trade is open).
// Keys are composite: "t|<Trades key>" / "a|<Analytics key>".
// ── TRADE_REPLAY_WATCH_20261021 ── "w|<SID>" is the strategy's WATCH chart
// (no open position: spot + its indicators, live in session). LiveChartCard
// puts that view — or the open position, live — inline above the focused
// strategy panel, re-checking every 30 s so an entry swaps the chart over.
//
// Fail-CLOSED: the provider hands out a launcher only after the licence read
// confirms an admin ui_level, and never on a phone (the replay is a desktop
// view); every ReplayLaunch renders nothing otherwise. The routes sit behind
// the backend admin wall as well.

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { getApiBase } from "../../api/base";
import { useEntitlements } from "../../hooks/useEntitlements";
import { useIsMobile } from "../../hooks/useIsMobile";
import { colors } from "../../tokens";
import TradeReplay from "./TradeReplay";

const Ctx = createContext(null);

const compositeUrl = (k) => {
  const s = String(k);
  const i = s.indexOf("|");
  if (s.slice(0, i) === "w") return `${getApiBase()}/api/replay/watch?strategy=${encodeURIComponent(s.slice(i + 1))}`;   // ── TRADE_REPLAY_WATCH_20261021 ──
  const scope = s.slice(0, i) === "a" ? "analytics" : "trades";
  return `${getApiBase()}/api/replay/trade?scope=${scope}&key=${encodeURIComponent(s.slice(i + 1))}`;
};
const toNavKey = (d, id) => `${d?.scope === "analytics" ? "a" : d?.scope === "watch" ? "w" : "t"}|${id}`;

export function ReplayHostProvider({ children }) {
  const { loaded, isAdminUi } = useEntitlements();
  const isMobile = useIsMobile();
  const enabled = loaded && isAdminUi && !isMobile;
  const [open, setOpen] = useState(null);          // { key, nav }
  const [msg, setMsg] = useState(null);

  useEffect(() => {
    if (!msg) return undefined;
    const t = setTimeout(() => setMsg(null), 4000);
    return () => clearTimeout(t);
  }, [msg]);

  const launch = useCallback(async (q) => {
    const p = new URLSearchParams();
    Object.entries(q || {}).forEach(([k, v]) => { if (v != null && v !== "") p.set(k, String(v)); });
    try {
      const r = await fetch(`${getApiBase()}/api/replay/find?${p}`);
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.ok) {
        setMsg(r.status === 404 ? "No trade to replay here yet." : `Replay unavailable (${d.detail || `HTTP ${r.status}`}).`);
        return;
      }
      setOpen({ key: d.key, nav: d.nav || [d.key] });
    } catch (e) {
      setMsg(`Replay unavailable (${String(e.message || e)}).`);
    }
  }, []);

  const value = useMemo(() => (enabled ? { launch } : null), [enabled, launch]);
  return (
    <Ctx.Provider value={value}>
      {children}
      {enabled && open && (
        <TradeReplay runId="live:dashboard" tradeId={open.key} navKeys={open.nav} sourceLabel="Dashboard"
          fetchUrl={compositeUrl} toNavKey={toNavKey} onClose={() => setOpen(null)} />
      )}
      {enabled && msg && (
        <div role="status" style={{ position: "fixed", right: 20, bottom: 56, zIndex: 1300, padding: "8px 14px", borderRadius: 8,
                                    background: colors.bg.elevated, border: `1px solid ${colors.border.light}`,
                                    color: colors.text.secondary, fontSize: 12.5, boxShadow: "0 8px 24px rgba(0,0,0,0.3)" }}>
          {msg}
        </div>
      )}
    </Ctx.Provider>
  );
}

export function useReplayHost() {
  return useContext(Ctx);
}

/* ▶ — same look as the Backtest / Trades replay button. `q` is the lookup
   ({ strategy, book } or { symbol, entry_ts, book, strategy }). `label`
   turns it into a text button ("▶ Replay"). */
export function ReplayLaunch({ q, label = null, title = "Replay this trade", style = null }) {
  const host = useReplayHost();
  if (!host) return null;
  return (
    <button type="button" title={title} aria-label={title}
      onClick={(e) => { e.stopPropagation(); host.launch(q); }}
      style={{ height: 22, minWidth: 24, borderRadius: 5, border: `1px solid ${colors.border.light}`, background: "transparent",
               color: colors.primary, cursor: "pointer", fontSize: label ? 11.5 : 10, lineHeight: "20px",
               padding: label ? "0 9px" : 0, flexShrink: 0, verticalAlign: "middle", fontWeight: label ? 600 : 400,
               ...(style || {}) }}>
      {label ? `▶ ${label}` : "▶"}
    </button>
  );
}


/* ── TRADE_REPLAY_WATCH_20261021 ── the focused strategy's live chart, inline.
   Open position → that position, live; none → the WATCH chart (spot + the
   strategy's indicators, forming bar and provisional values in session).
   Collapsible (remembered); "Full screen" opens the modal. Renders nothing
   for non-admin, on a phone, or outside a ReplayHostProvider. */
const CARD_OPEN_KEY = "scalp.replay.liveChartOpen";
export function LiveChartCard({ strategyId }) {
  const host = useReplayHost();
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(CARD_OPEN_KEY) !== "0"; } catch { return true; } });
  const [res, setRes] = useState(null);          // { key, nav, watch }
  const [err, setErr] = useState(null);
  useEffect(() => { setRes(null); setErr(null); }, [strategyId]);
  useEffect(() => {
    if (!host || !open || !strategyId) return undefined;
    let dead = false;
    const resolve = async () => {
      try {
        const r = await fetch(`${getApiBase()}/api/replay/find?strategy=${encodeURIComponent(strategyId)}`);
        const d = await r.json().catch(() => ({}));
        if (dead) return;
        if (!r.ok || !d.ok) { setErr(d.detail || `HTTP ${r.status}`); return; }
        setErr(null);
        setRes((prev) => (prev && prev.key === d.key && (prev.nav || []).join() === (d.nav || []).join())
          ? prev : { key: d.key, nav: d.nav || [d.key], watch: !!d.watch });
      } catch (e) { if (!dead) setErr(String(e.message || e)); }
    };
    resolve();
    const t = setInterval(resolve, 30000);
    return () => { dead = true; clearInterval(t); };
  }, [host, open, strategyId]);
  if (!host || !strategyId) return null;
  const toggle = () => setOpen((o) => { try { localStorage.setItem(CARD_OPEN_KEY, o ? "0" : "1"); } catch {} return !o; });
  return (
    <div style={{ margin: "0 0 10px" }} data-testid="live-chart-card">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: open ? 6 : 0 }}>
        <button type="button" onClick={toggle} aria-expanded={open}
          style={{ display: "inline-flex", alignItems: "center", gap: 8, font: "inherit", fontSize: 12, fontWeight: 600,
                   color: colors.text.secondary, background: "transparent", border: `1px solid ${colors.border.dark}`,
                   borderRadius: 6, padding: "5px 10px", cursor: "pointer" }}>
          <span style={{ display: "inline-block", transform: open ? "rotate(90deg)" : "none", transition: "transform .15s ease" }}>▸</span>
          Live chart{res ? (res.watch ? " · watching for an entry" : " · open position") : ""}
        </button>
        <ReplayLaunch q={{ strategy: strategyId }} label="Full screen" title="Open the live chart full screen" />
      </div>
      {open && err && <div style={{ fontSize: 12, color: colors.text.muted, padding: "6px 2px" }}>Live chart unavailable ({err}).</div>}
      {open && !err && !res && <div style={{ fontSize: 12, color: colors.text.muted, padding: "6px 2px" }}>Loading live chart…</div>}
      {open && res && (
        <TradeReplay key={`${strategyId}:${res.key}`} embedded runId="live:card" tradeId={res.key} navKeys={res.nav}
          fetchUrl={compositeUrl} toNavKey={toNavKey} />
      )}
    </div>
  );
}
