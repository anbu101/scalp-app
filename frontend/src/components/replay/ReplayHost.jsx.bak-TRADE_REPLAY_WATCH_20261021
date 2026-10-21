// frontend/src/components/replay/ReplayHost.jsx — ── TRADE_REPLAY_STREAM_20261017 ──
//
// Trade Replay on the Dashboard (Modern and Legacy), ADMIN ONLY.
// Dashboard rows do not carry the Trades / Analytics row ids, so a launcher
// asks GET /api/replay/find for one — by strategy (+ book): the strategy's
// newest open group, else its newest trade; or by symbol + entry time: that
// row — and opens the same TradeReplay modal (live while the trade is open).
// Keys are composite: "t|<Trades key>" / "a|<Analytics key>".
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
  const scope = s.slice(0, i) === "a" ? "analytics" : "trades";
  return `${getApiBase()}/api/replay/trade?scope=${scope}&key=${encodeURIComponent(s.slice(i + 1))}`;
};
const toNavKey = (d, id) => `${d?.scope === "analytics" ? "a" : "t"}|${id}`;

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
