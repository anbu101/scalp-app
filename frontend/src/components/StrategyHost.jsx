/**
 * STRATEGY HOST  (redesigned)
 *
 * Intended path: src/components/StrategyHost.jsx
 *
 * PHASE 3 CHANGE: the strategy list is now filtered by license
 * entitlements (useEntitlements). A user licensed for ["SCALP_V1","BB_V2"]
 * sees exactly those two panels — no idle/empty panels for strategies
 * their backend never launches. ADMIN (["*"]) sees everything, identical
 * to pre-license behavior. Until the first license fetch resolves, the
 * host renders nothing (avoids a flash of panels that then disappear).
 * Everything else is verbatim from the previous version.
 *
 * PERSIST_FOCUS: the focused strategy is now remembered across page
 * navigation and app restarts via localStorage. Previously focusId lived
 * only in component state, so leaving Dashboard (which unmounts this host)
 * and returning reset the focus to the first strategy (SCALP_V1). Now the
 * last user-picked strategy is restored on mount.
 *
 * Layout model (replaces the old expand/collapse row):
 *   - ONE expanded panel (the focus) on the left.
 *   - ALL strategies remain visible in the right RAIL of slim cards
 *     (including the currently-focused one, which is shown highlighted).
 *   - Clicking a rail card SWAPS it into the focus slot (smooth transition).
 *   - LIVE strategies are surfaced first; the default focus is a live strategy
 *     if any are live, else the first active strategy.
 *   - Mobile: master/detail — a horizontal chip rail (matching the Settings
 *     page) picks ONE strategy, and only that panel is rendered. This avoids
 *     the long stacked scroll AND the scroll-jump caused by off-screen panels
 *     changing height on their live-data polls.
 *
 * Mode source: the host fetches each strategy's config (slow 15s poll) to learn
 * trade_execution_mode for ordering + rail badges. Panels still fetch their own
 * detail; this is only for ordering/labels.
 *
 * ── KILL_SWITCH ── mounted ONCE here, above the focused panel (both the
 * mobile and desktop render sites). Deliberately NOT added to any strategy
 * panel file: only one panel renders at a time in this master/detail
 * layout, so a single host-level mount covers every strategy — including
 * BB, whose files stay untouched. The bar self-hides unless the focused
 * strategy is actually killable (LIVE mode, or IC's live group riding
 * under a flipped config).
 */

import { useEffect, useState, useMemo, useCallback } from "react";
import { getStrategyById } from "../strategies/registry";
import { getStrategyConfig } from "../api";
import { useIsMobile } from "../hooks/useIsMobile";
import { useEntitlements } from "../hooks/useEntitlements";
import { colors, spacing } from "../tokens";
import ScalpV3Panel from "../strategies/scalp_v3/ScalpV3Panel.jsx";
import ScalpV5Panel from "../strategies/scalpv5/ScalpV5Panel.jsx";
import ICV1Panel    from "../strategies/ic_v1/ICV1Panel.jsx";

import ScalpPanel   from "../strategies/scalp/ScalpPanel";
import BBPanel      from "../strategies/bb_v1/BBPanel";
import BBV2Panel    from "../strategies/bb_v2/BBV2Panel";
import HAPanel      from "../strategies/ha_v1/HAPanel";

import PSTPanel     from "../strategies/pst/PSTPanel.jsx";
import TMAPanel     from "../strategies/tma/TMAPanel.jsx";   // ── TMA_V1 ──
import KillSwitch   from "./KillSwitch.jsx";   // ── KILL_SWITCH ──
// Fixed display order — MUST match the Settings page rail order so the two
// pages list strategies identically. (Was previously live-first sorted.)
const ACTIVE_STRATEGY_IDS = ["SCALP_V1", "SCALP_V3", "SCALP_V5", "IC_V1", "BB_V1", "BB_V2", "HA_V1", "PST_SELL", "PST_HEDGE", "TMA_V1"];
const MAX_PANELS = 14;   // headroom — was 9 (sized for the pre-V2-removal list); the slice silently DROPPED strategies beyond it (PST_HEDGE was #10)

// PERSIST_FOCUS BEGIN — localStorage key for the last user-picked strategy.
const FOCUS_STORAGE_KEY = "scalp.strategyHost.focusId";
// PERSIST_FOCUS END


const META = {
  SCALP_V1: { name: "Scalp V1",         accent: colors.warning ?? "#f59e0b" },
  SCALP_V3: { name: "Scalp V3",      accent: "#ec4899" },
  SCALP_V5: { name: "Scalp V5",      accent: "#06b6d4" },
  IC_V1:    { name: "Iron Condor",   accent: "#6366f1" },
  BB_V1:    { name: "BB V1", accent: colors.primary ?? "#3b82f6" },
  BB_V2:    { name: "BB V2", accent: "#3b82f6" },
  HA_V1:    { name: "Heikin Ashi",   accent: "#14b8a6" },

  PST_SELL:  { name: "PST Sell",      accent: "#fb7185" },
  PST_HEDGE: { name: "PST Hedge",     accent: "#be123c" },
  TMA_V1:    { name: "TMA V1",        accent: "#8b5cf6" },   // ── TMA_V1 ──
};

const C = {
  bg:        colors.bg?.primary    ?? "#0a0f1e",
  bgCard:    colors.bg?.secondary  ?? "#111827",
  bgSurf:    colors.bg?.tertiary   ?? "#1f2937",
  border:    colors.border?.light  ?? "#374151",
  borderDim: colors.border?.dark   ?? "#1f2937",
  text:      colors.text?.primary  ?? "#f9fafb",
  textSec:   colors.text?.secondary ?? "#d1d5db",
  textMuted: colors.text?.muted    ?? "#6b7280",
  green:     colors.success        ?? "#10b981",
  red:       colors.danger         ?? "#ef4444",
};

function renderPanel(strategyId, ltpMap) {
  const common = { key: strategyId, ltpMap, isPrimary: true, onBecomePrimary: () => {} };
  switch (strategyId) {
    case "SCALP_V1": return <ScalpPanel   {...common} />;
    case "SCALP_V3": return <ScalpV3Panel {...common} />;
    case "SCALP_V5": return <ScalpV5Panel {...common} />;
    case "IC_V1":    return <ICV1Panel    {...common} />;
    case "BB_V1":    return <BBPanel      {...common} strategyId="BB_V1" />;
    case "BB_V2":    return <BBV2Panel    {...common} />;
    case "HA_V1":    return <HAPanel      {...common} />;
    case "PST_SELL":  return <PSTPanel     {...common} strategyId="PST_SELL" />;
    case "PST_HEDGE": return <PSTPanel     {...common} strategyId="PST_HEDGE" />;
    case "TMA_V1":    return <TMAPanel     {...common} />;   // ── TMA_V1 ──
    default:
      return (
        <div style={{ padding: 16, border: `1px dashed ${C.border}`, borderRadius: 8,
          color: C.textMuted, fontSize: 13, display: "flex", alignItems: "center", justifyContent: "center" }}>
          Unknown strategy: {strategyId}
        </div>
      );
  }
}

function RailCard({ id, name, accent, mode, active, onClick }) {
  const isLive = mode === "LIVE";
  return (
    <button
      onClick={onClick}
      aria-current={active ? "true" : undefined}
      style={{
        width: "100%", textAlign: "left",
        cursor: active ? "default" : "pointer",
        // Every card carries its accent: a 4px full-opacity left bar plus a
        // faint accent-tinted surface. Active is simply a STRONGER tint + a
        // tinted outer border. This guarantees the colour code is always
        // visible on every card, focused or not.
        background: active ? `${accent}26` : `${accent}0d`,
        borderTopWidth: 1, borderRightWidth: 1, borderBottomWidth: 1,
        borderTopStyle: "solid", borderRightStyle: "solid", borderBottomStyle: "solid",
        borderTopColor:    active ? `${accent}66` : C.borderDim,
        borderRightColor:  active ? `${accent}66` : C.borderDim,
        borderBottomColor: active ? `${accent}66` : C.borderDim,
        borderLeftWidth: 4,
        borderLeftStyle: "solid",
        borderLeftColor: accent,
        borderRadius: 8,
        padding: "9px 11px",
        transition: "background 0.15s ease, border-color 0.15s ease, transform 0.12s ease",
      }}
      onMouseEnter={(e) => {
        if (active) return;
        e.currentTarget.style.background = `${accent}1a`;
        e.currentTarget.style.transform = "translateX(2px)";
      }}
      onMouseLeave={(e) => {
        if (active) return;
        e.currentTarget.style.background = `${accent}0d`;
        e.currentTarget.style.transform = "translateX(0)";
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: active ? C.text : C.textSec, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {name}
        </span>
        <span style={{ width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
          background: isLive ? C.green : C.textMuted, boxShadow: isLive ? `0 0 6px ${C.green}88` : "none" }} />
      </div>
      <div style={{ fontSize: 9, color: C.textMuted, textTransform: "uppercase", letterSpacing: "0.5px", marginTop: 3 }}>
        {id} · {isLive ? "Live" : "Paper"}
      </div>
    </button>
  );
}

/* Mobile master/detail chip — mirrors the Settings page horizontal rail so the
   two screens pick strategies identically. Accent left-bar + live/paper dot. */
function RailChip({ id, name, accent, mode, active, onClick }) {
  const isLive = mode === "LIVE";
  return (
    <button
      onClick={onClick}
      aria-current={active ? "true" : undefined}
      style={{
        flexShrink: 0,
        display: "flex", alignItems: "center", gap: 6,
        padding: "8px 14px", borderRadius: 8,
        borderTopWidth: 1, borderRightWidth: 1, borderBottomWidth: 1,
        borderTopStyle: "solid", borderRightStyle: "solid", borderBottomStyle: "solid",
        borderTopColor:    active ? `${accent}66` : C.border,
        borderRightColor:  active ? `${accent}66` : C.border,
        borderBottomColor: active ? `${accent}66` : C.border,
        borderLeftWidth: 3, borderLeftStyle: "solid", borderLeftColor: accent,
        background: active ? `${accent}1f` : C.bgSurf,
        color: active ? C.text : C.textMuted,
        fontSize: 12, fontWeight: 600, cursor: "pointer",
        whiteSpace: "nowrap",
      }}
    >
      <span style={{
        width: 7, height: 7, borderRadius: "50%",
        background: mode == null ? "transparent" : isLive ? C.green : colors.primary ?? "#3b82f6",
      }} />
      {name}
    </button>
  );
}

export default function StrategyHost({ ltpMap }) {
  const isMobile = useIsMobile();
  const { loaded: licenseLoaded, allowsStrategy } = useEntitlements();
  const [modes, setModes] = useState({});

  // PERSIST_FOCUS BEGIN — initialise focus from localStorage so the last
  // strategy the user was viewing is restored after leaving and returning to
  // the Dashboard (which unmounts this host) or after an app restart.
  const [focusId, setFocusId] = useState(() => {
    try { return localStorage.getItem(FOCUS_STORAGE_KEY) || null; } catch { return null; }
  });
  const [userPicked, setUserPicked] = useState(() => {
    try { return !!localStorage.getItem(FOCUS_STORAGE_KEY); } catch { return false; }
  });
  // PERSIST_FOCUS END

  // PHASE 3: registry-resolved AND license-allowed. ADMIN (["*"]) passes
  // everything -> identical list to pre-license builds.
  const active = useMemo(
    () => ACTIVE_STRATEGY_IDS.filter((id) => {
      const found = getStrategyById(id);
      if (!found) console.warn(`[StrategyHost] Unknown strategy id "${id}" — skipping.`);
      return !!found && allowsStrategy(id);
    }).slice(0, MAX_PANELS),
    [allowsStrategy]
  );

  const loadModes = useCallback(async () => {
    const out = {};
    await Promise.all(active.map(async (id) => {
      try {
        const cfg = await getStrategyConfig(id);
        out[id] = cfg?.trade_execution_mode === "LIVE" ? "LIVE" : "PAPER";
      } catch { out[id] = "PAPER"; }
    }));
    setModes(out);
  }, [active]);

  useEffect(() => {
    loadModes();
    const t = setInterval(loadModes, 15000);
    return () => clearInterval(t);
  }, [loadModes]);

  // Fixed Settings-page order; no live-first reordering. Just the active
  // (registry-resolved) ids in their declared sequence.
  const ordered = useMemo(() => active, [active]);

  useEffect(() => {
    if (ordered.length === 0) return;
    if (!userPicked) {
      setFocusId(ordered[0]);
    } else if (focusId && !ordered.includes(focusId)) {
      // PERSIST_FOCUS BEGIN — a persisted/picked id is no longer available
      // (e.g. license change). Fall back to the first strategy AND clear the
      // stale persisted value so we don't keep trying to restore it.
      setFocusId(ordered[0]);
      setUserPicked(false);
      try { localStorage.removeItem(FOCUS_STORAGE_KEY); } catch {}
      // PERSIST_FOCUS END
    }
  }, [ordered, userPicked, focusId]);

  // PHASE 3: wait for the first license read so panels don't flash in and
  // then vanish for non-admin users. (Resolves in well under a second.)
  if (!licenseLoaded) return null;

  if (active.length === 0) return null;

  const effectiveFocus = focusId && ordered.includes(focusId) ? focusId : ordered[0];

  // PERSIST_FOCUS BEGIN — write the user's pick through to localStorage so it
  // survives unmount (page navigation) and app restarts.
  const pick = (id) => {
    setUserPicked(true);
    setFocusId(id);
    try { localStorage.setItem(FOCUS_STORAGE_KEY, id); } catch {}
  };
  // PERSIST_FOCUS END

  if (isMobile) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
        {/* Horizontal chip rail — master picker (matches Settings page). Only
            the focused strategy's panel is rendered below, so off-screen
            panels can't shift the scroll position when their live data polls. */}
        <div style={{
          display: "flex", gap: spacing.sm, overflowX: "auto",
          paddingBottom: spacing.xs,
        }}>
          {ordered.map((id) => (
            <RailChip
              key={id}
              id={id}
              name={META[id]?.name || id}
              accent={META[id]?.accent || C.border}
              mode={modes[id]}
              active={id === effectiveFocus}
              onClick={() => pick(id)}
            />
          ))}
        </div>

        <div
          key={effectiveFocus}
          style={{ width: "100%", animation: "hostFocusIn 0.32s cubic-bezier(0.22,1,0.36,1)" }}
        >
          <KillSwitch strategyId={effectiveFocus} />
          {renderPanel(effectiveFocus, ltpMap)}
        </div>

        <style>{`
          @keyframes hostFocusIn {
            0%   { opacity: 0; transform: translateY(6px) scale(0.995); }
            100% { opacity: 1; transform: translateY(0) scale(1); }
          }
        `}</style>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", gap: spacing.md, alignItems: "stretch", width: "100%" }}>
      {ordered.length > 0 && (
        <div style={{ flex: "0 0 184px", display: "flex", flexDirection: "column", gap: spacing.sm }}>
          <div style={{ fontSize: 9, color: C.textMuted, textTransform: "uppercase", letterSpacing: "1px", fontWeight: 700, padding: "0 2px 2px" }}>
            Strategies
          </div>
          {ordered.map((id) => (
            <div key={id} style={{ animation: "hostRailIn 0.3s ease" }}>
              <RailCard
                id={id}
                name={META[id]?.name || id}
                accent={META[id]?.accent || C.border}
                mode={modes[id]}
                active={id === effectiveFocus}
                onClick={() => pick(id)}
              />
            </div>
          ))}
          <div style={{ fontSize: 9, color: C.textMuted, opacity: 0.6, textAlign: "center", padding: 4 }}>
            click to focus
          </div>
        </div>
      )}

      <div
        key={effectiveFocus}
        style={{ flex: 1, minWidth: 0, animation: "hostFocusIn 0.32s cubic-bezier(0.22,1,0.36,1)" }}
      >
        <KillSwitch strategyId={effectiveFocus} />
        {renderPanel(effectiveFocus, ltpMap)}
      </div>

      <style>{`
        @keyframes hostFocusIn {
          0%   { opacity: 0; transform: translateY(6px) scale(0.995); }
          100% { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes hostRailIn {
          0%   { opacity: 0; transform: translateX(-8px); }
          100% { opacity: 1; transform: translateX(0); }
        }
      `}</style>
    </div>
  );
}