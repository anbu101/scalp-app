import { useEffect, useState } from "react";

/**
 * DebugPanel
 *
 * - Shows debug links
 * - Waits for backend readiness (/status)
 * - Disables auto-refresh links until engine is running
 */

export default function DebugPanel({ rows = [] }) {
  const base = "http://127.0.0.1:8000/debug/ui";

  const [backendReady, setBackendReady] = useState(false);
  const [checking, setChecking] = useState(true);

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
        } catch {
          // ignore
        }
        await new Promise(r => setTimeout(r, 1000));
      }
    }

    pollStatus();
    return () => { alive = false; };
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
        padding: 16,
        background: "#0f1628",
        marginTop: 20,
      }}
    >
      <h3 style={{ marginTop: 0 }}>🛠 Debug Tools</h3>

      {/* ---------- Backend status ---------- */}
      <div
        style={{
          marginBottom: 12,
          padding: "6px 10px",
          borderRadius: 6,
          fontSize: 12,
          background: backendReady ? "#123f2a" : "#3b2f12",
          color: backendReady ? "#7CFFB2" : "#FFD27C",
        }}
      >
        {checking
          ? "⏳ Checking backend status…"
          : backendReady
          ? "✅ Backend ready"
          : "⚠️ Backend not ready"}
      </div>

      {/* ---------- GLOBAL ---------- */}
      <Section title="Global">
        <Grid>
          <Btn
            disabled={!backendReady}
            onClick={() => open("/market_timeline?refresh=3")}
          >
            📊 Market Timeline (Live)
          </Btn>

          <Btn
            disabled={!backendReady}
            onClick={() =>
              open("/trades?state=BUY_FILLED&refresh=5")
            }
          >
            📘 Active Trades
          </Btn>

          <Btn
            disabled={!backendReady}
            onClick={() => open("/trades?refresh=5")}
          >
            📘 All Trades
          </Btn>
        </Grid>
      </Section>

      {/* ---------- CE ---------- */}
      {ceSlots.length > 0 && (
        <Section title="CE Slots">
          {ceSlots.map((slot) => {
            const symbol = slotMap[slot];
            return (
              <Row key={slot} label={`${slot} (${symbol})`}>
                <MiniBtn
                  disabled={!backendReady}
                  onClick={() =>
                    open(
                      `/market_timeline?symbol=${symbol}&refresh=3`
                    )
                  }
                >
                  📊 Timeline
                </MiniBtn>

                <MiniBtn
                  disabled={!backendReady}
                  onClick={() =>
                    open(
                      `/trades?symbol=${symbol}&refresh=5`
                    )
                  }
                >
                  📘 Trades
                </MiniBtn>
              </Row>
            );
          })}
        </Section>
      )}

      {/* ---------- PE ---------- */}
      {peSlots.length > 0 && (
        <Section title="PE Slots">
          {peSlots.map((slot) => {
            const symbol = slotMap[slot];
            return (
              <Row key={slot} label={`${slot} (${symbol})`}>
                <MiniBtn
                  disabled={!backendReady}
                  onClick={() =>
                    open(
                      `/market_timeline?symbol=${symbol}&refresh=3`
                    )
                  }
                >
                  📊 Timeline
                </MiniBtn>

                <MiniBtn
                  disabled={!backendReady}
                  onClick={() =>
                    open(
                      `/trades?symbol=${symbol}&refresh=5`
                    )
                  }
                >
                  📘 Trades
                </MiniBtn>
              </Row>
            );
          })}
        </Section>
      )}
    </div>
  );
}

/* ----------------------------------
   UI helpers
----------------------------------- */

function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <div
        style={{
          fontWeight: 600,
          marginBottom: 10,
          opacity: 0.9,
        }}
      >
        {title}
      </div>
      {children}
    </div>
  );
}

function Grid({ children }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(3, 1fr)",
        gap: 12,
      }}
    >
      {children}
    </div>
  );
}

function Row({ label, children }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        alignItems: "center",
        gap: 12,
        marginBottom: 10,
      }}
    >
      <div
        style={{
          fontFamily: "monospace",
          opacity: 0.85,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {label}
      </div>

      <div
        style={{
          display: "grid",
          gridAutoFlow: "column",
          gap: 10,
          justifyContent: "start",
        }}
      >
        {children}
      </div>
    </div>
  );
}

/* ----------------------------------
   Buttons
----------------------------------- */

function Btn({ children, onClick, disabled }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: "10px 14px",
        borderRadius: 8,
        border: "1px solid #243055",
        background: disabled ? "#020617" : "#020617",
        color: disabled ? "#6b7280" : "#e6e6e6",
        cursor: disabled ? "not-allowed" : "pointer",
        width: "100%",
        opacity: disabled ? 0.6 : 1,
      }}
    >
      {children}
    </button>
  );
}

function MiniBtn({ children, onClick, disabled }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: "6px 12px",
        borderRadius: 6,
        border: "1px solid #243055",
        background: "#020617",
        color: disabled ? "#6b7280" : "#e6e6e6",
        cursor: disabled ? "not-allowed" : "pointer",
        fontSize: 12,
        opacity: disabled ? 0.6 : 1,
      }}
    >
      {children}
    </button>
  );
}
