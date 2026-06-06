/**
 * STRATEGY HOST  (redesigned)
 *
 * Intended path: src/components/StrategyHost.jsx
 *
 * Layout model (replaces the old expand/collapse row):
 *   - ONE expanded panel (the focus) on the left.
 *   - All other strategies collapse into a right RAIL of slim cards.
 *   - Clicking a rail card SWAPS it into the focus slot (smooth transition).
 *   - LIVE strategies are surfaced first; the default focus is a live strategy
 *     if any are live, else the first active strategy.
 *   - Mobile: single-column stack, live strategies first, each panel full-width.
 *
 * Mode source: the host fetches each strategy's config (slow 15s poll) to learn
 * trade_execution_mode for ordering + rail badges. Panels still fetch their own
 * detail; this is only for ordering/labels.
 */

import { useEffect, useState, useMemo, useCallback } from "react";
import { getStrategyById } from "../strategies/registry";
import { getStrategyConfig } from "../api";
import { useIsMobile } from "../hooks/useIsMobile";
import { colors, spacing } from "../tokens";
import ScalpV3Panel from "../strategies/scalp_v3/ScalpV3Panel.jsx";

import ScalpPanel   from "../strategies/scalp/ScalpPanel";
import BBPanel      from "../strategies/bb_v1/BBPanel";
import BBV2Panel    from "../strategies/bb_v2/BBV2Panel";
import HAPanel      from "../strategies/ha_v1/HAPanel";
import ScalpV2Panel from "../strategies/scalp_v2/ScalpV2Panel.jsx";

const ACTIVE_STRATEGY_IDS = ["SCALP_V2", "SCALP_V3", "SCALP_V1", "BB_V1", "BB_V2", "HA_V1"];
const MAX_PANELS = 6;   // was 5

const META = {
  SCALP_V1: { name: "Scalp",         accent: colors.warning ?? "#f59e0b" },
  SCALP_V2: { name: "Scalp V2",      accent: "#a855f7" },
  SCALP_V3: { name: "Scalp V3",      accent: "#ec4899" },
  BB_V1:    { name: "BN BB Options", accent: colors.primary ?? "#3b82f6" },
  BB_V2:    { name: "BB Options V2", accent: "#3b82f6" },
  HA_V1:    { name: "Heikin Ashi",   accent: "#14b8a6" },
  
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
    case "SCALP_V2": return <ScalpV2Panel {...common} />;
    case "SCALP_V3": return <ScalpV3Panel {...common} />;
    case "BB_V1":    return <BBPanel      {...common} strategyId="BB_V1" />;
    case "BB_V2":    return <BBV2Panel    {...common} />;
    case "HA_V1":    return <HAPanel      {...common} />;
    default:
      return (
        <div style={{ padding: 16, border: `1px dashed ${C.border}`, borderRadius: 8,
          color: C.textMuted, fontSize: 13, display: "flex", alignItems: "center", justifyContent: "center" }}>
          Unknown strategy: {strategyId}
        </div>
      );
  }
}

function RailCard({ id, name, accent, mode, onClick }) {
  const isLive = mode === "LIVE";
  return (
    <button onClick={onClick} style={{
      width: "100%", textAlign: "left", cursor: "pointer",
      background: C.bgCard,
      border: `1px solid ${C.borderDim}`,
      borderLeft: `3px solid ${accent}`,
      borderRadius: 8,
      padding: "9px 11px",
      transition: "background 0.15s ease, border-color 0.15s ease, transform 0.12s ease",
    }}
      onMouseEnter={(e) => { e.currentTarget.style.background = C.bgSurf; e.currentTarget.style.transform = "translateX(-2px)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = C.bgCard; e.currentTarget.style.transform = "translateX(0)"; }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: C.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
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

export default function StrategyHost({ ltpMap }) {
  const isMobile = useIsMobile();
  const [modes, setModes] = useState({});
  const [focusId, setFocusId] = useState(null);
  const [userPicked, setUserPicked] = useState(false);

  const active = useMemo(
    () => ACTIVE_STRATEGY_IDS.filter((id) => {
      const found = getStrategyById(id);
      if (!found) console.warn(`[StrategyHost] Unknown strategy id "${id}" — skipping.`);
      return !!found;
    }).slice(0, MAX_PANELS),
    []
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

  const ordered = useMemo(() => {
    const live = active.filter((id) => modes[id] === "LIVE");
    const paper = active.filter((id) => modes[id] !== "LIVE");
    return [...live, ...paper];
  }, [active, modes]);

  useEffect(() => {
    if (ordered.length === 0) return;
    if (!userPicked) {
      setFocusId(ordered[0]);
    } else if (focusId && !ordered.includes(focusId)) {
      setFocusId(ordered[0]);
    }
  }, [ordered, userPicked, focusId]);

  if (active.length === 0) return null;

  const effectiveFocus = focusId && ordered.includes(focusId) ? focusId : ordered[0];
  const railIds = ordered.filter((id) => id !== effectiveFocus);

  const pick = (id) => { setUserPicked(true); setFocusId(id); };

  if (isMobile) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
        {ordered.map((id) => (
          <div key={id} style={{ width: "100%" }}>
            {renderPanel(id, ltpMap)}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", gap: spacing.md, alignItems: "stretch", width: "100%" }}>
      <div
        key={effectiveFocus}
        style={{ flex: 1, minWidth: 0, animation: "hostFocusIn 0.32s cubic-bezier(0.22,1,0.36,1)" }}
      >
        {renderPanel(effectiveFocus, ltpMap)}
      </div>

      {railIds.length > 0 && (
        <div style={{ flex: "0 0 184px", display: "flex", flexDirection: "column", gap: spacing.sm }}>
          <div style={{ fontSize: 9, color: C.textMuted, textTransform: "uppercase", letterSpacing: "1px", fontWeight: 700, padding: "0 2px 2px" }}>
            Strategies
          </div>
          {railIds.map((id) => (
            <div key={id} style={{ animation: "hostRailIn 0.3s ease" }}>
              <RailCard
                id={id}
                name={META[id]?.name || id}
                accent={META[id]?.accent || C.border}
                mode={modes[id]}
                onClick={() => pick(id)}
              />
            </div>
          ))}
          <div style={{ fontSize: 9, color: C.textMuted, opacity: 0.6, textAlign: "center", padding: 4 }}>
            click to focus
          </div>
        </div>
      )}

      <style>{`
        @keyframes hostFocusIn {
          0%   { opacity: 0; transform: translateY(6px) scale(0.995); }
          100% { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes hostRailIn {
          0%   { opacity: 0; transform: translateX(8px); }
          100% { opacity: 1; transform: translateX(0); }
        }
      `}</style>
    </div>
  );
}