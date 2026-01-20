import { useEffect, useState } from "react";

/**
 * DebugPanel (Compact / Collapsible)
 *
 * - Collapsible via chevron toggle
 * - Minimal vertical footprint when collapsed
 * - Slot → symbol context preserved
 */

export default function DebugPanel({ rows = [] }) {
  const base = "http://127.0.0.1:8000/debug/ui";

  const [backendReady, setBackendReady] = useState(false);
  const [checking, setChecking] = useState(true);
  const [collapsed, setCollapsed] = useState(true); // 🔒 default collapsed

  /* ----------------------------------
     Backend readiness check
  ----------------------------------- */

  useEffect(() => {
    let alive = true;

    async function pollStatus() {
      while (alive) {
        try {
          const res = await fetch("/status");
          if (res.ok) {
            const data = await res.json();
            if (data.engine_running === true) {
              setBackendReady(true);
              setChecking(false);
              return;
            }
          }
        } catch {}
        await new Promise((r) => setTimeout(r, 1000));
      }
    }

    pollStatus();
    return () => {
      alive = false;
    };
  }, []);

  /* ----------------------------------
     Helpers
  ----------------------------------- */

  function open(path) {
    if (!backendReady) return;
    window.open(`${base}${path}`, "_blank");
  }

  // Build slot → symbol map
  const slotMap = {};
  rows.forEach((r) => {
    if (r.slot && r.tradingsymbol && !slotMap[r.slot]) {
      slotMap[r.slot] = r.tradingsymbol;
    }
  });

  const ceSlots = Object.keys(slotMap)
    .filter((s) => s.startsWith("CE"))
    .sort();

  const peSlots = Object.keys(slotMap)
    .filter((s) => s.startsWith("PE"))
    .sort();

  /* ----------------------------------
     Render
  ----------------------------------- */

  return (
    <div
      style={{
        border: "1px solid #243055",
        borderRadius: 10,
        padding: "10px 14px",
        background: "#0f1628",
        marginTop: 16,
      }}
    >
      {/* ---------- HEADER ---------- */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          cursor: "pointer",
        }}
        onClick={() => setCollapsed((v) => !v)}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontWeight: 600, fontSize: 14 }}>🛠 Debug Tools</span>

          <span
            style={{
              fontSize: 11,
              padding: "4px 8px",
              borderRadius: 6,
              background: backendReady ? "#123f2a" : "#3b2f12",
              color: backendReady ? "#7CFFB2" : "#FFD27C",
            }}
          >
            {checking
              ? "Checking…"
              : backendReady
              ? "Backend Ready"
              : "Backend Not Ready"}
          </span>
        </div>

        {/* Chevron */}
        <span
          style={{
            fontSize: 16,
            opacity: 0.7,
            userSelect: "none",
          }}
        >
          {collapsed ? "▾" : "▴"}
        </span>
      </div>

      {/* ---------- COLLAPSIBLE CONTENT ---------- */}
      {!collapsed && (
        <>
          {/* ---------- GLOBAL ACTIONS ---------- */}
          <div
            style={{
              display: "flex",
              gap: 10,
              marginTop: 12,
              marginBottom: 14,
            }}
          >
            <IconBtn
              label="Market Timeline"
              icon="📊"
              disabled={!backendReady}
              onClick={() => open("/market_timeline?refresh=3")}
            />
            <IconBtn
              label="Active Trades"
              icon="📘"
              disabled={!backendReady}
              onClick={() =>
                open("/trades?state=BUY_FILLED&refresh=5")
              }
            />
            <IconBtn
              label="All Trades"
              icon="📄"
              disabled={!backendReady}
              onClick={() => open("/trades?refresh=5")}
            />
          </div>

          {/* ---------- CE SLOTS ---------- */}
          {ceSlots.length > 0 && (
            <SlotGroup
              title="CE"
              slots={ceSlots}
              slotMap={slotMap}
              backendReady={backendReady}
              open={open}
            />
          )}

          {/* ---------- PE SLOTS ---------- */}
          {peSlots.length > 0 && (
            <SlotGroup
              title="PE"
              slots={peSlots}
              slotMap={slotMap}
              backendReady={backendReady}
              open={open}
            />
          )}
        </>
      )}
    </div>
  );
}

/* ----------------------------------
   Slot Group (Option A – compact row)
----------------------------------- */

function SlotGroup({ title, slots, slotMap, backendReady, open }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div
        style={{
          fontSize: 12,
          opacity: 0.7,
          marginBottom: 6,
        }}
      >
        {title} Slots
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
          gap: 10,
        }}
      >
        {slots.map((slot) => {
          const symbol = slotMap[slot];

          return (
            <div
              key={slot}
              style={{
                border: "1px solid #243055",
                borderRadius: 8,
                padding: "6px 10px",
                background: "#020617",
                display: "flex",
                alignItems: "center",
                gap: 10,
              }}
            >
              {/* Slot + Symbol */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  minWidth: 0,
                  flex: 1,
                }}
              >
                <span
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    whiteSpace: "nowrap",
                  }}
                >
                  {slot}
                </span>

                <span
                  style={{
                    fontSize: 11,
                    fontFamily: "monospace",
                    opacity: 0.65,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {symbol}
                </span>
              </div>

              {/* Actions */}
              <div style={{ display: "flex", gap: 6 }}>
                <MiniIconBtn
                  icon="📊"
                  label="Timeline"
                  disabled={!backendReady}
                  onClick={() =>
                    open(`/market_timeline?symbol=${symbol}&refresh=3`)
                  }
                />
                <MiniIconBtn
                  icon="📄"
                  label="Trades"
                  disabled={!backendReady}
                  onClick={() =>
                    open(`/trades?symbol=${symbol}&refresh=5`)
                  }
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ----------------------------------
   Buttons
----------------------------------- */

function IconBtn({ icon, label, onClick, disabled }) {
  return (
    <button
      title={label}
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: "6px 12px",
        borderRadius: 8,
        border: "1px solid #243055",
        background: "#020617",
        color: "#e6e6e6",
        cursor: disabled ? "not-allowed" : "pointer",
        fontSize: 13,
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {icon} {label}
    </button>
  );
}

function MiniIconBtn({ icon, label, onClick, disabled }) {
  return (
    <button
      title={label}
      onClick={onClick}
      disabled={disabled}
      style={{
        minWidth: 56,            // ⬅ visual weight
        height: 34,              // ⬅ slightly taller
        padding: "0 14px",       // ⬅ THIS is the key
        borderRadius: 8,
        border: "1px solid #243055",
        background: "#020617",
        color: "#e6e6e6",
        cursor: disabled ? "not-allowed" : "pointer",
        fontSize: 16,
        opacity: disabled ? 0.5 : 1,

        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      {icon}
    </button>
  );
}

