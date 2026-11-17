/**
 * OthersGroup — src/components/OthersGroup.jsx
 *
 * ── SETTINGS_OTHERS_20261117 ── shared pieces for the "Others" group that folds
 * switched-off strategies away on the Settings rail and the App Settings sound
 * grid (same rule as Trades / Analytics, TD-009: OFF + flat + no trade today,
 * grouped only from two up — useStrategyVisibility).
 *
 *   DotCluster   overlapping accent dots, one per strategy in the group
 *   Chevron      ▸ that turns to ▾
 *   Collapse     smooth open / close (grid-template-rows 0fr → 1fr)
 *   useOthersOpen(key, forceOpen)   open state remembered per surface; forced
 *                open while the selection is inside the group
 */
import { useState } from "react";
import { colors } from "../tokens";

export function DotCluster({ accents = [], max = 5, size = 9 }) {
  const shown = accents.slice(0, max);
  const extra = accents.length - shown.length;
  return (
    <span aria-hidden="true" style={{ display: "inline-flex", alignItems: "center", flexShrink: 0 }}>
      {shown.map((c, i) => (
        <span key={i} style={{
          width: size, height: size, borderRadius: "50%", background: c,
          marginLeft: i === 0 ? 0 : -Math.round(size * 0.38),
          boxShadow: `0 0 0 2px ${colors.bg.secondary}`, opacity: 0.85,
        }} />
      ))}
      {extra > 0 && (
        <span style={{ marginLeft: 4, fontSize: 9, fontWeight: 700, color: colors.text.muted }}>+{extra}</span>
      )}
    </span>
  );
}

export function Chevron({ open, size = 10 }) {
  return (
    <span aria-hidden="true" style={{
      display: "inline-block", fontSize: size, lineHeight: 1, color: colors.text.muted,
      transform: open ? "rotate(90deg)" : "none", transition: "transform .2s ease",
    }}>▸</span>
  );
}

export function Collapse({ open, children, testid }) {
  return (
    <div data-testid={testid} data-open={open ? "true" : "false"} style={{
      display: "grid", gridTemplateRows: open ? "1fr" : "0fr",
      transition: "grid-template-rows .22s ease", minWidth: 0,
    }}>
      <div style={{ overflow: "hidden", minHeight: 0, minWidth: 0,
        visibility: open ? "visible" : "hidden",                        // closed rows: not clickable / focusable
        transition: `visibility 0s linear ${open ? "0s" : ".22s"}` }} aria-hidden={!open}>
        {children}
      </div>
    </div>
  );
}

export function useOthersOpen(storageKey, forceOpen = false) {
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(storageKey) === "1"; } catch { return false; } });
  const toggle = () => setOpen((o) => {
    const next = forceOpen ? true : !o;
    try { localStorage.setItem(storageKey, next ? "1" : "0"); } catch {}
    return next;
  });
  return [open || forceOpen, toggle];
}
