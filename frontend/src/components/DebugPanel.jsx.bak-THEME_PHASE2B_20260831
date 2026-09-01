/**
 * DebugPanel — Slide-up Drawer  (now globally mounted)
 * Path: src/components/DebugPanel.jsx
 *
 * GLOBAL, ALL-STRATEGY debug tool. Mounted once at the Dashboard level so it is
 * always available regardless of which strategy panel is focused. Sections:
 *   - Global  : Active Trades, All Trades, Market Timeline, Today's Log
 *   - BB      : Futures Candles
 *   - HA      : HA Candles (All / CE / PE)
 *   - Scalp   : per-slot Timeline + Trades links
 *
 * Scalp slot links need the current SCALP_V1 selection. Historically this came
 * in via a `rows` prop from ScalpPanel. Now that DebugPanel is global (not a
 * child of ScalpPanel), it FETCHES the selection itself on a slow poll. The
 * `rows` prop is still honored as an optional override — if a parent passes
 * rows, those win; otherwise the internal fetch populates them.
 *
 * Everything else is unchanged from the original DebugPanel.
 */

import { useState, useCallback, useEffect } from "react";
import { getApiBase } from "../api/base";
import { getCurrentSelection } from "../api";
import { useIsMobile } from "../hooks/useIsMobile";

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

const SCALP_POLL_MS = 15_000;

/* ─────────────────────────────────────────────
   DebugPanel
───────────────────────────────────────────── */
export default function DebugPanel({ rows: rowsProp = null }) {
  const base     = `${getApiBase()}/debug/ui`;
  const apiBase  = getApiBase();
  const isMobile = useIsMobile();
  const [open, setOpen] = useState(false);
  const [logContent,  setLogContent]  = useState(null);
  const [logLoading,  setLogLoading]  = useState(false);
  const [logExpanded, setLogExpanded] = useState(false);

  // Internal Scalp selection → rows (used when no rows prop is supplied).
  const [selRows, setSelRows] = useState([]);

  useEffect(() => {
    if (rowsProp) return; // parent supplies rows — skip internal fetch
    let alive = true;
    async function loadSelection() {
      try {
        const sel = await getCurrentSelection("SCALP_V1");
        if (!alive || !sel) return;
        const out = [];
        ["CE_1", "CE_2"].forEach((slot, i) => {
          const o = sel.CE?.[i];
          if (o?.tradingsymbol) out.push({ slot, tradingsymbol: o.tradingsymbol });
        });
        ["PE_1", "PE_2"].forEach((slot, i) => {
          const o = sel.PE?.[i];
          if (o?.tradingsymbol) out.push({ slot, tradingsymbol: o.tradingsymbol });
        });
        setSelRows(out);
      } catch { /* keep last */ }
    }
    loadSelection();
    const t = setInterval(loadSelection, SCALP_POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, [rowsProp]);

  const rows = rowsProp || selRows;

  const openDrawer  = useCallback(() => setOpen(true),  []);
  const closeDrawer = useCallback(() => setOpen(false), []);

  async function fetchTodayLog() {
    setLogExpanded(true);
    setLogLoading(true);
    try {
      const res  = await fetch(`${apiBase}/logs/today`);
      const data = await res.json();
      setLogContent(data);
    } catch (e) {
      setLogContent({ date: "", path: "", content: `Failed to fetch log: ${e.message}`, lines: 0 });
    } finally {
      setLogLoading(false);
    }
  }

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

  return (
    <>
      {/* ── Floating trigger pill ─────────────────────────────────────── */}
      <button
        onClick={openDrawer}
        title="Open debug tools"
        style={{
          position:     "fixed",
          bottom:       isMobile ? "calc(58px + env(safe-area-inset-bottom) + 10px)" : 36,
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
          bottom:      isMobile ? 58 : 28,
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
          paddingBottom: isMobile ? "env(safe-area-inset-bottom)" : 0,
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

        {/* Body — scrollable */}
        <div style={{
          padding:       "16px 20px 20px",
          display:       "flex",
          flexDirection: "column",
          gap:           14,
          maxHeight:     "70vh",
          overflowY:     "auto",
        }}>

          {/* Global actions */}
          <div>
            <div style={{ fontSize: 10, color: TEXT_MUTED, fontWeight: 500, letterSpacing: "0.4px", textTransform: "uppercase", marginBottom: 8 }}>
              Global
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <ActionBtn label="Active Trades"   icon="📘" onClick={() => go("/trades?state=BUY_FILLED&refresh=5")} />
              <ActionBtn label="All Trades"      icon="📄" onClick={() => go("/trades?refresh=5")} />
              <ActionBtn label="Market Timeline" icon="📊" onClick={() => go("/market_timeline?refresh=3")} />
              <ActionBtn label="Today's Log"     icon="📋" onClick={fetchTodayLog} />
            </div>
          </div>

          {/* Log viewer */}
          {logExpanded && (
            <div style={{
              background:   BG_CARD,
              border:       `1px solid ${BORDER}`,
              borderRadius: 8,
              overflow:     "hidden",
            }}>
              <div style={{
                display:        "flex",
                alignItems:     "center",
                justifyContent: "space-between",
                padding:        "8px 12px",
                borderBottom:   `1px solid ${BORDER}`,
                gap:            8,
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                  <span style={{ fontSize: 11, fontWeight: 600, color: TEXT }}>📋 Today's Log</span>
                  {logContent && (
                    <span style={{ fontSize: 10, color: TEXT_MUTED, fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {logContent.date} · {logContent.lines} lines
                    </span>
                  )}
                </div>
                <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                  <button
                    onClick={fetchTodayLog}
                    title="Refresh"
                    style={{ background: "none", border: `1px solid ${BORDER}`, borderRadius: 4, color: TEXT_MUTED, fontSize: 11, padding: "2px 8px", cursor: "pointer" }}
                  >↺ Refresh</button>
                  <button
                    onClick={() => setLogExpanded(false)}
                    style={{ background: "none", border: "none", color: TEXT_MUTED, fontSize: 16, cursor: "pointer", lineHeight: 1, padding: "2px 4px" }}
                  >✕</button>
                </div>
              </div>

              <div style={{ maxHeight: 300, overflowY: "auto", padding: "10px 12px" }}>
                {logLoading ? (
                  <div style={{ color: TEXT_MUTED, fontSize: 12, textAlign: "center", padding: "20px 0" }}>
                    Loading log…
                  </div>
                ) : (
                  <div style={{ fontFamily: "monospace", fontSize: 10, lineHeight: 1.7 }}>
                    {(logContent?.content ?? "No content").split("\n").map((line, i) => (
                      <div key={i} style={{ color: logLineColor(line), whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
                        {line || "\u00a0"}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* BB Strategy */}
          <div>
            <div style={{ fontSize: 10, color: TEXT_MUTED, fontWeight: 500, letterSpacing: "0.4px", textTransform: "uppercase", marginBottom: 8 }}>
              BB Strategy
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <ActionBtn label="Futures Candles" icon="📈" onClick={() => go("/futures_candles?refresh=3")} />
            </div>
          </div>

          {/* HA Strategy */}
          <div>
            <div style={{ fontSize: 10, color: TEXT_MUTED, fontWeight: 500, letterSpacing: "0.4px", textTransform: "uppercase", marginBottom: 8 }}>
              HA Strategy
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <ActionBtn
                label="HA Candles (All)"
                icon="🕯"
                onClick={() => go("/ha_candles?refresh=5")}
              />
              <ActionBtn
                label="HA Candles (CE)"
                icon="🟢"
                onClick={() => {
                  const ceSymbol = Object.entries(slotMap).find(([k]) => k.startsWith("CE"))?.[1];
                  go(ceSymbol
                    ? `/ha_candles?symbol=${ceSymbol}&refresh=5`
                    : "/ha_candles?refresh=5"
                  );
                }}
              />
              <ActionBtn
                label="HA Candles (PE)"
                icon="🔴"
                onClick={() => {
                  const peSymbol = Object.entries(slotMap).find(([k]) => k.startsWith("PE"))?.[1];
                  go(peSymbol
                    ? `/ha_candles?symbol=${peSymbol}&refresh=5`
                    : "/ha_candles?refresh=5"
                  );
                }}
              />
            </div>
          </div>

          {/* Scalp Strategy */}
          {(ceSlots.length > 0 || peSlots.length > 0) && (
            <div>
              <div style={{ fontSize: 10, color: TEXT_MUTED, fontWeight: 500, letterSpacing: "0.4px", textTransform: "uppercase", marginBottom: 8 }}>
                Scalp Strategy
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                {ceSlots.length > 0 && (
                  <SlotGroup title="CE Slots" slots={ceSlots} slotMap={slotMap} go={go} />
                )}
                {peSlots.length > 0 && (
                  <SlotGroup title="PE Slots" slots={peSlots} slotMap={slotMap} go={go} />
                )}
              </div>
            </div>
          )}

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
   Log line colour classifier
───────────────────────────────────────────── */
function logLineColor(line) {
  const u = line.toUpperCase();
  if (/\b(ERROR|ERR=|EXCEPTION|TRACEBACK|CRITICAL|FATAL)\b/.test(u))               return "#f87171";
  if (/\bNOT\s+(READY|STARTED|CONNECTED|ENABLED)\b/.test(u))                  return "#f87171";
  if (/\bBROKER\s+NOT\s+READY\b/.test(u))                                      return "#f87171";
  if (/\b(FAILED|FAILURE|DISCONNECTED|DISABLED|STOPPED|UNAVAILABLE)\b/.test(u)) return "#f87171";
  if (/\b(WARNING|WARN)\b/.test(u))                                             return "#fbbf24";
  if (/\b(SUCCESS|CONNECTED|ENABLED|STARTED|READY)\b/.test(u))                 return "#34d399";
  if (/\[TAILSCALE\]|\[WATCHDOG\]|\[RUNTIME\]/.test(line))                     return "#94a3b8";
  if (/\[ZERODHA\]|\[KITE\]/.test(line))                                        return "#818cf8";
  if (/\[BACKEND\]|\[SERVER\]|\[UVICORN\]/i.test(line))                        return "#60a5fa";
  return TEXT;
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