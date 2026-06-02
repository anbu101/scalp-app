/**
 * NotificationProvider — src/context/NotificationProvider.jsx
 *
 * Global, app-wide trade notifications (audio + toast) for ALL strategies in
 * BOTH paper and live modes. Replaces the per-strategy audio/toast that used
 * to live inside ScalpPanel.
 *
 * How it works:
 *   - Polls GET /api/app/events?after=<lastId> every 3s.
 *   - For each NEW event, fires audio and/or toast, gated by App Settings
 *     (notify_audio / notify_toast), fetched from GET /api/app/settings.
 *   - First poll sends after=-1 (no backlog server-side), so old events don't
 *     replay on page load. The server returns the current latest_id, which
 *     seeds the cursor. A cursor of 0 is now a REAL cursor (empty buffer at
 *     launch), not a "first poll" sentinel — this is what was stalling the
 *     feed and silently dropping every event.
 *
 * Mount once, inside ToastProvider (it uses useToast):
 *
 *   <ToastProvider>
 *     <NotificationProvider>
 *       ... app ...
 *     </NotificationProvider>
 *   </ToastProvider>
 *
 * App Settings are exposed via useAppSettings() so the Settings page can read
 * and update the same flags this provider respects.
 */

import { createContext, useContext, useEffect, useRef, useState, useCallback } from "react";
import { useToast } from "../components/ToastNotifications";
import { getApiBase } from "../api/base";

const POLL_MS = 3000;
const SETTINGS_POLL_MS = 30000;

/* ── App Settings context (shared with Settings page) ─────────── */
const AppSettingsContext = createContext(null);

export function useAppSettings() {
  const ctx = useContext(AppSettingsContext);
  if (!ctx) {
    return {
      settings: {
        notify_audio: true,
        notify_toast: true,
        show_account_balance: true,
        audio_rules: {},
      },
      loading: true,
      saveSettings: async () => {},
      refresh: async () => {},
    };
  }
  return ctx;
}

/* ── Audio engine (tones lifted verbatim from ScalpPanel) ─────── */
const AudioAlerts = {
  context: null,
  init() {
    if (!this.context && typeof window !== "undefined") {
      this.context = new (window.AudioContext || window.webkitAudioContext)();
    }
  },
  playTone(frequency, duration, type = "sine") {
    this.init();
    if (!this.context) return;
    // Browsers suspend AudioContext until a user gesture; resume best-effort.
    if (this.context.state === "suspended") {
      this.context.resume().catch(() => {});
    }
    const osc = this.context.createOscillator();
    const gain = this.context.createGain();
    osc.connect(gain); gain.connect(this.context.destination);
    osc.frequency.value = frequency; osc.type = type;
    gain.gain.setValueAtTime(0.3, this.context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, this.context.currentTime + duration);
    osc.start(this.context.currentTime);
    osc.stop(this.context.currentTime + duration);
  },
  positionEntered() { this.playTone(800, 0.15); setTimeout(() => this.playTone(1000, 0.15), 150); },
  stopLossHit()     { this.playTone(400, 0.2); setTimeout(() => this.playTone(350, 0.2), 200); setTimeout(() => this.playTone(300, 0.3), 400); },
  takeProfitHit()   { this.playTone(600, 0.1); setTimeout(() => this.playTone(800, 0.1), 100); setTimeout(() => this.playTone(1000, 0.15), 200); },
  positionClosed()  { this.playTone(520, 0.12); setTimeout(() => this.playTone(440, 0.16), 120); },
};

/* ── Helpers ──────────────────────────────────────────────────── */
function fmtPnL(v) {
  if (v == null || isNaN(v)) return "";
  const r = Math.round(v);
  return ` ${r >= 0 ? "+" : "−"}₹${Math.abs(r).toLocaleString("en-IN")}`;
}

const STRATEGY_LABEL = {
  SCALP_V1: "Scalp", SCALP_V2: "Scalp V2",
  BB_V1: "BB", BB_V2: "BB V2", HA_V1: "Heikin Ashi",
};
function stratLabel(id) { return STRATEGY_LABEL[id] || id || "Strategy"; }
function modeTag(mode) { return (mode || "live").toLowerCase() === "live" ? "LIVE" : "PAPER"; }

/* ── Provider ─────────────────────────────────────────────────── */
export function NotificationProvider({ children }) {
  const toast = useToast();

  const [settings, setSettings] = useState({
    notify_audio: true,
    notify_toast: true,
    show_account_balance: true,
    audio_rules: {},
  });
  const [loading, setLoading] = useState(true);

  // Keep latest settings in a ref so the polling loop always sees current
  // values without re-subscribing.
  const settingsRef = useRef(settings);
  useEffect(() => { settingsRef.current = settings; }, [settings]);

  // -1 = "first poll" sentinel. The server returns the current latest_id with
  // no backlog, seeding this ref. After that, 0 (or any value the server
  // returns) is a real cursor. Starting at 0 was the bug: the server treated
  // it as a fresh first poll on every tick and never delivered events.
  const lastIdRef = useRef(-1);

  /* ── Load + persist app settings ── */
  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/app/settings`);
      if (res.ok) {
        const data = await res.json();
        if (data && typeof data === "object") {
          // Spread server data first so ANY field the backend returns is
          // preserved (prevents the "frontend silently drops a setting" bug),
          // then normalise the known booleans with ON-by-default semantics.
          setSettings({
            ...data,
            notify_audio: data.notify_audio !== false,
            notify_toast: data.notify_toast !== false,
            show_account_balance: data.show_account_balance !== false,
            audio_rules: (data.audio_rules && typeof data.audio_rules === "object") ? data.audio_rules : {},
          });
        }
      }
    } catch { /* keep current */ }
    finally { setLoading(false); }
  }, []);

  const saveSettings = useCallback(async (next) => {
    // Optimistic update
    setSettings(next);
    try {
      await fetch(`${getApiBase()}/api/app/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(next),
      });
    } catch { /* best-effort; UI already reflects intent */ }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, SETTINGS_POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  /* ── Fire a single event ── */
  const fireEvent = useCallback((evt) => {
    const s = settingsRef.current;
    const type = (evt.event_type || "").toUpperCase();
    const label = stratLabel(evt.strategy_id);
    const sym = evt.symbol || "";
    const tag = modeTag(evt.mode);

    // Audio — master switch AND per-strategy/per-mode rule must both allow it.
    // Fail-open: if a strategy/mode is missing from audio_rules, default to ON.
    const modeKey = (evt.mode || "live").toLowerCase() === "live" ? "LIVE" : "PAPER";
    const rule = s.audio_rules?.[evt.strategy_id];
    const perRuleAllows = rule ? rule[modeKey] !== false : true;
    if (s.notify_audio && perRuleAllows) {
      if (type === "ENTER") AudioAlerts.positionEntered();
      else if (type === "TP") AudioAlerts.takeProfitHit();
      else if (type === "SL") AudioAlerts.stopLossHit();
      else {
        // EXIT (generic close — e.g. BB SuperTrend/EOD, which don't know
        // whether the broker GTT hit TP or SL). We don't claim TP/SL here;
        // we just make profit sound like a win and a loss sound like a loss,
        // mirroring the toast's P&L-sign branch below. When P&L is unknown,
        // fall back to the neutral closed tone.
        if (evt.pnl == null || isNaN(evt.pnl)) AudioAlerts.positionClosed();
        else if (evt.pnl >= 0) AudioAlerts.takeProfitHit();
        else AudioAlerts.stopLossHit();
      }
    }

    // Toast
    if (s.notify_toast) {
      if (type === "ENTER") {
        toast.info(`${label} · Entry`, `${sym}${evt.entry_price != null ? ` @ ₹${evt.entry_price}` : ""} · ${tag}`, { duration: 4000 });
      } else if (type === "TP") {
        toast.success(`${label} · Target Hit`, `${sym}${fmtPnL(evt.pnl)} · ${tag}`, { duration: 6000, icon: "🎯" });
      } else if (type === "SL") {
        toast.error(`${label} · Stop Loss`, `${sym}${fmtPnL(evt.pnl)} · ${tag}`, { duration: 6000 });
      } else {
        const win = (evt.pnl ?? 0) >= 0;
        const fn = win ? toast.success : toast.warning;
        fn(`${label} · Closed`, `${sym}${fmtPnL(evt.pnl)} · ${tag}`, { duration: 5000 });
      }
    }
  }, [toast]);

  /* ── Poll the event feed ── */
  useEffect(() => {
    let alive = true;
    async function poll() {
      while (alive) {
        try {
          const res = await fetch(`${getApiBase()}/api/app/events?after=${lastIdRef.current}`);
          if (res.ok) {
            const data = await res.json();
            if (data && typeof data === "object") {
              const events = Array.isArray(data.events) ? data.events : [];
              for (const evt of events) fireEvent(evt);
              if (typeof data.latest_id === "number") lastIdRef.current = data.latest_id;
            }
          }
        } catch { /* network hiccup — try again next tick */ }
        await new Promise((r) => setTimeout(r, POLL_MS));
      }
    }
    poll();
    return () => { alive = false; };
  }, [fireEvent]);

  const value = { settings, loading, saveSettings, refresh };

  return (
    <AppSettingsContext.Provider value={value}>
      {children}
    </AppSettingsContext.Provider>
  );
}