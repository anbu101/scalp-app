/**
 * NotificationProvider — src/context/NotificationProvider.jsx
 *
 * Global, app-wide notifications (audio + toast) for ALL strategies in BOTH
 * paper and live modes.
 *
 * TWO classes of event ride the same /api/app/events feed:
 *
 *   1. TRADE events (ENTER / TP / SL / EXIT) — fire audio + a transient toast.
 *      NOT added to the notification center (high-frequency; own surfaces).
 *
 *   2. ALERT events (event_type === "ALERT") — operational "needs attention"
 *      cases (dead/rejected entries, partial fills, GTT failures, timeouts,
 *      and system-state alerts like relay down/up, max-loss, EOD). These fire
 *      a severity-aware tone, a toast (sticky for "error"), AND append to the
 *      persistent in-session notification list shown by <NotificationCenter/>.
 *
 * FRONTEND-DETECTED SYSTEM ALERT (broker disconnect):
 *   Broker connectivity is detected HERE (not the backend) by watching the
 *   `health` prop that App.jsx already polls every 5s. This means it fires even
 *   if the backend itself is unreachable. It is EDGE-TRIGGERED: one alert when
 *   connected flips true→false, one recovery alert when it flips back — never a
 *   repeat every poll. A synthetic ALERT is injected into the same list/toast/
 *   audio path so it looks identical to backend alerts.
 *
 * AUDIO (autoplay-policy + webview-suspend fix — PER-TONE SHORT-LIVED CONTEXT):
 *   Long-lived AudioContexts in a desktop webview are unreliable: WKWebView
 *   (macOS) and WebView2 (Windows) both SUSPEND the context after a period of
 *   audio inactivity or on window blur, and resume() is async — a tone created
 *   synchronously on a still-suspended context is silently dropped. Worse, a
 *   continuous keep-alive oscillator stream can drive some webview builds into
 *   a "running but wedged" state where state==="running" yet no audio is
 *   produced (this is the failure we hit: toasts fine, audio fully silent).
 *
 *   FIX (Option 1 — fresh context per tone):
 *     Each tone is played on its OWN short-lived AudioContext that is created,
 *     resumed, used, and then closed. A context that lives for ~half a second
 *     and is then torn down can neither idle-suspend nor wedge — there is no
 *     long-lived graph to go stale. No keep-alive heartbeat is needed or used.
 *
 *     Autoplay policy still requires a prior user gesture before a context may
 *     produce sound, so we keep a one-time first-gesture "unlock" that proves
 *     audio is permitted this session (installUnlock). The per-tone contexts
 *     created after that gesture start in "running" on both webviews.
 *
 *   This is plain Web Audio API — identical behaviour on macOS and Windows,
 *   no per-OS branches.
 *
 * Mount once, inside ToastProvider, and pass health:
 *   <ToastProvider>
 *     <NotificationProvider health={health}>
 *       ... app ...
 *     </NotificationProvider>
 *   </ToastProvider>
 *
 * Consumers:
 *   useAppSettings()    → settings + saveSettings (Settings page)
 *   useNotifications()  → { items, unreadCount, markAllRead, markRead, clearAll }
 */

import { createContext, useContext, useEffect, useRef, useState, useCallback } from "react";
import { useToast } from "../components/ToastNotifications";
import { getApiBase } from "../api/base";

const POLL_MS = 3000;
const SETTINGS_POLL_MS = 30000;
const MAX_NOTIFICATIONS = 100;

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

/* ── Notification center context (bell + list) ────────────────── */
const NotificationsContext = createContext(null);

export function useNotifications() {
  const ctx = useContext(NotificationsContext);
  if (!ctx) {
    return {
      items: [],
      unreadCount: 0,
      markAllRead: () => {},
      markRead: () => {},
      clearAll: () => {},
    };
  }
  return ctx;
}

/* ── Audio engine — PER-TONE SHORT-LIVED CONTEXT ──────────────────
   See the header block for the full rationale. Invariants:
     - A one-time first-gesture unlock proves audio is permitted (autoplay).
     - Every tone gets a FRESH AudioContext that is created, played, and closed
       — nothing long-lived to idle-suspend or wedge.
     - No keep-alive heartbeat (it was the cause of the "running but silent"
       wedge). focus handler is kept only to flag re-permission cheaply. */
const AudioAlerts = {
  unlocked: false,
  _unlockInstalled: false,

  // First-gesture unlock: create a throwaway context from a real user gesture,
  // resume + prime it, then close it. This satisfies the autoplay policy for
  // the session so later per-tone contexts start "running". Idempotent.
  unlock() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const c = new Ctx();
      const finish = () => {
        try {
          const buf = c.createBuffer(1, 1, 22050);
          const src = c.createBufferSource();
          src.buffer = buf;
          src.connect(c.destination);
          src.start(0);
        } catch { /* priming best-effort */ }
        this.unlocked = true;
        // Close shortly after; we only needed the gesture-blessed resume.
        setTimeout(() => { try { c.close(); } catch {} }, 300);
      };
      if (c.state === "suspended") {
        c.resume().then(finish).catch(() => { try { c.close(); } catch {} });
      } else {
        finish();
      }
    } catch { /* never throw from audio */ }
  },

  // Register one-shot first-gesture listeners. They self-detach once unlocked.
  installUnlock() {
    if (this._unlockInstalled || typeof window === "undefined") return;
    this._unlockInstalled = true;

    const gestureHandler = () => {
      this.unlock();
      if (this.unlocked) {
        for (const evt of ["pointerdown", "keydown", "touchstart", "click"]) {
          window.removeEventListener(evt, gestureHandler, true);
        }
      }
    };
    for (const evt of ["pointerdown", "keydown", "touchstart", "click"]) {
      window.addEventListener(evt, gestureHandler, true);
    }
  },

  // Play a sequence of tones, each as [frequency, duration, type?, delayMs?].
  // ALL tones in one call share ONE fresh context that is closed after the
  // last tone finishes — so a multi-beep alert is still one short-lived ctx.
  _playSequence(tones) {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const c = new Ctx();

      const schedule = () => {
        let maxEnd = 0;
        for (const [freq, dur, type = "sine", delayMs = 0] of tones) {
          const start = c.currentTime + delayMs / 1000;
          try {
            const osc = c.createOscillator();
            const gain = c.createGain();
            osc.connect(gain); gain.connect(c.destination);
            osc.frequency.value = freq; osc.type = type;
            gain.gain.setValueAtTime(0.3, start);
            gain.gain.exponentialRampToValueAtTime(0.01, start + dur);
            osc.start(start);
            osc.stop(start + dur);
          } catch { /* skip this tone */ }
          maxEnd = Math.max(maxEnd, delayMs / 1000 + dur);
        }
        // Close the context a beat after the last tone ends.
        setTimeout(() => { try { c.close(); } catch {} }, (maxEnd + 0.15) * 1000);
      };

      if (c.state === "suspended") {
        // No prior gesture, or webview suspended a brand-new ctx: resume first.
        c.resume().then(schedule).catch(() => { try { c.close(); } catch {} });
      } else {
        schedule();
      }
    } catch { /* never throw from audio */ }
  },

  positionEntered() { this._playSequence([[800, 0.15, "sine", 0], [1000, 0.15, "sine", 150]]); },
  stopLossHit()     { this._playSequence([[400, 0.2, "sine", 0], [350, 0.2, "sine", 200], [300, 0.3, "sine", 400]]); },
  takeProfitHit()   { this._playSequence([[600, 0.1, "sine", 0], [800, 0.1, "sine", 100], [1000, 0.15, "sine", 200]]); },
  positionClosed()  { this._playSequence([[520, 0.12, "sine", 0], [440, 0.16, "sine", 120]]); },
  alertError()      { this._playSequence([[300, 0.18, "square", 0], [300, 0.18, "square", 230], [260, 0.28, "square", 480]]); },
  alertWarning()    { this._playSequence([[500, 0.14, "triangle", 0], [560, 0.18, "triangle", 160]]); },
  alertInfo()       { this._playSequence([[660, 0.16, "sine", 0]]); },
};

/* ── Helpers ──────────────────────────────────────────────────── */
function fmtPnL(v) {
  if (v == null || isNaN(v)) return "";
  const r = Math.round(v);
  return ` ${r >= 0 ? "+" : "−"}₹${Math.abs(r).toLocaleString("en-IN")}`;
}

const STRATEGY_LABEL = {
  SCALP_V1: "Scalp", SCALP_V2: "Scalp V2", SCALP_V3: "Scalp V3",
  BB_V1: "BB", BB_V2: "BB V2", HA_V1: "Heikin Ashi", PST_SELL: "PST Sell", PST_HEDGE: "PST Hedge",
};
function stratLabel(id) { return STRATEGY_LABEL[id] || id || "Strategy"; }
function modeTag(mode) { return (mode || "live").toLowerCase() === "live" ? "LIVE" : "PAPER"; }

// Map an alert code to a short human title for the toast/bell row.
const ALERT_TITLE = {
  DEAD_ENTRY:    "Order rejected",
  DEAD_LEG:      "Leg rejected",
  PARTIAL_FILL:  "Partial fill — action needed",
  ENTRY_TIMEOUT: "Order not filled",
  LEG_TIMEOUT:   "Leg not filled",
  GTT_FAIL:      "Protection failed",
  FILL_TIMEOUT:  "Fill not confirmed",
  RECONCILE_NEEDED: "Needs reconcile",
  // SCALP_V3-specific operational alerts
  V3_GTT_FAIL:     "Hedge protection failed",
  V3_DEAD_ENTRY:   "Hedge entry rejected",
  V3_PARTIAL_FILL: "Partial fill — action needed",
  // system-state
  BROKER_DOWN:   "Broker disconnected",
  BROKER_UP:     "Broker reconnected",
  RELAY_DOWN:    "Order relay down",
  RELAY_UP:      "Order relay back online",
  MAX_LOSS:      "Daily max-loss hit",
  MAX_PROFIT:    "Daily max-profit hit",
  EOD_SQUAREOFF: "End-of-day square-off",
  // storage watchdog (disk_guard.py)
  DISK_LOW:      "Storage running low",
  DISK_CRITICAL: "Storage critically low",
  DISK_OK:       "Storage recovered",
};
function alertTitle(code) { return ALERT_TITLE[code] || "Alert"; }

/* ── Provider ─────────────────────────────────────────────────── */
export function NotificationProvider({ children, health }) {
  const toast = useToast();

  const [settings, setSettings] = useState({
    notify_audio: true,
    notify_toast: true,
    show_account_balance: true,
    audio_rules: {},
  });
  const [loading, setLoading] = useState(true);
  const [items, setItems] = useState([]);

  const settingsRef = useRef(settings);
  useEffect(() => { settingsRef.current = settings; }, [settings]);

  const lastIdRef = useRef(-1);

  // Local id seed for synthetic (frontend-generated) alerts so their keys never
  // collide with backend event ids.
  const synthSeqRef = useRef(0);

  /* ── Install the audio unlock once, on mount ──
     Registers one-shot first-gesture listeners so audio is blessed by a real
     user interaction (autoplay policy). Per-tone contexts created afterwards
     start "running". Toasts/bell work regardless (pure DOM). */
  useEffect(() => {
    AudioAlerts.installUnlock();
  }, []);

  /* ── Notification center mutators ── */
  const addNotification = useCallback((n) => {
    setItems((prev) => {
      const next = [n, ...prev];
      return next.length > MAX_NOTIFICATIONS ? next.slice(0, MAX_NOTIFICATIONS) : next;
    });
  }, []);
  const markAllRead = useCallback(() => {
    setItems((prev) => prev.map((n) => (n.read ? n : { ...n, read: true })));
  }, []);
  const markRead = useCallback((id) => {
    setItems((prev) => prev.map((n) => (n.id === id ? { ...n, read: true } : n)));
  }, []);
  const clearAll = useCallback(() => setItems([]), []);

  /* ── Shared alert renderer (used by feed + synthetic) ── */
  const fireAlert = useCallback((a) => {
    // a: { id, ts(ms), severity, code, strategy_id, symbol, mode, message }
    const s = settingsRef.current;
    const severity = (a.severity || "warning").toLowerCase();
    const label = a.strategy_id ? stratLabel(a.strategy_id) : "System";
    const title = `${label} · ${alertTitle(a.code)}`;
    const body = a.message || a.code || "Alert";

    addNotification({
      id: String(a.id),
      ts: a.ts || Date.now(),
      severity,
      code: a.code || "",
      title,
      message: body,
      strategy_id: a.strategy_id || "",
      symbol: a.symbol || "",
      mode: a.mode ? modeTag(a.mode) : "",
      read: false,
    });

    if (s.notify_audio) {
      if (severity === "error") AudioAlerts.alertError();
      else if (severity === "warning") AudioAlerts.alertWarning();
      else AudioAlerts.alertInfo();
    }
    if (s.notify_toast) {
      const opts = severity === "error" ? { duration: 0 } : { duration: 8000 };
      if (severity === "error") toast.error(title, body, opts);
      else if (severity === "warning") toast.warning(title, body, opts);
      else toast.info(title, body, opts);
    }
  }, [toast, addNotification]);

  /* ── Load + persist app settings ── */
  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/app/settings`);
      if (res.ok) {
        const data = await res.json();
        if (data && typeof data === "object") {
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
    setSettings(next);
    // A settings save is a user gesture — a good moment to (re)unlock audio so
    // toggling sound on in Settings takes effect immediately this session.
    AudioAlerts.unlock();
    try {
      await fetch(`${getApiBase()}/api/app/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(next),
      });
    } catch { /* best-effort */ }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, SETTINGS_POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  /* ── Fire a single feed event ── */
  const fireEvent = useCallback((evt) => {
    const s = settingsRef.current;
    const type = (evt.event_type || "").toUpperCase();
    const label = stratLabel(evt.strategy_id);
    const sym = evt.symbol || "";
    const tag = modeTag(evt.mode);

    if (type === "ALERT") {
      fireAlert({
        id: evt.id,
        ts: evt.ts ? evt.ts * 1000 : Date.now(),
        severity: evt.severity,
        code: evt.code,
        strategy_id: evt.strategy_id,
        symbol: sym,
        mode: evt.mode,
        message: evt.message,
      });
      return;
    }

    // ── TRADE events (unchanged behaviour) ──
    const modeKey = (evt.mode || "live").toLowerCase() === "live" ? "LIVE" : "PAPER";
    const rule = s.audio_rules?.[evt.strategy_id];
    const perRuleAllows = rule ? rule[modeKey] !== false : true;
    if (s.notify_audio && perRuleAllows) {
      if (type === "ENTER") AudioAlerts.positionEntered();
      else if (type === "TP") AudioAlerts.takeProfitHit();
      else if (type === "SL") AudioAlerts.stopLossHit();
      else {
        if (evt.pnl == null || isNaN(evt.pnl)) AudioAlerts.positionClosed();
        else if (evt.pnl >= 0) AudioAlerts.takeProfitHit();
        else AudioAlerts.stopLossHit();
      }
    }
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
  }, [toast, fireAlert]);

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
        } catch { /* network hiccup — retry next tick */ }
        await new Promise((r) => setTimeout(r, POLL_MS));
      }
    }
    poll();
    return () => { alive = false; };
  }, [fireEvent]);

  /* ── FRONTEND broker-connectivity watch (edge-triggered) ──────
     Watches the health prop App.jsx already polls. Fires ONE alert on the
     true→false transition and ONE recovery alert on false→true. Never repeats
     while the state is unchanged, so the per-poll churn never floods the bell.

     We only start watching after the FIRST definitive reading, so a cold start
     (health defaults to disconnected before the first poll resolves) doesn't
     fire a spurious "disconnected" alert. */
  const brokerPrevRef = useRef(undefined);   // undefined = no reading yet
  useEffect(() => {
    if (!health) return;
    // Only treat as a real reading once the backend is up; if the backend is
    // down we can't trust the broker flag, and the (separate) backend-down
    // surface — BackendBootGuard — handles that case.
    const backendUp = health.backendUp === true;
    const connected = health.zerodhaConnected === true;

    if (!backendUp) {
      // Don't churn broker state while backend is down/unknown.
      return;
    }

    const prev = brokerPrevRef.current;

    if (prev === undefined) {
      // First definitive reading — seed without alerting.
      brokerPrevRef.current = connected;
      return;
    }

    if (prev === true && connected === false) {
      synthSeqRef.current -= 1;   // negative ids => synthetic, never collide with feed
      fireAlert({
        id: `sys${synthSeqRef.current}`,
        ts: Date.now(),
        severity: "error",
        code: "BROKER_DOWN",
        message: "Broker session disconnected — strategies cannot place or exit live orders until it reconnects.",
      });
    } else if (prev === false && connected === true) {
      synthSeqRef.current -= 1;
      fireAlert({
        id: `sys${synthSeqRef.current}`,
        ts: Date.now(),
        severity: "info",
        code: "BROKER_UP",
        message: "Broker session reconnected — live trading restored.",
      });
    }

    brokerPrevRef.current = connected;
  }, [health, fireAlert]);

  const unreadCount = items.reduce((n, it) => (it.read ? n : n + 1), 0);

  const settingsValue = { settings, loading, saveSettings, refresh };
  const notificationsValue = { items, unreadCount, markAllRead, markRead, clearAll };

  return (
    <AppSettingsContext.Provider value={settingsValue}>
      <NotificationsContext.Provider value={notificationsValue}>
        {children}
      </NotificationsContext.Provider>
    </AppSettingsContext.Provider>
  );
}