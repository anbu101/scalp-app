/**
 * DebugPanel — Slide-up Drawer
 * Path: src/components/DebugPanel.jsx
 *
 * During normal use: invisible. A small floating pill sits above the status bar.
 * Click it → drawer slides up from the bottom with all debug actions.
 * Click backdrop or ✕ → closes.
 *
 * All original functionality preserved (backend check, slot links, global links).
 */

import { useState, useCallback } from "react";
import { getApiBase } from "../api/base";

/* ─────────────────────────────────────────────
   Tokens
───────────────────────────────────────────── */
const BG_SURFACE  = "#0b1120";
const BG_CARD     = "#111827";
const BG_INPUT    = "#020617";
const BORDER      = "#1e2d45";
const TEXT        = "#cbd5e1";
const TEXT_MUTED  = "#475569";
const PRIMARY     = "#3b82f6";

/* ─────────────────────────────────────────────
   DebugPanel
───────────────────────────────────────────── */
export default function DebugPanel({ rows = [] }) {
  const base = `${getApiBase()}/debug/ui`;
  const [open, setOpen] = useState(false);

  const openDrawer  = useCallback(() => setOpen(true),  []);
  const closeDrawer = useCallback(() => setOpen(false), []);

  function go(path) {
    const url = `${base}${path}`;
    if (window.__TAURI__?.shell?.open) {
      window.__TAURI__.shell.open(url);
    } else {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  }

  // Build slot → symbol map
  const slotMap = {};
  rows.forEach((r) => {
    if (r.slot && r.tradingsymbol && !slotMap[r.slot]) {
      slotMap[r.slot] = r.tradingsymbol;
    }
  });
  const ceSlots = Object.keys(slotMap).filter((s) => s.startsWith("CE")).sort();
  const peSlots = Object.keys(slotMap).filter((s) => s.startsWith("PE")).sort();

  /* ── No internal health poll needed — StatusBar shows system status globally ── */

  return (
    <>
      {/* ── Floating trigger pill ─────────────────────────────────────── */}
      {/* Sits just above the 28px StatusBar; right-aligned */}
      <button
        onClick={openDrawer}
        title="Open debug tools"
        style={{
          position:     "fixed",
          bottom:       36,       // above 28px StatusBar
          right:        20,
          zIndex:       8000,
          display:      "flex",
          alignItems:   "center",
          gap:          6,
          padding:      "5px 12px",
          borderRadius: 20,
          background:   BG_SURFACE,
          border:       `1px solid ${BORDER}`,
          color:        TEXT_MUTED,
          fontSize:     11,
          fontWeight:   600,
          cursor:       "pointer",
          letterSpacing: "0.3px",
          boxShadow:    "0 2px 8px rgba(0,0,0,0.4)",
          transition:   "color 0.15s, border-color 0.15s",
          userSelect:   "none",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.color        = TEXT;
          e.currentTarget.style.borderColor  = PRIMARY + "80";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.color        = TEXT_MUTED;
          e.currentTarget.style.borderColor  = BORDER;
        }}
      >
        <span style={{ fontSize: 12 }}>🛠</span>
        Debug
      </button>

      {/* ── Backdrop ─────────────────────────────────────────────────── */}
      {open && (
        <div
          onClick={closeDrawer}
          style={{
            position:   "fixed",
            inset:      0,
            background: "rgba(0,0,0,0.55)",
            zIndex:     8100,
            backdropFilter: "blur(2px)",
          }}
        />
      )}

      {/* ── Drawer ───────────────────────────────────────────────────── */}
      <div
        style={{
          position:    "fixed",
          bottom:      28,       // sits above StatusBar
          left:        "50%",
          transform:   open
            ? "translateX(-50%) translateY(0)"
            : "translateX(-50%) translateY(110%)",
          width:       "min(680px, 96vw)",
          zIndex:      8200,
          background:  BG_SURFACE,
          border:      `1px solid ${BORDER}`,
          borderBottom: "none",
          borderRadius: "14px 14px 0 0",
          boxShadow:   "0 -8px 32px rgba(0,0,0,0.5)",
          transition:  "transform 0.28s cubic-bezier(0.32, 0.72, 0, 1)",
          overflow:    "hidden",
        }}
      >
        {/* Handle bar */}
        <div style={{ display: "flex", justifyContent: "center", paddingTop: 10, paddingBottom: 4 }}>
          <div style={{ width: 36, height: 4, borderRadius: 2, background: BORDER }} />
        </div>

        {/* Header */}
        <div style={{
          display:        "flex",
          alignItems:     "center",
          justifyContent: "space-between",
          padding:        "8px 20px 12px",
          borderBottom:   `1px solid ${BORDER}`,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 15, fontWeight: 700, color: TEXT }}>🛠 Debug Tools</span>
          </div>
          <button
            onClick={closeDrawer}
            style={{
              background:  "none",
              border:      "none",
              color:       TEXT_MUTED,
              fontSize:    18,
              cursor:      "pointer",
              lineHeight:  1,
              padding:     "2px 6px",
              borderRadius: 4,
              transition:  "color 0.15s",
            }}
            onMouseEnter={(e) => (e.target.style.color = TEXT)}
            onMouseLeave={(e) => (e.target.style.color = TEXT_MUTED)}
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: "16px 20px 20px", display: "flex", flexDirection: "column", gap: 14 }}>

          {/* Global actions */}
          <div>
            <div style={{ fontSize: 10, color: TEXT_MUTED, fontWeight: 500, letterSpacing: "0.4px", textTransform: "uppercase", marginBottom: 8 }}>
              Global
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <ActionBtn label="Active Trades"   icon="📘" onClick={() => go("/trades?state=BUY_FILLED&refresh=5")} />
              <ActionBtn label="All Trades"      icon="📄" onClick={() => go("/trades?refresh=5")} />
              <ActionBtn label="Market Timeline" icon="📊" onClick={() => go("/market_timeline?refresh=3")} />
            </div>
          </div>

          {/* BB Strategy */}
          <div>
            <div style={{ fontSize: 10, color: TEXT_MUTED, fontWeight: 500, letterSpacing: "0.4px", textTransform: "uppercase", marginBottom: 8 }}>
              BB Strategy
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <ActionBtn label="Futures Candles" icon="📈" onClick={() => go("/futures_candles?refresh=3")} />
            </div>
          </div>

          {/* Scalp Strategy */}
          {(ceSlots.length > 0 || peSlots.length > 0) && (
            <div>
              <div style={{ fontSize: 10, color: TEXT_MUTED, fontWeight: 500, letterSpacing: "0.4px", textTransform: "uppercase", marginBottom: 8 }}>
                Scalp Strategy
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                {/* CE Slots */}
                {ceSlots.length > 0 && (
                  <SlotGroup title="CE Slots" slots={ceSlots} slotMap={slotMap} go={go} />
                )}

                {/* PE Slots */}
                {peSlots.length > 0 && (
                  <SlotGroup title="PE Slots" slots={peSlots} slotMap={slotMap} go={go} />
                )}
              </div>
            </div>
          )}

          {/* Empty state */}
          {ceSlots.length === 0 && peSlots.length === 0 && (
            <div style={{ color: TEXT_MUTED, fontSize: 12, textAlign: "center", padding: "12px 0" }}>
              No active SCALP slots. Slot-specific links will appear here once positions are live.
            </div>
          )}
        </div>
      </div>
    </>
  );
}

/* ─────────────────────────────────────────────
   SlotGroup
───────────────────────────────────────────── */
function SlotGroup({ title, slots, slotMap, go }) {
  return (
    <div>
      <div style={{ fontSize: 9, color: TEXT_MUTED, fontWeight: 500, letterSpacing: "0.3px", textTransform: "uppercase", marginBottom: 8, opacity: 0.7 }}>
        {title}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 8 }}>
        {slots.map((slot) => {
          const symbol = slotMap[slot];
          return (
            <div
              key={slot}
              style={{
                background:   BG_CARD,
                border:       `1px solid ${BORDER}`,
                borderRadius: 8,
                padding:      "7px 12px",
                display:      "flex",
                alignItems:   "center",
                gap:          10,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: TEXT }}>{slot}</div>
                <div style={{ fontSize: 10, color: TEXT_MUTED, fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {symbol}
                </div>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <MiniBtn icon="📊" label="Timeline" onClick={() => go(`/market_timeline?symbol=${symbol}&refresh=3`)} />
                <MiniBtn icon="📄" label="Trades"   onClick={() => go(`/trades?symbol=${symbol}&refresh=5`)} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   Buttons
───────────────────────────────────────────── */
function ActionBtn({ icon, label, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding:      "7px 14px",
        borderRadius: 7,
        border:       `1px solid ${BORDER}`,
        background:   BG_INPUT,
        color:        TEXT,
        fontSize:     12,
        fontWeight:   500,
        cursor:       "pointer",
        display:      "flex",
        alignItems:   "center",
        gap:          6,
        transition:   "background 0.15s, border-color 0.15s",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "#1a2540")}
      onMouseLeave={(e) => (e.currentTarget.style.background = BG_INPUT)}
    >
      <span>{icon}</span>
      {label}
    </button>
  );
}

function MiniBtn({ icon, label, onClick }) {
  return (
    <button
      title={label}
      onClick={onClick}
      style={{
        width:          34,
        height:         30,
        borderRadius:   6,
        border:         `1px solid ${BORDER}`,
        background:     BG_INPUT,
        color:          TEXT,
        fontSize:       14,
        cursor:         "pointer",
        display:        "flex",
        alignItems:     "center",
        justifyContent: "center",
        transition:     "background 0.15s",
        flexShrink:     0,
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "#1a2540")}
      onMouseLeave={(e) => (e.currentTarget.style.background = BG_INPUT)}
    >
      {icon}
    </button>
  );
}